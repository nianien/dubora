"""
CLI entry point for dubora-pipeline

Commands:
  vsd-pipeline run 家里家外 5 --to parse        # Submit task to DB
  vsd-pipeline run 家里家外 4-70 --to burn      # Batch submit
  vsd-pipeline worker                           # Long-running task executor
  vsd-pipeline worker --api-url http://web:8765 # Remote mode (via HTTP API)
  vsd-pipeline phases                           # List phases
"""
import argparse
import os
import re
import sys
from typing import List

from dubora_core.config.settings import PipelineConfig, load_env_file, get_db_path
from dubora_core.phase_registry import PHASE_NAMES, PHASE_META, GATES, GATE_AFTER, STAGES
from dubora_core.store import DbStore
from dubora_core.submit import submit_pipeline
from dubora_pipeline.phases import build_phases
from dubora_pipeline.worker import PipelineWorker
from dubora_core.utils.logger import info, warning, error, success


def expand_episode_range(ep_arg: str) -> List[str]:
    m = re.match(r'^(\d+)-(\d+)$', ep_arg)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        return [str(i) for i in range(start, end + 1)]
    return [ep_arg]


def get_store() -> DbStore:
    p = get_db_path()
    if not p.exists():
        error(f"Pipeline DB not found: {p}")
        sys.exit(1)
    return DbStore(p)


def resolve_episodes(store: DbStore, drama_name: str, ep_arg: str) -> list[dict]:
    drama = store.get_drama_by_name(drama_name)
    if drama is None:
        error(f"Drama not found in DB: {drama_name}")
        sys.exit(1)

    ep_numbers = expand_episode_range(ep_arg)
    episodes = []
    for num in ep_numbers:
        ep = store.get_episode_by_names(drama_name, int(num))
        if ep is None:
            warning(f"Episode not found in DB: {drama_name} ep {num}")
        else:
            episodes.append(ep)

    if not episodes:
        error("No matching episodes found in DB")
        sys.exit(1)

    return episodes


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Video dubbing pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Phases: {' -> '.join(PHASE_NAMES)}

Examples:
  vsd-pipeline run 家里家外 5 --to parse            # Submit single episode
  vsd-pipeline run 家里家外 4-70 --to burn          # Batch submit
  vsd-pipeline run 家里家外 1-10 --from mt --to tts # Force re-run range
  vsd-pipeline worker                               # Start task executor (local DB)
  vsd-pipeline worker --api-url http://web:8765     # Start task executor (remote API)
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # run command
    run_parser = subparsers.add_parser("run", help="Submit pipeline tasks to DB")
    run_parser.add_argument("drama", type=str, help="Drama name")
    run_parser.add_argument("episodes", type=str, help="Episode number or range (e.g. 5 or 4-70)")
    run_parser.add_argument(
        "--to", type=str, choices=PHASE_NAMES,
        help="Target phase to run up to (omit for auto-advance)",
    )
    run_parser.add_argument(
        "--from", type=str, dest="from_phase", choices=PHASE_NAMES,
        help="Force refresh from this phase (inclusive)",
    )
    run_parser.add_argument(
        "--api-url", type=str, default=None,
        help="Web API URL for remote submission (e.g. http://web:8765)",
    )

    # worker command
    worker_parser = subparsers.add_parser("worker", help="Start long-running task executor")
    worker_parser.add_argument(
        "--api-url", type=str, default=None,
        help="Web API URL for remote mode (e.g. http://web:8765)",
    )

    # gate command
    gate_parser = subparsers.add_parser("gate", help="Pass a pipeline gate to continue execution")
    gate_parser.add_argument("drama", type=str, help="Drama name")
    gate_parser.add_argument("episodes", type=str, help="Episode number or range (e.g. 5 or 4-70)")
    gate_parser.add_argument(
        "gate_key", type=str, nargs="?", default=None,
        choices=[g["key"] for g in GATES],
        help="Gate key (default: auto-detect pending gate)",
    )

    # phases command
    subparsers.add_parser("phases", help="List available phases")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run" and getattr(args, "from_phase", None) and getattr(args, "to", None):
        from_idx = PHASE_NAMES.index(args.from_phase)
        to_idx = PHASE_NAMES.index(args.to)
        if from_idx > to_idx:
            parser.error(f"--from ({args.from_phase}) must be before --to ({args.to})")

    load_env_file()

    if args.command == "phases":
        _cmd_phases()
        return

    if args.command == "gate":
        _cmd_gate(args)
        return

    if args.command == "worker":
        api_url = getattr(args, "api_url", None) or os.environ.get("API_URL")
        _cmd_worker(api_url=api_url)
        return

    # ── run ──
    api_url = getattr(args, "api_url", None) or os.environ.get("API_URL")
    if api_url:
        _cmd_run_remote(api_url, args)
    else:
        _cmd_run_local(args)


def _cmd_gate(args):
    """Pass a gate to continue pipeline execution."""
    from dubora_core.events import EventEmitter, PipelineEvent
    from dubora_core.submit import PipelineReactor

    store = get_store()
    episodes = resolve_episodes(store, args.drama, args.episodes)

    for ep in episodes:
        episode_id = ep["id"]

        # Auto-detect pending gate if not specified
        gate_key = args.gate_key
        if not gate_key:
            latest = store.get_latest_task(episode_id)
            if latest and latest["status"] == "pending" and latest["type"] in {g["key"] for g in GATES}:
                gate_key = latest["type"]
            else:
                warning(f"[ep {ep['number']}] No pending gate found")
                continue

        task_id = store.pass_gate_task(episode_id, gate_key)
        if task_id is None:
            gate_task = store.get_gate_task(episode_id, gate_key)
            if gate_task and gate_task["status"] == "succeeded":
                info(f"[ep {ep['number']}] Gate '{gate_key}' already passed")
                continue
            task_id = store.create_task(episode_id, gate_key)
            store.complete_task(task_id)

        # Reactor creates next phase task
        emitter = EventEmitter()
        reactor = PipelineReactor(
            store, emitter, episode_id, PHASE_NAMES, GATE_AFTER,
        )
        reactor._on_succeeded(PipelineEvent(
            kind="task_succeeded",
            run_id=str(episode_id),
            data={"type": gate_key},
        ))
        success(f"[ep {ep['number']}] Gate '{gate_key}' passed")


def _cmd_phases():
    meta_map = {m["name"]: m for m in PHASE_META}
    gate_count = len(GATE_AFTER)
    print(f"\nPipeline ({len(PHASE_META)} phases, {gate_count} gates):\n")
    print(f"  {'Stage':<8}{'Phase':<9}{'Version':<9}Gate")
    print(f"  {'──────':<8}{'───────':<9}{'───────':<9}────")
    for stage in STAGES:
        stage_label = stage["label"]
        for i, pname in enumerate(stage["phases"]):
            meta = meta_map[pname]
            label = stage_label if i == 0 else ""
            gate = GATE_AFTER.get(pname)
            gate_str = f"\u2190 {gate['label']}" if gate else ""
            print(f"  {label:<8}{meta['name']:<9}{meta['version']:<9}{gate_str}")
    print()


def _cmd_worker(*, api_url: str | None = None):
    config = PipelineConfig()
    phases = build_phases(config)

    if api_url:
        from dubora_pipeline.remote_store import RemoteStore
        store = RemoteStore(api_url)
        worker = PipelineWorker(store, phases, GATE_AFTER, remote=True)
        info(f"Worker started (remote: {api_url}), polling for tasks... (Ctrl+C to stop)")
    else:
        store = get_store()
        worker = PipelineWorker(store, phases, GATE_AFTER)
        info("Worker started, polling for tasks... (Ctrl+C to stop)")

    try:
        worker.run_forever()
    except KeyboardInterrupt:
        info("Worker stopped")


def _cmd_run_local(args):
    """Submit pipeline via local DB."""
    store = get_store()
    episodes = resolve_episodes(store, args.drama, args.episodes)
    is_batch = len(episodes) > 1
    if is_batch:
        info(f"Batch mode: {len(episodes)} episodes")

    failed = []
    for i, ep in enumerate(episodes):
        if is_batch:
            info(f"--- [{i+1}/{len(episodes)}] ep {ep['number']} ---")
        try:
            submit_pipeline(
                store, ep["id"], PHASE_NAMES, GATE_AFTER,
                from_phase=args.from_phase, to_phase=args.to,
            )
            success(f"[ep {ep['number']}] Submitted")
        except Exception as e:
            error(f"[ep {ep['number']}] Submit failed: {e}")
            failed.append(ep["number"])

    if is_batch:
        ok_count = len(episodes) - len(failed)
        info(f"Batch complete: {ok_count}/{len(episodes)} succeeded")
        if failed:
            error(f"Failed: {', '.join(str(f) for f in failed)}")
            sys.exit(1)


def _cmd_run_remote(api_url: str, args):
    """Submit pipeline via web API."""
    import requests

    ep_numbers = [int(n) for n in expand_episode_range(args.episodes)]
    payload = {
        "drama": args.drama,
        "episodes": ep_numbers,
        "from_phase": args.from_phase,
        "to_phase": args.to,
    }
    url = f"{api_url.rstrip('/')}/api/worker/submit"
    info(f"Submitting to {url}")

    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        for result in data.get("results", []):
            if result["status"] == "submitted":
                success(f"[ep {result['episode']}] Submitted (id={result['episode_id']})")
            elif result["status"] == "not_found":
                warning(f"[ep {result['episode']}] Not found")
            else:
                error(f"[ep {result['episode']}] {result.get('error', 'unknown error')}")
    except requests.RequestException as e:
        error(f"Submit failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

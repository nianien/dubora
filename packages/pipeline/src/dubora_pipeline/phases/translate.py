"""
Translate Phase: 增量翻译（utterance 级增量, per-cue 回填）

职责：
- 调 calculate_utterances() 合并 SRC cues 为 utterances
- 只翻译脏 utterances（source_hash 不匹配 / cue text_en 缺失）
- 单 cue utterance：直接翻译
- 多 cue utterance：编号格式送 LLM，按子 cue 分别输出英文，一一回填
- 计算 tts_policy（utterance 间 gap 逻辑）
- 翻译成功后更新 source_hash
- 创建 DST cues（从 SRC cue text_en）

增量语义：
- source_hash 比对 + cue text_en 缺失判定脏行
- 脏行为空 → 直接成功（no-op）
"""
import re
import subprocess
from pathlib import Path
from typing import Dict, List

from dubora_pipeline.phase import Phase
from dubora_core.store import _compute_source_hash
from dubora_pipeline.types import Artifact, ErrorInfo, PhaseResult, RunContext, ResolvedOutputs
from dubora_pipeline.processors.mt.utterance_translate import (
    pick_k,
    translate_utterance_with_retry,
    clean_translation_output,
)
from dubora_pipeline.processors.mt.time_aware_impl import create_translate_fn
from dubora_pipeline.processors.mt.name_guard import NameGuard, load_config
from dubora_pipeline.processors.mt.dict_loader import DictLoader
from dubora_pipeline.processors.mt.name_map_complete import complete_names_with_llm
from dubora_core.utils.logger import info, error, warning


def _build_name_variants(en_name: str, src_name: str) -> List[str]:
    """Build common mistranslation variants for name replacement."""
    variants = []
    parts = en_name.split()
    if len(parts) == 2:
        a, b = parts
        variants.append(f"{a}'{b.lower()}")
        variants.append(f"{a}{b.lower()}")
        variants.append(f"{a.lower()}'{b.lower()}")
        variants.append(f"{a}{b}")
    return variants


def _probe_duration_ms(audio_path: str) -> int:
    """Probe audio duration using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    duration_str = result.stdout.strip()
    if duration_str == "N/A" or not duration_str:
        raise RuntimeError(f"ffprobe returned invalid duration for {audio_path}")
    return int(float(duration_str) * 1000)


def _parse_numbered_output(text: str, expected_count: int) -> list[str]:
    """Parse numbered output like [1] text1\\n[2] text2...

    Returns list of per-cue translations (length == expected_count).
    Falls back to line splitting if bracket parsing fails.
    """
    result = [""] * expected_count

    # Try [N] format (handles both newline-separated and inline markers)
    pattern = r'\[(\d+)\]\s*(.+?)(?=\s*\[\d+\]|\Z)'
    matches = re.findall(pattern, text.strip(), re.DOTALL)
    if matches:
        for num_str, content in matches:
            idx = int(num_str) - 1
            if 0 <= idx < expected_count:
                result[idx] = content.strip()
        if any(result):
            return result

    # Fallback: split by newlines, strip leading numbers/brackets
    lines = [ln.strip() for ln in text.strip().split('\n') if ln.strip()]
    for i, line in enumerate(lines[:expected_count]):
        cleaned = re.sub(r'^\[?\d+\]?\s*[:.\-]?\s*', '', line)
        result[i] = cleaned.strip()

    return result


class TranslatePhase(Phase):
    """增量翻译 Phase（utterance 级增量, per-cue 回填）。"""

    name = "translate"
    version = "4.0.0"

    def requires(self) -> list[str]:
        """需要 extract.audio（probe duration）。DB 模式下数据直接从 store 读取。"""
        return ["extract.audio"]

    def provides(self) -> list[str]:
        """翻译结果写入 DB，不产出 pipeline artifact。"""
        return []

    def run(
        self,
        ctx: RunContext,
        inputs: Dict[str, Artifact],
        outputs: ResolvedOutputs,
    ) -> PhaseResult:
        """
        执行 Translate Phase。

        流程：
        1. calculate_utterances() 合并 SRC cues 为 utterances + junction
        2. 找脏 utterances（source_hash / cue text_en）
        3. 脏行为空 → 直接成功（no-op）
        4. 翻译: 单 cue → 直接翻译; 多 cue → per-cue 编号翻译
        5. 写 cue.text_en + utterance.source_hash + tts_policy
        6. 从 SRC cue text_en 创建 DST cues
        """
        store = ctx.store
        episode_id = ctx.episode_id
        workspace_path = Path(ctx.workspace)

        if not store or not episode_id:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="Translate phase requires DB store and episode_id.",
                ),
            )

        # Probe audio duration
        audio_artifact = inputs.get("extract.audio")
        audio_duration_ms = 0
        if audio_artifact:
            audio_path = workspace_path / audio_artifact.relpath
            if audio_path.exists():
                try:
                    audio_duration_ms = _probe_duration_ms(str(audio_path))
                    info(f"Probed audio duration: {audio_duration_ms}ms")
                except RuntimeError as e:
                    warning(f"Could not probe audio duration: {e}")

        # Ensure SRC cues exist
        all_src_cues = store.get_cues(episode_id)
        if not all_src_cues:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(
                    type="ValueError",
                    message="No SRC cues found in DB. Run parse phase first.",
                ),
            )

        # Step 1: calculate_utterances() — merge SRC cues into utterances + junction
        all_utts = store.calculate_utterances(episode_id)
        info(f"calculate_utterances: {len(all_src_cues)} SRC cues -> {len(all_utts)} utterances")

        # Step 2: Find dirty utterances
        dirty_utts = store.get_dirty_utterances_for_translate(episode_id)
        info(f"Loaded {len(all_utts)} utterances, {len(dirty_utts)} need translation")

        if not dirty_utts:
            info("All utterances are up to date, skipping translation")
            return PhaseResult(
                status="succeeded",
                metrics={"total_utterances": len(all_utts), "dirty": 0, "translated": 0},
            )

        # Set up translation engine
        try:
            translate_fn, is_gemini, dict_loader, name_guard = self._setup_translation(ctx)
        except Exception as e:
            return PhaseResult(
                status="failed",
                error=ErrorInfo(type=type(e).__name__, message=str(e)),
            )

        # Build episode context from all SRC cues
        episode_context = " ".join(
            c["text"] for c in all_src_cues if c.get("text", "").strip()
        )

        # Load story background from DB (dramas.synopsis)
        story_background = self._load_story_background(ctx, store, episode_id)

        # Translate dirty utterances
        ok_count = 0
        fail_count = 0

        # Get all utterances for gap calculation
        sorted_utts = sorted(all_utts, key=lambda u: u.get("start_ms", 0))

        for utt in dirty_utts:
            # Get sub-cues for this utterance via junction table
            cues = store.get_cues_for_utterance(utt["id"])
            if not cues:
                continue

            source_text = "".join(c.get("text", "").strip() for c in cues)
            if not source_text:
                continue

            try:
                # Translate and write per-cue text_en
                translated = self._translate_and_write_cues(
                    utt, cues, store,
                    translate_fn, is_gemini, dict_loader,
                    name_guard, episode_context, story_background,
                )
            except Exception as e:
                error(f"Translation failed for utterance {utt['id']}: {e}")
                return PhaseResult(
                    status="failed",
                    error=ErrorInfo(type=type(e).__name__, message=str(e)),
                )

            # Calculate tts_policy (utterance-level gap)
            tts_policy = self._calc_tts_policy(utt, sorted_utts, audio_duration_ms, ctx)

            # Re-read cues to get updated text_en, build utterance cache
            cues_after = store.get_cues_for_utterance(utt["id"])
            text_en_cache = " ".join(
                c.get("text_en", "").strip() for c in cues_after
                if c.get("text_en", "").strip()
            )
            current_hash = _compute_source_hash(cues_after)

            # Update utterance: text_en cache + source_hash + tts_policy
            store.update_utterance(
                utt["id"],
                text_en=text_en_cache,
                source_hash=current_hash,
                tts_policy=tts_policy,
            )

            if translated:
                ok_count += 1
            else:
                fail_count += 1

        info(f"Translation complete: {ok_count} ok, {fail_count} empty")

        # Reload for metrics
        all_utts = store.get_utterances(episode_id)

        return PhaseResult(
            status="succeeded",
            metrics={
                "total_utterances": len(all_utts),
                "dirty": len(dirty_utts),
                "translated": ok_count,
                "empty": fail_count,
            },
        )

    def _setup_translation(self, ctx: RunContext):
        """Set up translation engine, dict loader, name guard. Returns (translate_fn, is_gemini, dict_loader, name_guard)."""
        phase_config = ctx.config.get("phases", {}).get("translate",
                       ctx.config.get("phases", {}).get("mt", {}))

        # Engine selection
        engine = phase_config.get("engine")
        if not engine:
            model_name = phase_config.get("model", ctx.config.get("mt_model", ""))
            if model_name.startswith("gemini"):
                engine = "gemini"
            elif model_name.startswith("gpt") or model_name.startswith("o1"):
                engine = "openai"
            else:
                engine = ctx.config.get("mt_engine", "gemini")
        engine = engine.lower()
        is_gemini = (engine == "gemini")

        if is_gemini:
            from dubora_core.config.settings import get_gemini_key
            model = phase_config.get("model", ctx.config.get("mt_model",
                    ctx.config.get("gemini_model", "gemini-2.0-flash")))
            api_key = phase_config.get("api_key") or get_gemini_key()
            if not api_key:
                raise RuntimeError("Gemini API key not found")
            default_temp = 0.4
            info(f"Using Gemini translation engine: {model}")
        else:
            from dubora_core.config.settings import get_openai_key
            model = phase_config.get("model", ctx.config.get("mt_model",
                    ctx.config.get("openai_model", "gpt-4o-mini")))
            api_key = phase_config.get("api_key") or get_openai_key()
            if not api_key:
                raise RuntimeError("OpenAI API key not found")
            default_temp = 0.3
            info(f"Using OpenAI translation engine: {model}")

        temperature = phase_config.get("temperature",
                      ctx.config.get("mt_temperature", default_temp))

        translate_fn = create_translate_fn(
            api_key=api_key, model=model, temperature=temperature,
        )

        # Dict loader from DB
        store = ctx.store
        episode_id = ctx.episode_id
        ep = store.get_episode(episode_id) if store and episode_id else None
        drama_id = ep["drama_id"] if ep else 0
        dict_loader = DictLoader(store, drama_id)

        # Name guard
        name_guard = None
        enable_name_guard = phase_config.get("enable_name_guard",
                            ctx.config.get("mt_enable_name_guard", True))
        if enable_name_guard:
            name_guard_config = load_config()
            name_guard = NameGuard(name_guard_config)
            # Sync dict names to name guard whitelist
            for name in dict_loader.names:
                if name not in name_guard.config.known_names:
                    name_guard.config.known_names.append(name)

        return translate_fn, is_gemini, dict_loader, name_guard

    def _load_story_background(self, ctx: RunContext, store, episode_id: int) -> str:
        """Load story background from DB dramas.synopsis."""
        if not store or not episode_id:
            return ""
        ep = store.get_episode(episode_id)
        if not ep:
            return ""
        return store.get_drama_synopsis(ep["drama_id"])

    def _translate_and_write_cues(
        self, utt: dict, cues: list[dict], store,
        translate_fn, is_gemini: bool, dict_loader, name_guard,
        episode_context: str, story_background: str,
    ) -> bool:
        """Translate an utterance and write per-cue text_en.

        Single-cue: direct translation (existing flow).
        Multi-cue: numbered format → LLM returns per-cue translations.

        Returns True if any translation produced non-empty text.
        """
        if len(cues) == 1:
            # ── Single cue: direct translation ──
            en_text = self._translate_single(
                cues[0]["text"], utt, translate_fn, is_gemini,
                dict_loader, name_guard, episode_context, story_background,
            )
            store.update_cue(cues[0]["id"], text_en=en_text)
            return bool(en_text.strip())

        # ── Multi-cue: per-cue numbered translation ──
        return self._translate_multi_cue(
            utt, cues, store, translate_fn, is_gemini,
            dict_loader, name_guard, episode_context, story_background,
        )

    def _translate_single(
        self, zh_text: str, utt: dict,
        translate_fn, is_gemini: bool, dict_loader, name_guard,
        episode_context: str, story_background: str,
    ) -> str:
        """Translate a single text (existing flow)."""
        max_retries = 3
        zh_text = zh_text.strip()
        if not zh_text:
            return ""

        # Apply name guard
        placeholder_to_name = {}
        if name_guard:
            zh_text, placeholder_to_name = name_guard.extract_and_replace_names(zh_text)
            if placeholder_to_name:
                self._ensure_names_in_dict(placeholder_to_name, dict_loader, translate_fn, is_gemini)
                for ph, src_name in placeholder_to_name.items():
                    annotated = f"<<{ph[2:-2]}:{src_name}>>"
                    zh_text = zh_text.replace(ph, annotated)

        # Calculate budget
        total_window_ms = utt["end_ms"] - utt["start_ms"]
        zh_char_count = sum(1 for c in zh_text if '\u4e00' <= c <= '\u9fff')
        zh_tps = zh_char_count / (total_window_ms / 1000.0) if total_window_ms > 0 else 0.0
        k = pick_k(zh_tps)
        budget_ms = total_window_ms * k

        # Glossary
        slang_glossary_text = dict_loader.get_glossary_hits(zh_text)

        # Translate
        en_text, retries = translate_utterance_with_retry(
            zh_text=zh_text, budget_ms=budget_ms, translate_fn=translate_fn,
            max_retries=max_retries, episode_context=episode_context,
            story_background=story_background, slang_glossary_text=slang_glossary_text,
            dict_loader=dict_loader, is_gemini=is_gemini,
        )
        en_text = self._post_process_translation(en_text, placeholder_to_name, dict_loader)
        return en_text

    def _translate_multi_cue(
        self, utt: dict, cues: list[dict], store,
        translate_fn, is_gemini: bool, dict_loader, name_guard,
        episode_context: str, story_background: str,
    ) -> bool:
        """Translate a multi-cue utterance with per-cue numbered format.

        Builds numbered input like:
            [1] 平安你怎么回来了
            [2] 我不是让你去学校了吗
            [3] 你怎么逃课了

        LLM outputs numbered translations:
            [1] PingAn, why are you back?
            [2] Didn't I tell you to go to school?
            [3] Why did you skip class?

        Returns True if any translation produced non-empty text.
        """
        max_retries = 3

        # Build numbered text with [i] markers, then apply name guard to the whole thing
        numbered_lines = []
        for i, cue in enumerate(cues):
            numbered_lines.append(f"[{i + 1}] {cue['text']}")
        marked_text = "\n".join(numbered_lines)

        # Apply name guard to the whole marked text (markers won't be touched)
        placeholder_to_name = {}
        if name_guard:
            marked_text, placeholder_to_name = name_guard.extract_and_replace_names(marked_text)
            if placeholder_to_name:
                self._ensure_names_in_dict(placeholder_to_name, dict_loader, translate_fn, is_gemini)
                for ph, src_name in placeholder_to_name.items():
                    annotated = f"<<{ph[2:-2]}:{src_name}>>"
                    marked_text = marked_text.replace(ph, annotated)

        # Build per-cue input for the prompt
        output_format = "\n".join(f"[{i + 1}] ..." for i in range(len(cues)))
        zh_text = (
            f"This utterance has {len(cues)} sentences by the same speaker. "
            "Translate each sentence separately on numbered lines.\n\n"
            f"{marked_text}\n\n"
            f"Output ONLY translations on numbered lines:\n{output_format}"
        )

        # Calculate budget for the whole utterance
        total_window_ms = utt["end_ms"] - utt["start_ms"]
        merged_cn = "".join(c["text"] for c in cues)
        zh_char_count = sum(1 for c in merged_cn if '\u4e00' <= c <= '\u9fff')
        zh_tps = zh_char_count / (total_window_ms / 1000.0) if total_window_ms > 0 else 0.0
        k = pick_k(zh_tps)
        budget_ms = total_window_ms * k

        # Glossary
        slang_glossary_text = dict_loader.get_glossary_hits(merged_cn)

        # Translate
        raw_output, retries = translate_utterance_with_retry(
            zh_text=zh_text, budget_ms=budget_ms, translate_fn=translate_fn,
            max_retries=max_retries, episode_context=episode_context,
            story_background=story_background, slang_glossary_text=slang_glossary_text,
            dict_loader=dict_loader, is_gemini=is_gemini,
        )

        # Post-process the whole output first (name replacement, cleanup)
        raw_output = self._post_process_translation(raw_output, placeholder_to_name, dict_loader)

        # Parse numbered output into per-cue translations
        per_cue_texts = _parse_numbered_output(raw_output, len(cues))

        # Write each cue's text_en
        any_translated = False
        for i, cue in enumerate(cues):
            en_text = per_cue_texts[i].strip()
            store.update_cue(cue["id"], text_en=en_text)
            if en_text:
                any_translated = True

        if any_translated:
            info(f"Multi-cue translation ({len(cues)} cues): "
                 + " | ".join(t[:30] for t in per_cue_texts if t))

        return any_translated

    def _ensure_names_in_dict(
        self, placeholder_to_name: dict, dict_loader, translate_fn, is_gemini: bool,
    ) -> None:
        """Ensure all names from placeholder_to_name exist in dict_loader."""
        all_src_names = set(placeholder_to_name.values())
        missing = [n for n in all_src_names if not dict_loader.has_name(n)]
        if missing:
            llm_results = complete_names_with_llm(
                missing_names=missing,
                translate_fn=translate_fn,
                is_gemini=is_gemini,
            )
            for src_name, result in llm_results.items():
                dict_loader.add_name(src_name, result["target"])
            dict_loader.save_names()

    def _post_process_translation(
        self, en_text: str, placeholder_to_name: dict, dict_loader,
    ) -> str:
        """Post-process a translated text: replace placeholders, clean, strip Chinese."""
        if not en_text:
            return ""

        if placeholder_to_name:
            placeholder_pattern = r'<<NAME_(\d+)(?::[^>]*)?>>'
            matches = list(re.finditer(placeholder_pattern, en_text))
            for match in reversed(matches):
                ph_idx = int(match.group(1))
                ph = f"<<NAME_{ph_idx}>>"
                if ph in placeholder_to_name:
                    src_name = placeholder_to_name[ph]
                    en_name = dict_loader.resolve_name(src_name)
                    if en_name:
                        en_text = en_text[:match.start()] + en_name + en_text[match.end():]

        if placeholder_to_name:
            for ph, src_name in placeholder_to_name.items():
                en_name = dict_loader.resolve_name(src_name)
                if not en_name or en_name.lower() in en_text.lower():
                    continue
                wrong_variants = _build_name_variants(en_name, src_name)
                for variant in wrong_variants:
                    pattern = re.compile(re.escape(variant), re.IGNORECASE)
                    if pattern.search(en_text):
                        en_text = pattern.sub(en_name, en_text)
                        break

        en_text = clean_translation_output(en_text)

        if re.search(r'[\u4e00-\u9fff]', en_text):
            en_text = re.sub(r'[\u4e00-\u9fff]+', '', en_text).strip()
            en_text = re.sub(r'\s+', ' ', en_text).strip()

        return en_text

    def _calc_tts_policy(
        self, utt: dict, sorted_utts: list[dict],
        audio_duration_ms: int, ctx: RunContext,
    ) -> dict:
        """Calculate TTS policy for an utterance (max_rate + allow_extend_ms)."""
        tts_config = ctx.config.get("phases", {}).get("tts", {})
        default_max_rate = float(tts_config.get("max_rate", 1.3))
        min_tts_window_ms = int(tts_config.get("min_tts_window_ms", 900))
        max_extend_cap_ms = int(tts_config.get("max_extend_cap_ms", 800))
        default_allow_extend_ms = int(tts_config.get("allow_extend_ms", 500))

        budget_ms = utt["end_ms"] - utt["start_ms"]

        # Find gap to next utterance
        gap_to_next_ms = None
        for i, u in enumerate(sorted_utts):
            if u["id"] == utt["id"] and i + 1 < len(sorted_utts):
                gap_to_next_ms = sorted_utts[i + 1]["start_ms"] - utt["end_ms"]
                break

        if gap_to_next_ms is not None and gap_to_next_ms > 0:
            allow_extend_ms = max(0, gap_to_next_ms - 60)
        else:
            gap_to_end = audio_duration_ms - utt["end_ms"] if audio_duration_ms else 0
            allow_extend_ms = gap_to_end if gap_to_end > 0 else default_allow_extend_ms

        if budget_ms < min_tts_window_ms:
            allow_extend_ms = max(
                allow_extend_ms,
                min(min_tts_window_ms - budget_ms, max_extend_cap_ms),
            )

        return {"max_rate": default_max_rate, "allow_extend_ms": allow_extend_ms}


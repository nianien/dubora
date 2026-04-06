"""
CLI entry point for dubora-web

Commands:
  vsd-web serve [--port] [--static-dir] [--dev]
"""
import argparse
import sys
from pathlib import Path

from dubora_core.config.settings import load_env_file
from dubora_core.utils.logger import info, error


def main():
    parser = argparse.ArgumentParser(description="Dubora web server")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    serve_parser = subparsers.add_parser("serve", help="Start web server")
    serve_parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    serve_parser.add_argument("--static-dir", type=str, default=None, help="Path to frontend dist/")
    serve_parser.add_argument("--dev", action="store_true", help="Development mode (no static serving)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    load_env_file()

    if args.command == "serve":
        _cmd_serve(args)


def _auto_build_frontend(web_dir: Path, dist_dir: Path):
    """Auto build frontend if dist/ is missing or outdated (source newer than dist)."""
    import subprocess

    needs_build = False
    if not dist_dir.is_dir():
        needs_build = True
    else:
        # Check if any source file is newer than dist/index.html
        index_html = dist_dir / "index.html"
        if not index_html.is_file():
            needs_build = True
        else:
            dist_mtime = index_html.stat().st_mtime
            for check_dir in [web_dir / "src", web_dir / "public"]:
                if needs_build:
                    break
                if check_dir.is_dir():
                    for f in check_dir.rglob("*"):
                        if f.is_file() and f.stat().st_mtime > dist_mtime:
                            needs_build = True
                            break

    if not needs_build:
        return

    # Check node_modules
    if not (web_dir / "node_modules").is_dir():
        info("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], cwd=str(web_dir), check=True)

    info("Building frontend...")
    subprocess.run(["npm", "run", "build"], cwd=str(web_dir), check=True)
    info("Frontend build complete.")


def _cmd_serve(args):
    try:
        import uvicorn
        from dubora_web.server import create_app
    except ImportError:
        error("Web dependencies not installed. Run: pip install dubora-web")
        sys.exit(1)

    static_dir = args.static_dir
    if not args.dev and static_dir is None:
        # Try to find web/dist relative to cwd
        web_dir = Path("web")
        candidate = web_dir / "dist"
        if web_dir.is_dir() and (web_dir / "package.json").is_file():
            _auto_build_frontend(web_dir, candidate)
        if candidate.is_dir():
            static_dir = str(candidate.resolve())

    app = create_app(static_dir=static_dir)

    info(f"Starting on http://localhost:{args.port}")
    if args.dev:
        info("Dev mode: use 'cd web && npm run dev' for frontend")
    elif static_dir:
        info(f"Serving frontend from: {static_dir}")

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()

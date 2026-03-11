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
        candidate = Path("web") / "dist"
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

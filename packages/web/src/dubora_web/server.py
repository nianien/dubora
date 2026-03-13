"""
FastAPI server for ASR Calibration IDE

启动方式：vsd ide [--port 8765]
"""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from dubora_core.config.settings import get_web_data_dir, get_pipeline_data_dir, get_db_path, get_upload_cache_dir, get_gcs_cache_dir
from dubora_web.api.auth import router as auth_router, auth_enabled, _get_session
from dubora_web.api.emotions import router as emotions_router
from dubora_web.api.episodes import router as episodes_router
from dubora_web.api.export import router as export_router
from dubora_web.api.media import router as media_router
from dubora_web.api.pipeline import router as pipeline_router
from dubora_web.api.roles import router as roles_router
from dubora_web.api.cues import router as cues_router
from dubora_web.api.voices import router as voices_router
from dubora_web.api.glossary import router as glossary_router
from dubora_web.api.worker import router as worker_router
from dubora_core.utils.file_store import FileStore

_AUTH_SKIP_PREFIXES = ("/api/auth/", "/api/health", "/api/worker/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not auth_enabled():
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") or any(path.startswith(p) for p in _AUTH_SKIP_PREFIXES):
            return await call_next(request)
        session = _get_session(request)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        request.state.user_id = session.get("user_id")
        return await call_next(request)


def create_app(
    static_dir: Optional[str] = None,
) -> FastAPI:
    """
    创建 FastAPI 应用。

    Args:
        static_dir: 前端静态文件目录（None 则不挂载）
    """
    get_web_data_dir()  # ensure subdirs created
    app = FastAPI(
        title="ASR Calibration IDE",
        version="1.0.0",
    )

    # Auth middleware (must be added before CORS so it runs after CORS)
    app.add_middleware(AuthMiddleware)

    # CORS（开发模式下允许 Vite dev server）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.db_path = get_db_path()

    # File access layer: local-first, GCS fallback
    file_store = FileStore()
    file_store.add_local(get_pipeline_data_dir() / "dub", "/api/media/dub")
    file_store.add_local(get_upload_cache_dir(), "/api/media")
    file_store.set_gcs_cache_dir(get_gcs_cache_dir())
    app.state.file_store = file_store

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # 注册 API 路由
    app.include_router(auth_router, prefix="/api")
    app.include_router(emotions_router, prefix="/api")
    app.include_router(episodes_router, prefix="/api")
    app.include_router(export_router, prefix="/api")
    app.include_router(media_router, prefix="/api")
    app.include_router(pipeline_router, prefix="/api")
    app.include_router(roles_router, prefix="/api")
    app.include_router(cues_router, prefix="/api")
    app.include_router(voices_router, prefix="/api")
    app.include_router(glossary_router, prefix="/api")
    app.include_router(worker_router, prefix="/api")

    # 挂载前端静态文件（生产模式）— SPA catch-all
    if static_dir and Path(static_dir).is_dir():
        static_path = Path(static_dir).resolve()
        # Serve /assets as static files (JS/CSS bundles)
        assets_dir = static_path / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # SPA catch-all: any non-API path returns index.html
        index_html = static_path / "index.html"

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # Serve existing static files (favicon, etc.) if they exist
            candidate = static_path / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_html)

    return app

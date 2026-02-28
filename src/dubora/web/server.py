"""
FastAPI server for ASR Calibration IDE

启动方式：vsd ide [--port 8765] [--videos ./videos/]
"""
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dubora.web.api.emotions import router as emotions_router
from dubora.web.api.episodes import router as episodes_router
from dubora.web.api.asr_model import router as asr_model_router
from dubora.web.api.export import router as export_router
from dubora.web.api.media import router as media_router
from dubora.web.api.pipeline import router as pipeline_router
from dubora.web.api.roles import router as roles_router


def create_app(
    videos_dir: str = "./videos",
    static_dir: Optional[str] = None,
) -> FastAPI:
    """
    创建 FastAPI 应用。

    Args:
        videos_dir: 视频根目录路径
        static_dir: 前端静态文件目录（None 则不挂载）
    """
    app = FastAPI(
        title="ASR Calibration IDE",
        version="1.0.0",
    )

    # CORS（开发模式下允许 Vite dev server）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 存储 videos_dir 到 app state
    app.state.videos_dir = Path(videos_dir).resolve()

    # 注册 API 路由
    app.include_router(emotions_router, prefix="/api")
    app.include_router(episodes_router, prefix="/api")
    app.include_router(asr_model_router, prefix="/api")
    app.include_router(export_router, prefix="/api")
    app.include_router(media_router, prefix="/api")
    app.include_router(pipeline_router, prefix="/api")
    app.include_router(roles_router, prefix="/api")

    # 挂载前端静态文件（生产模式）
    if static_dir and Path(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app

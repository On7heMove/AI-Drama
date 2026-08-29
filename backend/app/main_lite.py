"""生成版精简入口（exe 打包用）：只提供「生成视频提示词」链路，不挂逻辑校验/生产线路由。

裁剪范围（2026-08-21 用户确认：exe 只做生成）：
- 挂载路由：auth(登录) / settings(Key配置) / works(作品+生成+导出)
- 不挂载：production(剧本生产线) / license(激活) / check·events / review(逻辑校验/礼俗)
- 不 import：engine(逻辑校验) / review(礼俗) / quality(质检) / production 生产线
- 前端：web/ 静态资源托管（P5 交付目录；dev 时可用 frontend/dist）

run_server.py 已按此入口编译（Nuitka 静态收集只会带上生成链路依赖）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.settings import router as settings_router
from app.api.works import router as works_router
from app.api.inverse import router as inverse_router
from app.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")

app.include_router(settings_router)
app.include_router(auth_router)
app.include_router(works_router)
app.include_router(inverse_router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
    }


# ---------- 前端静态托管（P5：server.exe 提供 web/ 页面） ----------
def _web_dir() -> Path:
    """定位前端静态目录，按打包形态依次尝试：
    1. AIDRAMA_RUNTIME/web（交付目录，exe 旁）
    2. PyInstaller 解压目录 sys._MEIPASS/web
    3. 源码 frontend/dist（开发期）
    4. 兜底 Path("web")
    """
    import os
    import sys
    runtime = os.environ.get("AIDRAMA_RUNTIME")
    if runtime:
        cand = Path(runtime) / "web"
        if (cand / "index.html").exists():
            return cand
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "web"
        if (cand / "index.html").exists():
            return cand
    cand2 = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (cand2 / "index.html").exists():
        return cand2
    return Path("web")


_WEB = _web_dir()
if (_WEB / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="web")
else:
    @app.get("/")
    async def _index_placeholder() -> dict:
        return {"status": "ok", "note": "web/ 前端未就绪（P5 打包时随 server.exe 提供）"}

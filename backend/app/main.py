"""FastAPI 入口：健康检查 + 事件流校验 + 剧本整体检测（骨架）。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api.auth import router as auth_router
from app.api.inverse import router as inverse_router
from app.api.license import router as license_router
from app.api.production import router as production_router
from app.api.settings import router as settings_router
from app.api.works import router as works_router
from app.config import settings
from app.engine.detector import DetectionEngine, DetectionReport
from app.review.customs_store import CustomsStore
from app.schemas.character import CharacterProfile
from app.schemas.events import Event
from app.schemas.review import CustomsOverride
from app.state.store import EventSourcingStore

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(production_router)
app.include_router(settings_router)
app.include_router(license_router)
app.include_router(auth_router)
app.include_router(works_router)
app.include_router(inverse_router)

# 审核人自定义礼俗规范存储（骨架：内存，按 doc_id 隔离；生产切数据库）
customs_store = CustomsStore()


class ScriptCheckRequest(BaseModel):
    doc_id: str = ""
    events: list[Event] = Field(default_factory=list)
    profiles: list[CharacterProfile] = Field(default_factory=list)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
    }


@app.post("/api/v1/check/events")
async def check_events(events: list[Event]) -> dict:
    """把一串结构化事件喂给确定性状态机，返回矛盾清单与最终快照。"""
    store = EventSourcingStore()
    for ev in events:
        store.apply(ev)
    return {
        "violations": [v.model_dump() for v in store.violations()],
        "snapshots": {
            f"{k[0]}@{k[1]}": v.model_dump() for k, v in store.snapshots().items()
        },
    }


@app.post("/api/v1/check/script", response_model=DetectionReport)
async def check_script(req: ScriptCheckRequest) -> DetectionReport:
    """剧本整体检测（骨架）：事件流 + 角色卡 -> 检测报告（矛盾/已解释合理/快照/分支）。"""
    engine = DetectionEngine(profiles=req.profiles, doc_id=req.doc_id, customs_store=customs_store)
    return engine.process(req.events)


@app.get("/api/v1/review/{doc_id}/customs", response_model=list[CustomsOverride])
async def list_customs(doc_id: str) -> list[CustomsOverride]:
    """列出当前文档的审核人自定义礼俗（文档级作用域）。"""
    return customs_store.list(doc_id)


@app.post("/api/v1/review/{doc_id}/customs", response_model=CustomsOverride)
async def add_custom(doc_id: str, override: CustomsOverride) -> CustomsOverride:
    """审核人入口：为当前文档添加自定义礼俗规范（仅作用于该文档）。"""
    return customs_store.add(doc_id, override)


@app.delete("/api/v1/review/{doc_id}/customs/{override_id}")
async def remove_custom(doc_id: str, override_id: str) -> dict:
    """删除当前文档的一条自定义礼俗。"""
    return {"removed": customs_store.remove(doc_id, override_id)}


@app.get("/api/v1/review/{doc_id}/customs/effective")
async def effective_customs(doc_id: str, era: str, domain: str) -> dict:
    """当前文档生效的礼俗（全局默认 + 文档覆盖），供审核与 L4 判定使用。"""
    return customs_store.effective(doc_id, era, domain)

_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>AI 短剧生产线服务</title>
<style>
  body { font-family: "Microsoft YaHei", sans-serif; background: #101418; color: #e6edf3; margin: 0; padding: 40px 20px; }
  .card { max-width: 720px; margin: 0 auto; background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 32px; }
  h1 { margin-top: 0; }
  .status { display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: 14px; }
  .ok { background: #12261a; color: #3fb950; border: 1px solid #238636; }
  .bad { background: #2d1b1b; color: #f85149; border: 1px solid #da3633; }
  a { color: #58a6ff; text-decoration: none; }
  a:hover { text-decoration: underline; }
  ul { list-style: none; padding: 0; }
  li { padding: 8px 0; border-bottom: 1px solid #21262d; }
  .desc { color: #8b949e; font-size: 13px; }
</style>
</head>
<body>
<div class="card">
  <h1>🎬 AI 短剧生产线服务</h1>
  <p>服务状态：<span id="st" class="status bad">检测中…</span></p>
  <p class="desc">版本 0.1.0 ｜ 本地部署 · 代码混淆 ｜ 数据目录：运行目录下 data/、archive/</p>
  <h3>入口</h3>
  <ul>
    <li><a href="/docs">📖 接口文档（可交互测试）</a></li>
    <li><a href="/health">❤️ 健康检查</a></li>
    <li><a href="/api/v1/settings">⚙️ 服务设置（LLM / 视频 API）</a></li>
    <li><a href="/api/v1/license/status">🔑 激活状态</a></li>
  </ul>
  <h3>核心 API</h3>
  <ul>
    <li><code>POST /api/v1/production/jobs</code> <span class="desc">创建生产任务（剧本生产线）</span></li>
    <li><code>GET  /api/v1/production/jobs/{id}</code> <span class="desc">轮询任务进度</span></li>
    <li><code>POST /api/v1/check/events</code> <span class="desc">本地逻辑校验（状态机+规则）</span></li>
    <li><code>POST /api/v1/license/request</code> <span class="desc">生成激活申请码</span></li>
  </ul>
</div>
<script>
fetch('/health').then(r => r.json()).then(j => {
  const el = document.getElementById('st');
  el.textContent = '运行正常 · ' + j.llm_model;
  el.className = 'status ok';
}).catch(() => {
  const el = document.getElementById('st');
  el.textContent = '异常';
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """中文欢迎首页：状态 + 接口入口。"""
    return _INDEX_HTML
"""作品 API：作品管理 + 剧本导入 + 四类提示词生成/编辑/版本 + 导出（P2b）。

链路：POST /works → PUT /works/{id}/script → POST /works/{id}/prompts(生成任务)
      → GET /works/{id}/prompts → PUT 编辑 → GET /works/{id}/export
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
import json
import re
import zipfile
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.production.llm_client import DeepSeekClient
from app.promptgen.pipeline import TYPES, generate_prompt_set
from app.services import jobs, works_store

# 支持的剧本导入格式
SUPPORTED_UPLOAD_EXTS = {".txt", ".md", ".markdown", ".epub", ".docx"}

router = APIRouter(prefix="/api/v1/works", tags=["works"])


class WorkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    genre: str = ""
    aspect: str = Field(default="9:16", pattern="^(9:16|16:9|2\\.39:1|2\\.35:1|21:9|1\\.85:1|4:3|1:1)$")
    script_text: str = ""   # 可选：新建作品时一步导入剧本文本（含多格式文件解析后的文本）
    brief: dict = Field(default_factory=dict)   # 可选：故事梗概 + 必要信息（年代/背景/世界观/法则/风格/立意/冲突/主角/结局/禁忌）


class ScriptSave(BaseModel):
    text: str
    title: str = ""
    genre: str = ""


class PromptGenerate(BaseModel):
    types: list[Literal["plot", "characters", "scenes", "video"]] = Field(default_factory=list)
    aspect: str = Field(default="9:16", pattern="^(9:16|16:9|2\\.39:1|2\\.35:1|21:9|1\\.85:1|4:3|1:1)$")
    max_episodes: int = Field(default=80, ge=1, le=200)


class PromptSave(BaseModel):
    items: list


class ExportQuery(BaseModel):
    scope: Literal["work", "ep"] = "work"
    ep: int | None = None
    formats: list[Literal["json", "txt", "md"]] = Field(default_factory=lambda: ["json"])


# ---------- 作品 CRUD ----------

@router.get("")
async def list_works() -> dict:
    return {"works": works_store.list_works()}


@router.post("")
async def create_work(body: WorkCreate) -> dict:
    meta = works_store.create_work(body.title, body.genre, aspect=body.aspect, brief=body.brief or None)
    if body.script_text and body.script_text.strip():
        works_store.save_script(meta["id"], body.script_text)
        works_store.update_meta(meta["id"], status="script_ready")
    return works_store.get_work(meta["id"]) or meta


class ImportFileResult(BaseModel):
    """导入文件解析结果（不落库，仅返回文本供前端确认后保存）。"""
    filename: str = ""
    ext: str = ""
    text: str = ""
    chars: int = 0
    estimate: dict | None = None
    error: str = ""


@router.post("/import-file")
async def import_script_file(file: UploadFile = File(...)) -> dict:
    """解析上传的剧本文本文件（txt/md/epub/docx）→ 返回文本 + 字数 + 时长估算。

    不直接落库：前端拿到 text 后，随新建作品/保存剧本一并提交。
    """
    try:
        data = await file.read()
    except Exception as exc:  # noqa: BLE001
        return ImportFileResult(filename=file.filename or "", error=f"读取失败：{exc}").model_dump()
    try:
        text = _extract_text_from_bytes(file.filename or "", data)
    except ValueError as exc:
        return ImportFileResult(filename=file.filename or "", ext=Path(file.filename or "").suffix.lower(), error=str(exc)).model_dump()
    ext = Path(file.filename or "").suffix.lower()
    return ImportFileResult(
        filename=file.filename or "",
        ext=ext,
        text=text,
        chars=len(text),
        estimate=_estimate_duration(len(text)),
    ).model_dump()


@router.get("/{work_id}")
async def get_work(work_id: str) -> dict:
    meta = works_store.get_work(work_id)
    if meta is None:
        return {"error": "作品不存在", "status": "not_found"}
    return {**meta, "has_script": bool(works_store.get_script(work_id))}


@router.delete("/{work_id}")
async def delete_work(work_id: str) -> dict:
    return {"deleted": works_store.delete_work(work_id)}


# ---------- 剧本 ----------

@router.get("/{work_id}/script")
async def get_script(work_id: str) -> dict:
    """读取已导入的剧本（前端回显用）。"""
    meta = works_store.get_work(work_id)
    if meta is None:
        return {"error": "作品不存在", "status": "not_found"}
    return {"text": works_store.get_script(work_id)}


@router.put("/{work_id}/script")
async def save_script(work_id: str, body: ScriptSave) -> dict:
    if body.title:
        works_store.update_meta(work_id, title=body.title)
    if body.genre:
        works_store.update_meta(work_id, genre=body.genre)
    meta = works_store.save_script(work_id, body.text)
    if meta is None:
        return {"error": "作品不存在", "status": "not_found"}
    chars = len(body.text)
    return {"status": "saved", "chars": chars, "estimate": _estimate_duration(chars)}


# ---------- 提示词生成（异步任务） ----------

@router.post("/{work_id}/prompts")
async def generate_prompts(work_id: str, body: PromptGenerate) -> dict:
    meta = works_store.get_work(work_id)
    if meta is None:
        return {"error": "作品不存在", "status": "not_found"}
    text = works_store.get_script(work_id)
    if not text.strip():
        return {"error": "请先导入剧本", "status": "no_script"}
    types = tuple(body.types) if body.types else ("video",)

    job_id = jobs.create_job(kind="promptgen", total=len(types))
    meta2 = works_store.update_meta(work_id, status="generating")
    # 画幅比例：以作品创建时选择（meta.aspect）为准，旧作品回退请求参数/默认 9:16
    aspect = (meta2 or meta).get("aspect") or body.aspect or "9:16"

    async def worker() -> None:
        jobs.set_running(job_id)
        try:
            result = await generate_prompt_set(
                text,
                title=meta2["title"] if meta2 else meta["title"],
                genre=meta2["genre"] if meta2 else meta["genre"],
                client=DeepSeekClient(),
                storyboard_aspect=aspect,
                types=types,
                max_episodes=body.max_episodes,
            )
            mapping = {
                "plot": result.plot,
                "characters": result.characters,
                "scenes": result.scenes,
                "video": result.video,
            }
            for i, t in enumerate(types, 1):
                items = [x.model_dump(mode="json") if hasattr(x, "model_dump") else x for x in mapping[t]]
                works_store.save_prompts(work_id, t, items)
                jobs.update_job(job_id, done=i)
            works_store.update_meta(work_id, status="completed")
            jobs.finish_job(job_id, result={"generated": list(types)})
            _record_elapsed(work_id, job_id)
        except Exception as exc:  # noqa: BLE001
            works_store.update_meta(work_id, status="failed")
            jobs.finish_job(job_id, error=f"{type(exc).__name__}: {exc}")
            _record_elapsed(work_id, job_id)

    asyncio.create_task(worker())
    return {"job_id": job_id, "status": "queued"}


@router.get("/{work_id}/jobs/{job_id}")
async def get_work_job(work_id: str, job_id: str) -> dict:
    """轮询生成任务进度（works 内嵌，避免依赖 production 生产流水线路由）。

    返回 status/done/total/stage/error/elapsed_seconds（与 production.jobs 同语义）。
    """
    job = jobs.get_job(job_id)
    if job is None:
        return {"job_id": job_id, "status": "not_found"}
    return jobs.job_to_dict(job)


# ---------- 提示词读写 / 版本 ----------

@router.get("/{work_id}/prompts")
async def get_all_prompts(work_id: str) -> dict:
    return works_store.get_all_prompts(work_id)


@router.get("/{work_id}/prompts/{ptype}")
async def get_prompts(work_id: str, ptype: str) -> dict:
    data = works_store.get_prompts(work_id, ptype)
    if data is None:
        return {"error": f"{ptype} 未生成", "status": "not_generated"}
    return data


@router.put("/{work_id}/prompts/{ptype}")
async def save_prompts(work_id: str, ptype: str, body: PromptSave) -> dict:
    try:
        data = works_store.save_prompts(work_id, ptype, body.items)
    except ValueError as exc:
        return {"error": str(exc), "status": "bad_type"}
    if data is None:
        return {"error": "作品不存在", "status": "not_found"}
    return {"status": "saved", "version": data["version"]}


@router.get("/{work_id}/prompts/{ptype}/versions")
async def get_versions(work_id: str, ptype: str) -> dict:
    return {"versions": works_store.list_versions(work_id, ptype)}


@router.post("/{work_id}/prompts/{ptype}/versions/{version}/restore")
async def restore_version(work_id: str, ptype: str, version: int) -> dict:
    data = works_store.restore_version(work_id, ptype, version)
    if data is None:
        return {"error": "版本不存在", "status": "not_found"}
    return {"status": "restored", "version": data["version"]}


# ---------- 导出 ----------

def _ep_filtered(payload: dict, ep: int | None) -> dict:
    """按集筛选：plot 按 ep 精确过滤；video 按 scene_id e{ep}_ 前缀过滤；其它类型全量。"""
    if ep is None:
        return payload
    items = payload.get("items", [])
    keep = [it for it in items if it.get("ep") == ep or it.get("scene_id", "").startswith(f"e{ep}_")]
    return {**payload, "items": keep}


def _extract_text_from_bytes(filename: str, data: bytes) -> str:
    """从上传文件解析纯文本（支持 txt/md/epub/docx，2026-08-21 新增多格式导入）。

    - txt/md：直接 UTF-8 解码（容错 GBK）
    - epub：zipfile 读 .xhtml/.html 并剥标签
    - docx：zipfile 读 word/document.xml 剥标签
    失败抛 ValueError（带明确原因）。
    """
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_UPLOAD_EXTS:
        raise ValueError(f"不支持的文件格式：{ext or '无扩展名'}（支持：txt/md/epub/docx）")

    # 纯文本
    if ext in (".txt", ".md", ".markdown"):
        for enc in ("utf-8", "gbk", "utf-8-sig"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    # EPUB / DOCX（都是 zip + XML）
    if ext in (".epub", ".docx"):
        try:
            import zipfile
            import xml.etree.ElementTree as ET

            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if ext == ".epub":
                    # 收集 spine 顺序的 html 文件（简化：按文件名/OPF）
                    names = [n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))]
                    names.sort(key=lambda n: (len(n), n))  # 优先根目录/短名
                    parts = []
                    for n in names:
                        raw = z.read(n)
                        m = re.search(rb"<body[^>]*>(.*?)</body>", raw, re.S | re.I)
                        frag = m.group(1) if m else raw
                        txt = re.sub(rb"<[^>]+>", b"", frag)
                        parts.append(txt.decode("utf-8", errors="replace"))
                    text = "\n\n".join(p for p in parts if p.strip())
                else:  # docx
                    xml = z.read("word/document.xml")
                    root = ET.fromstring(xml)
                    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                    paras = []
                    for p_el in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                        runs = [t.text or "" for t in p_el.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
                        line = "".join(runs).strip()
                        if line:
                            paras.append(line)
                    text = "\n".join(paras)
            if not text.strip():
                raise ValueError(f"{ext.upper()} 未提取到文本")
            return text
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{ext.upper()} 解析失败：{exc}") from exc

    raise ValueError(f"不支持的文件格式：{ext}")


def _estimate_duration(chars: int) -> dict:
    """根据字数估算视频提示词生成时长（2026-08-21 工程实测校准）。

    实测数据点（后端 8081 实测）：
    - 278 字 → 45 秒（固定开销主导：LLM 连接/角色提取/剧情 + 校验）
    - 8211 字 → 344 秒（完整剧本）
    线性拟合：时长(秒) ≈ 45 + chars × 0.038（即固定约 45s + 每千字约 38s）。

    返回 {"seconds": int, "minutes": int, "chars": int, "basis": "实测校准 2026-08-21"}；
    字数过短(<30)时给最小值提示。
    """
    if chars <= 0:
        return {"seconds": 0, "minutes": 0, "chars": chars, "note": "空剧本"}
    sec = max(30, int(45 + chars * 0.038))
    note = ""
    if chars < 100:
        note = "剧本过短，可能无法提取场景（需含 场景/第N场 结构）"
    return {"seconds": sec, "minutes": max(1, round(sec / 60)), "chars": chars, "note": note, "basis": "实测校准 2026-08-21"}


def _record_elapsed(work_id: str, job_id: str) -> None:
    """把生成任务真实耗时写入作品 meta（持久化，供时长统计与估算校准）。"""
    j = jobs.get_job(job_id)
    if j is None or j.elapsed_seconds is None:
        return
    meta = works_store.get_work(work_id)
    if meta is None:
        return
    sec = j.elapsed_seconds
    works_store.update_meta(
        work_id,
        last_generate_elapsed_sec=sec,
        last_generate_duration_min=round(sec / 60, 1),
        last_generate_at=j.updated_at,
    )


def _episode_numbers(all_prompts: dict) -> list[int]:
    """交付包内出现的集号（plot.ep / video.ep / video.scene_id e{n}_）。"""
    eps: set[int] = set()
    for it in all_prompts.get("video", {}).get("items", []):
        if it.get("ep"):
            eps.add(int(it["ep"]))
        m = re.match(r"e(\d+)_", str(it.get("scene_id", "")))
        if m:
            eps.add(int(m.group(1)))
    return sorted(eps)


def _format_files(all_prompts: dict, fmt_list: list[str], ep: int | None) -> dict[str, bytes]:
    """按格式生成 prompts.json/txt/md；ep 非空时按集筛选（txt/md 沿用原 scope=ep 语义）。"""
    files: dict[str, bytes] = {}
    for fmt in fmt_list:
        if fmt == "json":
            body = {"video": _ep_filtered(all_prompts.get("video", {}), ep)}
            files["prompts.json"] = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        elif fmt == "txt":
            files["prompts.txt"] = _txt_body(all_prompts, ep, "ep" if ep else "work").encode("utf-8")
        elif fmt == "md":
            files["prompts.md"] = _md_body(all_prompts, ep, "ep" if ep else "work").encode("utf-8")
    return files


def _safe_top_dir(title: str) -> str:
    """作品标题 → 安全顶层文件夹名（与 works_store._safe_id 同规则）。"""
    base = re.sub(r"[^\w\u4e00-\u9fff-]+", "", title or "")[:24] or "work"
    return base


def _zip_response(files: dict[str, bytes], fname: str, top_dir: str = "") -> Response:
    """打包 zip；top_dir 非空时所有文件置于该顶层文件夹下（解压不散落）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            entry = f"{top_dir}/{name}" if top_dir else name
            z.writestr(entry, data)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(fname)},
    )


@router.get("/{work_id}/export")
async def export_work(work_id: str, scope: str = "work", ep: int | None = None,
                      formats: str = "json", pack: str = "work") -> Response:
    """导出交付包（PRD-701/Q4：作品一包 + 按集一包）。

    formats=json,txt,md（逗号分隔）；scope=work|ep（ep 指定单集）；
    pack=work 作品一包（zip 内含 prompts.json/txt/md，scope=ep 时仅该集）；
    pack=ep 按集一包（zip 内含 e1/…/eN/ 子目录，每集 prompts.json/txt/md + 根 meta.json 元信息）。
    """
    meta = works_store.get_work(work_id)
    if meta is None:
        return Response(json.dumps({"error": "作品不存在"}), media_type="application/json", status_code=404)
    all_prompts = works_store.get_all_prompts(work_id)
    fmt_list = [f.strip() for f in formats.split(",") if f.strip()]

    if pack == "ep":
        files: dict[str, bytes] = {
            "meta.json": json.dumps(
                {"id": meta["id"], "title": meta["title"], "genre": meta.get("genre", ""),
                 "status": meta.get("status", ""), "updated_at": meta.get("updated_at", "")},
                ensure_ascii=False, indent=2).encode("utf-8"),
        }
        for n in _episode_numbers(all_prompts):
            for name, data in _format_files(all_prompts, fmt_list, n).items():
                files[f"e{n}/{name}"] = data
        top = _safe_top_dir(meta.get("title", ""))
        return _zip_response(files, f"{work_id}_按集.zip", top_dir=top)

    # 作品一包 / 指定集一包（原行为）
    files = _format_files(all_prompts, fmt_list, ep if scope == "ep" else None)
    fname = f"{work_id}_{scope}" + (f"_e{ep}" if ep else "") + ".zip"
    top = _safe_top_dir(meta.get("title", ""))
    return _zip_response(files, fname, top_dir=top)

def _txt_body(all_prompts: dict, ep: int | None, scope: str) -> str:
    """导出只含视频提示词（2026-08-21 交付范围收窄：其他提示词不再交付）。"""
    lines = []
    for it in all_prompts.get("video", {}).get("items", []):
        if ep and it.get("ep") != ep:
            continue
        if it.get("ep") and it.get("scene_id"):
            lines.append(f"### 第{it['ep']}集 {it['scene_id']}")
        for p in it.get("image_prompts", []):
            lines.append(p)
    return "\n\n".join(x for x in lines if x)
def _md_body(all_prompts: dict, ep: int | None, scope: str) -> str:
    """导出只含视频提示词（2026-08-21 交付范围收窄）。"""
    parts = ["# 视频提示词导出"]
    for it in all_prompts.get("video", {}).get("items", []):
        if ep and it.get("ep") != ep:
            continue
        head = f"### 第{it.get('ep')}集 {it.get('scene_id','')}"
        body = "\n\n".join(it.get("image_prompts", []))
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)
    script_text: str = ""   # 可选：新建作品时一步导入剧本文本（含多格式文件解析后的文本）

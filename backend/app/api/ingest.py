"""入库清单 API：配置开关、待出清单、清单读写、生成请求队列。

外置 Agent 通过 X-API-Token 调用本组接口；页面通过登录会话调用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.deps import DbDep
from app.models.ingest import IngestManifest, IngestRequest
from app.models.incoming_file import IncomingFile
from app.services import ingest_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _clean_payload(payload: dict | None) -> dict | None:
    """读清单时过滤掉历史脏实体（模型曾把数组塞成字符串，解析出 "[" 这类名字）。"""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    for key in ("artists", "groups", "songs"):
        rows = out.get(key)
        if isinstance(rows, list):
            out[key] = [
                x for x in rows
                if isinstance(x, dict) and len(str(x.get("name") or "").strip()) >= 2
            ]
    rows = out.get("new_entities")
    if isinstance(rows, list):
        out["new_entities"] = [
            x for x in rows
            if isinstance(x, dict) and len(str(x.get("name") or "").strip()) >= 2
        ]
    return out


def _norm_path(value: str) -> str:
    """修复历史遗留的代理字符路径。

    容器早期没有 locale，Python 按 ASCII + surrogateescape 解码文件名，
    中文/韩文标题会变成孤立代理字符；这里把代理字符还原成原始字节再按 UTF-8 解码。
    """
    try:
        return value.encode("utf-8", "surrogateescape").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value

_INFO_KEEP = (
    "id", "title", "uploader", "channel", "upload_date", "release_date",
    "duration", "width", "height", "tags", "categories", "webpage_url", "extractor",
)


def _clip_info(info: dict) -> dict:
    """只给 Agent 判断需要的字段；简介截断，避免上下文过大。"""
    out = {k: info.get(k) for k in _INFO_KEEP if info.get(k) is not None}
    desc = info.get("description")
    if isinstance(desc, str):
        out["description"] = desc[:1200]
    return out


# ------------------------------------------------------------------ schemas


class ConfigPatch(BaseModel):
    draft_enabled: Optional[bool] = None
    ai_button_enabled: Optional[bool] = None
    agent_enabled: Optional[bool] = None
    agent_schedule: Optional[list[str]] = None
    # 内置 Agent（容器内直连 API）：独立开关 + 运行参数
    builtin_enabled: Optional[bool] = None
    builtin_max_steps: Optional[int] = None
    builtin_batch: Optional[int] = None
    builtin_model: Optional[str] = None
    # 外部数据源
    qqmusic_enabled: Optional[bool] = None
    qqmusic_api_key: Optional[str] = None
    qqmusic_base_url: Optional[str] = None
    qqmusic_skill_version: Optional[str] = None
    # 内置 Agent 独立模型
    agent_base_url: Optional[str] = None
    agent_api_key: Optional[str] = None
    agent_model: Optional[str] = None
    harness_enabled: Optional[bool] = None
    harness_interval_minutes: Optional[int] = None
    web_search_enabled: Optional[bool] = None


class ManifestWrite(BaseModel):
    file_path: str
    fingerprint: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None
    source: str = "agent"
    model: Optional[str] = None
    status: str = "ready"


class RequestCreate(BaseModel):
    scope: str = "all"  # all / file / manifest
    target: Optional[str] = None
    stage: str = "agent"


# ------------------------------------------------------------------ 配置


@router.get("/config")
def read_config(db: DbDep):
    return ingest_service.get_config(db)


@router.put("/config")
def write_config(patch: ConfigPatch, db: DbDep):
    return ingest_service.set_config(db, patch.model_dump(exclude_none=True))


# ------------------------------------------------------------------ 待出清单


@router.get("/pending")
def pending(db: DbDep, limit: int = 50):
    """给外置 Agent：还没有清单 / 指纹变了的待整理文件 + 规则草稿。"""
    cfg = ingest_service.get_config(db)
    if not cfg.get("agent_enabled"):
        return {"enabled": False, "items": []}

    files = ingest_service.pending_files(db, limit=limit)
    items = []
    for rec in files:
        manifest = (
            db.query(IngestManifest)
            .filter(IngestManifest.file_path == rec.file_path)
            .first()
        )
        items.append(
            {
                "incoming_id": rec.id,
                "file_path": _norm_path(rec.file_path),
                "file_name": _norm_path(rec.file_name),
                "source_platform": rec.source_platform,
                "source_id": rec.source_id,
                "duration": rec.duration,
                "width": rec.width,
                "height": rec.height,
                "info": _clip_info(ingest_service.read_info_json(rec.file_path)),
                "draft": manifest.payload if manifest else None,
                "draft_confidence": manifest.confidence if manifest else None,
                "draft_status": manifest.status if manifest else None,
            }
        )
    return {"enabled": True, "items": items, "config": cfg}


# ------------------------------------------------------------------ 清单


@router.post("/manifests")
def write_manifest(body: ManifestWrite, db: DbDep):
    """Agent 回写清单：只更新 ingest_manifests，不碰业务表。"""
    if not ingest_service.get_config(db).get("agent_enabled"):
        raise HTTPException(403, "外置 Agent 已在设置中关闭")

    fp = body.fingerprint or ingest_service.fingerprint(body.file_path)
    row = (
        db.query(IngestManifest)
        .filter(IngestManifest.file_path == body.file_path)
        .first()
    )
    if row and row.status in ("accepted", "rejected"):
        raise HTTPException(409, "该文件已确认或已驳回，不再接受新清单")
    if not row:
        row = IngestManifest(file_path=body.file_path, generated_at=datetime.utcnow())
        db.add(row)
    row.fingerprint = fp
    row.payload = body.payload
    row.confidence = body.confidence
    row.source = body.source
    row.model = body.model
    row.status = body.status
    row.generated_at = datetime.utcnow()
    row.error = None
    db.commit()
    db.refresh(row)
    return {"id": row.id, "status": row.status}


@router.get("/manifest")
def manifest_by_path(db: DbDep, file_path: str):
    """详情页用：按文件路径取当前清单（没有则返回 null）。"""
    row = (
        db.query(IngestManifest)
        .filter(IngestManifest.file_path == file_path)
        .first()
    )
    if not row:
        # 路径可能带历史代理字符：按规范化后的路径再匹配一次
        want = _norm_path(file_path)
        row = next(
            (m for m in db.query(IngestManifest).all() if _norm_path(m.file_path) == want),
            None,
        )
    if not row:
        return None
    return {
        "id": row.id,
        "file_path": row.file_path,
        "status": row.status,
        "source": row.source,
        "confidence": row.confidence,
        "payload": _clean_payload(row.payload),
        "generated_at": row.generated_at,
    }


@router.post("/draft")
def rebuild_draft(db: DbDep, file_path: str):
    """手动重算一条规则草稿（不调模型）。"""
    rec = (
        db.query(IncomingFile)
        .filter(IncomingFile.file_path == file_path)
        .first()
    )
    if not rec:
        raise HTTPException(404, "待整理文件不存在")
    row = ingest_service.ensure_draft(db, rec, force=True)
    return {"id": row.id, "confidence": row.confidence, "payload": row.payload}


@router.get("/manifests")
def list_manifests(db: DbDep, status: Optional[str] = None, limit: int = 100):
    q = db.query(IngestManifest)
    if status:
        q = q.filter(IngestManifest.status == status)
    rows = q.order_by(IngestManifest.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "file_path": r.file_path,
            "status": r.status,
            "source": r.source,
            "confidence": r.confidence,
            "payload": r.payload,
            "generated_at": r.generated_at,
        }
        for r in rows
    ]


@router.post("/manifests/{manifest_id}/reject")
def reject_manifest(manifest_id: int, db: DbDep):
    row = db.get(IngestManifest, manifest_id)
    if not row:
        raise HTTPException(404, "清单不存在")
    row.status = "rejected"
    row.reviewed_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/manifests/{manifest_id}/accept")
def accept_manifest(manifest_id: int, db: DbDep):
    """标记已确认。真正的入库由前端拿 payload 调 /api/library/ingest 完成。"""
    row = db.get(IngestManifest, manifest_id)
    if not row:
        raise HTTPException(404, "清单不存在")
    row.status = "accepted"
    row.reviewed_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "payload": row.payload}


# ------------------------------------------------------------------ 生成请求队列


@router.post("/requests")
def create_request(body: RequestCreate, db: DbDep):
    if not ingest_service.get_config(db).get("agent_enabled"):
        raise HTTPException(403, "外置 Agent 已在设置中关闭")
    req = ingest_service.enqueue(db, scope=body.scope, target=body.target, stage=body.stage)
    return {"id": req.id, "status": req.status}


@router.get("/requests")
def list_requests(db: DbDep, status: Optional[str] = "queued", limit: int = 20):
    q = db.query(IngestRequest)
    if status:
        q = q.filter(IngestRequest.status == status)
    rows = q.order_by(IngestRequest.id.asc()).limit(limit).all()
    return [
        {"id": r.id, "scope": r.scope, "target": r.target, "stage": r.stage, "status": r.status}
        for r in rows
    ]


@router.post("/requests/{req_id}/claim")
def claim_request(req_id: int, db: DbDep):
    req = db.get(IngestRequest, req_id)
    if not req or req.status != "queued":
        raise HTTPException(409, "任务不可领取")
    req.status = "running"
    req.claimed_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.post("/requests/{req_id}/finish")
def finish_request(req_id: int, db: DbDep, ok: bool = True, error: Optional[str] = None):
    req = db.get(IngestRequest, req_id)
    if not req:
        raise HTTPException(404, "任务不存在")
    req.status = "done" if ok else "failed"
    req.finished_at = datetime.utcnow()
    req.error = (error or None) and error[:2000]
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 外部数据源


@router.post("/integrations/qqmusic/test")
def test_qqmusic(db: DbDep, keyword: str = "tripleS"):
    """测试 QQ 音乐数据源是否可用（不写任何业务数据）。"""
    from app.services import qqmusic_service

    try:
        items = qqmusic_service.brief(keyword, "song", limit=3)
    except qqmusic_service.QQMusicError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "keyword": keyword, "items": items}


_ALLOWED_PATCH_FIELDS = {
    "name", "video_type", "video_types", "performance_date", "published_date",
    "event_name", "session_label", "camera_angle", "is_solo", "chinese_description",
    "artists", "groups", "songs", "new_entities",
}


def _sanitize_patch_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """清单 patch 白名单校验（替代旧 agent.harness._sanitize_patch）。"""
    from app.services.llm_core import _canonical_video_type

    clean: dict[str, Any] = {}
    for k, v in (fields or {}).items():
        if k not in _ALLOWED_PATCH_FIELDS:
            continue
        if k == "video_type":
            v = _canonical_video_type(v)
            if not v:
                continue
        if k in ("artists", "groups", "songs", "new_entities"):
            if not isinstance(v, list):
                continue
            rows = []
            for x in v:
                if isinstance(x, dict) and x.get("id"):
                    rows.append({"id": int(x["id"]), "name": str(x.get("name") or "")})
                elif k == "new_entities" and isinstance(x, dict) and x.get("name"):
                    rows.append({"kind": x.get("kind") or "song", "name": str(x["name"])})
            v = rows
        clean[k] = v
    return clean


class ManifestPatch(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)


@router.post("/manifests/{manifest_id}/patch")
def patch_manifest(manifest_id: int, body: ManifestPatch, db: DbDep):
    """应用 Agent 的修改建议：只合并指定字段，不创建任何实体。"""

    row = db.get(IngestManifest, manifest_id)
    if not row:
        raise HTTPException(404, "清单不存在")
    clean = _sanitize_patch_fields(body.fields)
    if not clean:
        raise HTTPException(400, "没有可应用的字段")

    payload = dict(row.payload or {})
    payload.update(clean)
    row.payload = payload
    row.source = "agent"
    row.generated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "applied": sorted(clean.keys()), "payload": _clean_payload(payload)}

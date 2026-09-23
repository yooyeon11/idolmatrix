"""entity_images 服务层：头像/横幅候选历史（TMDB 式多图管理）。

设计要点：
- 单一事实源：主图 = 实体指针字段（avatar_path / banner_path），本表不存
  is_primary；主图判定 = 指针值与候选行 rel_path 比对。
- 新写入一律唯一文件名 {id}_{seq}.{ext}，不再清扫同 id 其它扩展名（保留历史，
  也从设计上消灭「迟到站点图覆盖手动上传文件」的竞态）。
- 重复内容（sha256 前 16 位相同）直接复用旧行并晋升，不重复落盘。
- 每实体每 kind 上限 20 张，超出自动清理最旧的非主图（主图永不自动清理）。
- 存量主图惰性补录（source="existing"）：首次查列表时指针有值无行则补一行。
- 除 flush 外不做 commit，由调用方统一提交。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.media.ffprobe import ProbeError, probe
from app.models.artist import Artist
from app.models.entity_image import EntityImage
from app.models.group import Group
from app.services.file_service import DERIVED_IMAGE_EXTENSIONS, confined_derived_file

logger = logging.getLogger(__name__)

KINDS = ("avatar", "banner")
SOURCES = ("upload", "site", "crop", "existing")
POINTER_FIELD = {"avatar": "avatar_path", "banner": "banner_path"}
MAX_IMAGES_PER_KIND = 20
HASH_PREFIX_LEN = 16
# 与站点下载上限一致
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class EntityImageError(ValueError):
    """候选图管理业务失败，携带建议的 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def entity_type_of(entity) -> str:
    if isinstance(entity, Artist):
        return "artist"
    if isinstance(entity, Group):
        return "group"
    raise EntityImageError("实体类型不支持", 400)


def resolve_entity(db: Session, entity_type: str, entity_id: int):
    """把 entity_type/entity_id 解析为未软删除实体；非法或不存在抛错。"""
    if entity_type == "artist":
        model = Artist
    elif entity_type == "group":
        model = Group
    else:
        raise EntityImageError("entity_type 仅支持 artist / group", 400)
    obj = model.get_active(db, entity_id)
    if obj is None:
        raise EntityImageError("实体不存在", 404)
    return obj


def _filters(entity_type: str, entity_id: int, kind: str):
    return (
        EntityImage.entity_type == entity_type,
        EntityImage.entity_id == entity_id,
        EntityImage.kind == kind,
    )


def ensure_rows(db: Session, entity, kind: str) -> None:
    """惰性补录存量主图：指针有值但候选表无对应行时补一行（source=existing）。"""
    pointer = getattr(entity, POINTER_FIELD[kind])
    if not pointer:
        return
    et = entity_type_of(entity)
    exists = db.scalar(
        select(EntityImage).where(
            *_filters(et, entity.id, kind), EntityImage.rel_path == pointer
        )
    )
    if exists is not None:
        return
    p = confined_derived_file(pointer)
    if p is None:
        # 指针文件已丢失：不入册，交由 list_images 的自愈逻辑修复指针
        return
    width, height = _probe_size(p)
    row = EntityImage(
        entity_type=et,
        entity_id=entity.id,
        kind=kind,
        rel_path=pointer,
        source="existing",
        width=width,
        height=height,
        content_hash=_hash_file(p),
    )
    db.add(row)
    db.flush()
    logger.info("已补录存量%s候选: %s", kind, pointer)


def allocate_rel(
    db: Session, entity_type: str, entity_id: int, kind: str, ext: str
) -> str:
    """生成不与现有行/文件冲突的唯一候选路径 {folder}/{kind_dir}/{id}_{seq}{ext}。"""
    if ext not in DERIVED_IMAGE_EXTENSIONS:
        raise EntityImageError(f"不支持的图片扩展名: {ext}", 400)
    seq_re = re.compile(rf"^{re.escape(str(entity_id))}_(\d+)\.")
    max_seq = 0
    for rel in db.scalars(
        select(EntityImage.rel_path).where(*_filters(entity_type, entity_id, kind))
    ):
        m = seq_re.match(Path(rel).name)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    folder = "avatars" if kind == "avatar" else "banners"
    base_dir = settings.derived_dir / folder / f"{entity_type}s"
    if base_dir.is_dir():
        for p in base_dir.iterdir():
            m = seq_re.match(p.name)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
    return (Path(folder) / f"{entity_type}s" / f"{entity_id}_{max_seq + 1}{ext}").as_posix()


def register_row(
    db: Session,
    entity,
    kind: str,
    rel_path: str,
    *,
    source: str,
    content_hash: Optional[str],
    width: Optional[int] = None,
    height: Optional[int] = None,
    set_primary: bool = True,
) -> dict:
    """登记候选行（flush 拿 id），可选晋升主图，并按上限修剪。调用方负责 commit。"""
    if source not in SOURCES:
        raise EntityImageError(f"未知来源: {source}", 400)
    row = EntityImage(
        entity_type=entity_type_of(entity),
        entity_id=entity.id,
        kind=kind,
        rel_path=rel_path,
        source=source,
        width=width,
        height=height,
        content_hash=content_hash,
    )
    db.add(row)
    db.flush()  # SessionLocal 为 autoflush=False，需显式 flush 拿 id
    if set_primary:
        setattr(entity, POINTER_FIELD[kind], rel_path)
    _prune_over_limit(db, entity, kind, protected_rels={rel_path})
    return _row_dict(row, set_primary or getattr(entity, POINTER_FIELD[kind]) == rel_path)


def append_bytes(
    db: Session,
    entity,
    kind: str,
    data: bytes,
    ext: str,
    *,
    source: str,
    set_primary: bool = True,
) -> dict:
    """把图片字节作为候选落盘并登记；命中重复内容则复用旧行（不重复落盘）。"""
    if not data:
        raise EntityImageError("图片数据为空", 400)
    if len(data) > MAX_IMAGE_BYTES:
        raise EntityImageError(f"图片体积过大，上限 {MAX_IMAGE_BYTES // (1024 * 1024)}MB", 400)
    content_hash = hashlib.sha256(data).hexdigest()[:HASH_PREFIX_LEN]
    reused = _reuse_duplicate(db, entity, kind, content_hash, set_primary=set_primary)
    if reused is not None:
        return reused
    rel = allocate_rel(db, entity_type_of(entity), entity.id, kind, ext)
    dest = settings.derived_dir / rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except OSError as e:
        raise EntityImageError(f"图片保存失败：{e}", 500) from e
    width, height = _probe_size(dest)
    return register_row(
        db,
        entity,
        kind,
        rel,
        source=source,
        content_hash=content_hash,
        width=width,
        height=height,
        set_primary=set_primary,
    )


def append_file(
    db: Session,
    entity,
    kind: str,
    rel_path: str,
    *,
    source: str = "crop",
    set_primary: bool = True,
) -> dict:
    """登记一个已落盘的候选文件（裁切流程用）；命中重复则删除新文件复用旧行。"""
    p = settings.derived_dir / rel_path
    if not p.is_file():
        raise EntityImageError("候选文件未生成", 500)
    content_hash = _hash_file(p)
    reused = _reuse_duplicate(db, entity, kind, content_hash, set_primary=set_primary)
    if reused is not None:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
        return reused
    width, height = _probe_size(p)
    return register_row(
        db,
        entity,
        kind,
        rel_path,
        source=source,
        content_hash=content_hash,
        width=width,
        height=height,
        set_primary=set_primary,
    )


def _reuse_duplicate(
    db: Session, entity, kind: str, content_hash: str, *, set_primary: bool
) -> Optional[dict]:
    """同实体同 kind 命中同内容旧行则复用；文件已丢失的旧行顺手清理。"""
    et = entity_type_of(entity)
    rows = db.scalars(
        select(EntityImage).where(
            *_filters(et, entity.id, kind),
            EntityImage.content_hash == content_hash,
        )
    ).all()
    alive: Optional[EntityImage] = None
    stale = False
    for row in rows:
        if confined_derived_file(row.rel_path) is None:
            db.delete(row)
            stale = True
            continue
        alive = row
        break
    if stale:
        db.flush()
    if alive is None:
        return None
    if set_primary:
        setattr(entity, POINTER_FIELD[kind], alive.rel_path)
    return _row_dict(alive, getattr(entity, POINTER_FIELD[kind]) == alive.rel_path)


def _prune_over_limit(
    db: Session, entity, kind: str, protected_rels: set
) -> None:
    """超出上限时清理最旧的候选；主图与本次写入行永不自动清理。"""
    et = entity_type_of(entity)
    rows = db.scalars(
        select(EntityImage)
        .where(*_filters(et, entity.id, kind))
        .order_by(EntityImage.created_at.asc(), EntityImage.id.asc())
    ).all()
    overflow = rows[:-MAX_IMAGES_PER_KIND] if len(rows) > MAX_IMAGES_PER_KIND else []
    pointer = getattr(entity, POINTER_FIELD[kind])
    removed = 0
    for row in overflow:
        if row.rel_path in protected_rels or row.rel_path == pointer:
            continue
        db.delete(row)
        p = confined_derived_file(row.rel_path)
        if p is not None:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        removed += 1
    if removed:
        db.flush()
        logger.info(
            "%s/%s %s 候选超上限，清理 %s 张最旧非主图", et, entity.id, kind, removed
        )


def list_images(db: Session, entity, kind: Optional[str] = None) -> List[dict]:
    """列出候选（kind 缺省返回两种），含自愈：清理失效行、修复悬空指针。新→旧。"""
    if kind is not None and kind not in KINDS:
        raise EntityImageError("kind 仅支持 avatar / banner", 400)
    et = entity_type_of(entity)
    items: List[dict] = []
    for k in (kind,) if kind else KINDS:
        ensure_rows(db, entity, k)
        rows = db.scalars(
            select(EntityImage)
            .where(*_filters(et, entity.id, k))
            .order_by(EntityImage.created_at.desc(), EntityImage.id.desc())
        ).all()
        pointer = getattr(entity, POINTER_FIELD[k])
        valid: List[EntityImage] = []
        pointer_alive = False
        for row in rows:
            if confined_derived_file(row.rel_path) is None:
                db.delete(row)
                continue
            if row.rel_path == pointer:
                pointer_alive = True
            valid.append(row)
        if len(valid) != len(rows):
            db.flush()  # autoflush=False：让删除立即生效
        if pointer and not pointer_alive:
            # 指针悬空：回退到最新剩余候选，否则清空
            nxt = valid[0] if valid else None
            setattr(entity, POINTER_FIELD[k], nxt.rel_path if nxt else None)
            logger.info(
                "%s/%s %s 指针失效，自愈为 %s",
                et,
                entity.id,
                k,
                nxt.rel_path if nxt else None,
            )
        current = getattr(entity, POINTER_FIELD[k])
        items.extend(_row_dict(r, r.rel_path == current) for r in valid)
    return items


def get_row(db: Session, entity_type: str, entity_id: int, image_id: int) -> EntityImage:
    """按实体范围取候选行；不存在抛 404。"""
    if entity_type not in ("artist", "group"):
        raise EntityImageError("entity_type 仅支持 artist / group", 400)
    row = db.scalar(
        select(EntityImage).where(
            EntityImage.entity_type == entity_type,
            EntityImage.entity_id == entity_id,
            EntityImage.id == image_id,
        )
    )
    if row is None:
        raise EntityImageError("候选图片不存在", 404)
    return row


def promote_row(db: Session, entity, row: EntityImage) -> dict:
    """把候选设为主图（仅改指针，不动文件）。"""
    kind = row.kind
    if confined_derived_file(row.rel_path) is None:
        db.delete(row)
        db.flush()
        raise EntityImageError("图片文件已丢失，已从候选中移除", 404)
    setattr(entity, POINTER_FIELD[kind], row.rel_path)
    return {"item": _row_dict(row, True), "kind": kind, "pointer": row.rel_path}


def delete_row(db: Session, entity, row: EntityImage) -> dict:
    """删除候选；若删的是主图则自动晋升最新剩余候选（无则清空指针）。"""
    kind = row.kind
    was_primary = getattr(entity, POINTER_FIELD[kind]) == row.rel_path
    removed_rel = row.rel_path
    db.delete(row)
    db.flush()  # autoflush=False：显式 flush 让后续查询看不到已删行
    p = confined_derived_file(removed_rel)
    if p is not None:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    new_pointer = getattr(entity, POINTER_FIELD[kind])
    if was_primary:
        nxt = db.scalar(
            select(EntityImage)
            .where(*_filters(entity_type_of(entity), entity.id, kind))
            .order_by(EntityImage.created_at.desc(), EntityImage.id.desc())
            .limit(1)
        )
        if nxt is not None and confined_derived_file(nxt.rel_path) is not None:
            setattr(entity, POINTER_FIELD[kind], nxt.rel_path)
            new_pointer = nxt.rel_path
        else:
            if nxt is not None:
                db.delete(nxt)
                db.flush()
            setattr(entity, POINTER_FIELD[kind], None)
            new_pointer = None
    return {"removed": row.id, "kind": kind, "pointer": new_pointer}


def _row_dict(row: EntityImage, is_primary: bool) -> dict:
    return {
        "id": row.id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "kind": row.kind,
        "rel_path": row.rel_path,
        "url": f"/api/uploads/images/{row.entity_type}/{row.entity_id}/{row.id}/file",
        "source": row.source,
        "width": row.width,
        "height": row.height,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "is_primary": bool(is_primary),
    }


def _hash_file(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:HASH_PREFIX_LEN]
    except OSError:
        return None


def _probe_size(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        info = probe(path)
        return info.width, info.height
    except ProbeError:
        return None, None

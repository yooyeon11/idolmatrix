"""通用头像/横幅上传与候选图管理接口（艺人/组合）。

语义（TMDB 式多历史图）：
- 上传 = 追加唯一文件名候选并自动设为主图（响应保留 avatar_path/banner_path
  兼容字段，另带 image_id）；
- 移除 = 仅取消使用（清指针），文件保留在候选历史中；
- 候选管理：列表 / 设主图（仅改指针）/ 删除候选（删主图自动晋升最新候选）。
"""

from __future__ import annotations

from typing import Optional, Union

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from app.core.deps import DbDep
from app.models.artist import Artist
from app.models.group import Group
from app.core.http_limits import read_upload_capped
from app.services import audiodb_service, entity_image_service
from app.services.derived_http import serve_derived_image
from app.services.entity_image_service import MAX_IMAGE_BYTES, EntityImageError
from app.services.file_service import confined_derived_file

router = APIRouter(prefix="/uploads", tags=["uploads"])

_EXT_BY_CONTENT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _resolve_entity(db, entity_type: str, entity_id: int) -> Union[Artist, Group]:
    if entity_type == "artist":
        model = Artist
    elif entity_type == "group":
        model = Group
    else:
        raise HTTPException(400, "entity_type 仅支持 artist / group")
    obj = model.get_active(db, entity_id)
    if not obj:
        raise HTTPException(404, "实体不存在")
    return obj


def _row_or_404(db, entity_type: str, entity_id: int, image_id: int):
    try:
        return entity_image_service.get_row(db, entity_type, entity_id, image_id)
    except EntityImageError as e:
        raise HTTPException(e.status_code, str(e)) from e


def _append_or_raise(db, entity, kind: str, data: bytes, ext: str) -> dict:
    try:
        return entity_image_service.append_bytes(db, entity, kind, data, ext, source="upload")
    except EntityImageError as e:
        raise HTTPException(e.status_code, str(e)) from e


@router.post("/avatar/{entity_type}/{entity_id}")
async def upload_avatar(
    entity_type: str,
    entity_id: int,
    db: DbDep,
    file: UploadFile = File(...),
):
    obj = _resolve_entity(db, entity_type, entity_id)
    data = await read_upload_capped(file, MAX_IMAGE_BYTES)
    if not data:
        raise HTTPException(400, "上传文件为空")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    ext = _EXT_BY_CONTENT.get(ctype)
    if not ext:
        raise HTTPException(400, f"仅支持 JPG/PNG/WebP/GIF 图片（当前 {ctype or '未知类型'}）")
    row = _append_or_raise(db, obj, "avatar", data, ext)
    db.commit()
    return {"avatar_path": row["rel_path"], "image_id": row["id"]}


@router.delete("/avatar/{entity_type}/{entity_id}")
def remove_avatar(entity_type: str, entity_id: int, db: DbDep):
    obj = _resolve_entity(db, entity_type, entity_id)
    # 先把当前主图入册候选历史，再仅清指针（文件保留，可随时重新启用）
    entity_image_service.ensure_rows(db, obj, "avatar")
    obj.avatar_path = None
    db.commit()
    return {"avatar_path": None}


@router.post("/banner/{entity_type}/{entity_id}")
async def upload_banner(
    entity_type: str,
    entity_id: int,
    db: DbDep,
    file: UploadFile = File(...),
):
    """上传横幅海报（数据库面板维护，供首页主舞台等展示位使用）。"""
    obj = _resolve_entity(db, entity_type, entity_id)
    data = await read_upload_capped(file, MAX_IMAGE_BYTES)
    if not data:
        raise HTTPException(400, "上传文件为空")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    ext = _EXT_BY_CONTENT.get(ctype)
    if not ext:
        raise HTTPException(400, f"仅支持 JPG/PNG/WebP/GIF 图片（当前 {ctype or '未知类型'}）")
    old_banner = obj.banner_path
    row = _append_or_raise(db, obj, "banner", data, ext)
    if row["rel_path"] != old_banner:
        # 横幅成为主舞台素材时焦点需重测；重复内容复用旧行则焦点仍有效
        obj.focus_x = None
        obj.focus_y = None
    db.commit()
    return {"banner_path": row["rel_path"], "image_id": row["id"]}


@router.delete("/banner/{entity_type}/{entity_id}")
def remove_banner(entity_type: str, entity_id: int, db: DbDep):
    obj = _resolve_entity(db, entity_type, entity_id)
    # 先把当前主图入册候选历史，再仅清指针（文件保留，可随时重新启用）
    entity_image_service.ensure_rows(db, obj, "banner")
    obj.banner_path = None
    db.commit()
    return {"banner_path": None}


@router.get("/images/{entity_type}/{entity_id}")
def list_entity_images(
    entity_type: str,
    entity_id: int,
    db: DbDep,
    kind: Optional[str] = Query(None, description="avatar / banner，缺省返回两种"),
):
    obj = _resolve_entity(db, entity_type, entity_id)
    try:
        items = entity_image_service.list_images(db, obj, kind)
    except EntityImageError as e:
        raise HTTPException(e.status_code, str(e)) from e
    db.commit()  # 补录/自愈可能产生落库变更
    return {"items": items}


@router.post("/images/{entity_type}/{entity_id}/{image_id}/promote")
def promote_entity_image(
    entity_type: str,
    entity_id: int,
    image_id: int,
    db: DbDep,
):
    """把候选设为当前使用（仅改指针，不动文件）。"""
    obj = _resolve_entity(db, entity_type, entity_id)
    row = _row_or_404(db, entity_type, entity_id, image_id)
    old_banner = obj.banner_path
    try:
        result = entity_image_service.promote_row(db, obj, row)
    except EntityImageError as e:
        raise HTTPException(e.status_code, str(e)) from e
    if result["kind"] == "banner" and result["pointer"] != old_banner:
        # 主舞台素材换成另一张图，焦点需重测；换回同一张则保留
        obj.focus_x = None
        obj.focus_y = None
    db.commit()
    return result


@router.delete("/images/{entity_type}/{entity_id}/{image_id}")
def delete_entity_image(
    entity_type: str,
    entity_id: int,
    image_id: int,
    db: DbDep,
):
    """删除候选；若删除的是主图则自动晋升最新剩余候选（无则清空指针）。"""
    obj = _resolve_entity(db, entity_type, entity_id)
    row = _row_or_404(db, entity_type, entity_id, image_id)
    old_banner = obj.banner_path
    try:
        result = entity_image_service.delete_row(db, obj, row)
    except EntityImageError as e:
        raise HTTPException(e.status_code, str(e)) from e
    if result["kind"] == "banner" and result["pointer"] != old_banner:
        obj.focus_x = None
        obj.focus_y = None
    db.commit()
    return result


@router.get("/images/{entity_type}/{entity_id}/{image_id}/file")
def get_entity_image_file(
    entity_type: str,
    entity_id: int,
    image_id: int,
    db: DbDep,
    request: Request,
):
    """返回候选图文件（URL 不可变，带 ETag 条件请求协商）。"""
    _resolve_entity(db, entity_type, entity_id)
    row = _row_or_404(db, entity_type, entity_id, image_id)
    p = confined_derived_file(row.rel_path)
    if p is None:
        raise HTTPException(404, "图片文件不存在")
    return serve_derived_image(request, p, audiodb_service.media_type_of(p))

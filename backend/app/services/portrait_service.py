"""从图库原图裁切艺人/组合头像与手机横幅，落到 derived。"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.ffmpeg import FFmpegNotFoundError
from app.media.ffmpeg import FFmpegError, crop_still
from app.media.ffprobe import ProbeError, probe
from app.models.artist import Artist
from app.models.group import Group
from app.models.photo import Photo
from app.services import entity_image_service, photo_service

logger = logging.getLogger(__name__)

PortraitKind = Literal["avatar", "banner"]

AVATAR_RATIO = 1.0
# 手机横幅 1.82:1（2026-09-21 起）：与首页刊头照片窗口、资料库媒体卡显示口径统一。
# 1920/1.82 = 1054.9 → 取偶数 1054（实际 1.8216，与 1.82 偏差 0.09%，肉眼不可见）。
BANNER_RATIO = 1.82
AVATAR_SIZE = 1024
BANNER_SIZE = (1920, 1054)
RATIO_TOLERANCE = 0.08


class PortraitError(ValueError):
    """裁切业务失败。"""


def _even(n: int) -> int:
    n = max(2, int(n))
    return n if n % 2 == 0 else n - 1


def normalize_crop(
    img_w: int,
    img_h: int,
    x: float,
    y: float,
    width: float,
    height: float,
    ratio: float,
) -> tuple[int, int, int, int]:
    """把 0-1 裁剪框落到像素，并钳到图内、对齐目标宽高比。"""
    if img_w < 2 or img_h < 2:
        raise PortraitError("图片尺寸过小，无法裁切")
    if width <= 0 or height <= 0:
        raise PortraitError("裁剪区域无效")
    px = x * img_w
    py = y * img_h
    pw = width * img_w
    ph = height * img_h
    if pw < 2 or ph < 2:
        raise PortraitError("裁剪区域过小")
    current = pw / ph
    if abs(current - ratio) > RATIO_TOLERANCE:
        # 以中心为基准改成目标比例
        cx = px + pw / 2
        cy = py + ph / 2
        if current > ratio:
            pw = ph * ratio
        else:
            ph = pw / ratio
        px = cx - pw / 2
        py = cy - ph / 2
    if px < 0:
        px = 0
    if py < 0:
        py = 0
    if px + pw > img_w:
        px = img_w - pw
    if py + ph > img_h:
        py = img_h - ph
    if px < 0 or py < 0:
        # 框比图还大：取图内最大目标比例矩形
        if img_w / img_h >= ratio:
            ph = float(img_h)
            pw = ph * ratio
            px = (img_w - pw) / 2
            py = 0.0
        else:
            pw = float(img_w)
            ph = pw / ratio
            px = 0.0
            py = (img_h - ph) / 2
    ix, iy = int(round(px)), int(round(py))
    iw, ih = _even(round(pw)), _even(round(ph))
    if ix + iw > img_w:
        ix = max(0, img_w - iw)
    if iy + ih > img_h:
        iy = max(0, img_h - ih)
    iw = min(_even(iw), _even(img_w - ix))
    ih = min(_even(ih), _even(img_h - iy))
    if iw < 2 or ih < 2:
        raise PortraitError("裁剪区域过小")
    return ix, iy, iw, ih


def _output_size(kind: PortraitKind, crop_w: int, crop_h: int) -> tuple[int, int]:
    if kind == "avatar":
        side = min(AVATAR_SIZE, max(crop_w, crop_h, 256))
        side = _even(side)
        return side, side
    out_w, out_h = BANNER_SIZE
    if crop_w < out_w:
        out_w = _even(max(crop_w, 2))
        out_h = _even(max(int(round(out_w / BANNER_RATIO)), 2))
    return out_w, out_h


def _write_derived(rel: Path, src: Path, x: int, y: int, w: int, h: int, ow: int, oh: int) -> None:
    dest = settings.derived_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        crop_still(src, dest, x=x, y=y, width=w, height=h, out_width=ow, out_height=oh)
    except FFmpegNotFoundError as e:
        raise PortraitError("ffmpeg 不可用，无法裁切") from e
    except FFmpegError as e:
        raise PortraitError(str(e)) from e
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise PortraitError("裁切结果未生成")


def _source_path(db: Session, photo: Photo) -> tuple[Path, Path | None]:
    """返回 (供 ffmpeg 读的路径, 若是临时文件则给出以便清理)。"""
    if photo_service.is_mtphotos_photo(photo):
        data, _ctype = photo_service.fetch_mtphotos_original(db, photo)
        tmp = Path(tempfile.mkdtemp(prefix="portrait_")) / (photo.file_name or "in.jpg")
        tmp.write_bytes(data)
        return tmp, tmp.parent
    return photo_service.resolve_photo_file(photo), None


def apply_photo_crop(
    db: Session,
    photo_id: int,
    *,
    kind: PortraitKind,
    x: float,
    y: float,
    width: float,
    height: float,
) -> dict:
    if kind not in ("avatar", "banner"):
        raise PortraitError("kind 仅支持 avatar / banner")
    photo = db.scalar(
        select(Photo)
        .options(selectinload(Photo.source))
        .where(Photo.id == photo_id, Photo.active_filter())
    )
    if photo is None:
        raise PortraitError("照片不存在")
    if photo.media_kind != "image":
        raise PortraitError("只能用图片裁切头像或横幅")
    source = photo.source
    if source is None:
        raise PortraitError("照片来源不存在")
    owner_type = source.owner_type
    owner_id = source.owner_id
    if owner_type == "artist":
        entity = Artist.get_active(db, owner_id)
    elif owner_type == "group":
        entity = Group.get_active(db, owner_id)
    else:
        raise PortraitError("照片未绑定艺人或组合")
    if entity is None:
        raise PortraitError("艺人或组合不存在")

    tmp_dir: Path | None = None
    try:
        src, tmp_dir = _source_path(db, photo)
        try:
            info = probe(src)
        except ProbeError as e:
            raise PortraitError(f"无法读取图片尺寸：{e}") from e
        img_w, img_h = info.width or 0, info.height or 0
        ratio = AVATAR_RATIO if kind == "avatar" else BANNER_RATIO
        cx, cy, cw, ch = normalize_crop(img_w, img_h, x, y, width, height, ratio)
        ow, oh = _output_size(kind, cw, ch)
        folder = "avatars" if kind == "avatar" else "banners"
        kind_dir = "artists" if owner_type == "artist" else "groups"
        # 唯一候选文件名（不再覆盖同 id 旧文件，历史全部保留）
        try:
            rel_s = entity_image_service.allocate_rel(db, owner_type, owner_id, kind, ".jpg")
        except entity_image_service.EntityImageError as e:
            raise PortraitError(str(e)) from e
        _write_derived(Path(rel_s), src, cx, cy, cw, ch, ow, oh)
    except photo_service.PhotoServiceError as e:
        raise PortraitError(str(e)) from e
    finally:
        if tmp_dir is not None:
            try:
                for p in tmp_dir.iterdir():
                    p.unlink(missing_ok=True)
                tmp_dir.rmdir()
            except OSError:
                pass

    try:
        row = entity_image_service.append_file(db, entity, kind, rel_s, source="crop")
    except entity_image_service.EntityImageError as e:
        raise PortraitError(str(e)) from e
    db.commit()
    db.refresh(entity)
    logger.info(
        "已从照片 #%s 裁切 %s -> %s/%s",
        photo_id,
        kind,
        owner_type,
        owner_id,
    )
    return {
        "kind": kind,
        "path": row["rel_path"],
        "owner_type": owner_type,
        "owner_id": owner_id,
    }


def remove_banner(db: Session, owner_type: str, owner_id: int) -> None:
    if owner_type == "artist":
        entity = Artist.get_active(db, owner_id)
    elif owner_type == "group":
        entity = Group.get_active(db, owner_id)
    else:
        raise PortraitError("仅支持 artist / group")
    if entity is None:
        raise PortraitError("艺人或组合不存在")
    # 仅取消使用：先把当前主图入册候选历史，再清指针（文件保留）
    entity_image_service.ensure_rows(db, entity, "banner")
    entity.banner_path = None
    db.commit()

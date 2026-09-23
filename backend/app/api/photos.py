"""图片文件夹绑定、扫描与图库信息流。"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.ffmpeg import FFmpegNotFoundError
from app.media.ffmpeg import FFmpegError

from app.core.deps import DbDep
from app.core.http_limits import read_upload_capped
from app.models.photo import Photo, PhotoSource
from app.schemas.photo import (
    MtPhotosAlbumRead,
    MtPhotosFolderRead,
    MtPhotosPingRequest,
    MtPhotosPingResult,
    PhotoAnalysisEditResult,
    PhotoAnalysisUpdate,
    PhotoBrowseResult,
    PhotoCollectRequest,
    PhotoCollectionCreate,
    PhotoCollectionRead,
    PhotoCollectionUpdate,
    PhotoDeleteResult,
    PhotoFeedItem,
    PhotoFeedResponse,
    PhotoFilterOptions,
    PhotoMembershipsResponse,
    PhotoRead,
    PhotoScanResult,
    PhotoSourceCreate,
    PhotoSourceRead,
    PhotoPortraitCrop,
    PhotoPortraitResult,
    PhotoUploadResult,
)
from app.services import mtphotos, photo_analysis, photo_collection, photo_service, photo_thumb, portrait_service

router = APIRouter(prefix="/photos", tags=["photos"])


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    seen: list[str] = []
    for item in raw.split(","):
        text = item.strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _http(err: Exception) -> HTTPException:
    return HTTPException(400, str(err))


@router.get("/sources", response_model=list[PhotoSourceRead])
def list_photo_sources(
    db: DbDep,
    owner_type: str = Query(..., description="artist / group"),
    owner_id: int = Query(..., ge=1),
):
    try:
        rows = photo_service.list_sources(db, owner_type, owner_id)
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    labels = photo_service.source_labels(db, [s for s, _n in rows])
    return [
        PhotoSourceRead(**photo_service.source_to_read(s, n, label=labels.get(s.id)))
        for s, n in rows
    ]


@router.post("/sources", response_model=PhotoSourceRead, status_code=201)
def create_photo_source(payload: PhotoSourceCreate, db: DbDep):
    try:
        source = photo_service.bind_source(
            db,
            owner_type=payload.owner_type,
            owner_id=payload.owner_id,
            section=payload.section,
            folder_path=payload.folder_path,
            recursive=payload.recursive,
            provider=payload.provider,
            external_id=payload.external_id,
            mt_kind=payload.mt_kind,
        )
    except (photo_service.PhotoServiceError, mtphotos.MtPhotosError) as e:
        msg = str(e)
        code = 409 if "已绑定" in msg else 400
        raise HTTPException(code, msg) from e
    labels = photo_service.source_labels(db, [source])
    return PhotoSourceRead(
        **photo_service.source_to_read(source, 0, label=labels.get(source.id))
    )


@router.get("/mtphotos/albums", response_model=list[MtPhotosAlbumRead])
def list_mtphotos_albums(db: DbDep):
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
        albums = mtphotos.list_albums(base_url, api_key)
    except mtphotos.MtPhotosError as e:
        raise _http(e) from e
    return [MtPhotosAlbumRead(id=a.id, name=a.name, count=a.count) for a in albums]


@router.get("/mtphotos/folders", response_model=list[MtPhotosFolderRead])
def list_mtphotos_folders(
    db: DbDep,
    parent_id: int | None = Query(None, ge=1),
):
    try:
        base_url, api_key = mtphotos.configured_from_db(db)
        folders = mtphotos.list_folders(base_url, api_key, parent_id)
    except mtphotos.MtPhotosError as e:
        raise _http(e) from e
    return [
        MtPhotosFolderRead(
            id=f.id,
            name=f.name,
            path=f.path,
            count=f.count,
            subfolder_count=f.subfolder_count,
        )
        for f in folders
    ]


@router.get("/browse", response_model=PhotoBrowseResult)
def browse_photo_folders(
    path: str | None = Query(None, max_length=1000, description="要列出的目录；为空返回根层"),
):
    """绑定本地文件夹时的目录浏览器（白名单根路径内，见 PHOTO_BROWSE_ROOTS）。"""
    try:
        result = photo_service.browse_folder(path)
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return PhotoBrowseResult(**result)


@router.post("/mtphotos/ping", response_model=MtPhotosPingResult)
def ping_mtphotos(payload: MtPhotosPingRequest, db: DbDep):
    base_url = (payload.base_url or "").strip()
    api_key = (payload.api_key or "").strip()
    if not base_url or not api_key:
        try:
            base_url, api_key = mtphotos.configured_from_db(db)
        except mtphotos.MtPhotosError as e:
            raise _http(e) from e
    try:
        return MtPhotosPingResult(**mtphotos.ping(base_url, api_key))
    except mtphotos.MtPhotosError as e:
        raise _http(e) from e


@router.delete("/sources/{source_id}", status_code=204)
def delete_photo_source(source_id: int, db: DbDep):
    source = db.get(PhotoSource, source_id)
    if not source:
        raise HTTPException(404, "图片文件夹不存在")
    photo_service.delete_source(db, source)


@router.post("/sources/{source_id}/scan", response_model=PhotoScanResult)
def scan_photo_source(source_id: int, db: DbDep):
    source = db.get(PhotoSource, source_id)
    if not source:
        raise HTTPException(404, "图片文件夹不存在")
    try:
        stats = photo_service.scan_source(db, source)
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return PhotoScanResult(
        source_id=stats.source_id,
        files_seen=stats.files_seen,
        created=stats.created,
        updated=stats.updated,
        restored=stats.restored,
        removed=stats.removed,
        posts=stats.posts,
        elapsed_ms=stats.elapsed_ms,
        thumbs_queued=stats.thumbs_queued,
        warning=stats.warning,
    )


@router.post("/upload", response_model=PhotoUploadResult)
async def upload_wall_photos(
    db: DbDep,
    owner_type: str = Query(..., description="artist / group"),
    owner_id: int = Query(..., ge=1),
    files: list[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(400, "请选择要上传的文件")
    if len(files) > photo_service.MAX_UPLOAD_FILES:
        raise HTTPException(400, f"一次最多上传 {photo_service.MAX_UPLOAD_FILES} 个文件")
    try:
        source = photo_service.ensure_upload_source(db, owner_type, owner_id)
        created: list[Photo] = []
        for item in files:
            data = await read_upload_capped(item, photo_service.MAX_UPLOAD_BYTES)
            photo = photo_service.add_uploaded_photo(
                db, source, original_name=item.filename or "upload", data=data
            )
            created.append(photo)
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return PhotoUploadResult(
        created=len(created),
        photos=[PhotoRead.model_validate(p) for p in created],
    )


@router.get("/feed", response_model=PhotoFeedResponse)
def photo_feed(
    db: DbDep,
    owner_type: str = Query(..., description="artist / group"),
    owner_id: int = Query(..., ge=1),
    section: str = Query(..., description="official / fan / wall"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    scene: str | None = Query(None),
    shot: str | None = Query(None),
    shoes_type: str | None = Query(None),
    hosiery_present: str | None = Query(None),
    analyzed: bool | None = Query(None),
    tags: str | None = Query(None, description="逗号分隔的自由标签"),
    on_date: str | None = Query(None, description="拍摄日期 YYYY-MM-DD，空字符串表示无日期"),
    q: str | None = Query(None, max_length=100, description="模糊搜索：博文 / 发布者 / 文件名"),
    author: str | None = Query(None, max_length=120, description="发布者精确筛选"),
    sort_by: str = Query("date", description="date / name"),
    sort_dir: str = Query("desc", description="desc / asc"),
):
    try:
        data = photo_service.owner_feed(
            db,
            owner_type=owner_type,
            owner_id=owner_id,
            section=section,
            page=page,
            page_size=page_size,
            scene=scene or None,
            shot=shot or None,
            shoes_type=shoes_type or None,
            hosiery_present=hosiery_present or None,
            analyzed=analyzed,
            tags=_parse_tags(tags),
            on_date=None if on_date is None else on_date,
            q=q,
            author=author,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return _feed_response(data)


@router.get("/feed/filter-options", response_model=PhotoFilterOptions)
def photo_feed_filter_options(
    db: DbDep,
    owner_type: str = Query(..., description="artist / group"),
    owner_id: int = Query(..., ge=1),
    section: str = Query(..., description="official / fan / wall"),
):
    try:
        result = photo_service.owner_filter_options(
            db, owner_type=owner_type, owner_id=owner_id, section=section
        )
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return PhotoFilterOptions(**result)


def _feed_response(data: dict) -> PhotoFeedResponse:
    items = [
        PhotoFeedItem(
            kind=it["kind"],
            post_key=it["post_key"],
            caption=it["caption"],
            author=it.get("author"),
            published_at=it["published_at"],
            photos=[PhotoRead.model_validate(p) for p in it["photos"]],
        )
        for it in data["items"]
    ]
    return PhotoFeedResponse(
        items=items,
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        source_count=data["source_count"],
        photo_count=data["photo_count"],
        last_scanned_at=data["last_scanned_at"],
        readonly=bool(data.get("readonly")),
        timeline=data.get("timeline") or [],
    )


@router.get("/collections", response_model=list[PhotoCollectionRead])
def list_photo_collections(db: DbDep):
    cols = photo_collection.list_collections(db)
    return [PhotoCollectionRead(**photo_collection.collection_to_read(db, c)) for c in cols]


@router.post("/collections", response_model=PhotoCollectionRead, status_code=201)
def create_photo_collection(payload: PhotoCollectionCreate, db: DbDep):
    try:
        col = photo_collection.create_collection(
            db, name=payload.name, description=payload.description
        )
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return PhotoCollectionRead(**photo_collection.collection_to_read(db, col))


@router.get("/collections/memberships", response_model=PhotoMembershipsResponse)
def photo_collection_memberships(
    db: DbDep,
    photo_ids: str = Query("", description="逗号分隔的照片 id"),
):
    ids: list[int] = []
    for part in (photo_ids or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return PhotoMembershipsResponse(memberships=photo_collection.memberships_for_photos(db, ids))


@router.get("/collections/by-uid/{uid}", response_model=PhotoCollectionRead)
def get_photo_collection_by_uid(uid: str, db: DbDep):
    try:
        col = photo_collection.get_collection_by_uid(db, uid)
    except photo_service.PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    return PhotoCollectionRead(**photo_collection.collection_to_read(db, col))


@router.get("/collections/{collection_id}", response_model=PhotoCollectionRead)
def get_photo_collection(collection_id: int, db: DbDep):
    try:
        col = photo_collection.get_collection(db, collection_id)
    except photo_service.PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    return PhotoCollectionRead(**photo_collection.collection_to_read(db, col))


@router.patch("/collections/{collection_id}", response_model=PhotoCollectionRead)
def update_photo_collection(collection_id: int, payload: PhotoCollectionUpdate, db: DbDep):
    try:
        col = photo_collection.get_collection(db, collection_id)
        col = photo_collection.update_collection(
            db, col, name=payload.name, description=payload.description
        )
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return PhotoCollectionRead(**photo_collection.collection_to_read(db, col))


@router.delete("/collections/{collection_id}", status_code=204)
def delete_photo_collection(collection_id: int, db: DbDep):
    try:
        col = photo_collection.get_collection(db, collection_id)
    except photo_service.PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    photo_collection.delete_collection(db, col)


@router.get("/collections/{collection_id}/photos", response_model=PhotoFeedResponse)
def list_collection_photos(
    collection_id: int,
    db: DbDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    scene: str | None = Query(None),
    shot: str | None = Query(None),
    shoes_type: str | None = Query(None),
    hosiery_present: str | None = Query(None),
    analyzed: bool | None = Query(None),
    tags: str | None = Query(None, description="逗号分隔的自由标签"),
    q: str | None = Query(None, max_length=100, description="模糊搜索：博文 / 发布者 / 文件名"),
    author: str | None = Query(None, max_length=120, description="发布者精确筛选"),
    sort_by: str = Query("date", description="date / name"),
    sort_dir: str = Query("desc", description="desc / asc"),
):
    try:
        col = photo_collection.get_collection(db, collection_id)
        data = photo_collection.collection_photos(
            db,
            col,
            page=page,
            page_size=page_size,
            scene=scene or None,
            shot=shot or None,
            shoes_type=shoes_type or None,
            hosiery_present=hosiery_present or None,
            analyzed=analyzed,
            tags=_parse_tags(tags),
            q=q,
            author=author,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    except photo_service.PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    return _feed_response(data)


@router.get(
    "/collections/{collection_id}/photos/filter-options",
    response_model=PhotoFilterOptions,
)
def list_collection_filter_options(collection_id: int, db: DbDep):
    try:
        col = photo_collection.get_collection(db, collection_id)
        result = photo_collection.collection_filter_options(db, col)
    except photo_service.PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    return PhotoFilterOptions(**result)


@router.post("/collections/{collection_id}/photos", response_model=PhotoCollectionRead)
def add_photo_to_collection(collection_id: int, payload: PhotoCollectRequest, db: DbDep):
    try:
        col = photo_collection.get_collection(db, collection_id)
        photo = photo_collection.resolve_photo(
            db, photo_id=payload.photo_id, photo_uid=payload.photo_uid
        )
        photo_collection.add_photo(db, col, photo)
        db.refresh(col)
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return PhotoCollectionRead(**photo_collection.collection_to_read(db, col))


@router.delete("/collections/{collection_id}/photos/{photo_id}", response_model=PhotoCollectionRead)
def remove_photo_from_collection(collection_id: int, photo_id: int, db: DbDep):
    try:
        col = photo_collection.get_collection(db, collection_id)
        photo = photo_collection.resolve_photo(db, photo_id=photo_id)
        photo_collection.remove_photo(db, col, photo)
        db.refresh(col)
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e
    return PhotoCollectionRead(**photo_collection.collection_to_read(db, col))


@router.delete("/{photo_id}/post", response_model=PhotoDeleteResult)
def delete_photo_post(photo_id: int, db: DbDep):
    photo = db.scalar(
        select(Photo)
        .options(selectinload(Photo.source))
        .where(Photo.id == photo_id, Photo.active_filter())
    )
    if not photo:
        raise HTTPException(404, "照片不存在")
    try:
        return PhotoDeleteResult(**photo_service.delete_post(db, photo))
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e


@router.delete("/{photo_id}", response_model=PhotoDeleteResult)
def delete_photo(photo_id: int, db: DbDep):
    photo = db.scalar(
        select(Photo)
        .options(selectinload(Photo.source))
        .where(Photo.id == photo_id, Photo.active_filter())
    )
    if not photo:
        raise HTTPException(404, "照片不存在")
    try:
        return PhotoDeleteResult(**photo_service.delete_photo(db, photo))
    except photo_service.PhotoServiceError as e:
        raise _http(e) from e


@router.patch("/{photo_id}/analysis", response_model=PhotoAnalysisEditResult)
def update_photo_analysis(photo_id: int, payload: PhotoAnalysisUpdate, db: DbDep):
    photo = db.scalar(
        select(Photo)
        .options(selectinload(Photo.source))
        .where(Photo.id == photo_id, Photo.active_filter())
    )
    if not photo:
        raise HTTPException(404, "照片不存在")
    try:
        analysis = photo_analysis.update_analysis_text(
            photo, caption_zh=payload.caption_zh, tags=payload.tags
        )
        db.refresh(photo)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return PhotoAnalysisEditResult(photo=PhotoRead.model_validate(photo), analysis=analysis)


@router.post("/{photo_id}/portrait", response_model=PhotoPortraitResult)
def crop_photo_portrait(photo_id: int, payload: PhotoPortraitCrop, db: DbDep):
    """从图库原图裁切并保存为该照片所属艺人/组合的头像或手机横幅。"""
    try:
        return portrait_service.apply_photo_crop(
            db,
            photo_id,
            kind=payload.kind,
            x=payload.x,
            y=payload.y,
            width=payload.width,
            height=payload.height,
        )
    except portrait_service.PortraitError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/{photo_id}/file")
def get_photo_file(photo_id: int, db: DbDep):
    photo = db.scalar(
        select(Photo)
        .options(selectinload(Photo.source))
        .where(Photo.id == photo_id, Photo.active_filter())
    )
    if not photo:
        raise HTTPException(404, "照片不存在")
    if photo_service.is_mtphotos_photo(photo):
        try:
            data, ctype = photo_service.fetch_mtphotos_original(db, photo)
        except photo_service.PhotoServiceError as e:
            raise HTTPException(404, str(e)) from e
        return Response(
            content=data,
            media_type=ctype,
            headers={"Cache-Control": "private, max-age=3600"},
        )
    try:
        path = photo_service.resolve_photo_file(photo)
    except photo_service.PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(
        path,
        media_type=photo_service.media_type_of(path),
        filename=photo.file_name,
        content_disposition_type="inline",
    )


@router.get("/{photo_id}/thumb")
def get_photo_thumb(photo_id: int, db: DbDep):
    photo = db.scalar(
        select(Photo)
        .options(selectinload(Photo.source))
        .where(Photo.id == photo_id, Photo.active_filter())
    )
    if not photo:
        raise HTTPException(404, "照片不存在")
    try:
        path = photo_thumb.resolve_or_build_thumb(photo)
    except photo_service.PhotoServiceError as e:
        raise HTTPException(404, str(e)) from e
    except (FFmpegError, FFmpegNotFoundError) as e:
        raise HTTPException(404, "暂无缩略图") from e
    if not path.is_file():
        raise HTTPException(404, "暂无缩略图")
    return FileResponse(
        path,
        media_type="image/jpeg",
        content_disposition_type="inline",
        headers={"Cache-Control": "public, max-age=86400"},
    )

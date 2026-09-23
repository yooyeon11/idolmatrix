"""图片来源 / 照片索引 / 图库信息流 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.common import EntityReadMixin, ORMModel

PhotoOwnerType = Literal["artist", "group"]
PhotoSection = Literal["official", "fan", "wall"]
PhotoMediaKind = Literal["image", "video"]
PhotoFeedKind = Literal["post", "photo"]
PhotoProvider = Literal["folder", "mtphotos"]


class PhotoSourceCreate(BaseModel):
    owner_type: PhotoOwnerType
    owner_id: int
    section: PhotoSection
    folder_path: str = Field("", max_length=1000)
    recursive: bool = False
    provider: PhotoProvider = "folder"
    external_id: Optional[str] = Field(None, max_length=80)
    mt_kind: Optional[Literal["album", "folder"]] = None


class PhotoBrowseEntry(BaseModel):
    name: str
    path: str
    photo_count: int = 0
    subfolder_count: int = 0


class PhotoBrowseResult(BaseModel):
    path: str
    parent: Optional[str] = None
    entries: List[PhotoBrowseEntry] = []


class PhotoSourceRead(ORMModel):
    id: int
    owner_type: str
    owner_id: int
    section: str
    folder_path: str
    provider: str = "folder"
    external_id: Optional[str] = None
    recursive: bool
    last_scanned_at: Optional[datetime] = None
    last_scan_files: int = 0
    last_scan_posts: int = 0
    photo_count: int = 0
    label: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PhotoScanResult(BaseModel):
    source_id: int
    files_seen: int
    created: int
    updated: int
    restored: int
    removed: int
    posts: int
    elapsed_ms: int
    thumbs_queued: int = 0
    warning: str = ""


class PhotoRead(EntityReadMixin):
    id: int
    source_id: int
    section: str
    file_name: str
    media_kind: str
    published_at: Optional[datetime] = None
    caption: Optional[str] = None
    author: Optional[str] = None
    post_key: Optional[str] = None
    position_in_post: int = 0
    analysis: Optional[Dict[str, Any]] = None
    analysis_prompt_version: Optional[str] = None
    analyzed_at: Optional[datetime] = None
    provider: str = "folder"


class PhotoFeedItem(BaseModel):
    kind: PhotoFeedKind
    post_key: Optional[str] = None
    caption: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    photos: List[PhotoRead]


class PhotoTimelineDay(BaseModel):
    date: str
    count: int


class PhotoFeedResponse(BaseModel):
    items: List[PhotoFeedItem]
    total: int
    page: int
    page_size: int
    source_count: int
    photo_count: int
    last_scanned_at: Optional[datetime] = None
    readonly: bool = False
    timeline: List[PhotoTimelineDay] = []


class PhotoCollectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)


class PhotoCollectionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)


class PhotoCollectionRead(EntityReadMixin):
    id: int
    name: str
    description: Optional[str] = None
    photo_count: int = 0
    cover_photo_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PhotoCollectRequest(BaseModel):
    photo_id: Optional[int] = None
    photo_uid: Optional[str] = None


class PhotoMembershipsResponse(BaseModel):
    memberships: dict[int, list[int]]


class PhotoAnalysisUpdate(BaseModel):
    caption_zh: str = ""
    tags: List[str] = []


class PhotoAnalysisEditResult(BaseModel):
    photo: PhotoRead
    analysis: Dict[str, Any]


class PhotoTagOption(BaseModel):
    tag: str
    count: int


class PhotoAuthorOption(BaseModel):
    author: str
    count: int


class PhotoFilterOptions(BaseModel):
    options: List[PhotoTagOption]
    authors: List[PhotoAuthorOption] = []


class PhotoUploadResult(BaseModel):
    created: int
    photos: List[PhotoRead]


class PhotoDeleteResult(BaseModel):
    deleted: int
    files_deleted: int


class MtPhotosPingRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""


class MtPhotosPingResult(BaseModel):
    version: str = ""
    build: str = ""
    album_count: int = 0


class MtPhotosAlbumRead(BaseModel):
    id: int
    name: str
    count: int = 0


class MtPhotosFolderRead(BaseModel):
    id: int
    name: str
    path: str = ""
    count: int = 0
    subfolder_count: int = 0


class PhotoPortraitCrop(ORMModel):
    """从图库原图裁切头像或横幅。坐标为相对原图的 0-1。"""

    kind: Literal["avatar", "banner"]
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class PhotoPortraitResult(ORMModel):
    kind: str
    path: str
    owner_type: str
    owner_id: int

"""待整理目录扫描结果模型。

- IncomingFile：incoming 目录的扫描快照，由后台扫描落库，
  列表/统计接口后续改读本表，不再每次实时扫盘 + 全量 ffprobe。

注：本表是文件系统状态的投影（派生缓存），不是核心业务实体：
不使用 BaseEntityMixin（无 uid / 软删除），文件消失即删行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class IncomingFile(TimestampMixin, Base):
    """incoming 目录中一个视频文件的扫描快照。"""

    __tablename__ = "incoming_files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 文件绝对路径（与 ScannedFileItem.path 同口径），作为唯一键
    file_path: Mapped[str] = mapped_column(String(1000), unique=True, index=True)
    file_name: Mapped[str] = mapped_column(String(500), index=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    # 变更检测：mtime 与 file_size 均未变时直接复用下方 ffprobe 缓存字段
    mtime: Mapped[Optional[float]] = mapped_column(Float)
    duration: Mapped[Optional[int]] = mapped_column(Integer)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    video_codec: Mapped[Optional[str]] = mapped_column(String(50))
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50))
    # 扫描时按文件名+大小对 MusicVideo 的重复提示（入库后可能过期，读侧可复核）
    is_duplicate: Mapped[bool] = mapped_column(default=False)
    # 探测成功时间；NULL=探测失败/未探测，下轮扫描重试
    probed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

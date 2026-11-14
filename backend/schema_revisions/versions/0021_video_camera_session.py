"""music_videos：新增机位 camera_angle 与场次 session_label。

- `camera_angle`：机位规范值（词表见 `app/core/vocab.py`），NULL = 未标注。
- `session_label`：场次标签 `YYMMDD - 活动名`（业主 2026-10-17 口径）。
  场次**不参与落位目录**（决策 3），只作为展示 / 聚合 / 筛选维度。

两个字段都允许 NULL：存量 3412 条里有约 30% 的 name 尾标不含机位词
（周播节目默认机位），回填时留空比编造更诚实。

Revision ID: 0021_video_camera_session
Revises: 0020_featured_in_home
Create Date: 2026-10-17
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0021_video_camera_session"
down_revision: Union[str, Sequence[str], None] = "0020_featured_in_home"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "music_videos"
_COLUMNS = (
    ("camera_angle", "VARCHAR(30)"),
    ("session_label", "VARCHAR(200)"),
)
_INDEXES = (
    ("ix_music_videos_camera_angle", "camera_angle"),
    ("ix_music_videos_session_label", "session_label"),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if _TABLE not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns(_TABLE)}
    for name, ddl in _COLUMNS:
        if name not in cols:
            bind.execute(text(f"ALTER TABLE {_TABLE} ADD COLUMN {name} {ddl}"))
    # 索引：机位用于筛选、场次用于聚合/筛选
    existing_idx = {i["name"] for i in insp.get_indexes(_TABLE)}
    for idx_name, column in _INDEXES:
        if idx_name not in existing_idx:
            bind.execute(
                text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {_TABLE} ({column})")
            )


def downgrade() -> None:
    # SQLite 删列不安全（且这两个列保留无害），回滚只删索引
    bind = op.get_bind()
    insp = inspect(bind)
    if _TABLE not in set(insp.get_table_names()):
        return
    for idx_name, _column in _INDEXES:
        bind.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))


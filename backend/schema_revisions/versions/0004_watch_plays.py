"""观看记录表。

Revision ID: 0004_watch_plays
Revises: 0003_app_settings
Create Date: 2026-08-24
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0004_watch_plays"
down_revision: Union[str, Sequence[str], None] = "0003_app_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "watch_plays" in insp.get_table_names():
        return
    bind.execute(
        text(
            "CREATE TABLE watch_plays ("
            "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
            "music_video_id INTEGER, "
            "started_at DATETIME NOT NULL, "
            "last_seen_at DATETIME NOT NULL, "
            "last_position FLOAT NOT NULL DEFAULT 0, "
            "max_position FLOAT NOT NULL DEFAULT 0, "
            "seconds_watched FLOAT NOT NULL DEFAULT 0, "
            "source_duration FLOAT, "
            "completed BOOLEAN NOT NULL DEFAULT 0, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "FOREIGN KEY(music_video_id) REFERENCES music_videos (id) ON DELETE SET NULL"
            ")"
        )
    )
    bind.execute(
        text(
            "CREATE INDEX ix_watch_plays_video_seen "
            "ON watch_plays (music_video_id, last_seen_at)"
        )
    )
    bind.execute(text("CREATE INDEX ix_watch_plays_started ON watch_plays (started_at)"))
    bind.execute(
        text("CREATE INDEX ix_watch_plays_music_video_id ON watch_plays (music_video_id)")
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "watch_plays" in insp.get_table_names():
        bind.execute(text("DROP TABLE watch_plays"))

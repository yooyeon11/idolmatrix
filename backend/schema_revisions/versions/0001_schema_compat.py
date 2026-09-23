"""把原先启动时的 ALTER / 索引补丁收进 Alembic（幂等）。

Revision ID: 0001_schema_compat
Revises:
Create Date: 2026-08-20
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

revision: str = "0001_schema_compat"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    if column in existing:
        return
    bind.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def upgrade() -> None:
    _add_column_if_missing("artists", "avatar_path", "avatar_path VARCHAR(500)")
    _add_column_if_missing("groups", "avatar_path", "avatar_path VARCHAR(500)")
    _add_column_if_missing("music_videos", "is_solo", "is_solo BOOLEAN")
    _add_column_if_missing(
        "music_videos", "is_non_performance", "is_non_performance BOOLEAN"
    )
    _add_column_if_missing("albums", "cover_path", "cover_path VARCHAR(500)")
    _add_column_if_missing(
        "playback_sessions", "requested_quality", "requested_quality VARCHAR(20)"
    )
    _add_column_if_missing(
        "playback_sessions", "decision_reason", "decision_reason TEXT"
    )
    stmts = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_music_videos_file_hash_active "
        "ON music_videos(file_hash) WHERE deleted_at IS NULL AND file_hash IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_music_videos_source_active "
        "ON music_videos(source_platform, source_id) "
        "WHERE deleted_at IS NULL AND source_platform IS NOT NULL AND source_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_membership_group_artist_active "
        "ON group_memberships(group_id, artist_id) WHERE status = 'Active'",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_album_track_song "
        "ON album_tracks(album_id, song_id)",
        "DROP INDEX IF EXISTS uq_membership_group_artist_status",
    ]
    bind = op.get_bind()
    for sql in stmts:
        bind.execute(text(sql))


def downgrade() -> None:
    pass

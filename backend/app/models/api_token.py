"""API 令牌：给外部 AI / 脚本用的「门禁卡」。

与旧的 `app_settings.external_api.token` 的区别：

- **多张并存**，各自带权限范围（read / edit / ingest / admin）
- **可设有效期**、**可单独撤销**、记录最后使用时间与 IP
- **只存哈希**，明文仅在签发时返回一次（丢了只能重发）

旧 token 仍然有效（在 `deps.get_external_principal` 里作为兼容回退），
但它没有分级、没有撤销、也没有使用记录 —— 建议在设置页签发新卡后清空。

权限继承：admin ⊃ edit ⊃ read；ingest ⊃ read。即 `edit` 同时可读，
但 `ingest` 不能改元数据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class ApiToken(TimestampMixin, Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 人类可读的名字（「WorkBuddy 本机」「NAS 备份脚本」），用于审计归属
    name: Mapped[str] = mapped_column(String(80), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 明文前 8 位，列表里用来区分是哪张卡（不足以还原明文）
    token_hint: Mapped[str] = mapped_column(String(16), default="")
    # JSON 数组字符串，如 ["read","edit"]
    scopes: Mapped[str] = mapped_column(Text, default='["read"]')
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    last_used_ip: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_by: Mapped[Optional[str]] = mapped_column(String(80), default=None)
    note: Mapped[Optional[str]] = mapped_column(String(200), default=None)

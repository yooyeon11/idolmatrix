"""跨设备应用设置：首页文字与 AI 配置。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import DbDep, OwnerDep
from app.schemas.app_settings import AppSettingsPatch, AppSettingsRead
from app.services.app_settings import read_all, redact_for_client, write_sections

router = APIRouter(prefix="/app-settings", tags=["app-settings"])


@router.get("", response_model=AppSettingsRead)
def get_app_settings(db: DbDep):
    return redact_for_client(read_all(db))


@router.patch("", response_model=AppSettingsRead)
def patch_app_settings(payload: AppSettingsPatch, db: DbDep, _owner: OwnerDep):
    return redact_for_client(write_sections(db, payload.sections()))

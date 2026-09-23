"""跨设备应用设置：SQLite 键值，缺省用默认值补齐。"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.core.security import is_kept_secret
from app.schemas.app_settings import (
    ACCENTS,
    AppearanceSettings,
    AppSettingsRead,
    ExternalProxySettings,
    HeroSettings,
    HOME_HERO_IMAGES,
    HOME_STAGE_TYPES,
    INGEST_MATCH_MODES,
    IngestAiSettings,
    MtPhotosSettings,
    THEMES,
    TmdbSettings,
    UploaderRule,
    UploaderRulesSettings,
)

_SECRET_FIELDS = {
    "ingest_ai": ("api_key",),
    "mtphotos": ("api_key",),
    "tmdb": ("api_key",),
}
_PUBLIC_ONLY_FIELDS = ("api_key_set",)

# 产品设置分区白名单。原 "network"（放行跨站写请求）已移除 —— 该能力只剩部署层
# 环境变量 ALLOW_CROSS_ORIGIN_WRITES，存量库里的 network 行不再被读取或写回。
# 原 "ingest"（入库自动关联策略档位）已随「内容与匹配」栏一起下线（v3.2.21）——
# 档位固定 strict，写入被忽略；存量行仅被 read_ingest_match_mode 读一眼（归一化后仍是 strict）。
SECTIONS = (
    # 外观与显示（主题/强调色/显示封面/刊头优先图）：2026-09-22 起跨设备同步
    "appearance",
    "hero",
    "ingest_ai",
    "mtphotos",
    "external_proxy",
    "tmdb",
    "uploader_rules",
)

# 轮播池上限：足够撑起主舞台轮播，又不至于让检测/维护变重
HERO_POOL_MAX = 6

# 博主规则条数上限：防止误写入超大 JSON，正常库远用不到
UPLOADER_RULES_MAX = 500


def _defaults() -> dict[str, dict[str, Any]]:
    return {
        "appearance": AppearanceSettings().model_dump(),
        "hero": HeroSettings().model_dump(),
        "ingest_ai": IngestAiSettings().model_dump(),
        "mtphotos": MtPhotosSettings().model_dump(),
        "external_proxy": ExternalProxySettings().model_dump(),
        "tmdb": TmdbSettings().model_dump(),
        "uploader_rules": UploaderRulesSettings().model_dump(),
    }


def _strip_public_only(blob: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in blob.items() if k not in _PUBLIC_ONLY_FIELDS}


def _preserve_secrets(section: str, prev: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """空密钥 / 占位符不覆盖已保存的值。"""
    out = dict(incoming)
    for field in _SECRET_FIELDS.get(section, ()):
        if field in out and is_kept_secret(out.get(field)):
            if field in prev:
                out[field] = prev[field]
            else:
                out.pop(field, None)
    return _strip_public_only(out)


def redact_for_client(read: AppSettingsRead) -> AppSettingsRead:
    """HTTP 回读：密钥改成空串，并用 api_key_set 表示是否已配置。"""
    data = read.model_dump()
    for section, fields in _SECRET_FIELDS.items():
        blob = dict(data.get(section) or {})
        for field in fields:
            blob[f"{field}_set"] = bool(str(blob.get(field) or "").strip())
            blob[field] = ""
        data[section] = blob
    return AppSettingsRead.model_validate(data)


def normalize_uploader_rules(raw: Any) -> list[dict[str, Any]]:
    """归一化博主规则列表：去空名、按名去重（忽略大小写）、过滤非法视频类型。

    只保留「至少一个合法类型」的规则 —— 没选类型的行等于未配置，直接丢弃，
    这样前端把某行清空即视为恢复「跟随 AI」。
    """
    from app.services.ai_service import VIDEO_TYPES

    valid = set(VIDEO_TYPES)
    items = raw if isinstance(raw, list) else []
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        types: list[str] = []
        raw_types = item.get("video_types")
        if isinstance(raw_types, list):
            for t in raw_types:
                if isinstance(t, str) and t in valid and t not in types:
                    types.append(t)
        if not types:
            continue
        seen.add(key)
        rules.append({"name": name, "video_types": types})
        if len(rules) >= UPLOADER_RULES_MAX:
            break
    return rules


def _merge_section(key: str, stored: Any) -> dict[str, Any]:
    base = _defaults()[key]
    blob = stored if isinstance(stored, dict) else {}
    merged = {**base, **_strip_public_only(blob)}
    if key == "appearance":
        # 非法 / 历史值一律回落默认，保证前端拿到的一定是可用值
        merged["theme"] = merged.get("theme") if merged.get("theme") in THEMES else "dark"
        merged["accent"] = merged.get("accent") if merged.get("accent") in ACCENTS else "blue"
        sc = merged.get("show_covers")
        merged["show_covers"] = sc if isinstance(sc, bool) else True
        img = merged.get("home_hero_image")
        merged["home_hero_image"] = img if img in HOME_HERO_IMAGES else "avatar"
    if key == "hero":
        vid = merged.get("video_id")
        if vid is not None:
            try:
                merged["video_id"] = int(vid)
            except (TypeError, ValueError):
                merged["video_id"] = None
        # 轮播池：逐个转 int、去重保序、截断到上限
        raw_ids = merged.get("video_ids")
        ids: list[int] = []
        if isinstance(raw_ids, list):
            for item in raw_ids:
                try:
                    n = int(item)
                except (TypeError, ValueError):
                    continue
                if n > 0 and n not in ids:
                    ids.append(n)
        merged["video_ids"] = ids[:HERO_POOL_MAX]
        stage = merged.get("stage_type")
        merged["stage_type"] = stage if stage in HOME_STAGE_TYPES else "artist"
        for stale in (
            "title",
            "tagline",
            "sub",
            "display_mode",
            "detail_hero_preview",
            "detail_hero_media",
            "home_theme",
            "home_magazine_style",
        ):
            merged.pop(stale, None)
    if key == "ingest_ai":
        # 原「自动化能力」三开关（死设置）已下线：存量库里的值不再回读，也不再写回
        for stale in ("auto_describe", "auto_tag", "auto_subject"):
            merged.pop(stale, None)
    if key == "uploader_rules":
        merged["rules"] = normalize_uploader_rules(merged.get("rules"))
    return merged


def init_cross_origin_flag() -> None:
    """启动时初始化跨站放行开关。

    产品层已移除该设置项，唯一来源是环境变量 ALLOW_CROSS_ORIGIN_WRITES
    （默认 False = 严格 CSRF 校验）。存量库里的 network 分区不再参与初始化。
    """
    from app.core.config import settings
    from app.core.cors_gate import set_allow_cross_origin_writes

    set_allow_cross_origin_writes(bool(settings.allow_cross_origin_writes))


def read_ingest_match_mode(db: Session) -> str:
    """入库匹配档：恒为 strict（原 normal 档与「内容与匹配」设置栏均已下线）。

    调用方（/api/library/match-hints、/api/ai/suggest）保持原签名不变；这里仍读一眼存量
    `ingest` 行，只是任何非 strict 的值（含老库里的 normal）都会被归一化成 strict。
    """
    row = db.get(AppSetting, "ingest")
    mode = row.value.get("match_mode") if row is not None and isinstance(row.value, dict) else None
    return mode if mode in INGEST_MATCH_MODES else "strict"


def read_uploader_rules(db: Session) -> list[dict[str, Any]]:
    """已配置的博主固定视频类型规则（归一化后，保序）。"""
    row = db.get(AppSetting, "uploader_rules")
    raw = row.value.get("rules") if row is not None and isinstance(row.value, dict) else None
    return normalize_uploader_rules(raw)


def uploader_type_map(db: Session) -> dict[str, list[str]]:
    """{博主名(casefold): 固定视频类型}，供待整理 / AI 按上传人查表。"""
    return {r["name"].casefold(): list(r["video_types"]) for r in read_uploader_rules(db)}


def find_uploader_types(db: Session, uploader: Optional[str]) -> list[str]:
    """按上传人查固定视频类型；未配置返回空列表（= 仍交给 AI 判断）。"""
    name = (uploader or "").strip()
    if not name:
        return []
    return uploader_type_map(db).get(name.casefold(), [])


def write_uploader_rules(db: Session, rules: Any) -> list[dict[str, Any]]:
    """整体替换博主规则列表，返回归一化后的结果。"""
    current = normalize_uploader_rules(rules)
    row = db.get(AppSetting, "uploader_rules")
    if row is None:
        db.add(AppSetting(key="uploader_rules", value={"rules": current}))
    else:
        row.value = {"rules": current}
    db.commit()
    return current


def read_all(db: Session) -> AppSettingsRead:
    rows = db.scalars(select(AppSetting)).all()
    stored = {row.key: row.value for row in rows}
    payload = {key: _merge_section(key, stored.get(key)) for key in SECTIONS}
    return AppSettingsRead.model_validate(payload)


def write_sections(db: Session, updates: dict[str, Any]) -> AppSettingsRead:
    for key, value in updates.items():
        if key not in SECTIONS or not isinstance(value, dict):
            continue
        row = db.get(AppSetting, key)
        prev = row.value if row is not None and isinstance(row.value, dict) else {}
        incoming = _preserve_secrets(key, prev, value)
        current = _strip_public_only(_merge_section(key, {**prev, **incoming}))
        if row is None:
            db.add(AppSetting(key=key, value=current))
        else:
            row.value = current
    db.commit()
    if "mtphotos" in updates:
        from app.services.mtphotos import reset_auth_cache

        reset_auth_cache()
    if "external_proxy" in updates:
        # 立即刷新代理缓存，无需等待 TTL 过期
        from app.services import proxy_config

        row = db.get(AppSetting, "external_proxy")
        blob = row.value if row is not None and isinstance(row.value, dict) else {}
        proxy_config.set_proxy(bool(blob.get("enabled")), str(blob.get("url") or ""))
    if "tmdb" in updates:
        # 立即刷新 TMDB 凭据缓存，无需等待 TTL 过期
        from app.services import tmdb_service

        row = db.get(AppSetting, "tmdb")
        blob = row.value if row is not None and isinstance(row.value, dict) else {}
        tmdb_service.set_credentials(bool(blob.get("enabled")), str(blob.get("api_key") or ""))
    # 原 "network" 分支（写入后刷新跨站放行缓存）已移除：
    # 该设置项不再存在，放行开关只在启动时由环境变量决定，运行期不可变。
    if "hero" in updates:
        # 保存首页轮播池后，按需检测入选视频的封面焦点（单条失败不影响保存）
        from app.services.video_focus import ensure_focus_for_video_ids

        row = db.get(AppSetting, "hero")
        blob = row.value if row is not None and isinstance(row.value, dict) else {}
        ids = [v for v in (blob.get("video_ids") or []) if isinstance(v, int)]
        vid = blob.get("video_id")
        if isinstance(vid, int) and vid > 0 and vid not in ids:
            ids.append(vid)
        ensure_focus_for_video_ids(db, ids)
    return read_all(db)

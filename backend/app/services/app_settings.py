"""跨设备应用设置：SQLite 键值，缺省用默认值补齐。"""

from __future__ import annotations

import re
import unicodedata
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
    ExternalApiSettings,
    IngestAiSettings,
    MtPhotosSettings,
    THEMES,
    TmdbSettings,
    UploaderRule,
    UploaderRulesSettings,
    YoutubeSettings,
)

_SECRET_FIELDS = {
    "ingest_ai": ("api_key",),
    "mtphotos": ("api_key",),
    "tmdb": ("api_key",),
    "youtube": ("api_key",),
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
    "youtube",
    "uploader_rules",
    "external_api",
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
        "youtube": YoutubeSettings().model_dump(),
        "uploader_rules": UploaderRulesSettings().model_dump(),
        "external_api": ExternalApiSettings().model_dump(),
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


# 零宽 / 双向控制等不可见字符：混进博主名时肉眼完全看不出来，
# 但会让「逐字比对」失效，把同一个博主拆成两条互不相关的记录。
_INVISIBLE_RE = re.compile(
    r"[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff]"
)


def uploader_key(name: Optional[str]) -> str:
    """博主名归一化键：NFKC（全角转半角）+ 去不可见字符 + 去掉所有空白 + casefold。

    库内常见「肉眼同名」的重复记录（`MBC Kpop` / `MBCKpop`、名字尾随零宽字符、
    全角空格等），在 SQLite 里是两个不同字符串。所有「按博主名查规则」的场景
    （缺失检测频道配置 / 固定视频类型 / 快照统计）统一走本键，保证同一博主的
    各种写法命中同一份配置。
    """
    s = unicodedata.normalize("NFKC", name or "")
    s = _INVISIBLE_RE.sub("", s)
    return "".join(s.split()).casefold()


def normalize_uploader_rules(raw: Any) -> list[dict[str, Any]]:
    """归一化博主规则列表：去空名、按归一化键去重合并、过滤非法视频类型。

    保留「至少一个合法类型」**或**「配置了 YouTube 缺失检测」的规则 ——
    两类配置共用一张表：没选类型也没配频道的行等于未配置，直接丢弃，
    这样前端把某行清空即视为恢复「跟随 AI」。

    去重按 `uploader_key` 而非字面大小写：库里「肉眼同名但写法不同」的博主
    （`MBC Kpop` / `MBCKpop`）会各自出现一行，配置若分散在两行会互相覆盖
    （后者读表时胜出，且顺序不定），因此同名规则在写入侧就合并成一条。
    """
    from app.services.ai_service import VIDEO_TYPES

    valid = set(VIDEO_TYPES)
    items = raw if isinstance(raw, list) else []
    rules: list[dict[str, Any]] = []
    index: dict[str, int] = {}  # 归一化键 → rules 下标
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        types: list[str] = []
        raw_types = item.get("video_types")
        if isinstance(raw_types, list):
            for t in raw_types:
                if isinstance(t, str) and t in valid and t not in types:
                    types.append(t)
        channel = str(item.get("youtube_channel_url") or "").strip()
        enabled = bool(item.get("missing_check_enabled"))

        key = uploader_key(name)
        pos = index.get(key)
        if pos is not None:
            # 同键规则合并：类型取并集；频道配置以「有值的那份」为准
            prev = rules[pos]
            for t in types:
                if t not in prev["video_types"]:
                    prev["video_types"].append(t)
            if channel:
                prev["youtube_channel_url"] = channel
                prev["missing_check_enabled"] = enabled
            continue
        if not types and not channel:
            continue
        index[key] = len(rules)
        rules.append(
            {
                "name": name,
                "video_types": types,
                "youtube_channel_url": channel,
                "missing_check_enabled": enabled,
            }
        )
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
    """{博主名键(uploader_key): 固定视频类型}，供待整理 / AI 按上传人查表。"""
    return {uploader_key(r["name"]): list(r["video_types"]) for r in read_uploader_rules(db)}


def find_uploader_types(db: Session, uploader: Optional[str]) -> list[str]:
    """按上传人查固定视频类型；未配置返回空列表（= 仍交给 AI 判断）。"""
    name = (uploader or "").strip()
    if not name:
        return []
    return uploader_type_map(db).get(uploader_key(name), [])


def find_uploader_channel(db: Session, uploader: Optional[str]) -> Optional[dict[str, Any]]:
    """按上传人查 YouTube 缺失检测配置（频道地址 + 启用开关）。

    先按「逐字（忽略首尾空白与大小写）」精确匹配；未命中时退化为 uploader_key 宽容
    匹配（忽略空白 / 全角 / 不可见字符差异），让库内「肉眼同名」的重复记录共用配置。

    未配置频道地址返回 None；返回 dict 形如
    {"name": 规则里的原名, "youtube_channel_url": str,
     "missing_check_enabled": bool, "matched_by": "exact" | "loose"}。
    """
    name = (uploader or "").strip()
    if not name:
        return None
    rules = read_uploader_rules(db)
    strict = name.casefold()
    loose = uploader_key(name)
    for matched_by in ("exact", "loose"):
        for rule in rules:
            if matched_by == "exact":
                hit = rule["name"].casefold() == strict
            else:
                hit = uploader_key(rule["name"]) == loose
            if not hit:
                continue
            if not rule.get("youtube_channel_url"):
                return None
            return {
                "name": rule["name"],
                "youtube_channel_url": rule["youtube_channel_url"],
                "missing_check_enabled": bool(rule.get("missing_check_enabled")),
                "matched_by": matched_by,
            }
    return None


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
    if "youtube" in updates:
        # 立即刷新 YouTube API 凭据缓存，无需等待 TTL 过期
        from app.services import youtube_service

        row = db.get(AppSetting, "youtube")
        blob = row.value if row is not None and isinstance(row.value, dict) else {}
        youtube_service.set_credentials(bool(blob.get("enabled")), str(blob.get("api_key") or ""))
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

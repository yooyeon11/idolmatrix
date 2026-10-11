"""YouTube Data API v3 服务：博主频道完整投稿快照（缺失视频检测数据源）。

凭据存 SQLite app_setting（key=youtube：enabled/api_key），与 TMDB 同模式：
TTL 缓存 + 设置写入时立即刷新。所有请求经 proxy_config.urlopen，自动套用
站点获取代理（NAS 出网受限时统一走代理）。

拉取链路（quota 便宜且完整，不用 search.list）：
1. channels.list 解析频道引用（UC ID / @handle / /c/ 旧自定义址 / /user/ 旧用户名，
   均失败才退 search.list 兜底）
2. 频道的 uploads 播放列表（UU…）分页 playlistItems.list（每页 50 条）
3. videos.list 批量补时长（50 个/次），duration<70s 判 Shorts（与库内口径一致）
4. 封面选最高分辨率档下载到 derived/yt-covers/ 本地缓存
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.external_channel_video import ExternalChannelVideo
from app.models.music_video import MusicVideo
from app.services import app_settings, proxy_config
from app.services.audiodb_service import ProviderError

logger = logging.getLogger(__name__)

_YT_API = "https://www.googleapis.com/youtube/v3"
_UA = "Mozilla/5.0 (compatible; KpopMediaLibrary/1.0)"
_TTL_SECONDS = 15.0
# 播放列表分页保护：单频道最多拉取条数（防误配到超大频道耗尽 quota）
_MAX_ITEMS = 2000
# Shorts 判定阈值（秒），与库内短视频自动派生口径一致
SHORT_DURATION_SECONDS = 70

_lock = threading.Lock()
_state: Dict[str, Any] = {"enabled": False, "api_key": ""}
_loaded_at: float = 0.0


# ---------------------------------------------------------------------------
# 凭据（TTL 缓存，同 tmdb_service 模式）
# ---------------------------------------------------------------------------

def set_credentials(enabled: bool, api_key: str) -> None:
    """直接更新凭据状态（设置写入后立即生效，绕过 TTL）。"""
    global _loaded_at
    with _lock:
        _state["enabled"] = bool(enabled)
        _state["api_key"] = (api_key or "").strip()
        _loaded_at = time.monotonic()


def current() -> Dict[str, Any]:
    """返回当前凭据状态副本（供诊断/测试）。"""
    with _lock:
        return dict(_state)


def _credentials() -> Dict[str, Any]:
    global _loaded_at
    now = time.monotonic()
    if now - _loaded_at >= _TTL_SECONDS:
        try:
            # 延迟导入避免循环依赖
            from app.core.database import SessionLocal
            from app.models.app_setting import AppSetting

            with SessionLocal() as db:
                row = db.get(AppSetting, "youtube")
                blob = row.value if row is not None and isinstance(row.value, dict) else {}
            set_credentials(bool(blob.get("enabled")), str(blob.get("api_key") or ""))
        except Exception as e:  # noqa: BLE001
            logger.debug("[youtube] 读取设置失败，维持当前状态: %s", e)
            with _lock:
                _loaded_at = time.monotonic()
    with _lock:
        return dict(_state)


def _enabled_credentials() -> Dict[str, Any]:
    creds = _credentials()
    if not (creds["enabled"] and creds["api_key"]):
        raise ProviderError("YouTube Data API 未启用或未配置 API Key（设置 · 基础设置 · YouTube 缺失检测）")
    return creds


# ---------------------------------------------------------------------------
# HTTP（统一走 proxy_config，出网代理全站共用）
# ---------------------------------------------------------------------------

def _http_json(
    url: str,
    params: Optional[dict] = None,
    api_key: Optional[str] = None,
    error_hint: str = "YouTube",
) -> dict:
    key = api_key or _enabled_credentials()["api_key"]
    merged = dict(params or {})
    merged["key"] = key
    full = f"{url}?{urllib.parse.urlencode(merged)}"
    req = urllib.request.Request(full, headers={"User-Agent": _UA})
    try:
        with proxy_config.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                body = resp.read().decode("utf-8", "replace")[:300]
                raise ProviderError(f"{error_hint} 请求失败（HTTP {resp.status}）：{body}")
            return json_loads(resp.read().decode("utf-8"))
    except ProviderError:
        raise
    except TimeoutError as e:
        raise ProviderError(f"{error_hint} 请求超时，请稍后重试") from e
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"{error_hint} 请求失败：{e}") from e


def json_loads(text: str) -> dict:
    import json

    return json.loads(text)


def test_connection(api_key: str) -> Dict[str, Any]:
    """测试 API Key 连通性（videos.list 单条，1 quota）。"""
    data = _http_json(f"{_YT_API}/videos", {"part": "id", "id": "jNQXAC9IVRw"}, api_key=api_key)
    return {"ok": True, "items": len(data.get("items") or [])}


# ---------------------------------------------------------------------------
# 频道引用解析 → 频道投稿全集
# ---------------------------------------------------------------------------

def _resolve_channel(raw: str) -> Dict[str, Any]:
    """把用户输入的频道引用解析成 channels.list 可用的资源 ID。

    支持：裸 UC ID、@handle、/channel/UC…、/@handle、/c/…、/user/… 链接；
    全部落空时最后退 search.list（100 quota，仅在其它方式失败时触发）。
    """
    text = (raw or "").strip()
    if not text:
        raise ProviderError("频道地址为空，请先在 设置 · 资料库 · 博主 里配置")

    # 纯 ID 直接命中
    if re.fullmatch(r"UC[\w-]{10,}", text):
        return {"id": text}

    # 链接 / @handle：提取路径段
    m = re.search(r"youtube\.com/(?:@([\w.\-]+)|channel/(UC[\w-]+)|c/([\w.\-]+)|user/([\w.\-]+))", text)
    if not m and text.startswith("@"):
        m = re.match(r"@([\w.\-]+)", text)

    if m:
        if m.group(1):  # @handle
            ch = _channels_list({"forHandle": m.group(1)})
            if ch:
                return {"id": ch["id"]}
            raise ProviderError(f"找不到 @handle 为 {m.group(1)} 的频道")
        if m.group(2):  # /channel/UC…
            return {"id": m.group(2)}
        if m.group(3):  # /c/…（旧自定义地址）
            for param in ({"forHandle": m.group(3)}, {"forUsername": m.group(3)}):
                ch = _channels_list(param)
                if ch:
                    return {"id": ch["id"]}
        if m.group(4):  # /user/…（旧用户名）
            ch = _channels_list({"forUsername": m.group(4)})
            if ch:
                return {"id": ch["id"]}

    # 兜底：search.list（贵但通用；仅上面全部失败才走到这里）
    data = _http_json(
        f"{_YT_API}/search",
        {"part": "snippet", "q": text, "type": "channel", "maxResults": 1},
    )
    items = data.get("items") or []
    if items:
        return {"id": items[0]["snippet"]["channelId"]}
    raise ProviderError(f"无法识别的频道地址：{text}")


def _channels_list(params: dict) -> Optional[dict]:
    data = _http_json(f"{_YT_API}/channels", {"part": "contentDetails,snippet", **params})
    items = data.get("items") or []
    return items[0] if items else None


def _parse_iso_duration(s: str) -> Optional[int]:
    """ISO 8601（PT#H#M#S）→ 秒。"""
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


def probe_channel(channel_ref: str) -> Dict[str, Any]:
    """校验频道地址是否可用（1-2 quota，不拉投稿列表）。

    用途：在「设置 · 资料库 · 博主」里填完地址后点「测试」，立刻确认地址能否解析成
    频道、以及解析到的是不是想要的频道（回读频道名）。地址写错时抛 ProviderError。
    """
    ref = _resolve_channel(channel_ref)
    ch = _channels_list({"id": ref["id"]})
    if not ch:
        raise ProviderError(f"频道 {ref['id']} 不存在或没有公开信息")
    playlists = (ch.get("contentDetails") or {}).get("relatedPlaylists") or {}
    return {
        "channel_id": ch.get("id") or ref["id"],
        "title": (ch.get("snippet") or {}).get("title") or "",
        "uploads_playlist": playlists.get("uploads") or "",
    }


def fetch_channel_uploads(channel_ref: str) -> Dict[str, Any]:
    """拉取频道完整投稿列表（含 Shorts，按时长归类）。

    返回 {"channel_title", "channel_id", "items": [{video_id,title,published_at,duration,thumb_url}]}。
    """
    _enabled_credentials()  # 先校验凭据，未配置直接给清晰错误
    ch = _channels_list({"id": _resolve_channel(channel_ref)["id"]})
    if not ch:
        raise ProviderError("频道不存在或不可访问")
    uploads_id = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads_id:
        raise ProviderError("频道没有投稿播放列表（可能无公开视频）")

    items: List[dict] = []
    page_token: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"part": "snippet", "playlistId": uploads_id, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        data = _http_json(f"{_YT_API}/playlistItems", params)
        for it in data.get("items") or []:
            vid = (it.get("snippet", {}).get("resourceId") or {}).get("videoId")
            if not vid:
                continue
            items.append({"video_id": vid, "raw": it.get("snippet", {})})
            if len(items) >= _MAX_ITEMS:
                break
        if len(items) >= _MAX_ITEMS:
            logger.warning("[youtube] 拉取条数达上限 %s，截断（频道：%s）", _MAX_ITEMS, channel_ref)
            break
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    # 批量补时长（videos.list 50 个/次）
    for i in range(0, len(items), 50):
        batch = items[i : i + 50]
        data = _http_json(
            f"{_YT_API}/videos",
            {"part": "contentDetails", "id": ",".join(x["video_id"] for x in batch)},
        )
        durations = {
            it["id"]: _parse_iso_duration(it.get("contentDetails", {}).get("duration", ""))
            for it in data.get("items") or []
        }
        for x in batch:
            x["duration"] = durations.get(x["video_id"])

    def _best_thumb(sn: dict) -> str:
        thumbs = sn.get("thumbnails") or {}
        for key in ("maxres", "standard", "high", "medium", "default"):
            if thumbs.get(key, {}).get("url"):
                return thumbs[key]["url"]
        return ""

    for x in items:
        sn = x.pop("raw")
        published = sn.get("publishedAt") or ""
        x["title"] = str(sn.get("title") or "")
        x["thumb_url"] = _best_thumb(sn)
        try:
            x["published_at"] = datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None
        except ValueError:
            x["published_at"] = None

    return {
        "channel_title": str(ch.get("snippet", {}).get("title") or ""),
        "channel_id": ch.get("id") or "",
        "items": items,
    }


# ---------------------------------------------------------------------------
# 封面本地缓存
# ---------------------------------------------------------------------------

def _download_cover(video_id: str, thumb_url: str) -> Optional[str]:
    """下载封面到 derived/yt-covers/，返回文件名；失败返回 None（不阻断检测）。"""
    if not thumb_url:
        return None
    ext = ".webp" if ".webp" in thumb_url else ".jpg"
    name = f"{video_id}{ext}"
    dest = settings.youtube_cover_dir / name
    if dest.exists() and dest.stat().st_size > 0:
        return name
    try:
        req = urllib.request.Request(thumb_url, headers={"User-Agent": _UA})
        with proxy_config.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                logger.warning("[youtube] 封面下载失败 HTTP %s：%s", resp.status, video_id)
                return None
            data = resp.read()
        settings.youtube_cover_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return name
    except Exception as e:  # noqa: BLE001
        logger.warning("[youtube] 封面下载失败（%s）：%s", video_id, e)
        return None


# ---------------------------------------------------------------------------
# 快照落库 + 缺失比对
# ---------------------------------------------------------------------------

def _snapshot_keys(db: Session, norm: str) -> list[str]:
    """列出归一到同一博主键的全部历史 uploader_key（含旧版 casefold 键）。

    旧版本直接拿 `uploader_name.casefold()` 当键，未做 NFKC / 去空白 / 去不可见字符，
    同一博主可能留下多个键的快照行。按归一化键捞全，后续读取/改键/去重才不会漏。
    """
    all_keys = db.scalars(select(ExternalChannelVideo.uploader_key).distinct()).all()
    return [k for k in all_keys if app_settings.uploader_key(k) == norm]


def sync_uploader(db: Session, uploader_name: str, channel_ref: str) -> Dict[str, Any]:
    """拉取频道全集并刷新快照；返回同步统计（不返回缺失明细，由 report 统一计算）。"""
    result = fetch_channel_uploads(channel_ref)
    items = result["items"]
    now = datetime.utcnow()
    key = app_settings.uploader_key(uploader_name)
    # 历史键一并纳入，随后原地改写成新键并去重（迁移期两套键并存）
    keys = set(_snapshot_keys(db, key)) | {key}

    existing: dict[str, ExternalChannelVideo] = {}
    for row in db.scalars(
        select(ExternalChannelVideo).where(ExternalChannelVideo.uploader_key.in_(keys))
    ).all():
        prev = existing.get(row.video_id)
        if prev is None:
            existing[row.video_id] = row
            continue
        # 同一 video_id 在旧键与新键下各留了一行：保留新键那行，删掉多余行
        if prev.uploader_key == key or row.uploader_key != key:
            db.delete(row)
        else:
            db.delete(prev)
            existing[row.video_id] = row
    # 先落实去重删除，再把历史行改键，避免 UPDATE 与残留行撞 (uploader_key, video_id) 唯一约束
    db.flush()
    for row in existing.values():
        if row.uploader_key != key:
            row.uploader_key = key

    for it in items:
        vid = it["video_id"]
        duration = it.get("duration")
        row = existing.get(vid)
        if row is None:
            row = ExternalChannelVideo(uploader_key=key, video_id=vid)
            db.add(row)
            existing[vid] = row
        row.title = it.get("title") or ""
        row.duration = duration
        row.published_at = it.get("published_at")
        row.is_short = bool(duration is not None and duration < SHORT_DURATION_SECONDS)
        row.last_seen_at = now
        # 封面：本地缓存缺失才下载（增量，不重复拉）
        if not row.thumbnail_path or not (settings.youtube_cover_dir / row.thumbnail_path).exists():
            row.thumbnail_path = _download_cover(vid, it.get("thumb_url") or "")

    # 频道侧已删除/私有化的条目：物理删除快照（否则永远算「缺失」）
    seen_ids = {it["video_id"] for it in items}
    stale = [vid for vid in existing if vid not in seen_ids]
    if stale:
        db.execute(
            delete(ExternalChannelVideo).where(
                ExternalChannelVideo.uploader_key == key,
                ExternalChannelVideo.video_id.in_(stale),
            )
        )

    db.commit()
    logger.info(
        "[youtube] 博主「%s」快照刷新：%d 条（移除 %d）",
        uploader_name,
        len(items),
        len(stale),
    )
    return {
        "channel_title": result["channel_title"],
        "channel_total": len(items),
        "removed": len(stale),
    }


def missing_report(db: Session, uploader_name: str) -> Dict[str, Any]:
    """缺失比对报告：频道全集快照 − 库内该博主 YouTube 视频（按 source_id 比对）。"""
    key = app_settings.uploader_key(uploader_name)

    keys = _snapshot_keys(db, key)
    rows = (
        db.scalars(
            select(ExternalChannelVideo).where(
                ExternalChannelVideo.uploader_key.in_(keys)
            )
        ).all()
        if keys
        else []
    )
    # 迁移期旧键可能仍有残留：按 video_id 去重，避免缺失清单出现重复行
    rows = list({row.video_id: row for row in rows}.values())

    # 库内该博主的 YouTube 视频 ID 集合：先取库内博主名，用统一归一化键筛出
    # 「肉眼同名但写法不同」的候选名，再精确取 source_id。SQLite 的 lower() 只认 ASCII，
    # 同名不同写法（空格 / 全角 / 零宽字符）在 SQL 里是两个博主，会造成缺失误报。
    lib_names = [
        name
        for name in db.scalars(
            select(MusicVideo.original_uploader)
            .where(MusicVideo.deleted_at.is_(None))
            .where(func.lower(MusicVideo.source_platform) == "youtube")
            .where(MusicVideo.original_uploader.is_not(None))
            .distinct()
        ).all()
        if app_settings.uploader_key(name) == key
    ]
    lib_ids: set[str] = set()
    if lib_names:
        lib_ids = set(
            db.scalars(
                select(MusicVideo.source_id)
                .where(MusicVideo.deleted_at.is_(None))
                .where(func.lower(MusicVideo.source_platform) == "youtube")
                .where(MusicVideo.original_uploader.in_(lib_names))
                .where(MusicVideo.source_id.is_not(None))
            ).all()
        )

    items = []
    for row in rows:
        if row.video_id in lib_ids:
            continue
        items.append(
            {
                "video_id": row.video_id,
                "title": row.title,
                "duration": row.duration,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "is_short": bool(row.is_short),
                "thumbnail_path": row.thumbnail_path,
                "url": f"https://www.youtube.com/watch?v={row.video_id}",
            }
        )
    # 发布时间倒序：新的缺失排前面
    items.sort(key=lambda x: x["published_at"] or "", reverse=True)

    last_checked = None
    if rows:
        last_checked = max(r.last_seen_at for r in rows)

    return {
        "uploader": uploader_name,
        "channel_video_total": len(rows),
        "library_count": len(lib_ids),
        "missing_count": len(items),
        "short_missing_count": sum(1 for i in items if i["is_short"]),
        "last_checked_at": last_checked.isoformat() if last_checked else None,
        "items": items,
    }

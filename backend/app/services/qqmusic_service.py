"""QQ 音乐数据源：给入库判断补充「歌曲 / 专辑 / 歌手」的权威信息。

接口与 `.dsh/skills/qqmusic` 一致（Agent Gateway）：
    POST {base}/discover/search
    Authorization: Bearer <qmk-...>
    {"params": {"keyword": ..., "type": "0", "page": 0}, "comm": {"skill_version": "0.0.3"}}
type：0 歌曲 / 1 专辑 / 2 歌单 / 3 MV / 4 电台 / 5 歌手
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE = "https://a.y.qq.com"
DEFAULT_VERSION = "0.0.3"

TYPE_MAP = {
    "song": "0",
    "album": "1",
    "playlist": "2",
    "mv": "3",
    "radio": "4",
    "singer": "5",
    "artist": "5",
}


class QQMusicError(RuntimeError):
    pass


def _cfg() -> dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services import ingest_service

    db = SessionLocal()
    try:
        return ingest_service.get_config(db)
    finally:
        db.close()


def available(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or _cfg()
    return bool(cfg.get("qqmusic_enabled") and str(cfg.get("qqmusic_api_key") or "").strip())


def search(keyword: str, kind: str = "song", page: int = 0) -> dict[str, Any]:
    cfg = _cfg()
    if not cfg.get("qqmusic_enabled"):
        raise QQMusicError("QQ 音乐数据源未启用")
    key = str(cfg.get("qqmusic_api_key") or "").strip()
    if not key:
        raise QQMusicError("未配置 QQ 音乐 API Key")
    base = str(cfg.get("qqmusic_base_url") or DEFAULT_BASE).rstrip("/")
    version = str(cfg.get("qqmusic_skill_version") or DEFAULT_VERSION)
    payload = {
        "params": {"keyword": keyword, "type": TYPE_MAP.get(kind, "0"), "page": page},
        "comm": {"skill_version": version},
    }
    req = urllib.request.Request(
        base + "/discover/search",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise QQMusicError(f"HTTP {e.code}：{body}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise QQMusicError(f"请求失败：{e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise QQMusicError(f"返回非 JSON：{raw[:200]}") from e
    return data


def brief(keyword: str, kind: str = "song", limit: int = 5) -> list[dict[str, Any]]:
    """给 Agent 用的精简结果：只留名字与歌手，避免上下文过大。"""
    data = search(keyword, kind)
    body = data.get("data") if isinstance(data.get("data"), dict) else data
    out: list[dict[str, Any]] = []
    songs = body.get("songs") or []
    albums = body.get("albums") or []
    singers = body.get("singers") or []
    for s in songs[:limit]:
        out.append(
            {
                "kind": "song",
                "name": s.get("songName"),
                "singer": s.get("singerName"),
                "mid": s.get("songMid"),
            }
        )
    for a in albums[:limit]:
        out.append(
            {
                "kind": "album",
                "name": a.get("albumName"),
                "singer": a.get("singerName"),
                "mid": a.get("albumMid"),
            }
        )
    for s in singers[:limit]:
        out.append(
            {
                "kind": "singer",
                "name": s.get("singerName") or s.get("name"),
                "mid": s.get("singerMid"),
            }
        )
    return out

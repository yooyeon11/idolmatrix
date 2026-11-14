"""入库清单：规则草稿、指纹、开关配置。

设计原则
- **只建议不创建**：这里只产出 JSON，绝不写 artists/groups/songs/music_videos
- **幂等**：`file_path + fingerprint(size+mtime+前1MB sha1)` 不变就不重复生成
- **可降级**：规则能定的先出 draft；外置 Agent 之后再补全为 ready
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.artist import Artist
from app.models.group import Group
from app.models.ingest import IngestManifest, IngestRequest
from app.models.incoming_file import IncomingFile
from app.models.song import Song

CONFIG_KEY = "ingest_automation"

DEFAULT_CONFIG: dict[str, Any] = {
    # 三层开关：规则草稿 / 页面 AI 按钮 / 外置 Agent
    "draft_enabled": True,
    "ai_button_enabled": True,
    "agent_enabled": True,
    # 外置 Agent 的定时（本机 DSH 任务读取；项目只存不改）
    "agent_schedule": ["00:05", "12:05"],
    # 内置 Agent（容器内直连 API）：单独开关，便于灰度测试
    "builtin_enabled": False,
    "builtin_max_steps": 8,
    "builtin_batch": 10,
    "builtin_model": "",
    # 内置 Agent 独立模型（留空 = 沿用「入库 AI」的配置）
    "agent_base_url": "",
    "agent_api_key": "",
    "agent_model": "",
    # 外部数据源：QQ 音乐（补歌名/专辑/歌手）、联网搜索（默认关）
    "qqmusic_enabled": False,
    "qqmusic_api_key": "",
    "qqmusic_base_url": "https://a.y.qq.com",
    "qqmusic_skill_version": "0.0.3",
    "web_search_enabled": False,
    "harness_enabled": False,
    "harness_interval_minutes": 30,
}

# 画质/来源标签：只在解析文件名时剥离，绝不进 name
_QUALITY_RE = re.compile(
    r"\[(?:4K|8K|2160p|1440p|1080p|720p|60fps|60p|HEVC|AV1|VP9)[^\]]*\]", re.I
)
_DATE_PREFIX_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\s+")
_BRACKET_RE = re.compile(r"\[([^\]]{2,40})\]")
_ARTIST_SONG_RE = re.compile(r"^(?P<artist>[^\-–—]{2,40}?)\s*[-–—]\s*(?P<song>.+)$")


def norm_path(value: str) -> str:
    """把代理字符还原成正常 UTF-8（历史脏数据 / 无 locale 环境写入的路径）。"""
    try:
        return value.encode("utf-8", "surrogateescape").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def get_config(db: Session) -> dict[str, Any]:
    row = db.get(AppSetting, CONFIG_KEY)
    merged = dict(DEFAULT_CONFIG)
    if row and isinstance(row.value, dict):
        merged.update({k: v for k, v in row.value.items() if k in DEFAULT_CONFIG})
    return merged


def set_config(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    cur = get_config(db)
    for k, v in (patch or {}).items():
        if k in DEFAULT_CONFIG and v is not None:
            cur[k] = v
    row = db.get(AppSetting, CONFIG_KEY)
    if row:
        row.value = cur
    else:
        db.add(AppSetting(key=CONFIG_KEY, value=cur))
    db.commit()
    return cur


# ---------------------------------------------------------------- 指纹


def fingerprint(path: str | Path) -> str:
    """size + mtime_ns + 前 1MB sha1：文件被替换才变化，重命名不算。"""
    p = Path(path)
    try:
        st = p.stat()
        with p.open("rb") as f:
            head = f.read(1024 * 1024)
    except OSError:
        return ""
    digest = hashlib.sha1(head).hexdigest()[:16]
    return f"{st.st_size}-{st.st_mtime_ns}-{digest}"


# ---------------------------------------------------------------- 实体索引

_index_cache: tuple[float, dict[str, tuple[str, int, str]]] | None = None
_INDEX_TTL = 60.0


def _entity_index(db: Session) -> dict[str, tuple[str, int, str]]:
    """小写名称 → (kind, id, 官方名)。60 秒缓存，够用且不会拖慢入库。"""
    global _index_cache
    now = time.time()
    if _index_cache and now - _index_cache[0] < _INDEX_TTL:
        return _index_cache[1]

    idx: dict[str, tuple[str, int, str]] = {}

    def put(kind: str, rows: list[Any]) -> None:
        for r in rows:
            for raw in (
                r.name,
                getattr(r, "chinese_name", None),
                getattr(r, "english_name", None),
                getattr(r, "korean_name", None),
                getattr(r, "stage_name", None),
                *(getattr(r, "aliases", None) or []),
            ):
                key = (raw or "").strip().lower()
                if key and key not in idx:
                    idx[key] = (kind, r.id, r.name)

    put("group", db.scalars(select(Group).where(Group.deleted_at.is_(None))).all())
    put("artist", db.scalars(select(Artist).where(Artist.deleted_at.is_(None))).all())
    put("song", db.scalars(select(Song).where(Song.deleted_at.is_(None))).all())
    _index_cache = (now, idx)
    return idx


def resolve_entities(db: Session, kind: str, names: list[str]) -> tuple[list[dict], list[str]]:
    """把名字列表解析成库内实体；返回 (命中, 未命中)。

    先精确（名称/别名），再退一步做子串匹配；都不中就算"待新建"。
    """
    idx = _entity_index(db)
    matched: list[dict] = []
    unmatched: list[str] = []
    for raw in names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.lower()
        hit = idx.get(key)
        if hit is None or hit[0] != kind:
            # 子串匹配只在名字够长时启用，避免 "J" 这种单字把一堆实体拉进来
            hit = None
            if len(key) >= 3:
                hit = next(
                    (v for k, v in idx.items() if v[0] == kind and (key in k or k in key)),
                    None,
                )
        if hit and all(m["id"] != hit[1] for m in matched):
            matched.append({"id": hit[1], "name": hit[2]})
        elif not hit:
            unmatched.append(name)
    return matched, unmatched


# ---------------------------------------------------------------- 规则草稿

_UPLOADER_RULE_HINT = {
    "M COUNTDOWN": "M COUNTDOWN",
    "음악중심": "Show! 音乐中心",
    "인기가요": "人气歌谣",
    "Music Bank": "音乐银行",
}


def read_info_json(path: str | Path) -> dict[str, Any]:
    """公开版本：读同目录同名 .info.json（不存在返回空 dict）。"""
    return _read_info_json(Path(path))


def _read_info_json(path: Path) -> dict[str, Any]:
    p = path.with_suffix("")
    cand = path.parent / f"{p.name}.info.json"
    if not cand.exists():
        cand = path.parent / f"{path.stem}.info.json"
    try:
        return json.loads(cand.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _clean_title(raw: str) -> str:
    t = _QUALITY_RE.sub("", raw or "")
    return re.sub(r"\s{2,}", " ", t).strip()


def build_draft(db: Session, rec: IncomingFile) -> tuple[dict[str, Any], float]:
    """规则草稿：只用 info.json / 文件名 / 技术参数 / 库内实体做确定性判断。"""
    path = Path(rec.file_path)
    info = _read_info_json(path)
    raw_title = _clean_title(str(info.get("title") or path.stem))
    uploader = str(info.get("uploader") or info.get("channel") or "")
    if not uploader:
        m = _BRACKET_RE.findall(path.stem)
        uploader = m[-1] if m else ""

    # 日期：文件名前缀 > info.upload_date
    performance_date = ""
    m = _DATE_PREFIX_RE.match(path.name)
    if m:
        yy, mm, dd = m.groups()
        performance_date = f"20{yy}-{mm}-{dd}"
    elif info.get("upload_date"):
        u = str(info["upload_date"])
        if len(u) == 8 and u.isdigit():
            performance_date = f"{u[:4]}-{u[4:6]}-{u[6:]}"

    duration = rec.duration or (int(info["duration"]) if isinstance(info.get("duration"), (int, float)) else None)
    width, height = rec.width, rec.height

    # 视频类型：时长/画幅/关键词（频道规则留给 Agent 补全）
    folded = raw_title.lower()
    if "fancam" in folded or "직캠" in folded or "直拍" in folded or "fan cam" in folded:
        video_type = "Fancam"
    elif duration and duration < 60:
        video_type = "ShortVideo"
    else:
        video_type = "PerformanceVideo"

    # 艺人 - 歌曲
    artist_raw, song_raw = "", ""
    body = _DATE_PREFIX_RE.sub("", raw_title)
    m2 = _ARTIST_SONG_RE.match(body)
    if m2:
        artist_raw = m2.group("artist").strip()
        song_raw = m2.group("song").strip()

    idx = _entity_index(db)
    groups, artists, songs = [], [], []
    for token in _split_entities(artist_raw):
        hit = idx.get(token.lower())
        if not hit:
            continue
        kind, eid, name = hit
        target = {"group": groups, "artist": artists, "song": songs}[kind]
        if all(x["id"] != eid for x in target):
            target.append({"id": eid, "name": name})
    for token in _split_entities(song_raw):
        hit = idx.get(token.lower())
        if hit and hit[0] == "song" and all(x["id"] != hit[1] for x in songs):
            songs.append({"id": hit[1], "name": hit[2]})

    # 库内没有的实体只做"建议"，等用户确认后才创建
    new_entities: list[dict[str, Any]] = []
    if artist_raw and not groups and not artists:
        new_entities.append({"kind": "artist", "name": artist_raw, "reason": "库内无同名艺人/组合"})
    if song_raw and not songs:
        cleaned_song = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", song_raw).strip(" -–—")
        if cleaned_song:
            new_entities.append({"kind": "song", "name": cleaned_song, "reason": "库内无同名歌曲"})

    confidence = 0.45
    evidence = []
    if performance_date:
        confidence += 0.15
        evidence.append({"field": "performance_date", "source": "filename", "text": path.name[:60]})
    if groups:
        confidence += 0.1
    if artists:
        confidence += 0.1
    if songs:
        confidence += 0.12
        evidence.append({"field": "song", "source": "title", "text": song_raw[:60]})
    if uploader:
        confidence += 0.03

    payload: dict[str, Any] = {
        "name": raw_title,
        "video_type": video_type,
        "performance_date": performance_date or None,
        "event_name": _UPLOADER_RULE_HINT.get(uploader) or None,
        "uploader": uploader or None,
        "duration": duration,
        "width": width,
        "height": height,
        "groups": groups,
        "artists": artists,
        "songs": songs,
        "new_entities": new_entities,
        "evidence": evidence,
        "warnings": [] if (groups or artists or songs) else ["库内未匹配到艺人/组合/歌曲"],
    }
    return payload, round(min(confidence, 0.95), 2)


def _split_entities(raw: str) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[&/,、]|feat\.|ft\.", raw, flags=re.I)
    return [p.strip(" -–—()[]") for p in parts if p.strip(" -–—()[]")]


def enrich_albums(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """用库内关联补全曲目所属专辑（模型通常不知道专辑）。

    - 只补「已匹配到库内 id」的歌曲
    - 已填过的专辑不覆盖
    - 写回 payload["suggested_tracks"]（前端卡片与入库表单都读这个）
    """
    from app.models.album import Album, AlbumTrack

    songs = [x for x in (payload.get("songs") or []) if isinstance(x, dict) and x.get("id")]
    if not songs:
        return payload

    ids = [int(x["id"]) for x in songs]
    rows = db.execute(
        select(AlbumTrack.song_id, Album.name)
        .join(Album, Album.id == AlbumTrack.album_id)
        .where(AlbumTrack.song_id.in_(ids))
        .where(Album.deleted_at.is_(None))
    ).all()

    by_song: dict[int, list[str]] = {}
    for song_id, album_name in rows:
        if album_name and album_name not in by_song.setdefault(int(song_id), []):
            by_song[int(song_id)].append(album_name)

    if not by_song:
        return payload

    tracks = payload.get("suggested_tracks")
    if not isinstance(tracks, list):
        tracks = []
    # 按名字补，避免模型给的 track 顺序与 songs 不一致
    name_to_albums = {x["name"]: by_song[int(x["id"])] for x in songs if x.get("id") in by_song}
    for t in tracks:
        if not isinstance(t, dict):
            continue
        if t.get("albums"):
            continue
        t["albums"] = name_to_albums.get(str(t.get("song") or ""), [])
    if not tracks:
        tracks = [{"song": x["name"], "albums": by_song[int(x["id"])]} for x in songs if int(x["id"]) in by_song]
    payload["suggested_tracks"] = tracks
    return payload


def compute_destination_rel(db: Session, payload: dict[str, Any], file_name: str) -> dict[str, Any]:
    """按与 `/library/preview-path` 完全相同的规则算入库路径。

    只做推导，不写库；不适用（缺主体）时返回 notice，路径退回文件名。
    """
    from datetime import date as _date
    from pathlib import Path

    from app.models.artist import Artist
    from app.models.group import Group
    from app.models.music_video import MusicVideo
    from app.models.song import Song
    from app.services.library_service import (
        ArtistDirNameConflict,
        auto_organize_rel,
        match_existing_dir,
    )
    from app.services.video_meta import is_short_video, normalize_video_types

    vt_list = payload.get("video_types") or ([payload["video_type"]] if payload.get("video_type") else [])
    video_type, video_types = normalize_video_types(vt_list, payload.get("video_type"))

    perf = None
    if payload.get("performance_date"):
        try:
            perf = _date.fromisoformat(str(payload["performance_date"]).strip())
        except ValueError:
            perf = None

    mv = MusicVideo(
        name=payload.get("name") or file_name,
        video_type=video_type,
        video_types=video_types,
        is_solo=bool(payload.get("is_solo")),
        is_short=is_short_video(video_types, video_type),
        performance_date=perf,
    )
    mv.artists = [a for a in (db.get(Artist, x["id"]) for x in payload.get("artists") or []) if a]
    mv.groups = [g for g in (db.get(Group, x["id"]) for x in payload.get("groups") or []) if g]
    mv.songs = [t for t in (db.get(Song, x["id"]) for x in payload.get("songs") or []) if t]
    # 待新建实体以临时对象参与推导（与 preview-path 同口径）
    for ent in payload.get("new_entities") or []:
        name = str(ent.get("name") or "").strip()
        if not name:
            continue
        if ent.get("kind") == "artist":
            mv.artists.append(Artist(name=name))
        elif ent.get("kind") == "group":
            mv.groups.append(Group(name=name))
        elif ent.get("kind") == "song":
            mv.songs.append(Song(name=name))

    notice = None
    try:
        auto_dir = auto_organize_rel(mv, db=db)
    except ArtistDirNameConflict as exc:
        auto_dir = None
        notice = f"{exc}。"
    if auto_dir is not None:
        auto_dir = match_existing_dir(auto_dir)
        rel = auto_dir / file_name
    else:
        rel = Path(file_name)
        notice = notice or "未关联艺人/组合，无法自动归档（入库前请补全关联）"

    return {"destination_rel": rel.as_posix(), "path_notice": notice}


# ---------------------------------------------------------------- 清单读写


def ensure_draft(db: Session, rec: IncomingFile, *, force: bool = False) -> IngestManifest:
    """生成/更新一条 draft 清单；指纹没变且已有清单时直接返回。"""
    path_key = norm_path(rec.file_path)
    fp = fingerprint(rec.file_path)
    row = db.scalars(
        select(IngestManifest).where(IngestManifest.file_path == path_key)
    ).first()

    if row and not force and row.fingerprint == fp and row.status in ("draft", "ready"):
        return row

    payload, conf = build_draft(db, rec)
    if not row:
        row = IngestManifest(file_path=path_key, generated_at=datetime.utcnow())
        db.add(row)
    row.fingerprint = fp
    row.payload = payload
    row.confidence = conf
    # 文件已换：旧结论作废，回到 draft 等 Agent 补全
    row.status = "draft"
    row.source = "draft"
    row.rules_version = "v1"
    row.generated_at = datetime.utcnow()
    row.error = None
    db.commit()
    db.refresh(row)
    return row


def pending_files(db: Session, limit: int = 50) -> list[IncomingFile]:
    """待出清单：没有清单，或文件指纹变了。已 accepted/rejected 的不再出现。"""
    rows = db.scalars(select(IncomingFile).order_by(IncomingFile.id.desc())).all()
    manifests = {
        norm_path(m.file_path): m
        for m in db.scalars(select(IngestManifest)).all()
    }
    out: list[IncomingFile] = []
    for rec in rows:
        m = manifests.get(norm_path(rec.file_path))
        if m is None:
            out.append(rec)
        elif m.status in ("accepted", "rejected"):
            continue
        elif m.status == "draft":
            # 草稿等 Agent 补全；补完会变 ready，就不再进队列
            out.append(rec)
        elif m.fingerprint != fingerprint(rec.file_path):
            out.append(rec)
        if len(out) >= limit:
            break
    return out


def upsert_manifest(
    db: Session,
    file_path: str,
    payload: dict[str, Any],
    *,
    confidence: float | None = None,
    source: str = "agent",
    model: str | None = None,
    status: str = "ready",
) -> IngestManifest:
    """写入/覆盖一条清单（Agent 回写用）。"""
    path_key = norm_path(file_path)
    row = db.scalars(
        select(IngestManifest).where(IngestManifest.file_path == path_key)
    ).first()
    if row is None:
        row = IngestManifest(file_path=path_key, generated_at=datetime.utcnow())
        db.add(row)
    row.fingerprint = fingerprint(file_path)
    row.payload = payload
    row.confidence = confidence
    row.source = source
    row.model = model
    row.status = status
    row.generated_at = datetime.utcnow()
    row.error = None
    db.commit()
    db.refresh(row)
    return row


def generate_missing_drafts(db: Session, limit: int = 30) -> int:
    """扫描后补草稿：没有清单、或文件比清单更新时才生成。返回生成条数。"""
    if not get_config(db).get("draft_enabled"):
        return 0
    manifests = {m.file_path: m for m in db.scalars(select(IngestManifest)).all()}
    made = 0
    for rec in db.scalars(select(IncomingFile).order_by(IncomingFile.id.asc())).all():
        m = manifests.get(rec.file_path)
        if m is not None:
            fresh = bool(rec.mtime and m.generated_at and rec.mtime > m.generated_at.timestamp())
            if not fresh or m.status in ("accepted", "rejected"):
                continue
        ensure_draft(db, rec)
        made += 1
        if made >= limit:
            break
    return made


def enqueue(db: Session, *, scope: str = "all", target: Optional[str] = None, stage: str = "agent") -> IngestRequest:
    req = IngestRequest(
        scope=scope,
        target=target,
        stage=stage,
        status="queued",
        requested_at=datetime.utcnow(),
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req

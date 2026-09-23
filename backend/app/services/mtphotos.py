"""MT Photos HTTP 客户端。

照片墙只读接入：用 API Key 拉相册/文件/标签，用 24 小时 auth_code 取缩略图和原图。
auth_code 由本模块缓存并自动续期，调用方不用管。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.services.app_settings import read_all

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20
AUTH_TTL_SECONDS = 20 * 3600  # 官方 24h，提前 4h 换新
VIDEO_TYPES = {"mp4", "mov", "webm", "mkv", "m4v", "avi"}
MT_PATH_PREFIX = "mtphotos://"

OpenFn = Callable[[urllib.request.Request, float], Any]
_direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class MtPhotosError(ValueError):
    """MT Photos 配置错误或接口失败。"""


@dataclass
class MtAlbum:
    id: int
    name: str
    count: int = 0
    cover: Optional[str] = None


@dataclass
class MtFolder:
    id: int
    name: str
    path: str = ""
    count: int = 0
    subfolder_count: int = 0


@dataclass
class MtFile:
    id: int
    md5: str
    file_name: str = ""
    file_type: str = ""
    token_at: Optional[datetime] = None
    day: str = ""
    disk_path: str = ""
    folder_id: Optional[int] = None


@dataclass
class MtTag:
    id: int
    name: str


@dataclass
class _AuthCache:
    cache_key: str
    code: str
    until: float


_auth_lock = threading.Lock()
_auth_cache: Optional[_AuthCache] = None
_open: OpenFn = lambda req, timeout: _direct_opener.open(req, timeout=timeout)


def set_opener(fn: Optional[OpenFn]) -> None:
    """测试用：替换底层 HTTP。传 None 恢复直连（不走系统代理）。"""
    global _open
    if fn is None:
        _open = lambda req, timeout: _direct_opener.open(req, timeout=timeout)
    else:
        _open = fn


def reset_auth_cache() -> None:
    global _auth_cache
    with _auth_lock:
        _auth_cache = None


def normalize_base_url(raw: str) -> str:
    text = (raw or "").strip().rstrip("/")
    if not text:
        raise MtPhotosError("请先填写 MT Photos 地址")
    if "://" not in text:
        text = "http://" + text
    return text


def configured_from_db(db: Session) -> tuple[str, str]:
    settings = read_all(db).mtphotos
    if not settings.enabled:
        raise MtPhotosError("尚未启用 MT Photos")
    return normalize_base_url(settings.base_url), (settings.api_key or "").strip()


def is_configured(db: Session) -> bool:
    try:
        settings = read_all(db).mtphotos
    except Exception:  # noqa: BLE001
        return False
    return bool(settings.enabled and (settings.base_url or "").strip() and (settings.api_key or "").strip())


def album_source_path(album_id: int) -> str:
    return f"{MT_PATH_PREFIX}album/{int(album_id)}"


def folder_source_path(folder_id: int) -> str:
    return f"{MT_PATH_PREFIX}folder/{int(folder_id)}"


def photo_file_path(album_id: int, file_id: int, md5: str) -> str:
    return f"{MT_PATH_PREFIX}{int(album_id)}/{int(file_id)}/{md5}"


def parse_photo_path(path: str) -> Optional[tuple[int, int, str]]:
    raw = (path or "").strip()
    if not raw.startswith(MT_PATH_PREFIX):
        return None
    rest = raw[len(MT_PATH_PREFIX) :]
    parts = rest.split("/")
    if len(parts) != 3 or parts[0] == "album":
        return None
    try:
        return int(parts[0]), int(parts[1]), parts[2]
    except ValueError:
        return None


def parse_mt_source(
    source_path: str, external_id: Optional[str] = None
) -> tuple[str, int]:
    """解析绑定：('album'|'folder', id)。旧数据没有 kind 时按相册。"""
    raw = (source_path or "").strip()
    folder_prefix = f"{MT_PATH_PREFIX}folder/"
    album_prefix = f"{MT_PATH_PREFIX}album/"
    if raw.startswith(folder_prefix) and raw[len(folder_prefix) :].isdigit():
        return "folder", int(raw[len(folder_prefix) :])
    if raw.startswith(album_prefix) and raw[len(album_prefix) :].isdigit():
        return "album", int(raw[len(album_prefix) :])
    if external_id and str(external_id).strip().isdigit():
        kind = "folder" if "/folder/" in raw else "album"
        return kind, int(str(external_id).strip())
    raise MtPhotosError("无效的 MT Photos 绑定")


def parse_album_id(source_path: str, external_id: Optional[str] = None) -> int:
    kind, sid = parse_mt_source(source_path, external_id)
    if kind != "album":
        raise MtPhotosError("无效的 MT Photos 相册绑定")
    return sid


def media_kind_of(file_type: str, file_name: str) -> str:
    ext = (file_type or Path(file_name or "").suffix).lower().lstrip(".")
    return "video" if ext in VIDEO_TYPES else "image"


def _json_body(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise MtPhotosError(f"MT Photos 返回了无法解析的数据：{e}") from e


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    api_key: str = "",
    query: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, dict[str, str], bytes]:
    url = base_url.rstrip("/") + path
    if query:
        items = []
        for k, v in query.items():
            if v is None or v == "":
                continue
            items.append((k, str(v)))
        if items:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(items)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method.upper())
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("x-api-key", api_key)
    try:
        with _open(req, timeout) as resp:
            payload = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return getattr(resp, "status", 200) or 200, headers, payload
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        detail = ""
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict):
                detail = str(parsed.get("message") or parsed.get("error") or parsed.get("detail") or "")
        except Exception:  # noqa: BLE001
            detail = raw.decode("utf-8", errors="replace")[:200]
        raise MtPhotosError(detail or f"MT Photos 请求失败（HTTP {e.code}）") from e
    except urllib.error.URLError as e:
        raise MtPhotosError(f"无法连接 MT Photos：{e.reason}") from e
    except TimeoutError as e:
        raise MtPhotosError("连接 MT Photos 超时") from e


def _request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    api_key: str,
    query: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    _status, _headers, raw = _request(
        base_url, method, path, api_key=api_key, query=query, body=body, timeout=timeout
    )
    return _json_body(raw)


def ping(base_url: str, api_key: str) -> dict[str, Any]:
    url = normalize_base_url(base_url)
    key = (api_key or "").strip()
    if not key:
        raise MtPhotosError("请填写 API Key")
    info = _request_json(url, "GET", "/api-info", api_key=key) or {}
    albums = list_albums(url, key)
    return {
        "version": str(info.get("version") or ""),
        "build": str(info.get("build") or ""),
        "album_count": len(albums),
    }


def list_albums(base_url: str, api_key: str) -> list[MtAlbum]:
    data = _request_json(base_url, "GET", "/api-album", api_key=api_key) or []
    if not isinstance(data, list):
        return []
    out: list[MtAlbum] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            aid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        out.append(
            MtAlbum(
                id=aid,
                name=str(row.get("name") or f"相册 {aid}"),
                count=int(row.get("count") or 0),
                cover=str(row["cover"]) if row.get("cover") else None,
            )
        )
    return out


def _parse_folder_row(row: Any) -> Optional[MtFolder]:
    if not isinstance(row, dict):
        return None
    try:
        fid = int(row.get("id"))
    except (TypeError, ValueError):
        return None
    if fid <= 0:
        return None
    return MtFolder(
        id=fid,
        name=str(row.get("name") or f"文件夹 {fid}"),
        path=str(row.get("path") or ""),
        count=int(row.get("subFileNum") or row.get("count") or 0),
        subfolder_count=int(row.get("subFolderNum") or 0),
    )


def list_folders(
    base_url: str, api_key: str, parent_id: Optional[int] = None
) -> list[MtFolder]:
    if parent_id is None:
        data = _request_json(base_url, "GET", "/gateway/folders/root", api_key=api_key) or {}
    else:
        data = _request_json(
            base_url, "GET", f"/gateway/foldersV2/{int(parent_id)}", api_key=api_key
        ) or {}
    rows = data.get("folderList") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out: list[MtFolder] = []
    for row in rows:
        folder = _parse_folder_row(row)
        if folder is not None:
            out.append(folder)
    return out


def folder_info(base_url: str, api_key: str, folder_id: int) -> MtFolder:
    data = _request_json(
        base_url, "GET", f"/gateway/folderInfo/{int(folder_id)}", api_key=api_key
    )
    folder = _parse_folder_row(data if isinstance(data, dict) else {})
    if folder is None:
        raise MtPhotosError("找不到这个 MT Photos 文件夹")
    return folder


def list_folder_files(
    base_url: str, api_key: str, folder_id: int, *, recursive: bool = False
) -> list[MtFile]:
    """列文件夹文件。优先 foldersV2 的 fileList（带 fileName/tokenAt）；
    fileInIds / folderFiles 只有 id+MD5，名字会变成 999.jpg，对不上 sidecar。"""
    bucket: dict[int, MtFile] = {}
    seen_dirs: set[int] = set()
    queue = [int(folder_id)]
    while queue:
        fid = queue.pop(0)
        if fid in seen_dirs:
            continue
        seen_dirs.add(fid)
        try:
            info = _request_json(
                base_url, "GET", f"/gateway/foldersV2/{fid}", api_key=api_key
            )
        except MtPhotosError as e:
            logger.info("foldersV2 失败 folder=%s: %s", fid, e)
            continue
        if not isinstance(info, dict):
            continue
        for item in info.get("fileList") or []:
            _merge_file(bucket, "", item)
        if recursive:
            for child in info.get("folderList") or []:
                if isinstance(child, dict) and child.get("id") is not None:
                    try:
                        queue.append(int(child["id"]))
                    except (TypeError, ValueError):
                        continue
        if not recursive:
            break
    if not bucket:
        fid = int(folder_id)
        try:
            query = {"withSub": "1"} if recursive else None
            raw = _request_json(
                base_url,
                "GET",
                f"/gateway/folderFiles/{fid}",
                api_key=api_key,
                query=query,
            )
            for day, row in _iter_file_rows(raw):
                _merge_file(bucket, day, row)
        except MtPhotosError as e:
            logger.info("folderFiles 失败 folder=%s: %s", fid, e)
    logger.info("MT 文件夹 %s 解析到 %s 个文件", folder_id, len(bucket))
    return list(bucket.values())


def _coerce_file_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if n > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if isinstance(value, dict):
        for key in ("id", "fileId", "file_id"):
            if value.get(key) is None:
                continue
            fid = _coerce_file_id(value.get(key))
            if fid is not None:
                return fid
    return None


def _coerce_md5(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    for key in ("MD5", "md5", "hash", "fileMD5", "file_md5"):
        text = str(row.get(key) or "").strip()
        if text:
            return text
    return ""


def _iter_file_rows(payload: Any, *, _day: str = "") -> list[tuple[str, dict[str, Any]]]:
    """把时间线、扁平列表、id 数组收成 [(day, file_row)]。"""
    if payload is None:
        return []
    if isinstance(payload, bool):
        return []
    if isinstance(payload, (int, float)):
        fid = _coerce_file_id(payload)
        return [(_day, {"id": fid})] if fid else []
    if isinstance(payload, list):
        rows: list[tuple[str, dict[str, Any]]] = []
        for item in payload:
            rows.extend(_iter_file_rows(item, _day=_day))
        return rows
    if not isinstance(payload, dict):
        return []
    has_file_keys = any(
        payload.get(k) not in (None, "", [], {})
        for k in ("id", "fileId", "MD5", "md5", "files", "fileList")
    )
    if not has_file_keys:
        for wrap in ("data", "result", "payload", "list"):
            inner = payload.get(wrap)
            if isinstance(inner, (list, dict)):
                return _iter_file_rows(inner, _day=_day)
    day = str(payload.get("date") or payload.get("day") or _day or "")
    nested = payload.get("files") if isinstance(payload.get("files"), list) else None
    if nested is None and isinstance(payload.get("fileList"), list):
        nested = payload.get("fileList")
    if nested is not None and _coerce_file_id(payload) is None:
        rows = []
        for item in nested:
            rows.extend(_iter_file_rows(item, _day=day))
        return rows
    if _coerce_file_id(payload) is not None or _coerce_md5(payload):
        return [(day, payload)]
    groups = payload.get("result") or payload.get("list")
    if isinstance(groups, list):
        return _iter_file_rows(groups, _day=day)
    if isinstance(groups, dict):
        rows = []
        for key, value in groups.items():
            rows.extend(_iter_file_rows(value, _day=str(key)))
        return rows
    return []


def _merge_file(bucket: dict[int, MtFile], day: str, row: Any) -> None:
    fid = _coerce_file_id(row)
    if fid is None:
        return
    md5 = _coerce_md5(row)
    name = ""
    ftype = ""
    token = None
    disk_path = ""
    folder_id = None
    if isinstance(row, dict):
        name = str(row.get("fileName") or row.get("name") or "").strip()
        ftype = str(row.get("fileType") or "").strip()
        token = _parse_token_at(row.get("tokenAt") or row.get("token_at"))
        disk_path = str(row.get("filePath") or row.get("file_path") or "").strip()
        try:
            folder_id = int(row["folderId"]) if row.get("folderId") is not None else None
        except (TypeError, ValueError):
            folder_id = None
    token = token or _parse_token_at(day)
    cur = bucket.get(fid)
    if cur is None:
        bucket[fid] = MtFile(
            id=fid,
            md5=md5,
            file_name=name,
            file_type=ftype,
            token_at=token,
            day=day_key(token) or _normalize_day(day),
            disk_path=disk_path,
            folder_id=folder_id,
        )
        return
    if md5 and not cur.md5:
        cur.md5 = md5
    if name and not cur.file_name:
        cur.file_name = name
    if ftype and not cur.file_type:
        cur.file_type = ftype
    if token and cur.token_at is None:
        cur.token_at = token
        cur.day = day_key(token) or cur.day
    if day and not cur.day:
        cur.day = _normalize_day(day)
    if disk_path and not cur.disk_path:
        cur.disk_path = disk_path
    if folder_id is not None and cur.folder_id is None:
        cur.folder_id = folder_id


def list_album_files(base_url: str, api_key: str, album_id: int) -> list[MtFile]:
    """相册文件：先用 filesFlat（升级前能用的），再用相册详情和 filesV2 补齐 id/日期。"""
    bucket: dict[int, MtFile] = {}
    aid = int(album_id)

    try:
        flat = _request_json(base_url, "GET", f"/api-album/filesFlat/{aid}", api_key=api_key)
        for day, row in _iter_file_rows(flat):
            _merge_file(bucket, day, row)
    except MtPhotosError as e:
        logger.info("filesFlat 失败 album=%s: %s", aid, e)

    if not bucket:
        try:
            info = _request_json(base_url, "GET", f"/api-album/{aid}", api_key=api_key)
            raw = info.get("files") if isinstance(info, dict) else None
            if not raw and isinstance(info, dict):
                raw = info.get("auto_files")
            for item in raw or []:
                _merge_file(bucket, "", item)
        except MtPhotosError as e:
            logger.info("相册详情失败 album=%s: %s", aid, e)

    try:
        v2 = _request_json(
            base_url,
            "GET",
            f"/api-album/filesV2/{aid}",
            api_key=api_key,
            query={"listVer": "v2"},
        )
        for day, row in _iter_file_rows(v2):
            _merge_file(bucket, day, row)
    except MtPhotosError as e:
        logger.info("filesV2 失败 album=%s: %s", aid, e)

    if not bucket:
        try:
            v2 = _request_json(base_url, "GET", f"/api-album/filesV2/{aid}", api_key=api_key)
            for day, row in _iter_file_rows(v2):
                _merge_file(bucket, day, row)
        except MtPhotosError as e:
            logger.info("filesV2(无 listVer) 失败 album=%s: %s", aid, e)

    logger.info("MT 相册 %s 解析到 %s 个文件", aid, len(bucket))
    return list(bucket.values())


def _normalize_day(raw: str) -> str:
    parsed = _parse_token_at(raw)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d")
    text = (raw or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    return ""


def day_key(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def _parse_token_at(raw: Any) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return datetime.utcfromtimestamp(ts)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return _parse_token_at(int(text))
        except ValueError:
            return None
    text = text.replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    text = re.sub(r"[+-]\d{2}:\d{2}$", "", text).strip()
    for fmt, size in (
        ("%Y-%m-%d %H:%M:%S.%f", 26),
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
        ("%Y年%m月%d日", 11),
    ):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def files_by_ids(
    base_url: str,
    api_key: str,
    ids: list[int],
    *,
    album_id: Optional[int] = None,
) -> dict[int, MtFile]:
    out: dict[int, MtFile] = {}
    chunk_size = 100
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        body: dict[str, Any] = {"ids": chunk}
        if album_id is not None:
            body["albumId"] = int(album_id)
        data = _request_json(base_url, "POST", "/gateway/fileInIds", api_key=api_key, body=body)
        for day, row in _iter_file_rows(data):
            fid = _coerce_file_id(row)
            if fid is None:
                continue
            md5 = _coerce_md5(row)
            name = str(row.get("fileName") or row.get("name") or "").strip() if isinstance(row, dict) else ""
            ftype = str(row.get("fileType") or "").strip() if isinstance(row, dict) else ""
            token = None
            disk_path = ""
            folder_id = None
            if isinstance(row, dict):
                token = _parse_token_at(row.get("tokenAt") or row.get("token_at"))
                disk_path = str(row.get("filePath") or row.get("file_path") or "").strip()
                try:
                    folder_id = int(row["folderId"]) if row.get("folderId") is not None else None
                except (TypeError, ValueError):
                    folder_id = None
            token = token or _parse_token_at(day)
            prev = out.get(fid)
            out[fid] = MtFile(
                id=fid,
                md5=md5 or (prev.md5 if prev else ""),
                file_name=name or (prev.file_name if prev else ""),
                file_type=ftype or (prev.file_type if prev else ""),
                token_at=token or (prev.token_at if prev else None),
                day=day_key(token) or _normalize_day(day) or (prev.day if prev else ""),
                disk_path=disk_path or (prev.disk_path if prev else ""),
                folder_id=folder_id if folder_id is not None else (prev.folder_id if prev else None),
            )
    return out


def list_tags(base_url: str, api_key: str) -> list[MtTag]:
    data = _request_json(base_url, "GET", "/api-tag", api_key=api_key, query={"type": "all"}) or []
    if not isinstance(data, list):
        return []
    out: list[MtTag] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name or row.get("is_hide"):
            continue
        try:
            tid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        out.append(MtTag(id=tid, name=name))
    return out


def file_tags(base_url: str, api_key: str, file_id: int) -> list[str]:
    data = _request_json(base_url, "GET", f"/gateway/fileTags/{int(file_id)}", api_key=api_key) or []
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for row in data:
        if isinstance(row, dict):
            name = str(row.get("name") or "").strip()
        else:
            name = str(row).strip()
        if name and name not in names:
            names.append(name)
    return names


def _as_tag_names(raw: Any) -> list[str]:
    names: list[str] = []
    if raw is None or raw is False:
        return names
    if isinstance(raw, str):
        raw = [part.strip() for part in re.split(r"[,，、|/]+", raw) if part.strip()]
    if not isinstance(raw, list):
        return names
    for item in raw:
        if isinstance(item, dict):
            name = str(
                item.get("name")
                or item.get("tag")
                or item.get("label")
                or item.get("value")
                or item.get("text")
                or ""
            ).strip()
        else:
            name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return names


def parse_llm_insight(payload: Any) -> tuple[str, list[str]]:
    """MT Photos 实际返回 { insight: { description, score, ... }, tags: ["标签"] }。"""
    data = payload
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        for wrap in ("data", "result", "payload"):
            inner = data.get(wrap)
            if isinstance(inner, dict) and ("insight" in inner or "tags" in inner):
                data = inner
                break
    if not isinstance(data, dict):
        return "", []
    insight = data.get("insight")
    caption = ""
    if isinstance(insight, str):
        caption = insight.strip()
    elif isinstance(insight, dict):
        raw = (
            insight.get("description")
            or insight.get("desc")
            or insight.get("text")
            or insight.get("content")
            or ""
        )
        if isinstance(raw, dict):
            raw = raw.get("zh") or raw.get("text") or raw.get("content") or ""
        caption = str(raw or "").strip()
    if not caption:
        raw = (
            data.get("description")
            or data.get("desc")
            or data.get("caption")
            or data.get("caption_zh")
            or ""
        )
        if isinstance(raw, dict):
            raw = raw.get("zh") or raw.get("text") or ""
        caption = str(raw or "").strip()
    tags: list[str] = []
    for raw in (data.get("tags"), data.get("llmTags"), data.get("llm_tags")):
        for name in _as_tag_names(raw):
            if name not in tags:
                tags.append(name)
    if isinstance(insight, dict):
        for name in _as_tag_names(insight.get("tags")):
            if name not in tags:
                tags.append(name)
    return caption, tags


def file_llm_insight(
    base_url: str, api_key: str, file_id: int, md5: str = ""
) -> tuple[str, list[str]]:
    body: dict[str, Any] = {"fileId": int(file_id)}
    if md5:
        body["fileMD5"] = md5
    data = _request_json(
        base_url, "POST", "/gateway/fileLlmInsight", api_key=api_key, body=body
    )
    caption, tags = parse_llm_insight(data)
    if not caption and not tags and data:
        keys = list(data)[:12] if isinstance(data, dict) else type(data).__name__
        logger.info("LLM insight 未解析到描述/标签 file=%s keys=%s", file_id, keys)
    return caption, tags


def list_llm_tags(base_url: str, api_key: str) -> list[MtTag]:
    try:
        data = _request_json(base_url, "POST", "/gateway/llmTags", api_key=api_key, body={})
    except MtPhotosError:
        return []
    rows: list[Any]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("tags") or data.get("result") or data.get("list") or []
    else:
        rows = []
    out: list[MtTag] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            name = str(row.get("name") or row.get("tag") or row.get("label") or "").strip()
            try:
                tid = int(row.get("id") or 0)
            except (TypeError, ValueError):
                tid = 0
        else:
            name = str(row).strip()
            tid = 0
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(MtTag(id=tid, name=name))
    return out


def _file_ids_from_payload(payload: Any) -> set[int]:
    ids: set[int] = set()
    for _day, row in _iter_file_rows(payload):
        fid = _coerce_file_id(row)
        if fid:
            ids.add(fid)
    return ids


def search_files_by_tag_ids(
    base_url: str,
    api_key: str,
    tag_ids: list[int],
) -> set[int]:
    if not tag_ids:
        return set()
    data = _request_json(
        base_url,
        "POST",
        "/gateway/searchV2",
        api_key=api_key,
        body={"tags": tag_ids, "tagUseOr": False},
    )
    return _file_ids_from_payload(data)


def search_files_by_llm_tag(base_url: str, api_key: str, tag: str) -> set[int]:
    name = (tag or "").strip()
    if not name:
        return set()
    last_error: Optional[MtPhotosError] = None
    for body in (
        {"tag": name},
        {"name": name},
        {"llmTag": name},
        {"tags": [name]},
    ):
        try:
            data = _request_json(
                base_url, "POST", "/gateway/llmTagFiles", api_key=api_key, body=body
            )
        except MtPhotosError as e:
            last_error = e
            continue
        ids = _file_ids_from_payload(data)
        if ids:
            return ids
    if last_error:
        logger.info("按 LLM 标签查文件失败 tag=%s: %s", name, last_error)
    return set()


def auth_code(base_url: str, api_key: str) -> str:
    global _auth_cache
    cache_key = f"{base_url}|{api_key}"
    now = time.time()
    with _auth_lock:
        if _auth_cache and _auth_cache.cache_key == cache_key and _auth_cache.until > now:
            return _auth_cache.code
    data = _request_json(
        base_url, "POST", "/auth/auth_code", api_key=api_key, body={"api_key": api_key}
    ) or {}
    code = str(data.get("auth_code") or "").strip()
    if not code:
        raise MtPhotosError("未能获取 MT Photos 看图授权码")
    with _auth_lock:
        _auth_cache = _AuthCache(cache_key=cache_key, code=code, until=now + AUTH_TTL_SECONDS)
    return code


def fetch_bytes(
    base_url: str,
    api_key: str,
    path: str,
    *,
    query: Optional[dict[str, Any]] = None,
    timeout: float = 40,
) -> tuple[bytes, str]:
    code = auth_code(base_url, api_key)
    q = dict(query or {})
    q["auth_code"] = code
    try:
        _status, headers, raw = _request(
            base_url,
            "GET",
            path,
            api_key=api_key,
            query=q,
            timeout=timeout,
        )
    except MtPhotosError:
        reset_auth_cache()
        code = auth_code(base_url, api_key)
        q["auth_code"] = code
        _status, headers, raw = _request(
            base_url,
            "GET",
            path,
            api_key=api_key,
            query=q,
            timeout=timeout,
        )
    ctype = headers.get("content-type") or "application/octet-stream"
    return raw, ctype.split(";")[0].strip()


def fetch_thumb(base_url: str, api_key: str, md5: str, *, is_video: bool) -> tuple[bytes, str]:
    kind = "poster" if is_video else "h220"
    try:
        return fetch_bytes(base_url, api_key, f"/gateway/{kind}/{md5}")
    except MtPhotosError:
        if kind != "h220":
            return fetch_bytes(base_url, api_key, f"/gateway/h220/{md5}")
        raise


def fetch_original(
    base_url: str,
    api_key: str,
    file_id: int,
    md5: str,
    *,
    is_video: bool,
) -> tuple[bytes, str]:
    """灯箱用图：一律原文件。"""
    path = f"/gateway/file/{int(file_id)}/{md5}"
    return fetch_bytes(base_url, api_key, path, query={"type": "ori"})

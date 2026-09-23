"""照片墙只读接入 MT Photos：绑定相册、浏览、标签、拒绝本地管理。"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="mtphotos_wall_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models.artist import Artist  # noqa: E402
from app.models.photo import Photo, PhotoSource  # noqa: E402
from app.services import app_settings, mtphotos, photo_service  # noqa: E402
from app.services.mtphotos import MtFile  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

settings.ensure_directories()
init_db()
Base.metadata.create_all(bind=engine)

PASS = 0
FAIL = 0
AUTH_CALLS = 0
JPEG = b"\xff\xd8\xff\xdb" + b"thumb" * 20
FANS_ROOT = _tmp / "ins" / "fans"
BLOGGER_A = FANS_ROOT / "bloggerA"
BLOGGER_B = FANS_ROOT / "bloggerB"


def _write_sidecar(folder: Path, stem: str, caption: str, media: list[str]):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.txt").write_text(
        "发布时间：2024-08-01 12:00:00\n"
        f"博文原文：{caption}\n"
        "媒体文件列表：\n" + "\n".join(media) + "\n",
        encoding="utf-8",
    )
    for name in media:
        (folder / name).write_bytes(JPEG)


_write_sidecar(
    BLOGGER_A,
    "2024-08-01 12-00-00_UTC",
    "来自博主A的舞台图",
    ["2024-08-01 12-00-00_UTC.jpg", "2024-08-01 12-00-00_UTC_1.jpg"],
)
_write_sidecar(
    BLOGGER_B,
    "2024-08-01 12-00-00_UTC",
    "来自博主B的街拍",
    ["2024-08-01 12-00-00_UTC.jpg"],
)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


class FakeMt(BaseHTTPRequestHandler):
    def log_message(self, _fmt, *_args):
        return

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _json(self, payload, code=200):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _bytes(self, payload, ctype="image/jpeg"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/api-info":
            self._json({"version": "1.56.2", "build": "test"})
            return
        if path == "/api-album":
            self._json(
                [
                    {"id": 7, "name": "IU 照片墙", "count": 2, "cover": "aaa"},
                    {"id": 8, "name": "无 MD5 时间线", "count": 2, "cover": "ccc"},
                ]
            )
            return
        if path == "/api-album/7":
            self._json({"id": 7, "name": "IU 照片墙", "files": [101, 102], "count": 2})
            return
        if path == "/api-album/8":
            self._json({"id": 8, "name": "无 MD5 时间线", "files": [201, 202], "count": 2})
            return
        if path.startswith("/api-album/filesV2/"):
            album = path.rsplit("/", 1)[-1]
            if album == "8":
                self._json(
                    {
                        "result": [
                            {"date": "2024-07-01", "files": [{"id": 201}, {"id": 202}]},
                        ]
                    }
                )
                return
            self._json(
                {
                    "result": [
                        {"date": "2024-06-01", "files": [{"id": 102, "MD5": "bbb"}]},
                        {"date": "2024-05-01", "files": [{"id": 101, "MD5": "aaa"}]},
                    ],
                    "totalCount": 2,
                }
            )
            return
        if path == "/api-album/filesFlat/7":
            self._json([{"id": 101, "MD5": "aaa"}, {"id": 102, "MD5": "bbb"}])
            return
        if path == "/api-album/filesFlat/8":
            self._json([{"id": 201, "MD5": "ccc"}, {"id": 202, "MD5": "ddd"}])
            return
        if path == "/api-tag":
            self._json([{"id": 1, "name": "舞台", "is_hide": False}, {"id": 2, "name": "机场"}])
            return
        if path == "/gateway/fileTags/101":
            self._json([{"id": 1, "name": "舞台"}])
            return
        if path == "/gateway/fileTags/102":
            self._json([])
            return
        if path.startswith("/gateway/h220/") or path.startswith("/gateway/poster/"):
            if not qs.get("auth_code"):
                self.send_error(401)
                return
            self._bytes(JPEG)
            return
        if path.startswith("/gateway/file/"):
            if not qs.get("auth_code"):
                self.send_error(401)
                return
            self._bytes(b"original-bytes", "image/jpeg")
            return
        if path == "/gateway/folders/root":
            self._json(
                {
                    "path": str(FANS_ROOT),
                    "folderList": [
                        {
                            "id": 20,
                            "name": "fans",
                            "path": str(FANS_ROOT),
                            "subFileNum": 3,
                            "subFolderNum": 2,
                        }
                    ],
                }
            )
            return
        if path == "/gateway/folderInfo/20":
            self._json(
                {
                    "id": 20,
                    "name": "fans",
                    "path": str(FANS_ROOT),
                    "subFileNum": 3,
                    "subFolderNum": 2,
                }
            )
            return
        if path == "/gateway/foldersV2/20":
            self._json(
                {
                    "path": str(FANS_ROOT),
                    "folderList": [
                        {"id": 21, "name": "bloggerA", "subFileNum": 2, "subFolderNum": 0},
                        {"id": 22, "name": "bloggerB", "subFileNum": 1, "subFolderNum": 0},
                    ],
                    "fileList": [
                        {
                            "id": 301,
                            "MD5": "m301",
                            "fileName": "2024-08-01 12-00-00_UTC.jpg",
                            "fileType": "JPEG",
                            "tokenAt": "2024-08-01T12:00:00.000Z",
                        },
                        {
                            "id": 302,
                            "MD5": "m302",
                            "fileName": "2024-08-01 12-00-00_UTC_1.jpg",
                            "fileType": "JPEG",
                            "tokenAt": "2024-08-01T12:00:00.000Z",
                        },
                        {
                            "id": 401,
                            "MD5": "m401",
                            "fileName": "2024-08-01 12-00-00_UTC.jpg",
                            "fileType": "JPEG",
                            "tokenAt": "2024-08-01T12:00:00.000Z",
                        },
                    ],
                }
            )
            return
        if path.startswith("/gateway/foldersV2/"):
            self._json({"path": "", "folderList": [], "fileList": []})
            return
        if path in ("/gateway/folderSubFile/20", "/gateway/folderFiles/20"):
            self._json(
                [
                    {"id": 301, "MD5": "m301", "fileName": "2024-08-01 12-00-00_UTC.jpg"},
                    {"id": 302, "MD5": "m302", "fileName": "2024-08-01 12-00-00_UTC_1.jpg"},
                    {"id": 401, "MD5": "m401", "fileName": "2024-08-01 12-00-00_UTC.jpg"},
                ]
            )
            return
        self.send_error(404)

    def do_POST(self):
        global AUTH_CALLS
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()
        if path == "/auth/auth_code":
            AUTH_CALLS += 1
            self._json({"auth_code": "code-1"})
            return
        if path == "/gateway/fileInIds":
            ids = body.get("ids") or []
            files = []
            if 101 in ids:
                files.append(
                    {
                        "id": 101,
                        "MD5": "aaa",
                        "fileName": "stage.jpg",
                        "fileType": "JPEG",
                        "tokenAt": "2024-05-01T12:00:00.000Z",
                    }
                )
            if 102 in ids:
                files.append(
                    {
                        "id": 102,
                        "MD5": "bbb",
                        "fileName": "airport.jpg",
                        "fileType": "JPEG",
                        "tokenAt": "2024-06-01 12:00:00",
                    }
                )
            if 301 in ids:
                files.append(
                    {
                        "id": 301,
                        "MD5": "m301",
                        "fileName": "2024-08-01 12-00-00_UTC.jpg",
                        "fileType": "JPEG",
                        "filePath": str(BLOGGER_A / "2024-08-01 12-00-00_UTC.jpg"),
                        "tokenAt": "2024-08-01T12:00:00.000Z",
                    }
                )
            if 302 in ids:
                files.append(
                    {
                        "id": 302,
                        "MD5": "m302",
                        "fileName": "2024-08-01 12-00-00_UTC_1.jpg",
                        "fileType": "JPEG",
                        "filePath": str(BLOGGER_A / "2024-08-01 12-00-00_UTC_1.jpg"),
                        "tokenAt": "2024-08-01T12:00:00.000Z",
                    }
                )
            if 401 in ids:
                files.append(
                    {
                        "id": 401,
                        "MD5": "m401",
                        "fileName": "2024-08-01 12-00-00_UTC.jpg",
                        "fileType": "JPEG",
                        "filePath": str(BLOGGER_B / "2024-08-01 12-00-00_UTC.jpg"),
                        "tokenAt": "2024-08-01T12:00:00.000Z",
                    }
                )
            self._json([{"date": "2024-08-01", "files": files}])
            return
        if path == "/gateway/searchV2":
            tags = body.get("tags") or []
            files = [{"id": 101, "MD5": "aaa"}] if 1 in tags else []
            self._json({"result": [{"date": "2024-05-01", "files": files}]})
            return
        if path == "/gateway/fileLlmInsight":
            fid = body.get("fileId")
            if fid == 101:
                self._json(
                    {
                        "insight": {
                            "description": "舞台上的白色西装",
                            "score": 0.9,
                            "chat_model": "test",
                        },
                        "tags": ["白色西装"],
                    }
                )
            else:
                self._json({})
            return
        if path == "/gateway/llmTags":
            self._json([{"name": "白色西装"}])
            return
        if path == "/gateway/llmTagFiles":
            tag = body.get("tag") or body.get("name") or ""
            if tag == "白色西装":
                self._json(
                    {"result": [{"date": "2024-05-01", "files": [{"id": 101, "MD5": "aaa"}]}]}
                )
            else:
                self._json({"result": []})
            return
        self.send_error(404)


httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeMt)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
mtphotos.reset_auth_cache()

db = SessionLocal()
empty = app_settings.read_all(db)
check("设置里有 mtphotos 分区", empty.mtphotos.enabled is False)

app_settings.write_sections(
    db,
    {"mtphotos": {"enabled": True, "base_url": base_url, "api_key": "sk_live_test"}},
)
info = mtphotos.ping(base_url, "sk_live_test")
check("能连上并看到相册", info["album_count"] >= 1 and info["version"] == "1.56.2")

artist = Artist(name="IU")
db.add(artist)
db.commit()
db.refresh(artist)
aid = artist.id

source = photo_service.bind_source(
    db,
    owner_type="artist",
    owner_id=aid,
    section="wall",
    provider="mtphotos",
    external_id="7",
)
check("照片墙能绑相册", source.provider == "mtphotos" and source.external_id == "7")
check("首次同步写入索引", source.last_scan_files == 2, str(source.last_scan_files))

scan_wall = photo_service.scan_source(db, source)
check("MT 来源可以手动同步", scan_wall.files_seen == 2, str(scan_wall.files_seen))

feed = photo_service.owner_feed(
    db, owner_type="artist", owner_id=aid, section="wall", page=1, page_size=24
)
check("照片墙只读", feed["readonly"] is True)
check("照片墙两张", feed["photo_count"] == 2, str(feed["photo_count"]))
check("按单张排", all(it["kind"] == "photo" for it in feed["items"]))
photos = [it["photos"][0] for it in feed["items"]]
check("带 provider", all(p.provider == "mtphotos" for p in photos))
names = {p.file_name for p in photos}
check("有文件名", names == {"stage.jpg", "airport.jpg"}, str(names))
stage = next(p for p in photos if p.file_name == "stage.jpg")
check(
    "ISO 拍摄时间能解析",
    mtphotos._parse_token_at("2024-05-01T12:00:00.000Z") is not None,
)
check("时间线按新到旧", [d["date"] for d in feed["timeline"]][:2] == ["2024-06-01", "2024-05-01"], str(feed["timeline"]))
check("墙按拍摄日倒序", photos[0].file_name == "airport.jpg", photos[0].file_name)
named = photo_service.owner_feed(
    db,
    owner_type="artist",
    owner_id=aid,
    section="wall",
    page=1,
    page_size=24,
    sort_by="name",
    sort_dir="asc",
)
check(
    "文件名升序",
    named["items"][0]["photos"][0].file_name == "airport.jpg"
    and named["items"][1]["photos"][0].file_name == "stage.jpg",
    str([it["photos"][0].file_name for it in named["items"]]),
)
check(
    "灯箱能看到用户标签和 LLM 标签",
    set((stage.analysis or {}).get("tags") or []) >= {"舞台", "白色西装"},
    str(stage.analysis),
)
check("灯箱能看到 LLM 描述", (stage.analysis or {}).get("caption_zh") == "舞台上的白色西装", str(stage.analysis))

options = photo_service.owner_filter_options(db, owner_type="artist", owner_id=aid, section="wall")
check(
    "标签筛选项来自 MT",
    {o["tag"] for o in options["options"]} >= {"舞台", "机场"},
    str(options),
)

tagged = photo_service.owner_feed(
    db,
    owner_type="artist",
    owner_id=aid,
    section="wall",
    page=1,
    page_size=24,
    tags=["舞台"],
)
check("按标签只留下舞台", tagged["photo_count"] == 1, str(tagged["photo_count"]))
llm_hit = photo_service.owner_feed(
    db,
    owner_type="artist",
    owner_id=aid,
    section="wall",
    page=1,
    page_size=24,
    tags=["白色西装"],
)
check("LLM 标签筛选生效", llm_hit["photo_count"] == 1, str(llm_hit["photo_count"]))
cap, names = mtphotos.parse_llm_insight({"desc": "侧光", "llmTags": "红衣,舞台"})
check("逗号 LLM 标签能拆开", cap == "侧光" and names == ["红衣", "舞台"], str((cap, names)))
cap2, names2 = mtphotos.parse_llm_insight(
    {"insight": {"description": "侧光人像"}, "tags": ["红衣", "舞台"]}
)
check(
    "MT 真实结构 insight+tags",
    cap2 == "侧光人像" and names2 == ["红衣", "舞台"],
    str((cap2, names2)),
)
oneday = photo_service.owner_feed(
    db,
    owner_type="artist",
    owner_id=aid,
    section="wall",
    page=1,
    page_size=24,
    on_date="2024-05-01",
)
check("跳到 5 月 1 日只剩当天", oneday["photo_count"] == 1 and oneday["items"][0]["photos"][0].file_name == "stage.jpg")

thumb = photo_service.fetch_mtphotos_thumb(db, stage)
check("能取缩略图", thumb == JPEG, str(len(thumb)))
original, ctype = photo_service.fetch_mtphotos_original(db, stage)
check("能取预览图", original == b"original-bytes" and "jpeg" in ctype, ctype)
check("auth_code 会缓存", AUTH_CALLS == 1, str(AUTH_CALLS))

try:
    photo_service.add_uploaded_photo(db, source, original_name="x.jpg", data=b"abc")
    check("拒绝上传到 MT 墙", False)
except photo_service.PhotoServiceError as e:
    check("拒绝上传到 MT 墙", "MT Photos" in str(e))

loaded = db.scalar(select(Photo).options(selectinload(Photo.source)).where(Photo.id == stage.id))
try:
    photo_service.delete_photo(db, loaded)
    check("拒绝删除 MT 照片", False)
except photo_service.PhotoServiceError as e:
    check("拒绝删除 MT 照片", "MT Photos" in str(e))

rows = photo_service.list_sources(db, "artist", aid)
check("来源列表能看到绑定", rows[0][0].provider == "mtphotos" and rows[0][1] == 2)

slim = mtphotos.list_album_files(base_url, "sk_live_test", 8)
check("时间线没有 MD5 时仍能靠 filesFlat 拿到图", {f.id for f in slim} == {201, 202}, str(slim))
check("MD5 从扁平列表补上", {f.md5 for f in slim} == {"ccc", "ddd"}, str({f.md5 for f in slim}))

official = photo_service.bind_source(
    db,
    owner_type="artist",
    owner_id=aid,
    section="official",
    provider="mtphotos",
    external_id="8",
    mt_kind="album",
)
check("官方能绑 MT 相册", official.section == "official" and official.provider == "mtphotos")

fan = photo_service.bind_source(
    db,
    owner_type="artist",
    owner_id=aid,
    section="fan",
    provider="mtphotos",
    external_id="20",
    mt_kind="folder",
    recursive=True,
)
listed = mtphotos.list_folder_files(base_url, "sk_live_test", 20, recursive=True)
check(
    "文件夹列表带真实文件名而不是 id.jpg",
    all("UTC" in (f.file_name or "") for f in listed) and len(listed) == 3,
    str([(f.id, f.file_name) for f in listed]),
)
check("粉丝能绑 MT 文件夹", fan.folder_path.endswith("/folder/20"), fan.folder_path)
check("粉丝同步到 3 张", fan.last_scan_files == 3, str(fan.last_scan_files))
check("两个博主拆成 2 帖", fan.last_scan_posts == 2, str(fan.last_scan_posts))

fan_feed = photo_service.owner_feed(
    db, owner_type="artist", owner_id=aid, section="fan", page=1, page_size=24
)
check("粉丝流按帖展示", all(it["kind"] == "post" for it in fan_feed["items"]), str([it["kind"] for it in fan_feed["items"]]))
check("粉丝 2 个帖", fan_feed["total"] == 2, str(fan_feed["total"]))
caps = {it["caption"] for it in fan_feed["items"]}
check(
    "混博主文案不串",
    caps == {"来自博主A的舞台图", "来自博主B的街拍"},
    str(caps),
)
keys = {it["post_key"] for it in fan_feed["items"]}
check("子目录写进 post_key", any("bloggerA" in (k or "") for k in keys) and any("bloggerB" in (k or "") for k in keys), str(keys))
post_a = next(it for it in fan_feed["items"] if it["caption"] == "来自博主A的舞台图")
check("博主A 帖有 2 张", len(post_a["photos"]) == 2, str(len(post_a["photos"])))

try:
    photo_service.bind_source(
        db,
        owner_type="artist",
        owner_id=aid,
        section="wall",
        provider="mtphotos",
        external_id="20",
        mt_kind="folder",
    )
    check("照片墙不能绑文件夹", False)
except photo_service.PhotoServiceError as e:
    check("照片墙不能绑文件夹", "相册" in str(e), str(e))

roots = mtphotos.list_folders(base_url, "sk_live_test")
check("能列出 MT 根文件夹", [f.id for f in roots] == [20], str(roots))

mapped = photo_service.rewrite_mt_disk_path(
    r"Y:\photo\ins\heejin", r"Y:\photo", "/data/mt-photos"
)
check(
    "Windows 路径能映射到容器",
    mapped == "/data/mt-photos/ins/heejin",
    mapped,
)
mapped2 = photo_service.rewrite_mt_disk_path(
    "/volume1/photo/ins/heejin", "/volume1/photo", "/data/mt-photos"
)
check("NAS 路径能映射到容器", mapped2 == "/data/mt-photos/ins/heejin", mapped2)
mapped3 = photo_service.rewrite_mt_disk_path(
    "/photo/ins/heejin", "/home/idol/photo", "/data/mt-photos"
)
check(
    "MT 容器 /photo 即使前缀填错也能映射",
    mapped3 == "/data/mt-photos/ins/heejin",
    mapped3,
)
mapped4 = photo_service.rewrite_mt_disk_path("/photo", "/home/idol/photo", "/data/mt-photos")
check("MT 根目录 /photo 映射到挂载点", mapped4 == "/data/mt-photos", mapped4)

named_only = [
    MtFile(id=1, md5="a", file_name="2024-01-01 10-00-00_UTC.jpg"),
    MtFile(id=2, md5="b", file_name="2024-01-01 10-00-00_UTC_1.jpg"),
    MtFile(id=3, md5="c", file_name="2024-02-01 11-00-00_UTC.jpg"),
]
meta = photo_service._apply_post_meta(named_only, "official", None, True)
check("无磁盘也能按文件名成帖", len({m.post_key for m in meta.values()}) == 2, str(meta))
check("多图帖仍一组", meta[1].post_key == meta[2].post_key and meta[1].position_in_post == 0)
check("单图 Instagram 文件也成帖", bool(meta[3].post_key), meta[3].post_key)
check(
    "官方按文件名日期而不是拍摄 EXIF",
    meta[1].published_at is not None and meta[1].published_at.month == 1,
    str(meta[1].published_at),
)

x_files = [
    MtFile(id=11, md5="x1", file_name="2026-07-11 2075689223316004968 2075689183453360128.jpg"),
    MtFile(id=12, md5="x2", file_name="2026-07-11 2075689223316004968 2075689183453249536.jpg"),
    MtFile(id=13, md5="x3", file_name="2026-07-08 2074561585079849124 2074561578792615936.jpg"),
]
x_meta = photo_service._apply_post_meta(x_files, "fan", None, True)
check(
    "Twitter/X 同一推文成一帖",
    x_meta[11].post_key == x_meta[12].post_key and x_meta[11].post_key != x_meta[13].post_key,
    str({i: x_meta[i].post_key for i in x_meta}),
)
check("X 多图帖 2 张", {x_meta[11].position_in_post, x_meta[12].position_in_post} == {0, 1})
check(
    "X 按文件名日期",
    x_meta[11].published_at is not None and x_meta[11].published_at.day == 11,
    str(x_meta[11].published_at),
)

x_dir = _tmp / "xdump"
x_dir.mkdir()
(x_dir / "2026-07-11 2075689223316004968 2075689183453360128.jpg").write_bytes(JPEG)
(x_dir / "2026-07-11 2075689223316004968 2075689183453249536.jpg").write_bytes(JPEG)
(x_dir / "2026-07-11 2075689223316004968.txt").write_text("今天的琉然太好看了", encoding="utf-8")
x_files2 = [
    MtFile(
        id=21,
        md5="t1",
        file_name="2026-07-11 2075689223316004968 2075689183453360128.jpg",
        disk_path=str(x_dir / "2026-07-11 2075689223316004968 2075689183453360128.jpg"),
    ),
    MtFile(
        id=22,
        md5="t2",
        file_name="2026-07-11 2075689223316004968 2075689183453249536.jpg",
        disk_path=str(x_dir / "2026-07-11 2075689223316004968 2075689183453249536.jpg"),
    ),
]
x_cap = photo_service._apply_post_meta(x_files2, "fan", x_dir, True)
check(
    "X 同目录 txt 作为博文",
    x_cap[21].caption == "今天的琉然太好看了" and x_cap[21].post_key == x_cap[22].post_key,
    x_cap[21].caption,
)

dump_dir = _tmp / "xdump_full"
dump_dir.mkdir()
dump_txt = """============================================================
USER INFO
============================================================
Name: A
Screen Name: @wavSSS_5
User ID: 1863512030361378817

============================================================
TWEET
============================================================
Tweet ID: 2075849226597359894
Created At: 2026-07-11 15:46:37
URL: https://twitter.com/wavSSS_5/status/2075849226597359894

Content:
----------------------------------------
260703 tripleS LOVE&POP everline fansign
----------------------------------------

Stats:
  Likes: 243

============================================================
MEDIA
============================================================
[1] PHOTO
    ID: 2075849220184264704
"""
(dump_dir / "2026-07-11 2075849226597359894.txt").write_text(dump_txt, encoding="utf-8")
jpg_name = "2026-07-11 2075849226597359894 2075849220184264704.jpg"
(dump_dir / jpg_name).write_bytes(JPEG)
dump_files = [
    MtFile(id=31, md5="d1", file_name=jpg_name, disk_path=str(dump_dir / jpg_name)),
]
dump_meta = photo_service._apply_post_meta(dump_files, "fan", dump_dir, True)
check(
    "X dump 发帖人单独存",
    dump_meta[31].author == "A (@wavSSS_5)"
    and dump_meta[31].caption is not None
    and "260703" in dump_meta[31].caption
    and "USER INFO" not in dump_meta[31].caption,
    f"author={dump_meta[31].author}",
)
check(
    "X dump Created At 进时间",
    dump_meta[31].published_at is not None
    and dump_meta[31].published_at.isoformat() == "2026-07-11T15:46:37",
    str(dump_meta[31].published_at),
)

httpd.shutdown()
db.close()
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

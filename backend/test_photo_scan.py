"""图片文件夹扫描、帖子分组、uid 稳定、照片墙不分组。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_tmp = Path(tempfile.mkdtemp(prefix="photo_scan_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DERIVED_DIR"] = str(_tmp / "derived")
os.environ["STORAGE_LIBRARY_DIR"] = str(_tmp / "library")
os.environ["STORAGE_INCOMING_DIR"] = str(_tmp / "incoming")
os.environ["PHOTO_BROWSE_ROOTS"] = str(_tmp)

from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.models.artist import Artist  # noqa: E402
from app.models.photo import Photo, PhotoSource  # noqa: E402
from app.services import photo_service  # noqa: E402

settings.ensure_directories()
init_db()
Base.metadata.create_all(bind=engine)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def write_txt(path: Path, published: str, caption: str, files: list[str]) -> None:
    body = (
        f"发布时间：{published}\n\n"
        f"博文原文：{caption}\n\n"
        "媒体文件列表：\n"
        + "\n".join(files)
        + "\n"
    )
    path.write_text(body, encoding="utf-8")


folder = _tmp / "ig"
folder.mkdir()
write_txt(
    folder / "2023-05-17 00-46-45.txt",
    "2023-05-17 00:46:45",
    "♥️\n第二行",
    [
        "2023-05-17 00-46-45_01.jpg",
        "2023-05-17 00-46-45_02.jpg",
        "2023-05-17 00-46-45_03.jpg",
    ],
)
for i in range(1, 4):
    (folder / f"2023-05-17 00-46-45_0{i}.jpg").write_bytes(b"fake-jpg")
(folder / "2024-01-01 12-00-00_01.jpg").write_bytes(b"loose")
(folder / "crawler-index.json").write_text("{}", encoding="utf-8")
(folder / "clip.mp4").write_bytes(b"fake-mp4")

sidecar = photo_service.parse_sidecar_txt(folder / "2023-05-17 00-46-45.txt")
check("sidecar 解析到 3 个媒体名", sidecar is not None and sidecar.media_names == [
    "2023-05-17 00-46-45_01.jpg",
    "2023-05-17 00-46-45_02.jpg",
    "2023-05-17 00-46-45_03.jpg",
])
check("sidecar 多行正文", sidecar is not None and sidecar.caption == "♥️\n第二行")
check(
    "文件名解析发布时间",
    photo_service.published_at_from_name("2023-05-17 00-46-45_01.jpg") is not None,
)

db = SessionLocal()
artist = Artist(name="HEEJIN")
db.add(artist)
db.commit()
db.refresh(artist)
aid = artist.id
db.close()

db = SessionLocal()
try:
    photo_service.bind_source(
        db,
        owner_type="artist",
        owner_id=999999,
        section="official",
        folder_path=str(folder),
    )
    check("不存在的艺人不能绑定", False)
except photo_service.PhotoServiceError:
    check("不存在的艺人不能绑定", True)
db.close()

db = SessionLocal()
src = photo_service.bind_source(
    db,
    owner_type="artist",
    owner_id=aid,
    section="official",
    folder_path=str(folder),
)
sid = src.id
check("绑定成功", sid > 0, str(sid))
db.close()

db = SessionLocal()
try:
    photo_service.bind_source(
        db,
        owner_type="artist",
        owner_id=aid,
        section="fan",
        folder_path=str(folder),
    )
    check("同一文件夹不能重复绑定", False)
except photo_service.PhotoServiceError as e:
    check("同一文件夹不能重复绑定", "已绑定" in str(e), str(e))
db.close()

db = SessionLocal()
source = db.get(PhotoSource, sid)
stats = photo_service.scan_source(db, source)
check("扫描看到 5 个媒体（3+1+mp4）", stats.files_seen == 5, str(stats.files_seen))
check("扫描创建 5 条", stats.created == 5, str(stats.created))
check("扫描识别到 1 个帖子", stats.posts == 1, str(stats.posts))
db.close()

db = SessionLocal()
feed = photo_service.owner_feed(
    db, owner_type="artist", owner_id=aid, section="official", page=1, page_size=24
)
check("官方信息流 3 条（1 帖 + 2 散图）", feed["total"] == 3, str(feed["total"]))
posts = [it for it in feed["items"] if it["kind"] == "post"]
check("帖子含 3 张图", len(posts) == 1 and len(posts[0]["photos"]) == 3, str(len(posts[0]["photos"]) if posts else None))
check("帖子正文保留", posts and posts[0]["caption"] == "♥️\n第二行")
uids = [p.uid for it in feed["items"] for p in it["photos"]]
check("每张照片都有 uid", len(uids) == 5 and all(uids), str(len(uids)))
uid_by_name = {p.file_name: p.uid for it in feed["items"] for p in it["photos"]}
uid_02 = uid_by_name.get("2023-05-17 00-46-45_02.jpg")
db.close()

# 文件消失后再扫描：软删除；放回后 uid 不变
missing = folder / "2023-05-17 00-46-45_02.jpg"
missing.unlink()
db = SessionLocal()
source = db.get(PhotoSource, sid)
stats = photo_service.scan_source(db, source)
check("消失的文件从索引移除", stats.removed == 1, str(stats.removed))
db.close()

db = SessionLocal()
feed = photo_service.owner_feed(
    db, owner_type="artist", owner_id=aid, section="official", page=1, page_size=24
)
active_names = [p.file_name for it in feed["items"] for p in it["photos"]]
check("信息流不再含消失文件", "2023-05-17 00-46-45_02.jpg" not in active_names, str(active_names))
db.close()

missing.write_bytes(b"fake-jpg")
db = SessionLocal()
source = db.get(PhotoSource, sid)
stats = photo_service.scan_source(db, source)
check("文件回来后恢复而非新建", stats.restored == 1 and stats.created == 0, f"restored={stats.restored} created={stats.created}")
db.close()

db = SessionLocal()
row = db.scalar(
    select(Photo).where(
        Photo.file_name == "2023-05-17 00-46-45_02.jpg",
        Photo.active_filter(),
    )
)
check("恢复后 uid 不变", row is not None and row.uid == uid_02, str(getattr(row, "uid", None)))
db.close()

wall_dir = _tmp / "wall"
wall_dir.mkdir()
write_txt(
    wall_dir / "2023-05-17 00-46-45.txt",
    "2023-05-17 00:46:45",
    "不应分组",
    ["a.jpg", "b.jpg"],
)
(wall_dir / "a.jpg").write_bytes(b"a")
(wall_dir / "b.jpg").write_bytes(b"b")
(wall_dir / "c.jpg").write_bytes(b"c")

db = SessionLocal()
wall_src = photo_service.bind_source(
    db,
    owner_type="artist",
    owner_id=aid,
    section="wall",
    folder_path=str(wall_dir),
)
photo_service.scan_source(db, wall_src)
feed = photo_service.owner_feed(
    db, owner_type="artist", owner_id=aid, section="wall", page=1, page_size=24
)
check("照片墙全部按单张", feed["total"] == 3 and all(it["kind"] == "photo" for it in feed["items"]), str(feed["total"]))
check("照片墙忽略 sidecar 分组", all(it["post_key"] is None for it in feed["items"]))
db.close()

db = SessionLocal()
row = db.scalar(
    select(Photo)
    .options(selectinload(Photo.source))
    .where(
        Photo.file_name == "2023-05-17 00-46-45_01.jpg",
        Photo.active_filter(),
    )
)
path = photo_service.resolve_photo_file(row)
check("按索引取原文件", path.is_file() and path.name == row.file_name, str(path))
db.close()

X_DUMP = """============================================================
USER INFO
============================================================
Name: A
Screen Name: @wavSSS_5
User ID: 1863512030361378817
Bio: kimyooyeon pabo
Location: yooyeon’s heart
Followers: 766
Following: 225
Tweets: 1517

============================================================
TWEET
============================================================
Tweet ID: 2075849226597359894
Created At: 2026-07-11 15:46:37
URL: https://twitter.com/wavSSS_5/status/2075849226597359894

Content:
----------------------------------------
260703 tripleS LOVE&POP everline fansign

˚₊‧꒰ა ♡ ໒꒱ ‧₊˚

#tripleS #yooyeon #kimyooyeon #유연 https://t.co/zTWGokJfyF
----------------------------------------

Stats:
  Views: 1821
  Likes: 243
  Retweets: 30
  Replies: 0
  Bookmarks: 26

Source: <a href="http://twitter.com/download/iphone" rel="nofollow">Twitter for iPhone</a>

Tags: #tripleS #yooyeon #kimyooyeon #유연

============================================================
MEDIA
============================================================
[1] PHOTO
    ID: 2075849220184264704
    Size: 899x1198
    URL: https://pbs.twimg.com/media/HM7l3hBa0AAnbmN.jpg
"""

x_parsed = photo_service.parse_twitter_dump_text(X_DUMP)
check("X dump 不是 Instagram sidecar", photo_service.parse_sidecar_txt is not None)
check(
    "X dump 识别发帖人",
    x_parsed is not None and x_parsed.author == "A (@wavSSS_5)",
    getattr(x_parsed, "author", None),
)
check(
    "X dump 作者单独存、不再拼进文案首行",
    x_parsed is not None and x_parsed.caption.startswith("260703 tripleS LOVE&POP everline fansign"),
    (x_parsed.caption.splitlines()[0] if x_parsed else None),
)
check(
    "X dump 正文不含 USER INFO / Stats",
    x_parsed is not None
    and "USER INFO" not in x_parsed.caption
    and "Stats:" not in x_parsed.caption
    and "260703 tripleS LOVE&POP everline fansign" in x_parsed.caption,
    (x_parsed.caption[:80] if x_parsed else None),
)
check(
    "X dump 支持显式「发布者：」行覆盖 USER INFO",
    (lambda m: m is not None and m.author == "自定义发布者" and "260703" in m.caption)(
        photo_service.parse_twitter_dump_text(
            X_DUMP.replace("USER INFO", "发布者：自定义发布者\nUSER INFO")
        )
    ),
)
check(
    "X dump 取 Created At 时分秒",
    x_parsed is not None
    and x_parsed.published_at is not None
    and x_parsed.published_at.isoformat() == "2026-07-11T15:46:37",
    getattr(x_parsed, "published_at", None),
)
check(
    "Instagram sidecar 不会被当成 X dump",
    photo_service.parse_twitter_dump_text("发布时间：2023-05-17\n博文原文：hi") is None,
)

xfolder = _tmp / "x"
xfolder.mkdir()
(xfolder / "2026-07-11 2075849226597359894.txt").write_text(X_DUMP, encoding="utf-8")
(xfolder / "2026-07-11 2075849226597359894 2075849220184264704.jpg").write_bytes(b"x1")
(xfolder / "2026-07-11 2075689223316004968 2075689183453249536.jpg").write_bytes(b"x2a")
(xfolder / "2026-07-11 2075689223316004968 2075689183453360128.jpg").write_bytes(b"x2b")
(xfolder / "2026-07-11 2075689223316004968.txt").write_text(
    X_DUMP.replace("2075849226597359894", "2075689223316004968").replace(
        "15:46:37", "12:00:01"
    ).replace("260703 tripleS LOVE&POP everline fansign", "第二帖正文"),
    encoding="utf-8",
)

db = SessionLocal()
xsrc = photo_service.bind_source(
    db,
    owner_type="artist",
    owner_id=aid,
    section="fan",
    folder_path=str(xfolder),
)
xstats = photo_service.scan_source(db, xsrc)
check("X 扫描识别 2 个帖子", xstats.posts == 2, str(xstats.posts))
check("X 扫描看到 3 张图", xstats.files_seen == 3, str(xstats.files_seen))
db.close()

db = SessionLocal()
xfeed = photo_service.owner_feed(
    db, owner_type="artist", owner_id=aid, section="fan", page=1, page_size=24
)
xposts = [it for it in xfeed["items"] if it["kind"] == "post"]
check("X 信息流 2 条帖子", len(xposts) == 2, str(len(xposts)))
by_key = {it["post_key"]: it for it in xposts}
one = by_key.get("2026-07-11_2075849226597359894")
two = by_key.get("2026-07-11_2075689223316004968")
check(
    "X 单图帖也成帖并带发帖人",
    one is not None
    and len(one["photos"]) == 1
    and one.get("author") == "A (@wavSSS_5)"
    and "260703" in (one.get("caption") or ""),
    f"author={one.get('author') if one else None}",
)
check(
    "X 帖子时间用 Created At 而不是文件名日期",
    one is not None and str(one.get("published_at") or "").startswith("2026-07-11 15:46:37"),
    one.get("published_at") if one else None,
)
check(
    "X 多图帖共用同一正文",
    two is not None and len(two["photos"]) == 2 and "第二帖正文" in (two.get("caption") or ""),
    (two.get("caption") if two else None),
)
db.close()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

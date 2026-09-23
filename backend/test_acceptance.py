# -*- coding: utf-8 -*-
"""ID/UID/Soft Delete 架构升级 —— 9 项验收测试（第 2 版）。"""
import json
import sqlite3
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"
DB = "/Users/zhanghaoran/Downloads/mv/backend/data/kpop_media.db"
PASS = []


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read()
            if resp.status == 204 or not raw:
                return resp.status, None
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except Exception:
            return e.code, None


def check(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))


def db_rows(sql, args=()):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


# ================= 1. UID 自动生成（6 个实体） =================
created = {}
for ent, payload in [
    ("artists", {"name": "IVE"}),
    ("groups", {"name": "Girls' Generation"}),
    ("companies", {"name": "STARSHIP"}),
    ("albums", {"name": "I've IVE"}),
    ("songs", {"name": "I AM"}),
    ("music-videos", {"name": "IVE - I AM [M/V]"}),
]:
    st, obj = req("POST", f"/api/{ent}", payload)
    check(f"create {ent} -> 201", st == 201, f"status={st}")
    if st == 201:
        check(
            f"create {ent} uid/deleted_at",
            obj.get("id") and obj.get("uid") and obj.get("deleted_at") is None,
            json.dumps(obj, ensure_ascii=False)[:120],
        )
        created[ent] = obj

uids = [o["uid"] for o in created.values()]
check("uid 唯一性(跨实体)", len(set(uids)) == len(uids), str(uids))

artist1 = created["artists"]
mv1 = created["music-videos"]
hash_h = "HASH-ACCEPT-001"

# ================= 2. UID 不可修改 =================
st, upd = req("PATCH", f"/api/artists/{artist1['id']}", {"uid": "FORCED-UID", "name": "IVE2"})
check("PATCH uid 被忽略", st == 200 and upd.get("uid") == artist1["uid"] and upd.get("name") == "IVE2", str(upd)[:120])

# ================= 3. 软删除 =================
st, _ = req("DELETE", f"/api/artists/{artist1['id']}")
check("DELETE artist -> 204", st == 204, f"status={st}")
rows = db_rows("SELECT id, uid, deleted_at FROM artists WHERE id=?", (artist1["id"],))
check(
    "DB 行保留 id/uid, deleted_at 非空",
    len(rows) == 1 and rows[0][0] == artist1["id"] and rows[0][1] == artist1["uid"] and rows[0][2] is not None,
    str(rows),
)

# ================= 4. 默认列表过滤（6 个实体各删一个再查列表） =================
for ent in ["artists", "groups", "companies", "albums", "songs", "music-videos"]:
    st, obj = req("POST", f"/api/{ent}", {"name": f"TMP-{ent}"})
    if st == 201:
        req("DELETE", f"/api/{ent}/{obj['id']}")
        st2, lst = req("GET", f"/api/{ent}?page_size=100")
        ids = [i["id"] for i in (lst or {}).get("items", [])]
        check(f"list {ent} 不含已删实体", st2 == 200 and obj["id"] not in ids)
st, lst = req("GET", "/api/music-videos?page_size=100")
mv_ids = [i["id"] for i in (lst or {}).get("items", [])]
check("list music-videos 仍含活跃实体 mv1", st == 200 and mv1["id"] in mv_ids)

# ================= 5. 普通详情 404 =================
st, _ = req("GET", f"/api/artists/{artist1['id']}")
check("GET deleted artist detail -> 404", st == 404, f"status={st}")

# ================= 6. ID 不复用 =================
st, artist2 = req("POST", "/api/artists", {"name": "NewJeans"})
check(
    "删除后新建 id/uid 不复用",
    st == 201 and artist2["id"] != artist1["id"] and artist2["uid"] != artist1["uid"],
    f"old=({artist1['id']},{artist1['uid']}) new=({artist2['id']},{artist2['uid']})",
)

# ================= 7. UID 查询 =================
st, byuid = req("GET", f"/api/artists/by-uid/{artist2['uid']}")
check("by-uid 查询命中", st == 200 and byuid["id"] == artist2["id"])
st, _ = req("GET", f"/api/artists/by-uid/{artist1['uid']}")
check("by-uid 已删除 -> 404", st == 404, f"status={st}")

# ================= 8. MusicVideo 去重 =================
# 8a. (source_platform, source_id) 活跃重复 -> 409（API 层）
# 幂等：清理历史运行遗留的同 source 活跃行，保证可重复执行
con = sqlite3.connect(DB)
try:
    con.execute("UPDATE music_videos SET deleted_at=datetime('now') WHERE source_platform='youtube' AND source_id='abc123' AND deleted_at IS NULL")
    con.commit()
finally:
    con.close()
st, src1 = req("POST", "/api/music-videos", {"name": "src-1", "source_platform": "youtube", "source_id": "abc123"})
check("创建 source 视频 1", st == 201, f"status={st}")
st, _ = req("POST", "/api/music-videos", {"name": "src-2", "source_platform": "youtube", "source_id": "abc123"})
check("同 source 活跃重复 -> 409", st == 409, f"status={st}")

# 8b. 普通创建接口不接受 file_hash（该字段仅扫描/入库回填，Pydantic 忽略）
st, _ = req("POST", "/api/music-videos", {"name": "ghost", "file_hash": "GHOST-HASH"})
check(
    "create API 忽略 file_hash(不落库)",
    st == 201 and db_rows("SELECT COUNT(*) FROM music_videos WHERE file_hash='GHOST-HASH'")[0][0] == 0,
)

# 8c. DB 层验证部分唯一索引（模拟扫描回填 file_hash 后）
con = sqlite3.connect(DB)
try:
    # 幂等：清理历史运行遗留的同 hash 活跃行（避免部分唯一索引冲突）
    con.execute("UPDATE music_videos SET deleted_at=datetime('now') WHERE file_hash=? AND deleted_at IS NULL", (hash_h,))
    con.commit()
    con.execute("UPDATE music_videos SET file_hash=? WHERE id=?", (hash_h, src1["id"]))
    con.commit()
    dup_raised = False
    uid_dup = f"uid-dup-{int(time.time() * 1000)}"
    try:
        con.execute(
            "INSERT INTO music_videos (uid, name, file_hash, video_type, ingestion_status, created_at, updated_at) VALUES (?, ?, ?, 'Other', 'incoming', datetime('now'), datetime('now'))",
            (uid_dup, "dup-active", hash_h),
        )
        con.commit()
    except sqlite3.IntegrityError as e:
        dup_raised = "UNIQUE constraint failed" in str(e) and "file_hash" in str(e)
        con.rollback()
    check("活跃行同 file_hash 唯一索引拦截", dup_raised)
    con.execute("UPDATE music_videos SET deleted_at=datetime('now') WHERE id=?", (src1["id"],))
    con.commit()
    rebuilt = False
    uid_rebuild = f"uid-rebuild-{int(time.time() * 1000)}"
    try:
        con.execute(
            "INSERT INTO music_videos (uid, name, file_hash, video_type, ingestion_status, created_at, updated_at) VALUES (?, ?, ?, 'Other', 'incoming', datetime('now'), datetime('now'))",
            (uid_rebuild, "rebuild", hash_h),
        )
        con.commit()
        rebuilt = True
    except sqlite3.IntegrityError:
        con.rollback()
    check("软删除后同 hash 重建允许", rebuilt)
finally:
    con.close()

# 8d. 历史去重能力保留：扫描去重查询（不过滤 deleted_at）仍能找到软删除记录
rows = db_rows("SELECT id, file_hash, deleted_at FROM music_videos WHERE file_hash=?", (hash_h,))
deleted_rows = [r for r in rows if r[2] is not None]
check("历史去重：软删除行保留可识别", len(deleted_rows) >= 1, str(rows))

# ================= 9. 关系完整性 =================
st, rel_artist = req("POST", "/api/artists", {"name": "aespa"})
st, rel_song = req("POST", "/api/songs", {"name": "Supernova"})
st, rel_album = req("POST", "/api/albums", {"name": "Armageddon"})
st, rel_group = req("POST", "/api/groups", {"name": "aespa"})
st, rel = req("POST", "/api/songs/relations", {"song_id": rel_song["id"], "artist_id": rel_artist["id"], "role": "PrimaryArtist"})
check("创建 SongArtistRelation", st == 201, f"status={st}")
st, rel_mv = req(
    "POST",
    "/api/music-videos",
    {
        "name": "aespa - Supernova [M/V]",
        "song_ids": [rel_song["id"]],
        "album_ids": [rel_album["id"]],
        "artist_ids": [rel_artist["id"]],
        "group_ids": [rel_group["id"]],
    },
)
check("创建带关联 MV", st == 201, f"status={st}")

# Dashboard 统计过滤软删除（删除前记录 total_artists）
st, stats_before = req("GET", "/api/dashboard/stats")
total_artists_before = (stats_before or {}).get("total_artists", 0)

st, _ = req("DELETE", f"/api/artists/{rel_artist['id']}")
check("删除关联 artist -> 204", st == 204)
st, stats_after = req("GET", "/api/dashboard/stats")
total_artists_after = (stats_after or {}).get("total_artists", 0)
check("dashboard total_artists 过滤已删实体", total_artists_after == total_artists_before - 1,
      f"before={total_artists_before} after={total_artists_after}")

# 关系数据仍存在（软删除不物理删除关系）
song_artist = db_rows(
    "SELECT COUNT(*) FROM song_artist_relations WHERE artist_id=? AND song_id=?",
    (rel_artist["id"], rel_song["id"]),
)[0][0]
check("关系数据仍存在(song_artist)", song_artist >= 1, f"rows={song_artist}")
mv_links = db_rows(
    "SELECT COUNT(*) FROM music_video_artists WHERE music_video_id=? AND artist_id=?",
    (rel_mv["id"], rel_artist["id"]),
)[0][0]
check("关系数据仍存在(mv_artist)", mv_links >= 1)
# Song/Album/MV 未被物理删除，仍可访问
st, _ = req("GET", f"/api/songs/{rel_song['id']}")
check("Song 未被物理删除", st == 200)
st, _ = req("GET", f"/api/albums/{rel_album['id']}")
check("Album 未被物理删除", st == 200)
st, _ = req("GET", f"/api/music-videos/{rel_mv['id']}")
check("MusicVideo 未被物理删除", st == 200)
# 播放路径对已删除 MV 404（先软删除 mv1）
st, _ = req("DELETE", f"/api/music-videos/{mv1['id']}")
check("删除 MV -> 204", st == 204, f"status={st}")
st, _ = req("POST", "/api/playback/sessions", {"music_video_id": mv1["id"]})
check("已删除 MV 创建播放会话 -> 404", st == 404, f"status={st}")
st, _ = req("GET", f"/api/playback/sessions/1")
check("不存在的播放会话 -> 404", st == 404, f"status={st}")

# ================= 10. 软删除关系完整性 =================
deleted_ids = {"artists": [], "groups": [], "songs": [], "albums": [], "companies": []}

# 10a. 删除 Artist 后，创建 SongArtistRelation 应失败
st, s10_song = req("POST", "/api/songs", {"name": "R-Integrity-Song"})
st, s10_artist = req("POST", "/api/artists", {"name": "R-Integrity-Artist"})
st, _ = req("POST", "/api/songs/relations", {"song_id": s10_song["id"], "artist_id": s10_artist["id"]})
check("创建正常 SongArtistRelation -> 201", st == 201, f"status={st}")
req("DELETE", f"/api/artists/{s10_artist['id']}")
deleted_ids["artists"].append(s10_artist["id"])
st, _ = req("POST", "/api/songs/relations", {"song_id": s10_song["id"], "artist_id": s10_artist["id"]})
check("Artist 已删 -> 创建 SongArtistRelation -> 404", st == 404, f"status={st}")
st, rels = req("GET", f"/api/songs/{s10_song['id']}/relations")
check("song relations 不返回已删 Artist 的关系", st == 200 and s10_artist["id"] not in [x.get("artist_id") for x in rels], str(rels)[:100])

# 10b. 删除 Artist 后，创建 GroupMembership 应失败
st, s10_group = req("POST", "/api/groups", {"name": "R-Integrity-Group"})
st, s10_artist2 = req("POST", "/api/artists", {"name": "R-Integrity-Artist2"})
st, _ = req("POST", "/api/memberships", {"group_id": s10_group["id"], "artist_id": s10_artist2["id"]})
check("创建正常 GroupMembership -> 201", st == 201, f"status={st}")
req("DELETE", f"/api/artists/{s10_artist2['id']}")
deleted_ids["artists"].append(s10_artist2["id"])
st, _ = req("POST", "/api/memberships", {"group_id": s10_group["id"], "artist_id": s10_artist2["id"]})
check("Artist 已删 -> 创建 GroupMembership -> 404", st == 404, f"status={st}")
st, ms = req("GET", f"/api/memberships?group_id={s10_group['id']}&page_size=100")
ids = [x["artist_id"] for x in (ms or {}).get("items", [])]
check("memberships 列表不返回已删 Artist 成员", st == 200 and s10_artist2["id"] not in ids, str(ids)[:100])
req("DELETE", f"/api/groups/{s10_group['id']}")
deleted_ids["groups"].append(s10_group["id"])
st, _ = req("GET", f"/api/memberships/by_group/{s10_group['id']}")
check("Group 已删 -> GET memberships/by_group -> 404", st == 404, f"status={st}")

# 10c. 删除 Album 后，创建 AlbumTrack 应失败
st, s10_album = req("POST", "/api/albums", {"name": "R-Integrity-Album"})
st, s10_song2 = req("POST", "/api/songs", {"name": "R-Integrity-Song2"})
st, _ = req("POST", "/api/albums/tracks", {"album_id": s10_album["id"], "song_id": s10_song2["id"], "track_number": 1})
check("创建正常 AlbumTrack -> 201", st == 201, f"status={st}")
req("DELETE", f"/api/albums/{s10_album['id']}")
deleted_ids["albums"].append(s10_album["id"])
st, _ = req("POST", "/api/albums/tracks", {"album_id": s10_album["id"], "song_id": s10_song2["id"], "track_number": 2})
check("Album 已删 -> 创建 AlbumTrack -> 404", st == 404, f"status={st}")
st, _ = req("GET", f"/api/albums/{s10_album['id']}/tracks")
check("Album 已删 -> GET tracks -> 404", st == 404, f"status={st}")

# 10d. 删除 Company 后，创建 Artist/GroupCompanyRelation 应失败
st, s10_company = req("POST", "/api/companies", {"name": "R-Integrity-Company"})
st, s10_artist3 = req("POST", "/api/artists", {"name": "R-Integrity-Artist3"})
st, _ = req("POST", "/api/companies/artist-relations", {"artist_id": s10_artist3["id"], "company_id": s10_company["id"]})
check("创建正常 ArtistCompanyRelation -> 201", st == 201, f"status={st}")
req("DELETE", f"/api/companies/{s10_company['id']}")
deleted_ids["companies"].append(s10_company["id"])
st, _ = req("POST", "/api/companies/artist-relations", {"artist_id": s10_artist3["id"], "company_id": s10_company["id"]})
check("Company 已删 -> 创建 ArtistCompanyRelation -> 404", st == 404, f"status={st}")
st, s10_group2 = req("POST", "/api/groups", {"name": "R-Integrity-Group2"})
st, _ = req("POST", "/api/companies/group-relations", {"group_id": s10_group2["id"], "company_id": s10_company["id"]})
check("Company 已删 -> 创建 GroupCompanyRelation -> 404", st == 404, f"status={st}")

# 10e. 删除 Song 后，创建/更新 MusicVideo.song_id 应失败；详情不返回已删 song
st, s10_song3 = req("POST", "/api/songs", {"name": "R-Integrity-Song3"})
st, s10_mv = req("POST", "/api/music-videos", {"name": "R-Integrity-MV", "song_id": s10_song3["id"]})
check("创建带 song_id 的 MV -> 201", st == 201, f"status={st}")
req("DELETE", f"/api/songs/{s10_song3['id']}")
deleted_ids["songs"].append(s10_song3["id"])
st, _ = req("POST", "/api/music-videos", {"name": "R-Integrity-MV2", "song_id": s10_song3["id"]})
check("Song 已删 -> 创建 MV(song_id) -> 404", st == 404, f"status={st}")
st, _ = req("PATCH", f"/api/music-videos/{s10_mv['id']}", {"song_id": s10_song3["id"]})
check("Song 已删 -> 更新 MV.song_id -> 404", st == 404, f"status={st}")
st, mv_detail = req("GET", f"/api/music-videos/{s10_mv['id']}")
check(
    "MV 详情不返回已删 song 名称",
    st == 200 and mv_detail.get("song_name") is None
    and s10_song3["id"] not in [x["id"] for x in mv_detail.get("songs", [])],
    json.dumps(mv_detail, ensure_ascii=False)[:120],
)
st, _ = req("GET", f"/api/songs/{s10_song3['id']}/relations")
check("Song 已删 -> GET relations -> 404", st == 404, f"status={st}")
st, _ = req("GET", f"/api/songs/{s10_song3['id']}/credits")
check("Song 已删 -> GET credits -> 404", st == 404, f"status={st}")
st, _ = req("GET", f"/api/songs/{s10_song3['id']}/albums")
check("Song 已删 -> GET albums -> 404", st == 404, f"status={st}")

# 10f. 多态 release_artist：发行主体已删 -> 创建失败
st, s10_artist4 = req("POST", "/api/artists", {"name": "R-Integrity-Artist4"})
st, _ = req("POST", "/api/albums", {"name": "R-Integrity-Album2", "release_artist_type": "artist", "release_artist_id": s10_artist4["id"]})
check("创建带 release_artist 专辑 -> 201", st == 201, f"status={st}")
req("DELETE", f"/api/artists/{s10_artist4['id']}")
deleted_ids["artists"].append(s10_artist4["id"])
st, _ = req("POST", "/api/albums", {"name": "R-Integrity-Album3", "release_artist_type": "artist", "release_artist_id": s10_artist4["id"]})
check("Artist 已删 -> 创建 Album(release_artist) -> 404", st == 404, f"status={st}")

# 10g. Company 父级已删 -> 创建子公司失败
st, s10_cp = req("POST", "/api/companies", {"name": "R-Integrity-Company-Parent"})
st, _ = req("POST", "/api/companies", {"name": "R-Integrity-Company-Child", "parent_company_id": s10_cp["id"]})
check("创建带 parent 公司 -> 201", st == 201, f"status={st}")
req("DELETE", f"/api/companies/{s10_cp['id']}")
deleted_ids["companies"].append(s10_cp["id"])
st, _ = req("POST", "/api/companies", {"name": "R-Integrity-Company-Child2", "parent_company_id": s10_cp["id"]})
check("Parent Company 已删 -> 创建子公司 -> 404", st == 404, f"status={st}")

# 10h. 已删核心实体不出现在对应默认列表
for ent, ids in deleted_ids.items():
    if not ids:
        continue
    st, lst = req("GET", f"/api/{ent}?page_size=100")
    all_ids = [i["id"] for i in (lst or {}).get("items", [])]
    check(f"默认列表 {ent} 不返回已删实体", st == 200 and all(x not in all_ids for x in ids), f"deleted={ids}")

# ================= 汇总 =================
failed = [n for n, ok in PASS if not ok]
print("\n========== SUMMARY ==========")
print(f"total={len(PASS)} passed={len(PASS)-len(failed)} failed={len(failed)}")
if failed:
    print("FAILED:", failed)

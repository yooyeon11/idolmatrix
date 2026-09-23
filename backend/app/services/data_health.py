"""数据体检引擎（只读）。

对资料库做一组纯 SELECT 检查，产出「问题清单 + 健康分」。设计原则：

1. 全部只读：体检绝不写库，可在任意时刻安全运行（「标注无问题」豁免由
   health_ignores 单独写，不在这里）。
2. 只看活跃数据：所有实体过滤 deleted_at IS NULL；但「指向已软删除实体」
   的关系本身算问题（悬空引用），因为前台查询都会过滤软删除，这类关系
   用户实际看不到。
3. 每个问题都能定位到一个可修改的地方：issue 带 entity_type/entity_id/
   entity_uid 与 deep_link（跳转到旧版详情页，先修复，后续新版工作台接管）。
4. 分级：
   - error   错误：矛盾日期、悬空引用、孤立实体、疑似重复 —— 影响正确性
   - warning 警告：路径与入库规则不符、日期跨度异常、影响检索/浏览的空字段与缺关联
   - hint    提示：可以补全但不紧急
5. 健康分：100 - 5×error - 2×warning - 0.5×hint，下限 0。被豁免（标注无问题）
   的条目不参与计分。
6. 豁免按 key+signature 生效：数据一变签名就变，问题会重新出现并标 `reopened`。

新检查项：在 CHECKS 里注册一个 key，并实现同前缀的 _check_* 函数即可。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.services.health_ignores import (
    ignore_map,
    issue_key as make_issue_key,
    issue_signature,
)

from app.models.album import Album, AlbumTrack
from app.models.artist import Artist
from app.models.company import (
    ArtistCompanyRelation,
    Company,
    GroupCompanyRelation,
)
from app.models.group import Group
from app.models.membership import GroupMembership
from app.models.music_video import (
    MusicVideo,
    MusicVideoTrack,
    music_video_artists,
    music_video_groups,
    music_video_songs,
)
from app.models.song import Song, SongArtistRelation

# 健康分扣分权重
WEIGHTS = {"error": 5.0, "warning": 2.0, "hint": 0.5}
# 单项检查返回的问题上限，防止极端库把报告撑爆
MAX_ISSUES_PER_CHECK = 200
# 上传日期与表演日期跨度阈值（天）：达到即提示（2 个月）
DATE_SPAN_DAYS = 60

SEVERITY_ORDER = {"error": 0, "warning": 1, "hint": 2}


@dataclass
class Issue:
    """单条体检问题。"""

    check: str
    severity: str  # error / warning / hint
    entity_type: str  # group / artist / album / song / company / music_video / membership
    entity_id: int
    entity_uid: Optional[str]
    entity_name: str
    title: str
    detail: str = ""
    suggestion: str = ""
    deep_link: Optional[str] = None
    # 以下三项由 run_health_report 统一填充，检查函数不必关心
    issue_key: str = ""
    signature: str = ""
    reopened: bool = False  # 曾被标注无问题、但数据已变化 → 重新提示

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_uid": self.entity_uid,
            "entity_name": self.entity_name,
            "title": self.title,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "deep_link": self.deep_link,
            "issue_key": self.issue_key,
            "signature": self.signature,
            "reopened": self.reopened,
        }


@dataclass
class CheckResult:
    key: str
    name: str
    severity: str
    issues: List[Issue] = field(default_factory=list)


# ====================== 检查项实现 ======================
# 命名：_check_<key>(db) -> List[Issue]


def _active(db: Session, model):
    return db.scalars(select(model).where(model.deleted_at.is_(None))).all()


def _check_membership_dates(db: Session) -> List[Issue]:
    """成员在籍记录：加入日期晚于退出日期。"""
    out: List[Issue] = []
    rows = db.scalars(
        select(GroupMembership).where(
            GroupMembership.join_date.is_not(None),
            GroupMembership.leave_date.is_not(None),
            GroupMembership.join_date > GroupMembership.leave_date,
        )
    ).all()
    artist_ids = {m.artist_id for m in rows}
    group_ids = {m.group_id for m in rows}
    artists = {
        a.id: a
        for a in db.scalars(select(Artist).where(Artist.id.in_(artist_ids)))
    } if artist_ids else {}
    groups = {
        g.id: g
        for g in db.scalars(select(Group).where(Group.id.in_(group_ids)))
    } if group_ids else {}
    for m in rows[:MAX_ISSUES_PER_CHECK]:
        a = artists.get(m.artist_id)
        g = groups.get(m.group_id)
        aname = (a.name if a else f"#{m.artist_id}") or f"#{m.artist_id}"
        gname = (g.name if g else f"#{m.group_id}") or f"#{m.group_id}"
        out.append(
            Issue(
                check="membership_dates",
                severity="error",
                entity_type="membership",
                entity_id=m.id,
                entity_uid=None,
                entity_name=f"{aname} × {gname}",
                title=f"{aname} 在 {gname} 的加入日期晚于退出日期",
                detail=f"加入 {m.join_date} / 退出 {m.leave_date}",
                suggestion="核对哪一个是正确日期；若是两段在籍，应拆成两条记录",
                deep_link=f"/db/groups/{g.uid}" if g else None,
            )
        )
    return out


def _check_company_relation_dates(db: Session) -> List[Issue]:
    """公司关系：起始日期晚于结束日期。"""
    out: List[Issue] = []
    for rel_cls, label in (
        (ArtistCompanyRelation, "艺人"),
        (GroupCompanyRelation, "组合"),
    ):
        rows = db.scalars(
            select(rel_cls).where(
                rel_cls.start_date.is_not(None),
                rel_cls.end_date.is_not(None),
                rel_cls.start_date > rel_cls.end_date,
            )
        ).all()
        for r in rows[:MAX_ISSUES_PER_CHECK]:
            out.append(
                Issue(
                    check="company_relation_dates",
                    severity="error",
                    entity_type="company_relation",
                    entity_id=r.id,
                    entity_uid=None,
                    entity_name=f"{label}#{r.artist_id if label == '艺人' else r.group_id}",
                    title=f"{label}的公司关系起始日期晚于结束日期",
                    detail=f"起 {r.start_date} / 止 {r.end_date}",
                    suggestion="核对合同起止区间",
                )
            )
    return out


def _check_membership_dangling(db: Session) -> List[Issue]:
    """成员关系指向已软删除的艺人/组合。"""
    out: List[Issue] = []
    rows = db.execute(
        select(GroupMembership, Artist, Group)
        .join(Artist, GroupMembership.artist_id == Artist.id)
        .join(Group, GroupMembership.group_id == Group.id)
        .where(
            (Artist.deleted_at.is_not(None)) | (Group.deleted_at.is_not(None))
        )
    ).all()
    for m, a, g in rows[:MAX_ISSUES_PER_CHECK]:
        bad = []
        if a.deleted_at is not None:
            bad.append(f"艺人「{a.name}」已删除")
        if g.deleted_at is not None:
            bad.append(f"组合「{g.name}」已删除")
        out.append(
            Issue(
                check="membership_dangling",
                severity="error",
                entity_type="membership",
                entity_id=m.id,
                entity_uid=None,
                entity_name=f"{a.name} × {g.name}",
                title="、".join(bad) + "，这条成员关系已不可见",
                detail="前台查询会过滤软删除实体，该关系处于悬空状态",
                suggestion="恢复对应实体，或确认删除后清理该关系",
                deep_link=f"/db/groups/{g.uid}" if g.deleted_at is None else None,
            )
        )
    return out


def _check_mv_subject_dangling(db: Session) -> List[Issue]:
    """视频的直拍主体指向已软删除的艺人。"""
    out: List[Issue] = []
    rows = db.execute(
        select(MusicVideo, Artist)
        .join(Artist, MusicVideo.subject_artist_id == Artist.id)
        .where(
            MusicVideo.deleted_at.is_(None),
            Artist.deleted_at.is_not(None),
        )
    ).all()
    for mv, a in rows[:MAX_ISSUES_PER_CHECK]:
        out.append(
            Issue(
                check="mv_subject_dangling",
                severity="error",
                entity_type="music_video",
                entity_id=mv.id,
                entity_uid=mv.uid,
                entity_name=mv.name or f"MV#{mv.id}",
                title=f"影像关联的直拍主体「{a.name}」已被删除",
                detail="subject_artist 指向软删除实体，页面上会显示空白主体",
                suggestion="改挂到正确成员，或去掉主体改挂组合",
                deep_link=f"/videos/{mv.uid}",
            )
        )
    return out


def _check_subunit_parent_dangling(db: Session) -> List[Issue]:
    """小分队的上级组合指向已软删除的组合。"""
    out: List[Issue] = []
    subunits = db.scalars(
        select(Group).where(
            Group.deleted_at.is_(None), Group.parent_group_id.is_not(None)
        )
    ).all()
    parent_ids = {g.parent_group_id for g in subunits}
    parents = {
        p.id: p
        for p in db.scalars(select(Group).where(Group.id.in_(parent_ids)))
    } if parent_ids else {}
    for g in subunits[:MAX_ISSUES_PER_CHECK]:
        p = parents.get(g.parent_group_id)
        if p is None or p.deleted_at is not None:
            pname = p.name if p else f"#{g.parent_group_id}"
            out.append(
                Issue(
                    check="subunit_parent_dangling",
                    severity="error",
                    entity_type="group",
                    entity_id=g.id,
                    entity_uid=g.uid,
                    entity_name=g.name,
                    title=f"小分队「{g.name}」的上级组合「{pname}」不存在或已删除",
                    detail="parent_group_id 悬空",
                    suggestion="改挂正确的完整体，或清空上级组合",
                    deep_link=f"/db/groups/{g.uid}",
                )
            )
    return out


def _check_song_no_performer(db: Session) -> List[Issue]:
    """歌曲没有任何艺人/组合归属。

    归属认定两种之一：song_artist_relations 有关联行，或
    release_artist_type/id（多态发行主体）已填。都没有才报；
    且要求歌曲至少被专辑/影像引用（完全孤立的由 song_orphan 负责，
    避免同一首歌被报两条）。
    """
    out: List[Issue] = []
    rel_song_ids = select(SongArtistRelation.song_id)
    track_song_ids = select(AlbumTrack.song_id)
    mv_song_ids = select(music_video_songs.c.song_id)
    mv_track_song_ids = select(MusicVideoTrack.song_id)
    rows = db.scalars(
        select(Song).where(
            Song.deleted_at.is_(None),
            Song.id.in_(track_song_ids)
            | Song.id.in_(mv_song_ids)
            | Song.id.in_(mv_track_song_ids),
            Song.id.not_in(rel_song_ids),
            (Song.release_artist_type.is_(None)) | (Song.release_artist_id.is_(None)),
        )
    ).all()
    for s in rows[:MAX_ISSUES_PER_CHECK]:
        out.append(
            Issue(
                check="song_no_performer",
                severity="warning",
                entity_type="song",
                entity_id=s.id,
                entity_uid=s.uid,
                entity_name=s.name,
                title=f"歌曲「{s.name}」没有关联任何艺人或组合",
                detail="既影响检索，也无法在艺人/组合页下看到这首歌",
                suggestion="补挂发行主体（组合优先）",
                deep_link=f"/songs/{s.uid}",
            )
        )
    return out


def _check_song_orphan(db: Session) -> List[Issue]:
    """歌曲完全孤立：无演唱者、无专辑曲目、未被任何影像引用。"""
    out: List[Issue] = []
    rel_song_ids = select(SongArtistRelation.song_id)
    track_song_ids = select(AlbumTrack.song_id)
    mv_song_ids = select(music_video_songs.c.song_id)
    mv_track_song_ids = select(MusicVideoTrack.song_id)
    rows = db.scalars(
        select(Song).where(
            Song.deleted_at.is_(None),
            Song.id.not_in(rel_song_ids),
            Song.id.not_in(track_song_ids),
            Song.id.not_in(mv_song_ids),
            Song.id.not_in(mv_track_song_ids),
            (Song.release_artist_type.is_(None)) | (Song.release_artist_id.is_(None)),
        )
    ).all()
    for s in rows[:MAX_ISSUES_PER_CHECK]:
        out.append(
            Issue(
                check="song_orphan",
                severity="error",
                entity_type="song",
                entity_id=s.id,
                entity_uid=s.uid,
                entity_name=s.name,
                title=f"歌曲「{s.name}」完全孤立：无演唱者、无专辑、未被影像引用",
                detail="大概率是 AI 建库/入库时来源页信息不全产生的半成品",
                suggestion="补全归属，或确认无用后删除",
                deep_link=f"/songs/{s.uid}",
            )
        )
    return out


def _duplicate_check(db: Session, model, label: str, check_key: str, link: Callable[[Any], Optional[str]]) -> List[Issue]:
    """同名活跃实体查重（精确同名；别名查重后续由 name_match 接管）。"""
    out: List[Issue] = []
    name_col = model.name
    dup_names = db.scalars(
        select(name_col)
        .where(model.deleted_at.is_(None))
        .group_by(name_col)
        .having(func.count() > 1)
    ).all()
    if not dup_names:
        return out
    rows = db.scalars(
        select(model).where(model.deleted_at.is_(None), name_col.in_(dup_names))
    ).all()
    by_name: dict[str, list] = {}
    for r in rows:
        by_name.setdefault(r.name, []).append(r)
    for name, items in by_name.items():
        group = items[:MAX_ISSUES_PER_CHECK]
        ids = "、".join(f"#{i.id}" for i in group)
        first = group[0]
        out.append(
            Issue(
                check=check_key,
                severity="error",
                entity_type=check_key.replace("duplicate_", "").rstrip("s"),
                entity_id=first.id,
                entity_uid=first.uid,
                entity_name=name,
                title=f"{len(group)} 个同名{label}：「{name}」（{ids}）",
                detail="可能是同一实体的重复录入，也可能是真实的同名实体",
                suggestion="确认后合并；合并前先核对外部链接与生日等区分字段",
                deep_link=link(first),
            )
        )
    return out


def _check_duplicate_artists(db: Session) -> List[Issue]:
    return _duplicate_check(
        db, Artist, "艺人", "duplicate_artists", lambda a: f"/artists/{a.uid}"
    )


def _check_duplicate_groups(db: Session) -> List[Issue]:
    return _duplicate_check(
        db, Group, "组合", "duplicate_groups", lambda g: f"/db/groups/{g.uid}"
    )


def _check_duplicate_companies(db: Session) -> List[Issue]:
    return _duplicate_check(db, Company, "公司", "duplicate_companies", lambda c: None)


def _check_duplicate_songs(db: Session) -> List[Issue]:
    """同名 + 同发行主体的歌曲。"""
    out: List[Issue] = []
    rows = db.execute(
        select(
            Song.name,
            Song.release_artist_type,
            Song.release_artist_id,
            func.count().label("cnt"),
        )
        .where(Song.deleted_at.is_(None))
        .group_by(Song.name, Song.release_artist_type, Song.release_artist_id)
        .having(func.count() > 1)
    ).all()
    for name, rtype, rid, cnt in rows[:MAX_ISSUES_PER_CHECK]:
        first = db.scalar(
            select(Song).where(
                Song.deleted_at.is_(None),
                Song.name == name,
                Song.release_artist_type == rtype,
                Song.release_artist_id == rid,
            )
        )
        if first is None:
            continue
        label = f"{rtype}#{rid}" if rtype and rid else "（无发行主体）"
        out.append(
            Issue(
                check="duplicate_songs",
                severity="warning",
                entity_type="song",
                entity_id=first.id,
                entity_uid=first.uid,
                entity_name=name,
                title=f"{cnt} 首同名歌曲「{name}」同属 {label}",
                detail="同主体同名歌曲大概率是重复录入（同名翻唱/多版本应挂不同主体）",
                suggestion="确认后合并；同名不同版本请补齐发行主体以区分",
                deep_link=f"/songs/{first.uid}",
            )
        )
    return out


def _check_mv_no_subject(db: Session) -> List[Issue]:
    """已入库影像没有关联任何艺人/组合/直拍主体。"""
    out: List[Issue] = []
    linked_mv_artists = select(music_video_artists.c.music_video_id)
    linked_mv_groups = select(music_video_groups.c.music_video_id)
    rows = db.scalars(
        select(MusicVideo).where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.ingestion_status == "library",
            MusicVideo.subject_artist_id.is_(None),
            MusicVideo.id.not_in(linked_mv_artists),
            MusicVideo.id.not_in(linked_mv_groups),
        )
    ).all()
    for mv in rows[:MAX_ISSUES_PER_CHECK]:
        out.append(
            Issue(
                check="mv_no_subject",
                severity="warning",
                entity_type="music_video",
                entity_id=mv.id,
                entity_uid=mv.uid,
                entity_name=mv.name or f"MV#{mv.id}",
                title=f"影像「{mv.name or mv.id}」没有关联任何艺人或组合",
                detail="不会出现在任何艺人/组合的影像列表里",
                suggestion="补挂主体（个人直拍挂成员，其余挂组合）",
                deep_link=f"/videos/{mv.uid}",
            )
        )
    return out


def _check_mv_no_song(db: Session) -> List[Issue]:
    """已入库影像没有关联任何歌曲（曲目行或歌曲直连都算已关联）。"""
    out: List[Issue] = []
    linked_tracks = select(MusicVideoTrack.music_video_id)
    linked_songs = select(music_video_songs.c.music_video_id)
    rows = db.scalars(
        select(MusicVideo).where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.ingestion_status == "library",
            MusicVideo.id.not_in(linked_tracks),
            MusicVideo.id.not_in(linked_songs),
        )
    ).all()
    for mv in rows[:MAX_ISSUES_PER_CHECK]:
        out.append(
            Issue(
                check="mv_no_song",
                severity="hint",
                entity_type="music_video",
                entity_id=mv.id,
                entity_uid=mv.uid,
                entity_name=mv.name or f"MV#{mv.id}",
                title=f"影像「{mv.name or mv.id}」没有关联任何歌曲",
                detail="影响按歌曲浏览影像",
                suggestion="补充曲目行（ song + 可选专辑）",
                deep_link=f"/videos/{mv.uid}",
            )
        )
    return out


def _dir_parts(path: Path) -> tuple[str, ...]:
    """目录的归一化比较键：大小写不敏感、忽略 `.` 与空段。"""
    return tuple(p.casefold() for p in path.parts if p not in ("", "."))


def _show_dir(path: Path) -> str:
    """目录展示名（相对正式库，POSIX 分隔符）；根目录给出可读占位。"""
    parts = [p for p in path.parts if p not in ("", ".")]
    return "/".join(parts) if parts else "（正式库根目录）"


def _check_path_mismatch(db: Session) -> List[Issue]:
    """已入库影像的存放路径与入库规则（auto_organize_rel）推导出的路径不符。

    只对「文件确实在正式库内」的视频生效：file_path 是绝对路径时说明入库时选择了
    不移动文件（文件还留在待整理目录），没有正式库路径可比，跳过；规则不适用
    （无主体关联，auto_organize_rel 返回 None）时也无「应有路径」，同样跳过。
    比较大小写不敏感：磁盘上是 Twice/ 而规则写 TWICE/ 属同一条目，不算问题。

    ⚠ 性能（v3.5.6 修复「资料库页面加载很慢」）：`auto_organize_rel` 不传
    `artist_names` 时会**自己算一遍**重名艺人消歧表（`artist_dir_names` 要全表读
    `artists`）—— 逐条算就是 O(视频数 × 艺人数)。实测 NAS 规模（1500 条正式库影像 /
    481 位艺人）下本项独占整份体检 90% 耗时（1136ms / 1247ms），而整份体检又被内嵌在
    每个 /api/db/* 列表与工作台请求里 → 页面「每次都要等几秒」。这里预计算一次传进去，
    结果与逐条算完全一致（同一份全库表）。
    """
    from app.services.file_service import match_existing_dir
    from app.services.library_service import artist_dir_names, auto_organize_rel

    out: List[Issue] = []
    rows = db.scalars(
        select(MusicVideo)
        .options(
            selectinload(MusicVideo.artists),
            selectinload(MusicVideo.groups),
            selectinload(MusicVideo.songs),
        )
        .where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.ingestion_status == "library",
        )
        .order_by(MusicVideo.id)
    ).all()

    # 同一规则目录只归并一次（match_existing_dir 会读磁盘）
    merged_cache: dict[tuple[str, ...], Path] = {}
    # 重名艺人消歧表只算一次（见 docstring 的性能说明）；算不出来就交回给
    # auto_organize_rel 自己兜底，不影响结论。
    try:
        artist_names = artist_dir_names(db)
    except Exception:  # noqa: BLE001
        artist_names = None
    for mv in rows:
        raw = (mv.file_path or "").strip().replace("\\", "/")
        if not raw:
            continue
        current = Path(raw)
        if current.is_absolute() or raw.startswith("/"):
            # 未移动文件入库（file_path 写的是待整理目录的绝对路径），
            # 或 macOS 老库遗留的 /Users/... 路径 —— 都测不出「入库规则路径」
            continue
        try:
            # db / artist_names 传入：重名艺人消歧后的目录才算「应有路径」；
            # 重名艺人缺中文名时推导会抛 ArtistDirNameConflict，下面统一跳过
            expect = auto_organize_rel(mv, db=db, artist_names=artist_names)
        except Exception:  # noqa: BLE001 —— 规则推导失败不该把整份体检打挂
            continue
        if expect is None:
            continue
        current_dir = current.parent
        if _dir_parts(current_dir) == _dir_parts(expect):
            continue
        cache_key = tuple(expect.parts)
        merged = merged_cache.get(cache_key)
        if merged is None:
            merged = match_existing_dir(expect)
            merged_cache[cache_key] = merged
        if _dir_parts(current_dir) == _dir_parts(merged):
            continue
        out.append(
            Issue(
                check="path_mismatch",
                severity="warning",
                entity_type="music_video",
                entity_id=mv.id,
                entity_uid=mv.uid,
                entity_name=mv.name or f"MV#{mv.id}",
                title=f"影像「{mv.name or mv.id}」的存放路径与入库规则不符",
                detail=(
                    f"实际：{_show_dir(current_dir)} · "
                    f"按规则应为：{_show_dir(merged)}"
                ),
                suggestion=(
                    "在视频页点「更改存储路径」按规则路径归位；"
                    "若当前路径是有意为之，可标注无问题"
                ),
                deep_link=f"/videos/{mv.uid}",
            )
        )
        if len(out) >= MAX_ISSUES_PER_CHECK:
            break
    return out


def _check_date_span(db: Session) -> List[Issue]:
    """上传日期与表演日期跨度异常（相差 ≥ DATE_SPAN_DAYS，即 2 个月）。

    K-pop 场景「补档旧舞台」很常见（今年上传几年前那场的舞台），所以本条只是提醒
    核对，不一定是错误；确认无误可在体检页标注无问题（数据变了会自动重新提示）。
    """
    out: List[Issue] = []
    rows = db.scalars(
        select(MusicVideo)
        .where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.ingestion_status == "library",
            MusicVideo.published_date.is_not(None),
            MusicVideo.performance_date.is_not(None),
        )
        .order_by(MusicVideo.id)
    ).all()
    for mv in rows:
        gap = (mv.published_date - mv.performance_date).days
        if abs(gap) < DATE_SPAN_DAYS:
            continue
        tip = (
            "上传晚于表演：补档旧舞台属常见情况，确认无误可标注无问题"
            if gap > 0
            else "上传早于表演：通常是其中一个日期填错了"
        )
        out.append(
            Issue(
                check="date_span",
                severity="warning",
                entity_type="music_video",
                entity_id=mv.id,
                entity_uid=mv.uid,
                entity_name=mv.name or f"MV#{mv.id}",
                title=(
                    f"影像「{mv.name or mv.id}」的上传日期与表演日期"
                    f"相差 {abs(gap)} 天"
                ),
                detail=(
                    f"上传 {mv.published_date} · 表演 {mv.performance_date}；{tip}"
                ),
                suggestion="核对两个日期，改成正确值后本条自动消失",
                deep_link=f"/videos/{mv.uid}",
            )
        )
        if len(out) >= MAX_ISSUES_PER_CHECK:
            break
    return out


def _check_group_no_members(db: Session) -> List[Issue]:
    """组合没有任何成员。"""
    out: List[Issue] = []
    with_members = select(GroupMembership.group_id)
    rows = db.scalars(
        select(Group).where(
            Group.deleted_at.is_(None),
            Group.id.not_in(with_members),
        )
    ).all()
    for g in rows[:MAX_ISSUES_PER_CHECK]:
        is_subunit = g.parent_group_id is not None
        out.append(
            Issue(
                check="group_no_members",
                severity="warning",
                entity_type="group",
                entity_id=g.id,
                entity_uid=g.uid,
                entity_name=g.name,
                title=f"组合「{g.name}」没有任何成员" + ("（小分队）" if is_subunit else ""),
                detail="成员列表为空，详情页会显得残缺",
                suggestion="补齐成员关系",
                deep_link=f"/db/groups/{g.uid}",
            )
        )
    return out


def _check_album_no_tracks(db: Session) -> List[Issue]:
    """专辑没有任何曲目。"""
    out: List[Issue] = []
    with_tracks = select(AlbumTrack.album_id)
    rows = db.scalars(
        select(Album).where(
            Album.deleted_at.is_(None), Album.id.not_in(with_tracks)
        )
    ).all()
    for a in rows[:MAX_ISSUES_PER_CHECK]:
        out.append(
            Issue(
                check="album_no_tracks",
                severity="hint",
                entity_type="album",
                entity_id=a.id,
                entity_uid=a.uid,
                entity_name=a.name,
                title=f"专辑「{a.name}」还没有任何曲目",
                detail="空专辑无法承载按专辑浏览歌曲",
                suggestion="用 AI 补全曲目，或手动添加",
                deep_link=None,
            )
        )
    return out


def _check_missing_dates(db: Session) -> List[Issue]:
    """关键字段缺失：组合出道日 / 专辑发行日 / 艺人出生日。"""
    out: List[Issue] = []
    for g in _active(db, Group):
        if g.debut_date is None:
            out.append(
                Issue(
                    check="missing_dates",
                    severity="warning",
                    entity_type="group",
                    entity_id=g.id,
                    entity_uid=g.uid,
                    entity_name=g.name,
                    title=f"组合「{g.name}」缺少出道日期",
                    detail="影响时间线、年代筛选与排序",
                    suggestion="在资料源中抓取出道日期",
                    deep_link=f"/db/groups/{g.uid}",
                )
            )
    for a in _active(db, Album):
        if a.release_date is None:
            out.append(
                Issue(
                    check="missing_dates",
                    severity="warning",
                    entity_type="album",
                    entity_id=a.id,
                    entity_uid=a.uid,
                    entity_name=a.name,
                    title=f"专辑「{a.name}」缺少发行日期",
                    detail="影响按年份浏览与排序",
                    suggestion="AI 补全或手动填写",
                    deep_link=None,
                )
            )
    for ar in _active(db, Artist):
        if ar.birth_date is None:
            out.append(
                Issue(
                    check="missing_dates",
                    severity="hint",
                    entity_type="artist",
                    entity_id=ar.id,
                    entity_uid=ar.uid,
                    entity_name=ar.name,
                    title=f"艺人「{ar.name}」缺少出生日期",
                    detail="影响生日提醒与资料完整度",
                    suggestion="AI 补全或手动填写",
                    deep_link=f"/artists/{ar.uid}",
                )
            )
    return out[: MAX_ISSUES_PER_CHECK * 3]


def _check_company_no_relations(db: Session) -> List[Issue]:
    """公司没有关联任何艺人/组合。"""
    out: List[Issue] = []
    with_ar = select(ArtistCompanyRelation.company_id)
    with_gr = select(GroupCompanyRelation.company_id)
    rows = db.scalars(
        select(Company).where(
            Company.deleted_at.is_(None),
            Company.id.not_in(with_ar),
            Company.id.not_in(with_gr),
        )
    ).all()
    for c in rows[:MAX_ISSUES_PER_CHECK]:
        out.append(
            Issue(
                check="company_no_relations",
                severity="hint",
                entity_type="company",
                entity_id=c.id,
                entity_uid=c.uid,
                entity_name=c.name,
                title=f"公司「{c.name}」没有关联任何艺人或组合",
                detail="孤立公司通常来自 AI 建库时只建了实体没建关系",
                suggestion="补挂关系，或确认无用后删除",
                deep_link=None,
            )
        )
    return out


CHECKS: List[dict] = [
    {"key": "membership_dates", "name": "成员日期矛盾", "fn": _check_membership_dates, "severity": "error"},
    {"key": "company_relation_dates", "name": "公司关系日期矛盾", "fn": _check_company_relation_dates, "severity": "error"},
    {"key": "membership_dangling", "name": "成员关系悬空", "fn": _check_membership_dangling, "severity": "error"},
    {"key": "mv_subject_dangling", "name": "影像主体悬空", "fn": _check_mv_subject_dangling, "severity": "error"},
    {"key": "subunit_parent_dangling", "name": "小分队上级悬空", "fn": _check_subunit_parent_dangling, "severity": "error"},
    {"key": "song_orphan", "name": "孤立歌曲", "fn": _check_song_orphan, "severity": "error"},
    {"key": "duplicate_artists", "name": "同名艺人", "fn": _check_duplicate_artists, "severity": "error"},
    {"key": "duplicate_groups", "name": "同名组合", "fn": _check_duplicate_groups, "severity": "error"},
    {"key": "duplicate_companies", "name": "同名公司", "fn": _check_duplicate_companies, "severity": "error"},
    {"key": "duplicate_songs", "name": "同主体同名歌曲", "fn": _check_duplicate_songs, "severity": "warning"},
    {"key": "song_no_performer", "name": "歌曲缺演唱者", "fn": _check_song_no_performer, "severity": "warning"},
    {"key": "mv_no_subject", "name": "影像缺主体", "fn": _check_mv_no_subject, "severity": "warning"},
    {"key": "path_mismatch", "name": "路径与规则不符", "fn": _check_path_mismatch, "severity": "warning"},
    {"key": "date_span", "name": "日期跨度异常", "fn": _check_date_span, "severity": "warning"},
    {"key": "group_no_members", "name": "组合无成员", "fn": _check_group_no_members, "severity": "warning"},
    {"key": "missing_dates", "name": "关键字段缺失", "fn": _check_missing_dates, "severity": "warning"},
    {"key": "album_no_tracks", "name": "专辑无曲目", "fn": _check_album_no_tracks, "severity": "hint"},
    {"key": "mv_no_song", "name": "影像无曲目", "fn": _check_mv_no_song, "severity": "hint"},
    {"key": "company_no_relations", "name": "孤立公司", "fn": _check_company_no_relations, "severity": "hint"},
]


# ===== 报告缓存（v3.4.7）=====
# 资料库列表端点（/api/db/*）每请求都会内嵌跑一遍完整体检 —— NAS 上一次体检 ~3.3s
# （~19 项检查 + path_mismatch 逐条 auto_organize_rel + 逐规则目录磁盘探测），
# 表现为「设置-资料库每个页面都 3 秒起」。本缓存只服务列表端点（use_cache=True），
# 体检页 /api/data-health 不走缓存（用户主动查看，要求实时）。
# 失效：main.py 的写请求中间件在任何成功的 POST/PATCH/PUT/DELETE 后调用
# invalidate_health_cache()；另有 TTL 兜底（防写路径遗漏导致数字长期不更新）。
HEALTH_CACHE_TTL_SECONDS = 300.0
_health_cache: dict = {"expires": 0.0, "max_issues": None, "report": None}
_health_cache_lock = threading.Lock()


def invalidate_health_cache() -> None:
    """数据写入后调用：下次 run_health_report(use_cache=True) 重新计算。"""
    with _health_cache_lock:
        _health_cache["expires"] = 0.0
        _health_cache["max_issues"] = None
        _health_cache["report"] = None


def run_health_report(
    db: Session, max_issues: int = 1000, use_cache: bool = False
) -> dict:
    """运行全部检查，返回报告 dict（可直接作为 API 响应）。

    被用户「标注无问题」的条目会从主清单里摘出来放进 ignored_issues：
    不计分、不进 counts、不进检查项计数（口径统一，前端三处数字不会打架）。
    若同一条问题的数据后来变了（signature 不再匹配），豁免自动失效，
    该条重新出现在主清单并带 reopened=True。

    use_cache=True（资料库列表端点专用）：命中 TTL 内的同 max_issues 缓存时
    直接返回上一次报告 —— 体检只读，报告 dict 共享只读是安全的。
    """
    if use_cache:
        with _health_cache_lock:
            cached = _health_cache["report"]
            if (
                cached is not None
                and _health_cache["max_issues"] == max_issues
                and _health_cache["expires"] > time.monotonic()
            ):
                return cached

    all_issues: List[Issue] = []
    for spec in CHECKS:
        all_issues.extend(spec["fn"](db))

    ignores = ignore_map(db)
    issues: List[Issue] = []
    ignored: List[Issue] = []
    for issue in all_issues:
        issue.issue_key = make_issue_key(
            issue.check, issue.entity_type, issue.entity_id
        )
        issue.signature = issue_signature(issue.title, issue.detail)
        saved = ignores.get(issue.issue_key)
        if saved is not None:
            if saved.get("signature") == issue.signature:
                ignored.append(issue)
                continue
            # 数据已变化：旧豁免不再算数，重新提示并标注原因
            issue.reopened = True
        issues.append(issue)

    counts = {"error": 0, "warning": 0, "hint": 0}
    by_check: dict[str, int] = {}
    for i in issues:
        counts[i.severity] += 1
        by_check[i.check] = by_check.get(i.check, 0) + 1

    check_summaries = [
        {
            "key": spec["key"],
            "name": spec["name"],
            "severity": spec["severity"],
            "count": by_check.get(spec["key"], 0),
        }
        for spec in CHECKS
    ]

    deduction = (
        WEIGHTS["error"] * counts["error"]
        + WEIGHTS["warning"] * counts["warning"]
        + WEIGHTS["hint"] * counts["hint"]
    )
    score = max(0, round(100 - deduction))

    sort_key = lambda i: (  # noqa: E731
        SEVERITY_ORDER.get(i.severity, 9),
        i.check,
        i.entity_id,
    )
    issues.sort(key=sort_key)
    ignored.sort(key=sort_key)

    entity_stats = {
        "groups": db.scalar(
            select(func.count()).select_from(Group).where(Group.deleted_at.is_(None)
            )
        ) or 0,
        "artists": db.scalar(
            select(func.count()).select_from(Artist).where(Artist.deleted_at.is_(None))
        ) or 0,
        "albums": db.scalar(
            select(func.count()).select_from(Album).where(Album.deleted_at.is_(None))
        ) or 0,
        "songs": db.scalar(
            select(func.count()).select_from(Song).where(Song.deleted_at.is_(None))
        ) or 0,
        "companies": db.scalar(
            select(func.count()).select_from(Company).where(Company.deleted_at.is_(None))
        ) or 0,
        "music_videos": db.scalar(
            select(func.count())
            .select_from(MusicVideo)
            .where(MusicVideo.deleted_at.is_(None))
        ) or 0,
    }

    report = {
        "score": score,
        "counts": counts,
        "total_issues": len(issues),
        "ignored_count": len(ignored),
        "entity_stats": entity_stats,
        "checks": check_summaries,
        "issues": [i.to_dict() for i in issues[:max_issues]],
        "ignored_issues": [i.to_dict() for i in ignored[:max_issues]],
    }
    if use_cache:
        with _health_cache_lock:
            _health_cache.update(
                {
                    "expires": time.monotonic() + HEALTH_CACHE_TTL_SECONDS,
                    "max_issues": max_issues,
                    "report": report,
                }
            )
    return report

"""库服务：扫描待整理目录、入库（写库 + 移动文件）。

核心流程：
1. scan_incoming：遍历 incoming 目录的视频文件，调用 ffprobe 取技术字段，
   用文件名+大小与库内记录做重复提示（全量哈希放到真正入库）。
2. ingest：根据用户提供的元数据创建 MusicVideo，移动文件到正式库目录，
   更新 file_path 与 ingestion_status='library'。
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.core.config import settings
from app.media.ffmpeg import extract_thumbnail
from app.media.ffprobe import ProbeError, probe
from app.models.artist import Artist
from app.models.base import resolve_active_ids
from app.models.group import Group
from app.models.music_video import MusicVideo
from app.models.song import Song
from app.schemas.music_video import (
    IngestRequest,
    IngestResult,
    MusicVideoCreate,
    ScannedFileItem,
)
from app.services.file_service import (
    _sanitize,
    compute_file_hash,
    drop_file_cache,
    find_sidecar_cover,
    get_file_size,
    list_library_dirs,
    match_existing_dir,
    move_to_library,
)
from app.services.group_tree import root_group
from app.services.img_variant import VIDEO_THUMB_CACHE_DIRNAME
from app.services.json_top_level import extract_json_top_level
from app.services.relation_service import apply_video_song_tracks
from app.services.video_meta import (
    COLLAB_VIDEO_TYPE,
    COVER_VIDEO_TYPE,
    MIX_VIDEO_TYPE,
    is_short_video,
)

logger = logging.getLogger(__name__)

# 第一期支持的扩展名（按需扩展）
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".m4v", ".wmv", ".ts", ".m2ts"
}

# 文件静置阈值（秒）：mtime 距今小于该值视为「仍在写入」（如 yt-dlp 下载中），
# 扫描时跳过，避免探测读到半截文件
INCOMING_SETTLE_SECONDS = 30

# 列表/统计接口附带清理的最短间隔，避免每次翻页都整库扫盘
_CLEANUP_MIN_INTERVAL = 120.0
_cleanup_lock = threading.Lock()
_last_cleanup_at = 0.0

_COVER_EXTS = (".webp", ".jpg", ".jpeg", ".png", ".avif")
_SIDECAR_SUFFIXES = (".info.json", ".webp", ".jpg", ".jpeg", ".png", ".avif")

# info.json 精简白名单：不包含评论/字幕/自动字幕等与库无关的内容
INFO_KEYS = (
    "title", "fulltitle", "webpage_url", "id", "extractor",
    "uploader", "uploader_id", "channel",
    "upload_date", "timestamp", "description",
    "duration", "width", "height", "resolution", "fps",
    "vcodec", "acodec", "filesize_approx", "format_id", "format",
    "view_count", "like_count", "thumbnail",
)


def read_incoming_info(path: Path) -> dict:
    """读取与视频文件同名的 .info.json，仅返回白名单字段。

    不整文件 json.loads：跳过 comments / captions / formats 等大字段。
    """
    source = path.resolve()
    try:
        source.relative_to(settings.incoming_dir.resolve())
    except ValueError:
        try:
            source.relative_to(settings.incoming_bili_dir.resolve())
        except ValueError:
            raise ValueError(f"文件不在 incoming 目录内: {source}") from None
    if not source.exists():
        raise FileNotFoundError(f"源文件不存在: {source}")

    info_path = source.with_suffix(".info.json")
    if info_path.exists():
        try:
            data = extract_json_top_level(info_path, INFO_KEYS)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"解析元数据失败: {e}") from e
        return {key: data[key] for key in INFO_KEYS if key in data}

    # Bilibili 元数据：同名 .nfo / .json（bili-sync / Bili23-Downloader）
    from app.services.bilibili_meta import read_bilibili_meta

    bili = read_bilibili_meta(source)
    if bili is not None:
        return bili
    raise FileNotFoundError(f"未找到元数据文件: {info_path}")


def scan_incoming(db: Session) -> list[ScannedFileItem]:
    """扫描 incoming 目录，返回所有视频文件及其技术字段。

    失败的探测以路径+文件名占位返回，不阻断流程。
    """
    incoming = settings.incoming_dir
    if not incoming.exists():
        return []

    items: list[ScannedFileItem] = []
    for path in sorted(incoming.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            if time.time() - path.stat().st_mtime < INCOMING_SETTLE_SECONDS:
                continue  # 疑似仍在写入（下载中），跳过
        except OSError:
            continue  # 文件刚被移走/删除
        item = _scan_one(path, db)
        items.append(item)
    return items


def _scan_one(path: Path, db: Session) -> ScannedFileItem:
    file_size = get_file_size(path)
    duration = width = height = None
    video_codec = audio_codec = None

    try:
        result = probe(path)
        duration = result.duration_seconds_int
        width = result.width
        height = result.height
        video_codec = result.video_codec
        audio_codec = result.audio_codec
    except ProbeError as e:
        logger.warning("探测失败 %s: %s", path, e)
    except Exception as e:  # noqa: BLE001
        logger.warning("探测异常 %s: %s", path, e)
    finally:
        drop_file_cache(path)

    # 扫描阶段不做全量 SHA256（4K 片会把 NAS 页缓存顶满）。
    # 重复提示用文件名 + 大小；入库时再哈希并按 file_hash 唯一约束拦截。
    existing = db.scalar(
        select(MusicVideo.id).where(
            MusicVideo.deleted_at.is_(None),
            MusicVideo.file_name == path.name,
            MusicVideo.file_size == file_size,
        ).limit(1)
    )

    return ScannedFileItem(
        path=str(path),
        file_name=path.name,
        file_size=file_size,
        duration=duration,
        width=width,
        height=height,
        video_codec=video_codec,
        audio_codec=audio_codec,
        file_hash=None,
        is_duplicate=existing is not None,
    )


# ===== 自动入库整理规则（定稿 v1.3：三步判定模型）=====
#
# 第②步「特殊根 / 显式类型」短路，命中即出（顺序定稿）：
#   MIX混剪 > 短视频 > 合作舞台 > 串烧 > 翻唱 > 非表演视频
#   - MIX混剪（video_types 含 MixEdit，显式类型优先于自动推导规则）：
#       **只保留优先级短路**（v1.3：防止落进短视频/串烧/翻唱/非表演根），
#       路径逻辑与粉丝直拍（Fancam）相同 → 直接走下方常规路径：
#         成员直拍 → 组合[/歌曲]/成员名；组合主体 → 组合[/歌曲]/Live；
#         solo 艺人 → 艺人[/歌曲]/{MIX混剪}（_TYPE_DIR 标签）
#   - 短视频：短视频/{艺人|组合}（艺人优先，无艺人用组合）
#   - 合作舞台（video_types 含 CollabStage，v3.2.55 用户定稿）：
#       合作舞台[/歌曲]（**不含主体段**——合作是多主体，取任一都不对；
#       放在串烧之前：合作舞台常有多首歌，不能被「歌曲≥2 → 串烧」抢走）
#   - 串烧（关联歌曲 ≥2）：串烧/{艺人|组合}/{表演日期yymmdd|未注日期}（艺人优先）
#   - 翻唱：翻唱/{艺人|组合}/{歌曲}（艺人优先；歌曲缺失跳层）
#   - 非表演视频：非表演视频/{艺人|组合}（Other 类型或非表演标记触发，艺人优先；短视频优先）
# 常规路径主体（组合优先）：组合 > solo 艺人 > 仅艺人
#   - 艺人主体（is_solo 或无组合）→ 艺人[/歌曲]/{类型标签}
#   - 个人直拍（有组合 + 成员，非 solo）→ 组合[/歌曲]/成员名
#   - 组合主体（组合直拍/舞台/MV/现场等）→ 组合[/歌曲]/{MV|Live}
# 统一行为规范（消灭静默滑落与占位目录）：
#   - 歌曲缺失跳过对应层级，不生成占位目录
#   - 串烧缺表演日期占位「未注日期」，补齐后可通过「更改存储路径」归位
#   - 无任何主体关联（艺人/组合皆无）返回 None：入库时阻断提示补关联

MV_VIDEO_TYPES = {"OfficialMV", "SpecialVideo"}

# 类型 → 目录标签（目录只分 Live / MV / MIX混剪 / Other；艺人主体与组合主体同口径）
_TYPE_DIR = {
    "OfficialMV": "MV",
    "Teaser": "MV",
    "SpecialVideo": "MV",
    "MixEdit": "MIX混剪",
    "PerformanceVideo": "Live",
    "Fancam": "Live",
    "SpecialStage": "Live",
    "CoverStage": "Live",
    # 合作舞台正常走 ②-2 短路（自带根目录），这里是纵深防御：万一将来短路被摘掉，
    # 也落 Live 而不是静默退化成 Other
    "CollabStage": "Live",
    "ShortVideo": "Live",
    "Other": "Other",
}

def _display_name(entity: Any) -> str:
    """实体展示名（用于入库目录命名）。

    艺人优先取艺名（stage_name），回退中文名 / 主名；其余实体（组合/歌曲）保持中文名优先，回退主名。
    """
    if isinstance(entity, Artist):
        return entity.stage_name or entity.chinese_name or entity.name
    return entity.chinese_name or entity.name


def _cleaned(parts: list[str]) -> Path:
    """各段清洗后拼目录；清洗失败的段用 _unknown 占位。"""
    return Path(*(_sanitize(p) or "_unknown" for p in parts))


def _session_of(mv: MusicVideo, db: Optional[Session]) -> Optional[Session]:
    """取 Session：显式传入优先，其次 mv 所属 Session（入库时 mv 已在 Session 内）。"""
    return db or object_session(mv)


def _personal_subject(mv: MusicVideo, session: Optional[Session] = None) -> Any:
    """个人口径主体（短视频/串烧/翻唱场景）：艺人优先，无艺人用组合。

    组合上溯到根母队（v3.2.42）：「磁盘归母队 + 数据层留分队」，
    小分队作品不另开顶层目录；艺人主体不受影响。
    """
    if mv.artists:
        return mv.artists[0]
    if mv.groups:
        return root_group(session, mv.groups[0])
    return None


# ===== 重名艺人目录消歧（v3.2.40）=====


class ArtistDirNameConflict(ValueError):
    """重名艺人缺少可区分的中文名 → 无法自动确定入库目录。

    调用方（入库 / 路径预览 / 更改存储路径）应当把它转成给用户的明确提示，
    而不是回退成「路径不适用」这种含糊结论。
    """

    def __init__(self, display: str, artist_id: Optional[int], reason: str) -> None:
        self.display = display
        self.artist_id = artist_id
        self.reason = reason or "缺少中文名"
        super().__init__(
            f"重名艺人「{display}」（艺人#{artist_id}）{self.reason}，"
            "无法自动确定入库目录：请先补全该艺人的中文名，或手动指定入库路径"
        )


@dataclass(frozen=True)
class ArtistDirName:
    """一位艺人在入库路径里使用的目录段名。"""

    artist_id: Optional[int]
    display: str  # 展示名（stage_name → chinese_name → name）
    dir_name: Optional[str]  # None = 重名且无法消歧（禁止自动入库）
    chinese_name: Optional[str] = None
    reason: Optional[str] = None  # dir_name 为 None 时的原因


def _artist_chinese_name(artist: Artist) -> Optional[str]:
    name = (artist.chinese_name or "").strip()
    return name or None


def artist_dir_names(db: Session) -> dict[int, ArtistDirName]:
    """全库「重名艺人 → 目录段名」表（v3.2.40 用户定稿）。

    展示名（stage_name → chinese_name → name）相同的艺人组成一个重名组，
    按 id 升序：**第一个保留展示名**，其后逐个追加中文名 —— `Yuna(유나)`。
    中文名缺失 / 与展示名相同 / 与该组内其他艺人的候选名撞车时，该艺人拿不到
    目录名（dir_name=None），自动入库前必须先补中文名。

    ⚠ 稳定性：排序只依赖 id，不依赖查询顺序；软删第一个会让后一位顶上成为
    「保留原名」的那个，此时存量文件会被数据体检的「路径与规则不符」提示归位。
    """
    rows = db.scalars(
        select(Artist).where(Artist.deleted_at.is_(None)).order_by(Artist.id)
    ).all()
    groups: dict[str, list[Artist]] = {}
    for artist in rows:
        groups.setdefault(_display_name(artist), []).append(artist)

    table: dict[int, ArtistDirName] = {}
    for display, members in groups.items():
        if len(members) == 1:
            only = members[0]
            table[only.id] = ArtistDirName(
                only.id, display, display, _artist_chinese_name(only)
            )
            continue
        # 重名组：首个保留展示名，其后逐个用中文名区分（候选名必须互不相同）
        plans: list[tuple[Artist, Optional[str], Optional[str]]] = [
            (members[0], display, None)
        ]
        used = {display}
        for artist in members[1:]:
            chinese = _artist_chinese_name(artist)
            if not chinese:
                plans.append((artist, None, "缺少中文名"))
                continue
            if chinese == display:
                plans.append((artist, None, "中文名与展示名相同，无法区分"))
                continue
            candidate = f"{display}({chinese})"
            if candidate in used:
                plans.append((artist, None, f"中文名「{chinese}」在该重名组内重复"))
                continue
            used.add(candidate)
            plans.append((artist, candidate, None))
        for artist, dir_name, reason in plans:
            table[artist.id] = ArtistDirName(
                artist.id, display, dir_name, _artist_chinese_name(artist), reason
            )
    return table


def _require_root_segment(entity: Any, names: Optional[dict[int, ArtistDirName]]) -> str:
    """「根层主体」目录段名：艺人走重名消歧表（无中文名 → 抛冲突），其余用展示名。"""
    if isinstance(entity, Artist):
        info = (names or {}).get(entity.id)
        if info is not None:
            if info.dir_name is None:
                raise ArtistDirNameConflict(
                    info.display, info.artist_id, info.reason or "缺少中文名"
                )
            return info.dir_name
    return _display_name(entity)


def _artist_segment(artist: Artist, names: Optional[dict[int, ArtistDirName]]) -> str:
    """非根层艺人段（成员直拍末段 / B站文件名主体）：能消歧就用消歧名，否则原名（不阻断）。

    这类位置下方还有组合（或 BV 号）兜底，同名不至于真的撞目录，
    所以只做「一致命名」，不因缺中文名阻断入库。
    """
    info = (names or {}).get(artist.id)
    if info is not None and info.dir_name:
        return info.dir_name
    return _display_name(artist)


def _bili_rename(
    mv: MusicVideo,
    source: Path,
    names: Optional[dict[int, ArtistDirName]] = None,
    db: Optional[Session] = None,
) -> Optional[str]:
    """B站视频入库重命名：yymmdd-主体-歌曲(BV号)；非 B站来源返回 None。

    主体取 auto_organize 同口径展示名（组合优先，成员直拍场景为成员名由目录体现，
    文件名主体仍取关联主体的第一名）；歌曲缺失时省略中间段。
    组合主体同样上溯到根母队（v3.2.42），与所在目录段保持一致。
    names 为重名艺人消歧表：艺人主体与目录段保持一致命名（缺中文名不阻断，BV 号已够区分）。
    """
    from app.services.bilibili_meta import extract_tail_date, is_bilibili_info

    info = getattr(mv, "_bili_info", None)
    if not isinstance(info, dict) or not is_bilibili_info(info):
        return None
    bvid = info.get("id")
    if not isinstance(bvid, str) or not bvid.strip():
        return None

    subject = None
    if mv.groups:
        subject = _display_name(root_group(db, mv.groups[0]))
    elif mv.artists:
        subject = _artist_segment(mv.artists[0], names)
    if not subject:
        return None

    date_part = (
        mv.performance_date.strftime("%y%m%d") if mv.performance_date else None
    )
    if not date_part:
        date_part = extract_tail_date(info.get("title") or "") or extract_tail_date(
            source.stem
        )
    song = _display_name(mv.songs[0]) if mv.songs else None

    parts = [p for p in (date_part, subject, song) if p]
    name = "-".join(parts) + f"({bvid.strip()})" + source.suffix.lower()
    return _sanitize(name) or None


def auto_organize_rel(
    mv: MusicVideo,
    db: Optional[Session] = None,
    artist_names: Optional[dict[int, ArtistDirName]] = None,
) -> Optional[Path]:
    """按定稿规则 v1.1 推导入库目录（相对 library_dir 的目录部分）；不适用返回 None。

    全系统唯一的路径决策实现：入库（ingest_video）、路径预览（preview-path）、
    更改存储路径（suggest_relocate_path）与存量重整共用本函数。

    db / artist_names：重名艺人消歧表（v3.2.40）的数据源。优先用显式传入的
    artist_names，其次用 db，再其次从 mv 所属 Session 推断（入库时 mv 已在
    Session 内）；都拿不到（游离对象 / 单测直构）时回退为「不做消歧」的展示名。

    组合主体段一律取**根母队**（v3.2.42 用户定稿：磁盘归母队 + 数据层留分队）：
    挂 EVOLution / LOVElution 的视频也落 `tripleS/`，同一母队不会散成多个顶层目录。
    顺带把「mv.groups 顺序不定」的隐患一并抹平 —— 同时挂母队与分队时上溯结果相同。

    ⚠ 重名艺人缺中文名而该艺人又是路径根层主体时抛 ArtistDirNameConflict。
    不希望抛异常的调用方（如数据体检只想知道路径）改用 tolerant_auto_organize_rel()。
    """
    session = _session_of(mv, db)
    names = (
        artist_names
        if artist_names is not None
        else (artist_dir_names(session) if session is not None else None)
    )
    from app.services.video_meta import normalize_video_types

    _, video_types = normalize_video_types(mv.video_types, mv.video_type)
    vt = set(video_types)

    # ②-0 MIX混剪：显式类型优先于短视频/串烧/翻唱/非表演（v1.3 只保留优先级短路，
    #   路径逻辑与粉丝直拍相同 → 不在这里组装目录，直接落下方常规路径）
    is_mix = MIX_VIDEO_TYPE in vt

    # ②-1 短视频 → 短视频/{艺人|组合}（is_short 只看类型是否含 ShortVideo，在入库入口派生）
    if mv.is_short and not is_mix:
        subject = _personal_subject(mv, session)
        if subject is not None:
            return _cleaned(["短视频", _require_root_segment(subject, names)])

    # ②-2 合作舞台 → 合作舞台[/歌曲]（v3.2.55 用户定稿：不带主体段）
    #   为什么没有主体段：合作舞台天然是多主体（A×B、跨团、跨代际），
    #   取任一主体都会把另一方的作品挪到错误的人名下 → 用固定根目录承载。
    #   为什么排在串烧之前：合作舞台常有多首歌，若排在串烧之后会被
    #   「歌曲≥2」抢走，落到 串烧/{第一个主体}/{日期}，既丢类型也丢合作语义。
    #   无主体不阻断：路径不依赖主体关联，未关联任何艺人/组合也能归位。
    if not is_mix and COLLAB_VIDEO_TYPE in vt:
        parts = ["合作舞台"]
        if mv.songs:
            parts.append(_display_name(mv.songs[0]))
        return _cleaned(parts)

    # ②-3 串烧（关联歌曲 ≥2）→ 串烧/{艺人|组合}/{表演日期yymmdd|未注日期}
    if not is_mix and len(mv.songs) >= 2:
        subject = _personal_subject(mv, session)
        if subject is not None:
            date_part = (
                mv.performance_date.strftime("%y%m%d")
                if mv.performance_date
                else "未注日期"
            )
            return _cleaned(
                ["串烧", _require_root_segment(subject, names), date_part]
            )

    # ②-3 翻唱 → 翻唱/{艺人|组合}/{歌曲}（歌曲缺失跳层）
    if not is_mix and COVER_VIDEO_TYPE in vt:
        subject = _personal_subject(mv, session)
        if subject is None:
            # 翻唱类型必须进翻唱根：无主体不滑落到常规路径
            return None
        parts = ["翻唱", _require_root_segment(subject, names)]
        if mv.songs:
            parts.append(_display_name(mv.songs[0]))
        return _cleaned(parts)

    # ②-5 非表演视频 → 非表演视频/{艺人|组合}（艺人优先：直拍等艺人相关视频挂艺人，无艺人才落组合）
    #    与翻唱同口径按类型触发：Other 类型或非表演标记（幕后/预告等 AI 标记）都进非表演根
    if not is_mix and vt & {"Other", "SpecialVideo", "Teaser"}:
        subject = mv.artists[0] if mv.artists else root_group(session, mv.groups[0] if mv.groups else None)
        if subject is None:
            # 非表演类型必须进非表演根：无主体不滑落到常规路径
            return None
        return _cleaned(["非表演视频", _require_root_segment(subject, names)])

    # ===== 常规路径（主体：组合 > solo 艺人 > 仅艺人）=====
    # 组合主体段取根母队（v3.2.42）→ 组合直拍/舞台/MV 与成员直拍都归在母队名下
    group_entity = root_group(session, mv.groups[0]) if mv.groups else None
    group_name = _display_name(group_entity) if group_entity is not None else None
    song_name = _display_name(mv.songs[0]) if mv.songs else None
    first_artist = mv.artists[0] if mv.artists else None

    # 艺人主体（is_solo 或无组合）→ 艺人[/歌曲]/{类型标签}
    # （solo 直拍归艺人名下，与「组合成员个人直拍归组合」的口径区分）
    # 艺人在路径根层：重名且缺中文名 → 抛冲突（要求补中文名，或手动指定路径）
    artist_subject = (
        _require_root_segment(first_artist, names)
        if first_artist is not None and (mv.is_solo or not group_name)
        else None
    )
    if artist_subject:
        kind = next((_TYPE_DIR[t] for t in video_types if t in _TYPE_DIR), "Other")
        parts = [artist_subject]
        if song_name:
            parts.append(song_name)
        parts.append(kind)
        return _cleaned(parts)

    # 成员直拍（有组合 + 成员，非 solo）→ 组合[/歌曲]/成员名
    #   取消类型限制：只要能区分出组合与成员，就落到成员名下（官方舞台/MV 若也带成员，一并归属成员）
    #   成员名不是路径根层（上方有组合兜底），重名只做统一命名、不阻断入库
    if group_name and first_artist is not None:
        member_name = _artist_segment(first_artist, names)
        if member_name:
            parts = [group_name]
            if song_name:
                parts.append(song_name)
            parts.append(member_name)
            return _cleaned(parts)

    # 组合主体（组合直拍/舞台/MV/现场等）→ 组合[/歌曲]/{MV|Live}
    if group_name:
        kind = "MV" if (vt & MV_VIDEO_TYPES) else "Live"
        parts = [group_name]
        if song_name:
            parts.append(song_name)
        parts.append(kind)
        return _cleaned(parts)

    return None


def tolerant_auto_organize_rel(
    mv: MusicVideo,
    db: Optional[Session] = None,
    artist_names: Optional[dict[int, ArtistDirName]] = None,
) -> Optional[Path]:
    """auto_organize_rel 的容忍版：重名艺人缺中文名时返回 None，不抛异常。"""
    try:
        return auto_organize_rel(mv, db=db, artist_names=artist_names)
    except ArtistDirNameConflict:
        return None


# ===== 已入库视频更改存储路径 =====

def suggest_relocate_path(
    mv: MusicVideo,
    db: Optional[Session] = None,
    artist_names: Optional[dict[int, ArtistDirName]] = None,
) -> Optional[Path]:
    """按视频当前元数据推导「更改存储路径」的目标完整相对路径（目录 + 原文件名）。

    与入库自动整理同口径：auto_organize_rel 推导目录后做大小写归并匹配
    （已有 Twice/ 时不建议 TWICE/）；规则不适用返回 None。
    重名艺人缺中文名时抛 ArtistDirNameConflict（由调用方转成 400 提示）。
    仅对已入库（正式库）且带相对路径的视频给出建议。

    artist_names：批量场景（存量重整列表）可预计算消歧表复用，避免逐条查库。
    """
    if mv.ingestion_status != "library":
        return None
    raw = (mv.file_path or "").strip()
    if not raw:
        return None
    source_name = Path(raw.replace("\\", "/")).name
    auto_dir = auto_organize_rel(mv, db=db, artist_names=artist_names)
    if auto_dir is None:
        return None
    auto_dir = match_existing_dir(auto_dir)
    safe_name = _sanitize(source_name) or source_name
    return auto_dir / safe_name


def relocate_video_file(
    db: Session, mv: MusicVideo, *, destination_rel: Optional[str] = None
) -> dict[str, Any]:
    """把已入库视频移动到新位置，并一并迁移同目录下的同名伴随文件。

    destination_rel 为空时按自动整理规则推导目标路径（与入库同规则）。
    移动复用 move_to_library：含同名冲突处理、哈希校验、FileMoveLog 记录；
    失败时回滚 DB 并抛 ValueError（文件保持原位）。成功后提交事务。
    """
    if mv.ingestion_status != "library":
        raise ValueError("仅已入库（正式库）的视频支持更改存储路径")
    current_raw = (mv.file_path or "").strip()
    if not current_raw:
        raise ValueError("该视频没有记录存储路径，无法移动")
    source_abs = resolve_video_abs_path(mv)
    if source_abs is None or not source_abs.exists():
        raise FileNotFoundError(
            f"源文件不存在或不可访问: {current_raw}（请检查正式库卷是否已挂载）"
        )

    if destination_rel and destination_rel.strip():
        # 用户手动指定目标完整相对路径：与入库同一套清洗校验
        rel = Path(destination_rel.strip().replace("\\", "/"))
        if rel.is_absolute():
            raise ValueError("目标路径必须是相对路径（相对正式库目录），请勿填写绝对路径")
        resolved = (settings.library_dir / rel).resolve()
        if not resolved.is_relative_to(settings.library_dir.resolve()):
            raise ValueError("目标路径不能超出正式库目录范围")
        dest_rel = rel
    else:
        suggested = suggest_relocate_path(mv, db=db)
        if suggested is None:
            raise ValueError(
                "自动整理规则不适用（缺少组合/歌曲等关联信息），请手动指定目标路径"
            )
        dest_rel = suggested

    current_norm = Path(current_raw.replace("\\", "/")).as_posix()
    dest_norm = dest_rel.as_posix()
    if current_norm == dest_norm:
        raise ValueError("新路径与当前路径相同，无需移动")

    dest_abs, log = move_to_library(
        db,
        source_abs,
        dest_rel,
        music_video_id=mv.id,
        move=True,
        source_hash=mv.file_hash,
    )
    if log.status not in ("moved", "copied", "reused"):
        db.rollback()
        raise ValueError(f"文件移动失败: {log.message or log.status}")

    previous_path = mv.file_path or ""
    # move_to_library 返回的是 resolve() 后的绝对路径，这里同样 resolve 再取相对，
    # 避免正式库路径含软链（如 macOS /var -> /private/var）时 relative_to 失败
    mv.file_path = str(dest_abs.relative_to(settings.library_dir.resolve()).as_posix())

    # 一并迁移伴随文件：yt-dlp 的 info.json 与同名封面图，保持「视频 + 伴随」不分离
    moved_sidecars: list[str] = []
    sidecar_candidates = [
        source_abs.parent / f"{source_abs.name}.info.json",
        source_abs.parent / f"{source_abs.stem}.info.json",
    ]
    cover = find_sidecar_cover(source_abs)
    if cover is not None:
        sidecar_candidates.append(cover)
    seen: set[Path] = set()
    for sidecar in sidecar_candidates:
        try:
            if not sidecar.is_file() or sidecar.resolve() in seen:
                continue
        except OSError:
            continue
        seen.add(sidecar.resolve())
        target = dest_abs.parent / sidecar.name
        if target.exists() or target.resolve() == sidecar.resolve():
            continue
        try:
            shutil.move(str(sidecar), str(target))
            moved_sidecars.append(sidecar.name)
        except OSError:
            logger.warning("伴随文件迁移失败（保留原位）: %s", sidecar)

    db.commit()
    logger.info(
        "视频 %s 存储路径变更: %s -> %s（伴随文件 %d 个）",
        mv.id, previous_path, mv.file_path, len(moved_sidecars),
    )
    return {
        "previous_path": previous_path,
        "file_path": mv.file_path,
        "moved_sidecars": moved_sidecars,
    }


# ===== 存量重整：按现行规则批量纠偏 =====


def plan_reorganize(db: Session) -> list[dict[str, Any]]:
    """存量重整 dry-run：列出已入库视频中「当前路径 ≠ 规则建议路径」的记录。

    只读不写库。规则不适用（缺主体关联）的记录标记 rule_missed=True，
    需补全关联后重试；文件留在 incoming 的记录（绝对路径）不参与。
    """
    videos = list(
        db.scalars(
            select(MusicVideo).where(
                MusicVideo.deleted_at.is_(None),
                MusicVideo.ingestion_status == "library",
            )
        ).all()
    )
    items: list[dict[str, Any]] = []
    # 消歧表在整批内只算一次（重名艺人缺中文名 → 该条按「规则不适用」处理，不中断整批）
    names = artist_dir_names(db)
    for mv in videos:
        raw = (mv.file_path or "").strip()
        if not raw:
            continue
        current = Path(raw.replace("\\", "/"))
        if current.is_absolute():
            continue  # 文件留 incoming 的记录（绝对路径）不参与重整
        try:
            suggested = suggest_relocate_path(mv, db=db, artist_names=names)
        except ArtistDirNameConflict:
            suggested = None
        if suggested is None:
            if posix_dir_of(raw):
                # 已有目录结构但规则算不出建议：多半是缺主体关联
                items.append(
                    {
                        "id": mv.id,
                        "name": mv.name,
                        "current_path": current.as_posix(),
                        "suggested_path": None,
                        "rule_missed": True,
                    }
                )
            continue
        if suggested.as_posix() != current.as_posix():
            items.append(
                {
                    "id": mv.id,
                    "name": mv.name,
                    "current_path": current.as_posix(),
                    "suggested_path": suggested.as_posix(),
                    "rule_missed": False,
                }
            )
    return items


def execute_reorganize(db: Session, ids: list[int]) -> dict[str, Any]:
    """按规则建议路径批量移动已入库视频（复用 relocate_video_file 的全部防护）。

    单条失败回滚该条并继续，不影响其他条目；返回逐条结果。
    """
    results: list[dict[str, Any]] = []
    moved = 0
    for vid in ids:
        mv = db.get(MusicVideo, vid)
        if mv is None or mv.deleted_at is not None:
            db.rollback()
            results.append({"id": vid, "ok": False, "error": "记录不存在或已删除"})
            continue
        try:
            r = relocate_video_file(db, mv)
            results.append(
                {
                    "id": vid,
                    "ok": True,
                    "previous_path": r["previous_path"],
                    "file_path": r["file_path"],
                }
            )
            moved += 1
        except (ValueError, FileNotFoundError) as e:
            db.rollback()
            results.append({"id": vid, "ok": False, "error": str(e)})
    return {"moved": moved, "failed": len(results) - moved, "results": results}


def ingest_video(db: Session, request: IngestRequest) -> IngestResult:
    """入库：创建 MusicVideo + 移动文件 + 截封面。

    原子性约定：
    - 文件移动失败 → 回滚 DB（不留下指向不存在文件的记录）
    - DB 提交失败但文件已移动 → 把文件回移 incoming（不留孤儿文件）
    - 源文件必须在 incoming 目录内（防止把服务器任意文件移入正式库）
    """
    source = Path(request.file_path).resolve()
    if not source.exists():
        raise FileNotFoundError(f"源文件不存在: {source}")
    if settings.resolve_incoming_root(source) is None:
        raise ValueError(
            f"入库源文件必须在待整理（incoming）目录内: {source}"
        )

    # B站来源：读 NFO/json 元数据辅助重命名与 source 回填（不写库表字段，仅入库过程使用）
    bili_info: Optional[dict] = None
    if not source.with_suffix(".info.json").exists():
        try:
            from app.services.bilibili_meta import read_bilibili_meta

            bili_info = read_bilibili_meta(source)
        except Exception as e:  # noqa: BLE001
            logger.warning("B站元数据读取失败（不影响入库）：%s", e)

    mv_data = request.music_video

    from app.services.video_meta import normalize_video_types

    video_type, video_types = normalize_video_types(mv_data.video_types, mv_data.video_type)

    # 0. 直接 FK active 校验：禁止指向已软删除的 Song / Artist
    if mv_data.song_id is not None and not Song.get_active(db, mv_data.song_id):
        raise ValueError(f"关联 Song 不存在或已删除 (id={mv_data.song_id})")
    if (
        mv_data.subject_artist_id is not None
        and not Artist.get_active(db, mv_data.subject_artist_id)
    ):
        raise ValueError(f"关联 Artist(subject) 不存在或已删除 (id={mv_data.subject_artist_id})")

    # 1. 计算哈希（若未提供）
    file_hash = None
    try:
        file_hash = compute_file_hash(source)
    except OSError:
        pass

    # 1.5 去重预检（仅对活跃记录；软删除记录允许重建，不阻断入库）
    #    提前校验避免文件移动后才因唯一索引失败，留下孤儿文件。
    if file_hash:
        existing = db.scalar(
            select(MusicVideo)
            .where(
                MusicVideo.file_hash == file_hash,
                MusicVideo.deleted_at.is_(None),
            )
            .limit(1)
        )
        if existing is not None:
            raise ValueError(
                f"该文件已入库（重复 file_hash），music_video_id={existing.id}"
            )
    if mv_data.source_platform and mv_data.source_id:
        existing = db.scalar(
            select(MusicVideo)
            .where(
                MusicVideo.source_platform == mv_data.source_platform,
                MusicVideo.source_id == mv_data.source_id,
                MusicVideo.deleted_at.is_(None),
            )
            .limit(1)
        )
        if existing is not None:
            raise ValueError(
                f"该平台视频已入库（重复 source_platform+source_id），"
                f"music_video_id={existing.id}"
            )

    # 2. 写数据库记录（先入库元数据，文件移动失败可回滚）
    is_short = mv_data.is_short
    if is_short is None:
        # 短视频判定唯一口径：只看 video_types 是否含 ShortVideo，不再用「时长 < 70 秒」兜底
        # （否则 70 秒以内的 MIX 混剪 / 预告会被塞进短视频列表）
        is_short = is_short_video(video_types, video_type)
    # B站来源回填：用户未显式填 source 时，用 NFO/json 的 BV 号与链接补齐
    source_platform = mv_data.source_platform
    source_id = mv_data.source_id
    source_url = mv_data.source_url
    if bili_info and not source_platform:
        source_platform = "bilibili"
        if not source_id and isinstance(bili_info.get("id"), str):
            source_id = bili_info["id"]
        if not source_url and isinstance(bili_info.get("webpage_url"), str):
            source_url = bili_info["webpage_url"]

    mv = MusicVideo(
        name=mv_data.name,
        original_title=mv_data.original_title,
        chinese_name=mv_data.chinese_name,
        english_name=mv_data.english_name,
        korean_name=mv_data.korean_name,
        aliases=mv_data.aliases,
        song_id=None,
        video_type=video_type,
        video_types=video_types,
        subject_artist_id=mv_data.subject_artist_id,
        event_name=mv_data.event_name,
        performance_date=mv_data.performance_date,
        release_date=mv_data.release_date,
        published_date=mv_data.published_date,
        source_platform=source_platform,
        source_id=source_id,
        source_url=source_url,
        original_uploader=mv_data.original_uploader,
        duration=mv_data.duration,
        width=mv_data.width,
        height=mv_data.height,
        video_codec=mv_data.video_codec,
        audio_codec=mv_data.audio_codec,
        frame_rate=mv_data.frame_rate,
        bitrate=mv_data.bitrate,
        file_size=get_file_size(source),
        file_name=source.name,
        file_hash=file_hash,
        file_path=str(source),  # 临时，移动后更新
        external_links=mv_data.external_links,
        description=mv_data.description,
        chinese_description=mv_data.chinese_description,
        is_solo=mv_data.is_solo,
        is_short=is_short,
        ingestion_status="library",
    )
    db.add(mv)
    db.flush()  # 拿到 id
    # B站元数据挂载：仅供 _bili_rename 在本次入库内读取（不入库表字段）
    if bili_info:
        mv._bili_info = bili_info

    # 同步多对多关联（仅关联未软删除实体）
    apply_video_song_tracks(
        db,
        mv,
        tracks=getattr(mv_data, "tracks", None),
        song_ids=mv_data.song_ids
        or ([mv_data.song_id] if mv_data.song_id else None),
        album_ids=mv_data.album_ids,
    )
    if mv_data.artist_ids:
        mv.artists = resolve_active_ids(db, Artist, mv_data.artist_ids)
    if mv_data.group_ids:
        mv.groups = resolve_active_ids(db, Group, mv_data.group_ids)

    # 3. 移动文件
    moved = False
    destination_abs: Optional[Path] = None
    if request.move_file:
        if request.destination_rel and request.destination_rel.strip():
            # 用户手动指定了入库路径：仅作基本清洗，保留用户选择
            rel = Path(request.destination_rel.strip().replace("\\", "/"))
            if rel.is_absolute():
                raise ValueError("入库路径必须是相对路径（相对正式库目录），请勿填写绝对路径")
            resolved = (settings.library_dir / rel).resolve()
            if not resolved.is_relative_to(settings.library_dir.resolve()):
                raise ValueError("入库路径不能超出正式库目录范围")
        else:
            # 自动整理规则（定稿 v1.1：三步判定模型，与 preview / 更改存储路径同一实现）
            # 重名艺人消歧表（v3.2.40）：根层主体是「重名且缺中文名」的艺人时，
            # auto_organize_rel 抛 ArtistDirNameConflict → 直接阻断入库（400 回给用户）
            names = artist_dir_names(db)
            auto_dir = auto_organize_rel(mv, db=db, artist_names=names)
            if auto_dir is None:
                # 无主体关联（艺人/组合皆无）阻断入库，不再平铺占位目录
                raise ValueError(
                    "无法自动归档：请至少关联一位艺人或一个组合后再入库"
                )
            # 大小写不敏感匹配正式库已有目录：已有 Twice/ 时不新建 TWICE/
            auto_dir = match_existing_dir(auto_dir)
            safe_name = _bili_rename(mv, source, names, db) or (
                _sanitize(source.name) or source.name
            )
            rel = auto_dir / safe_name
        destination_abs, log = move_to_library(
            db, source, rel, music_video_id=mv.id, move=True, source_hash=file_hash
        )
        if log.status not in ("moved", "copied", "reused"):
            # 移动/校验失败：文件仍在原地，回滚 DB 记录，
            # 不能留下指向不存在文件的悬空记录
            db.rollback()
            raise ValueError(f"文件移动失败: {log.message or log.status}")
        moved = True
        lib_root = settings.library_dir.resolve()
        dest_res = destination_abs.resolve()
        try:
            mv.file_path = str(dest_res.relative_to(lib_root).as_posix())
        except ValueError:
            db.rollback()
            if dest_res.exists():
                try:
                    shutil.move(str(dest_res), str(source))
                except Exception:  # noqa: BLE001
                    logger.exception("入库路径不在正式库内，回移失败: %s", dest_res)
            raise ValueError("入库目标不在正式库目录内") from None
    else:
        # move_file=False：文件留在 incoming，file_path 写绝对路径
        # （_resolve_file_path 对绝对路径不查 status，播放不受影响）；
        # ingestion_status 仍为 "library"——前端主列表按 library 过滤，
        # 改为 "incoming" 会导致视频不可见
        mv.file_path = str(source)

    # 4. 转移伴随文件：同名封面跟视频进正式库，并复制一份作缩略图；info.json 一并归档
    thumbnail_ready = False
    try:
        cover_src = find_sidecar_cover(source)
        if cover_src is not None:
            sidecar_dst: Optional[Path] = None
            if request.move_file and destination_abs is not None:
                sidecar_dst = destination_abs.with_name(
                    f"{destination_abs.stem}{cover_src.suffix}"
                )
                sidecar_dst.parent.mkdir(parents=True, exist_ok=True)
                if sidecar_dst.resolve() != cover_src.resolve():
                    if sidecar_dst.exists():
                        sidecar_dst.unlink()
                    shutil.copy2(str(cover_src), str(sidecar_dst))
            thumb_rel = Path("thumbnails") / f"{mv.id}{cover_src.suffix.lower()}"
            thumb_abs = settings.thumbnail_dir.parent / thumb_rel
            thumb_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(cover_src), str(thumb_abs))
            mv.thumbnail_path = str(thumb_rel)
            thumbnail_ready = True
            try:
                from app.services.video_thumb import source_fingerprint, write_stamp

                write_stamp(mv.id, source_fingerprint(cover_src, "sidecar"))
            except OSError:
                pass
            if (
                request.move_file
                and sidecar_dst is not None
                and cover_src.resolve() != sidecar_dst.resolve()
            ):
                cover_src.unlink(missing_ok=True)
            logger.info(
                "同名封面已随视频入库 mv=%s sidecar=%s thumb=%s",
                mv.id,
                sidecar_dst or cover_src,
                thumb_abs,
            )

        info_src = source.with_suffix(".info.json")
        if (
            info_src.exists()
            and request.move_file
            and destination_abs is not None
        ):
            json_dst = destination_abs.parent / f"{destination_abs.stem}.info.json"
            json_dst.parent.mkdir(parents=True, exist_ok=True)
            if json_dst.exists():
                json_dst.unlink()
            shutil.move(str(info_src), str(json_dst))
            logger.info("转移元数据 json 到库 mv=%s: %s", mv.id, json_dst)

        # B站 NFO/json 元数据随行归档（与视频同名转移，便于日后溯源）
        if request.move_file and destination_abs is not None:
            for meta_src in (
                source.with_suffix(".nfo"),
                Path(str(source) + ".nfo"),
                source.with_suffix(".json"),
                Path(str(source) + ".json"),
            ):
                try:
                    if not meta_src.is_file():
                        continue
                except OSError:
                    continue
                meta_dst = destination_abs.parent / f"{destination_abs.stem}{meta_src.suffix}"
                if meta_dst.exists():
                    meta_dst.unlink()
                shutil.move(str(meta_src), str(meta_dst))
                logger.info("转移 B站元数据到库 mv=%s: %s", mv.id, meta_dst)
                break
    except Exception as e:  # noqa: BLE001
        logger.warning("伴随文件转移失败 mv=%s: %s", mv.id, e)

    # 5. 截封面：仅当没有可用的下载封面时才从视频帧提取（失败不影响入库）
    if not thumbnail_ready:
        try:
            thumb_rel = (
                Path("thumbnails") / f"{mv.id}.jpg"
            )
            thumb_abs = settings.thumbnail_dir.parent / thumb_rel
            extract_thumbnail(mv.file_path if Path(mv.file_path).is_absolute() else settings.library_dir / mv.file_path, thumb_abs)
            mv.thumbnail_path = str(thumb_rel)
            try:
                from app.services.video_thumb import source_fingerprint, write_stamp

                src_abs = (
                    Path(mv.file_path)
                    if Path(mv.file_path).is_absolute()
                    else settings.library_dir / mv.file_path
                )
                write_stamp(mv.id, source_fingerprint(src_abs, "frame"))
            except OSError:
                pass
        except Exception as e:  # noqa: BLE001
            logger.warning("封面截取失败 mv=%s: %s", mv.id, e)

    try:
        db.commit()
    except Exception:
        # 补偿：文件已物理移入正式库而 DB 提交失败（锁/约束冲突）时，
        # 把文件移回 incoming，避免「源丢失 + 库目录孤儿文件」两头不讨好
        db.rollback()
        if moved and destination_abs is not None and destination_abs.exists():
            try:
                shutil.move(str(destination_abs), str(source))
                logger.warning("入库提交失败，已将文件回移至 %s", source)
            except Exception:  # noqa: BLE001
                logger.exception("入库回移失败，正式库遗留文件: %s", destination_abs)
        raise

    _drop_incoming_file_row(db, source)

    return IngestResult(
        music_video_id=mv.id,
        moved=moved,
        destination_path=str(destination_abs) if destination_abs else None,
    )


def _drop_incoming_file_row(db: Session, source: Path) -> None:
    """入库成功后删掉 incoming_files 缓存行，避免待整理列表残留幽灵项。"""
    from sqlalchemy import delete as sql_delete

    from app.models.incoming_file import IncomingFile

    keys = {str(source)}
    try:
        keys.add(str(source.resolve()))
    except OSError:
        pass
    try:
        db.execute(sql_delete(IncomingFile).where(IncomingFile.file_path.in_(list(keys))))
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("删除 incoming_files 缓存失败 path=%s", source, exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


# ===== 失效视频清理：磁盘文件已删、库记录仍在 =====


def posix_dir_of(file_path: Optional[str]) -> str:
    """视频 file_path 的目录部分（POSIX，相对正式库）。文件在根目录则空串。"""
    raw = _strip_dot_slash_prefix((file_path or "").replace("\\", "/").strip())
    if not raw:
        return ""
    if "/" not in raw:
        return ""
    return raw.rsplit("/", 1)[0]


def aggregate_folder_browse(
    rows: Iterable[tuple[int, Optional[str]]],
    prefix: str,
) -> tuple[list[dict[str, Any]], list[int]]:
    """根据全部库内路径聚合某一层文件夹。

    rows 应按「新到旧」排列，这样 cover_id 取该文件夹下最新一条。
    返回 (子文件夹列表, 当前目录下的视频 id 列表，顺序与 rows 一致)。
    """
    prefix = (prefix or "").replace("\\", "/").strip("/")
    folders: dict[str, dict[str, Any]] = {}
    video_ids: list[int] = []
    for vid, fp in rows:
        d = posix_dir_of(fp)
        if prefix:
            if d == prefix:
                video_ids.append(vid)
                continue
            head = prefix + "/"
            if not d.startswith(head):
                continue
            child = d[len(head) :].split("/", 1)[0]
        else:
            if d == "":
                video_ids.append(vid)
                continue
            child = d.split("/", 1)[0]
        if not child:
            continue
        rec = folders.get(child)
        if rec is None:
            folders[child] = {"name": child, "count": 1, "cover_id": vid}
        else:
            rec["count"] += 1
    folder_list = sorted(folders.values(), key=lambda x: str(x["name"]).lower())
    return folder_list, video_ids


def resolve_video_abs_path(mv: MusicVideo) -> Optional[Path]:
    """把 MusicVideo.file_path 解析为绝对路径；路径缺失返回 None。

    与播放/封面接口同一口径：绝对路径原样使用，相对路径按
    ingestion_status 拼到 library_dir 或 incoming_dir。
    """
    raw = (mv.file_path or "").strip()
    if not raw:
        return None
    p = Path(raw.replace("\\", "/"))
    if p.is_absolute():
        return p
    base = (
        settings.library_dir
        if mv.ingestion_status == "library"
        else settings.incoming_dir
    )
    return base / p


def _strip_dot_slash_prefix(rel: str) -> str:
    """去掉前导 ./ 与多余 /，不使用 lstrip('./')（会把 .hidden 吃成 hidden）。"""
    normalized = (rel or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _rel_key(rel: str) -> str:
    normalized = _strip_dot_slash_prefix(rel)
    return normalized.casefold() if os.name == "nt" else normalized


def _dir_is_accessible(root: Path) -> bool:
    try:
        return root.is_dir()
    except OSError:
        return False


def _dir_has_entries(root: Path) -> bool:
    """目录是否可访问且非空。空目录或不可访问视为「可能未挂载」。"""
    try:
        if not root.is_dir():
            return False
        with os.scandir(root) as it:
            for _ in it:
                return True
        return False
    except OSError:
        return False


# 正式库相对路径记录 ≥ 该数，且磁盘上扫到的视频不足 20%，视为半挂载
_MOUNT_GUARD_MIN_RECORDS = 3
_MOUNT_GUARD_INDEX_RATIO = 0.2


def _library_looks_unmounted(
    library_index: Optional[set[str]], library_rel_count: int
) -> bool:
    """有库记录但几乎扫不到视频：空目录、半挂载、或目录不可访问。"""
    if library_rel_count <= 0:
        return False
    if library_index is None:
        return True
    indexed = len(library_index)
    if indexed == 0:
        return True
    if library_rel_count >= _MOUNT_GUARD_MIN_RECORDS and indexed < max(
        1, int(library_rel_count * _MOUNT_GUARD_INDEX_RATIO)
    ):
        return True
    return False


def _index_videos_on_disk(root: Path) -> Optional[set[str]]:
    """扫描 root 下视频文件，返回相对 POSIX 路径集合。

    目录不可访问返回 None；可访问但没有视频返回空集合。
    """
    try:
        if not root.is_dir():
            return None
        found: set[str] = set()
        for p in root.rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                    found.add(_rel_key(p.relative_to(root).as_posix()))
            except OSError:
                continue
        return found
    except OSError:
        return None


def _is_stored_absolute(file_path: Optional[str]) -> bool:
    if not file_path:
        return False
    return Path(file_path.replace("\\", "/")).is_absolute()


def _path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _file_exists(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def collect_missing_videos(
    db: Session, *, respect_mount_guard: bool = True
) -> tuple[list[MusicVideo], list[str], int]:
    """找出活跃记录中文件已不存在的 MusicVideo。

    挂载防护：正式库目录完全空（或不可访问）且库内仍有相对路径记录时，
    视为 NAS/存储未挂载，不把全部记录当失效，避免误删整个目录。
    返回 (缺失列表, 被防护跳过的根路径, 扫描条数)。
    """
    videos = list(
        db.scalars(select(MusicVideo).where(MusicVideo.deleted_at.is_(None))).all()
    )
    library_index = _index_videos_on_disk(settings.library_dir)
    incoming_index = _index_videos_on_disk(settings.incoming_dir)

    library_rel_count = sum(
        1
        for mv in videos
        if mv.ingestion_status == "library" and not _is_stored_absolute(mv.file_path)
    )
    library_guard = bool(
        respect_mount_guard
        and _library_looks_unmounted(library_index, library_rel_count)
    )
    incoming_guard = bool(
        respect_mount_guard and not _dir_is_accessible(settings.incoming_dir)
    )

    unmounted: list[str] = []
    if library_guard:
        unmounted.append(str(settings.library_dir))
        logger.warning(
            "正式库疑似未挂载或半挂载（记录 %s / 扫到视频 %s），跳过失效清理: %s",
            library_rel_count,
            0 if library_index is None else len(library_index),
            settings.library_dir,
        )
    if incoming_guard:
        unmounted.append(str(settings.incoming_dir))
        logger.warning(
            "待整理目录不可访问，跳过其路径上的失效判定: %s",
            settings.incoming_dir,
        )

    missing: list[MusicVideo] = []
    for mv in videos:
        abs_path = resolve_video_abs_path(mv)
        if abs_path is None:
            missing.append(mv)
            continue

        use_library_index = (
            mv.ingestion_status == "library" and not _is_stored_absolute(mv.file_path)
        )
        if use_library_index:
            if library_guard:
                continue
            if library_index is None:
                continue
            if _rel_key(mv.file_path or "") not in library_index:
                missing.append(mv)
            continue

        under_incoming = _path_under(abs_path, settings.incoming_dir)
        if incoming_guard and under_incoming:
            continue

        # 绝对路径 / incoming / archived：直接 stat；incoming 为空是常态
        if library_guard and _path_under(abs_path, settings.library_dir):
            continue
        if incoming_index is not None and mv.ingestion_status == "incoming" and not _is_stored_absolute(mv.file_path):
            if _rel_key(mv.file_path or "") not in incoming_index:
                missing.append(mv)
            continue
        if not _file_exists(abs_path):
            missing.append(mv)

    return missing, unmounted, len(videos)


def _purge_derived_for(db: Session, mv: MusicVideo) -> None:
    """用户确认后：删除失效视频的缩略图、伴随 json/封面、转码缓存。

    同名 sidecar 仅在视频文件确认不存在时删除，避免误判缺失时把还在的
    info.json / 封面一起删掉。不删除 library/incoming 里的视频文件本身。
    """
    if mv.thumbnail_path:
        thumb = Path(mv.thumbnail_path)
        if not thumb.is_absolute():
            thumb = settings.derived_dir / thumb
        if _path_under(thumb, settings.derived_dir):
            try:
                thumb.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        for ext in _COVER_EXTS:
            p = settings.thumbnail_dir / f"{mv.id}{ext}"
            if _path_under(p, settings.derived_dir):
                p.unlink(missing_ok=True)
        stamp = settings.thumbnail_dir / f"{mv.id}.src"
        if _path_under(stamp, settings.derived_dir):
            stamp.unlink(missing_ok=True)
    except OSError:
        pass

    # 列表位的封面缩放变体：derived/video-thumb-cache/{id}-{mtime}-wNNN[-qNN].jpg
    # 文件名以「源封面 stem（= 视频 id）」开头，按 "id-" 前缀精确匹配即可，
    # 不会误伤 id=1 与 id=12 这种前缀包含关系（后面紧跟的是分隔符）。
    try:
        variant_dir = settings.derived_dir / VIDEO_THUMB_CACHE_DIRNAME
        if variant_dir.is_dir():
            for p in variant_dir.glob(f"{mv.id}-*"):
                if _path_under(p, settings.derived_dir):
                    p.unlink(missing_ok=True)
    except OSError:
        pass

    abs_path = resolve_video_abs_path(mv)
    video_gone = abs_path is None or not _file_exists(abs_path)
    if video_gone and abs_path is not None:
        try:
            stem = abs_path.with_suffix("")
            for suffix in _SIDECAR_SUFFIXES:
                side = Path(str(stem) + suffix)
                if _path_under(side, settings.library_dir) or _path_under(
                    side, settings.incoming_dir
                ):
                    side.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        from app.services.transcode_manager import transcode_manager

        transcode_manager.purge_cache_for(db, mv.id)
    except Exception:  # noqa: BLE001
        logger.warning("清理转码缓存失败 mv=%s", mv.id, exc_info=True)


def _cleanup_result(
    *,
    scanned: int,
    cleaned: int,
    missing: int,
    unmounted: list[str],
    items: list[dict[str, Any]],
    files_purged: int = 0,
) -> dict[str, Any]:
    return {
        "scanned": scanned,
        "cleaned": cleaned,
        "missing": missing,
        "skipped_unmounted": bool(unmounted),
        "unmounted_roots": unmounted,
        "items": items,
        "files_purged": files_purged,
    }


def _missing_item(mv: MusicVideo) -> dict[str, Any]:
    return {
        "id": mv.id,
        "name": mv.name,
        "file_path": mv.file_path,
        "ingestion_status": mv.ingestion_status,
    }


def cleanup_missing_videos(
    db: Session,
    *,
    dry_run: bool = False,
    respect_mount_guard: bool = True,
    purge_files: bool = False,
) -> dict[str, Any]:
    """扫描并（可选）软删除文件已不存在的视频记录。

    dry_run=True 只报告不写库。
    默认不删任何本地视频/图片/json；purge_files=True 才删缩略图与
    （仅当视频文件确认不存在时的）伴随文件，必须由用户确认后的接口传入。
    """
    global _last_cleanup_at

    missing, unmounted, scanned = collect_missing_videos(
        db, respect_mount_guard=respect_mount_guard
    )
    items = [_missing_item(mv) for mv in missing]
    if dry_run or not missing:
        if not dry_run:
            _last_cleanup_at = time.monotonic()
        return _cleanup_result(
            scanned=scanned,
            cleaned=0,
            missing=len(missing),
            unmounted=unmounted,
            items=items,
        )

    files_purged = 0
    for mv in missing:
        if purge_files:
            try:
                _purge_derived_for(db, mv)
                files_purged += 1
            except Exception:  # noqa: BLE001
                logger.warning("清理派生产物失败 mv=%s", mv.id, exc_info=True)
        mv.soft_delete()

    db.commit()
    _last_cleanup_at = time.monotonic()
    logger.info(
        "清理失效视频：软删除 %s 条 purge_files=%s（扫描 %s 条）",
        len(missing),
        purge_files,
        scanned,
    )
    return _cleanup_result(
        scanned=scanned,
        cleaned=len(missing),
        missing=len(missing),
        unmounted=unmounted,
        items=items,
        files_purged=files_purged,
    )


def maybe_cleanup_missing_videos(db: Session) -> Optional[dict[str, Any]]:
    """列表/统计接口的挂钩。不再自动软删或删文件，必须在设置里确认。"""
    return None


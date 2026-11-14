"""机位（camera_angle）与场次（session_label）的单一权威词表。

## 机位词表来源
`idolmatrix-ingest/rules.py` 的 `CAMRE` / `CAM_DESC`（2026-10-15 入库对话定稿，
2026-10-17 按业主口径收敛）。**本模块是项目内唯一来源**，imflow 侧应从同一份
定义读取（或导出同构 JSON），避免两边各维护一套导致漂移。

## 三条已定口径（业主 2026-10-17）
1. **画幅不是机位**：`FanCam` 在实际库里横屏 1197 条 / 竖屏 637 条，
   横竖屏由 width/height 派生，**不进入机位枚举**。
   因此 `Vertical FanCam` 归一化为 `FanCam`（原名尾标不动，画幅另算）。
2. **`K-Choreo Tower Cam` 按塔式机位**：归一化为 `Tower Cam`，不保留组合值。
3. **词表外一律不入库**：识别到「像机位」但不在词表里的写法时，
   由 `detect_unknown_camera()` 报出来，入库前阻断并提示，由业主决定是否扩充词表。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple

# ===== 机位 =====

# 规范值（顺序即 UI 下拉顺序）。`FanCam` 是周播打歌节目的默认机位。
CAMERA_FANCAM = "FanCam"
CAMERA_ANGLES: Tuple[str, ...] = (
    "FanCam",
    "FaceCam",
    "FullCam",
    # 2026-10-18 业主新增：电视台播出版（导播切换的整段舞台）
    "BroadcastCam",
    "Tower Cam",
    "K-Choreo",
    "OneTake",
    "MultiCam",
    "SkyCam",
    "其他",
)

# 中文名（详情页 / 下拉 / 中文简介统一口径，对齐 rules.CAM_DESC）
CAMERA_LABELS = {
    "FanCam": "粉丝直拍",
    "FaceCam": "面部特写机位",
    "FullCam": "全景机位",
    "BroadcastCam": "放送机位",
    "Tower Cam": "塔式机位",
    "K-Choreo": "编舞镜头",
    "OneTake": "一镜到底",
    "MultiCam": "多机位",
    "SkyCam": "航拍机位",
    "其他": "其他",
}

# 识别规则：**顺序即优先级**，多词机位必须排在单词机位（직캠/fancam）之前，
# 否则 `K-Choreo` 会被 `직캠` 抢先判成 FanCam（2026-10-15 实批 02/10 即此错）。
# `K-Choreo Tower Cam` 由 tower cam 规则先截获 → 按业主口径记 Tower Cam。
CAMERA_RULES = (
    (re.compile(r"얼빡|페이스캠|포커스캠|face\s*cam", re.I), "FaceCam"),
    (re.compile(r"full\s*focus(?:ed)?|full\s*cam|전체캠|풀캠|full\s*ver", re.I), "FullCam"),
    (re.compile(r"멀티캠|multi\s*cam", re.I), "MultiCam"),
    (re.compile(r"항공캠|sky\s*cam", re.I), "SkyCam"),
    (re.compile(r"tower\s*cam|타워캠", re.I), "Tower Cam"),
    (re.compile(r"k-?choreo", re.I), "K-Choreo"),
    (re.compile(r"단독샷|원테이크|one\s*take", re.I), "OneTake"),
    # 竖版是画幅不是机位 → 归一化为 FanCam（业主 2026-10-17 口径 1）
    (re.compile(r"세로|vertical", re.I), "FanCam"),
    (re.compile(r"직캠|fan\s*cam|fancam|focus\s*cam", re.I), "FanCam"),
    # 放送机位（电视台播出版）。**刻意排在最后**：`방송 직캠` 这类同时含机位词的尾标
    # 仍由上面的 `직캠` 判成 FanCam（更具体的那一个胜出）。
    # ⚠️ 2026-10-26 修：**不能单认 `방송`/`broadcast`** —— 韩文标题里的 `MBC230812방송`
    #    是「播出日期」不是机位，会把官方舞台尾标污染成 `[Show! MusicCore BroadcastCam]`。
    (re.compile(r"放送|broadcast ?cam|broadcastcam", re.I), "BroadcastCam"),
)

# 精确别名（人工填写 / AI 回填可能给出的写法，大小写不敏感）
CAMERA_ALIASES = {
    "facecam": "FaceCam",
    "face cam": "FaceCam",
    "fullcam": "FullCam",
    "full cam": "FullCam",
    "broadcast": "BroadcastCam",
    "broadcast cam": "BroadcastCam",
    "broadcastcam": "BroadcastCam",
    "放送": "BroadcastCam",
    "放送机位": "BroadcastCam",
    "방송": "BroadcastCam",
    "방송캠": "BroadcastCam",
    "fullcam": "FullCam",
    "multicam": "MultiCam",
    "multi cam": "MultiCam",
    "skycam": "SkyCam",
    "sky cam": "SkyCam",
    "towercam": "Tower Cam",
    "tower cam": "Tower Cam",
    "k-choreo tower cam": "Tower Cam",
    "kchoreo": "K-Choreo",
    "k-choreo": "K-Choreo",
    "onetake": "OneTake",
    "one take": "OneTake",
    "vertical fancam": "FanCam",
    "vertical": "FanCam",
    "fancam": "FanCam",
    "fan cam": "FanCam",
    "其他": "其他",
    "other": "其他",
}

# 「像机位」的特征词：命中但词表没覆盖 → 视为词表外新写法（业主口径 3）
# ⚠ 只认真正像机位的词：`close up Ver.` / `Dance Practice Ver.` 这类**版本**词不算，
#   否则会把合法的舞蹈练习版拦下来。
_UNKNOWN_CAMERA_HINT = re.compile(
    r"cam|캠|직캠|one\s*pick|앵글|angle", re.I
)

# 纯栏目/版本词：出现在尾标里但不构成机位，命中这些不算「词表外机位」
_NON_CAMERA_TAILS = re.compile(
    r"^(?:performance\s*video|performancevideo|specialstage|special\s*stage|"
    r"specialvideo|special\s*video|officialmv|coverstage|cover\s*stage|"
    r"shortvideo|shorts|mixedit|teaser|collabstage|collab\s*stage|other)$",
    re.I,
)


def is_non_camera_tail(value: Optional[str]) -> bool:
    """尾标是纯「栏目/类型」词（PerformanceVideo / CoverStage / Other…）→ 不是机位。

    这类尾标在词表里可能恰好有同名字段（如 `Other` → `其他`），
    但它是**视频类型**不是机位，不能拿来做机位推断。
    """
    text = (value or "").strip()
    return bool(text) and bool(_NON_CAMERA_TAILS.match(text))


def normalize_camera(value: Optional[str]) -> Optional[str]:
    """把任意写法归一化为规范机位值；无法识别返回 None（= 未标注）。

    - 先按精确别名表（大小写/空格不敏感）
    - 再按 CAMERA_RULES 顺序做子串匹配（长词优先）
    - `其他` / `other` 保留为 `其他`
    """
    text = (value or "").strip()
    if not text:
        return None
    key = re.sub(r"\s+", " ", text).strip().lower()
    alias = CAMERA_ALIASES.get(key)
    if alias:
        return alias
    for pattern, camera in CAMERA_RULES:
        if pattern.search(text):
            return camera
    return None


def detect_unknown_camera(value: Optional[str]) -> Optional[str]:
    """判断某段文本是否含「词表外的机位写法」。

    返回**原尾标文本**（供提示文案原样展示），未命中返回 None。

    只对**像机位**的词敏感（cam/캠/one pick/angle…），
    纯栏目词（PerformanceVideo / CoverStage / Shorts…）不算。
    """
    text = (value or "").strip()
    if not text or normalize_camera(text):
        return None
    # 去掉方括号与常见栏目词后再判断，避免 `[SpecialVideo Shorts]` 之类误报
    probe = re.sub(r"[\[\]（）()]", " ", text)
    tokens = [t for t in re.split(r"[\s,·/]+", probe) if t]
    for token in tokens:
        if _NON_CAMERA_TAILS.match(token):
            continue
        if _UNKNOWN_CAMERA_HINT.search(token):
            # 命中即报整段尾标：`Cam` / `CAM` 这类单词单独报出来没有信息量
            return text
    return None


def camera_label(value: Optional[str]) -> str:
    """机位的中文展示名；未知值原样返回。"""
    if not value:
        return ""
    return CAMERA_LABELS.get(value, value)


def orientation_of(width: Optional[int], height: Optional[int]) -> Optional[str]:
    """画幅：portrait / landscape；未知返回 None。

    画幅是**派生属性**，不入库、不进机位词表（业主 2026-10-17 口径 1）。
    """
    try:
        w = int(width or 0)
        h = int(height or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    if h > w:
        return "portrait"
    if w > h:
        return "landscape"
    return None


# ===== 场次 =====

# 场次格式：`YYMMDD - 活动名`（业主 2026-10-17 口径）
SESSION_SEP = " - "
SESSION_RE = re.compile(r"^\d{6} - \S.*$")
SESSION_NAME_MAX = 80


def build_session_label(
    performance_date: Optional[date],
    published_date: Optional[date],
    event_name: Optional[str],
    uploader: Optional[str] = None,
) -> Optional[str]:
    """生成场次标签 `YYMMDD - 活动名`。

    口径：
    - 日期：优先 performance_date，缺失回退 published_date（上传日期）
    - 活动名：**只认 event_name**。缺失 → 返回 None（留空，由数据体检报出来，不编造）
    - ★ 2026-10-26 业主："活动名空→回退频道名这个不合理" —— 频道（`M2` / `tripleS official`
      / `유연하게`）不是活动，回退出来的是假场次。已**取消频道名回退**。
      `uploader` 参数保留仅为兼容调用方，不再参与取值。
    - `event_name` 同样缺失且日期也缺 → 返回 None
    """
    d = performance_date or published_date
    if d is None:
        return None
    name = (event_name or "").strip()
    if not name:
        return None
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > SESSION_NAME_MAX:
        name = name[:SESSION_NAME_MAX].rstrip()
    return f"{d:%y%m%d}{SESSION_SEP}{name}"


def session_date_of(label: Optional[str]) -> Optional[str]:
    """从场次标签里取出 6 位日期段（用于与 performance_date 做一致性校验）。"""
    text = (label or "").strip()
    m = re.match(r"^(\d{6}) - ", text)
    return m.group(1) if m else None


def validate_session_label(label: Optional[str]) -> Optional[str]:
    """校验场次格式，返回错误说明；通过返回 None。"""
    text = (label or "").strip()
    if not text:
        return None
    if not SESSION_RE.match(text):
        return "场次格式应为「YYMMDD - 活动名」，例如「261017 - Music Bank」"
    if len(text) > SESSION_NAME_MAX + 9:
        return f"场次名称过长（上限 {SESSION_NAME_MAX + 9} 字符）"
    return None

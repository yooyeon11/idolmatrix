"""AI 标题清洗：方括号内不得残留画质 / 帧率 / 编码标签（2026-09-22 业主口径）。

背景：AI 系统提示词历史上要求「画质/帧率（4K、8K、60p）必须追加标注」，
于是入库标题出现 `[M COUNTDOWN FanCam 4K60p]` 这类后缀。提示词已改，
这里验收兜底清洗 `_strip_quality_tags`：只动方括号段，括号外的艺人 / 歌曲段一律不碰。

跑法：cd backend && python test_title_quality_strip.py
（不要交给 pytest 收集）
"""

from app.services.ai_service import _normalize, _strip_quality_tags

PASS = 0
FAIL = 0


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL {label}\n  got  = {got!r}\n  want = {want!r}")


# ===== 1. 典型旧惯例：后缀画质标签被剥掉 =====
check(
    "4K 后缀",
    _strip_quality_tags("240511 tripleS - Girls Never Die [M COUNTDOWN FanCam 4K]"),
    "240511 tripleS - Girls Never Die [M COUNTDOWN FanCam]",
)
check(
    "4K60p 后缀",
    _strip_quality_tags("260812 Hearts2Hearts - ICONIC HEART [Japan Debut Event Rehearsal FanCam 4K60p]"),
    "260812 Hearts2Hearts - ICONIC HEART [Japan Debut Event Rehearsal FanCam]",
)
check(
    "8K60p 后缀",
    _strip_quality_tags("240101 Artist - Song [Music Bank FanCam 8K60p]"),
    "240101 Artist - Song [Music Bank FanCam]",
)
check(
    "1080p 后缀",
    _strip_quality_tags("240101 SBS Artist - Song [Music Core FanCam 1080p]"),
    "240101 SBS Artist - Song [Music Core FanCam]",
)
check(
    "2160p 后缀",
    _strip_quality_tags("240101 Artist - Song [Music Core FanCam 2160p]"),
    "240101 Artist - Song [Music Core FanCam]",
)
check(
    "60fps 后缀",
    _strip_quality_tags("240101 Artist - Song [FanCam 60fps]"),
    "240101 Artist - Song [FanCam]",
)
check(
    "60p 独立标签",
    _strip_quality_tags("240101 Artist - Song [FanCam 60p]"),
    "240101 Artist - Song [FanCam]",
)
check(
    "编码标签 HEVC",
    _strip_quality_tags("240101 Artist - Song [FanCam HEVC]"),
    "240101 Artist - Song [FanCam]",
)
check(
    "小写 4k60p",
    _strip_quality_tags("240101 Artist - Song [FanCam 4k60p]"),
    "240101 Artist - Song [FanCam]",
)

# ===== 2. 前置 / 独占方括号里的画质标签 =====
check(
    "文件名前置 [4K] 整段去掉",
    _strip_quality_tags("[4K] Artist - Song MV_240102"),
    "Artist - Song MV_240102",
)
check(
    "括号内只有画质 → 整段去掉",
    _strip_quality_tags("240101 Artist - Song [4K]"),
    "240101 Artist - Song",
)
check(
    "括号内只有画质（前置）",
    _strip_quality_tags("[4K60p] Artist - Song"),
    "Artist - Song",
)

# ===== 3. 不能误伤：正常标题 / 活动名 / Shorts / 括号外内容 =====
check(
    "正常标题不变",
    _strip_quality_tags("150912 Irene - Dumb Dumb [2015 DMC Festival Show! Music Core Rehearsal Fancam]"),
    "150912 Irene - Dumb Dumb [2015 DMC Festival Show! Music Core Rehearsal Fancam]",
)
check(
    "Shorts 不被误删",
    _strip_quality_tags("240101 Artist - Song [FanCam Shorts]"),
    "240101 Artist - Song [FanCam Shorts]",
)
check(
    "括号外歌名里的 4K 不碰",
    _strip_quality_tags("240101 Artist - 4K Love Song [FanCam]"),
    "240101 Artist - 4K Love Song [FanCam]",
)
check(
    "无方括号标题原样返回",
    _strip_quality_tags("240101 Artist - Song"),
    "240101 Artist - Song",
)
check("空串安全", _strip_quality_tags(""), "")
check("只有空格的方括号", _strip_quality_tags("240101 Artist - Song [ ]"), "240101 Artist - Song")

# ===== 4. _normalize 集成：只清 name，不动 original_title =====
norm = _normalize(
    {
        "name": "240511 tripleS - Girls Never Die [M COUNTDOWN FanCam 4K]",
        "original_title": "tripleS - Girls Never Die 4K M COUNTDOWN Fancam",
        "video_type": "Fancam",
    }
)
check("normalize 清洗 name", norm.get("name"), "240511 tripleS - Girls Never Die [M COUNTDOWN FanCam]")
check("normalize 保留 original_title 原文", norm.get("original_title"), "tripleS - Girls Never Die 4K M COUNTDOWN Fancam")
check("normalize 其它字段不受影响", norm.get("video_type"), "Fancam")

check("normalize 清洗后为空则不落 name", "name" in _normalize({"name": "[4K]"}), False)
check("normalize 空白 name 不落字段", "name" in _normalize({"name": "   "}), False)
check(
    "normalize 无画质时 name 原样",
    _normalize({"name": "240101 Artist - Song [FanCam Shorts]"}).get("name"),
    "240101 Artist - Song [FanCam Shorts]",
)

print(f"\n{PASS} PASS / {FAIL} FAIL")
raise SystemExit(1 if FAIL else 0)

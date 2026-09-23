"""AI 入库提示词守卫：中文简介规则（可自定义）+ 标题禁令。

⚠ 2026-09-23 业主口径变更：中文简介**恢复原版自由口径**（不再要固定句式），
并把这条规则抽成可在「AI 设置」页编辑 / 恢复默认的 `desc_prompt`。
所以本文件里原先「旧口径必须彻底消失」那组断言已按新口径反转。

跑法：cd backend && python test_ai_description_prompt.py（不要交给 pytest 收集）
"""

import re

from app.schemas.app_settings import IngestAiSettings
from app.services.ai_service import (
    DEFAULT_DESC_RULE,
    SYSTEM_PROMPT,
    build_system_prompt,
    _normalize,
    _strip_identity_parens,
)

PASS = 0
FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label} {extra}")


def block_of(field: str) -> str:
    """取某个字段说明及其后续缩进子条目（到下一个顶层 `- ` 为止）。"""
    lines = SYSTEM_PROMPT.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"- {field}:")), None)
    if start is None:
        return ""
    out = [lines[start]]
    for ln in lines[start + 1 :]:
        if ln.startswith("- ") or ln.startswith("📛") or ln.startswith("⚠️"):
            break
        out.append(ln)
    return "\n".join(out)


ORIGINAL_RULE = (
    "- chinese_description: 中文简介（用中文根据视频内容生成 2-4 句话，"
    "介绍这是什么演出/舞台/节目、表演者与曲目等；不要照抄原始简介原文）"
)

desc = block_of("chinese_description")
title_rule = next((ln for ln in SYSTEM_PROMPT.splitlines() if ln.startswith("5️⃣")), "")

print("=== chinese_description 段（默认渲染）===")
print(desc)

print("\n=== 1. 默认 = 原版自由口径 ===")
check("取到了 chinese_description 段", bool(desc))
check("DEFAULT_DESC_RULE 逐字等于原版", DEFAULT_DESC_RULE == ORIGINAL_RULE, repr(DEFAULT_DESC_RULE))
check("默认段逐字等于原版", desc == ORIGINAL_RULE, repr(desc))
check("默认段是单行（不再有 19 行细则）", len(desc.splitlines()) == 1)
check("保留「2-4 句话」原版措辞", "2-4 句话" in desc)
check("保留原版「不要照抄原始简介原文」", "不要照抄原始简介原文" in desc)
# v3.6.7~v3.6.11 的严格句式已退回默认之外
check("默认不再要求固定句式", "固定句式" not in desc)
check("默认不再要求「表演歌曲」", "表演歌曲" not in desc)
check("默认不再约束主体写法", "组合名在前" not in desc and "艺人张元英" not in desc)
check("默认不再禁技术参数 / 主观评价", "禁止出现画质" not in desc and "严禁添油加醋" not in desc)
check("默认段没有画质示例", not re.search(r"4K|60p|1080p", desc))

print("\n=== 2. 模板渲染 build_system_prompt ===")
check("SYSTEM_PROMPT 无占位符残留", "{desc_rule}" not in SYSTEM_PROMPT)
check("SYSTEM_PROMPT == 默认渲染", SYSTEM_PROMPT == build_system_prompt())
check("None 回落默认", build_system_prompt(None) == SYSTEM_PROMPT)
check("空串回落默认", build_system_prompt("") == SYSTEM_PROMPT)
check("全空白回落默认", build_system_prompt("   \n  ") == SYSTEM_PROMPT)
check("默认渲染无双前缀", "- chinese_description: - chinese_description:" not in SYSTEM_PROMPT)
check("模板里 chinese_description 只出现一次", SYSTEM_PROMPT.count("chinese_description") == 1)

custom = "- chinese_description: 只用一句话，写成「主体 于 日期 表演 曲目」。"
rendered = build_system_prompt(custom)
check("自定义规则已注入", custom in rendered)
check("自定义渲染无占位符残留", "{desc_rule}" not in rendered)
check("自定义渲染里默认口径已让位", "2-4 句话" not in rendered)
check("自定义渲染该段仍只 1 行", len([ln for ln in rendered.splitlines() if ln.startswith("- chinese_description")]) == 1)
check(
    "其余字段说明不受影响",
    "video_type: 视频类型" in rendered
    and "📛 标题（name）规范" in rendered
    and "- original_title:" in rendered,
)
check("自定义渲染仍等于完整提示词（不是片段）", rendered.startswith("你是 K-pop 媒体库的元数据整理助手"))

print("\n=== 3. 防呆：只写内容时自动补字段名前缀 ===")
loose = build_system_prompt("用中文写 2 句简介，不要照抄原文。")
check("自动补出整行", "- chinese_description: 用中文写 2 句简介，不要照抄原文。" in loose)
check("不产生双前缀", "- chinese_description: - chinese_description:" not in loose)
check("该段仍只 1 行", len([ln for ln in loose.splitlines() if ln.startswith("- chinese_description")]) == 1)

print("\n=== 4. 设置字段（AI 设置页可编辑 / 恢复默认）===")
check("IngestAiSettings.desc_prompt 默认空串", IngestAiSettings().desc_prompt == "")
check("空串语义 = 用后端内置默认", build_system_prompt(IngestAiSettings().desc_prompt) == SYSTEM_PROMPT)
check(
    "自定义值可透传",
    build_system_prompt(IngestAiSettings(desc_prompt=custom).desc_prompt).count(custom) == 1,
)

print("\n=== 5. 标题禁令（未受本次改动影响，一并守）===")
check("标题段存在", bool(title_rule))
check("括号内禁止写画质", "禁止写画质" in SYSTEM_PROMPT)
check("width/height/fps 严禁写进 name", "严禁写进 name" in SYSTEM_PROMPT)
check("旧规则「必须追加标注」已消失", "必须追加标注" not in SYSTEM_PROMPT)
check("旧判断依据「width ≥ 3840」已消失", "width ≥ 3840" not in SYSTEM_PROMPT)
check(
    "文件名示例不再带 [4K] 范本",
    "[4K] Artist - Song MV_240102"
    not in SYSTEM_PROMPT.replace("（如「[4K] Artist - Song MV_240102」「… 4K60p」）", ""),
)

print("\n=== 6. 身份标注规范化 _strip_identity_parens（兜底清洗，保留）===")
cases = [
    # 去括号：组合 → 身份词后置；艺人 → 身份词前置
    (
        "STAYC（组合）于 2023 年 2 月 17 日在 Music Bank 表演 Teddy Bear。",
        "STAYC组合于 2023 年 2 月 17 日在 Music Bank 表演 Teddy Bear。",
    ),
    (
        "张元英（艺人）于 2024 年 1 月 5 日在 M COUNTDOWN 表演歌曲 X。",
        "艺人张元英于 2024 年 1 月 5 日在 M COUNTDOWN 表演歌曲 X。",
    ),
    ("STAYC(组合)于 2023 年 2 月 17 日。", "STAYC组合于 2023 年 2 月 17 日。"),  # 半角括号
    ("IVE （ 组合 ）于 2024 年。", "IVE 组合于 2024 年。"),  # 括号内含空格
    ("STAYC（组合)于 2024 年。", "STAYC组合于 2024 年。"),  # 全角/半角混用
    # 后置的「X艺人」（模型照组合的写法类推出来）→ 统一翻成前置
    (
        "张元英艺人于 2024 年 1 月 5 日在 M COUNTDOWN 表演歌曲 X。",
        "艺人张元英于 2024 年 1 月 5 日在 M COUNTDOWN 表演歌曲 X。",
    ),
    ("IU艺人于 2024 年表演歌曲 X。", "艺人IU于 2024 年表演歌曲 X。"),
    ("金在中 艺人 在首尔出演 X。", "艺人金在中 在首尔出演 X。"),
    ("张元英艺人在 M COUNTDOWN 表演歌曲 X。", "艺人张元英在 M COUNTDOWN 表演歌曲 X。"),
    # 不能误伤：以下括号里不是「组合 / 艺人」两字，一律不动
    ("翻唱自 TWICE 的（特别版）舞台。", "翻唱自 TWICE 的（特别版）舞台。"),
    ("表演《Ditto》（NewJeans 的歌）。", "表演《Ditto》（NewJeans 的歌）。"),
    ("无括号的普通句子。", "无括号的普通句子。"),
    ("", ""),
    # 不能误伤：本来就正确的前置写法 / 「艺人」不是身份词时
    ("艺人张元英于 2024 年表演歌曲 X。", "艺人张元英于 2024 年表演歌曲 X。"),
    ("该视频为艺人张元英的直拍。", "该视频为艺人张元英的直拍。"),
    ("与艺人张元英合作。", "与艺人张元英合作。"),
    ("韩国艺人在此活动出演。", "韩国艺人在此活动出演。"),
    # 原版口径下的自由句子必须原样通过（不能被兜底清洗改动）
    ("这是一个特别舞台，成员们带来了两首歌曲的串烧表演。", "这是一个特别舞台，成员们带来了两首歌曲的串烧表演。"),
]
for src, want in cases:
    got = _strip_identity_parens(src)
    check(f"{src[:24]!r} → {want[:24]!r}", got == want, f"got={got!r}")

check(
    "_normalize 对 chinese_description 也走这条清洗",
    _normalize({"chinese_description": "STAYC（组合）于 2023 年表演 X。"}).get("chinese_description")
    == "STAYC组合于 2023 年表演 X。",
)
check(
    "_normalize 不误改 original_title（保留来源原文）",
    _normalize({"original_title": "STAYC（组合）原题"}).get("original_title")
    == "STAYC（组合）原题",
)

print(f"\n{PASS} PASS / {FAIL} FAIL")
raise SystemExit(1 if FAIL else 0)

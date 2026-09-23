"""入库 AI 建议服务：调用用户配置的 LLM API 生成入库元数据建议。

支持两类协议：
- OpenAI 兼容（openai-compatible / ollama / custom）：POST {base_url}/chat/completions
- Anthropic：POST {base_url}/v1/messages

全部使用标准库 urllib，不引入额外依赖。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Album, Artist, Company, Group, GroupMembership
from app.services import wiki_service

logger = logging.getLogger("app.ai")

VIDEO_TYPES = (
    "OfficialMV",
    "PerformanceVideo",
    "Fancam",
    "SpecialStage",
    "CollabStage",
    "CoverStage",
    "MixEdit",
    "Teaser",
    "SpecialVideo",
    "ShortVideo",
    "Other",
)

# ⚠ chinese_description 那一条是**占位符** —— 由 build_system_prompt(desc_prompt) 替换注入，
# 默认值 DEFAULT_DESC_RULE（原版自由口径），可在「AI 设置」页自定义。SYSTEM_PROMPT 是渲染结果。
_SYSTEM_TEMPLATE = """你是 K-pop 媒体库的元数据整理助手。根据给定的视频信息（来自 yt-dlp 的 info.json 精简字段）、库内已选关联名称以及用户当前表单值，生成入库元数据。

字段说明：
- original_title: 视频原始标题（必须逐字保留来源标题原文，禁止翻译、禁止改写、禁止增删字词；该值以入库时的来源标题为准，AI 不应改动它）
- name: 主标题（库内展示名），必须严格遵循标题规范「YYMMDD Artist - Song [Event Name Type]」，规则见下
- chinese_name: 中文名（如适用，否则 null）
- video_type: 视频类型，必须逐字等于以下 11 个值之一（严格枚举，不允许变体写法）：OfficialMV、PerformanceVideo、Fancam、SpecialStage、CollabStage、CoverStage、MixEdit、Teaser、SpecialVideo、ShortVideo、Other
- video_types: 视频类型数组（可多选，0-2 个），每个值必须逐字等于上面 11 个枚举之一；若只确定一个类型，也请返回数组（如 ["OfficialMV"]）

⚠️ 视频类型严格枚举对照（以下常见叫法都不是合法值，必须转换成对应枚举）：
- MV / M/V / Music Video → OfficialMV
- Stage / 打歌舞台 / 音乐节目舞台 / Live / 现场 / 直播舞台 / 演唱会 → PerformanceVideo（官方舞台）
- 特别舞台 → SpecialStage
- 合作舞台 / 联合舞台 / 跨团舞台 / Collab Stage / Joint Stage / Special Collab → CollabStage（两组及以上共同表演的舞台，合作方是不同团体/不同代际艺人；同一组内部的 unit 小分队舞台不算，走 PerformanceVideo）
- 练习室 / Dance Practice → SpecialStage
- FanCam / 直拍 / 饭拍 / FocusCam → Fancam（官方或粉丝直拍都归 Fancam，不细分）
- 翻唱 / Cover / 커버 / Cover Stage / Cover Dance / 翻唱舞台 → CoverStage
- 混剪 / MIX / MIX混剪 / 混剪视频 / 混剪合集 / Mix Edit → MixEdit（多曲混剪、剪辑合集；与「串烧」不同，串烧是一镜到底连唱，混剪是剪辑拼接）
- 预告 / Highlight → Teaser
- 花絮 / BTS / Behind / 幕后 → SpecialVideo（非表演）
- Special Clip / 特别视频 / 特辑 / 访谈 / 综艺 / 粉丝见面会 / Fan Meeting → SpecialVideo（非表演）
- 短视频 / Short / Shorts / Reels / Short Video → ShortVideo（仅限竖屏短片段本体；混剪/剪辑合集一律用 MixEdit，不要把短片长的混剪标成 ShortVideo）
- 纯音频 / 音源不是合法类型，选 Other
- 无法确定时选 Other，严禁自创类型名称。
- event_name: 舞台/活动名称（如 XX 艺术节、XX 校庆、XX 电视台节目、M COUNTDOWN、音乐银行等；用于区分「XX艺术节 / XX校庆 / XX电视台」这类演出场合，无则 null）
- performance_date: 表演日期 YYYY-MM-DD（不确定则 null）
- subject_artist_name: 直拍对象（艺人或成员名，仅 Fancam 适用，否则 null）
- is_solo: 是否为 solo 表演（布尔值 true/false）。判断标准：表演者是独立 solo 艺人（如 IU、金在中），或视频是个人舞台/个曲表演而非团体合体表演。组合成员的个人直拍（如 Red Velvet 的 IRENE FanCam）不算 solo 表演；只有表演主体是 solo 歌手/个人舞台时才为 true，不确定则 null
{desc_rule}
- suggested_tracks: 曲目列表。多首歌时必须按「每首歌自己的专辑」配对，不要把所有专辑摊到所有歌上。每项 {"song": "官方曲名", "albums": ["专辑1", "专辑2"]}；一首歌可对应多张专辑（数字单曲 + 正专很常见）；不确定专辑则 albums 为 [] 或省略。单曲视频也请返回一项。
- suggested_songs / suggested_albums: 仅作兼容；有 suggested_tracks 时可以不填。suggested_artists / suggested_groups: 艺人 / 组合名建议（字符串数组；优先官方名称；不确认则 null）

注意：视频平台（YouTube/Bilibili 等）与是否短视频（is_short）由系统自动判断，不要生成 source_platform / is_short / 原 description 字段。

📛 标题（name）规范：YYMMDD Artist - Song [Event Name Type]

1️⃣ YYMMDD（日期前缀）：6 位日期（如 240511）。优先取表演日期（performance_date），无则取上传日期（upload_date）。

2️⃣ 艺人名（Artist）
- 团体/组合表演 → 填入 团体英文名（如 tripleS、Red Velvet）。
- 个人直拍/Focus Cam → 必须且只能填入 成员的英文艺名/本名（如 Kim YooYeon、Irene）。
- ❌ 严禁拼合为「团体-成员」或「团体 成员」格式。
- 若「库内已选关联」中已有艺人或组合，优先使用其中的官方名称。

3️⃣ 歌曲名（Song）
- 必须优先使用官方英文曲名，严禁对韩文曲名进行机器直译。
- 例：은하수를 여행하는 히치하이커를 위한 안내서 → 必须用 The Hitchhiker's Guide to the Galaxy
- 若「库内已选关联」中已有歌曲，优先使用其中的官方曲名。

4️⃣ 多曲目连接
- 若为串烧/拼盘/多首歌曲，用 & 连接（如 Song A & Song B）。

5️⃣ 后缀括号 [Event Name Type] 特殊标识
- 括号内为「活动/节目名 + 视频类型」，**写到视频类型词即止**。
- 时长 < 1 分钟（duration < 60）→ 必须包含 Shorts（如 [FanCam Shorts]）。
- ❌ 括号内禁止写画质 / 帧率 / 编码等来源技术标签：4K、8K、60p、4K60p、8K60p、1080p、2160p、60fps、HEVC 之类一律不写。
- video_info 的 width / height / fps 只用于判断视频类型与时长分档，**严禁写进 name**。
- ❌ 来源标题 / 文件名里自带的画质标签（如「[4K] Artist - Song MV_240102」「… 4K60p」）同样必须丢弃，不得带进 name。

📛 缺少 info.json 元数据时的处理：
- 当「视频信息」中缺少 title / webpage_url / uploader / upload_date 等来源字段（仅剩 file_name 文件名与技术参数）时，说明该视频没有 info.json，文件名是唯一线索。
- 请从文件名解析常见命名格式：如「Artist - Song MV_240102」「240101 SBS 인기가요_Artist_Song」→ 艺人 / 歌曲 / 事件类型 / 日期；文件名里的 [4K] / 60fps 一类画质前缀是来源标签，直接忽略。
- 文件名含打歌台关键字（SBS / MBC / KBS / Mnet / 인기가요 / 음악중심 / Show 等）→ video_types 优先含 PerformanceVideo 并填 event_name；含粉丝见面会关键字（Fan Meeting / FM / 粉丝见面会）→ SpecialVideo。类型只按内容性质判断，不要因为时长很短就选 ShortVideo（少于 70 秒的混剪/合集请用 MixEdit）。
- 文件名无法可靠解析出艺人或歌曲时，对应字段必须设为 null，并在 notice 中说明原因，严禁编造艺人、歌曲或专辑名。

只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。字段没有把握时设为 null。"""

# chinese_description 规则的内置默认 —— 即「原版」自由口径（业主 2026-09-23 要求恢复）。
# AI 设置页留空 = 用它；「恢复默认」按钮也回落到它。
DEFAULT_DESC_RULE = (
    "- chinese_description: 中文简介（用中文根据视频内容生成 2-4 句话，"
    "介绍这是什么演出/舞台/节目、表演者与曲目等；不要照抄原始简介原文）"
)

_DESC_RULE_SLOT = "{desc_rule}"
_DESC_RULE_PREFIX = "- chinese_description: "


def build_system_prompt(desc_rule: Optional[str] = None) -> str:
    """把中文简介规则注入系统提示词模板（其余字段说明不变）。

    ⚠ 必须用 replace —— 模板正文含 JSON 示例（`{"song": ...}`），str.format 会直接炸。
    规则文本按**整行**填写（自带 `- chinese_description: ` 前缀）；若用户只写了内容，
    这里自动补回前缀，避免模型丢失字段名。
    """
    rule = (desc_rule or "").strip() or DEFAULT_DESC_RULE
    if "chinese_description" not in rule:
        rule = f"{_DESC_RULE_PREFIX}{rule}"
    return _SYSTEM_TEMPLATE.replace(_DESC_RULE_SLOT, rule)


# 渲染结果（= 内置默认规则）。保留 SYSTEM_PROMPT 之名，既有调用方与守卫测试不受影响。
SYSTEM_PROMPT = build_system_prompt()


def _is_gemini_style(cfg: Dict[str, Any]) -> bool:
    """Gemini / gemini-fastapi 等兼容层不支持 OpenAI 的 json_object 响应格式。"""
    blob = " ".join(str(cfg.get(k) or "") for k in ("provider", "base_url", "model")).lower()
    return "gemini" in blob


def _skip_json_object(cfg: Dict[str, Any]) -> bool:
    """Ollama / Gemini 等兼容层经常拒 json_object。"""
    provider = (cfg.get("provider") or "").strip().lower()
    if provider == "ollama" or _is_gemini_style(cfg):
        return True
    blob = " ".join(str(cfg.get(k) or "") for k in ("provider", "base_url", "model")).lower()
    return "ollama" in blob or ":11434" in blob


def completions_url(base_url: str) -> str:
    """把用户填的 Base URL 收成 OpenAI 兼容的 /chat/completions 地址。"""
    raw = (base_url or "").strip()
    if not raw:
        return "/chat/completions"
    parsed = urllib.parse.urlparse(raw)
    base = raw.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    if "generativelanguage.googleapis.com" in host:
        if "/openai" not in path:
            if path in {"", "/"}:
                base = f"{parsed.scheme}://{parsed.netloc}/v1beta/openai"
            else:
                base = f"{base}/openai"
        return f"{base.rstrip('/')}/chat/completions"
    if path in {"", "/"}:
        base = f"{base}/v1"
    return f"{base}/chat/completions"


def _http_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"AI 接口返回非 JSON: {raw[:300]}") from e


def message_text(data: dict) -> str:
    """从 OpenAI 兼容响应里抽出文本；兼容 content 数组和 reasoning_content。"""
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"AI 响应结构异常: {json.dumps(data, ensure_ascii=False)[:300]}") from e
    if not isinstance(msg, dict):
        raise ValueError(f"AI 响应结构异常: {json.dumps(data, ensure_ascii=False)[:300]}")
    content = msg.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str) and part.strip():
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content") or ""
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        content = "\n".join(parts)
    if isinstance(content, str) and content.strip():
        return content
    for key in ("reasoning_content", "reasoning"):
        extra = msg.get(key)
        if isinstance(extra, str) and extra.strip():
            return extra
    raise ValueError(f"AI 响应结构异常: {json.dumps(data, ensure_ascii=False)[:300]}")


def _split_data_url(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or not url.startswith("data:") or "," not in url:
        raise ValueError("图片内容不是 data URL")
    header, b64 = url.split(",", 1)
    mime = "image/jpeg"
    rest = header[5:]
    if ";" in rest:
        mime = rest.split(";", 1)[0] or mime
    return mime, b64


def _anthropic_user_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content
    blocks: List[dict] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": part.get("text") or ""})
        elif part.get("type") == "image_url":
            image = part.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            mime, b64 = _split_data_url(str(url or ""))
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64},
                }
            )
    return blocks


def _openai_compatible(cfg: Dict[str, Any], messages: List[dict], timeout: int = 90) -> str:
    url = completions_url(cfg.get("base_url") or "")
    payload: Dict[str, Any] = {
        "model": cfg.get("model"),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    if not _skip_json_object(cfg):
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    api_key = cfg.get("api_key") or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        return message_text(_http_json(url, payload, headers, timeout))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code == 400 and "response_format" in payload:
            payload.pop("response_format", None)
            try:
                return message_text(_http_json(url, payload, headers, timeout))
            except urllib.error.HTTPError as e2:
                body = e2.read().decode("utf-8", errors="replace")
                raise ValueError(f"AI 请求失败（{e2.code}）: {body[:300]}") from e2
        raise ValueError(f"AI 请求失败（{e.code}）: {body[:300]}") from e


def _anthropic(cfg: Dict[str, Any], messages: List[dict], timeout: int = 90) -> str:
    base_url = (cfg.get("base_url") or "").rstrip("/")
    url = f"{base_url}/v1/messages"
    system_parts = []
    user_msgs = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                system_parts.extend(
                    p.get("text") or ""
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
        else:
            user_msgs.append(
                {"role": m.get("role"), "content": _anthropic_user_content(m.get("content"))}
            )
    payload = {
        "model": cfg.get("model"),
        "max_tokens": 8192,
        "temperature": 0.2,
        "system": "\n\n".join(system_parts),
        "messages": user_msgs,
    }
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    api_key = cfg.get("api_key") or ""
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"AI 接口返回非 JSON: {raw[:300]}") from e
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(parts)


def chat_completion(cfg: Dict[str, Any], messages: List[dict], timeout: int = 90) -> str:
    """调用用户配置的聊天接口，支持文本和 data-URL 图片。"""
    if not cfg.get("base_url") or not cfg.get("model"):
        raise ValueError("请先在「入库 AI 设置」中填写 API Base URL 与模型")
    provider = cfg.get("provider", "openai-compatible")
    try:
        if provider == "anthropic":
            return _anthropic(cfg, messages, timeout=timeout)
        return _openai_compatible(cfg, messages, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"AI 请求失败（{e.code}）: {body[:300]}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"AI 请求失败: {e.reason}") from e


def parse_json(text: str) -> dict:
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    """解析 AI 返回的 JSON，自动修复 LLM 输出中的常见格式错误。"""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    for candidate in (text, _repair_json(text)):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(
        "AI 返回内容无法解析为 JSON（已尝试自动修复）。"
        f"返回原文: {text[:300]}"
    )


def _repair_json(text: str) -> str:
    """单遍修复 LLM 输出常见的 JSON 问题：
    字符串内未转义的引号/裸换行、尾随逗号、对象与数组项间缺失的逗号。
    修复过程感知字符串边界，不会误改字符串内容。
    """
    out: List[str] = []
    in_string = False
    i, n = 0, len(text)

    def peek_non_ws(start: int) -> Optional[str]:
        """返回下一个非空白字符；到达末尾返回 None（不能用 ""，否则 "" in "..."
        恒为 True 会误判边界）。"""
        j = start
        while j < n and text[j] in " \t\r\n":
            j += 1
        return text[j] if j < n else None

    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\":
                out.append(ch)
                if i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                else:
                    i += 1
                continue
            if ch == "\n":
                out.append("\\n")
                i += 1
                continue
            if ch == '"':
                nxt = peek_non_ws(i + 1)
                if nxt and nxt in "}]:":
                    out.append(ch)
                    in_string = False
                    i += 1
                    continue
                if nxt == ",":
                    # 逗号后紧跟另一个字符串/对象/数组才视为值结束符；
                    # 若逗号后是 }/] 则为尾随逗号，同样关闭字符串（逗号随后在字符串外被移除）；
                    # 否则视作行内引号（如 "Go", then ...）
                    j = i + 1
                    while j < n and text[j] in " \t\r\n,":
                        j += 1
                    if j < n and text[j] in '"{[':
                        out.append(ch)
                        in_string = False
                    elif j < n and text[j] in "}]":
                        out.append(ch)
                        in_string = False
                    else:
                        out.append('\\"')
                    i += 1
                    continue
                if nxt and nxt in '"{[':
                    # 缺失逗号的值结束符：补一个逗号
                    out.append(ch)
                    out.append(",")
                    in_string = False
                    i += 1
                    continue
                if nxt is None:
                    out.append(ch)
                    in_string = False
                    i += 1
                    continue
                # 行内未转义的双引号
                out.append('\\"')
                i += 1
                continue
            out.append(ch)
            i += 1
            continue

        # 不在字符串内
        if ch == '"':
            out.append(ch)
            in_string = True
            i += 1
            continue
        if ch == ",":
            nxt = peek_non_ws(i + 1)
            if nxt and nxt in "}]":
                # 尾随逗号
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch in "}]":
            out.append(ch)
            nxt = peek_non_ws(i + 1)
            if nxt and nxt in '"{[':
                out.append(",")
            i += 1
            continue
        out.append(ch)
        i += 1

    return "".join(out)


_VIDEO_TYPE_FOLD: Dict[str, str] = {
    re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", t.lower()): t for t in VIDEO_TYPES
}

# 常见非标准叫法（已折叠：小写、去空格/标点）→ 合法类型。旧枚举叫法统一收敛到现行类型
_TYPE_SYNONYMS: Dict[str, str] = {
    "mv": "OfficialMV",
    "musicvideo": "OfficialMV",
    "官方mv": "OfficialMV",
    "stage": "PerformanceVideo",
    "musicshow": "PerformanceVideo",
    "musicstage": "PerformanceVideo",
    "打歌舞台": "PerformanceVideo",
    "舞台": "PerformanceVideo",
    "表演": "PerformanceVideo",
    "live": "PerformanceVideo",
    "livestage": "PerformanceVideo",
    "现场": "PerformanceVideo",
    "直播": "PerformanceVideo",
    "concert": "PerformanceVideo",
    "演唱会": "PerformanceVideo",
    "dancepractice": "SpecialStage",
    "practice": "SpecialStage",
    "练习室": "SpecialStage",
    "dancestage": "SpecialStage",
    "specialstage": "SpecialStage",
    "特别舞台": "SpecialStage",
    "newsongstage": "SpecialStage",
    "collabstage": "CollabStage",
    "collab": "CollabStage",
    "jointstage": "CollabStage",
    "合作舞台": "CollabStage",
    "联合舞台": "CollabStage",
    "跨团舞台": "CollabStage",
    "facecam": "Fancam",
    "focuscam": "Fancam",
    "官方面拍": "Fancam",
    "个人直拍": "Fancam",
    "personalfancam": "Fancam",
    "组合直拍": "Fancam",
    "groupfancam": "Fancam",
    "官方直拍": "Fancam",
    "officialfancam": "Fancam",
    "官方个人直拍": "Fancam",
    "fancam": "Fancam",
    "直拍": "Fancam",
    "teaser": "Teaser",
    "highlight": "Teaser",
    "预告": "Teaser",
    "behind": "SpecialVideo",
    "behindthescenes": "SpecialVideo",
    "bts": "SpecialVideo",
    "花絮": "SpecialVideo",
    "幕后": "SpecialVideo",
    "special": "SpecialVideo",
    "specialclip": "SpecialVideo",
    "特别视频": "SpecialVideo",
    "特辑": "SpecialVideo",
    "fanmeeting": "SpecialVideo",
    "fanmeet": "SpecialVideo",
    "粉丝见面会": "SpecialVideo",
    "见面会": "SpecialVideo",
    "interview": "SpecialVideo",
    "综艺": "SpecialVideo",
    "访谈": "SpecialVideo",
    "short": "ShortVideo",
    "shortvideo": "ShortVideo",
    "shorts": "ShortVideo",
    "reels": "ShortVideo",
    "短视频": "ShortVideo",
    "cover": "CoverStage",
    "coverstage": "CoverStage",
    "coverdance": "CoverStage",
    "翻唱": "CoverStage",
    "翻唱舞台": "CoverStage",
    "커버": "CoverStage",
    "mix": "MixEdit",
    "mixedit": "MixEdit",
    "mixeditvideo": "MixEdit",
    "mixvideo": "MixEdit",
    "mixclip": "MixEdit",
    "混剪": "MixEdit",
    "mix混剪": "MixEdit",
    "混剪视频": "MixEdit",
    "混剪合集": "MixEdit",
    "多曲混剪": "MixEdit",
    "剪辑混剪": "MixEdit",
    "audio": "Other",
    "音源": "Other",
    "音频": "Other",
    "other": "Other",
    "其他": "Other",
    "非表演视频": "Other",
    "etc": "Other",
}

# 家族类词（无法唯一映射时按 Group/Official/Personal 再细分）
_FAMILY_HINTS = ("fancam", "直拍", "focuscam")

# 混剪关键词（中文子串命中即归 MixEdit；英文只认精确同义词，避免 remix / mixtape 误伤）
_MIX_HINTS = ("混剪",)


def _canonical_video_type(raw: Any) -> Optional[str]:
    """把 AI 返回的类型字符串规整为合法类型之一；无法识别返回 None。"""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s in VIDEO_TYPES:
        return s
    folded = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s.lower())
    if folded in _VIDEO_TYPE_FOLD:
        return _VIDEO_TYPE_FOLD[folded]
    if folded in _TYPE_SYNONYMS:
        return _TYPE_SYNONYMS[folded]
    if any(hint in folded for hint in _MIX_HINTS):
        return "MixEdit"
    if any(hint in folded for hint in _FAMILY_HINTS):
        return _infer_fancam_type(folded)
    return None


def _infer_fancam_type(folded: str) -> str:
    return "Fancam"

# ===== 标题里不得残留画质 / 帧率标签（2026-09-22 业主口径）=====
# 历史提示词曾要求「4K / 60p 必须追加标注」，现已删除；这里再兜底剥掉模型漏写的残留。
# 只动方括号 [ ] 段（即 [活动名 类型词] 的位置），括号外的艺人 / 歌曲段一律不碰。
_QUALITY_TOKEN_RE = re.compile(
    r"""(?ix)^(?:
        [48]k(?:60p)?          # 4K / 8K / 4K60p / 8K60p
      | [48]k\d{2,3}p?         # 4K120 / 8K120p
      | \d{3,4}p\d{0,3}        # 1080p / 2160p / 1080p60
      | \d{2,3}fps             # 60fps / 120fps
      | 60p                    # 60p
      | h\.?26[45]|hevc|av1|vp9
    )$"""
)


def _strip_quality_tags(name: str) -> str:
    """剥掉标题方括号内残留的画质 / 帧率 / 编码标签；括号因此变空则整段去掉。"""
    if not name or "[" not in name:
        return name

    def _clean(match: "re.Match[str]") -> str:
        tokens = [t for t in match.group(1).split() if not _QUALITY_TOKEN_RE.match(t)]
        inner = " ".join(tokens)
        return f"[{inner}]" if inner else ""

    return re.sub(r"\[([^\[\]]*)\]", _clean, name).strip()


# 主体身份标注的括号：业主口径「不要括号」（2026-09-22）——「STAYC（组合）」要写成「STAYC组合」。
# ⚠ 身份词位置**不对称**（业主 2026-09-22 二次口径）：组合**后置**（`STAYC组合`）、艺人**前置**（`艺人张元英`）。
# 这里做两件事：① 剥掉「（组合）/（艺人）」括号；② 把后置的「X艺人」翻成前置（模型容易照组合的写法类推）。
# 两个替换本身都是安全的（不会留下残句），所以做成兜底，不指望模型每次都听话。
# ⚠「表演 → 表演歌曲」那类需要理解语序的改写**不做**后处理，只靠提示词约束。
_IDENTITY_PAREN_RE = re.compile(
    r"([^\s（）()，。、；：！？]{1,24})(\s*)[（(]\s*(组合|艺人)\s*[)）]"
)
# 后置「X艺人」→ 前置「艺人X」。两个约束都必要：
#   ① 「艺人」后面必须紧跟谓语/活动词（于/在/表演…），否则会误伤「该视频为艺人张元英的直拍」这类
#      **本来就正确**的前置写法（那是「艺人 + 名字」，不是「名字 + 身份词」）；
#   ② 名字段不得含「的/为/是/该/此/这/那」——挡掉「该视频中的艺人在舞台上表演」这种把前文当名字的误伤。
# ⚠ 已知不覆盖的边角：句首「韩国艺人在…」这种「国名前缀 + 艺人 + 谓语」仍会被误判（不硬做，靠提示词约束）。
_IDENTITY_TRAIL_RE = re.compile(
    r"(?:^|(?<=[\s，。、；：！？]))"
    r"((?![^\s，。、；：！？（）()]*[的为是该此这那])[^\s，。、；：！？（）()]{1,12}?)\s*艺人"
    r"(?=\s*(?:于|在|表演|发布|出演|带来|出席|参加|登上|亮相|与|携手))"
)


def _strip_identity_parens(text: str) -> str:
    """身份标注规范化：去括号 + 把身份词摆到业主口径的位置。

    组合 → `STAYC组合`（身份词**后置**）；艺人 → `艺人张元英`（身份词**前置**）。只动身份标注，其余一字不改。
    """
    if not text:
        return text
    if "（" in text or "(" in text:
        text = _IDENTITY_PAREN_RE.sub(
            lambda m: (
                m.group(1) + m.group(2) + "组合" if m.group(3) == "组合" else "艺人" + m.group(1)
            ),
            text,
        )
    return _IDENTITY_TRAIL_RE.sub(lambda m: "艺人" + m.group(1), text)


def _normalize(data: dict) -> dict:
    result: Dict[str, Any] = {}
    for key in (
        "original_title",
        "name",
        "chinese_name",
        "event_name",
        "subject_artist_name",
        "chinese_description",
    ):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if key == "name":
                text = _strip_quality_tags(text)
            if key == "chinese_description":
                text = _strip_identity_parens(text)
            if text:
                result[key] = text
    if "video_type" in data or "video_types" in data:
        raws: List[str] = []
        raw_primary = data.get("video_type")
        if isinstance(raw_primary, str):
            raws.append(raw_primary)
        raw_extra = data.get("video_types")
        if isinstance(raw_extra, list):
            raws.extend(v for v in raw_extra if isinstance(v, str))
        candidates: List[str] = []
        family_folded: List[str] = []
        for raw in raws:
            if not isinstance(raw, str) or not raw.strip():
                continue
            s = raw.strip()
            if s in VIDEO_TYPES:
                if s not in candidates:
                    candidates.append(s)
                continue
            folded = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s.lower())
            if folded in _VIDEO_TYPE_FOLD:
                t = _VIDEO_TYPE_FOLD[folded]
            elif folded in _TYPE_SYNONYMS:
                t = _TYPE_SYNONYMS[folded]
            elif any(hint in folded for hint in _MIX_HINTS):
                t = "MixEdit"
            else:
                if any(hint in folded for hint in _FAMILY_HINTS):
                    family_folded.append(folded)
                continue
            if t not in candidates:
                candidates.append(t)
        inferred = False
        if not candidates and family_folded:
            inferred_type = _infer_fancam_type(" ".join(family_folded))
            candidates = [inferred_type]
            inferred = True
        if candidates:
            from app.services.video_meta import normalize_video_types

            primary, types = normalize_video_types(candidates[:2], candidates[0])
            result["video_type"] = primary
            result["video_types"] = types
            if inferred:
                result["notice"] = (
                    f"AI 返回的视频类型「{data.get('video_type') or data.get('video_types') or ''}」"
                    f"无法精确匹配，已按 {inferred_type} 处理，可手动调整"
                )
        else:
            raw_desc = "、".join(
                dict.fromkeys(r.strip() for r in raws if isinstance(r, str) and r.strip())
            ) or "空"
            result["notice"] = f"AI 未能识别视频类型（返回值：{raw_desc}），已保留当前选择"
    performance_date = data.get("performance_date")
    if isinstance(performance_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", performance_date.strip()):
        result["performance_date"] = performance_date.strip()
    if "is_solo" in data and isinstance(data.get("is_solo"), bool):
        result["is_solo"] = data["is_solo"]
    for key in (
        "suggested_songs",
        "suggested_albums",
        "suggested_artists",
        "suggested_groups",
    ):
        value = data.get(key)
        if isinstance(value, list):
            items: List[str] = []
            for v in value:
                if isinstance(v, str) and v.strip():
                    name = v.strip()
                    if name not in items:
                        items.append(name)
            if items:
                result[key] = items[:10]
    raw_tracks = data.get("suggested_tracks")
    if isinstance(raw_tracks, list):
        tracks: List[Dict[str, Any]] = []
        for item in raw_tracks[:10]:
            if not isinstance(item, dict):
                continue
            song = item.get("song") or item.get("name")
            if not isinstance(song, str) or not song.strip():
                continue
            albums_raw = item.get("albums")
            if albums_raw is None:
                albums_raw = item.get("album")
            albums: List[str] = []
            if isinstance(albums_raw, str) and albums_raw.strip():
                albums = [albums_raw.strip()]
            elif isinstance(albums_raw, list):
                for a in albums_raw:
                    if isinstance(a, str) and a.strip() and a.strip() not in albums:
                        albums.append(a.strip())
            tracks.append({"song": song.strip(), "albums": albums[:5]})
        if tracks:
            result["suggested_tracks"] = tracks
            if "suggested_songs" not in result:
                result["suggested_songs"] = [t["song"] for t in tracks]
    return result


def suggest_ingest(
    cfg: Dict[str, Any],
    video_info: Dict[str, Any],
    current: Dict[str, Any],
    *,
    library_context: Optional[Dict[str, Any]] = None,
    aux_notes: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> dict:
    """调用 AI 生成入库元数据建议。

    `system_prompt` 由调用方按 AI 设置里的自定义中文简介规则渲染
    （见 build_system_prompt）；为空时退回内置默认提示词 SYSTEM_PROMPT。
    """
    if not cfg.get("base_url") or not cfg.get("model"):
        raise ValueError("请先在「入库 AI 设置」中填写 API Base URL 与模型")

    info_clean = _clip_video_info(video_info)
    resolved_relations = video_info.get("resolved_relations")
    if not isinstance(resolved_relations, dict):
        resolved_relations = {}
    current_clean = {k: v for k, v in current.items() if v not in (None, "")}

    # 无 info.json 判定：缺失来源字段 → 文件名与技术参数是唯一线索，提示词走专项分支
    source_keys = ("title", "fulltitle", "webpage_url", "uploader", "channel", "upload_date")
    if any(info_clean.get(k) for k in source_keys):
        info_label = "视频信息（来自 info.json）："
        no_json_directive = ""
    else:
        info_label = "视频信息（该视频没有 info.json 元数据，以下 file_name 文件名与技术参数是全部线索）："
        no_json_directive = (
            "\n注意：该视频缺少 info.json，请以 file_name（文件名）为第一线索解析艺人、歌曲、事件类型与日期"
            "（常见命名如「Artist - Song MV_240102」「240101 SBS 인기가요_Artist_Song」；文件名里的 [4K] / 60fps 一类画质前缀是来源标签，直接忽略）。"
            "结合 width/height 等技术参数交叉校验类型（竖屏且很短的片段才是 ShortVideo，混剪/合集用 MixEdit）；"
            "文件名含打歌台关键字（SBS/MBC/KBS/Mnet/인기가요/음악중심 等）→ PerformanceVideo 并给出 event_name；"
            "含粉丝见面会关键字（Fan Meeting / FM / 粉丝见面会）→ SpecialVideo。"
            "若文件名无法可靠解析出艺人或歌曲，对应字段必须设为 null 并在 notice 中说明原因，严禁编造。"
        )

    # Bilibili 专项提示：NFO/Bili23-json 元数据 + 粉丝二创语境
    bili_directive = ""
    if info_clean.get("extractor") == "bilibili":
        bili_directive = (
            "\n注意：该视频来自哔哩哔哩（Bilibili），元数据来自 NFO/json 而非 YouTube info.json。"
            "标题中【】内通常是艺人/组合名；uploader 为投稿粉丝（up主），不是官方频道，"
            "不能作为官方来源依据；标题尾部 8 位数字（如 20260822）通常为表演/投稿日期。"
            "歌曲识别请结合 title（标题）与 description（简介）综合判断（B站内容多为翻跳/二创，"
            "表演的常是既有歌曲）；判定类型时优先参考 bili_tags：含「直拍」→ Fancam；"
            "含「翻跳/翻唱」→ CoverStage；含「合作舞台/联合舞台」且是两组及以上同台 → CollabStage；"
            "含「舞台」→ PerformanceVideo 或 SpecialStage。"
            "若无法从标题与简介可靠判断歌曲，suggested_songs 留空并在 notice 说明，严禁编造。"
        )

    # 本库候选：组合/成员名单 + 模糊匹配的艺人与歌曲，识别时优先在候选中做别名匹配
    library_block = ""
    if isinstance(library_context, dict) and library_context:
        library_block = (
            "\n\n库内候选（来自本库，用于辅助识别）：\n"
            f"{json.dumps(library_context, ensure_ascii=False, indent=1)}\n"
            "规则：suggested_artists / suggested_groups / suggested_songs / subject_artist_name 等名称"
            "应优先与上方候选做别名匹配（韩文/罗马音/中文互相对应也算同一人），命中时使用候选中的官方名称，"
            "严禁为已在候选中的实体建议新建；确实无法匹配时才建议新建。"
            "matched_tracks 给出库内「歌曲 → 所属专辑」的官方配对：suggested_tracks 中对应歌曲的 albums"
            "必须直接使用配对中的官方专辑名，不确定归属时 albums 留空，"
            "严禁为 matched_tracks 中已配对的歌曲编造其他专辑。"
        )

    # 用户手动补充的辅助识别备注（如百科链接、「这是某组合的某成员」）
    aux_block = ""
    if aux_notes and aux_notes.strip():
        aux_block = (
            "\n\n用户补充提示（人工提供的辅助识别信息，可信度高，请优先采纳）：\n"
            f"{aux_notes.strip()[:600]}\n"
        )

    user_prompt = (
        f"{info_label}\n"
        f"{json.dumps(info_clean, ensure_ascii=False, indent=2)}"
        f"{no_json_directive}{bili_directive}\n\n"
        "库内已选关联（用户已从媒体库中选中的歌曲/专辑/艺人/组合，生成标题与建议时应优先引用这些官方名称）：\n"
        f"{json.dumps(resolved_relations, ensure_ascii=False, indent=2)}\n\n"
        "用户当前表单值（作为参考，未填则为空）：\n"
        f"{json.dumps(current_clean, ensure_ascii=False, indent=2)}"
        f"{library_block}"
        f"{aux_block}\n\n"
        "请生成入库元数据 JSON。"
    )
    messages = [
        {"role": "system", "content": (system_prompt or "").strip() or SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    return _normalize(_parse_json(chat_completion(cfg, messages)))


def _clip_video_info(video_info: Dict[str, Any]) -> Dict[str, Any]:
    """丢掉空值，并把过长简介截断，避免撑爆上下文。"""
    clean: Dict[str, Any] = {}
    for key, value in video_info.items():
        if value is None or key == "resolved_relations":
            continue
        if isinstance(value, str) and key == "description" and len(value) > 2000:
            value = value[:2000] + "…"
        clean[key] = value
    return clean


ALBUM_SEARCH_PROMPT = """你是 K-pop 专辑知识助手。根据已给的歌曲名、表演者和视频信息判断收录专辑：

1. 只依据你确定知道的发行信息作答。当前请求没有联网检索工具，不要假装已经打开过 Apple Music / Melon / Spotify。
2. 数字单曲 / OST / Remix / 直播混音通常不属于任何正式专辑，返回空数组。
3. 区分正规、迷你、单曲专辑层级；小分队歌曲优先归属大队专辑。
4. 不能确定时返回空数组，严禁给出“看起来合理”的专辑名；song_name / performer 仍按实际返回。
5. 若记得具体来源，可在顶层 sources 数组注明，形如
   {"field": "suggested_albums", "value": "UNCUT GEM", "source": "Apple Music：UNCUT GEM - EP"}；
   记不清来源时 sources 为空数组即可。"""


def _extract_sources(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 AI 返回中提取并归一化来源清单，供前端展示核对。"""
    raw = data.get("sources") or data.get("field_sources") or data.get("album_sources")
    if not isinstance(raw, list):
        return []
    clean: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        field = item.get("field") or item.get("key") or ""
        value = item.get("value") or item.get("name") or item.get("album") or item.get("original") or ""
        source = item.get("source") or item.get("site") or item.get("url") or ""
        field, value, source = str(field).strip(), str(value).strip(), str(source).strip()
        if value and source:
            clean.append(
                {
                    "field": field[:100] or "field",
                    "value": value[:300],
                    "source": source[:500],
                }
            )
        if len(clean) >= 20:
            break
    return clean


def search_albums(
    cfg: Dict[str, Any],
    video_info: Dict[str, Any],
    current: Dict[str, Any],
    db: Optional[Session] = None,
    song_names: Optional[List[str]] = None,
    artist_names: Optional[List[str]] = None,
    group_names: Optional[List[str]] = None,
    *,
    album_candidates: Optional[List[str]] = None,
    aux_notes: Optional[str] = None,
) -> dict:
    """调用 AI 根据歌曲名与组合/艺人名判断该歌曲所属的专辑。

    优先使用 AI 入库阶段已识别出的名称；未提供时回退到 current 关联 id 解析。
    """
    if not cfg.get("base_url") or not cfg.get("model"):
        raise ValueError("请先在「入库 AI 设置」中填写 API Base URL 与模型")

    _KEEP_KEYS = ("title", "fulltitle", "description", "uploader", "channel", "webpage_url", "upload_date", "extractor")
    info_clean = _clip_video_info({k: video_info.get(k) for k in _KEEP_KEYS})
    for key, value in list(info_clean.items()):
        if value is not None:
            info_clean[key] = str(value)
    resolved_relations = video_info.get("resolved_relations")
    if not isinstance(resolved_relations, dict):
        resolved_relations = {}

    song_names = song_names or resolved_relations.get("song_names") or []
    artist_names = artist_names or resolved_relations.get("artist_names") or []
    group_names = group_names or resolved_relations.get("group_names") or []

    song = song_names[0] if song_names else ""
    group = group_names[0] if group_names else ""
    artist = artist_names[0] if artist_names else ""
    # 组合名优先，没有组合名才用艺人名
    performer = group or artist

    multi = len(song_names) > 1
    if multi:
        # 多首歌曲：逐首判断所属专辑
        label = "组合" if group else ("艺人" if artist else "表演者")
        performer_desc = f"\n表演者（{label}）：{performer}。" if performer else ""
        user_prompt = (
            f"该视频包含 {len(song_names)} 首歌曲：{'、'.join(song_names)}。{performer_desc}"
            "请分别判断每首歌曲收录于哪张专辑（每首歌最多 3 张，按可能性从高到低）。"
        )
    elif song and performer:
        label = "组合" if group else "艺人"
        user_prompt = f"请你通过歌曲名 {song} 和{label} {performer}，搜索这首歌所属专辑。"
    elif song:
        user_prompt = f"请你通过歌曲名 {song}，搜索这首歌所属专辑。"
    elif performer:
        label = "组合" if group else "艺人"
        user_prompt = f"请你通过{label} {performer} 的歌曲，搜索这首歌所属专辑。"
    else:
        user_prompt = (
            "请先根据视频信息推断歌曲名和组合/艺人名，再判断该歌曲收录于哪张专辑。\n"
            "视频标题通常遵循格式「日期 组合/艺人 - 歌曲 [活动名 视频类型]」："
            "组合名一般在方括号内（如 tripleS、Red Velvet），歌曲名在破折号后。"
            "如果破折号前是成员个人名（如 JiYeon & Kim YooYeon），请以方括号内的组合名作为 performer。"
        )

    if info_clean and (multi or not (song and performer)):
        user_prompt += (
            "\n\n视频信息（补充参考，标题中通常包含歌曲名与组合名）：\n"
            f"{json.dumps(info_clean, ensure_ascii=False, indent=2)}"
        )
    if isinstance(album_candidates, list) and album_candidates:
        user_prompt += (
            "\n\n库内专辑候选（本库中该艺人/组合的影像已关联过的专辑，按可能性优先从中选择）：\n"
            f"{json.dumps(album_candidates, ensure_ascii=False)}\n"
            "若候选中有符合的专辑，suggested_albums 必须使用候选中的官方名称；"
            "候选确实都不符合时再凭可靠发行信息另行给出，仍不确定则空数组。"
        )
    if aux_notes and aux_notes.strip():
        user_prompt += (
            "\n\n用户补充提示（人工提供的辅助信息，可信度高，请优先采纳）：\n"
            f"{aux_notes.strip()[:600]}\n"
        )
    if multi:
        user_prompt += (
            '\n\n请只返回一个 JSON 对象：'
            '{"song_name": "第一首歌曲名", "performer": "组合名或艺人名", '
            '"suggested_albums": ["第一首歌曲的专辑", ...], '
            '"multi_songs": [{"song": "歌曲名1", "albums": ["专辑名", ...]}, '
            '{"song": "歌曲名2", "albums": ["专辑名", ...]}]}\n'
            "不要输出任何其他文字或代码块标记。"
        )
    else:
        user_prompt += (
            '\n\n请只返回一个 JSON 对象：{"song_name": "歌曲名", "performer": "组合名或艺人名", "suggested_albums": ["专辑官方名称", ...]}\n'
            "不要输出任何其他文字或代码块标记。"
        )
    messages = [
        {"role": "system", "content": ALBUM_SEARCH_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    logger.info("[search-albums] 提示词:\n%s", user_prompt)

    data = _parse_json(chat_completion(cfg, messages))
    logger.info("[search-albums] AI 原始返回: %s", json.dumps(data, ensure_ascii=False)[:2000])
    normalized = _normalize(data)
    albums = normalized.get("suggested_albums")
    # 兼容 AI 可能返回的其他专辑字段名
    if not albums:
        for alt in ("albums", "album_names", "albums_list"):
            alt_val = data.get(alt)
            if isinstance(alt_val, list):
                albums = [v for v in alt_val if isinstance(v, str) and v.strip()]
                if albums:
                    break
    result: Dict[str, Any] = {"suggested_albums": albums if isinstance(albums, list) else []}
    for key in ("song_name", "performer"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    # 多首歌曲时按歌曲分组返回，供前端逐首展示与关联
    multi_raw = data.get("multi_songs") or data.get("songs")
    if isinstance(multi_raw, list):
        multi: List[Dict[str, Any]] = []
        for m in multi_raw:
            if not isinstance(m, dict):
                continue
            m_song = m.get("song") or m.get("song_name")
            if not isinstance(m_song, str) or not m_song.strip():
                continue
            raw_albums = m.get("albums") or m.get("suggested_albums") or []
            if not isinstance(raw_albums, list):
                continue
            albums_clean = [v for v in raw_albums if isinstance(v, str) and v.strip()]
            if albums_clean:
                multi.append({"song": m_song.strip(), "albums": albums_clean[:3]})
        if multi:
            result["multi_songs"] = multi
    sources = _extract_sources(data)
    if sources:
        result["sources"] = sources
    return result


ALBUM_TYPE_VALUES = ("Single", "MiniAlbum", "FullAlbum", "Repackage", "OST", "Compilation", "Other")
GENDER_VALUES = ("Female", "Male", "Other")
GROUP_TYPE_VALUES = ("Girl Group", "Boy Group", "Co-ed", "Project", "Sub-unit")

ENTITY_FIELD_SPECS: Dict[str, List[Dict[str, Any]]] = {
    "songs": [
        {"key": "name", "label": "名称", "kind": "text"},
        {"key": "chinese_name", "label": "中文名", "kind": "text"},
        {"key": "english_name", "label": "英文名", "kind": "text"},
        {"key": "korean_name", "label": "韩文名", "kind": "text"},
        {"key": "song_type", "label": "歌曲类型", "kind": "text"},
        {"key": "release_date", "label": "发行日期 YYYY-MM-DD", "kind": "date"},
        {"key": "duration", "label": "时长（秒）", "kind": "number"},
        {"key": "album_ids", "label": "所属专辑（优先使用库内专辑名；库内没有时返回正确专辑名，系统会查重提示并新建）", "kind": "album_names"},
        {"key": "description", "label": "简介", "kind": "text"},
    ],
    "albums": [
        {"key": "name", "label": "名称", "kind": "text"},
        {"key": "chinese_name", "label": "中文名", "kind": "text"},
        {"key": "english_name", "label": "英文名", "kind": "text"},
        {"key": "korean_name", "label": "韩文名", "kind": "text"},
        {"key": "album_type", "label": "专辑类型", "kind": "select", "options": ALBUM_TYPE_VALUES},
        {"key": "release_date", "label": "发行日期 YYYY-MM-DD", "kind": "date"},
        {"key": "description", "label": "简介", "kind": "text"},
    ],
    "artists": [
        {"key": "name", "label": "名称", "kind": "text"},
        {"key": "stage_name", "label": "艺名", "kind": "text"},
        {"key": "chinese_name", "label": "中文名", "kind": "text"},
        {"key": "english_name", "label": "英文名", "kind": "text"},
        {"key": "korean_name", "label": "韩文名", "kind": "text"},
        {"key": "gender", "label": "性别", "kind": "select", "options": GENDER_VALUES},
        {"key": "occupation", "label": "职业", "kind": "text"},
        {"key": "birth_place", "label": "出生地", "kind": "text"},
        {"key": "debut_date", "label": "出道日期 YYYY-MM-DD", "kind": "date"},
        {"key": "birth_date", "label": "出生日期 YYYY-MM-DD", "kind": "date"},
        {"key": "description", "label": "简介", "kind": "text"},
    ],
    "groups": [
        {"key": "name", "label": "名称", "kind": "text"},
        {"key": "chinese_name", "label": "中文名", "kind": "text"},
        {"key": "english_name", "label": "英文名", "kind": "text"},
        {"key": "korean_name", "label": "韩文名", "kind": "text"},
        {"key": "group_type", "label": "组合类型", "kind": "select", "options": GROUP_TYPE_VALUES},
        {"key": "gender_type", "label": "性别类型", "kind": "text"},
        {"key": "origin_country", "label": "出道国家/地区", "kind": "text"},
        {"key": "debut_date", "label": "出道日期 YYYY-MM-DD", "kind": "date"},
        {"key": "company_ids", "label": "所属公司（优先使用库内公司名；库内没有时返回正确公司名，系统会自动新建）", "kind": "company_names"},
        {"key": "description", "label": "简介", "kind": "text"},
    ],
    "companies": [
        {"key": "name", "label": "名称", "kind": "text"},
        {"key": "chinese_name", "label": "中文名", "kind": "text"},
        {"key": "english_name", "label": "英文名", "kind": "text"},
        {"key": "korean_name", "label": "韩文名", "kind": "text"},
        {"key": "company_type", "label": "公司类型", "kind": "text"},
        {"key": "description", "label": "简介", "kind": "text"},
    ],
}


def _normalize_entity_field(spec: Dict[str, Any], value: Any) -> Any:
    """按字段规范归一化 AI 返回的单个字段值。"""
    if value is None:
        return None
    kind = spec.get("kind")
    if kind == "date":
        if isinstance(value, str):
            s = value.strip()
            return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None
        return None
    if kind == "number":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value) if isinstance(value, int) or float(value).is_integer() else value
        if isinstance(value, str):
            s = value.strip()
            try:
                n = float(s)
            except ValueError:
                return None
            return int(n) if n.is_integer() else n
        return None
    if kind == "select":
        if isinstance(value, str) and value in (spec.get("options") or []):
            return value.strip()
        return None
    if isinstance(value, str):
        s = value.strip()
        return s if s else None
    return None


def _company_name_variants(c: Company) -> List[str]:
    """收集公司的全部可匹配名称（官方名 + 各语言名 + 别名）。"""
    vals: List[str] = [c.name, c.chinese_name, c.english_name, c.korean_name]
    if isinstance(c.aliases, list):
        vals.extend(a for a in c.aliases if isinstance(a, str))
    return [v.strip() for v in vals if v and v.strip()]


def _member_name_variants(a: Artist) -> List[str]:
    """收集艺人的全部姓名变体（官方名 + 艺名 + 各语言名 + 别名），供成员名单对照。"""
    vals: List[str] = [a.name, a.stage_name, a.chinese_name, a.korean_name, a.english_name]
    if isinstance(a.aliases, list):
        vals.extend(x for x in a.aliases if isinstance(x, str))
    return list(dict.fromkeys(v.strip() for v in vals if v and v.strip()))


def _match_library_group(db: Session, current: Dict[str, Any]) -> Optional[Group]:
    """按当前表单已填名称字段匹配库内已有组合（忽略大小写，精确后包含）。"""
    if db is None:
        return None
    candidates: List[str] = []
    for key in ("chinese_name", "english_name", "name", "korean_name"):
        v = current.get(key)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return None
    groups = db.scalars(select(Group).where(Group.deleted_at.is_(None))).all()
    for g in groups:
        # 别名（含粉丝名）不参与待整理匹配：粉丝名常是英语常用词，容易误挂组合
        variants: List[str] = [g.name, g.chinese_name, g.english_name, g.korean_name]
        variants = [v.strip().lower() for v in variants if v and v.strip()]
        if not variants:
            continue
        for cand in candidates:
            cl = cand.lower()
            if cl in variants:
                return g
            for var in variants:
                if len(cl) >= 4 and (cl in var or var in cl):
                    return g
    return None


def _match_company_names(db: Session, value: Any) -> Dict[str, Any]:
    """匹配 AI 返回的公司名称（字符串或数组）。

    返回 {"ids": 匹配到库内公司的 id 列表, "unresolved": 未匹配的公司名列表}。
    匹配顺序：全名精确（忽略大小写）→ 包含匹配（任一方向，长度 >= 4 避免误配）。
    未命中的名称放入 unresolved，交由调用方决定是否新建。
    """
    if db is None:
        return {"ids": [], "unresolved": []}
    companies = db.scalars(
        select(Company).where(Company.deleted_at.is_(None)).order_by(Company.name)
    ).all()
    raw = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    ids: List[int] = []
    unresolved: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name:
            continue
        needle = name.lower()
        matched = None
        for c in companies:
            variants = [v.lower() for v in _company_name_variants(c)]
            if needle in variants:
                matched = c
                break
            if len(needle) >= 4 and any(
                len(v) >= 4 and (needle in v or v in needle) for v in variants
            ):
                matched = c
                break
        if matched is not None:
            if matched.id not in ids:
                ids.append(matched.id)
        elif name not in unresolved:
            unresolved.append(name)
    return {"ids": ids, "unresolved": unresolved}


def _album_name_variants(a: Album) -> List[str]:
    """收集专辑的全部可匹配名称（官方名 + 各语言名 + 别名）。"""
    vals: List[str] = [a.name, a.chinese_name, a.english_name, a.korean_name]
    if isinstance(a.aliases, list):
        vals.extend(x for x in a.aliases if isinstance(x, str))
    return [v.strip() for v in vals if v and v.strip()]


def _match_album_names(db: Session, value: Any) -> Dict[str, Any]:
    """匹配 AI 返回的专辑名称（字符串或数组）。

    返回 {"ids": 匹配到库内专辑的 id 列表, "unresolved": 未匹配的专辑名列表}。
    匹配顺序：全名精确（忽略大小写）→ 包含匹配（任一方向，长度 >= 4 避免误配）。
    未命中的名称放入 unresolved，交由调用方查重提示后新建。
    """
    if db is None:
        return {"ids": [], "unresolved": []}
    albums = db.scalars(
        select(Album).where(Album.deleted_at.is_(None)).order_by(Album.name)
    ).all()
    raw = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    ids: List[int] = []
    unresolved: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name:
            continue
        needle = name.lower()
        matched = None
        for a in albums:
            variants = [v.lower() for v in _album_name_variants(a)]
            if needle in variants:
                matched = a
                break
            if len(needle) >= 4 and any(
                len(v) >= 4 and (needle in v or v in needle) for v in variants
            ):
                matched = a
                break
        if matched is not None:
            if matched.id not in ids:
                ids.append(matched.id)
        elif name not in unresolved:
            unresolved.append(name)
    return {"ids": ids, "unresolved": unresolved}


def _normalize_group_members(
    data: dict, db: Optional[Session] = None, group_id: Optional[int] = None
) -> Optional[List[dict]]:
    """归一化组合分析返回的成员名单。

    group_id：正在编辑的组合。用于成员匹配的组合上下文判定——
    普通组合同名默认新建（同名成员大概率是不同人），小分队优先
    复用母队艺人（重合队员是常态），其余交给用户在导入弹窗手动选择。
    """
    raw = data.get("members")
    if not isinstance(raw, list):
        return None
    from app.services.name_match import normalize_person_name

    result: List[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("stage_name") or item.get("korean_name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        key = normalize_person_name(name) or name.casefold()
        if key in seen:
            continue
        row: Dict[str, Any] = {"name": name}
        for k in ("stage_name", "korean_name", "chinese_name", "english_name"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                row[k] = v.strip()
        aliases: List[str] = []
        a = item.get("aliases")
        if isinstance(a, str):
            aliases = [x.strip() for x in re.split(r"[,，、;/；]", a) if x.strip()]
        elif isinstance(a, list):
            aliases = [x.strip() for x in a if isinstance(x, str) and x.strip()]
        if aliases:
            row["aliases"] = aliases[:12]
        for k in ("join_date", "leave_date"):
            v = item.get(k)
            if isinstance(v, str):
                s = v.strip()
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                    row[k] = s
        positions: List[str] = []
        p = item.get("positions")
        if isinstance(p, str):
            positions = [x.strip() for x in re.split(r"[/,，、;；]", p) if x.strip()]
        elif isinstance(p, list):
            positions = [x.strip() for x in p if isinstance(x, str) and x.strip()]
        if positions:
            row["positions"] = positions[:10]
        status = item.get("status")
        if isinstance(status, str) and status.strip() in ("Active", "Former", "Inactive"):
            row["status"] = status.strip()
        elif row.get("leave_date"):
            row["status"] = "Former"
        else:
            row["status"] = "Active"
        extras = [
            row.get("stage_name"),
            row.get("korean_name"),
            row.get("chinese_name"),
            row.get("english_name"),
            *(row.get("aliases") or []),
        ]
        seen.add(key)
        for extra in extras:
            ek = normalize_person_name(extra) if extra else ""
            if ek:
                seen.add(ek)
        if db is not None:
            from app.services.name_match import match_artist_candidates

            cands = match_artist_candidates(db, name, extra=[x for x in extras if x])
            if cands:
                row["candidates"] = [
                    {
                        "id": c.id,
                        "name": c.name,
                        "korean_name": c.korean_name,
                        "groups": _artist_group_labels(db, c.id),
                    }
                    for c in cands
                ]
                default_id = _pick_default_member_match(db, group_id, cands)
                if default_id is not None:
                    row["match_mode"] = "artist"
                    row["matched_artist_id"] = default_id
                    row["matched_artist_name"] = next(
                        (c.name for c in cands if c.id == default_id), None
                    )
                else:
                    row["match_mode"] = "new"
                row["conflict"] = _has_other_group_membership(db, cands, group_id)
            else:
                row["match_mode"] = "new"
        else:
            row["match_mode"] = "new"
        result.append(row)
    return result or None


def _artist_group_labels(db: Session, artist_id: int) -> List[str]:
    """艺人当前所属组合及身份，供导入弹窗展示（如「NMIXX（现役）」）。"""
    rows = db.execute(
        select(Group.name, GroupMembership.status)
        .join(Group, GroupMembership.group_id == Group.id)
        .where(GroupMembership.artist_id == artist_id, Group.deleted_at.is_(None))
    ).all()
    return [
        f"{name}（{'现役' if status == 'Active' else '已退出'}）" for name, status in rows
    ]


def _has_other_group_membership(
    db: Session, candidates: List[Artist], group_id: Optional[int]
) -> bool:
    """候选艺人中是否有人已属于其他组合（同名不同人的强信号）。"""
    for c in candidates:
        stmt = select(GroupMembership.id).where(GroupMembership.artist_id == c.id)
        if group_id is not None:
            stmt = stmt.where(GroupMembership.group_id != group_id)
        if db.scalar(stmt.limit(1)) is not None:
            return True
    return False


def _pick_default_member_match(
    db: Session, group_id: Optional[int], candidates: List[Artist]
) -> Optional[int]:
    """组合成员导入的默认关联对象；返回 None = 默认新建。

    - 候选已是本组合成员 → 幂等复用（重复分析同一组合不会重复建人）
    - 小分队（有母队）且候选是母队成员 → 复用母队艺人（唯一命中时）
    - 其余（普通组合的同名、候选属于其他组合）→ 默认新建，交用户手动选择
    """
    if group_id is None or not candidates:
        return None
    group = Group.get_active(db, group_id)
    if group is None:
        return None
    ids = [c.id for c in candidates]
    own = set(
        db.scalars(
            select(GroupMembership.artist_id).where(
                GroupMembership.group_id == group_id,
                GroupMembership.artist_id.in_(ids),
            )
        )
    )
    if own:
        for c in candidates:
            if c.id in own:
                return c.id
        return None
    if group.parent_group_id:
        parent_hits = [
            c
            for c in candidates
            if db.scalar(
                select(GroupMembership.id).where(
                    GroupMembership.group_id == group.parent_group_id,
                    GroupMembership.artist_id == c.id,
                )
            )
            is not None
        ]
        if len(parent_hits) == 1:
            return parent_hits[0].id
    return None


def _normalize_memberships(data: dict) -> Optional[List[dict]]:
    """归一化 AI 返回的艺人群组经历数组。"""
    raw = data.get("group_memberships")
    if not isinstance(raw, list):
        return None
    result: List[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("group_name")
        if not isinstance(name, str) or not name.strip():
            continue
        row: Dict[str, Any] = {"group_name": name.strip()}
        for k in ("join_date", "leave_date"):
            v = item.get(k)
            if isinstance(v, str):
                s = v.strip()
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                    row[k] = s
        positions: List[str] = []
        p = item.get("positions")
        if isinstance(p, str):
            positions = [x.strip() for x in re.split(r"[/,，、;；]", p) if x.strip()]
        elif isinstance(p, list):
            positions = [x.strip() for x in p if isinstance(x, str) and x.strip()]
        if positions:
            row["positions"] = positions[:10]
        result.append(row)
    return result or None


def analyze_entity(
    cfg: Dict[str, Any],
    entity_type: str,
    current: Dict[str, Any],
    memberships: List[dict],
    group_names: List[str],
    db: Optional[Session] = None,
    only_fields: Optional[List[str]] = None,
    group_id: Optional[int] = None,
    aux_notes: Optional[str] = None,
) -> dict:
    """调用 AI 分析并自动填充数据库编辑表单。

    only_fields：字段锁配套参数。给定后只把这些键（外加 memberships 保留键）
    作为 AI 的填写任务，其余锁定字段仅随 current 作为上下文出现，
    不进入任务清单、不做归一化，返回 JSON 也不应包含它们。
    """
    if not cfg.get("base_url") or not cfg.get("model"):
        raise ValueError("请先在「入库 AI 设置」中填写 API Base URL 与模型")

    spec = ENTITY_FIELD_SPECS.get(entity_type)
    if spec is None:
        raise ValueError(f"不支持的实体类型: {entity_type}")

    # 字段锁裁剪：任务清单 = only_fields 与实体字段定义的交集
    only_set = {str(k) for k in only_fields} if only_fields is not None else None
    task_spec = spec if only_set is None else [f for f in spec if f["key"] in only_set]
    # memberships 是保留区块键：艺术家经历 / 组合成员的 AI 任务由它控制
    members_ask = only_set is None or "memberships" in only_set
    desc_ask = only_set is None or "description" in only_set
    # 实体字段全锁但成员/经历放开 → 「仅成员」模式，属合法请求
    members_only = not task_spec
    if members_only and not members_ask:
        raise ValueError("所有字段均已锁定，没有需要 AI 填写的内容")

    field_lines: List[str] = []
    for f in task_spec:
        hint = f.get("label", "")
        if f.get("kind") == "select":
            hint = f"{hint}（{'、'.join(f['options'])}）"
        field_lines.append(f"{f['key']}: {hint}")
    fields_part = "\n".join(field_lines)

    current_clean = {k: v for k, v in current.items() if v not in (None, "")}
    memberships_part = (
        json.dumps(memberships, ensure_ascii=False, indent=2) if memberships else "无"
    )
    group_names_part = "、".join(group_names) if group_names else "（无）"

    if only_set is not None and members_only:
        user_prompt = (
            f"实体类型：{entity_type}。本次无需填写实体字段（均已锁定），"
            "只需按后续说明返回成员/经历信息。\n"
            "已知信息（上下文，仅供理解与保持一致，禁止改写）：\n"
            f"{json.dumps(current_clean, ensure_ascii=False, indent=2)}\n"
        )
    elif only_set is not None:
        user_prompt = (
            f"实体类型：{entity_type}。本次只需填写下列字段，返回 JSON 中不要出现其他字段"
            "（无法确定的字段设为 null，日期一律 YYYY-MM-DD）：\n"
            f"{fields_part}\n"
            "已知信息（上下文，仅供理解与保持一致，禁止改写或重复返回）：\n"
            f"{json.dumps(current_clean, ensure_ascii=False, indent=2)}\n"
        )
    else:
        user_prompt = (
            f"实体类型：{entity_type}。请按以下字段键名返回 JSON，无法确定的字段设为 null，"
            "日期一律 YYYY-MM-DD：\n"
            f"{fields_part}\n"
            "当前表单值（已填的保留规范化，缺的补齐）：\n"
            f"{json.dumps(current_clean, ensure_ascii=False, indent=2)}\n"
        )
    if entity_type == "artists" and members_ask:
        user_prompt += (
            "另返回 group_memberships 数组（只填有把握的，每项含 group_name、join_date、"
            "leave_date、positions，positions 为团内担当字符串或数组）：\n"
            f"- 库内已有组合名：{group_names_part}\n"
            f"- 当前组合经历：{memberships_part}\n"
        )
    if entity_type == "groups" and db is not None:
        if only_set is None or "company_ids" in only_set:
            companies = db.scalars(
                select(Company)
                .where(Company.deleted_at.is_(None))
                .order_by(Company.name)
            ).all()
            company_names_part = (
                "、".join(c.name for c in companies if c.name) if companies else "（无）"
            )
            user_prompt += (
                "库内已有公司名（company_ids 优先使用下列名称；"
                "若库内没有该公司，可返回正确的公司名，系统会自动新建）：\n"
                f"{company_names_part}\n"
            )
        if members_ask:
            artists = db.scalars(
                select(Artist)
                .where(Artist.deleted_at.is_(None))
                .order_by(Artist.name)
                .limit(200)
            ).all()
            artist_bits: List[str] = []
            for a in artists:
                bit = a.stage_name or a.name
                if a.korean_name and a.korean_name != bit:
                    bit = f"{bit}/{a.korean_name}"
                if bit:
                    artist_bits.append(bit)
            artist_names_part = "、".join(artist_bits) if artist_bits else "（无）"
            user_prompt += (
                "另返回 members 数组：该组合的现任及已退成员（有把握才填，禁止编造）。每项字段：\n"
                "- name: 官方罗马音主名（与官网/Melon 一致，如 Jeong Saebi）\n"
                "- stage_name: 艺名（可与 name 相同）\n"
                "- korean_name: 韩文名（强烈建议填写，用于消歧）\n"
                "- chinese_name / english_name: 有则填\n"
                "- aliases: 其他常见写法数组，必须包含无空格/有空格/连字符变体"
                "（如 Jeong Saebi 要带 JEONG SAEBI、JEONG SAE BI）\n"
                "- join_date / leave_date: YYYY-MM-DD，现任 leave_date 为 null\n"
                "- positions: 团内担当数组\n"
                "- status: Active 或 Former\n"
                f"库内已有艺人（优先用下列名称，不要因空格不同再造一条）：{artist_names_part}\n"
            )
            group = _match_library_group(db, current)
            if group is not None and desc_ask:
                artists = db.scalars(
                    select(Artist)
                    .join(GroupMembership, GroupMembership.artist_id == Artist.id)
                    .where(
                        GroupMembership.group_id == group.id,
                        GroupMembership.status == "Active",
                        Artist.deleted_at.is_(None),
                    )
                    .order_by(GroupMembership.join_date.asc())
                ).all()
                if artists:
                    member_lines = "\n".join(
                        f"- {' / '.join(_member_name_variants(a))}" for a in artists
                    )
                    user_prompt += (
                        "库内成员名单（description 写入成员姓名时必须与下列名单对照：\n"
                        "同一人全文只出现一种写法、优先使用官方汉字名/官方罗马字，"
                        "严禁重复音译或照抄半中英混排拼写；名单外的新成员请单独标注说明）：\n"
                        f"{member_lines}\n"
                    )
    if entity_type == "songs" and db is not None and (
        only_set is None or "album_ids" in only_set
    ):
        albums = db.scalars(
            select(Album).where(Album.deleted_at.is_(None)).order_by(Album.name)
        ).all()
        album_names_part = (
            "、".join(a.name for a in albums if a.name) if albums else "（无）"
        )
        user_prompt += (
            "库内已有专辑名（album_ids 优先使用下列名称；"
            "若库内没有该专辑，可返回正确的专辑名，系统会查重提示后新建）：\n"
            f"{album_names_part}\n"
        )
    # 程序侧先抓取 Wikidata/Wikipedia 参考资料，让模型做「阅读理解」而非「回忆」
    reference_sources: List[Dict[str, Any]] = []
    try:
        reference = wiki_service.fetch_reference(entity_type, current)
    except Exception as e:  # noqa: BLE001
        logger.warning("[analyze-entity][%s] 维基参考资料抓取失败: %s", entity_type, e)
        reference = None
    if reference:
        reference_sources = reference.get("sources_for_fields") or []
        reference_clean = {k: v for k, v in reference.items() if v and k != "sources_for_fields"}
        user_prompt += (
            "\n\n参考资料（程序自动抓取的 Wikidata/Wikipedia 真实数据，事实字段必须以此为准，"
            "禁止与其中已明确给出的值冲突；成员/经历日期未给出的按 null 处理，不得自行推断）：\n"
            f"{json.dumps(reference_clean, ensure_ascii=False, indent=2)}\n"
        )
    user_prompt += "只返回一个 JSON 对象，不要任何其他文字。"

    system_prompt = (
        "你是 K-pop 媒体库数据库编辑助手。按用户给定字段键名返回 JSON，必须遵守：\n"
        "\n"
        "1. 资料优先：用户消息若提供了「参考资料」（程序抓取的 Wikidata/Wikipedia 数据），\n"
        "   事实字段必须与参考资料一致，禁止改写、增删或与资料冲突；资料未覆盖且未明确为 null 的字段，\n"
        "   若你的运行环境支持联网检索则应检索核实（至少 2 个独立来源），否则凭已有知识填写时必须在\n"
        "   该字段来源中注明「模型记忆，可能过时」，没有把握就置 null。仅凭记忆冒充检索结果视为不合格。\n"
        "2. 来源优先：优先采信官方/主流权威来源——官方账号、公司官网、Melon 艺人页、Apple Music、\n"
        "   维基百科（英文版）等；中文百科/聚合站仅作参考，与权威来源冲突时以权威来源为准。\n"
        "3. 出道日口径：以「官方认定」的出道日期为准（官方资料、Melon 艺人页、维基 infobox 等明确标注的\n"
        "   出道日期字段优先，不做自行定义）。\n"
        "   若存在先行单曲与正式专辑两个日期口径：官方口径明确则采用官方口径；官方未明确时，采用主流共识，\n"
        "   并将两个口径及各自来源一并写入 description 说明（如「先行单曲《I DO ME》2025-02-24，首专\n"
        "   《UNCUT GEM》2025-03-24，官方口径以 XX 为准」），严禁混淆或编造一个折中日期。\n"
        "4. 日期来源绑定：日期值必须是来源中出现的原始字符串（YYYY-MM-DD），严禁推算、改写或凭印象编造。\n"
        "5. 字段冲突处理：多来源不一致时优先官方口径；仍无法确定置 null。\n"
        "6. 禁止编造：查不到确切值的字段一律 null，不得给出\"看起来合理\"的数值或名称。\n"
        "7. 简介（description）须基于检索到的真实资料撰写，不得虚构经历或奖项。\n"
        "8. 返回来源清单：JSON 顶层额外返回可见的 \"sources\" 数组（可空），每项形如\n"
        "   {\"field\": \"debut_date\", \"value\": \"2025-03-24\", \"source\": \"Wikipedia(en)：KiiiKiii infobox\"}，\n"
        "   用于前端展示核对；查不到来源的字段不要放进 sources。\n"
        "9. 人名规范：description 或成员名单中提及成员/艺人姓名时——\n"
        "   - 同一人全文只出现一次：从多个来源转录时先做姓名判重（同一人的汉字名、韩文名、罗马字只取一种写法），\n"
        "     严禁同一人换两种音译重复列出（如「金彩元、金采沅」同时出现）；\n"
        "   - 写法统一：官方已公布汉字名则统一用汉字名（如 金彩元）；未公布汉字名则统一用官方罗马字/艺名（如 Kotone、Mayu）；\n"
        "   - 严禁照抄来源中的半中英混排拼写（如「金彩 weon」「weon」），须转成规范写法而不是原样引用；\n"
        "   - 音译冲突时以官方/Melon 艺人页/维基 infobox 的拼写为准；若用户提供了库内成员名单，必须与名单对照判重并统一写法。\n"
        "10. 分析组合时必须返回 members 数组（现任+已退，有把握才填）。每项含规范罗马音主名、韩文名、"
        "常见别名（必须包含有空格/无空格/连字符变体，避免 JEONG SAEBI 与 JEONG SAE BI 被当成两个人）。"
    )
    if only_set is not None and not members_ask:
        system_prompt += (
            "\n本次任务不包含组合成员与组合经历（用户已锁定），忽略上一条中关于 members 的要求，"
            "也不要在返回 JSON 中出现 group_memberships 或 members。"
        )

    # 用户手动补充的辅助识别备注（如百科链接、「这是某组合的某成员」），高可信、优先采纳
    if aux_notes and aux_notes.strip():
        user_prompt += (
            "\n\n用户补充提示（人工提供的辅助识别信息，可信度高，请优先采纳并结合其内容检索）：\n"
            f"{aux_notes.strip()[:600]}\n"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    logger.info("[analyze-entity][%s] 提示词:\n%s", entity_type, user_prompt[:4000])

    provider = cfg.get("provider", "openai-compatible")
    try:
        if provider == "anthropic":
            content = _anthropic(cfg, messages)
        else:
            content = _openai_compatible(cfg, messages)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"AI 请求失败（{e.code}）: {body[:300]}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"AI 请求失败: {e.reason}") from e

    data = _parse_json(content)
    logger.info(
        "[analyze-entity][%s] AI 原始返回: %s",
        entity_type,
        json.dumps(data, ensure_ascii=False)[:2000],
    )
    fields: Dict[str, Any] = {}
    company_match: Optional[Dict[str, Any]] = None
    album_match: Optional[Dict[str, Any]] = None
    for f in task_spec:
        raw_value = data.get(f["key"])
        if f.get("kind") == "company_names":
            if company_match is None:
                company_match = _match_company_names(db, raw_value)
            normalized = company_match["ids"] or None
        elif f.get("kind") == "album_names":
            if album_match is None:
                album_match = _match_album_names(db, raw_value)
            normalized = album_match["ids"] or None
        else:
            normalized = _normalize_entity_field(f, raw_value)
        if normalized is not None:
            fields[f["key"]] = normalized
    logger.info("[analyze-entity][%s] 归一化结果: %s", entity_type, json.dumps(fields, ensure_ascii=False)[:2000])

    result: Dict[str, Any] = {"fields": fields}
    if entity_type == "artists" and members_ask:
        memberships_result = _normalize_memberships(data)
        if memberships_result:
            result["group_memberships"] = memberships_result
    if entity_type == "groups" and members_ask:
        members_result = _normalize_group_members(data, db, group_id=group_id)
        if members_result:
            result["members"] = members_result
    if entity_type == "groups" and company_match and company_match["unresolved"]:
        result["unresolved_companies"] = company_match["unresolved"]
    if entity_type == "songs" and album_match and album_match["unresolved"]:
        result["unresolved_albums"] = album_match["unresolved"]
    sources = _extract_sources(data)
    if reference_sources or sources:
        # 程序侧抓取的维基来源排在前面（真实可核对），AI 自报来源排后
        result["sources"] = reference_sources + sources
    return result


# ===== 艺人/组合一句话简介（首页主舞台 tagline） =====

ENTITY_TAGLINE_DEFAULT_PROMPT = """你是 K-pop 音乐杂志的编辑，为一位艺人或组合写一句说明文字。

要求：
- tagline：中文一句话，16–24 字。点出这个人物最鲜明的标签或气质（声线、风格、身份、代表性），像画册图注，不像百科。
- 禁止：出道日期罗列、成员名单、奖项流水账、「著名」「知名」「备受喜爱」等空洞形容、句号结尾。
- 语气克制、有画面感，允许一个轻微的比喻。

只返回一个 JSON 对象，不要输出任何其他文字或代码块标记：
{"tagline": ""}"""


def generate_entity_tagline(
    cfg: Dict[str, Any],
    context: Dict[str, Any],
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """调用 AI 生成艺人/组合的一句话简介。"""
    if not cfg.get("base_url") or not cfg.get("model"):
        raise ValueError("请先在「入库 AI 设置」中填写 API Base URL 与模型")

    system_prompt = (prompt or "").strip() or ENTITY_TAGLINE_DEFAULT_PROMPT
    user_prompt = (
        "以下是该艺人/组合的真实资料（基于库内数据）：\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "请生成一句话简介 JSON。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    logger.info("[entity-tagline] 提示词:\n%s", user_prompt[:4000])

    provider = cfg.get("provider", "openai-compatible")
    try:
        if provider == "anthropic":
            content = _anthropic(cfg, messages)
        else:
            content = _openai_compatible(cfg, messages)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"AI 请求失败（{e.code}）: {body[:300]}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"AI 请求失败: {e.reason}") from e

    data = _parse_json(content)
    logger.info("[entity-tagline] AI 原始返回: %s", json.dumps(data, ensure_ascii=False)[:1000])
    tagline = str(data.get("tagline") or "").strip() if isinstance(data, dict) else ""
    if not tagline:
        raise ValueError("AI 未返回有效的一句话简介，请重试或手填")
    return {"tagline": tagline[:500]}


_AI_CRED_KEYS = ("provider", "base_url", "api_key", "model")


def _ai_section_cfg(section: str) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services import app_settings as app_settings_service

    with SessionLocal() as db:
        read = app_settings_service.read_all(db)
    blob = getattr(read, section, None)
    if blob is None:
        return {}
    return blob.model_dump() if hasattr(blob, "model_dump") else dict(blob)


def fill_saved_ai_cfg(cfg: Dict[str, Any], *, section: str = "ingest_ai") -> Dict[str, Any]:
    """请求里空着的凭据用已保存设置补上（GET 脱敏后前端常带空 api_key）。"""
    saved = _ai_section_cfg(section)
    out = dict(cfg or {})
    for key in _AI_CRED_KEYS:
        cur = out.get(key)
        empty = not str(cur).strip() if cur is not None else True
        if empty and saved.get(key):
            out[key] = saved[key]
    return out


def _ingest_ai_cfg() -> Dict[str, Any]:
    """读取「数据入库AI」配置（自开短会话，便于在任何服务里调用）。"""
    return _ai_section_cfg("ingest_ai")


def translate_to_simplified(text: str, timeout: int = 90) -> Optional[str]:
    """把介绍文本翻译为简体中文。

    使用「数据入库AI」配置；未启用 / 调用失败一律返回 None，调用方保留原文，
    不让翻译失败阻断数据获取。
    """
    t = (text or "").strip()
    if not t:
        return None
    try:
        cfg = _ingest_ai_cfg()
    except Exception:  # noqa: BLE001 — 配置读取失败视同未配置
        return None
    if not cfg.get("enabled") or not cfg.get("base_url") or not cfg.get("model"):
        return None
    prompt = (
        "把下面的介绍翻译成自然流畅的简体中文。"
        "艺人名、组合名、歌曲名等专有名词可保留原文或在括号内附原文；"
        "如果原文已经是简体中文就原样输出。只输出译文，不要任何解释或引号。\n\n" + t
    )
    try:
        out = chat_completion(cfg, [{"role": "user", "content": prompt}], timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    out = (out or "").strip().strip('"“”‘’')
    return out or None


SOCIAL_SUGGEST_PROMPT = """你是 K-pop 资料助手。任务：根据给定艺人/组合资料，提议其官方社交媒体主页链接。
硬性规则：
1. 只返回你有把握的官方账号链接；不确定就不要写。
2. 禁止编造生日、出道日、成员等任何非链接信息。
3. 只输出 JSON：{"links":{"instagram":"https://...","x":"https://...","weibo":"https://...","youtube":"https://...","tiktok":"https://...","bilibili":"https://...","facebook":"https://...","website":"https://..."},"basis":"一句话说明依据"}
4. links 里只保留有 URL 的平台；键名用小写英文平台名；twitter 请统一写成 x。
5. URL 必须是 https 开头的完整链接。"""


def _normalize_social_links(raw: Any) -> dict[str, str]:
    """规整 AI 返回的 links 为 {platform: url}。"""
    if not isinstance(raw, dict):
        return {}
    alias = {
        "twitter": "x",
        "twitter/x": "x",
        "yt": "youtube",
        "youTube": "youtube",
        "ig": "instagram",
        "ins": "instagram",
        "抖音": "tiktok",
        "微博": "weibo",
        "官网": "website",
        "official": "website",
    }
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        key = alias.get(key, alias.get(key.lower(), key.lower()))
        if isinstance(v, dict):
            url = str(v.get("url") or v.get("href") or "").strip()
        else:
            url = str(v or "").strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        out[key] = url
    return out


def suggest_social_links(
    cfg: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """调用 AI 提议社交媒体链接（只读，不写库）。"""
    if not cfg.get("base_url") or not cfg.get("model"):
        raise ValueError("请先在「入库 AI 设置」中填写 API Base URL 与模型")

    user_prompt = (
        "以下是该艺人/组合的已知资料（仅供识别正确对象，不要据此编造非链接字段）：\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "请只返回社交媒体 links JSON。"
    )
    messages = [
        {"role": "system", "content": SOCIAL_SUGGEST_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    logger.info("[suggest-social] 提示词:\n%s", user_prompt[:3000])

    provider = cfg.get("provider", "openai-compatible")
    try:
        if provider == "anthropic":
            content = _anthropic(cfg, messages)
        else:
            content = _openai_compatible(cfg, messages)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"AI 请求失败（{e.code}）: {body[:300]}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"AI 请求失败: {e.reason}") from e

    data = _parse_json(content)
    logger.info("[suggest-social] AI 原始返回: %s", json.dumps(data, ensure_ascii=False)[:1000])
    if not isinstance(data, dict):
        raise ValueError("AI 未返回有效的社交媒体 JSON，请重试")
    links = _normalize_social_links(data.get("links") or data)
    basis = str(data.get("basis") or "").strip()
    sources: List[Dict[str, Any]] = []
    if basis:
        sources.append({"field": "social_media", "value": basis, "source": "AI"})
    return {"links": links, "basis": basis, "sources": sources}

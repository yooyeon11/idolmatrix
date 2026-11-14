"""通用 LLM 调用管道（从旧 ai_service.py 抽取，P0 拆除旧 AI 后的最小保留件）。

只含与具体业务无关的底层能力：
- OpenAI 兼容 / Anthropic 聊天调用（stdlib urllib，无额外依赖）
- 健壮的 JSON 解析与自动修复
- 简体中文翻译辅助（未配置时静默返回 None，不阻断业务）
- VIDEO_TYPES 枚举与非标准叫法归一

ai2 子系统落地后，本模块即为其 llm 客户端的地基。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.llm")

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


_FAMILY_HINTS = ("fancam", "直拍", "focuscam")


_MIX_HINTS = ("混剪",)


_VIDEO_TYPE_FOLD: Dict[str, str] = {
    re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", t.lower()): t for t in VIDEO_TYPES
}

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

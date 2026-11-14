"""任务模板：单文件入库建议（ingest_suggest）——待整理详情页的「ai2 建议」按钮。

与旧 AI 填写的区别：draft→verify 双步 + 枚举归一确定性清洗 + 只填表单不碰库。
服务批次漏网的单个文件；主流程仍是 imflow 批次制。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.ai2 import knowledge, llm
from app.ai2.registry import ToolContext, run as run_tool
from app.ai2.tasks.registry import task
from app.services.llm_core import VIDEO_TYPES, _canonical_video_type

_CAMERA_VOCAB = (
    "FanCam", "FaceCam", "FullCam", "BroadcastCam", "Tower Cam",
    "K-Choreo", "OneTake", "MultiCam", "SkyCam", "其他",
)

_PROMPT = """你是 K-pop 媒体库的入库元数据建议助手。根据给定的视频信息（yt-dlp info.json 精简字段 + 文件名）生成入库建议。

字段与硬规则：
- name: 主标题，严格遵循「YYMMDD Artist - Song [Event Name Type]」：
  1) YYMMDD 优先取表演日期（performance_date），无则取上传日期（upload_date）。
  2) 团体表演填团体英文名；个人直拍只填成员艺名，禁止「团体-成员」拼合。
  3) 歌曲名用官方英文曲名，禁止机器直译韩文；多曲用 & 连接。
  4) 方括号写到「活动名 + 类型词」为止；禁止写 4K/60p/1080p 等画质帧率标签。
  5) 时长 < 60 秒必须含 Shorts。
- video_types: 数组，每个值必须逐字等于以下 11 个枚举之一：
  OfficialMV、PerformanceVideo、Fancam、SpecialStage、CollabStage、CoverStage、MixEdit、Teaser、SpecialVideo、ShortVideo、Other
- camera_angle: 必须逐字等于以下之一，判断不了填 null：
  FanCam、FaceCam、FullCam、BroadcastCam、Tower Cam、K-Choreo、OneTake、MultiCam、SkyCam、其他
- event_name: 舞台/活动名（如 M COUNTDOWN、音乐银行），无则 null。
- performance_date: YYYY-MM-DD，不确定 null。
- chinese_description: 用中文 2-4 句描述这是什么演出、表演者与曲目。
- 缺少 info.json 字段时从文件名解析；解析不出的一律 null 并在 notice 说明，严禁编造。

只输出一个 JSON 对象：
{"name": "...", "chinese_name": "...|null", "event_name": "...|null",
 "performance_date": "YYYY-MM-DD|null", "camera_angle": "...|null",
 "video_types": ["枚举"], "is_solo": true|false|null,
 "chinese_description": "...", "notice": "...|null",
 "evidence": {"字段": "依据（引用源标题/简介原文）"}}
{rules}"""


def _clean_suggestion(raw: Dict[str, Any]) -> Dict[str, Any]:
    """确定性清洗：枚举归一、词表外机位置空、画质标签剥除。"""
    from app.ai2.tools.consistency import _strip_quality_from_name  # 局部导入避免环

    out: Dict[str, Any] = {}
    name = str(raw.get("name") or "").strip()
    if name:
        out["name"] = _strip_quality_from_name(name)
    for k in ("chinese_name", "event_name", "chinese_description", "notice"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    pd = str(raw.get("performance_date") or "").strip()
    if len(pd) == 10:
        out["performance_date"] = pd
    cam = raw.get("camera_angle")
    if isinstance(cam, str) and cam.strip() in _CAMERA_VOCAB:
        out["camera_angle"] = cam.strip()
    vt = raw.get("video_types")
    if isinstance(vt, list):
        clean_vt = []
        for x in vt:
            c = _canonical_video_type(x)
            if c and c not in clean_vt:
                clean_vt.append(c)
        if clean_vt:
            out["video_types"] = clean_vt
    if isinstance(raw.get("is_solo"), bool):
        out["is_solo"] = raw["is_solo"]
    return out


@task("ingest_suggest", "单文件入库建议")
def ingest_suggest(ctx: ToolContext) -> Dict[str, Any]:
    info = ctx.params.get("info") or {}
    if not isinstance(info, dict) or not (info.get("title") or info.get("file_name")):
        raise ValueError("缺少参数 info（需含 title 或 file_name）")

    steps: List[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {}

    # 1) 知识层：记忆 + 已合入规则（自我升级闭环的读侧）
    mem = run_tool("knowledge.read_memory", ctx, {})
    rules_text = knowledge.read_rules()
    steps.append({"step": "knowledge", "ok": True, "detail": f"记忆 {mem['chars']} 字" + (f"；规则 {len(rules_text)} 字" if rules_text else "")})

    # 2) LLM 草稿
    if not llm.enabled(ctx.db):
        raise llm.Ai2NotConfigured("ingest_suggest 需要 LLM（draft 步）：请在设置页配置 ai2")
    prompt = _PROMPT.replace("{rules}", f"\n\n以下是本库已确认的补充规则（优先级高于以上默认）：\n{rules_text}" if rules_text else "")
    # info 序列化时剔除超大字段
    slim = {k: v for k, v in info.items() if k not in ("thumbnail", "formats", "description") or k == "description"}
    if isinstance(slim.get("description"), str) and len(slim["description"]) > 2000:
        slim["description"] = slim["description"][:2000] + "…"
    raw = llm.call_json("draft", [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"视频信息：\n{slim}"},
    ], ctx.db)
    suggestion = _clean_suggestion(raw)
    if not suggestion.get("name"):
        raise ValueError("建议缺 name（LLM 未给出可用标题，禁止编造——任务失败是正确行为）")
    steps.append({"step": "draft", "ok": True, "detail": f"建议 {len(suggestion)} 字段"})

    # 3) 独立核验（只给源数据 + 建议）
    verify_out = llm.verify_call(
        "verify",
        instruction="核对下面入库建议的每个非 null 字段是否都能从视频信息直接确认；无法确认的列入 issues。",
        raw_data={"info": slim, "suggestion": suggestion},
        db=ctx.db,
    )
    verify_issues = [str(x) for x in (verify_out.get("issues") or []) if str(x).strip()]
    steps.append({"step": "verify", "ok": not verify_issues, "detail": f"独立核验 {'通过' if not verify_issues else f'{len(verify_issues)} 项存疑'}"})
    evidence["verify_issues"] = verify_issues

    # 4) 日志（建议类任务不落报告文件，日志留痕即可）
    title = suggestion.get("name", "")
    knowledge.append_log("ingest_suggest", f"建议标题「{title}」；核验{'通过' if not verify_issues else '存疑'}")

    return {
        "steps": steps,
        "suggestion": suggestion,
        "verify_issues": verify_issues,
        "evidence": evidence,
    }

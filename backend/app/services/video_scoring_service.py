"""AI 视频评分服务（实验功能）。

抽 N 帧 → 多模态消息送配置的视觉模型 → 解析各维度评分 → 落 video_scores 表。
维度与评分锚点写死在 prompt（清晰度/曝光色彩/人物遮挡/机位角度/构图），
按 video_type 感知 rubric（直拍重遮挡/稳定性，舞台重机位）。
"""


from __future__ import annotations

import base64
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.media.ffmpeg import FFmpegError, extract_thumbnail, probe
from app.media.ffmpeg import ProbeError
from app.models.music_video import MusicVideo
from app.models.video_score import VideoScore
from app.services.ai_service import chat_completion, parse_json
from app.services.library_service import resolve_video_abs_path

logger = logging.getLogger(__name__)

# 送评帧宽：768px 宽对"看得清糊不糊/挡没挡"足够（帧数按时长/档位决定）
FRAME_WIDTH = 768
# 自适应帧数公式：时长每 20 秒 +1 帧、基准 4，收在 6~16。
# 短视频(~30s)≈6 帧，3 分钟≈13 帧，5 分钟以上顶到 16 —— 成本花在需要覆盖的地方。
FRAME_AUTO_MIN = 6
FRAME_AUTO_MAX = 16

# 固定档位（设置里可选）
FRAME_FIXED_MODES = {"8": 8, "12": 12, "16": 16}


def compute_frame_count(duration: float, mode: str = "auto") -> int:
    """按抽帧策略算送评帧数。

    mode: "auto"=按时长自适应；"8"/"12"/"16"=固定档位。未知值按 auto。
    """
    if mode in FRAME_FIXED_MODES:
        return FRAME_FIXED_MODES[mode]
    return max(FRAME_AUTO_MIN, min(FRAME_AUTO_MAX, round(duration / 20) + 4))

# 维度 key → 中文名（前端展示与 prompt 输出键一致）
SCORE_DIMS: Dict[str, str] = {
    "sharpness": "清晰度",
    "exposure": "曝光与色彩",
    "occlusion": "人物遮挡",
    "angle": "机位与角度",
    "framing": "构图",
}


def _type_aware_rubric(video_type: str) -> str:
    """按视频类型追加维度侧重说明（rubric 的类型感知层）。"""
    vt = (video_type or "").lower()
    if vt == "fancam":
        return (
            "- 本片是粉丝直拍：遮挡（其他成员/观众/话筒杆挡住主体）与机位角度（跟拍是否到位、"
            "远近是否合适）是这类视频最核心的质量信号，请从严评分。"
        )
    if vt in ("performancevideo", "specialstage", "collabstage"):
        return (
            "- 本片是舞台表演：机位角度侧重「镜头切换与景别分布」（全身/半身/特写是否兼顾），"
            "遮挡侧重字幕/舞台装置对表演者的遮挡。"
        )
    if vt == "officialmv":
        return (
            "- 本片是官方 MV：专业制作，遮挡通常不适用（按无遮挡处理即可），"
            "重点看清晰度与色彩调性。"
        )
    return ""


def _build_prompt(video_type: str, video_name: str) -> str:
    dims_block = "\n".join(
        f'- "{key}"（{name}）: {{"score": 1-10 整数, "reason": "一句话中文理由"}}'
        for key, name in SCORE_DIMS.items()
    )
    return f"""你是 K-pop 音乐视频的质量评审。根据给出的视频抽帧（按时间均匀抽取，帧按顺序代表时间线），
对视频《{video_name}》评分。{f"视频类型: {video_type}。" if video_type else ""}

各维度 1-10 整数分，评分锚点（对齐后再打分，严禁无锚点的好人分）：
- 清晰度：9=发丝与布料纹理可见；7=面部轮廓清晰但细节平淡；5=能认人但发糊；3=糊到看不清表情。
- 曝光与色彩：10=曝光准确色彩自然；7=整体可看但有偏色/轻微过曝；4=大面块过曝或欠曝影响观感。
- 人物遮挡：10=主体全程无遮挡；7=偶有前景穿过；4=关键动作被明显遮挡；档位感优先于精确数字。
- 机位与角度：9=视角与距离对这类视频是理想机位；7=可接受但普通；5=机位尴尬（如大逆光远景）。
- 构图：9=构图讲究主体突出；7=正常水平；5=主体出框/裁头裁脚。

{_type_aware_rubric(video_type)}

严格要求：
- score 必须是整数；reason 一句话中文，指出具体画面依据（如「3-4 帧明显欠曝」）。
- 全部维度必须有值，禁止省略。

只返回一个 JSON 对象，不要输出任何其他文字或代码块标记：
{{
  "scores": {{
{dims_block}
  }},
  "comment": "两句话以内的整体点评（中文）"
}}"""


def _extract_frames(video: Path, count: int) -> List[Path]:
    """均匀抽 count 帧（768px 宽 JPG），返回成功抽取的临时文件列表。"""
    duration = None
    try:
        duration = probe(str(video)).duration
    except ProbeError as e:
        raise FFmpegError(f"读取视频信息失败: {e}") from e
    if not duration or duration <= 2:
        raise FFmpegError("视频时长过短，无法抽帧评分")

    # 均匀取位：避开开头 2%（片头黑场/台标）与结尾 2%
    span = duration * 0.96
    times = [duration * 0.02 + span * (i + 0.5) / count for i in range(count)]

    out_dir = Path(tempfile.mkdtemp(prefix="ai-score-"))
    frames: List[Path] = []
    for i, t in enumerate(times):
        out = out_dir / f"frame-{i:02d}.jpg"
        try:
            extract_thumbnail(video, out, t, width=FRAME_WIDTH)
        except FFmpegError:
            continue
        if out.is_file() and out.stat().st_size > 0:
            frames.append(out)
    if not frames:
        raise FFmpegError("抽帧全部失败，无法评分")
    return frames


def _frame_data_url(frame: Path) -> str:
    b64 = base64.b64encode(frame.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _build_messages(prompt: str, frames: List[Path]) -> List[dict]:
    content: List[dict] = [{"type": "text", "text": prompt}]
    for f in frames:
        content.append({"type": "image_url", "image_url": {"url": _frame_data_url(f)}})
    return [{"role": "user", "content": content}]


def _cleanup_frames(frames: List[Path]) -> None:
    for f in frames:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            f.parent.rmdir()
        except OSError:
            pass


def score_video(
    db: Session,
    mv: MusicVideo,
    creds: Dict[str, Any],
    *,
    model: Optional[str] = None,
    frames_mode: str = "auto",
) -> VideoScore:
    """对单个视频执行 AI 评分并落库，返回评分行。

    creds：{provider, base_url, api_key?, model} —— 由前端把用户配置的评分 AI
    凭据随请求带来（与入库 AI 同模式）。model 参数优先于 creds 里的 model。
    """
    video = resolve_video_abs_path(mv)
    if video is None or not video.is_file():
        raise ValueError("视频文件不存在，无法评分")

    used_model = (model or creds.get("model") or "").strip()
    if not creds.get("base_url") or not used_model:
        raise ValueError("请先在「设置 · AI 设置 · 视频评分」中填写 Base URL 与模型")

    # 先探测时长算帧数（自适应档），再抽帧
    try:
        duration = probe(str(video)).duration or 0
    except ProbeError:
        duration = 0
    frame_count = compute_frame_count(duration or 60, frames_mode)

    frames: List[Path] = []
    try:
        frames = _extract_frames(video, frame_count)
        prompt = _build_prompt(mv.video_type or "", mv.name)
        messages = _build_messages(prompt, frames)
        cfg = {
            **creds,
            "model": used_model,
        }
        text = chat_completion(cfg, messages, timeout=300)
        data = parse_json(text)
    finally:
        _cleanup_frames(frames)

    scores = data.get("scores") or {}
    dims: Dict[str, Any] = {}
    for key, label in SCORE_DIMS.items():
        entry = scores.get(key)
        if isinstance(entry, dict):
            try:
                score = int(entry.get("score"))
            except (TypeError, ValueError):
                score = 0
            dims[key] = {"label": label, "score": max(0, min(10, score)), "reason": str(entry.get("reason") or "")}
        elif isinstance(entry, (int, float)):
            dims[key] = {"label": label, "score": max(0, min(10, int(entry))), "reason": ""}

    if not dims:
        raise ValueError("AI 未返回可解析的评分，请检查模型是否支持图片输入")

    row = VideoScore(
        music_video_id=mv.id,
        model=used_model,
        dims=dims,
        comment=str(data.get("comment") or ""),
        frames=len(frames),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("AI 评分完成: mv=%s model=%s 帧数=%d", mv.id, used_model, len(frames))
    return row


def store_external_score(
    db: Session,
    mv: MusicVideo,
    *,
    model: str,
    dims_raw: Dict[str, Any],
    comment: str = "",
    frames: int = 0,
    note: str = "",
) -> VideoScore:
    """落一条外部系统(workbuddy 等)提交的评分结果。

    dims 兼容两种形态：{"key": 整数} 或 {"key": {"score": 整数, "reason": "..."}}；
    分数截到 0-10。已知维度 key 自动带中文名，未知 key 原样作 label。
    """
    if not isinstance(dims_raw, dict) or not dims_raw:
        raise ValueError("dims 必须是非空对象")
    if not (model or "").strip():
        raise ValueError("model 必填")
    dims: Dict[str, Any] = {}
    for key, entry in dims_raw.items():
        label = SCORE_DIMS.get(key, key)
        try:
            if isinstance(entry, dict):
                score = int(entry.get("score"))
                reason = str(entry.get("reason") or "")
            else:
                score = int(entry)
                reason = ""
        except (TypeError, ValueError):
            raise ValueError(f"维度 {key} 的分数必须是整数")
        dims[key] = {"label": label, "score": max(0, min(10, score)), "reason": reason}
    row = VideoScore(
        music_video_id=mv.id,
        model=model.strip()[:200],
        dims=dims,
        comment=str(comment or ""),
        frames=max(0, int(frames or 0)),
        note=(str(note).strip()[:500] or None) if note else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("外部评分写入: mv=%s model=%s dims=%d", mv.id, row.model, len(dims))
    return row


def latest_score(db: Session, mv_id: int) -> Optional[VideoScore]:
    """取某视频最近一次评分（无则 None）。"""
    return db.scalar(
        select(VideoScore)
        .where(VideoScore.music_video_id == mv_id)
        .order_by(VideoScore.created_at.desc(), VideoScore.id.desc())
        .limit(1)
    )

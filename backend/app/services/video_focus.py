"""封面焦点：按需检测人脸位置，供前端 object-position 居中裁切。

只在视频被选入首页轮播池/主打位时才检测（懒加载），结果落库长期复用，
不做全库批处理。检测器优先用 OpenCV YuNet（模型自动下载，缓存于
derived/models/），不可用时退回 OpenCV 自带的 Haar 级联；OpenCV 整体
缺失则不检测——前端回退到居中显示，功能不受影响。
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.music_video import MusicVideo
from app.services.video_thumb import ensure_video_thumbnail

logger = logging.getLogger(__name__)

# YuNet 模型（opencv_zoo 官方，约 5MB）与下载地址
_YUNET_FILE = "face_detection_yunet_2023mar.onnx"
_YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    + _YUNET_FILE
)
_YUNET_SCORE_THRESHOLD = 0.6
# 检测输入宽度上限：等比缩小后再检测，焦点是相对坐标不受影响
_DETECT_MAX_WIDTH = 960

_cv2 = None
_cv2_checked = False
_yunet_model: object | None = None
_yunet_failed = False


def _load_cv2():
    """惰性导入 cv2；未安装时返回 None（不报错，调用方回退）。"""
    global _cv2, _cv2_checked
    if not _cv2_checked:
        _cv2_checked = True
        try:
            import cv2  # noqa: F401  延迟导入：未装 opencv 时其余功能不受影响

            _cv2 = cv2
        except Exception as e:  # noqa: BLE001
            logger.info("OpenCV 不可用，封面焦点检测回退居中：%s", e)
    return _cv2


def _yunet_path() -> Path:
    return settings.derived_dir / "models" / _YUNET_FILE


def _get_yunet(width: int, height: int):
    """获取 YuNet 检测器；模型缺失时尝试下载一次，失败则返回 None。"""
    global _yunet_model, _yunet_failed
    cv2 = _load_cv2()
    if cv2 is None or _yunet_failed:
        return None
    if _yunet_model is None:
        path = _yunet_path()
        try:
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".onnx.part")
                logger.info("下载 YuNet 人脸检测模型：%s", _YUNET_URL)
                with urllib.request.urlopen(_YUNET_URL, timeout=60) as resp, open(tmp, "wb") as f:
                    f.write(resp.read())
                if tmp.stat().st_size <= 0:
                    raise OSError("模型下载为空")
                tmp.replace(path)
            _yunet_model = cv2.FaceDetectorYN.create(
                str(path), "", (width, height),
                score_threshold=_YUNET_SCORE_THRESHOLD,
            )
        except Exception as e:  # noqa: BLE001
            _yunet_failed = True
            logger.warning("YuNet 模型不可用，回退 Haar 级联：%s", e)
            return None
    else:
        # 输入尺寸随图片变化，需同步更新
        _yunet_model.setInputSize((width, height))
    return _yunet_model


def _detect_faces(img) -> List[Tuple[float, float, float, float]]:
    """返回 [(x, y, w, h)]；YuNet 优先，Haar 兜底。"""
    cv2 = _load_cv2()
    if cv2 is None:
        return []
    h, w = img.shape[:2]

    detector = _get_yunet(w, h)
    if detector is not None:
        try:
            _, faces = detector.detect(img)
            if faces is not None and len(faces):
                return [(float(f[0]), float(f[1]), float(f[2]), float(f[3])) for f in faces]
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("YuNet 检测失败，回退 Haar：%s", e)

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    rects = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    return [(float(x), float(y), float(w_), float(h_)) for (x, y, w_, h_) in rects]


def detect_image_focus(path: Path) -> Optional[Tuple[float, float]]:
    """对单张图片做人脸检测，返回 0~1 归一化的人脸质心；无脸/失败返回 None。"""
    cv2 = _load_cv2()
    if cv2 is None:
        return None
    try:
        img = cv2.imread(str(path))
    except Exception as e:  # noqa: BLE001
        logger.warning("读取封面失败 %s：%s", path, e)
        return None
    if img is None or not img.size:
        return None

    h, w = img.shape[:2]
    if w > _DETECT_MAX_WIDTH:
        scale = _DETECT_MAX_WIDTH / w
        img = cv2.resize(img, (_DETECT_MAX_WIDTH, max(1, round(h * scale))))

    faces = _detect_faces(img)
    if not faces:
        return None
    # 多脸取质心（编舞常对称站位，质心比最大脸更稳）
    cx = sum(x + w_ / 2 for (x, y, w_, h_) in faces) / len(faces)
    cy = sum(y + h_ / 2 for (x, y, w_, h_) in faces) / len(faces)
    ih, iw = img.shape[:2]
    return (min(max(cx / iw, 0.0), 1.0), min(max(cy / ih, 0.0), 1.0))


def ensure_focus(db: Session, mv: MusicVideo) -> Optional[Tuple[float, float]]:
    """确保该视频已有封面焦点；无则检测一次并落库。检测不到返回 None。"""
    if mv.focus_x is not None and mv.focus_y is not None:
        return (mv.focus_x, mv.focus_y)
    try:
        thumb = ensure_video_thumbnail(db, mv, commit=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("生成封面失败 mv=%s：%s", mv.id, e)
        return None
    if thumb is None or not thumb.is_file():
        return None
    focus = detect_image_focus(thumb)
    if focus is None:
        return None
    mv.focus_x, mv.focus_y = focus
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return focus


def ensure_focus_for_video_ids(db: Session, ids: List[int]) -> None:
    """批量入口：逐条确保焦点，单条失败只记日志。"""
    for vid in ids:
        try:
            mv = db.get(MusicVideo, vid)
        except Exception as e:  # noqa: BLE001
            logger.warning("焦点检测取视频失败 id=%s：%s", vid, e)
            continue
        if mv is None:
            continue
        try:
            ensure_focus(db, mv)
        except Exception as e:  # noqa: BLE001
            logger.warning("焦点检测失败 mv=%s：%s", vid, e)


# ===== 艺人/组合：主舞台素材焦点 =====
# 素材优先级：横幅 > 头像（横幅多为横版全身/团体照，做主舞台底图更合适）


def _entity_stage_image(obj) -> Optional[Path]:
    """返回该艺人/组合的主舞台素材文件；无横幅回退头像，都没有返回 None。"""
    from app.services import audiodb_service

    for attr in ("banner_path", "avatar_path"):
        rel = getattr(obj, attr, None)
        if not rel:
            continue
        p = audiodb_service.resolve_avatar_path(rel)
        if p is not None and p.is_file():
            return p
    return None


def ensure_entity_focus(db: Session, obj) -> Optional[Tuple[float, float]]:
    """确保艺人/组合已有主舞台素材焦点；无则检测一次并落库。"""
    if obj is None or getattr(obj, "deleted_at", None) is not None:
        return None
    if obj.focus_x is not None and obj.focus_y is not None:
        return (obj.focus_x, obj.focus_y)
    img = _entity_stage_image(obj)
    if img is None:
        return None
    focus = detect_image_focus(img)
    if focus is None:
        return None
    obj.focus_x, obj.focus_y = focus
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return focus

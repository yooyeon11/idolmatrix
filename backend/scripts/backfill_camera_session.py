"""存量回填：机位（camera_angle）与场次（session_label）。

用法（在 `backend/` 下）：
    python scripts/backfill_camera_session.py                 # dry-run，只出报告
    python scripts/backfill_camera_session.py --apply         # 真正写库
    python scripts/backfill_camera_session.py --apply --only-empty-camera   # 只补空机位

口径（业主 2026-10-17）：
- 机位：只从 **name 尾标**按权威词表匹配（`app/core/vocab.py`）；匹配不到就留空，
  不猜、不近似。词表外但「像机位」的尾标单独列出来，供决定是否扩充词表。
- 场次：`YYMMDD - 活动名`，日期取 performance_date（缺失回退 published_date），
  活动名取 event_name（缺失回退 original_uploader 频道名）。两者都缺则留空。
- 场次不参与落位目录（决策 3），本脚本**不动任何文件路径**。

默认**不覆盖已有值**：只补空字段。加 `--overwrite` 才按规则重算覆盖。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.vocab import build_session_label  # noqa: E402
from app.models.music_video import MusicVideo  # noqa: E402
from app.services.video_meta import (  # noqa: E402
    resolve_camera_angle,
    title_camera_tail,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="回填机位 / 场次")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有值（默认只补空）")
    ap.add_argument("--only-empty-camera", action="store_true", help="只回填机位")
    ap.add_argument("--report", default="", help="把明细报告写到该 JSON 路径")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(MusicVideo).where(MusicVideo.deleted_at.is_(None))
            ).all()
        )
        total = len(rows)
        cam_filled = cam_skipped = cam_unknown = 0
        sess_filled = sess_skipped = sess_empty = 0
        unknown_tails: Counter[str] = Counter()
        cam_counter: Counter[str] = Counter()
        sess_samples: list[dict] = []
        unknown_samples: list[dict] = []

        for mv in rows:
            changed = False

            # ---- 机位 ----
            if args.overwrite or not (mv.camera_angle or "").strip():
                camera, unknown = resolve_camera_angle(mv.name, None)
                if camera:
                    cam_counter[camera] += 1
                    if (mv.camera_angle or "") != camera:
                        mv.camera_angle = camera
                        changed = True
                    cam_filled += 1
                elif unknown:
                    cam_unknown += 1
                    unknown_tails[unknown] += 1
                    if len(unknown_samples) < 80:
                        unknown_samples.append({"id": mv.id, "name": mv.name, "tail": unknown})
                else:
                    cam_skipped += 1
            else:
                cam_skipped += 1

            # ---- 场次 ----
            if not args.only_empty_camera:
                if args.overwrite or not (mv.session_label or "").strip():
                    label = build_session_label(
                        mv.performance_date,
                        mv.published_date,
                        mv.event_name,
                        mv.original_uploader,
                    )
                    if label:
                        if (mv.session_label or "") != label:
                            mv.session_label = label
                            changed = True
                        sess_filled += 1
                        if len(sess_samples) < 10:
                            sess_samples.append({"id": mv.id, "label": label})
                    else:
                        sess_empty += 1
                else:
                    sess_skipped += 1

            if not changed:
                pass

        report = {
            "total": total,
            "camera": {
                "filled": cam_filled,
                "skipped_no_camera_word": cam_skipped,
                "unknown_vocab": cam_unknown,
                "by_value": dict(cam_counter.most_common()),
                "unknown_tails": dict(unknown_tails.most_common()),
                "unknown_samples": unknown_samples,
            },
            "session": {
                "filled": sess_filled,
                "skipped_has_value": sess_skipped,
                "empty_no_date_or_name": sess_empty,
                "samples": sess_samples,
            },
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))

        if args.report:
            Path(args.report).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n报告已写入 {args.report}")

        if args.apply:
            db.commit()
            print(f"\n已写库：{total} 条扫描，机位补 {cam_filled}，场次补 {sess_filled}")
        else:
            db.rollback()
            print("\n[dry-run] 未写库；确认无误后加 --apply")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

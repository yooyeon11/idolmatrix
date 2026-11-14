"""全库一致性核对 CLI —— 一条命令出清单，**stdout 只打几行汇总**。

用法（在 `backend/` 下）：
    python scripts/audit_library.py                          # 全库 dry-run，清单写到 out/audit/
    python scripts/audit_library.py --scope path:A2O\\ LITE   # 只核对某个目录（file_path 前缀）
    python scripts/audit_library.py --scope group:83          # 只核对某个团体
    python scripts/audit_library.py --scope id:3200-3300      # 按 id 区间
    python scripts/audit_library.py --apply                   # 写库：只写 ✅「可直接改」的机位/场次
    python scripts/audit_library.py --json out.json --md out.md

设计（业主 2026-10-18：核对要内置、别每次烧 token）：
- 规则逻辑全在 `app/services/library_audit.py`，本脚本只负责取数 / 落盘 / 写库。
- **stdout 只打汇总**（通常 5~8 行），明细一律进文件；agent 读汇总即可判断。
- `--apply` 只处理 `kind == "auto"` 且字段属于 `camera_angle` / `session_label` 的项，
  写完逐条回读断言，失败即中止并报告。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select                      # noqa: E402
from sqlalchemy.orm import selectinload            # noqa: E402

from app.core.database import SessionLocal         # noqa: E402
from app.models.music_video import MusicVideo      # noqa: E402
from app.models.song import Song                   # noqa: E402
from app.services.library_audit import (           # noqa: E402
    audit_records,
    find_by_scope,
    render_markdown,
    summarize,
)

OUT_DIR = Path(__file__).resolve().parents[1] / "out" / "audit"
APPLY_FIELDS = {"camera_angle", "session_label"}


def load_records(db):
    stmt = (
        select(MusicVideo)
        .where(MusicVideo.deleted_at.is_(None))
        .options(
            selectinload(MusicVideo.songs),
            selectinload(MusicVideo.groups),
            selectinload(MusicVideo.artists),
        )
        .order_by(MusicVideo.id)
    )
    return list(db.scalars(stmt).all())


def dirty_songs(db) -> set[int]:
    rows = db.execute(
        select(Song.id).where(
            (Song.release_artist_type.is_(None)) | (Song.release_artist_id.is_(None))
        )
    ).all()
    return {r[0] for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="全库一致性核对（机位/场次/简介/曲目/日期/同名）")
    ap.add_argument("--scope", default="all", help="all | path:前缀 | id:起-止 | group:团体id")
    ap.add_argument("--json", default="", help="汇总 JSON 输出路径（默认 out/audit/*.json）")
    ap.add_argument("--md", default="", help="清单 Markdown 输出路径（默认 out/audit/*.md）")
    ap.add_argument("--apply", action="store_true", help="写库：只写 kind=auto 的机位/场次")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条（调试用）")
    ap.add_argument("--with-optional", action="store_true", help="清单里也列旧式简介")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        all_recs = load_records(db)
        if args.limit:
            all_recs = all_recs[: args.limit]
        scope_recs = find_by_scope(all_recs, args.scope)
        findings = audit_records(
            scope_recs,
            dirty_song_ids=dirty_songs(db),
            name_counts=Counter(r.name for r in all_recs),
        )
        s = summarize(findings)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        safe = args.scope.replace(":", "_").replace("\\", "_").replace("/", "_").strip("_") or "all"
        md_path = Path(args.md) if args.md else OUT_DIR / f"audit-{safe}-{stamp}.md"
        js_path = Path(args.json) if args.json else OUT_DIR / f"audit-{safe}-{stamp}.json"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            render_markdown(findings, title=f"一致性检查清单 · scope={args.scope}",
                            include_optional=args.with_optional),
            encoding="utf-8",
        )
        js_path.write_text(json.dumps(
            {"scope": args.scope, "generated_at": stamp, "summary": s,
             "findings": [f.as_dict() for f in findings]},
            ensure_ascii=False, indent=1), encoding="utf-8")

        # ---- 写库 ----
        applied = failed = 0
        if args.apply:
            todo = [f for f in findings
                    if f.kind == "auto" and f.field_name in APPLY_FIELDS and f.new]
            by_id: dict = {}
            for f in todo:
                by_id.setdefault(f.video_id, []).append(f)
            for vid, fs in by_id.items():
                mv = db.get(MusicVideo, vid)
                if mv is None:
                    failed += len(fs)
                    continue
                for f in fs:
                    setattr(mv, f.field_name, f.new)
                db.commit()
                db.refresh(mv)
                for f in fs:
                    if (getattr(mv, f.field_name) or "") != f.new:
                        failed += 1
                        print(f"  ✗ #{vid} {f.field_name} 写入未生效", file=sys.stderr)
                    else:
                        applied += 1

        # ---- 汇总（只打这几行）----
        bk = s["by_kind"]
        print(f"scope={args.scope} ｜ 条目 {s['items']} ｜ 待处理 {s['findings']} 项"
              f"（✅ {bk['auto']} ｜ 🔶 {bk['suggest']} ｜ ✋ {bk['manual']}"
              f" ｜ ⚠️ {bk['optional']} ｜ ℹ️ {bk['info']}）")
        cod = " ".join(f"{k}:{v}" for k, v in sorted(s["by_code"].items(), key=lambda x: -x[1]))
        print(f"按规则： {cod}")
        print(f"清单 → {md_path}")
        print(f"数据 → {js_path}")
        if args.apply:
            print(f"写库： ✅ {applied} 项 ｜ ✗ {failed} 项")
        elif bk["auto"]:
            print(f"提示：{bk['auto']} 项标为「可直接改」，加 --apply 写入（只动机位/场次）")
        return 0 if failed == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

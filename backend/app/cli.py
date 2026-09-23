"""容器内维护：重置 owner 密码、清除重置指纹、重算短视频标记。

WORKDIR /app/backend：
  python -m app.cli reset-owner-password
  python -m app.cli clear-reset-fingerprint
  python -m app.cli fix-short-flags
"""

from __future__ import annotations

import argparse
import getpass
import sys


def fix_short_flags() -> int:
    """按「只看视频类型」的口径重算全库 is_short（v3.2.32 口径变更后的一次性修复）。

    历史数据里时长 < 70 秒的视频（含 MIX 混剪、预告）被标成了 is_short，
    会错误出现在短视频列表；这里把它们改回按类型判定。
    """
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.music_video import MusicVideo
    from app.services.video_meta import is_short_video

    db = SessionLocal()
    to_short = to_long = 0
    try:
        for mv in db.scalars(select(MusicVideo)).all():
            want = is_short_video(mv.video_types, mv.video_type)
            if bool(mv.is_short) == want:
                continue
            mv.is_short = want
            if want:
                to_short += 1
            else:
                to_long += 1
        db.commit()
    finally:
        db.close()
    print(f"is_short 重算完成：改为短视频 {to_short} 条，移出短视频 {to_long} 条")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="idolMatrix 维护命令")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reset = sub.add_parser("reset-owner-password", help="重置 owner 密码并踢掉全部会话")
    p_reset.add_argument("--password", default="", help="新密码；省略则交互输入")

    sub.add_parser("clear-reset-fingerprint", help="允许再次使用同一 AUTH_RESET_OWNER_PASSWORD")
    sub.add_parser(
        "fix-short-flags",
        help="按视频类型重算 is_short（清掉历史上因「时长 < 70 秒」误标为短视频的记录）",
    )

    args = parser.parse_args(argv)

    if args.cmd == "fix-short-flags":
        return fix_short_flags()

    from app.services.auth_service import (
        AuthError,
        clear_reset_fingerprint,
        reset_owner_password,
    )

    if args.cmd == "clear-reset-fingerprint":
        clear_reset_fingerprint()
        print("已清除重置指纹")
        return 0

    password = (args.password or "").strip()
    if not password:
        a = getpass.getpass("新密码: ")
        b = getpass.getpass("再输入一次: ")
        if a != b:
            print("两次输入不一致", file=sys.stderr)
            return 1
        password = a
    try:
        reset_owner_password(password)
    except AuthError as e:
        print(e.detail, file=sys.stderr)
        return 1
    print("owner 密码已重置，所有登录已失效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

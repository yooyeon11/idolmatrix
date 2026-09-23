"""从大型 JSON 对象里只取出顶层指定字段，不把其余内容建成 Python 对象。

yt-dlp 的 .info.json 经常带 comments / automatic_captions / formats，体积可达
几十上百 MB。整文件 json.loads 会把 NAS 上的 Python RSS 顶到数 GB。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

# 单个保留字段的原始 JSON 上限。标题/简介/URL 远小于此；超限则丢弃该字段。
_MAX_KEEP_VALUE_BYTES = 256 * 1024
_READ_CHUNK = 64 * 1024


class _Buf:
    def __init__(self, fh):
        self._fh = fh
        self._buf = b""
        self._i = 0
        self._eof = False

    def _fill(self) -> None:
        if self._eof:
            return
        if self._i:
            self._buf = self._buf[self._i :]
            self._i = 0
        data = self._fh.read(_READ_CHUNK)
        if not data:
            self._eof = True
            return
        self._buf += data

    def peek(self, n: int = 1) -> bytes:
        while len(self._buf) - self._i < n and not self._eof:
            self._fill()
        return self._buf[self._i : self._i + n]

    def read(self, n: int = 1) -> bytes:
        while len(self._buf) - self._i < n and not self._eof:
            self._fill()
        out = self._buf[self._i : self._i + n]
        self._i += len(out)
        return out

    def skip_ws(self) -> None:
        while True:
            b = self.peek(1)
            if not b or b not in b" \t\r\n":
                return
            self._i += 1


class _CopyBuf:
    """边读边拷贝；超过 max_bytes 后继续读但不保存。"""

    def __init__(self, inner: _Buf, max_bytes: int):
        self._inner = inner
        self._max = max_bytes
        self.parts = bytearray()
        self.overflow = False

    def peek(self, n: int = 1) -> bytes:
        return self._inner.peek(n)

    def read(self, n: int = 1) -> bytes:
        data = self._inner.read(n)
        if data and not self.overflow:
            if len(self.parts) + len(data) > self._max:
                self.overflow = True
                self.parts.clear()
            else:
                self.parts.extend(data)
        return data

    def skip_ws(self) -> None:
        while True:
            b = self.peek(1)
            if not b or b not in b" \t\r\n":
                return
            self.read(1)


def extract_json_top_level(
    path: str | Path,
    keys: Iterable[str],
    *,
    max_value_bytes: int = _MAX_KEEP_VALUE_BYTES,
) -> dict[str, Any]:
    """读取 JSON 对象，只解析 `keys` 里的顶层字段，其余值流式跳过。"""
    wanted = frozenset(keys)
    if not wanted:
        return {}
    with open(path, "rb") as fh:
        buf = _Buf(fh)
        buf.skip_ws()
        if buf.peek(3) == b"\xef\xbb\xbf":
            buf.read(3)
            buf.skip_ws()
        if buf.read(1) != b"{":
            raise ValueError("元数据不是 JSON 对象")
        out: dict[str, Any] = {}
        while True:
            buf.skip_ws()
            nxt = buf.peek(1)
            if not nxt:
                raise ValueError("元数据 JSON 未结束")
            if nxt == b"}":
                buf.read(1)
                return out
            if nxt == b",":
                buf.read(1)
                buf.skip_ws()
                if buf.peek(1) == b"}":
                    buf.read(1)
                    return out
            key = _read_string(buf)
            buf.skip_ws()
            if buf.read(1) != b":":
                raise ValueError(f"元数据 JSON 缺少冒号: {key}")
            buf.skip_ws()
            if key in wanted:
                value = _read_kept_value(buf, max_value_bytes)
                if value is not None:
                    out[key] = value
            else:
                _skip_value(buf)


def _read_string(buf) -> str:
    raw = _copy_string(buf)
    return json.loads(raw.decode("utf-8"))


def _copy_string(buf) -> bytes:
    if buf.read(1) != b'"':
        raise ValueError("元数据 JSON 期望字符串")
    parts = [b'"']
    esc = False
    while True:
        b = buf.read(1)
        if not b:
            raise ValueError("元数据 JSON 字符串未结束")
        parts.append(b)
        if esc:
            esc = False
            continue
        if b == b"\\":
            esc = True
            continue
        if b == b'"':
            return b"".join(parts)


def _skip_string(buf) -> None:
    if buf.read(1) != b'"':
        raise ValueError("元数据 JSON 期望字符串")
    esc = False
    while True:
        b = buf.read(1)
        if not b:
            raise ValueError("元数据 JSON 字符串未结束")
        if esc:
            esc = False
            continue
        if b == b"\\":
            esc = True
            continue
        if b == b'"':
            return


def _skip_literal(buf) -> None:
    first = buf.peek(1)
    if first == b"t":
        want = b"true"
    elif first == b"f":
        want = b"false"
    elif first == b"n":
        want = b"null"
    else:
        raise ValueError("元数据 JSON 字面量无效")
    got = buf.read(len(want))
    if got != want:
        raise ValueError(f"元数据 JSON 字面量无效: {got!r}")


def _skip_number(buf) -> None:
    nxt = buf.peek(1)
    if nxt == b"-":
        buf.read(1)
        nxt = buf.peek(1)
    if not nxt or not (b"0" <= nxt <= b"9"):
        raise ValueError("元数据 JSON 数字无效")
    while True:
        nxt = buf.peek(1)
        if nxt and nxt in b"0123456789.eE+-":
            buf.read(1)
            continue
        return


def _skip_object(buf) -> None:
    if buf.read(1) != b"{":
        raise ValueError("元数据 JSON 对象无效")
    while True:
        buf.skip_ws()
        nxt = buf.peek(1)
        if nxt == b"}":
            buf.read(1)
            return
        if nxt == b",":
            buf.read(1)
            buf.skip_ws()
            if buf.peek(1) == b"}":
                buf.read(1)
                return
        _skip_string(buf)
        buf.skip_ws()
        if buf.read(1) != b":":
            raise ValueError("元数据 JSON 对象缺少冒号")
        _skip_value(buf)


def _skip_array(buf) -> None:
    if buf.read(1) != b"[":
        raise ValueError("元数据 JSON 数组无效")
    while True:
        buf.skip_ws()
        nxt = buf.peek(1)
        if nxt == b"]":
            buf.read(1)
            return
        if nxt == b",":
            buf.read(1)
            buf.skip_ws()
            if buf.peek(1) == b"]":
                buf.read(1)
                return
        _skip_value(buf)


def _skip_value(buf) -> None:
    buf.skip_ws()
    nxt = buf.peek(1)
    if not nxt:
        raise ValueError("元数据 JSON 值缺失")
    if nxt == b'"':
        _skip_string(buf)
    elif nxt == b"{":
        _skip_object(buf)
    elif nxt == b"[":
        _skip_array(buf)
    elif nxt in b"tfn":
        _skip_literal(buf)
    else:
        _skip_number(buf)


def _read_kept_value(buf, max_bytes: int) -> Optional[Any]:
    copy = _CopyBuf(buf, max_bytes)
    _skip_value(copy)
    if copy.overflow or not copy.parts:
        return None
    try:
        return json.loads(bytes(copy.parts).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"解析元数据字段失败: {e}") from e

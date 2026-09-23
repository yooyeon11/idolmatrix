"""轻量 ELF 读取工具（纯 Python，无第三方依赖）。

用途：在没有 Docker / Linux 的机器上静态核对「一个 Linux 二进制能不能跑起来」——
读 DT_NEEDED（依赖哪些 .so）、DT_RPATH / DT_RUNPATH（去哪里找）、SONAME。

只解析 64 位 ELF 的 section header + .dynamic，够用且不引入 pyelftools 依赖。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

# .dynamic 里的 d_tag
DT_NULL = 0
DT_NEEDED = 1
DT_SONAME = 14
DT_RPATH = 15
DT_RUNPATH = 29

_ELF_MAGIC = b"\x7fELF"


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(4) == _ELF_MAGIC
    except OSError:
        return False


def _sections(data: bytes, order: str) -> List[dict]:
    e_shoff = int.from_bytes(data[0x28:0x30], order)
    e_shentsize = int.from_bytes(data[0x3A:0x3C], order)
    e_shnum = int.from_bytes(data[0x3C:0x3E], order)
    out = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        s = data[off : off + e_shentsize]
        if len(s) < e_shentsize:
            break
        out.append(
            {
                "name_off": int.from_bytes(s[0:4], order),
                "type": int.from_bytes(s[4:8], order),
                "off": int.from_bytes(s[24:32], order),
                "size": int.from_bytes(s[32:40], order),
                "link": int.from_bytes(s[40:44], order),
                "entsize": int.from_bytes(s[56:64], order),
            }
        )
    return out


def read_elf(path: Path) -> Optional[dict]:
    """返回 {"needed", "soname", "rpath", "runpath", "bits"}；非 ELF 返回 None。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:4] != _ELF_MAGIC:
        return None
    if data[4] != 2:  # 只支持 ELF64
        return {"needed": [], "soname": "", "rpath": [], "runpath": [], "bits": 32}
    order = "little" if data[5] == 1 else "big"
    secs = _sections(data, order)
    e_shstrndx = int.from_bytes(data[0x3E:0x40], order)

    dyn = next((s for s in secs if s["type"] == 6), None)  # SHT_DYNAMIC
    if not dyn:
        return {"needed": [], "soname": "", "rpath": [], "runpath": [], "bits": 64}
    strtab = secs[dyn["link"]]
    strs = data[strtab["off"] : strtab["off"] + strtab["size"]]

    def _str(idx: int) -> str:
        end = strs.find(b"\0", idx)
        return strs[idx : end if end >= 0 else len(strs)].decode("ascii", "replace")

    needed: List[str] = []
    rpath: List[str] = []
    runpath: List[str] = []
    soname = ""
    ent = dyn["entsize"] or 16
    for off in range(dyn["off"], dyn["off"] + dyn["size"], ent):
        tag = int.from_bytes(data[off : off + 8], order, signed=True)
        val = int.from_bytes(data[off + 8 : off + 16], order)
        if tag == DT_NULL:
            break
        if tag == DT_NEEDED:
            needed.append(_str(val))
        elif tag == DT_SONAME:
            soname = _str(val)
        elif tag == DT_RPATH:
            rpath.append(_str(val))
        elif tag == DT_RUNPATH:
            runpath.append(_str(val))
    return {"needed": needed, "soname": soname, "rpath": rpath, "runpath": runpath, "bits": 64}


def needed(path: Path) -> List[str]:
    info = read_elf(path)
    return list(info["needed"]) if info else []


def soname_links(real_names: List[str]) -> Dict[str, str]:
    """按 Dockerfile 里的规则，从真实文件名推出会生成哪些 soname 别名。

    libfoo.so.a.b.c -> libfoo.so.a / libfoo.so.a.b / libfoo.so
    返回 {别名: 真实文件名}（别名与原文件同名时舍去）。
    """
    out: Dict[str, str] = {}
    for name in real_names:
        if ".so." not in name:
            continue
        base, ver = name.split(".so.", 1)
        acc = ""
        for part in ver.split("."):
            acc = f"{acc}.{part}" if acc else part
            alias = f"{base}.so.{acc}"
            if alias != name:
                out[alias] = name
        out[f"{base}.so"] = name
    return out

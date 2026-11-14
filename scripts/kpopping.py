"""kpopping.com 抓取模块：搜索、组合成员表、图片下载（带磁盘缓存）。

页面是 Next.js，正文数据以 RSC flight 形式内嵌（JSON 被反斜杠转义）。
组合页里的 ``"members"`` 数组含每位成员的 name / koreanName / birthday / image / slug，
可直接用于与 idolMatrix 艺人匹配。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import requests

PROXY = "http://127.0.0.1:7897"
MIN_INTERVAL = 0.8
_LOCK = threading.Lock()
_LAST_REQ = [0.0]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CACHE = Path(__file__).with_name(".cache")
CACHE.mkdir(exist_ok=True)


class Kpopping:
    def __init__(self, proxy: str = PROXY) -> None:
        self.s = requests.Session()
        self.s.proxies = {"http": proxy, "https": proxy}
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

    # ---------- 底层 ----------
    def _throttle(self) -> None:
        """全局最小请求间隔，避免触发 kpopping 的 429 限流。"""
        with _LOCK:
            now = time.time()
            wait = _LAST_REQ[0] + MIN_INTERVAL - now
            if wait > 0:
                time.sleep(wait)
            _LAST_REQ[0] = time.time()

    def get(self, url: str, timeout: int = 60, retries: int = 5) -> requests.Response:
        last: Optional[Exception] = None
        for i in range(retries):
            self._throttle()
            try:
                r = self.s.get(url, timeout=(10, timeout))
                if r.status_code == 200:
                    return r
                last = RuntimeError(f"HTTP {r.status_code} {url}")
                if r.status_code in (404, 403):
                    raise last
                if r.status_code == 429:
                    time.sleep(20 + 10 * i)
                    continue
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(3 + 3 * i)
        raise last  # type: ignore[misc]

    def cached_get(self, url: str, timeout: int = 60) -> bytes:
        key = hashlib.sha1(url.encode()).hexdigest()[:20]
        fp = CACHE / key
        if fp.exists() and fp.stat().st_size > 0:
            return fp.read_bytes()
        r = self.get(url, timeout=timeout)
        fp.write_bytes(r.content)
        return r.content

    # ---------- 搜索 / 组合 ----------
    def search(self, q: str) -> dict[str, Any]:
        url = "https://kpopping.com/api/search?q=" + requests.utils.quote(q)
        r = self.get(url, timeout=45)
        return r.json()

    def group_members(self, slug: str) -> list[dict[str, Any]]:
        """抓组合页，解析内嵌 members 数组。"""
        url = f"https://kpopping.com/profiles/group/{slug}"
        raw = self.cached_get(url).decode("utf-8", "replace")
        text = raw.replace('\\"', '"')
        for m in re.finditer(r'"members"\s*:\s*\[', text):
            start = m.end() - 1
            try:
                arr, _ = json.JSONDecoder().raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(arr, list) and arr and isinstance(arr[0], dict) and "image" in arr[0]:
                return arr
        return []

    def group_meta(self, slug: str) -> dict[str, Any]:
        """抓组合页里的组合本体信息（image / gallery / fandom 等）。"""
        url = f"https://kpopping.com/profiles/group/{slug}"
        text = self.cached_get(url).decode("utf-8", "replace").replace('\\"', '"')
        out: dict[str, Any] = {}
        m = re.search(r'"group"\s*:\s*\{', text)
        if m:
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[m.end() - 1 :])
                out.update(obj)
            except json.JSONDecodeError:
                pass
        m = re.search(r'"gallery"\s*:\s*\[', text)
        if m:
            try:
                gal, _ = json.JSONDecoder().raw_decode(text[m.end() - 1 :])
                out.setdefault("gallery", gal)
            except json.JSONDecodeError:
                pass
        if not out.get("image"):
            m = re.search(r'"image"\s*:\s*"(https://[^"]*?/groups/[^"]+)"', text)
            if m:
                out["image"] = m.group(1)
        return out

    def group_title(self, slug: str) -> str:
        """组合页 <title> 里的组合名（用于验证 slug 猜得对不对）。"""
        raw = self.cached_get(f"https://kpopping.com/profiles/group/{slug}").decode("utf-8", "replace")
        m = re.search(r"<title>([^<|]+)", raw)
        return m.group(1).strip() if m else ""

    def try_group_slug(self, name: str) -> Optional[str]:
        """按名字猜 slug 并验证页面标题，命中返回 slug。"""
        cands: list[str] = []
        base = name.strip()
        cands.append(base.replace(" ", "-"))
        cands.append(base.replace(" ", ""))
        cands.append(base.replace(" ", "-").replace("'", ""))
        cands.append(base.replace(" ", "-").replace("&", "and"))
        cands.append(base.replace(" ", "-").upper())
        cands.append(re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-"))
        seen: list[str] = []
        for c in cands:
            if c and c not in seen:
                seen.append(c)
        for c in seen:
            try:
                title = self.group_title(c)
            except Exception:  # noqa: BLE001
                continue
            if title and norm(title) == norm(name):
                return c
        return None


    def idol_profile_image(self, query: str, prefer_group: str | None = None) -> Optional[dict]:
        """按艺人名搜索 idol，返回 {name, slug, image, groupName}。"""
        data = self.search(query)
        idols = data.get("idols") or []
        if not idols:
            return None
        if prefer_group:
            same = [i for i in idols if (i.get("groupName") or "").casefold() == prefer_group.casefold()]
            if same:
                return same[0]
        return idols[0]

    def idol_birthday(self, slug: str) -> Optional[str]:
        """成员页里的生日（ISO 日期），用于同名防串档校验。"""
        url = f"https://kpopping.com/profiles/idol/{slug}"
        try:
            text = self.cached_get(url).decode("utf-8", "replace").replace('\\"', '"')
        except Exception:  # noqa: BLE001
            return None
        m = re.search(r'"birthDate"\s*:\s*"(\d{4}-\d{2}-\d{2})', text)
        return m.group(1) if m else None


def norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.casefold()
    s = re.sub(r"\(.*?\)", "", s)
    return re.sub(r"[^0-9a-z\uac00-\ud7a3\u4e00-\u9fff]", "", s)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    kp = Kpopping()
    members = kp.group_members("RESCENE")
    print("members", len(members))
    for m in members:
        print(m["name"], "|", m.get("koreanName"), "|", m.get("birthday"), "|", m["image"])
    print("meta", {k: v for k, v in kp.group_meta("RESCENE").items() if k in ("image", "fandom", "debutDate")})

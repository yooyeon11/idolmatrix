"""下载/更新自托管的思源宋体 Noto Serif SC（hero 名字的汉字字形用）。

产出：
  frontend/public/fonts/noto-serif-sc/notoserifsc-<weight>-<n>.woff2   （字重 400 / 500）
  frontend/src/styles/font-noto-serif-sc.css                           （@font-face + unicode-range）

用法：
  python scripts/fetch_noto_serif_sc.py            # 下载缺失分片并重写 CSS（幂等，已存在的跳过）
  python scripts/fetch_noto_serif_sc.py --css-only # 不联网，只按本地文件重写 CSS

⚠ 字体文件必须放在 **frontend/public/**：202 个资产若进 vite 依赖图（src/assets），
  `vite build` 会卡在渲染阶段（实测 >9 分钟不返回；移到 public 后全程 ~17s）。
  CSS 里因此用绝对路径 `/fonts/noto-serif-sc/...`，vite 不会改写绝对 URL。
⚠ 代理：国内直连 fonts.googleapis.com 会失败，脚本默认走 http://127.0.0.1:7897（Clash）。
  可用环境变量 FONT_PROXY 覆盖，置空字符串则直连。
"""

import argparse
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "frontend", "public", "fonts", "noto-serif-sc")
OUT_CSS = os.path.join(ROOT, "frontend", "src", "styles", "font-noto-serif-sc.css")
URL = "https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;500&display=swap"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
PROXY = os.environ.get("FONT_PROXY", "http://127.0.0.1:7897")

HEADER = """/* 思源宋体 Noto Serif SC（SIL OFL 1.1，可商用免署名）
 * 源：Google Fonts（fonts.googleapis.com），本地自托管，不依赖外网。
 * 官方按 unicode-range 切成 {n} 片/字重 —— 浏览器只下载页面实际用到的分片：
 * 名字是拉丁/韩文时**不下载任何汉字分片**，名字是汉字时按需 1~3 片（约 10~60KB）。
 * 字体文件在 frontend/public/fonts/noto-serif-sc/，故此处用**绝对路径** /fonts/... 引用。
 * ⚠ 别挪回 src/assets/：202 个资产进 vite 依赖图会让 `vite build` 卡在渲染阶段（实测 >9 分钟）。
 * ⚠ 只用于 hero 名字的**汉字字形**（entity-detail.css 的 .hero-cover-name--han）：
 *   拉丁仍归 Playfair Display、韩文仍走系统字体，两者刻意不改。
 * 生成脚本：scripts/fetch_noto_serif_sc.py（勿手改本文件）。
 * 授权：SIL OFL 1.1，见 frontend/src/assets/fonts/LICENSE.txt 第 [3] 条。
 */

"""


def fetch_css(proxy):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    )
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with opener.open(req, timeout=60) as r:
        return r.read().decode("utf-8")


def download(url, dest, proxy):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=60) as r, open(dest, "wb") as w:
        w.write(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--css-only", action="store_true", help="不联网，按本地文件重写 CSS")
    args = ap.parse_args()

    os.makedirs(FONT_DIR, exist_ok=True)
    blocks = None
    if args.css_only:
        with open(OUT_CSS, encoding="utf-8") as f:
            blocks = re.findall(r"@font-face\s*\{(.*?)\}", f.read(), re.S)
        print("css-only: 复用现有 CSS 的", len(blocks), "个 @font-face")
    else:
        blocks = re.findall(r"@font-face\s*\{(.*?)\}", fetch_css(PROXY), re.S)
        print("拉取到", len(blocks), "个 @font-face")

    out, idx, per_weight, total, missing = [], {}, {}, 0, []
    for b in blocks:
        weight = re.search(r"font-weight:\s*(\d+)", b).group(1)
        url = re.search(r"url\((https://[^)]+\.woff2)\)", b)
        urange = re.search(r"unicode-range:\s*([^;]+);", b).group(1).strip()
        idx[weight] = idx.get(weight, 0) + 1
        name = f"notoserifsc-{weight}-{idx[weight]}.woff2"
        dest = os.path.join(FONT_DIR, name)
        if url and (not os.path.exists(dest) or os.path.getsize(dest) == 0):
            download(url.group(1), dest, PROXY)
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            missing.append(name)
            continue
        size = os.path.getsize(dest)
        total += size
        per_weight[weight] = per_weight.get(weight, 0) + size
        out.append(
            "@font-face {\n"
            "  font-family: 'Noto Serif SC';\n"
            "  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            "  font-display: swap;\n"
            f"  src: url('/fonts/noto-serif-sc/{name}') format('woff2');\n"
            f"  unicode-range: {urange};\n"
            "}\n"
        )

    if missing:
        print("缺失分片（未写进 CSS）:", missing, file=sys.stderr)

    with open(OUT_CSS, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER.format(n=idx.get("400", 0)) + "\n".join(out))

    for w in sorted(per_weight):
        print(f"  weight {w}: {idx[w]} 片, {per_weight[w] / 1024 / 1024:.2f} MB")
    print(f"  total {total / 1024 / 1024:.2f} MB / {sum(idx.values())} 片")
    print("css ->", OUT_CSS)


if __name__ == "__main__":
    main()

import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
P = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
UA = {"User-Agent": "Mozilla/5.0"}

orig = "https://cdn.kpopping.com/idols/Woni/profile.png"
cases = {
    "orig": orig,
    "next1920": "https://kpopping.com/_next/image?url=" + requests.utils.quote(orig, safe="") + "&w=1920&q=90",
    "next1080": "https://kpopping.com/_next/image?url=" + requests.utils.quote(orig, safe="") + "&w=1080&q=90",
}


def run(item):
    name, u = item
    t = time.time()
    try:
        r = requests.get(u, proxies=P, timeout=(8, 90), headers=UA)
        im = Image.open(io.BytesIO(r.content))
        return f"{name}: {r.status_code} {im.size} {im.format} {len(r.content)//1024}KB {time.time()-t:.1f}s"
    except Exception as e:
        return f"{name}: ERR {type(e).__name__} {str(e)[:100]} {time.time()-t:.1f}s"


with ThreadPoolExecutor(3) as ex:
    for line in ex.map(run, cases.items()):
        print(line)

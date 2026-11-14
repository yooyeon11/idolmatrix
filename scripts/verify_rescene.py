"""验证：读取已上传海报的尺寸与头像接口返回；并探测 kpopping 成员页能否取到生日。"""
import io
import re
import sys

from PIL import Image

from im_api import Api
from kpopping import Kpopping

sys.stdout.reconfigure(encoding="utf-8")
api = Api()
for aid in (237, 238, 239, 240, 241):
    a = api.get(f"/api/artists/{aid}")
    items = api.get(f"/api/uploads/images/artist/{aid}")["items"]
    cur = next((r for r in items if r["rel_path"] == a["avatar_path"]), None)
    dims = f"{cur['width']}x{cur['height']}" if cur else "?"
    r = api.call("GET", f"/api/artists/{aid}/avatar")
    im = Image.open(io.BytesIO(r.content))
    print(f"artist#{aid} {a['name']:<8} avatar={a['avatar_path']:<26} dims={dims} served={im.size} {len(r.content)//1024}KB")

kp = Kpopping()
text = kp.cached_get("https://kpopping.com/profiles/idol/Woni").decode("utf-8", "replace").replace('\\"', '"')
for key in ("birthday", "birthDate", "birthDay", "dateOfBirth", "born"):
    m = re.search(rf'"{key}"\s*:\s*"([^"]{{4,30}})"', text)
    if m:
        print("idol page", key, "=", m.group(1))

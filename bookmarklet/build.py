"""สร้าง install.html จาก install.template.html + capture.js  (รัน: python bookmarklet/build.py)"""
from pathlib import Path

here = Path(__file__).parent
src = (here / "capture.js").read_text(encoding="utf-8")
if "</script" in src.lower():
    raise SystemExit("capture.js ห้ามมี </script")
tpl = (here / "install.template.html").read_text(encoding="utf-8")
(here / "install.html").write_text(tpl.replace("__CAPTURE_SOURCE__", src), encoding="utf-8")
print("wrote", here / "install.html")

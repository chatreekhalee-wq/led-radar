"""ประกอบไฟล์สำหรับ GitHub Pages ลงโฟลเดอร์ site/
  python dashboard/build_site.py   (ใช้ env SB_URL, SB_ANON)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
site = root / "site"
site.mkdir(exist_ok=True)

body = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
head = ('<!doctype html>\n<html lang="th">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n')
# ย้าย <title>/<meta>/<link>/<style> ไปอยู่ใน <head> แล้วตามด้วย body
split = body.index('<header class="bar">')
html = head + body[:split] + "</head>\n<body>\n" + body[split:] + "\n</body>\n</html>\n"
(site / "index.html").write_text(html, encoding="utf-8")

subprocess.run([sys.executable, str(root / "bookmarklet" / "build.py")], check=True)
(site / "install.html").write_text((root / "bookmarklet" / "install.html").read_text(encoding="utf-8"), encoding="utf-8")

(site / "config.json").write_text(json.dumps({"url": os.environ.get("SB_URL", ""), "anon": os.environ.get("SB_ANON", "")}))
cfg = json.loads((root / "worker" / "config.json").read_text(encoding="utf-8"))
(site / "presets.json").write_text(json.dumps({k: cfg[k] for k in ("presets", "risk_weight", "round_price_pct")},
                                              ensure_ascii=False), encoding="utf-8")
print("site ready:", sorted(p.name for p in site.iterdir()))

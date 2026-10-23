"""นำเข้าไฟล์ led-capture-*.json ที่ปุ่มเก็บข้อมูลดาวน์โหลดไว้ (กรณีส่งตรงไม่ได้)

  python -m worker.import_json ~/Downloads/led-capture-*.json
"""
from __future__ import annotations

import json
import sys

from .db import DB


def main(paths: list[str]):
    db = DB()
    rows = []
    for p in paths:
        js = json.load(open(p, encoding="utf-8"))
        rows.append({
            "page_url": js["p_url"], "page_title": js.get("p_title"), "page_kind": js.get("p_kind"),
            "tables_html": js.get("p_tables_html"), "page_text": js.get("p_page_text"),
            "links": js.get("p_links") or [], "images": js.get("p_images") or [],
        })
    db.insert("raw_captures", rows)
    print(f"imported {len(rows)} captures — รัน pipeline เพื่อคิดคะแนน")


if __name__ == "__main__":
    main(sys.argv[1:])

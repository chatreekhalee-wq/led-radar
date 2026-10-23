"""รัน pipeline ทั้งเส้นกับฐานข้อมูลจำลองในหน่วยความจำ (ไม่ต้องต่อ Supabase จริง)"""
import json
from datetime import date
from pathlib import Path

from worker import pipeline

FX = Path(__file__).parent / "fixtures"
KEYS = {"assets": ("id",), "auction_rounds": ("asset_id", "round_no"), "asset_scores": ("asset_id",),
        "asset_location": ("asset_id",), "asset_user": ("asset_id",), "geo_cache": ("query",),
        "alerts_sent": ("asset_id", "kind", "ref")}


class FakeDB:
    def __init__(self):
        self.t = {k: [] for k in ["raw_captures", "price_history", *KEYS]}

    def _rt(self, rows):  # จำลอง JSON round-trip ของ PostgREST
        return json.loads(json.dumps(rows, default=str))

    def select(self, table, params=None, page=1000):
        rows = self.t[table]
        for k, v in (params or {}).items():
            if isinstance(v, str) and v.startswith("eq."):
                rows = [r for r in rows if str(r.get(k)) == v[3:]]
        return self._rt(rows)

    def upsert(self, table, rows, on_conflict):
        rows = [rows] if isinstance(rows, dict) else rows
        keys = KEYS[table]
        for row in self._rt(rows):
            hit = next((r for r in self.t[table] if all(r.get(k) == row.get(k) for k in keys)), None)
            if hit:
                hit.update(row)
            else:
                base = {"status": "active", "first_seen": "2026-10-23"} if table == "assets" else {}
                self.t[table].append({**base, **row})

    def insert(self, table, rows):
        for i, r in enumerate(self._rt(rows)):
            r.setdefault("id", len(self.t[table]) + 1)
            r.setdefault("status", "new")
            self.t[table].append(r)

    def update(self, table, match, values):
        for r in self.t[table]:
            if all(r.get(k) == v for k, v in match.items()):
                r.update(self._rt(values))


def test_pipeline_end_to_end(monkeypatch, capsys):
    db = FakeDB()
    db.insert("raw_captures", [
        {"page_url": "https://asset.led.go.th/s", "page_kind": "list",
         "tables_html": (FX / "list_page.html").read_text(encoding="utf-8"), "page_text": ""},
        {"page_url": "https://asset.led.go.th/d", "page_kind": "detail", "tables_html": "",
         "page_text": (FX / "detail_page.txt").read_text(encoding="utf-8")},
        {"page_url": "https://asset.led.go.th/x", "page_kind": "list", "tables_html": "<p>x</p>", "page_text": "ไม่มีอะไร"},
    ])
    monkeypatch.setattr(pipeline, "DB", lambda: db)

    class D(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 23)
    monkeypatch.setattr(pipeline, "date", D)
    monkeypatch.setattr("worker.alerts.date", D)

    pipeline.main(["--no-geo"])

    assert [c["status"] for c in db.t["raw_captures"]] == ["parsed", "parsed", "error"]
    assert len(db.t["assets"]) == 3
    assert len(db.t["asset_scores"]) == 3
    land = next(s for s in db.t["asset_scores"]
                if next(a for a in db.t["assets"] if a["id"] == s["asset_id"])["province"] == "ราชบุรี")
    assert land["next_round_no"] == 3 and land["next_start_price"] == 992000
    assert "ขายเฉพาะส่วน/กรรมสิทธิ์รวม" in land["risk_flags"]
    # รันซ้ำต้องไม่พังและไม่สร้างทรัพย์ซ้ำ
    pipeline.main(["--no-geo", "--reparse"])
    assert len(db.t["assets"]) == 3
    out = capsys.readouterr().out
    assert "scored 3 assets" in out

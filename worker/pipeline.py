"""LED Radar pipeline — รันโดย GitHub Actions

ขั้นตอน: parse ข้อมูลดิบ → อัปเดตนัด/สถานะ → พิกัด → ข้อมูลทำเล → คะแนน → แจ้งเตือน

  python -m worker.pipeline              # รันปกติ
  python -m worker.pipeline --reparse    # parse ข้อมูลดิบทั้งหมดใหม่ (หลังแก้ parser)
  python -m worker.pipeline --no-geo     # ข้ามขั้น geocode/Overpass
  python -m worker.pipeline --no-alerts
"""
from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

from . import alerts as AL
from . import geo
from .db import DB
from .parse_led import parse_capture
from .rounds import asset_status, fill_round_prices, pct_for_round
from .score import local_medians, score_asset

CFG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))

ASSET_COLS = ["source_url", "case_no", "lot_no", "office", "asset_type", "deed_type", "deed_no",
              "province", "amphoe", "tambon", "address_text", "area_rai", "area_ngan", "area_wah",
              "area_sqm", "appraisal_price", "appraisal_dept", "deposit", "sale_condition",
              "occupant_note", "images"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def to_rows(item: dict) -> tuple[dict, list[dict]]:
    """แปลงผล parse เป็นแถว assets + auction_rounds (ส่งเฉพาะค่าที่ไม่ว่าง เพื่อ merge กับข้อมูลเดิม)"""
    rounds = item.get("_rounds") or {}
    appraisal = item.get("appraisal_price")
    # ถ้าประกาศมีแต่ "ราคาเริ่มต้น" → ย้อนหาราคาประเมินจากนัดที่ประกาศ
    if not appraisal and item.get("listed_start_price"):
        upcoming = sorted(n for n, r in rounds.items() if not r.get("result")) or [1]
        appraisal = round(item["listed_start_price"] / pct_for_round(upcoming[0], CFG), 2)
        item["appraisal_price"] = appraisal
    fill_round_prices(appraisal, rounds, CFG)

    a = {"id": item["id"], "last_seen": now_iso(), "updated_at": now_iso()}
    for c in ASSET_COLS:
        v = item.get(c)
        if v not in (None, "", [], {}):
            a[c] = v
    a["raw"] = {k: v for k, v in item.items() if k not in ("_rounds",) and not isinstance(v, (date,))}
    rr = []
    for n, r in rounds.items():
        row = {"asset_id": item["id"], "round_no": int(n)}
        for k in ("sale_date", "result", "start_price", "sold_price"):
            if r.get(k) is not None:
                row[k] = r[k]
        rr.append(row)
    return a, rr


def step_parse(db: DB, reparse: bool) -> int:
    params = {} if reparse else {"status": "eq.new"}
    caps = db.select("raw_captures", {**params, "order": "id.asc"})
    n_items = 0
    for cap in caps:
        try:
            items = parse_capture(cap)
            for it in items:
                a, rr = to_rows(it)
                db.upsert("assets", a, "id")
                db.upsert("auction_rounds", rr, "asset_id,round_no")
            n_items += len(items)
            db.update("raw_captures", {"id": cap["id"]},
                      {"status": "parsed" if items else "error",
                       "parse_error": None if items else "ไม่พบรายการทรัพย์ในหน้านี้ — ตรวจ HEADER_MAP/DETAIL_PATTERNS",
                       "parsed_at": now_iso()})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            db.update("raw_captures", {"id": cap["id"]}, {"status": "error", "parse_error": str(e)[:500]})
    print(f"parse: {len(caps)} captures → {n_items} items")
    return n_items


def _rounds_by_asset(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        if isinstance(r.get("sale_date"), str):
            r["sale_date"] = date.fromisoformat(r["sale_date"][:10])
        out.setdefault(r["asset_id"], []).append(r)
    for v in out.values():
        v.sort(key=lambda r: r["round_no"])
    return out


def step_geocode(db: DB, assets: list[dict], users: dict) -> None:
    cache = {c["query"]: c for c in db.select("geo_cache")}
    for a in assets:
        u = users.get(a["id"]) or {}
        if u.get("manual_lat") is not None:
            if a.get("lat") != u["manual_lat"] or a.get("geo_precision") != "manual":
                a.update(lat=u["manual_lat"], lng=u["manual_lng"], geo_precision="manual")
                db.update("assets", {"id": a["id"]}, {"lat": a["lat"], "lng": a["lng"], "geo_precision": "manual"})
            continue
        if a.get("lat") is not None or a.get("status") != "active":
            continue
        for q, prec in geo.geocode_queries(a):
            hit = cache.get(q)
            if hit is None:
                try:
                    res = geo.nominatim(q)
                except Exception as e:  # noqa: BLE001
                    print("nominatim error", q, e)
                    break
                hit = {"query": q, "lat": res[0] if res else None, "lng": res[1] if res else None,
                       "precision": prec}
                db.upsert("geo_cache", hit, "query")
                cache[q] = hit
            if hit.get("lat") is not None:
                a.update(lat=hit["lat"], lng=hit["lng"], geo_precision=prec)
                db.update("assets", {"id": a["id"]}, {"lat": hit["lat"], "lng": hit["lng"], "geo_precision": prec})
                break


def step_location(db: DB, assets: list[dict]) -> dict[str, dict]:
    locs = {l["asset_id"]: l for l in db.select("asset_location")}
    # ทรัพย์ในตำบลเดียวกันที่พิกัดระดับตำบลซ้ำกัน ใช้ผลเดียวกันได้ (ลดการเรียก Overpass)
    by_point = {(round(l["lat"], 5), round(l["lng"], 5)): l for l in locs.values() if l.get("lat") is not None}
    budget = CFG["location"]["max_new_per_run"]
    deadline = time.time() + CFG["location"].get("max_minutes", 12) * 60   # งบเวลา ไม่ให้งานหมดเวลาก่อนคิดคะแนน
    for a in sorted(assets, key=lambda x: x.get("first_seen") or "", reverse=True):
        if a.get("lat") is None or a.get("status") != "active":
            continue
        old = locs.get(a["id"])
        if old and old.get("lat") == a["lat"] and old.get("lng") == a["lng"]:
            continue
        key = (round(a["lat"], 5), round(a["lng"], 5))
        if key in by_point:
            feat = {k: v for k, v in by_point[key].items() if k not in ("asset_id", "computed_at")}
        else:
            if budget <= 0 or time.time() > deadline:
                continue
            budget -= 1
            try:
                feat = geo.location_features(a["lat"], a["lng"], geo.overpass(a["lat"], a["lng"]))
            except Exception as e:  # noqa: BLE001
                print("overpass error", a["id"], e)
                continue
            feat.update(lat=a["lat"], lng=a["lng"])
        row = {"asset_id": a["id"], "computed_at": now_iso(), **feat}
        row.pop("raw", None)
        db.upsert("asset_location", row, "asset_id")
        locs[a["id"]] = row
        by_point[key] = row
    return locs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--reparse", action="store_true")
    ap.add_argument("--no-geo", action="store_true")
    ap.add_argument("--no-alerts", action="store_true")
    args = ap.parse_args(argv)

    db = DB()
    today = date.today()
    step_parse(db, args.reparse)

    assets = db.select("assets")
    rounds = _rounds_by_asset(db.select("auction_rounds"))
    users = {u["asset_id"]: u for u in db.select("asset_user")}

    # สถานะ
    for a in assets:
        st = asset_status(rounds.get(a["id"], []), today)
        if st == "active" and (a.get("raw") or {}).get("_struck"):
            st = "ended"          # แถวสีแดงในหน้าผลค้นหา = ขายแล้ว/งด/ถอน
        if st != a.get("status"):
            a["status"] = st
            db.update("assets", {"id": a["id"]}, {"status": st})

    locs: dict = {}
    if not args.no_geo:
        step_geocode(db, assets, users)
        locs = step_location(db, assets)
    else:
        locs = {l["asset_id"]: l for l in db.select("asset_location")}

    # คะแนน
    prev = {s["asset_id"]: s for s in db.select("asset_scores")}
    meds = local_medians(assets)
    scores, history = [], []
    for a in assets:
        s = score_asset(a, rounds.get(a["id"], []), locs.get(a["id"]), meds, CFG, today)
        s["computed_at"] = now_iso()
        scores.append(s)
        p = prev.get(a["id"])
        if not p or p.get("next_start_price") != s["next_start_price"] or p.get("next_round_no") != s["next_round_no"]:
            history.append({"asset_id": a["id"], "round_no": s["next_round_no"],
                            "start_price": s["next_start_price"], "status": a.get("status")})
    db.upsert("asset_scores", scores, "asset_id")
    db.insert("price_history", history)
    print(f"scored {len(scores)} assets, {len(history)} price changes")

    if not args.no_alerts:
        sent = {(r["asset_id"], r["kind"], r["ref"]) for r in db.select("alerts_sent")}
        amap = {a["id"]: a for a in assets}
        todo = AL.pick_alerts(amap, scores, users, prev, sent, CFG, today)
        dash = os.environ.get("DASHBOARD_URL")
        for kind, ref, s in todo:
            if AL.send(AL.message(kind, amap[s["asset_id"]], s, CFG["presets"], dash)):
                db.upsert("alerts_sent", {"asset_id": s["asset_id"], "kind": kind, "ref": ref}, "asset_id,kind,ref")
        print(f"alerts: {len(todo)}")


if __name__ == "__main__":
    import functools, builtins
    builtins.print = functools.partial(builtins.print, flush=True)
    main()

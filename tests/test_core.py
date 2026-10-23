import json
from datetime import date
from pathlib import Path

from worker import pipeline
from worker.geo import location_features
from worker.parse_led import parse_capture
from worker.rounds import asset_status, next_round, passed_no_sale
from worker.score import combine, local_medians, score_asset
from worker.thai import parse_area, to_date, to_number

FX = Path(__file__).parent / "fixtures"
CFG = json.loads((Path(__file__).parent.parent / "worker" / "config.json").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 23)


def test_thai_helpers():
    assert to_number("๑,๒๓๔,๕๐๐.๕๐ บาท") == 1234500.5
    assert to_date("12/11/2569") == date(2026, 11, 12)
    assert to_date("3 ธ.ค. 69") == date(2026, 12, 3)
    assert to_date("12 พฤศจิกายน พ.ศ. 2569") == date(2026, 11, 12)
    assert parse_area("1-2-35.5") == {"rai": 1, "ngan": 2, "wah": 35.5}
    assert parse_area("2 ไร่ 1 งาน 40 ตารางวา") == {"rai": 2, "ngan": 1, "wah": 40}
    assert parse_area("ห้องชุด 34.96 ตารางเมตร")["sqm"] == 34.96


def _list_items():
    cap = {"page_url": "https://asset.led.go.th/search", "page_kind": "list",
           "tables_html": (FX / "list_page.html").read_text(encoding="utf-8"), "page_text": ""}
    return parse_capture(cap)


def test_parse_list():
    items = _list_items()
    assert len(items) == 2
    a, b = items
    assert a["case_no"] == "ผบ.4820/2567"
    assert a["province"] == "ชลบุรี" and a["amphoe"] == "ศรีราชา" and a["tambon"] == "บางพระ"
    assert a["appraisal_price"] == 1850000 and a["area_wah"] == 52.5
    assert a["asset_type"] == "ที่ดินพร้อมสิ่งปลูกสร้าง"
    assert a["source_url"] == "https://asset.led.go.th/detail?id=101"
    assert a["_rounds"][2]["result"] == "ไม่มีผู้ซื้อ"
    assert a["_rounds"][4]["sale_date"] == date(2026, 11, 14)
    assert b["asset_type"] == "ห้องชุด"
    assert a["id"] != b["id"]


def test_parse_detail_and_rows():
    cap = {"page_url": "https://asset.led.go.th/detail?id=9", "page_kind": "detail",
           "tables_html": "", "page_text": (FX / "detail_page.txt").read_text(encoding="utf-8")}
    (d,) = parse_capture(cap)
    assert d["appraisal_price"] == 1240000
    assert d["appraisal_dept"] == 900000
    assert d["deposit"] == 62000
    assert d["case_no"] == "ผบ.987/2567" and d["lot_no"] == "3"
    assert d["deed_no"] == "45678"
    assert (d["area_rai"], d["area_ngan"], d["area_wah"]) == (2, 1, 40)
    assert d["province"] == "ราชบุรี" and d["amphoe"] == "โพธาราม" and d["tambon"] == "หนองโพ"
    assert d["asset_type"] == "ที่ดินเปล่า"
    assert "สำนักงานบังคับคดีจังหวัดราชบุรี" in d["office"]
    asset, rounds = pipeline.to_rows(d)
    prices = {r["round_no"]: r["start_price"] for r in rounds}
    assert prices == {1: 1240000, 2: 1116000, 3: 992000, 4: 868000}
    assert "_rounds" not in asset["raw"]


def test_rounds_logic():
    rr = [{"round_no": 1, "sale_date": date(2026, 10, 1), "result": "งดขาย", "start_price": 100},
          {"round_no": 2, "sale_date": date(2026, 10, 22), "result": None, "start_price": 90},
          {"round_no": 3, "sale_date": date(2026, 11, 12), "result": None, "start_price": 80}]
    assert next_round(rr, TODAY)["round_no"] == 3
    assert passed_no_sale(rr, TODAY) == 2
    assert asset_status(rr, TODAY) == "active"
    rr[2]["result"] = "ขายได้"
    assert asset_status(rr, TODAY) == "sold"


def test_location_features():
    els = [
        {"type": "way", "tags": {"highway": "primary"}, "geometry": [{"lat": 13.001, "lon": 100.0}]},
        {"type": "node", "lat": 13.01, "lon": 100.0, "tags": {"amenity": "hospital"}},
        {"type": "node", "lat": 13.002, "lon": 100.0, "tags": {"amenity": "school"}},
        {"type": "relation", "tags": {"landuse": "industrial"},
         "members": [{"geometry": [{"lat": 13.05, "lon": 100.0}]}]},
        {"type": "count", "tags": {"ways": "350"}},
    ]
    f = location_features(13.0, 100.0, els)
    assert 100 < f["d_main_road_m"] < 120
    assert 1100 < f["d_hospital_m"] < 1120
    assert 5500 < f["d_industrial_m"] < 5600
    assert f["n_building_1km"] == 350
    assert f["n_amenity_2km"] == 2   # school + hospital อยู่ใน 2 กม.


def test_scoring_orders_sensibly():
    base = {"province": "ชลบุรี", "amphoe": "ศรีราชา", "asset_type": "บ้านเดี่ยว", "area_wah": 50,
            "geo_precision": "address"}
    pool = [dict(base, id=f"x{i}", appraisal_price=2_000_000 + i * 50_000) for i in range(6)]
    meds = local_medians(pool)
    good_loc = {"d_main_road_m": 200, "d_market_m": 400, "d_school_m": 300, "d_hospital_m": 900,
                "d_rail_m": 3000, "d_industrial_m": 2500, "n_amenity_2km": 35, "n_building_1km": 900}
    far_loc = {"d_main_road_m": 5000, "d_market_m": 9000, "d_school_m": 6000, "d_hospital_m": 20000,
               "d_rail_m": 30000, "d_industrial_m": 40000, "n_amenity_2km": 1, "n_building_1km": 20}
    r4 = [{"round_no": n, "sale_date": date(2026, 9, n), "result": "ไม่มีผู้ซื้อ", "start_price": 0} for n in (1, 2, 3)]
    r4.append({"round_no": 4, "sale_date": date(2026, 11, 5), "result": None, "start_price": 1_260_000})
    r1 = [{"round_no": 1, "sale_date": date(2026, 11, 5), "result": None, "start_price": 1_800_000}]

    deal = score_asset(dict(base, id="deal", appraisal_price=1_800_000), r4, good_loc, meds, CFG, TODAY)
    meh = score_asset(dict(base, id="meh", appraisal_price=1_800_000), r1, far_loc, meds, CFG, TODAY)
    risky = score_asset(dict(base, id="risky", appraisal_price=1_800_000,
                             sale_condition="ขายเฉพาะส่วนของจำเลย ไม่มีทางเข้าออก"), r4, good_loc, meds, CFG, TODAY)

    assert deal["s_discount"] == 100 and meh["s_discount"] == 0
    assert deal["next_round_no"] == 4 and deal["next_start_price"] == 1_260_000
    assert deal["s_unit_price"] > 50          # ราคาประเมินต่อวาต่ำกว่าค่ากลาง
    for k in CFG["presets"]:
        assert deal[f"score_{k}"] > meh[f"score_{k}"]
        assert deal[f"score_{k}"] > risky[f"score_{k}"]
    assert "ขายเฉพาะส่วน/กรรมสิทธิ์รวม" in risky["risk_flags"]


def test_combine_missing_is_neutral():
    p = CFG["presets"]["flip"]
    assert combine({}, p, 0.3) == 50.0


def test_parse_real_led_layout():
    """โครงสร้างตารางจริงของ asset.led.go.th: หัว 2 ชั้น, ขายเป็นชุด, แถวสีแดง = ขายแล้ว/งด"""
    cap = {"page_url": "https://asset.led.go.th/newbidreg/default.asp", "page_kind": "list",
           "tables_html": (FX / "led_real_list.html").read_text(encoding="utf-8"), "page_text": ""}
    items = parse_capture(cap)
    assert len(items) == 3                       # 3 แปลงของชุด 16-1 รวมเป็น 1 รายการ
    s16, condo, house = items
    assert s16["case_no"] == "อ.745/2567" and s16["lot_no"] == "16 - 1"
    assert s16["n_plots"] == 3 and s16["appraisal_price"] == 84058859
    assert (s16["area_rai"], s16["area_ngan"], s16["area_wah"]) == (0, 1, 76.2)   # 133+31+12.2 = 176.2 วา = 1 งาน 76.2 วา
    assert s16["_struck"] is True
    assert s16["asset_type"] == "ที่ดินพร้อมสิ่งปลูกสร้าง"
    assert "asset_open.asp" in s16["source_url"]
    assert condo["asset_type"] == "ห้องชุด" and condo["_struck"] is False
    assert condo["tambon"] == "บางนา" and condo["amphoe"] == "บางนา"
    assert house["appraisal_price"] == 2400000 and house["area_wah"] == 25
    assert house["province"] == "กรุงเทพมหานคร" and house["amphoe"] == "ประเวศ"

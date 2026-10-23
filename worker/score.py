"""คะแนนย่อย 0–100 และคะแนนรวมตาม preset

ทุกคะแนนย่อยออกแบบให้ "50 = กลางๆ / ไม่มีข้อมูล" เพื่อไม่ให้ทรัพย์ที่ข้อมูลไม่ครบถูกดันขึ้นหรือกดลงเกินจริง
"""
from __future__ import annotations

import re
import statistics
from datetime import date

from .rounds import next_round, passed_no_sale
from .thai import total_wah


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def near(d, good, bad):
    """ระยะ d (เมตร): ≤good = 100, ≥bad = 0, ระหว่างนั้นลดเชิงเส้น; ไม่มีข้อมูล = None"""
    if d is None:
        return None
    if d <= good:
        return 100.0
    if d >= bad:
        return 0.0
    return 100.0 * (bad - d) / (bad - good)


def wavg(parts: list[tuple[float | None, float]]) -> float | None:
    got = [(v, w) for v, w in parts if v is not None]
    if not got:
        return None
    tw = sum(w for _, w in got)
    return sum(v * w for v, w in got) / tw


# ---------------------------------------------------------------- ราคาต่อหน่วย
def unit_price(a: dict, price: float | None) -> tuple[float | None, str | None]:
    if not price:
        return None, None
    if a.get("asset_type") == "ห้องชุด" or (a.get("area_sqm") and not any(
            a.get(k) for k in ("area_rai", "area_ngan", "area_wah"))):
        sqm = a.get("area_sqm")
        return (price / sqm, "sqm") if sqm else (None, "sqm")
    w = total_wah(a.get("area_rai"), a.get("area_ngan"), a.get("area_wah"))
    return (price / w, "wah") if w else (None, "wah")


def local_medians(assets: list[dict]) -> dict:
    """ค่ากลาง บาท/หน่วย (ราคาประเมิน) แยก (จังหวัด, อำเภอ, ประเภท) และ (จังหวัด, ประเภท)"""
    buckets: dict[tuple, list[float]] = {}
    for a in assets:
        up, kind = unit_price(a, a.get("appraisal_price"))
        if not up:
            continue
        for key in ((a.get("province"), a.get("amphoe"), a.get("asset_type"), kind),
                    (a.get("province"), None, a.get("asset_type"), kind)):
            buckets.setdefault(key, []).append(up)
    return {k: (statistics.median(v), len(v)) for k, v in buckets.items()}


# ---------------------------------------------------------------- ทำเล
def location_scores(loc: dict | None, precision: str | None, shrink: float) -> dict:
    if not loc:
        return {"s_location": None, "s_location_rent": None, "s_location_land": None}
    road = near(loc.get("d_main_road_m"), 300, 3000)
    road_land = near(loc.get("d_main_road_m"), 100, 2000)
    market = near(loc.get("d_market_m"), 500, 5000)
    school = near(loc.get("d_school_m"), 500, 4000)
    hosp = near(loc.get("d_hospital_m"), 1000, 10000)
    rail = near(loc.get("d_rail_m"), 1000, 10000)
    ind = near(loc.get("d_industrial_m"), 2000, 15000)
    ind_land = near(loc.get("d_industrial_m"), 3000, 20000)
    amen = clamp((loc.get("n_amenity_2km") or 0) / 30 * 100) if loc.get("n_amenity_2km") is not None else None
    bld = clamp((loc.get("n_building_1km") or 0) / 800 * 100) if loc.get("n_building_1km") is not None else None

    general = wavg([(road, .25), (market, .20), (school, .15), (hosp, .15), (amen, .15), (bld, .10)])
    rent = wavg([(ind, .30), (market, .20), (road, .20), (rail, .10), (amen, .10), (bld, .10)])
    land = wavg([(road_land, .50), (market, .20), (ind_land, .15), (amen, .15)])

    def adj(v):
        if v is None:
            return None
        # พิกัดระดับตำบล/อำเภอ = ไม่แน่นอน → ดึงเข้าหา 50
        if precision in ("tambon", "amphoe"):
            k = shrink if precision == "tambon" else min(1.0, shrink * 1.5)
            v = v * (1 - k) + 50 * k
        return round(v, 1)

    return {"s_location": adj(general), "s_location_rent": adj(rent), "s_location_land": adj(land)}


# ---------------------------------------------------------------- ความเสี่ยง
def risk(a: dict, cfg: dict) -> tuple[float, list[str]]:
    text = " ".join(filter(None, [a.get("sale_condition"), a.get("occupant_note"), a.get("_rowtext"),
                                  (a.get("raw") or {}).get("_rowtext") if isinstance(a.get("raw"), dict) else None,
                                  a.get("deed_type")]))
    pts, flags = 0.0, []
    for rk in cfg["risk_keywords"]:
        if re.search(rk["re"], text):
            pts += rk["pts"]
            flags.append(rk["label"])
    if not a.get("appraisal_price"):
        flags.append("ไม่พบราคาประเมิน")
    return clamp(pts), flags


# ---------------------------------------------------------------- รวม
def score_asset(a: dict, rounds: list[dict], loc: dict | None, medians: dict, cfg: dict,
                today: date | None = None) -> dict:
    today = today or date.today()
    nr = next_round(rounds, today)
    nxt_price = nr.get("start_price") if nr else None
    appraisal = a.get("appraisal_price")

    # 1) ส่วนลดจากราคาประเมิน: นัด1=0%, นัด4+=30% → 0..100
    ratio = (nxt_price / appraisal) if (nxt_price and appraisal) else None
    s_discount = clamp((1 - ratio) / 0.30 * 100) if ratio is not None else None

    # 2) ราคาต่อหน่วย: เทียบราคาประเมินต่อหน่วยกับค่ากลางพื้นที่ (ไม่นับส่วนลดตามนัดซ้ำ)
    #    ส่วน unit_price ที่แสดงบน Dashboard = ราคาที่ต้องจ่ายนัดถัดไปต่อหน่วย
    up, kind = unit_price(a, nxt_price or appraisal)
    up_app, _ = unit_price(a, appraisal)
    med, n = None, 0
    for key in ((a.get("province"), a.get("amphoe"), a.get("asset_type"), kind),
                (a.get("province"), None, a.get("asset_type"), kind)):
        if key in medians and medians[key][1] >= 4:
            med, n = medians[key]
            break
    s_unit = clamp(50 + (1 - up_app / med) * 100) if (up_app and med) else None

    # 3) การแข่งขัน: ยิ่งผ่านมาหลายนัดไม่มีคนซื้อ ยิ่งแข่งน้อย
    ns = passed_no_sale(rounds, today)
    s_comp = clamp(ns * 25) if rounds else None

    locs = location_scores(loc, a.get("geo_precision"), cfg["location"]["tambon_shrink"])
    rp, flags = risk(a, cfg)
    if locs["s_location"] is None:
        flags.append("ยังไม่มีข้อมูลทำเล")

    out = {
        "asset_id": a["id"],
        "next_round_no": nr.get("round_no") if nr else None,
        "next_sale_date": nr["sale_date"].isoformat() if nr and nr.get("sale_date") else None,
        "next_start_price": nxt_price,
        "price_ratio": round(ratio, 4) if ratio is not None else None,
        "unit_price": round(up, 2) if up else None,
        "unit_kind": kind,
        "local_median_unit": round(med, 2) if med else None,
        "local_comps": n,
        "s_discount": _r(s_discount),
        "s_unit_price": _r(s_unit),
        "s_competition": _r(s_comp),
        "risk_penalty": rp,
        "risk_flags": flags,
        **locs,
    }
    for name, p in cfg["presets"].items():
        out[f"score_{name}"] = combine(out, p, cfg["risk_weight"])
    return out


def combine(s: dict, preset: dict, risk_weight: float) -> float:
    """คะแนนรวม = ค่าเฉลี่ยถ่วงน้ำหนัก (ค่าที่ไม่มีข้อมูลนับเป็น 50) − risk × risk_weight
    Dashboard ใช้สูตรเดียวกันนี้ (dashboard/index.html: combine())"""
    w = preset["w"]
    vals = {
        "discount": s.get("s_discount"),
        "unit_price": s.get("s_unit_price"),
        "location": s.get(preset["location_key"]),
        "competition": s.get("s_competition"),
    }
    tot = sum(w.values())
    base = sum((50.0 if vals[k] is None else vals[k]) * w[k] for k in w) / tot
    return round(clamp(base - (s.get("risk_penalty") or 0) * risk_weight), 1)


def _r(v):
    return round(v, 1) if v is not None else None

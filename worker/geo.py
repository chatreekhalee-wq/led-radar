"""พิกัดและข้อมูลทำเลจาก OpenStreetMap
  * Nominatim: แปลงตำบล/อำเภอ/จังหวัด เป็นพิกัด (กฎการใช้: ≤1 ครั้ง/วินาที, ต้องมี User-Agent, แคชผล)
  * Overpass: ระยะถึงถนนหลัก/รพ./โรงเรียน/ตลาด/สถานี/นิคม และความหนาแน่นรอบแปลง
"""
from __future__ import annotations

import math
import os
import time

import requests

UA = os.environ.get("OSM_USER_AGENT", "LED-Radar/1.0 (personal property research)")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")

_last_nominatim = 0.0


def haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geocode_queries(a: dict) -> list[tuple[str, str]]:
    """คืนลำดับคำค้น (query, precision) จากละเอียดไปหยาบ"""
    p, am, t = a.get("province"), a.get("amphoe"), a.get("tambon")
    if not p:
        return []
    bkk = p == "กรุงเทพมหานคร"
    qs = []
    if t and am:
        qs.append((f"{'แขวง' if bkk else 'ตำบล'}{t} {'เขต' if bkk else 'อำเภอ'}{am} {p}", "tambon"))
    if am:
        qs.append((f"{'เขต' if bkk else 'อำเภอ'}{am} {p}", "amphoe"))
    return qs


def nominatim(q: str) -> tuple[float, float] | None:
    global _last_nominatim
    wait = 1.1 - (time.time() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim = time.time()
    r = requests.get(NOMINATIM, params={"q": q, "format": "json", "limit": 1, "countrycodes": "th",
                                        "accept-language": "th"},
                     headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    js = r.json()
    if not js:
        return None
    return float(js[0]["lat"]), float(js[0]["lon"])


OVERPASS_Q = """
[out:json][timeout:45];
way(around:3000,{lat},{lng})[highway~"^(motorway|trunk|primary|secondary)$"];
out geom qt;
(
  nwr(around:10000,{lat},{lng})[amenity=hospital];
  nwr(around:5000,{lat},{lng})[amenity~"^(school|college|university)$"];
  nwr(around:6000,{lat},{lng})[amenity=marketplace];
  nwr(around:6000,{lat},{lng})[shop~"^(supermarket|mall|department_store)$"];
  nwr(around:12000,{lat},{lng})[railway~"^(station|halt)$"];
  nwr(around:20000,{lat},{lng})[landuse=industrial];
);
out center qt;
nwr(around:2000,{lat},{lng})[amenity];
out count;
way(around:1000,{lat},{lng})[building];
out count;
"""


def _elem_points(e) -> list[tuple[float, float]]:
    if "geometry" in e and e["geometry"]:
        return [(g["lat"], g["lon"]) for g in e["geometry"] if g]
    if e.get("members"):
        pts = []
        for m in e["members"]:
            pts += [(g["lat"], g["lon"]) for g in (m.get("geometry") or []) if g]
            if "lat" in m:
                pts.append((m["lat"], m["lon"]))
        if pts:
            return pts[::max(1, len(pts) // 400)]
    if "center" in e:
        return [(e["center"]["lat"], e["center"]["lon"])]
    if "lat" in e:
        return [(e["lat"], e["lon"])]
    return []


def location_features(lat: float, lng: float, elements: list[dict]) -> dict:
    """แปลงผล Overpass เป็นระยะใกล้สุดแต่ละหมวด (เมตร) — แยกเป็นฟังก์ชันเพื่อทดสอบได้"""
    best = {k: None for k in ("d_main_road_m", "d_hospital_m", "d_school_m", "d_market_m",
                              "d_rail_m", "d_industrial_m")}
    n_amenity, n_building = 0, 0

    def upd(key, d):
        if best[key] is None or d < best[key]:
            best[key] = d

    counts = [e for e in elements if e.get("type") == "count"]
    if len(counts) >= 2:                       # ลำดับใน query: amenity ก่อน แล้ว building
        n_amenity = int(counts[0].get("tags", {}).get("total", 0) or 0)
        n_building = int(counts[1].get("tags", {}).get("ways", 0) or counts[1].get("tags", {}).get("total", 0) or 0)
    elif counts:
        n_building = int(counts[0].get("tags", {}).get("ways", 0) or counts[0].get("tags", {}).get("total", 0) or 0)
    have_amenity_count = len(counts) >= 2
    for e in elements:
        if e.get("type") == "count":
            continue
        tags = e.get("tags", {})
        pts = _elem_points(e)
        if not pts:
            continue
        d = min(haversine(lat, lng, p[0], p[1]) for p in pts)
        hw, am, shop = tags.get("highway"), tags.get("amenity"), tags.get("shop")
        if hw in ("motorway", "trunk", "primary", "secondary"):
            upd("d_main_road_m", d)
        if am == "hospital":
            upd("d_hospital_m", d)
        if am in ("school", "college", "university"):
            upd("d_school_m", d)
        if am == "marketplace" or shop in ("supermarket", "mall", "department_store"):
            upd("d_market_m", d)
        if tags.get("railway") in ("station", "halt") or tags.get("public_transport") == "station":
            upd("d_rail_m", d)
        if tags.get("landuse") == "industrial":
            upd("d_industrial_m", d)
        if am and d <= 2000 and not have_amenity_count:
            n_amenity += 1
    out = {k: (round(v) if v is not None else None) for k, v in best.items()}
    out["n_amenity_2km"] = n_amenity
    out["n_building_1km"] = n_building
    return out


def overpass(lat: float, lng: float) -> list[dict]:
    q = OVERPASS_Q.format(lat=lat, lng=lng)
    for attempt in range(2):
        r = requests.post(OVERPASS, data={"data": q}, headers={"User-Agent": UA}, timeout=60)
        if r.status_code in (429, 504):
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json().get("elements", [])
    raise RuntimeError("overpass busy")

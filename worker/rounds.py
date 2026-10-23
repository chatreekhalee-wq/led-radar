"""กติการาคาเริ่มต้นตามนัด และการหานัดถัดไป"""
from __future__ import annotations

from datetime import date

SOLD = {"ขายได้"}
ENDED = {"ขายได้", "ถอนการยึด", "ยกเลิก"}
NO_SALE = {"งดขาย", "ไม่มีผู้ซื้อ"}


def pct_for_round(n: int, cfg: dict) -> float:
    table = cfg["round_price_pct"]
    return float(table.get(str(n), table["default"]))


def start_price(appraisal: float | None, n: int, cfg: dict) -> float | None:
    if not appraisal:
        return None
    return round(appraisal * pct_for_round(n, cfg), 2)


def fill_round_prices(appraisal: float | None, rounds: dict[int, dict], cfg: dict) -> dict[int, dict]:
    for n, r in rounds.items():
        if r.get("start_price") is None:
            r["start_price"] = start_price(appraisal, n, cfg)
    return rounds


def next_round(rounds: list[dict], today: date | None = None) -> dict | None:
    """rounds: [{round_no, sale_date, result, start_price}] -> นัดถัดไปที่ยังไม่ผ่าน (None ถ้าจบแล้ว)"""
    today = today or date.today()
    if any((r.get("result") in ENDED) for r in rounds):
        return None
    upcoming = [r for r in rounds if r.get("sale_date") and r["sale_date"] >= today and not r.get("result")]
    if upcoming:
        return min(upcoming, key=lambda r: r["sale_date"])
    return None


def passed_no_sale(rounds: list[dict], today: date | None = None) -> int:
    """จำนวนนัดที่ผ่านไปแล้วโดยไม่มีคนซื้อ (นัดที่วันผ่านไปแล้วและไม่มีผล ถือว่าไม่มีผู้ซื้อด้วย)"""
    today = today or date.today()
    n = 0
    for r in rounds:
        res = r.get("result")
        if res in NO_SALE or (not res and r.get("sale_date") and r["sale_date"] < today):
            n += 1
    return n


def asset_status(rounds: list[dict], today: date | None = None) -> str:
    if any(r.get("result") in SOLD for r in rounds):
        return "sold"
    if any(r.get("result") in ("ถอนการยึด", "ยกเลิก") for r in rounds):
        return "withdrawn"
    if rounds and next_round(rounds, today) is None:
        return "ended"
    return "active"

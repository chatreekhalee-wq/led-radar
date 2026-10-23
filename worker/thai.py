"""แปลงตัวเลข วันที่ เนื้อที่ แบบไทย"""
from __future__ import annotations

import re
from datetime import date

THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

MONTHS = {
    "มกราคม": 1, "ม.ค.": 1, "กุมภาพันธ์": 2, "ก.พ.": 2, "มีนาคม": 3, "มี.ค.": 3,
    "เมษายน": 4, "เม.ย.": 4, "พฤษภาคม": 5, "พ.ค.": 5, "มิถุนายน": 6, "มิ.ย.": 6,
    "กรกฎาคม": 7, "ก.ค.": 7, "สิงหาคม": 8, "ส.ค.": 8, "กันยายน": 9, "ก.ย.": 9,
    "ตุลาคม": 10, "ต.ค.": 10, "พฤศจิกายน": 11, "พ.ย.": 11, "ธันวาคม": 12, "ธ.ค.": 12,
}


def norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.translate(THAI_DIGITS).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def to_number(s: str | None) -> float | None:
    """'1,234,500.00 บาท' -> 1234500.0"""
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", norm(str(s)))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _year(y: int) -> int:
    if y > 2400:      # พ.ศ.
        return y - 543
    if y < 100:       # 2 หลัก -> สมมติ พ.ศ. 25xx
        return y + 2500 - 543
    return y


def to_date(s: str | None) -> date | None:
    """รองรับ 12/11/2569, 12-11-69, '12 พฤศจิกายน 2569', '12 พ.ย. 69'"""
    s = norm(s)
    if not s:
        return None
    m = re.search(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2,4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), _year(int(m.group(3)))
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    for name, mo in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        m = re.search(r"(\d{1,2})\s*" + re.escape(name) + r"\s*(?:พ\.ศ\.\s*)?(\d{2,4})", s)
        if m:
            try:
                return date(_year(int(m.group(2))), mo, int(m.group(1)))
            except ValueError:
                return None
    return None


def parse_area(s: str | None) -> dict:
    """คืน {'rai','ngan','wah'} หรือ {'sqm'}  รองรับ '1-2-35.5', '1 ไร่ 2 งาน 35 ตร.วา', '45.5 ตารางเมตร'"""
    s = norm(s)
    out: dict = {}
    if not s:
        return out
    m = re.search(r"([\d.,]+)\s*(?:ตาราง\s*เมตร|ตร\.?\s*ม\.?)", s)
    if m:
        out["sqm"] = to_number(m.group(1))
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*-\s*(\d+(?:\.\d+)?)", s)
    if m:
        out.update(rai=float(m.group(1)), ngan=float(m.group(2)), wah=float(m.group(3)))
        return out
    for key, pat in (("rai", r"([\d.]+)\s*ไร่"), ("ngan", r"([\d.]+)\s*งาน"),
                     ("wah", r"([\d.]+)\s*(?:ตาราง\s*วา|ตร\.?\s*ว\.?|วา)")):
        mm = re.search(pat, s)
        if mm:
            out[key] = float(mm.group(1))
    return out


def total_wah(rai=0, ngan=0, wah=0) -> float:
    return (rai or 0) * 400 + (ngan or 0) * 100 + (wah or 0)


_LOC_PATTERNS = {
    "tambon": r"(?:ตำบล|แขวง|ต\.)\s*([ก-๙A-Za-z]+(?:\s?[ก-๙]+)?)",
    "amphoe": r"(?:อำเภอ|เขต|อ\.)\s*([ก-๙A-Za-z]+(?:\s?[ก-๙]+)?)",
    "province": r"(?:จังหวัด|จ\.)\s*([ก-๙A-Za-z]+)",
}


def parse_location(s: str | None) -> dict:
    s = norm(s)
    out = {}
    for k, pat in _LOC_PATTERNS.items():
        m = re.search(pat, s)
        if m:
            v = m.group(1).strip()
            v = re.split(r"\s(?:อำเภอ|เขต|จังหวัด|อ\.|จ\.)", v)[0]
            out[k] = v
    if "province" not in out and re.search(r"กรุงเทพ", s):
        out["province"] = "กรุงเทพมหานคร"
    return out


def normalize_province(p: str | None) -> str | None:
    if not p:
        return p
    p = norm(p)
    if p in ("กทม", "กทม.", "กรุงเทพ", "กรุงเทพฯ"):
        return "กรุงเทพมหานคร"
    return p

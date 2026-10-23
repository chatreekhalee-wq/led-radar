"""แปลงหน้าเว็บที่เก็บมา (raw_captures) เป็นรายการทรัพย์

ออกแบบให้ทนต่อการเปลี่ยนหน้าตาเว็บ:
  * หน้าผลค้นหา: หา <table> ที่มีหัวคอลัมน์ตรงกับคำในตาราง HEADER_MAP แล้วอ่านทีละแถว
  * หน้ารายละเอียด: ดึงค่าจากข้อความด้วย regex (ราคาประเมิน, นัดที่ 1–6, ที่ตั้ง, เนื้อที่ ...)
ถ้าเว็บเปลี่ยนรูปแบบ ให้แก้ HEADER_MAP / DETAIL_PATTERNS แล้วรัน
`python -m worker.pipeline --reparse` ระบบจะ parse ข้อมูลดิบเดิมใหม่ทั้งหมด (ไม่ต้องเก็บใหม่)
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .thai import (norm, normalize_province, parse_area, parse_location, to_date,
                   to_number)

# หัวคอลัมน์ -> ชื่อฟิลด์ (เทียบแบบ "มีคำนี้อยู่ในหัวคอลัมน์", ลำดับสำคัญ: ตัวที่เจาะจงกว่าอยู่ก่อน)
HEADER_MAP: list[tuple[str, str]] = [
    (r"ราคา.*ธนารักษ์|ธนารักษ์", "appraisal_dept"),
    (r"ราคาเริ่ม", "listed_start_price"),
    (r"ราคาประเมิน|ราคาเจ้าพนักงาน|ราคาที่กำหนด", "appraisal_price"),
    (r"หลักประกัน", "deposit"),
    (r"หมายเลขคดี|คดีหมายเลข|เลขคดี|คดีแดง", "case_no"),
    (r"ลำดับที่|ล็อต|ล๊อต|ชุดที่|ลำดับ", "lot_no"),
    (r"ประเภททรัพย์|ประเภท", "asset_type"),
    (r"โฉนด|เอกสารสิทธิ|เลขที่ห้อง|น\.ส\.3", "deed_no"),
    (r"ตำบล|แขวง", "tambon"),
    (r"อำเภอ|เขต", "amphoe"),
    (r"จังหวัด", "province"),
    (r"ที่ตั้ง|สถานที่ตั้ง|ทำเล", "address_text"),
    (r"เนื้อที่|ขนาด", "area_text"),
    (r"^ไร่$", "area_rai"),
    (r"^งาน$", "area_ngan"),
    (r"ตร\.?\s*วา|ตารางวา|^วา$", "area_wah"),
    (r"ตร\.?\s*ม|ตารางเมตร", "area_sqm"),
    (r"วันที่ขาย|วันนัด|นัดขาย|วันที่", "sale_date"),
    (r"สถานะ|ผลการขาย", "status_text"),
    (r"สำนักงาน", "office"),
    (r"หมายเหตุ|เงื่อนไข", "sale_condition"),
]

TYPE_MAP = [
    (r"ห้องชุด|คอนโด", "ห้องชุด"),
    (r"ทาวน์|ทาวน์เฮ้าส์|ทาวน์เฮาส์|บ้านแถว", "ทาวน์เฮ้าส์"),
    (r"อาคารพาณิชย์|ตึกแถว|อาคารพาณิช", "อาคารพาณิชย์"),
    (r"บ้านแฝด", "บ้านแฝด"),
    (r"บ้าน|สิ่งปลูกสร้าง|อาคาร", "บ้านเดี่ยว"),
    (r"ที่ดินว่างเปล่า|ที่ดินเปล่า|ที่ดิน|ที่ว่าง", "ที่ดินเปล่า"),
]

ROUND_RESULT = [
    (r"ขายได้|ขายไปแล้ว", "ขายได้"),
    (r"ถอนการยึด|ถอนยึด", "ถอนการยึด"),
    (r"งดขาย|งดการขาย", "งดขาย"),
    (r"ไม่มีผู้(?:ซื้อ|เสนอ|สู้)", "ไม่มีผู้ซื้อ"),
    (r"เลื่อน", "เลื่อน"),
    (r"ยกเลิก", "ยกเลิก"),
]

DETAIL_PATTERNS = {
    "appraisal_dept": r"(?:ราคาประเมิน)?(?:ของ)?\s*กรมธนารักษ์[^\d]{0,40}([\d,]+(?:\.\d+)?)",
    "appraisal_price": r"ราคาประเมิน(?:ของ)?(?:เจ้าพนักงาน(?:บังคับคดี|ประเมินราคาทรัพย์)?|ของ?ผู้เชี่ยวชาญ|ของคณะกรรมการ)?[^\d]{0,40}([\d,]+(?:\.\d+)?)",
    "deposit": r"หลักประกัน[^\d]{0,40}([\d,]+(?:\.\d+)?)",
    "case_no": r"(?:คดี)?หมายเลขแดง(?:ที่)?\s*([ก-๙\.]{1,6}\s?\d+/\d{2,4})",
    "lot_no": r"(?:ลำดับที่|ล็อตที่|ชุดที่)\s*(\d+(?:-\d+)?)",
    "deed_no": r"(?:โฉนด(?:ที่ดิน)?(?:เลขที่)?|น\.ส\.\s?3\s?ก?\.?\s*เลขที่|ห้องชุดเลขที่)\s*([\d/,\s]+)",
    "office": r"(สำนักงานบังคับคดี[ก-๙A-Za-z\s]{2,40}?)(?:\s{2,}|\n|โทร|$)",
}

ROUND_LINE = re.compile(
    r"นัดที่\s*([1-9])\s*[:\-]?\s*(?:วันที่\s*)?"
    r"(\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}|\d{1,2}\s*[ก-๙\.]+\s*\d{2,4})"
    r"((?:(?!นัดที่)[^\n]){0,60})"
)


def _hdr_field(h: str) -> str | None:
    h = norm(h)
    for pat, field in HEADER_MAP:
        if re.search(pat, h):
            return field
    return None


def classify_type(*texts: str | None) -> str | None:
    t = " ".join(norm(x) for x in texts if x)
    if not t:
        return None
    for pat, label in TYPE_MAP:
        if re.search(pat, t):
            return label
    return "อื่นๆ"


def round_result(s: str | None) -> str | None:
    s = norm(s)
    for pat, label in ROUND_RESULT:
        if re.search(pat, s):
            return label
    return None


def make_id(a: dict) -> str:
    if a.get("case_no") and a.get("lot_no"):
        key = f"c|{norm(a['case_no'])}|{norm(a['lot_no'])}"
    elif a.get("deed_no") and (a.get("province") or a.get("amphoe")):
        key = f"d|{norm(a['deed_no'])}|{a.get('province','')}|{a.get('amphoe','')}"
    else:
        key = "r|" + norm(a.get("_rowtext", ""))[:400]
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _finish(a: dict) -> dict:
    """เติมค่าที่คำนวณได้ และทำความสะอาด"""
    if a.get("area_text"):
        ar = parse_area(a["area_text"])
        a.setdefault("area_rai", ar.get("rai"))
        a.setdefault("area_ngan", ar.get("ngan"))
        a.setdefault("area_wah", ar.get("wah"))
        if ar.get("sqm"):
            a.setdefault("area_sqm", ar["sqm"])
    for k in ("area_rai", "area_ngan", "area_wah", "area_sqm",
              "appraisal_price", "appraisal_dept", "deposit", "listed_start_price"):
        if isinstance(a.get(k), str):
            a[k] = to_number(a[k])
    loc = parse_location(" ".join(filter(None, [a.get("address_text"), a.get("_rowtext")])))
    for k in ("tambon", "amphoe", "province"):
        if not a.get(k) and loc.get(k):
            a[k] = loc[k]
        if a.get(k):
            a[k] = re.sub(r"^(ตำบล|แขวง|อำเภอ|เขต|จังหวัด|ต\.|อ\.|จ\.)\s*", "", norm(a[k]))
    a["province"] = normalize_province(a.get("province"))
    t = classify_type(a.get("asset_type"), a.get("deed_type"))
    if not t or t == "อื่นๆ":
        t = classify_type(a.get("_rowtext")) or t
    a["asset_type"] = t
    if a.get("deed_no"):
        a["deed_no"] = norm(a["deed_no"]).strip(" ,")
    a["id"] = make_id(a)
    return a


def parse_list_tables(tables_html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(tables_html or "", "lxml")
    out: list[dict] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        # หาแถวหัวตาราง: แถวแรกที่จับคู่ฟิลด์ได้ ≥ 2
        header_idx, fields = None, []
        for i, tr in enumerate(rows[:5]):
            cells = tr.find_all(["th", "td"])
            f = [_hdr_field(c.get_text(" ", strip=True)) for c in cells]
            if sum(1 for x in f if x) >= 2:
                header_idx, fields = i, f
                break
        if header_idx is None:
            continue
        for tr in rows[header_idx + 1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < max(2, len(fields) // 2):
                continue
            a: dict = {}
            rounds: dict[int, dict] = {}
            for field, c in zip(fields, cells):
                txt = norm(c.get_text(" ", strip=True))
                if not field or not txt:
                    continue
                if field == "sale_date":
                    # บางเว็บใส่นัด 1–6 ในช่องเดียว
                    found = list(ROUND_LINE.finditer(txt))
                    if found:
                        for m in found:
                            rounds[int(m.group(1))] = {"sale_date": to_date(m.group(2)),
                                                       "result": round_result(m.group(3))}
                    else:
                        d = to_date(txt)
                        if d:
                            rounds.setdefault(1, {"sale_date": d, "result": None})
                elif field == "status_text":
                    a["status_text"] = txt
                else:
                    a.setdefault(field, txt)
            rowtext = norm(tr.get_text(" ", strip=True))
            if not rowtext or not (a.get("appraisal_price") or a.get("listed_start_price")
                                   or a.get("deed_no") or a.get("case_no")):
                continue
            a["_rowtext"] = rowtext
            link = tr.find("a", href=True)
            if link:
                a["source_url"] = urljoin(base_url, link["href"])
            for m in ROUND_LINE.finditer(rowtext):
                rounds.setdefault(int(m.group(1)), {"sale_date": to_date(m.group(2)),
                                                    "result": round_result(m.group(3))})
            a["_rounds"] = rounds
            out.append(_finish(a))
    return out


def parse_detail_text(text: str, url: str, tables_html: str = "", images=None) -> dict | None:
    t = norm(text)
    if not t:
        return None
    a: dict = {"source_url": url, "_rowtext": t[:2000]}
    for field, pat in DETAIL_PATTERNS.items():
        m = re.search(pat, t)
        if m:
            a[field] = m.group(1).strip()
    # ราคาประเมินห้ามจับราคากรมธนารักษ์ซ้ำ
    if a.get("appraisal_price") and a.get("appraisal_dept") and a["appraisal_price"] == a["appraisal_dept"]:
        m = list(re.finditer(DETAIL_PATTERNS["appraisal_price"], t))
        for mm in m:
            if "ธนารักษ์" not in t[max(0, mm.start() - 5): mm.end()]:
                a["appraisal_price"] = mm.group(1)
                break
    # เนื้อที่
    m = re.search(r"เนื้อที่[^\n]{0,80}", t)
    if m:
        a["area_text"] = m.group(0)
    # ที่ตั้ง
    m = re.search(r"(?:ที่ตั้ง|ตั้งอยู่)[^\n]{0,160}", t)
    a["address_text"] = m.group(0) if m else None
    # ประเภท
    m = re.search(r"ประเภท(?:ทรัพย์)?\s*[:：]?\s*([^\n]{2,60})", t)
    a["asset_type"] = classify_type(m.group(1) if m else None, t[:600])
    m = re.search(r"(โฉนด|น\.ส\.\s?3\s?ก?|ห้องชุด|ส\.ป\.ก|สค\.?\s?1)", t)
    a["deed_type"] = m.group(1) if m else None
    # เงื่อนไข/ผู้อาศัย (เก็บข้อความรอบคำสำคัญไว้คิดความเสี่ยง)
    conds = []
    for kw in ("หมายเหตุ", "เงื่อนไข", "ขายเฉพาะส่วน", "จำนอง", "ภาระจำยอม", "ผู้อาศัย", "ครอบครอง",
               "ทางเข้าออก", "เวนคืน", "รุกล้ำ"):
        for mm in re.finditer(kw, t):
            conds.append(t[max(0, mm.start() - 40): mm.end() + 120])
    a["sale_condition"] = " | ".join(dict.fromkeys(conds))[:3000] or None
    m = re.search(r"(?:ผู้อาศัย|ผู้ครอบครอง)[^\n]{0,120}", t)
    a["occupant_note"] = m.group(0) if m else None
    # นัดขาย
    rounds = {}
    for mm in ROUND_LINE.finditer(t):
        rounds.setdefault(int(mm.group(1)), {"sale_date": to_date(mm.group(2)),
                                             "result": round_result(mm.group(3))})
    a["_rounds"] = rounds
    a["images"] = list(images or [])[:12]
    if not (a.get("appraisal_price") or a.get("deed_no") or a.get("case_no")):
        return None
    return _finish(a)


def parse_capture(cap: dict) -> list[dict]:
    """cap = แถวจาก raw_captures"""
    url = cap.get("page_url") or ""
    kind = cap.get("page_kind")
    items = []
    if kind != "detail":
        items = parse_list_tables(cap.get("tables_html") or "", url)
    if not items:
        d = parse_detail_text(cap.get("page_text") or "", url, cap.get("tables_html") or "", cap.get("images"))
        if d:
            items = [d]
    return items

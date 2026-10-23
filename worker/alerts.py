"""แจ้งเตือน Telegram: ทรัพย์ใหม่คะแนนสูง / ใกล้ถึงนัด / ราคาลด"""
from __future__ import annotations

import html
import os
from datetime import date

import requests


def fmt_baht(v) -> str:
    return "-" if v is None else f"{v:,.0f} ฿"


def best_preset(s: dict, presets: dict) -> tuple[str, float]:
    best = max(presets, key=lambda k: s.get(f"score_{k}") or 0)
    return presets[best]["label"], s.get(f"score_{best}") or 0


def message(kind: str, a: dict, s: dict, presets: dict, dash_url: str | None) -> str:
    head = {"new_high": "🟢 ทรัพย์ใหม่คะแนนสูง", "round_soon": "⏰ ใกล้ถึงนัดขาย",
            "price_drop": "🔻 ราคาลดลงแล้ว"}[kind]
    label, sc = best_preset(s, presets)
    loc = " ".join(filter(None, [a.get("tambon"), a.get("amphoe"), a.get("province")]))
    lines = [
        f"<b>{head}</b>",
        f"{html.escape(a.get('asset_type') or '-')} · {html.escape(loc)}",
        f"คะแนน <b>{sc:.0f}</b> ({label})",
        f"นัดที่ {s.get('next_round_no') or '-'} วันที่ {s.get('next_sale_date') or '-'}",
        f"ราคาเริ่ม {fmt_baht(s.get('next_start_price'))} (ประเมิน {fmt_baht(a.get('appraisal_price'))})",
    ]
    if s.get("risk_flags"):
        lines.append("⚠️ " + html.escape(", ".join(s["risk_flags"][:3])))
    links = []
    if dash_url:
        links.append(f'<a href="{html.escape(dash_url)}#{a["id"]}">Dashboard</a>')
    if a.get("source_url"):
        links.append(f'<a href="{html.escape(a["source_url"])}">ประกาศ</a>')
    if links:
        lines.append(" · ".join(links))
    return "\n".join(lines)


def send(text: str) -> bool:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[alert:dry-run]\n" + text)
        return True
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=30)
    if not r.ok:
        print("telegram error", r.status_code, r.text[:200])
    return r.ok


def pick_alerts(assets: dict, scores: list[dict], users: dict, prev: dict, sent: set,
                cfg: dict, today: date | None = None) -> list[tuple[str, str, dict]]:
    """คืน [(kind, ref, score_row)] ที่ควรส่ง (ยังไม่เคยส่ง)"""
    today = today or date.today()
    ac = cfg["alerts"]
    out = []
    for s in scores:
        aid = s["asset_id"]
        a = assets.get(aid) or {}
        if a.get("status", "active") != "active":
            continue
        best = max((s.get(f"score_{k}") or 0) for k in cfg["presets"])
        watch = bool((users.get(aid) or {}).get("watch"))
        # ทรัพย์ใหม่คะแนนสูง
        if best >= ac["new_high_min_score"] and (aid, "new_high", "1") not in sent:
            out.append(("new_high", "1", s))
        # ใกล้ถึงนัด
        if s.get("next_sale_date") and (watch or best >= ac["round_soon_min_score"]):
            days = (date.fromisoformat(s["next_sale_date"]) - today).days
            ref = f"r{s.get('next_round_no')}"
            if 0 <= days <= ac["round_soon_days"] and (aid, "round_soon", ref) not in sent:
                out.append(("round_soon", ref, s))
        # ราคาเริ่มลดลงเทียบรอบก่อน (เฉพาะที่ติดตามหรือคะแนนผ่านเกณฑ์)
        old = prev.get(aid)
        if (old and s.get("next_start_price") and old.get("next_start_price")
                and s["next_start_price"] < old["next_start_price"]
                and (watch or best >= ac["round_soon_min_score"])):
            ref = f"p{int(s['next_start_price'])}"
            if (aid, "price_drop", ref) not in sent:
                out.append(("price_drop", ref, s))
    out.sort(key=lambda t: -max((t[2].get(f"score_{k}") or 0) for k in cfg["presets"]))
    return out[: ac["max_per_run"]]

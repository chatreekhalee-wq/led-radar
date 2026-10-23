"""ตัวเชื่อม Supabase ผ่าน PostgREST (ใช้ service role key — เก็บใน GitHub Secrets เท่านั้น)"""
from __future__ import annotations

import json
import os
from datetime import date, datetime

import requests


def _default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(type(o))


class DB:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ["SUPABASE_URL"]).rstrip("/") + "/rest/v1"
        key = key or os.environ["SUPABASE_SERVICE_KEY"]
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.s = requests.Session()

    def select(self, table: str, params: dict | None = None, page: int = 1000) -> list[dict]:
        out, offset = [], 0
        while True:
            p = {"select": "*", **(params or {}), "limit": page, "offset": offset}
            r = self.s.get(f"{self.url}/{table}", headers=self.h, params=p, timeout=60)
            r.raise_for_status()
            rows = r.json()
            out += rows
            if len(rows) < page:
                return out
            offset += page

    def upsert(self, table: str, rows: list[dict] | dict, on_conflict: str):
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            return
        # PostgREST ต้องการคีย์เท่ากันทุกแถวใน batch → จัดกลุ่มตามชุดคีย์
        groups: dict[tuple, list] = {}
        for row in rows:
            groups.setdefault(tuple(sorted(row)), []).append(row)
        for keys, batch in groups.items():
            for i in range(0, len(batch), 500):
                r = self.s.post(f"{self.url}/{table}", params={"on_conflict": on_conflict},
                                headers={**self.h, "Prefer": "resolution=merge-duplicates,return=minimal"},
                                data=json.dumps(batch[i:i + 500], default=_default, ensure_ascii=False),
                                timeout=120)
                if r.status_code >= 300:
                    raise RuntimeError(f"upsert {table}: {r.status_code} {r.text[:300]}")

    def insert(self, table: str, rows: list[dict]):
        if not rows:
            return
        r = self.s.post(f"{self.url}/{table}", headers={**self.h, "Prefer": "return=minimal"},
                        data=json.dumps(rows, default=_default, ensure_ascii=False), timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"insert {table}: {r.status_code} {r.text[:300]}")

    def update(self, table: str, match: dict, values: dict):
        params = {k: f"eq.{v}" for k, v in match.items()}
        r = self.s.patch(f"{self.url}/{table}", params=params, headers={**self.h, "Prefer": "return=minimal"},
                         data=json.dumps(values, default=_default, ensure_ascii=False), timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"update {table}: {r.status_code} {r.text[:300]}")

    def rpc(self, fn: str, args: dict):
        r = self.s.post(f"{self.url}/rpc/{fn}", headers=self.h,
                        data=json.dumps(args, default=_default, ensure_ascii=False), timeout=60)
        r.raise_for_status()
        return r.json()

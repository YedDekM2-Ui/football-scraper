# -*- coding: utf-8 -*-
"""เกรดใบเตือน FABEL5 ที่ค้างอยู่ — จาก "สกอร์จบเกม" ไม่ต้องรอเจ้าของตอบ

ทำไมต้องมี (18 ส.ค. 69): ใบเตือน 93 ใบ เกรดแล้วแค่ 21 · ค้าง 72
   เพราะทางเดียวที่เกรดได้คือเจ้าของตอบสกอร์ใต้ใบเตือนเอง
   → ยิ่งเตือนเยอะ ตัวเลขยิ่งไม่ขยับ ระบบวัดตัวเองไม่ได้

⚠️ ห้ามเกรดจากคอลัมน์ "นาทีที่เห็นลูก" (gmin) — ช่องว่างแปลได้ 2 อย่าง
   "ไม่มีลูกจริง" กับ "ท่อจดพลาด" (เคยพลาด 8 ใน 13 ใบ 11 ส.ค. 69)
   นับเฉพาะใบที่มี gmin = ได้ 100% ปลอม → ตัวนี้จึงยึดสกอร์จบอย่างเดียว

แบ่งงานกับ GAS: ตัวนี้หาสกอร์ · GAS (f5Grade_) เป็นคนตัดสิน+เขียนชีต
   เกณฑ์ตัดสินอยู่ที่ f5Judge_ ที่เดียว ตัวเดียวกับตอนเจ้าของตอบเอง — ห้ามคิดซ้ำที่นี่

รัน:  python fb_f5grade.py [--days N] [--dry]
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

BKK = timezone(timedelta(hours=7))
DEPLOY_FILE = r"D:\Projects\t.seeedz\PIKTAX\.deployId"
KEY_FILE = r"D:\Projects\.gas-creds\piktax-admin-key.txt"

GRADE_AFTER_H = 3.5   # หลังเที่ยงคืนของวันนั้นอีกกี่ชม.ถึงเริ่มเกรด (เผื่อคู่ดึกสุดจบ)
MAX_DAYS = 3          # ดึง Forebet กี่วันต่อรอบ (อ้อม ?ff= ~90 วิ/วัน — รอบถัดไปเก็บตกเอง)
BATCH = 40            # ส่งเข้า GAS ทีละกี่ใบ (ยิงแบบ GET — URL ยาวเกินแล้วโดนตัด)


# ── ทางคุยกับ PIKTAX (ก๊อปจาก fb_value — ตั้งใจซ้ำ ไม่ import ข้ามไฟล์) ────────
def _piktax_url():
    u = (os.environ.get("PIKTAX_STATE_URL") or "").split("?")[0]
    if not u and os.path.exists(DEPLOY_FILE):
        dep = open(DEPLOY_FILE, encoding="utf-8").read().strip().split()[0]
        u = f"https://script.google.com/macros/s/{dep}/exec"
    return u


def _admin_key():
    k = os.environ.get("PIKTAX_ADMIN_KEY", "").strip()
    if k or not os.path.exists(KEY_FILE):
        return k
    for line in open(KEY_FILE, encoding="utf-8"):
        line = line.strip()
        if re.fullmatch(r"[A-Za-z0-9_.\-]{8,}", line):
            return line
    return ""


def _call(action, params, timeout=180):
    url, key = _piktax_url(), _admin_key()
    if not url or not key:
        return False, "ไม่มี PIKTAX_STATE_URL หรือ PIKTAX_ADMIN_KEY"
    q = {"admin": key, "action": action}
    q.update(params)
    try:
        r = requests.get(url, params=q, timeout=timeout)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return r.status_code == 200, r.text


# ── ใบที่ค้าง ─────────────────────────────────────────────────────────────────
def pending(now):
    """คืน (ใบที่ถึงเวลาเกรดแล้ว, ใบที่ยังไม่ถึงเวลา) จาก f5dump"""
    ok, txt = _call("f5dump", {})
    if not ok:
        raise RuntimeError(f"f5dump ไม่ผ่าน: {txt[:150]!r}")
    ready, early = [], []
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue          # บรรทัดพังข้ามไป ดีกว่าพาทั้งงานล้ม
        if r.get("res") is not None or r.get("FT"):
            continue          # มีผลแล้ว
        if not str(r.get("id") or "").strip():
            continue          # ไม่มี match_id = จับคู่กับฟีดไม่ได้
        day = str(r.get("at") or "")[:10]
        try:
            dd = datetime.fromisoformat(day).replace(tzinfo=BKK)
        except Exception:
            continue
        r["_day"] = day
        r["_hh"] = int(str(r.get("at"))[11:13] or 99)
        (ready if now >= dd + timedelta(days=1, hours=GRADE_AFTER_H) else early).append(r)
    return ready, early


def days_needed(rows):
    """วันที่ต้องดึงฟีด · ใบที่เตือนก่อน 06:00 นับวันก่อนหน้าด้วย

    ทำไม: ฟีด Forebet แบ่งวันตามเวลาไทย (tz=+420) แต่ใบเตือนออกกลางเกม
    คู่ที่เตะ 23:30 จะถูกเตือนตอน 00:1x ของ "วันใหม่" ทั้งที่คู่นั้นอยู่หน้าวันเก่า
    """
    need = set()
    for r in rows:
        need.add(r["_day"])
        if r["_hh"] < 6:
            d = datetime.fromisoformat(r["_day"]) - timedelta(days=1)
            need.add(d.strftime("%Y-%m-%d"))
    return sorted(need)


def scores(days):
    """คืน {match_id: "H-G"} เฉพาะคู่ที่จบแล้ว (comment == FT)"""
    from forebet_api import fb_fetch_day, fb_session
    route = fb_session()
    out = {}
    for day in days:
        try:
            fb, _lg = fb_fetch_day(day, markets=["1x2"], sess=route)
        except Exception as e:
            print(f"⚠️ ดึง {day} ไม่ได้ ({type(e).__name__}: {e}) → ไว้รอบหน้า", flush=True)
            continue
        n = 0
        for mid, m in fb.items():
            if str(m.get("comment") or "").strip() != "FT":
                continue
            hs, gs = m.get("Host_SC"), m.get("Guest_SC")
            if hs in (None, "") or gs in (None, ""):
                continue
            out[str(mid)] = f"{int(hs)}-{int(gs)}"
            n += 1
        print(f"📅 {day}: คู่ที่จบแล้ว {n} คู่ (สะสม {len(out)})", flush=True)
    return out


def main():
    days_cap = MAX_DAYS
    dry = "--dry" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            days_cap = int(sys.argv[i + 1])

    now = datetime.now(BKK)
    ready, early = pending(now)
    if not ready:
        print(f"✅ ไม่มีใบค้างที่ถึงเวลาเกรด (รอเวลาอยู่ {len(early)} ใบ)")
        return 0

    want = days_needed(ready)
    use = want[:days_cap]
    print(f"🧾 ใบค้างถึงเวลาเกรด {len(ready)} ใบ · ต้องดึง {len(want)} วัน "
          f"→ รอบนี้ {len(use)} วัน {use}", flush=True)

    sc = scores(use)
    items, nofind = [], 0
    for r in ready:
        ft = sc.get(str(r["id"]).strip())
        if ft:
            items.append({"id": str(r["id"]).strip(), "FT": ft})
        else:
            nofind += 1
    # ส่ง id ซ้ำไม่ได้ประโยชน์ (GAS ไล่ทุกแถวของ id นั้นอยู่แล้ว) — ตัดซ้ำก่อนส่ง
    seen, uniq = set(), []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        uniq.append(it)

    print(f"🎯 หาสกอร์จบเจอ {len(items)} ใบ ({len(uniq)} คู่) · ยังไม่เจอ {nofind} ใบ", flush=True)
    if not uniq:
        return 0
    if dry:
        print("🧪 --dry: ไม่ส่งเข้าชีต")
        print(json.dumps(uniq[:5], ensure_ascii=False))
        return 0

    for i in range(0, len(uniq), BATCH):
        chunk = uniq[i:i + BATCH]
        ok, txt = _call("f5grade", {
            "data": json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))})
        print(("✅ " if ok else "❌ ") + txt.strip()[:300], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

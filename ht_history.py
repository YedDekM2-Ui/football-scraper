# -*- coding: utf-8 -*-
"""ht_history.py — สร้าง/อัปเดตสถิติ "ครึ่งแรก → เต็มเวลา" (ht_flip.json)

ทำไมต้องมี:
  ฟีด getrs.php ให้ทั้ง Host_SC/Guest_SC (จบ) และ Host_SC_HT/Guest_SC_HT (ครึ่งแรก) ของ "ทุกวันย้อนหลัง"
  → ขอวันเก่าๆ ได้ไม่จำกัด (วัดแล้ว 2025-10-26 ยังคืน 1,081 แถว) = มีคลังผลจริงฟรีอยู่แล้ว
  เอามานับว่า "ทีมที่นำครึ่งแรก จบเกมยังชนะอยู่ไหม" แยกรายลีก → รู้ว่าลีกไหนพลิกบ่อย
  main.py เอาไปใส่ prompt ให้ Gemini ใช้ตอนเลือกทิปครึ่งแรก/ตอนดูคู่ที่กำลังนำอยู่

รันที่ไหน:
  ⚠️ รันจาก "เครื่องบ้าน" เท่านั้น (IP บ้านยิง Forebet ตรงได้ ~1 วิ/วัน)
  IP ของ GitHub Actions โดน Cloudflare 403 ถ้าจะรันบน Actions ต้องอ้อม ?ff= ซึ่งช้ามาก (15-25 วิ/ก้อน)
  → เลยเก็บผลเป็นไฟล์ ht_flip.json commit ขึ้น repo แล้ว main.py แค่อ่านไฟล์ (ไม่ต้องยิงเน็ตเพิ่มเลย)

usage:
  python ht_history.py 180        # ย้อนหลัง 180 วัน (ข้ามวันที่เก็บไว้แล้ว)
  python ht_history.py 7          # อัปเดตรายสัปดาห์
"""
import json
import os
import sys
import time
from datetime import date, timedelta

import forebet_api as fbapi

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ht_flip.json")

# ตัวนับต่อลีก:
#   n   = แมตช์ที่จบและมีสกอร์ครึ่งแรกครบ
#   lead= ครึ่งแรกมีคนนำ · lw/ld/ll = ตัวที่นำ จบเกม ชนะ/เสมอ/แพ้
#   dr  = ครึ่งแรกเสมอ · drd = ครึ่งแรกเสมอแล้วจบเกมมีผลแพ้ชนะ
#   hg/fg = ลูกครึ่งแรก/ลูกทั้งเกม (เอาไว้ดูว่าลีกไหนลูกมากระจุกครึ่งหลัง)
KEYS = ("n", "lead", "lw", "ld", "ll", "dr", "drd", "hg", "fg")


def _blank():
    return {k: 0 for k in KEYS}


def _load():
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("dates", [])
            d.setdefault("leagues", {})
            d.setdefault("all", _blank())
            return d
        except Exception as e:
            print(f"⚠️ อ่าน {OUT} เดิมไม่ได้ ({e}) → เริ่มใหม่")
    return {"updated": "", "dates": [], "leagues": {}, "all": _blank()}


def _int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def soak_day(db, rows, leagues):
    """นับผลของวันหนึ่งเข้า db · คืนจำนวนแมตช์ที่นับได้"""
    used = 0
    for r in rows:
        hs, gs = _int(r.get("Host_SC")), _int(r.get("Guest_SC"))
        hh, gh = _int(r.get("Host_SC_HT")), _int(r.get("Guest_SC_HT"))
        if None in (hs, gs, hh, gh):
            continue
        # ตัดเกมที่ยังไม่จบ/ถูกยกเลิก — เอาเฉพาะสถานะจบจริง
        st = str(r.get("comment") or "").strip()
        if st not in ("FT", "AET", "Pen.", "AP", "90"):
            continue
        lid = str(r.get("league_id") or "0")
        lg = db["leagues"].setdefault(lid, _blank())
        if "name" not in lg:
            meta = leagues.get(lid, [])
            lg["name"] = (meta[1] if len(meta) > 1 else (r.get("short_tag") or "")) or "?"
            lg["cc"] = (meta[5] if len(meta) > 5 else (r.get("code") or "")) or ""
        for tgt in (lg, db["all"]):
            tgt["n"] += 1
            tgt["hg"] += hh + gh
            tgt["fg"] += hs + gs
            if hh != gh:
                tgt["lead"] += 1
                lead_home = hh > gh
                if hs == gs:
                    tgt["ld"] += 1
                elif (hs > gs) == lead_home:
                    tgt["lw"] += 1
                else:
                    tgt["ll"] += 1
            else:
                tgt["dr"] += 1
                if hs != gs:
                    tgt["drd"] += 1
        used += 1
    return used


def main():
    back = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    db = _load()
    done = set(db["dates"])
    route = fbapi.fb_session()
    today = date.today()
    added = 0
    for i in range(1, back + 1):          # เริ่มจากเมื่อวาน (วันนี้ยังแข่งไม่จบ)
        d = (today - timedelta(days=i)).isoformat()
        if d in done:
            continue
        try:
            rows, lg = fbapi.fb_feed(route, "1x2", d)
        except Exception as e:
            print(f"⚠️ {d}: {e}")
            continue
        used = soak_day(db, rows, lg)
        db["dates"].append(d)
        added += 1
        if added % 10 == 0 or used == 0:
            print(f"  {d} · {used}/{len(rows)} แมตช์ (สะสม {db['all']['n']:,})")
        time.sleep(0.6)

    db["dates"] = sorted(set(db["dates"]))
    db["updated"] = today.isoformat()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
    a = db["all"]
    ld = a["lead"] or 1
    print(f"\n💾 {OUT} · {len(db['dates'])} วัน · {a['n']:,} แมตช์ · {len(db['leagues'])} ลีก")
    print(f"   นำครึ่งแรก {a['lead']:,} คู่ → ชนะ {a['lw']/ld*100:.1f}% · โดนตีเสมอ {a['ld']/ld*100:.1f}% · "
          f"แพ้พลิก {a['ll']/ld*100:.1f}%")
    dr = a["dr"] or 1
    print(f"   เสมอครึ่งแรก {a['dr']:,} คู่ → จบมีผลแพ้ชนะ {a['drd']/dr*100:.1f}%")
    print(f"   ลูกครึ่งแรก {a['hg']/(a['n'] or 1):.2f} · ทั้งเกม {a['fg']/(a['n'] or 1):.2f} "
          f"(ครึ่งหลัง {(a['fg']-a['hg'])/(a['n'] or 1):.2f})")


if __name__ == "__main__":
    main()

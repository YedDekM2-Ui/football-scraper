# -*- coding: utf-8 -*-
"""
forebet_api.py — ดึงข้อมูล Forebet จาก "JSON API หลังปุ่ม More" แทนการอ่าน markdown ผ่าน Jina

⭐ ที่มา: ปุ่ม More ท้ายตารางคือ  <span onclick='ltodrows("1x2","2026-07-26","","0","+420",...)'>More</span>
   ฟังก์ชัน ltodrows อยู่ใน https://www.forebet.com/includes/js/all.js — มันยิงไปที่
     https://www.forebet.com/scripts/getrs.php?ln=en&tp=<ตลาด>&in=<YYYY-MM-DD>&ord=0&tz=%2B420&tzs=..&tze=..
   คืน JSON = [ [แถวแมตช์...], {league_id: [ประเทศ, ชื่อลีก, path, .., .., cc, ..]} ]

ทำไมถึงเปลี่ยนมาใช้:
   Jina อ่านได้แค่ "หน้าแรก" ของตาราง = 42 คู่/ตลาด และหน้า predictions-1x2 ตัดลิงก์+ชื่อทีมทิ้ง
   API ให้ 467 คู่/ตลาด พร้อมชื่อทีม + id + สกอร์ครึ่งแรก/เต็ม + ฟอร์ม + อันดับ + เรท ครบในก้อนเดียว
   และ id ตรงกันเป๊ะทุกตลาด (ตรวจแล้ว 467/467) → join ข้ามตลาดไม่ต้องเดา

⚠️ Cloudflare: ยิง getrs.php ตรงๆ = 403 "Just a moment..."
   ต้องเปิดหน้า HTML ปกติก่อน 1 ครั้งเพื่อรับคุกกี้ แล้วค่อยยิง API ด้วย session เดิม + Referer
   และห้ามใส่ X-Requested-With (ใส่แล้วโดน 403)
"""

import time
import requests

FB_BASE = "https://www.forebet.com"
FB_HOME = FB_BASE + "/en/football-tips-and-predictions-for-today"
FB_API = FB_BASE + "/scripts/getrs.php"
FB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ตลาดที่ต้องดึง (tp code ของ Forebet)
#   1x2  = หน้าแพ้ชนะเสมอ (ก้อนหลัก — มีฟอร์ม/อันดับ/เรท/เทรนด์ ครบสุด)
#   uo   = สูง/ต่ำ · bts = ทั้งคู่ยิง · ah = อาเซียนแฮนดิแคป · ht = ครึ่งแรก · dbc = ดับเบิลชานซ์
#   ❌ htft ไม่ต้องดึง — คีย์เหมือน ht ทุกตัว (ตรวจแล้ว) ดึงซ้ำเปลืองเปล่าๆ
FB_MARKETS = ["1x2", "uo", "bts", "ah", "ht", "dbc"]


def fb_session():
    """เปิด session + เก็บคุกกี้ Cloudflare จากหน้า HTML ปกติก่อน"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": FB_UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    r = s.get(FB_HOME, timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f"เปิดหน้า Forebet ไม่ได้ (HTTP {r.status_code})")
    return s


def fb_feed(sess, tp, day):
    """ดึง 1 ตลาด · คืน (rows, leagues)"""
    params = {
        "ln": "en", "tp": tp, "in": day, "ord": "0",
        "tz": "+420",     # โซนเวลาไทย (ใช้แค่คัดขอบวัน — DATE_BAH ยังเป็นเวลายุโรปเสมอ)
        "tzs": "0", "tze": "0",
    }
    r = sess.get(FB_API, params=params, timeout=90, headers={
        "Accept": "*/*",
        "Referer": FB_HOME,
        # 🚫 ห้ามใส่ X-Requested-With — Cloudflare เด้ง 403
    })
    if r.status_code != 200:
        raise RuntimeError(f"tp={tp} HTTP {r.status_code}")
    j = r.json()
    rows = j[0] if isinstance(j, list) and j and isinstance(j[0], list) else []
    leagues = j[1] if isinstance(j, list) and len(j) > 1 and isinstance(j[1], dict) else {}
    return rows, leagues


def fb_fetch_day(day, markets=None, sess=None, pause=1.0):
    """ดึงทุกตลาดของวันหนึ่ง แล้ว merge ตาม id

    คืน (matches, leagues)
      matches = {id: {..ฟิลด์รวมทุกตลาด.., '_mkt': set(ตลาดที่เจอ)}}
      leagues = {league_id: [ประเทศ, ชื่อลีก, path, .., .., cc, ..]}
    """
    markets = markets or FB_MARKETS
    sess = sess or fb_session()
    matches, leagues, okmk = {}, {}, []
    for i, tp in enumerate(markets):
        try:
            rows, lg = fb_feed(sess, tp, day)
        except Exception as e:
            print(f"⚠️ Forebet API tp={tp}: {e}")
            continue
        if lg:
            leagues.update(lg)
        okmk.append(f"{tp}:{len(rows)}")
        for r in rows:
            mid = str(r.get("id") or "")
            if not mid:
                continue
            d = matches.setdefault(mid, {"_mkt": set()})
            d["_mkt"].add(tp)
            for k, v in r.items():
                # ก้อนแรกชนะเสมอ (1x2 ครบสุด) — ตลาดหลังเติมเฉพาะฟิลด์ที่ยังไม่มี/ยังว่าง
                if k not in d or d[k] in (None, "", []):
                    d[k] = v
        if i < len(markets) - 1:
            time.sleep(pause)
    print(f"📡 Forebet API {day} → {len(matches)} คู่ ({' · '.join(okmk) or 'ไม่ได้เลย'})")
    return matches, leagues

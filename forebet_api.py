# -*- coding: utf-8 -*-
"""
forebet_api.py — ดึงข้อมูล Forebet จาก "JSON API หลังปุ่ม More" แทนการอ่าน markdown ผ่าน Jina

⭐ ที่มา: ปุ่ม More ท้ายตารางคือ  <span onclick='ltodrows("1x2","2026-07-26","","0","+420",...)'>More</span>
   ฟังก์ชัน ltodrows อยู่ใน https://www.forebet.com/includes/js/all.js — มันยิงไปที่
     https://www.forebet.com/scripts/getrs.php?ln=en&tp=<ตลาด>&in=<YYYY-MM-DD>&ord=0&tz=%2B420&tzs=..&tze=..
   คืน JSON = [ [แถวแมตช์...], {league_id: [ประเทศ, ชื่อลีก, path, .., .., cc, ..]} ]

ทำไมถึงเปลี่ยนมาใช้:
   Jina อ่านได้แค่ "หน้าแรก" ของตาราง = 42 คู่/ตลาด และหน้า predictions-1x2 ตัดลิงก์+ชื่อทีมทิ้ง
   API ให้ 374-467 คู่/ตลาด พร้อมชื่อทีม + id + สกอร์ครึ่งแรก/เต็ม + ฟอร์ม + อันดับ + เรท ครบในก้อนเดียว
   และ id ตรงกันเป๊ะทุกตลาด (ตรวจแล้ว 467/467) → join ข้ามตลาดไม่ต้องเดา

⚠️ Cloudflare — วัดจริงแล้ว 26 ก.ค. 2026 (cf_probe.py):
   · IP บ้าน (182.232.x) → ยิงตรงได้ 200 ไม่ต้องมีคุกกี้ด้วยซ้ำ · 374 คู่/ตลาด
   · IP GitHub Actions (Azure 20.168.119.244) → 403 "Just a moment" ทั้งหน้า HTML และ API
     = priming คุกกี้ไม่ช่วยเลย เพราะตายตั้งแต่ประตูแรก (ปัญหาชื่อเสียง IP ไม่ใช่ header)
   · proxy ฟรีสาธารณะ (codetabs / allorigins / corsproxy / thingproxy) ตายหมด — 5xx หรือขอเงิน
   · r.jina.ai ยิงตรงด้วย URL ที่มี query (&) → 403 · แต่อ้อม PIKTAX ?ff= ได้ครบ 1,268,772 bytes ✅
   → จึงมี 2 เส้นทาง: direct ก่อน ถ้าโดนเด้งค่อยอ้อม ?ff= (GAS ใช้ IP Google → Jina → Forebet)
     และห้ามใส่ X-Requested-With ในสาย direct (ใส่แล้ว 403 แม้จาก IP ที่ปกติผ่าน)
"""

import json
import os
import time
import urllib.parse

import requests

FB_BASE = "https://www.forebet.com"
FB_HOME = FB_BASE + "/en/football-tips-and-predictions-for-today"
FB_API = FB_BASE + "/scripts/getrs.php"
FB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# URL ของ PIKTAX (GAS) — ตัด query ทิ้งเหมือน main.py (secret อาจมี ?key=.. ต่อท้ายอยู่)
FF_BASE = (os.environ.get("PIKTAX_STATE_URL", "") or "").split("?")[0]

# ตลาดที่ต้องดึง (tp code ของ Forebet)
#   1x2  = หน้าแพ้ชนะเสมอ (ก้อนหลัก — มีฟอร์ม/อันดับ/เรท/เทรนด์ ครบสุด)
#   uo   = สูง/ต่ำ · bts = ทั้งคู่ยิง · ah = อาเซียนแฮนดิแคป · ht = ครึ่งแรก · dbc = ดับเบิลชานซ์
#   ❌ htft ไม่ต้องดึง — คีย์เหมือน ht ทุกตัว (ตรวจแล้ว) ดึงซ้ำเปลืองเปล่าๆ
FB_MARKETS = ["1x2", "uo", "bts", "ah", "ht", "dbc"]


class FbRoute:
    """เส้นทางที่ใช้ดึงจริงในรอบนี้ · mode = 'direct' (ยิงเอง) หรือ 'ff' (อ้อม PIKTAX → Jina)"""

    def __init__(self, mode, sess=None):
        self.mode = mode
        self.sess = sess

    def __repr__(self):
        return f"<FbRoute {self.mode}>"


def _api_url(tp, day):
    return FB_API + "?" + urllib.parse.urlencode({
        "ln": "en", "tp": tp, "in": day, "ord": "0",
        "tz": "+420",     # โซนเวลาไทย (ใช้แค่คัดขอบวัน — DATE_BAH ยังเป็นเวลายุโรปเสมอ)
        "tzs": "0", "tze": "0",
    })


def fb_session():
    """เลือกเส้นทาง: ลองยิงตรงก่อน ถ้า Cloudflare เด้งค่อยถอยไป ?ff="""
    try:
        s = requests.Session()
        s.headers.update({
            "User-Agent": FB_UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        r = s.get(FB_HOME, timeout=45)
        if r.status_code == 200:
            return FbRoute("direct", s)
        err = f"HTTP {r.status_code}"
    except Exception as e:
        err = str(e)

    if FF_BASE:
        print(f"↩️ ยิง Forebet ตรงไม่ได้ ({err}) → อ้อม PIKTAX ?ff= (GAS → Jina)")
        return FbRoute("ff")
    raise RuntimeError(f"เปิดหน้า Forebet ไม่ได้ ({err}) และไม่มี PIKTAX_STATE_URL ให้อ้อม")


def _ff_fetch(url, timeout=240, tries=3):
    """ดึงผ่าน PIKTAX ?ff= — GAS ใช้ IP ของ Google ยิงเข้า Jina แล้ว Jina ยิง Forebet
    ช้ากว่าสาย direct (10-30 วิ/ก้อน) แต่ทะลุ Cloudflare ได้

    ⚠️ วัดจาก Actions 26 ก.ค.: Jina สะดุดเป็นก้อนได้ — tp=uo คืน 200 แต่ตัวเปล่า 8 bytes
       retry รอบเดียวไม่พอ → 3 รอบ ถอยยาวขึ้นเรื่อยๆ (Jina ต้องมีเวลาเคลียร์)"""
    backoff = [6, 15, 25]
    last = ""
    u = FF_BASE + "?ff=" + urllib.parse.quote(url, safe="")
    for i in range(tries):
        try:
            r = requests.get(u, timeout=timeout)
            if r.status_code == 200 and len(r.content) > 500:
                return r.text
            # เก็บเนื้อสั้นๆ ไว้ด้วย — เวลาพังจะได้รู้ว่า GAS ว่า อะไร ไม่ใช่เห็นแค่ขนาด
            last = f"HTTP {r.status_code} · {len(r.content)} bytes · {r.text[:60]!r}"
        except Exception as e:
            last = str(e)[:120]
        if i < tries - 1:
            time.sleep(backoff[min(i, len(backoff) - 1)])
    raise RuntimeError(f"?ff= ไม่ได้ผล {tries} รอบ ({last})")


def _json_from_jina(txt):
    """Jina คืน markdown มีหัว 'Title:/URL Source:/Markdown Content:' แล้วต่อด้วย JSON ดิบ
    (วัดแล้ว 1,268,772 bytes vs ยิงตรง 1,268,631 = หัวเกินมา 141 bytes เนื้อไม่ถูกตัด)"""
    i = txt.find("[[")
    if i < 0:
        i = txt.find("[")
    if i < 0:
        raise RuntimeError("ไม่เจอ JSON ในผลลัพธ์ (หัว: " + txt[:120].replace("\n", " ") + ")")
    j = txt.rfind("]")
    body = txt[i:j + 1] if j > i else txt[i:]
    try:
        return json.loads(body)
    except Exception as e:
        raise RuntimeError(f"parse JSON ไม่ผ่าน ({e}) · ท้ายก้อน: {body[-120:]!r}")


def fb_feed(route, tp, day):
    """ดึง 1 ตลาด · คืน (rows, leagues) — รับได้ทั้ง FbRoute และ requests.Session แบบเดิม"""
    if isinstance(route, FbRoute) and route.mode == "ff":
        j = _json_from_jina(_ff_fetch(_api_url(tp, day)))
    else:
        sess = route.sess if isinstance(route, FbRoute) else route
        r = sess.get(FB_API, params={
            "ln": "en", "tp": tp, "in": day, "ord": "0",
            "tz": "+420", "tzs": "0", "tze": "0",
        }, timeout=90, headers={
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


def fb_fetch_day(day, markets=None, sess=None, pause=None):
    """ดึงทุกตลาดของวันหนึ่ง แล้ว merge ตาม id

    คืน (matches, leagues)
      matches = {id: {..ฟิลด์รวมทุกตลาด.., '_mkt': set(ตลาดที่เจอ)}}
      leagues = {league_id: [ประเทศ, ชื่อลีก, path, .., .., cc, ..]}
    """
    markets = markets or FB_MARKETS
    route = sess or fb_session()
    mode = route.mode if isinstance(route, FbRoute) else "direct"
    if pause is None:
        pause = 2.5 if mode == "ff" else 1.0   # สาย ff ผ่าน Jina → เว้นจังหวะกันโดนลิมิต
    matches, leagues, okmk = {}, {}, []

    def soak(tp, rows, lg):
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

    failed = []
    for i, tp in enumerate(markets):
        try:
            rows, lg = fb_feed(route, tp, day)
        except Exception as e:
            print(f"⚠️ Forebet API tp={tp}: {e}")
            failed.append(tp)
        else:
            soak(tp, rows, lg)
        if i < len(markets) - 1:
            time.sleep(pause)

    # รอบเก็บตก — ตลาดที่ล่มค่อยวนซ้ำท้ายสุด (ตอนนั้น Jina มักหายสะดุดแล้ว)
    # ยอมช้าเพิ่ม 30 วิ ดีกว่าเสียทั้งตลาด เช่น uo หาย = ทิปสูง/ต่ำหายทั้งวัน
    if failed:
        print(f"🔁 วนเก็บตกตลาดที่ล่ม: {', '.join(failed)}")
        time.sleep(pause * 2)
        for tp in failed:
            try:
                rows, lg = fb_feed(route, tp, day)
            except Exception as e:
                print(f"❌ tp={tp} เก็บตกไม่ได้: {e}")
            else:
                soak(tp, rows, lg)
                time.sleep(pause)

    print(f"📡 Forebet API [{mode}] {day} → {len(matches)} คู่ ({' · '.join(okmk) or 'ไม่ได้เลย'})")
    return matches, leagues

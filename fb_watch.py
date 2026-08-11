# -*- coding: utf-8 -*-
"""fb_watch.py — วงจรเตือน FABEL5: "Forebet ติดหนี้ประตู + ลีกที่เคยใช้หนี้จริง"

สมองประโยคเดียว (ของผู้ใช้):
  "Forebet ประกาศไว้แล้วยังไม่เกิด แล้วเวลาใกล้หมด — เตือนเฉพาะลีกที่มันเคยใช้หนี้จริง"
ตัวนี้ไม่ใช่ตัวทาย เป็น "ตัวชี้จุด" ว่าตอนนี้ไปเปิดดูคู่ไหน

⚖️ กติกาเหล็ก: ห้ามมีเลขที่ไม่เคยวัด
  ทุกเลขในข้อความเตือนมาจาก fb_trust.json (วัดจริงจาก fb_hist.jsonl 132,851 คู่ · 365 วัน)
  และของที่ยังวัดไม่ได้ ต้องบอกตรงๆ ว่าวัดไม่ได้:
    ประวัติมีแค่สกอร์ "ครึ่งแรก + จบ" ไม่มีรายนาที
    → "นาที 89 ยิงทัน" ยังพิสูจน์ไม่ได้ ตัวนี้จึงเตือน "ตอนพักครึ่ง" ซึ่งเป็นจังหวะที่วัดมาแล้วจริงๆ
    → ระหว่างเฝ้า จะจดสกอร์รายนาทีลง fb_live_log.jsonl ไปเรื่อยๆ อีกสักพักถึงจะวัดเรื่องนาทีได้

🔗 แหล่งข้อมูล 2 ทาง (แยกหน้าที่กันชัดๆ)
  Forebet   → รายชื่อคู่ + ตัวทำนาย (Pred_1/2, pr_over, Pred_gg, goalsavg) + league_id
  LiveScore → นาทีปัจจุบัน + สกอร์ครึ่งแรก + สกอร์สด (Forebet ไม่ให้ระหว่างเกมเลย · ดู live_api.py)
  ต่อกันด้วย live_api.join แบบ fail-closed — จับคู่ไม่มั่นใจ = ไม่เตือน

☁️ รันบนคลาวด์ได้ แต่ต้องแยกเป็น 2 งาน — IP GitHub Actions โดน Cloudflare ของ Forebet เด้ง 403
   (LiveScore ผ่านฉลุย) → งานแรกวันละครั้งดึง Forebet อ้อม ?ff= เก็บแคช · งานที่สองอ่านแคช

usage:
  python fb_watch.py                 # เฝ้ายาว เช็คทุก 3 นาที
  python fb_watch.py --once          # เช็ครอบเดียวแล้วออก (ไว้เทสต์)
  python fb_watch.py --dry           # ไม่ส่ง Telegram แค่พิมพ์
  python fb_watch.py --every 5 --hours 8
  python fb_watch.py --build-cache   # (คลาวด์) ดึง Forebet ของวัน เก็บลง fb_day_cache.json.gz
  FB_CACHE=read FB_NO_LIVELOG=1 python fb_watch.py --once   # (คลาวด์) รอบเดียวจากแคช
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta

import requests

import forebet_api as fbapi
import live_api

HERE = os.path.dirname(os.path.abspath(__file__))
TRUST = os.path.join(HERE, "fb_trust.json")
SEEN = os.path.join(HERE, "fb_watch_seen.json")
LIVELOG = os.path.join(HERE, "fb_live_log.jsonl")     # คลังสกอร์รายนาที (ไว้วัด "นาที 89" ในอนาคต)
ALERTLOG = os.path.join(HERE, "fb_alert_log.jsonl")   # ทุกใบที่เตือนไป + เลขที่โชว์ (ไว้เช็คย้อนว่า % จริงเท่าไหร่)
CACHE = os.path.join(HERE, "fb_day_cache.json.gz")    # รายการคู่ Forebet ของวัน (ใช้ตอนรันบนคลาวด์)

# ── โหมดคลาวด์ (GitHub Actions) ───────────────────────────────────────────────
# ทำไมต้องมี: IP ของ Actions โดน Cloudflare ของ Forebet เด้ง 403 (วัดแล้ว) แต่ LiveScore ผ่านฉลุย
#   → แยกเป็น 2 งาน: งานแรกวันละครั้ง ดึง Forebet อ้อม ?ff= เก็บใส่แคช
#                    งานที่สองทุก 5 นาที อ่านแคช + ยิง LiveScore เอง (ไม่แตะ Forebet เลย)
# FB_CACHE=read  → live_now() อ่านจากแคชแทนการดึง Forebet
# FB_NO_LIVELOG=1 → ไม่จด fb_live_log.jsonl (บนคลาวด์มันโตวันละ ~1.5MB ทำ repo บวม)
CACHE_MODE = os.environ.get("FB_CACHE", "").strip().lower()
NO_LIVELOG = os.environ.get("FB_NO_LIVELOG", "").strip() == "1"

# ── ทางส่ง Telegram ────────────────────────────────────────────────────────────
# ไม่เก็บโทเคนบอทไว้ที่เครื่อง — ยิงผ่าน PIKTAX (?admin=..&action=notify) ซึ่งถือโทเคนอยู่แล้ว
DEPLOY_FILE = r"D:\Projects\t.seeedz\PIKTAX\.deployId"
KEY_FILE = r"D:\Projects\.gas-creds\piktax-admin-key.txt"

# ── หน้าต่างเวลาที่ยอมเตือน ───────────────────────────────────────────────────
# ตัวเลขที่วัดไว้ทั้งหมดคือ "จากตอนพักครึ่ง → จบเกม" → เตือนช้ากว่านี้ = เลขที่โชว์จะเกินจริง
#
# ทำไมแคบแค่ 45-47 (หดจาก 58 เมื่อ 11 ส.ค. 69):
#   เจ้าของบอกเอง "ราคา Over ยังสูงเกินเอื้อม เข้าไม่ทันอยู่ดี" — พอเปิดครึ่งหลังราคาหาย
#   วัดจริง 16 ใบ: ยิงตอนนาที 45 ไป 15 ใบ / นาที 49 ใบเดียว → หน้าต่างลงจริงคือ "ช่วงพักครึ่ง"
#   ที่หดแล้วไม่เจ็บ เพราะ LiveScore รายงานนาที 45 ค้างไว้ตลอดช่วงพักครึ่ง (~15 นาที)
#   ซึ่งยาวกว่ารอบเฝ้า 5 นาทีเกือบ 3 เท่า → รอบไหนก็เก็บทัน · ค่าเสีย ~1 ใบใน 23
MIN_FROM, MIN_TO = 45, 47

# ── เกณฑ์ % ของ Forebet · ต้องตรงกับ fb_calib.py เป๊ะ ไม่งั้นเลขบนใบเตือนโกหก ────
FAV_TH = 70      # ตัวเต็ง (lv_nolose / bh_g1)
GG_TH = 65       # ทายยิงกันทั้งคู่ (bts1_g1)
OVER_TH = 65     # ทายสูง (over_g1)

# ── กฎ: ชื่อตลาดต้องตรงกับที่ fb_calib.py เขียนลง fb_trust.json ───────────────
# ⚠️ ทุกเกณฑ์วัดเป็น "ลูกที่ยังไม่เกิด" — ใบเตือนออกตอนสกอร์ยังเท่าพักครึ่งเป๊ะ
#    เกณฑ์แบบ "จบ ≥2 ลูก" ตอนพัก 2-0 = ชนะฟรี (ของเดิมพลาดตรงนี้ทั้งชุด)
# เส้นฐานเปล่า 131,011 คู่: ครึ่งหลังมีลูก ≥1 = 78.4% ← ทุกตลาดหน้าลูกต้องชนะเลขนี้
#    ที่ฆ่าทิ้ง: ht00_2h_goal 74.6% (ต่ำกว่าเส้นฐาน) · debt_over 42.6%
#               debt_btts 52.9% · debt_fav 47.8%
#
# gate = "league" → กรองรายลีก (need_lift = ต้องดีกว่าเส้นฐานทุกลีกกี่จุด)
# gate = "global" → ไม่กรองลีก ใช้เลขรวมทุกลีก
#    ทำไม: 2 ตัวนี้ตัวอย่างรายลีกบางเกิน (lv_nolose 2 ลีกผ่าน · bh_g1 0 ลีก)
#    และรวมระดับประเทศก็ไม่ช่วย — ความต่างระหว่างประเทศ 86.1–92.0% ขณะที่
#    ค่าคลาดเคลื่อนปกติ ±3.9 = อยู่ในช่วงมั่วล้วนๆ แปลว่าเป็นเรื่องโครงสร้างเกม
#    ไม่ใช่เรื่องลีก → กรองลีกไปก็เป็นความแม่นยำปลอม ใช้เกณฑ์ % แทนเป็นปุ่มลดใบ
RULES = {
    # ⚠️ คำในป้ายต้องพูดตรงกับที่เกรดจริงเป๊ะ ห้ามเติมคำที่วัดไม่ได้ต่อท้าย
    #    เจ้าของสั่งใส่ "(เต็งไม่แพ้หรืออาจจะแซงได้)" ใน bh_g1 → ใส่ให้ตามสั่ง
    #    แต่คำนั้นวัดได้ 54.1% (n=344) ไม่ใช่ 86.1% ที่โชว์ข้างหลัง
    #    → ต้องมี "note" กำกับเลขของมันเองเสมอ ห้ามปล่อยให้เลขเดียวขาย 2 ตลาด
    "lv_nolose": {"label": "พักครึ่งยังเสมอ แต่เขาทายเต็งไว้แรง → เต็งไม่น่าแพ้",
                  "gate": "global"},                                  # 89.8% · n=1,113
    "bts1_g1":   {"label": "ครึ่งแรกยิงอยู่ฝั่งเดียว แต่เขาทายว่ายิงกันทั้งคู่ "
                           "→ อีกฝั่งอาจยิงคืนได้อย่างน้อย 1 ลูก",
                  "gate": "league", "need_lift": 5.0, "minn": 40},    # ทุกลีก 84.0% · 30 ลีกผ่าน
    "bh_g1":     {"label": "เต็งตามอยู่ครึ่งแรก → ครึ่งหลังยังมีลูกมาอีก "
                           "(เต็งไม่แพ้หรืออาจจะแซงได้)",
                  "note": "ท่อนในวงเล็บมีเลขของมันเอง — เต็งไม่แพ้ตอนจบ 54.1% "
                          "(ตามมาเสมอ 25.0% + พลิกชนะ 29.1% · 344 คู่)",
                  "gate": "global"},                                  # 86.1% · n=345
    "over_g1":   {"label": "ครึ่งแรกบอลต่ำ แต่เขาทายว่าเกมนี้จะยิงได้ครึ่งหลัง "
                           "→ มีอีกอย่างน้อย 1 ลูก",
                  "gate": "league", "need_lift": 5.0, "minn": 40},    # ทุกลีก 82.7% · 25 ลีกผ่าน
}


def _f(v):
    try:
        return float(str(v).strip())
    except Exception:
        return None


def _i(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


# ── เส้นฐาน + ตัวกรองรายลีก ───────────────────────────────────────────────────
def load_trust():
    """คืน (trust, baseline, gall)

    baseline = เส้นฐานทุกลีกของแต่ละตลาด (ถ่วงน้ำหนักด้วย n · เฉพาะลีกที่ผ่าน minn ของ calib)
    gall     = เลขรวมทุกลีกจริงๆ ทุกคู่ (คีย์ `_all`) ใช้กับตลาด gate=global
    ⚠️ คีย์ที่ขึ้นต้นด้วย _ ไม่ใช่ลีก ต้องข้ามตอนคิดเส้นฐาน ไม่งั้นนับซ้ำทั้งกอง
    """
    trust = json.load(open(TRUST, encoding="utf-8"))
    gall = (trust.get("_all") or {}).get("mkt") or {}
    tot = {}
    for lg, d in trust.items():
        if lg.startswith("_"):
            continue
        for mkt, v in (d.get("mkt") or {}).items():
            a = tot.setdefault(mkt, [0, 0.0])
            a[0] += v["n"]
            a[1] += v["n"] * v["hit"] / 100.0
    base = {k: (v[1] / v[0] * 100 if v[0] else 0.0) for k, v in tot.items()}
    return trust, base, gall


def league_ok(trust, base, gall, lg, mkt):
    """ตลาดนี้ผ่านด่านไหม · คืน (ผ่าน, hit, n, เส้นฐาน)

    แยกรายตลาดเสมอ — ผู้ใช้ย้ำเอง: "ยูฟ่าหญิง ยิงเยอะจริง แต่ 1X2 แพ้ราบ"
    = ลีกเดียวกันเก่งคนละเรื่อง ให้คะแนนรวมทั้งลีกไม่ได้
    ตลาด gate=global ไม่กรองลีก (เหตุผลอยู่ที่ RULES) แต่ยังต้องมีเลขที่วัดมาจริง
    """
    r = RULES[mkt]
    if r["gate"] == "global":
        d = gall.get(mkt)
        if not d:
            return False, None, 0, 0.0      # ไม่มีเลขวัด = ไม่เตือน (fail-closed)
        return True, d["hit"], d["n"], d["hit"]
    b = base.get(mkt, 0.0)
    d = ((trust.get(str(lg)) or {}).get("mkt") or {}).get(mkt)
    if not d or d["n"] < r["minn"]:
        return False, None, (d or {}).get("n", 0), b
    return d["hit"] >= b + r["need_lift"], d["hit"], d["n"], b


# ── หาคู่ที่กำลังเตะ ───────────────────────────────────────────────────────────
def fb_days():
    """วันที่ต้องมีในแคชเสมอ = เมื่อวาน + วันนี้ + พรุ่งนี้

    ทำไมต้องเผื่อพรุ่งนี้: งาน fb-cache รอบสุดท้ายของวันสร้างแคชตอน ~20:00 UTC
    พอผ่านเที่ยงคืน UTC (07:00 ไทย) "วันนี้" กลายเป็นวันใหม่ที่แคชไม่มี → cache_load
    ตีว่าแคชเก่า ปฏิเสธทั้งก้อน = ตาบอดยาวจนกว่ารอบเช้าจะมา (วัดจริง 10-11 ส.ค. 69
    GitHub รันสายจากคิว 1.5-2 ชม. ทุกรอบ → รูเงียบ 07:00-10:00 ไทยทุกเช้า)
    ใส่พรุ่งนี้ไว้ตั้งแต่แรก รอบดึกก็ครอบวันถัดไปอยู่แล้ว งานมาสายไม่เป็นไร
    """
    t = date.today()
    return [(t - timedelta(days=1)).isoformat(), t.isoformat(), (t + timedelta(days=1)).isoformat()]


def fetch_fb(route):
    """ดึงรายการคู่ Forebet ของเมื่อวาน+วันนี้+พรุ่งนี้ (3 ตลาดที่กฎใช้จริง)"""
    fb = {}
    for d in fb_days():
        try:
            ms, _ = fbapi.fb_fetch_day(d, markets=("1x2", "uo", "bts"), sess=route)
            fb.update(ms)
        except Exception as e:
            print(f"⚠️ Forebet {d}: {e}", flush=True)
    return fb


def cache_build():
    """งานวันละครั้ง: ดึง Forebet (อ้อม ?ff= เองถ้ายิงตรงไม่ได้) แล้วอัดลงแคช

    เก็บทั้งก้อนไม่ตัดฟิลด์ — gzip แล้วเล็กพอ และตัดฟิลด์ทีหลังคือความเสี่ยงที่จะลืมตัวใดตัวหนึ่ง
    """
    fb = fetch_fb(fbapi.fb_session())
    if not fb:
        print("❌ ดึง Forebet ไม่ได้เลย — ไม่เขียนทับแคชเดิม", flush=True)
        return 1
    # _mkt เป็น set() — JSON ไม่รับ ต้องแปลงเป็น list ก่อน
    slim = {k: {kk: (sorted(vv) if isinstance(vv, set) else vv) for kk, vv in v.items()}
            for k, v in fb.items()}
    blob = json.dumps({"built": datetime.now().isoformat(timespec="seconds"),
                       "days": fb_days(),
                       "fb": slim}, ensure_ascii=False).encode("utf-8")
    tmp = CACHE + ".tmp"
    with gzip.open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, CACHE)
    print(f"✅ แคช {len(fb)} คู่ → {os.path.basename(CACHE)} ({os.path.getsize(CACHE)/1024:.0f} KB)", flush=True)
    return 0


def cache_load():
    """อ่านแคช · คืน ({} , เหตุผล) ถ้าใช้ไม่ได้ — ยอมเงียบดีกว่าเตือนด้วยรายการของเมื่อวาน"""
    if not os.path.exists(CACHE):
        return {}, "ไม่มีแคช Forebet (งาน fb-cache ยังไม่เคยรันสำเร็จ)"
    try:
        with gzip.open(CACHE, "rb") as f:
            d = json.loads(f.read().decode("utf-8"))
    except Exception as e:
        return {}, f"แคชพัง: {e}"
    # แคชต้องครอบคลุมวันนี้ ไม่งั้นคู่ที่เตะอยู่จะไม่มีในรายการ
    if date.today().isoformat() not in (d.get("days") or []):
        return {}, f"แคชเก่าเกินไป (สร้างเมื่อ {d.get('built')})"
    fb = d.get("fb") or {}
    for v in fb.values():
        if isinstance(v.get("_mkt"), list):
            v["_mkt"] = set(v["_mkt"])
    return fb, ""


def live_now(route):
    """คืน (matches, note) — matches = {id: ฟิลด์ Forebet + สกอร์สดจาก LiveScore}

    ทำไมต้องพึ่งเว็บอื่น: Forebet ไม่ให้ข้อมูลระหว่างเกมเลย (วัดแล้ว 1,342 คู่ 2 วัน)
      tp=live ว่างเปล่าตลอด · comment ไม่มีเลขนาที · สกอร์ครึ่งแรกโผล่พร้อม FT เท่านั้น
    → รายชื่อคู่+ตัวทำนายยังเป็นของ Forebet เหมือนเดิม เอามาจาก LiveScore แค่ "นาที + สกอร์"

    ⚖️ จับคู่ไม่ได้ = ไม่เตือน (ดูด่านใน live_api.join) ยอมพลาดดีกว่าเตือนผิดคู่
    """
    # ดึง Forebet วันนี้+เมื่อวาน (คู่ดึกเวลายุโรปไปโผล่วันก่อนหน้า)
    # เอาแค่ 3 ตลาดที่กฎใช้จริง (1x2 → Pred_1/2+goalsavg · uo → pr_over · bts → Pred_gg)
    if CACHE_MODE == "read":
        fb, note = cache_load()
        if not fb:
            return {}, note
    else:
        fb = fetch_fb(route)
    if not fb:
        return {}, "ดึง Forebet ไม่ได้"

    joined, stat = live_api.join(fb)
    off = stat.pop("_offset_hr", None)
    stat.pop("_offset_from", None)

    got = {}
    for mid, g in joined.items():
        if live_api.parse_minute(g["eps"]) is not None:
            got[mid] = live_api.apply_live(fb[mid], g)
    return got, f"Forebet {len(fb)} → ต่อ LiveScore ติด {len(joined)} (เวลาต่าง {off:+g} ชม.)"


# ── กฎเตือน ───────────────────────────────────────────────────────────────────
def check_rules(m):
    """คืน list ของ (market, หัวข้อ, เหตุผลที่ยังเป็นหนี้อยู่)

    "หนี้" = Forebet ประกาศไว้ แต่ครึ่งแรกยังไม่เกิด → เงื่อนไขตรงกับที่ fb_calib วัดเป๊ะๆ
    """
    HH, GH = _i(m.get("Host_SC_HT")), _i(m.get("Guest_SC_HT"))
    HS, GS = _i(m.get("Host_SC")) or 0, _i(m.get("Guest_SC")) or 0
    if HH is None or GH is None:
        return []                     # ไม่รู้สกอร์ครึ่งแรก = เทียบกับของที่วัดไว้ไม่ได้
    if (HS, GS) != (HH, GH):
        return []                     # ครึ่งหลังมีลูกไปแล้ว = หนี้ถูกใช้ไปแล้ว ไม่ต้องเตือน

    p1, p2 = _f(m.get("Pred_1")), _f(m.get("Pred_2"))
    pro, pgg = _f(m.get("pr_over")), _f(m.get("Pred_gg"))
    htot = HH + GH
    out = []

    # 1) พักครึ่งเสมอ + Forebet ให้ฝั่งนั้น ≥70 → ฝั่งนั้นไม่แพ้
    if None not in (p1, p2) and HH == GH:
        if p1 >= FAV_TH:
            out.append(("lv_nolose", "บ้านไม่แพ้",
                        f"ทายเจ้าบ้านชนะ {p1:.0f}% · พักครึ่งยังเสมอ {HH}-{GH}"))
        elif p2 >= FAV_TH:
            out.append(("lv_nolose", "เยือนไม่แพ้",
                        f"ทายทีมเยือนชนะ {p2:.0f}% · พักครึ่งยังเสมอ {HH}-{GH}"))

    # 2) พักครึ่งยิงข้างเดียว + ทายยิงกันทั้งคู่ ≥65 → ครึ่งหลังมีอีก ≥1
    if pgg is not None and pgg >= GG_TH and (HH > 0) != (GH > 0):
        out.append(("bts1_g1", "ครึ่งหลังมีอีกลูก",
                    f"ทายยิงกันทั้งคู่ {pgg:.0f}% · พักครึ่งยิงฝั่งเดียว {HH}-{GH}"))

    # 3) ฝั่งที่ตามอยู่คือตัวเต็ง ≥70 → ครึ่งหลังมีอีก ≥1
    #    ⚠️ เกณฑ์คือ "มีอีกลูก" ไม่ใช่ "พลิกชนะ" — พลิกชนะจริงแค่ 24.9% (ของเดิมโม้ตรงนี้)
    if None not in (p1, p2) and HH != GH:
        if p1 >= FAV_TH and HH < GH:
            out.append(("bh_g1", "ครึ่งหลังมีอีกลูก",
                        f"ทายเจ้าบ้านชนะ {p1:.0f}% แต่พักครึ่งตามอยู่ {HH}-{GH}"))
        elif p2 >= FAV_TH and GH < HH:
            out.append(("bh_g1", "ครึ่งหลังมีอีกลูก",
                        f"ทายทีมเยือนชนะ {p2:.0f}% แต่พักครึ่งตามอยู่ {HH}-{GH}"))

    # 4) ทายสูง ≥65 แต่พักครึ่งได้ ≤1 ลูก → ครึ่งหลังมีอีก ≥1
    if pro is not None and pro >= OVER_TH and htot <= 1:
        out.append(("over_g1", "ครึ่งหลังมีอีกลูก",
                    f"ทายสูง {pro:.0f}% · พักครึ่งเพิ่ง {htot} ประตู"))
    return out


def _ko_th(m):
    """เวลาเตะเป็นเวลาไทย 'HH:MM ' — ไม่รู้เวลา = คืนค่าว่าง ไม่เดา"""
    ko = m.get("_ko")
    if not isinstance(ko, datetime):
        return ""
    return (ko + timedelta(hours=7)).strftime("%H:%M ")


def _red(m):
    """ป้ายใบแดงติดข้างชื่อทีม → คืน (หน้าชื่อเหย้า, หลังชื่อเยือน)

    เลขในวงเล็บ = **จำนวนใบแดง** ตรงกับที่ goaloo ส่งมาดิบๆ ไม่ต้องคำนวณต่อ
    โชว์เฉพาะฝั่งที่โดน — ฝั่งที่ไม่โดนไม่มีอะไรโผล่เลย (สะอาดสุด ไม่มีทางอ่านผิด)
    มาจาก goaloo เจ้าเดียว (Forebet/LiveScore ไม่มีให้เลย) · None = ไม่รู้ ไม่ใช่ 0
    → ไม่รู้ก็ไม่พูด เพราะ "ไม่มีใบแดง" กับ "ไม่รู้ว่ามีไหม" คนละเรื่องกัน
    ยังไม่เอามาเป็นเงื่อนไข — คลัง 132k คู่เป็น Forebet ล้วน ไม่มีข้อมูลใบแดงให้วัดสักคู่
    จดไว้ก่อน อีก 2-3 เดือนค่อยวัดว่ามันเปลี่ยนอะไรจริงไหม
    """
    rh, ra = m.get("_rh"), m.get("_ra")
    return (f"[{rh}]🟥" if rh else ""), (f"🟥[{ra}]" if ra else "")


def _bet(m, mkt, head):
    """คืน (บรรทัด "กินเมื่อ", บรรทัด "ช่องที่แทง", บรรทัด "ห้าม")

    ทำไมต้องมี: เกณฑ์ที่วัดกับช่องบนโต๊ะบอลไม่ใช่คำเดียวกัน — บันไดจริงตอน 0-0:
        เสมอ (เส้น 0)      → คืนทุน       ← เกณฑ์ที่วัด 89.8% ยืนอยู่ตรงนี้
        ต่อบ้าน -0.25      → เสียครึ่ง
        ต่อบ้าน -0.5       → เสียเต็ม
    เลข 89.8% **นับเสมอเป็นเข้า** ถ้าเอาไปแทง "ต่อ" เลขนั้นใช้ไม่ได้เลย
    และของเดิมบรรทัด 📊 เขียนว่า "ฝั่งนั้นไม่แพ้" ซึ่งไม่มีคำว่าฝั่งไหนอยู่ในประโยค
    ⚠️ ต้องตรงกับ f5Judge_ ใน Fabel5.gs เป๊ะ ไม่งั้นการ์ดบอกอย่าง เกรดอีกอย่าง
       lv_nolose เกรดด้วย HS >= GS (ฝั่งเต็ง) · อีก 3 ตลาดเกรดด้วย ลูกครึ่งหลัง >= 1
    """
    if mkt == "lv_nolose":
        away = head.startswith("เยือน")
        team = m.get("GUEST_NAME") if away else m.get("HOST_NAME")
        side = "เยือน" if away else "บ้าน"
        return (f"{team} ไม่แพ้ — ชนะก็ได้ เสมอก็ได้",
                f'ช่อง "{side} ส." · ถ้าเล่นเสมอ (0) เสมอแล้วได้ทุนคืนเฉยๆ ไม่ได้กำไร',
                f"ต่อ{side} -0.25 (เสมอเสียครึ่ง) · ต่อ{side} -0.5 (เสมอเสียหมด) "
                f"— เลข 89.8% มันนับเสมอเป็นเข้าไว้แล้ว")
    return ("มีอีกลูก ใครยิงก็ได้ ตั้งแต่ตอนนี้ถึงจบเกม",
            'ช่อง "สูง" — มาลูกเดียวก็จบ · ไม่ได้ทายว่าใครชนะ',
            "")


def fmt(m, mkt, head, why, hit, n, base, minute):
    r = RULES[mkt]
    lg = ((m.get("short_tag") or "?"), str(m.get("code") or "??").upper())
    # ตลาด gate=global ไม่มีเลขรายลีก → โชว์เลขรวมตรงๆ ห้ามแกล้งทำเป็นเลขรายลีก
    stat = (f"วัดรวมทุกลีก {hit:.1f}% ({n:,} คู่)" if r["gate"] == "global"
            else f"ลีกนี้เข้า {hit:.1f}% (วัด {n} คู่) · ทุกลีก {base:.1f}%")
    rpre, rpost = _red(m)
    win, slip, ban = _bet(m, mkt, head)
    return (
        f"⚽ {head} · {minute}\"\n"
        f"{_ko_th(m)}{rpre}{m.get('HOST_NAME')} {_i(m.get('Host_SC')) or 0}-"
        f"{_i(m.get('Guest_SC')) or 0} {m.get('GUEST_NAME')}{rpost}\n"
        f"🏆 [{lg[1]}] {lg[0]}\n"
        f"🔎 {why}\n"
        f"🎯 กินเมื่อ: {win}\n"
        f"🎫 แทงช่อง: {slip}\n"
        + (f"🚫 อย่าแทง: {ban}\n" if ban else "")
        + f"⏱ ลงตอนพักครึ่ง — เปิดครึ่งหลังแล้วราคาหายแล้ว\n"
        + f"📊 {r['label']} — {stat}\n"
        + (f"📎 {r['note']}\n" if r.get("note") else "")
        + f"⚠️ เลขนี้วัดจากสภาพตอนพักครึ่ง → ผลจบเกม ไม่ได้ดูนาทีนี้"
    )


# ── ส่ง ────────────────────────────────────────────────────────────────────────
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
    # ไฟล์มีคำอธิบายภาษาไทยปนอยู่ — เอาบรรทัดที่หน้าตาเป็นกุญแจล้วน
    for line in open(KEY_FILE, encoding="utf-8"):
        line = line.strip()
        if re.fullmatch(r"[A-Za-z0-9_.\-]{8,}", line):
            return line
    return ""


def send(text, dry=False, meta=None):
    """ส่งใบเตือน

    ทางหลัก = PIKTAX `action=f5alert` — มันจำ message_id ไว้ในชีต เจ้าของจะได้ "ตอบสกอร์"
    ใต้ใบเตือนแล้วระบบเกรดเองบนคลาวด์ได้ (ไม่ต้องเปิดคอมมารัน fb_grade.py)
    ถ้า f5alert ไม่ผ่าน (ยังไม่ deploy) ถอยไป `notify` — ใบต้องถึงมือไว้ก่อน เกรดทีหลังได้
    """
    if dry:
        print("─" * 46 + "\n" + text, flush=True)
        return True
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=20)
        return r.status_code == 200
    url, key = _piktax_url(), _admin_key()
    if not url or not key:
        print("⚠️ ส่งไม่ได้ — ไม่มีทั้ง TELEGRAM_BOT_TOKEN และกุญแจ PIKTAX", flush=True)
        return False

    if meta is not None:
        p = {"admin": key, "action": "f5alert", "text": text,
             "meta": json.dumps(meta, ensure_ascii=False, separators=(",", ":"))}
        try:
            r = requests.get(url, params=p, timeout=90)
            if r.status_code == 200 and "f5alert OK" in r.text:
                return True
            print(f"⚠️ f5alert ไม่ผ่าน ({r.status_code} · {r.text[:90]!r}) → ถอยไป notify", flush=True)
        except Exception as e:
            print(f"⚠️ f5alert พัง ({e}) → ถอยไป notify", flush=True)

    r = requests.get(url, params={"admin": key, "action": "notify", "text": text}, timeout=90)
    ok = r.status_code == 200 and "notify OK" in r.text
    if not ok:
        print(f"⚠️ PIKTAX notify ไม่ผ่าน: HTTP {r.status_code} · {r.text[:120]!r}", flush=True)
    return ok


def load_seen():
    if os.path.exists(SEEN):
        try:
            return json.load(open(SEEN, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_seen(seen):
    today = date.today().isoformat()
    seen = {k: v for k, v in seen.items() if k.startswith(today)}   # เก็บแค่ของวันนี้
    json.dump(seen, open(SEEN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return seen


def log_live(matches, minute_of):
    """จดสกอร์รายนาทีไว้ — ของที่วัดย้อนหลังไม่ได้ ต้องเริ่มเก็บตั้งแต่วันนี้"""
    if NO_LIVELOG:      # บนคลาวด์ไม่จด — โตวันละ ~1.5MB แล้วต้อง commit กลับทุก 5 นาที
        return
    now = datetime.now().isoformat(timespec="seconds")
    with open(LIVELOG, "a", encoding="utf-8") as f:
        for mid, m in matches.items():
            f.write(json.dumps({
                "at": now, "id": mid, "min": minute_of[mid],
                "HS": _i(m.get("Host_SC")), "GS": _i(m.get("Guest_SC")),
                "HH": _i(m.get("Host_SC_HT")), "GH": _i(m.get("Guest_SC_HT")),
                "lg": m.get("league_id"), "h": m.get("HOST_NAME"), "a": m.get("GUEST_NAME"),
            }, ensure_ascii=False, separators=(",", ":")) + "\n")


def log_alert(mid, m, mkt, head, minute, hit, n, b, dry):
    """จดทุกใบที่เตือนไป — ไว้ตามเก็บผลจบทีหลังแล้ววัดว่า % ที่โชว์ตรงจริงไหม

    ไม่จดผลจบตรงนี้ (ตอนเตือนเกมยังไม่จบ) · `res` เว้นว่างไว้ให้ตัวเกรดมาเติม
    """
    with open(ALERTLOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "at": datetime.now().isoformat(timespec="seconds"),
            "id": mid, "mkt": mkt, "head": head, "min": minute,
            "lg": m.get("league_id"), "tag": m.get("short_tag"), "cc": m.get("code"),
            "h": m.get("HOST_NAME"), "a": m.get("GUEST_NAME"),
            "HH": _i(m.get("Host_SC_HT")), "GH": _i(m.get("Guest_SC_HT")),
            "rh": m.get("_rh"), "ra": m.get("_ra"),   # ใบแดง (None = ไม่รู้) — เก็บไว้วัดทีหลัง
            "p1": _f(m.get("Pred_1")), "p2": _f(m.get("Pred_2")),
            "pro": _f(m.get("pr_over")), "pgg": _f(m.get("Pred_gg")), "gavg": _f(m.get("goalsavg")),
            "hit": hit, "n": n, "base": b,        # เลขที่โชว์ในใบนั้น
            "res": None,                          # ตัวเกรดมาเติม: True/False
            "dry": bool(dry),
        }, ensure_ascii=False, separators=(",", ":")) + "\n")


def stamp_goals(matches, minute_of, seen, dry):
    """จด "นาทีที่เห็นลูกแรกหลังพักครึ่ง" ของใบที่เตือนไปแล้ววันนี้ ลงชีตผ่าน f5stamp

    ตอบคำถามเดียวที่เจ้าของทักมา (9 ส.ค. 69): "ส่วนใหญ่มันก็ยิงก่อน"
      ถ้าลูกมาไวเกือบทุกใบ = ช่วงที่ยังเข้าทันแทบไม่มีจริง → ใบเตือนมีค่าน้อยกว่าที่ % บอก
      วัด 2-3 อาทิตย์ค่อยสรุป ห้ามเดาก่อนมีเลข

    ต้องจบที่ชีต ไม่ใช่ไฟล์ที่เครื่อง — ตัวจริงรันบน GitHub Actions ไฟล์หายไปกับ runner ทุกรอบ
    ⏱️ รอบละ 5 นาที → คลาดได้ถึง +5 ("นาทีที่เห็น" ไม่ใช่ "นาทีที่ยิง") พอหาค่ากลาง ไม่พออ้างรายใบ
    """
    today = date.today().isoformat()
    todo = []
    for mid, m in matches.items():
        key = f"{today}:{mid}"
        mark = str(seen.get(key) or "")
        if not mark or "|G" in mark:
            continue                      # ไม่ใช่ใบที่เตือนวันนี้ / จดไปแล้ว
        HS, GS = _i(m.get("Host_SC")), _i(m.get("Guest_SC"))
        HH, GH = _i(m.get("Host_SC_HT")), _i(m.get("Guest_SC_HT"))
        mn = minute_of.get(mid)
        if None in (HS, GS, HH, GH) or mn is None:
            continue
        if HS + GS <= HH + GH:
            continue                      # ยังไม่มีลูกใหม่ (ใบออกตอนสกอร์เท่าพักครึ่งเป๊ะ)
        todo.append((key, mid, mn))
    if not todo:
        return 0

    if dry:
        print("  (dry) นาทีที่เห็นลูก: "
              + ", ".join(f"{i}@{n}'" for _, i, n in todo), flush=True)
        return len(todo)

    url, key = _piktax_url(), _admin_key()
    if not url or not key:
        return 0
    data = json.dumps([{"id": i, "min": n} for _, i, n in todo],
                      ensure_ascii=False, separators=(",", ":"))
    try:
        r = requests.get(url, params={"admin": key, "action": "f5stamp", "data": data}, timeout=90)
        if r.status_code == 200 and "f5stamp: จด" in r.text:
            # จดสำเร็จค่อยกาไว้ — ไม่สำเร็จปล่อยให้รอบหน้าลองใหม่ (ช้าไป 5 นาทีดีกว่าหายเลย)
            for k, _, n in todo:
                seen[k] = f"{seen[k]}|G{n}"
            print(f"⚽ จดนาทีลูกมา {len(todo)} ใบ · {r.text.strip()[:80]}", flush=True)
            return len(todo)
        print(f"⚠️ f5stamp ไม่ผ่าน: HTTP {r.status_code} · {r.text[:90]!r}", flush=True)
    except Exception as e:
        print(f"⚠️ f5stamp พัง: {e}", flush=True)
    return 0


def sweep(route, trust, base, gall, seen, dry):
    matches, src = live_now(route)
    stamp = datetime.now().strftime("%H:%M")
    if not matches:
        print(f"[{stamp}] ยังไม่มีบอลสด · {src}", flush=True)
        return 0

    minute_of = {mid: m.get("_min") for mid, m in matches.items()}
    log_live(matches, minute_of)
    sent = 0
    for mid, m in matches.items():
        minute = minute_of[mid]
        if not (MIN_FROM <= minute <= MIN_TO):
            continue
        key = f"{date.today().isoformat()}:{mid}"
        if key in seen:
            continue                  # 1 คู่ = 1 ใบต่อวัน · ตัวนี้เป็น "ตัวชี้จุด" ไม่ใช่ตัวสาดใบ
        # หลายกฎยิงคู่เดียวกันได้ (เช่น ยิงข้างเดียว 1-0 เข้าทั้ง bts1_g1 และ over_g1)
        # → ส่งใบเดียว เอาอันที่วัดได้แม่นสุด
        cand = []
        for mkt, head, why in check_rules(m):
            ok, hit, n, b = league_ok(trust, base, gall, m.get("league_id"), mkt)
            if ok:
                cand.append((hit, mkt, head, why, n, b))
        if not cand:
            continue
        hit, mkt, head, why, n, b = max(cand)
        meta = {
            "id": mid, "mkt": mkt, "head": head,
            "tag": m.get("short_tag"), "cc": m.get("code"), "lg": m.get("league_id"),
            "h": m.get("HOST_NAME"), "a": m.get("GUEST_NAME"), "min": minute,
            "HH": _i(m.get("Host_SC_HT")), "GH": _i(m.get("Guest_SC_HT")),
            # ใบแดงต้องมากับ meta ด้วย ไม่ใช่จดแค่ไฟล์ที่เครื่องบ้าน —
            # ตัวจริงรันบน GitHub Actions ไฟล์นั้นหายไปกับ runner ทุกรอบ
            # ชีตคือที่เดียวที่อยู่ถาวร ถ้าไม่ส่งไป อีก 2-3 เดือนจะไม่มีอะไรให้วัด
            "rh": m.get("_rh"), "ra": m.get("_ra"),   # None = ไม่รู้ (ไม่ใช่ 0)
            "hit": round(hit, 1), "n": n, "base": round(b, 1),
            "p1": _f(m.get("Pred_1")), "p2": _f(m.get("Pred_2")),
        }
        if send(fmt(m, mkt, head, why, hit, n, b, minute), dry, meta):
            log_alert(mid, m, mkt, head, minute, hit, n, b, dry)
            seen[key] = stamp
            sent += 1
    # ตามใบที่เตือนไปแล้วว่าลูกมาเมื่อไหร่ — นอกหน้าต่างนาที 45-58 ด้วย (ลูกมานาที 80 ก็ต้องจด)
    stamp_goals(matches, minute_of, seen, dry)

    inplay = sum(1 for v in minute_of.values() if v is not None)
    window = sum(1 for v in minute_of.values() if v is not None and MIN_FROM <= v <= MIN_TO)
    print(f"[{stamp}] บอลสด {inplay} คู่ (อยู่ในช่วงเตือน {window}) · เตือน {sent} · {src}", flush=True)
    return sent


def main():
    a = sys.argv[1:]
    if "--build-cache" in a:     # งานวันละครั้งบนคลาวด์ — ไม่ต้องใช้ fb_trust.json
        sys.exit(cache_build())
    once = "--once" in a
    dry = "--dry" in a
    every = float(a[a.index("--every") + 1]) if "--every" in a else 3.0
    hours = float(a[a.index("--hours") + 1]) if "--hours" in a else 12.0

    if not os.path.exists(TRUST):
        sys.exit("❌ ไม่มี fb_trust.json — รัน `python fb_calib.py 40` ก่อน")
    trust, base, gall = load_trust()
    bits = []
    for k, r in RULES.items():
        if r["gate"] == "global":
            d = gall.get(k)
            bits.append(f"{k} รวมทุกลีก {d['hit']:.1f}%" if d else f"{k} ❌ไม่มีเลขวัด")
        elif k in base:
            bits.append(f"{k} ฐาน {base[k]:.1f}%+{r['need_lift']:g}")
    print(f"🧠 ตัวกรองรายลีก {sum(1 for x in trust if not x.startswith('_'))} ลีก · "
          + " · ".join(bits), flush=True)
    print(f"⏱️ เตือนเฉพาะนาที {MIN_FROM}-{MIN_TO} (ช่วงที่ตัวเลขวัดมาตรงจริง)", flush=True)

    seen = load_seen()
    # โหมดอ่านแคชไม่แตะ Forebet เลย — ไม่ต้องเปิดเส้นทาง (ไม่งั้นโดน 403 ทุก 5 นาที วันละ 288 ครั้ง)
    route = None if CACHE_MODE == "read" else fbapi.fb_session()
    deadline = time.time() + hours * 3600
    total = 0
    while True:
        try:
            total += sweep(route, trust, base, gall, seen, dry)
        except Exception as e:
            print(f"⚠️ รอบนี้พัง: {e}", flush=True)
        seen = save_seen(seen)
        if once or time.time() >= deadline:
            break
        time.sleep(every * 60)
    print(f"\n🏁 จบ · เตือนไปทั้งหมด {total} ครั้ง", flush=True)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""fb_value.py — ใบ "ก่อนเกม" จากหน้า Forebet /en/values (แยกขาดจาก fb_watch.py)

⚠️ ทำไมต้องเป็นไฟล์แยก ห้ามเอาไปยัดใน fb_watch.py
   CLAUDE.md ข้อ 2 เขียนไว้ว่า "ห้ามเตือนก่อนเกมเริ่ม" — นั่นคือกติกาของ *เครื่องเตือนสด*
   (4 ตลาด · เส้นฐาน 78.4% · วัดจากสภาพตอนพักครึ่ง) ตัวนี้เป็นคนละเครื่อง คนละสมอง
   จึงต้องแยกไฟล์ · แยก workflow · แยกไฟล์ seen · และ **ไม่ยิงเข้าชีต FABEL5**
   (ใช้ action=notify ไม่ใช่ f5alert — ไม่งั้นแถวมันจะไปปนกับ f5Tally_ ทำ % ของเครื่องสดเพี้ยน)

🧠 สมองประโยคเดียว
   "Forebet มั่นใจสูง แต่เจ้ามือยังให้ราคาดีอยู่" — ส่วนต่างนั้นคือค่าคุ้ม

   ค่าคุ้ม = (Prob% / 100) x Coef - 1
   สูตรนี้ผลิตตัวเลข % ท้ายแถวบนหน้า /en/values ได้ตรงเป๊ะ (ตรวจกับภาพหน้าจอจริง 8 แถว)

📋 การ์ดบอกอะไรบ้าง — และทำไมถึงไม่บอกว่าใครชนะ
   เจ้าของสั่งเอง: "ก็บอกแต่สกอร์มาดิ่ เดี๋ยวตัดสินใจเอง จะกดรอง กดต่อเองได้ เลือกเองเป็น"
   ซึ่งตรงกับเจตนาโครงการพอดี (🔴 ไม่ใช่ตัวแนะนำแทง)
   → การ์ดให้แค่ วัตถุดิบ: สกอร์ที่ทาย + ความเข้ม + Coef + ค่าคุ้ม แล้วจบ
   หมายเหตุ: สกอร์ที่ทาย = ตัวเดียวกับ 1X2 อยู่แล้ว (วัด 97,227 คู่ ตรงกัน 100.0%)
   เช่น 3-0/2-1/1-0 = เจ้าบ้าน · 0-2/1-3 = ทีมเยือน  → ดูสกอร์ก็รู้ฝั่งเอง ไม่ต้องมีใครบอก

⚖️ กติกาเหล็ก (เหมือน fb_watch): ห้ามมีเลขที่ไม่เคยวัด
   ทุกเลขบนการ์ดมาจาก fb_hist.jsonl (132,851 คู่ · 365 วัน) ตาราง TIERS ข้างล่าง
   และเลขที่ยังพิสูจน์ไม่ได้ ต้องบอกตรงๆ ว่าพิสูจน์ไม่ได้

usage:
  python fb_value.py --dry        # พิมพ์เฉยๆ ไม่ส่ง (ไว้ดูหน้าตาการ์ด)
  python fb_value.py              # ส่งจริงผ่าน PIKTAX
  python fb_value.py --lead 30    # เอาเฉพาะคู่ที่ยังไม่เตะในอีก 30 นาทีขึ้นไป
"""
import argparse
import gzip
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import requests

from fb_block import blocked

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "fb_day_cache.json.gz")     # แคชเดียวกับที่ fb-cache สร้าง (อ่านอย่างเดียว ห้ามเขียน)
SEEN = os.path.join(HERE, "fb_value_seen.json")        # ไฟล์ seen ของตัวเอง — ห้ามใช้ร่วมกับ fb_watch_seen.json
LOG = os.path.join(HERE, "fb_value_log.jsonl")         # ใบที่ส่งไปแล้ว + เลขที่โชว์ (ไว้เช็คย้อนหลัง)

DEPLOY_FILE = r"D:\Projects\t.seeedz\PIKTAX\.deployId"
KEY_FILE = r"D:\Projects\.gas-creds\piktax-admin-key.txt"

# ── ด่าน ──────────────────────────────────────────────────────────────────────
# วัดจาก fb_hist.jsonl ทั้งคลัง · ตัดบอลถ้วยทิ้ง · ตัดคู่ที่ Forebet ทายเสมอทิ้ง
#
#   ค่าคุ้ม >= +20%  n=317  เข้า 62.8%  กำไร/ไม้  +63.1%
#   ค่าคุ้ม >= +40%  n=163  เข้า 66.3%  กำไร/ไม้ +120.8%   <-- ใช้เส้นนี้
#   ค่าคุ้ม >= +75%  n= 87  เข้า 72.4%  กำไร/ไม้ +208.0%
#   ค่าคุ้ม >=+100%  n= 59  เข้า 76.3%  กำไร/ไม้ +277.5%
#
# ทำไมเลือก +40% ทั้งที่ +75% แม่นกว่า: เพราะ +75% เหลือปีละ 87 ใบ พอแบ่งชั้นความเข้ม
# แล้วเหลือชั้นละ ~40 ใบ ค่าคลาด ±7 → สองชั้นเหลื่อมกันจนแยกไม่ออก = ความเข้มโกหก
# เส้น +40% มี 163 ใบ แบ่งแล้วชั้นละ ~80 ใบ ค่าคลาด ±5 → สองชั้นห่างกัน 12 จุด แยกออกจริง
EDGE_MIN = 0.40
PROB_MIN = 70

# ── ตารางความเข้ม (วัดจริง ห้ามแต่งเลขเอง) ────────────────────────────────────
# ทุกแถววัดบนเงื่อนไข: ไม่ใช่บอลถ้วย · Forebet ไม่ได้ทายเสมอ · ค่าคุ้ม >= +40%
#   prob 75 ขึ้นไป : n=74  เข้า 73.0% +-5.2  กำไร/ไม้ +133.9%
#   prob 70-74     : n=89  เข้า 60.7% +-5.2  กำไร/ไม้ +110.0%
TIERS = [
    # (prob ขั้นต่ำ, ป้าย, %เข้าที่วัดได้, จำนวนใบที่ใช้วัด)
    (75, "🔥🔥 เข้ม 2/2", 73.0, 74),
    (70, "🔥 เข้ม 1/2", 60.7, 89),
]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ── อ่านแคช ───────────────────────────────────────────────────────────────────
def cache_load():
    """อ่านแคช · คืน ({}, เหตุผล) ถ้าใช้ไม่ได้ — ยอมเงียบดีกว่าส่งรายการของเมื่อวาน

    ก๊อปตรรกะมาจาก fb_watch.cache_load() ตั้งใจให้ซ้ำ ไม่ import ข้ามไฟล์
    เพราะ import fb_watch จะลาก live_api + forebet_api ตามมาทั้งพวง โดยตัวนี้ไม่ได้ใช้เลย
    """
    if not os.path.exists(CACHE):
        return {}, "ไม่มีแคช Forebet (งาน fb-cache ยังไม่เคยรันสำเร็จ)"
    try:
        with gzip.open(CACHE, "rb") as f:
            d = json.loads(f.read().decode("utf-8"))
    except Exception as e:
        return {}, f"แคชพัง: {e}"
    if date.today().isoformat() not in (d.get("days") or []):
        return {}, f"แคชเก่าเกินไป (สร้างเมื่อ {d.get('built')})"
    return (d.get("fb") or {}), ""


# ── เวลา ──────────────────────────────────────────────────────────────────────
def _ko_bkk(m):
    """DATE_BAH ('YYYY-MM-DD HH:MM:SS') เป็นเวลายุโรปเสมอ → คืน datetime เวลาไทย

    ยืนยันแล้วที่ main.py:1063 ว่าพารามิเตอร์ tz ของ API ไม่ขยับค่านี้
    ไม่รู้เวลา = คืน None แล้วตัดทิ้ง ไม่เดา (เดาผิดแปลว่าส่งใบของคู่ที่เตะไปแล้ว)
    """
    try:
        from zoneinfo import ZoneInfo
        return (datetime.strptime(str(m.get("DATE_BAH"))[:19], "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=ZoneInfo("Europe/Paris"))
                .astimezone(ZoneInfo("Asia/Bangkok")))
    except Exception:
        return None


def _now_bkk():
    return datetime.now(timezone(timedelta(hours=7)))


# ── คัดใบ ─────────────────────────────────────────────────────────────────────
def pick(m, lead_min, lead_max_min=None, stat=None):
    """คืน dict ของใบถ้าผ่านด่าน · คืน None ถ้าไม่ผ่าน

    ลำดับด่าน (เรียงจากถูกที่สุดไปแพงที่สุด):
      1. ยังไม่จบ/ยังไม่เตะ   2. ไม่ใช่บอลถ้วย   3. Forebet ไม่ได้ทายเสมอ
      4. Prob >= 70          5. ค่าคุ้ม >= +40%   6. อยู่ในหน้าต่างเวลาก่อนเตะ

    stat = dict นับว่าตกด่านไหน — บทเรียนจาก gmin ของเครื่องสด:
    ล็อกที่บอกแค่ยอดรวม แยก "ไม่มีคู่เข้าเกณฑ์" กับ "ด่านพัง" ไม่ออก ต้องให้มันบอกเอง
    """
    def no(k):
        if stat is not None:
            stat[k] = stat.get(k, 0) + 1
        return None

    if str(m.get("comment") or "").strip().upper() == "FT":
        return no("จบแล้ว")
    if blocked(m):
        return no("ลีกที่ปิดไว้")     # เว็บไม่เปิดให้แทง
    if _i(m.get("isCup")) == 1:
        return no("บอลถ้วย")                            # ตัวแปรเยอะ (ทีมสำรอง/ต่างชั้น) ไม่เอา

    pr = {"1": _i(m.get("Pred_1")), "X": _i(m.get("Pred_X")), "2": _i(m.get("Pred_2"))}
    if None in pr.values():
        return no("ไม่มีค่าทาย")
    side = max(pr, key=lambda k: pr[k])
    if side == "X":
        return no("ทายเสมอ")                            # ไม่มีฝั่งให้เลือก ตัดทิ้ง
    if pr[side] < PROB_MIN:
        return no("Prob<%d" % PROB_MIN)

    coef = _f({"1": m.get("best_odd_1"), "X": m.get("best_odd_X"),
               "2": m.get("best_odd_2")}[side])
    if not coef or coef <= 1:
        return no("ไม่มีราคา")
    edge = pr[side] / 100.0 * coef - 1.0
    if edge < EDGE_MIN:
        return no("ค่าคุ้ม<+%d%%" % (EDGE_MIN * 100))

    ko = _ko_bkk(m)
    if ko is None:
        return no("ไม่รู้เวลาเตะ")
    now = _now_bkk()
    if ko <= now + timedelta(minutes=lead_min):
        return no("จวนเตะ/เตะแล้ว")                      # สายเกินจะลง
    if lead_max_min and ko > now + timedelta(minutes=lead_max_min):
        # ⏱ เพดานเวลา — ยังอีกไกลเกิน ปล่อยไว้ให้รอบหลังยิง
        #    เหตุผล: Coef บนการ์ดคือราคา "ตอนแคชถูกสร้าง" ถ้ายิงล่วงหน้าครึ่งวัน
        #    ราคาขยับจนค่าคุ้มที่โฆษณาไว้ไม่จริงแล้ว → การ์ดกลายเป็นตัวหลอก
        #    ตั้ง 180 นาที (3 ชม.) เพราะรอบงานห่างกัน 2 ชม. → กว้างกว่ารอบ 1 ชม. ไม่มีคู่ไหนหลุด
        return no("ยังอีกไกล")

    hs, gs = _i(m.get("host_sc_pr")), _i(m.get("guest_sc_pr"))
    if hs is None or gs is None:
        return no("ไม่มีสกอร์ที่ทาย")                     # ไม่มีอะไรจะบอก

    tier = next(((lab, hit, n) for lo, lab, hit, n in TIERS if pr[side] >= lo), None)
    if tier is None:
        return no("ไม่เข้าชั้นความเข้ม")

    return dict(id=str(m.get("id") or ""), ko=ko, edge=edge, coef=coef, prob=pr[side],
                host=m.get("HOST_NAME"), guest=m.get("GUEST_NAME"),
                hs=hs, gs=gs, tier=tier,
                lg=(m.get("short_tag") or "?"), code=str(m.get("code") or "??").upper())


def fmt(p):
    """การ์ด — สกอร์ + ความเข้ม + วัตถุดิบ (Coef/ค่าคุ้ม) เท่านั้น ไม่บอกว่าใครชนะ"""
    lab, hit, n = p["tier"]
    return (
        f"⚽ [{p['code']}] {p['lg']} · {p['ko']:%H:%M}\n"
        f"{p['host']} vs {p['guest']}\n"
        f"Forebet ทาย {p['hs']}-{p['gs']}\n"
        f"{lab} — กลุ่มนี้ย้อนหลังเข้า {hit:.1f}% ({n} ใบ)\n"
        f"💱 Coef {p['coef']:.2f} · ค่าคุ้ม +{p['edge'] * 100:.0f}%\n"
        f"⚠️ เลขนี้คือ 1X2 (แพ้/ชนะ) ไม่ใช่สกอร์เป๊ะ — สกอร์เป๊ะทั้งคลังเข้าแค่ 9.5%"
    )


# ── ส่ง (ก๊อปทางส่งจาก fb_watch — ตั้งใจซ้ำ ไม่ import ข้ามไฟล์) ───────────────
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


def _call(action, params, timeout=90):
    """ยิงคำสั่งเดียวไป PIKTAX · คืน (สำเร็จไหม, ข้อความตอบกลับ)"""
    url, key = _piktax_url(), _admin_key()
    if not url or not key:
        return False, "ไม่มี PIKTAX_STATE_URL หรือ PIKTAX_ADMIN_KEY"
    q = {"admin": key, "action": action}
    q.update(params)
    try:
        r = requests.get(url, params=q, timeout=timeout)
    except Exception as e:
        return False, str(e)
    return r.status_code == 200, r.text


def send(p, dry=False):
    """ส่งผ่าน action=fvalert — ส่งเข้า Telegram แล้วจดลงแท็บ FBVALUE

    ⚠️ ห้ามเปลี่ยนไปใช้ f5alert — f5alert จดลงแท็บ FABEL5 แล้ว f5Tally_/f5StatsText_
       จะนับใบก่อนเกมพวกนี้รวมกับเครื่องเตือนสด ทำให้ % ของเครื่องสด (เส้นฐาน 78.4%) เพี้ยน
       fvalert = แท็บของตัวเอง คนละเกณฑ์วัด (1X2 ไม่ใช่ "ลูกที่ยังไม่เกิด")
    """
    text = fmt(p)
    if dry:
        print("─" * 46 + "\n" + text, flush=True)
        return True
    # meta = วัตถุดิบที่ชีตต้องใช้ตอนเกรดผล — ต้องมี id กับ side ไม่งั้นเกรดไม่ได้
    meta = json.dumps(dict(
        id=p["id"], lg=p["lg"], cc=p["code"], h=p["host"], a=p["guest"],
        ko=p["ko"].strftime("%Y-%m-%d %H:%M"), hs=p["hs"], gs=p["gs"],
        side=("1" if p["hs"] > p["gs"] else "2"),
        prob=p["prob"], coef=round(p["coef"], 2),
        edge=round(p["edge"] * 100, 1), tier=p["tier"][0], claim=p["tier"][1],
    ), ensure_ascii=False)
    ok, body = _call("fvalert", {"text": text, "meta": meta})
    if not ok:
        print(f"⚠️ ส่งไม่ผ่าน: {body[:200]}", flush=True)
        return False
    return "fvalert OK" in body


# ── เกรดผล ────────────────────────────────────────────────────────────────────
def grade(dry=False):
    """เติมผลใบเก่าจากแคช — ไม่ต้องรอเจ้าของตอบสกอร์เหมือน FABEL5

    ทำได้เพราะแคชคู่ที่จบแล้วพก Host_SC/Guest_SC มาด้วย (ตรวจแล้ว 1,296 แถว FT)
    ถามชีตก่อนว่าค้างใบไหน แล้วค่อยส่งเฉพาะสกอร์ของใบนั้นกลับไป
    (runner ไม่มีความจำ ชีตคือที่เดียวที่รู้ว่าเคยส่งใบอะไรไปบ้าง)
    """
    ok, body = _call("fvpending", {})
    if not ok:
        print(f"⚠️ ถามใบค้างไม่ได้: {body[:200]}", flush=True)
        return
    pend = {x.strip() for x in re.sub(r"\s+", "", body).split(",") if x.strip().isdigit()}
    if not pend:
        print("✓ ไม่มีใบค้างรอผล", flush=True)
        return

    # แคชเก็บ "เมื่อวาน + วันนี้" (ตรวจของจริงแล้ว: days=[D-1, D])
    # → คู่ที่เตะสี่ทุ่มเมื่อคืน ยังอยู่ในแคชรอบเช้า = เกรดทันเสมอ
    fb, why = cache_load()
    if not fb:
        print(f"⏸ เกรดไม่ได้: {why}", flush=True)
        return
    done = []
    for mid in pend:
        m = fb.get(mid) or fb.get(str(mid))
        if not m or str(m.get("comment") or "").strip().upper() != "FT":
            continue                                   # ยังไม่จบ = รอรอบหน้า
        hs, gs = _i(m.get("Host_SC")), _i(m.get("Guest_SC"))
        if hs is None or gs is None:
            continue
        done.append({"id": mid, "hs": hs, "gs": gs})

    print(f"ใบค้าง {len(pend)} · เจอสกอร์แล้ว {len(done)}", flush=True)
    if not done:
        return
    if dry:
        print(json.dumps(done, ensure_ascii=False), flush=True)
        return
    ok, body = _call("fvgrade", {"data": json.dumps(done, ensure_ascii=False)})
    print(("✓ " if ok else "⚠️ ") + body[:200], flush=True)


# ── กันส่งซ้ำ ─────────────────────────────────────────────────────────────────
def load_seen():
    try:
        d = json.load(open(SEEN, encoding="utf-8"))
        return set(d.get(date.today().isoformat()) or [])
    except Exception:
        return set()


def save_seen(ids):
    try:
        json.dump({date.today().isoformat(): sorted(ids)},
                  open(SEEN, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ เขียน seen ไม่ได้: {e}", flush=True)


def log(p):
    """จดใบที่ส่งไป + เลขที่โชว์ ไว้เช็คย้อนว่าที่โฆษณาไว้ตรงของจริงไหม

    ⚠️ ไฟล์นี้อยู่บน runner = หายทุกรอบ (CLAUDE.md เตือนไว้แล้ว)
       ถ้าจะเก็บของจริงไว้วัด ต้องต่อท่อลง Google Sheet — ยังไม่ได้ทำใน v1
    """
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(
                sent=_now_bkk().isoformat(timespec="seconds"), id=p["id"],
                ko=p["ko"].isoformat(timespec="minutes"), lg=p["lg"], code=p["code"],
                host=p["host"], guest=p["guest"], ps=f"{p['hs']}-{p['gs']}",
                prob=p["prob"], coef=round(p["coef"], 2), edge=round(p["edge"], 3),
                tier=p["tier"][0], claim=p["tier"][1],
            ), ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="พิมพ์เฉยๆ ไม่ส่ง")
    ap.add_argument("--lead", type=int, default=30, help="เอาเฉพาะคู่ที่เหลืออีกกี่นาทีขึ้นไปถึงเตะ")
    ap.add_argument("--lead-max", type=int, default=180,
                    help="เพดาน: ไม่ยิงคู่ที่ยังอีกไกลเกินกี่นาที (0 = ไม่มีเพดาน)")
    ap.add_argument("--max", type=int, default=12, help="ส่งได้มากสุดกี่ใบต่อรอบ")
    ap.add_argument("--no-grade", action="store_true", help="ไม่ต้องเกรดผลใบเก่า")
    a = ap.parse_args()

    # เกรดก่อนส่ง — ถ้าส่งพังจะได้ยังได้ผลใบเก่า (ผลสำคัญกว่าใบใหม่ ใบใหม่รอบหน้ามีอีก)
    if not a.no_grade:
        grade(dry=a.dry)

    fb, why = cache_load()
    if not fb:
        print(f"⏸ ไม่ทำอะไร: {why}", flush=True)
        return 0

    seen = load_seen()
    picks, stat = [], {}
    for m in fb.values():
        p = pick(m, a.lead, a.lead_max or None, stat)
        if p and p["id"] and p["id"] not in seen:
            picks.append(p)

    picks.sort(key=lambda p: (-p["prob"], -p["edge"]))
    print(f"อ่านแคช {len(fb):,} คู่ → ผ่านด่าน {len(picks)} ใบ (ส่งได้ไม่เกิน {a.max})", flush=True)
    if stat:
        # 🔎 ตกด่านไหนบ้าง — ไว้ดูว่า "0 ใบ" เพราะไม่มีคู่เข้าเกณฑ์ หรือเพราะด่านพัง
        print("   ตกด่าน: " + " · ".join(f"{k} {v}" for k, v in
                                        sorted(stat.items(), key=lambda kv: -kv[1])), flush=True)

    for p in picks[:a.max]:
        if send(p, dry=a.dry):   # ⚠️ ส่ง dict ไม่ใช่ข้อความ — send() เรียก fmt() เองข้างใน
            seen.add(p["id"])
            if not a.dry:
                log(p)
        else:
            print(f"⚠️ ส่งไม่ผ่าน: {p['host']} vs {p['guest']}", flush=True)

    if not a.dry:
        save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())

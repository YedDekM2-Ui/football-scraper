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
MIN_FROM, MIN_TO = 45, 58

# ── กฎ: ชื่อตลาดต้องตรงกับที่ fb_calib.py เขียนลง fb_trust.json ───────────────
#   need_lift = ลีกต้องดีกว่าเส้นฐานทุกลีกกี่จุด ถึงจะยอมเตือน
RULES = {
    "ht00_2h_goal": {"label": "ครึ่งแรก 0-0 → ครึ่งหลังมีประตู", "need_lift": 5.0, "minn": 60},
    "debt_fav":     {"label": "ตัวเต็งยังไม่นำ → จบพลิกชนะ",     "need_lift": 5.0, "minn": 40},
    "debt_over":    {"label": "สูงค้าง → จบ ≥3 ประตู",          "need_lift": 5.0, "minn": 40},
    "debt_btts":    {"label": "ยิงฝั่งเดียว → BTTS", "need_lift": 8.0, "minn": 40},
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
    """คืน (trust, baseline) · baseline = เส้นฐานทุกลีกของแต่ละตลาด (ถ่วงน้ำหนักด้วย n)"""
    trust = json.load(open(TRUST, encoding="utf-8"))
    tot = {}
    for lg, d in trust.items():
        for mkt, v in (d.get("mkt") or {}).items():
            a = tot.setdefault(mkt, [0, 0.0])
            a[0] += v["n"]
            a[1] += v["n"] * v["hit"] / 100.0
    base = {k: (v[1] / v[0] * 100 if v[0] else 0.0) for k, v in tot.items()}
    return trust, base


def league_ok(trust, base, lg, mkt):
    """ลีกนี้ผ่านเกณฑ์ตลาดนี้ไหม · คืน (ผ่าน, hit, n, เส้นฐาน)

    แยกรายตลาดเสมอ — ผู้ใช้ย้ำเอง: "ยูฟ่าหญิง ยิงเยอะจริง แต่ 1X2 แพ้ราบ"
    = ลีกเดียวกันเก่งคนละเรื่อง ให้คะแนนรวมทั้งลีกไม่ได้
    """
    r = RULES[mkt]
    b = base.get(mkt, 0.0)
    d = ((trust.get(str(lg)) or {}).get("mkt") or {}).get(mkt)
    if not d or d["n"] < r["minn"]:
        return False, None, (d or {}).get("n", 0), b
    return d["hit"] >= b + r["need_lift"], d["hit"], d["n"], b


# ── หาคู่ที่กำลังเตะ ───────────────────────────────────────────────────────────
def fetch_fb(route):
    """ดึงรายการคู่ Forebet ของเมื่อวาน+วันนี้ (3 ตลาดที่กฎใช้จริง)"""
    fb = {}
    for d in (date.today() - timedelta(days=1), date.today()):
        try:
            ms, _ = fbapi.fb_fetch_day(d.isoformat(), markets=("1x2", "uo", "bts"), sess=route)
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
                       "days": [(date.today() - timedelta(days=1)).isoformat(), date.today().isoformat()],
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
    gavg = _f(m.get("goalsavg"))
    out = []

    if HH + GH == 0:
        why = "พักครึ่ง 0-0"
        if gavg is not None:
            why += f" · Forebet ทายประตูรวม {gavg}"
        out.append(("ht00_2h_goal", "ครึ่งหลังอาจจะมีประตู", why))

    if pro is not None and pro >= 65 and HH + GH <= 1:
        out.append(("debt_over", "มีสกอร์อย่างน้อย1", f"ทายสูง {pro:.0f}% · พักครึ่งเพิ่ง {HH+GH} ประตู"))

    if pgg is not None and pgg >= 65 and (HH > 0) != (GH > 0):
        out.append(("debt_btts", "BTTS,สูง,จบ2+", f"ทายยิงกันทั้งคู่ {pgg:.0f}% · พักครึ่งยิงฝั่งเดียว {HH}-{GH}"))

    if p1 is not None and p2 is not None:
        # ตามหลังอยู่ = ยังต้องไล่ · เสมออยู่ = แค่รักษาไว้ → คนละหัว
        if p1 >= 60 and HH <= GH:
            head = "เสมอบ้าน,ลุ้นแซง,กดสูง" if HH < GH else "บ้านไม่แพ้"
            out.append(("debt_fav", head, f"ทายเจ้าบ้านชนะ {p1:.0f}% · พักครึ่ง {HH}-{GH}"))
        elif p2 >= 60 and GH <= HH:
            head = "เสมอเยือน,ลุ้นแซง,กดสูง" if GH < HH else "เยือนไม่แพ้"
            out.append(("debt_fav", head, f"ทายทีมเยือนชนะ {p2:.0f}% · พักครึ่ง {HH}-{GH}"))
    return out


def fmt(m, mkt, head, why, hit, n, base, minute):
    r = RULES[mkt]
    lg = ((m.get("short_tag") or "?"), str(m.get("code") or "??").upper())
    return (
        f"⚽ {head} · นาที {minute}\n"
        f"{m.get('HOST_NAME')} {_i(m.get('Host_SC')) or 0}-{_i(m.get('Guest_SC')) or 0} {m.get('GUEST_NAME')}\n"
        f"🏆 [{lg[1]}] {lg[0]}\n"
        f"🔎 {why}\n"
        f"📊 {r['label']} — ลีกนี้เข้า {hit:.1f}% (วัด {n} คู่) · ทุกลีก {base:.1f}%\n"
        f"⚠️ เป็นการคาดการณ์ \"ตอนพักครึ่ง → จบเกม\" ไม่ใช่ของนาทีนี้"
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
            "p1": _f(m.get("Pred_1")), "p2": _f(m.get("Pred_2")),
            "pro": _f(m.get("pr_over")), "pgg": _f(m.get("Pred_gg")), "gavg": _f(m.get("goalsavg")),
            "hit": hit, "n": n, "base": b,        # เลขที่โชว์ในใบนั้น
            "res": None,                          # ตัวเกรดมาเติม: True/False
            "dry": bool(dry),
        }, ensure_ascii=False, separators=(",", ":")) + "\n")


def sweep(route, trust, base, seen, dry):
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
        for mkt, head, why in check_rules(m):
            key = f"{date.today().isoformat()}:{mid}:{mkt}"
            if key in seen:
                continue
            ok, hit, n, b = league_ok(trust, base, m.get("league_id"), mkt)
            if not ok:
                continue
            meta = {
                "id": mid, "mkt": mkt, "head": head,
                "tag": m.get("short_tag"), "cc": m.get("code"), "lg": m.get("league_id"),
                "h": m.get("HOST_NAME"), "a": m.get("GUEST_NAME"), "min": minute,
                "HH": _i(m.get("Host_SC_HT")), "GH": _i(m.get("Guest_SC_HT")),
                "hit": round(hit, 1), "n": n, "base": round(b, 1),
                "p1": _f(m.get("Pred_1")), "p2": _f(m.get("Pred_2")),
            }
            if send(fmt(m, mkt, head, why, hit, n, b, minute), dry, meta):
                log_alert(mid, m, mkt, head, minute, hit, n, b, dry)
                seen[key] = stamp
                sent += 1
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
    trust, base = load_trust()
    print(f"🧠 ตัวกรองรายลีก {len(trust)} ลีก · เส้นฐาน " +
          " · ".join(f"{k} {base[k]:.1f}%" for k in RULES if k in base), flush=True)
    print(f"⏱️ เตือนเฉพาะนาที {MIN_FROM}-{MIN_TO} (ช่วงที่ตัวเลขวัดมาตรงจริง)", flush=True)

    seen = load_seen()
    route = fbapi.fb_session()
    deadline = time.time() + hours * 3600
    total = 0
    while True:
        try:
            total += sweep(route, trust, base, seen, dry)
        except Exception as e:
            print(f"⚠️ รอบนี้พัง: {e}", flush=True)
        seen = save_seen(seen)
        if once or time.time() >= deadline:
            break
        time.sleep(every * 60)
    print(f"\n🏁 จบ · เตือนไปทั้งหมด {total} ครั้ง", flush=True)


if __name__ == "__main__":
    main()

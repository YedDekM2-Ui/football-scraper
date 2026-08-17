# -*- coding: utf-8 -*-
"""fb_pick.py — ใบสแกนก่อนเกม แยก 4 ประเภท (สูง / ต่ำ / 1X2 / สกอร์) แบบกดขอแล้วเด้ง

⚠️ เครื่องที่ 3 — แยกขาดจาก fb_watch.py (เตือนสด) และ fb_value.py (ค่าคุ้ม +40%)
   · fb_watch = ในเกม 4 ตลาด · ห้ามเตือนก่อนเกม (กติกาของ *เครื่องนั้น* ไม่ใช่ทุกเครื่อง)
   · fb_value = ก่อนเกม 1X2 เส้นแคบ prob>=70 + ค่าคุ้ม>=+40% (เจ้าของสั่ง "ปล่อยไว้เท่าเดิม")
   · fb_pick  = ก่อนเกม 4 ประเภท ใช้กฎที่เจ้าของส่งมาเอง · seen แยกไฟล์ · ไม่ยิงเข้าชีต FABEL5

🎯 ใบนี้คือ "ด่านแรก" ไม่ใช่คำสั่งลงเงิน
   ตามที่เจ้าของสั่ง: การ์ดสแกนหาคู่ที่น่าสนใจมาก่อน แล้วไปเทียบราคาสดเอง ค่อยตัดสินใจแทง

🧭 ที่มาข้อมูล — ไม่ต้องขูด HTML หน้า /en/values
   ฟีด getrs.php ที่ใช้อยู่มีครบทุกคอลัมน์ของหน้านั้นแล้ว:
     Prob% = Pred_1/Pred_X/Pred_2 · Coef = best_odd_1/X/2 · ประตูเฉลี่ย = goalsavg
     สูง/ต่ำ = pr_over/pr_under + best_over/best_under · สกอร์ที่ทาย = host_sc_pr/guest_sc_pr
     อากาศ = weather_code/high/low

🔎 "Live coef." คืออะไร — ตรวจของจริง 17 ส.ค. 69
   หน้า /en/values ช่องท้ายแถว (class la_prmod) หัวตารางเขียน "Live coef."
   แต่ค่าที่ออกมาเป็น % ไม่ใช่ราคา และตรงสูตร Kelly เป๊ะ 65/65 แถว:
       kelly = (prob/100 * coef - 1) / (coef - 1)
   เช่น prob 37 · coef 8.00 -> (0.37*8-1)/7 = 0.28 = ที่หน้าเว็บโชว์ "28%"
   ราคาสดของจริงอยู่หน้า today (class "lscrsp lcurodd") และมีเฉพาะคู่ที่เตะไปแล้ว
   ตรวจ 2 รอบ: 34 ช่อง มีค่าจริง 1 ช่อง (คู่ที่กำลังเล่นนาที 84) · อีกรอบ 32 ช่อง "-" หมด
   -> ใบก่อนเกมไม่มีทางมี Live coef. ได้ ของมันยังไม่เกิด · คู่ที่เตะแล้วดึงได้จริง (--live)

⚖️ ห้ามมีเลขที่ไม่เคยวัด
   ทุก % บนการ์ดวัดจาก fb_hist.jsonl 97,239 คู่จบแล้ว (ตัดบอลถ้วยเหลือ 90,842) — 17 ส.ค. 69

usage:
  python fb_pick.py --dry                    # ดูการ์ด ไม่ส่ง
  python fb_pick.py --type over,under        # เลือกประเภท
  python fb_pick.py --live                   # แนบราคาสดของคู่ที่เตะไปแล้ว
  python fb_pick.py --day 2026-08-18         # ดูวันอื่น
  python fb_pick.py --dump ตาราง.txt         # เทตารางกลางทุกคู่ออกมาดู
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from forebet_api import FB_HOME, FB_UA, fb_fetch_day  # noqa: E402

SEEN = os.path.join(HERE, "fb_pick_seen.json")
LOG = os.path.join(HERE, "fb_pick_log.jsonl")
DEPLOY_FILE = r"D:\Projects\t.seeedz\PIKTAX\.deployId"
KEY_FILE = r"D:\Projects\.gas-creds\piktax-admin-key.txt"
BKK = timezone(timedelta(hours=7))

# ── กฎหาคู่ = กฎที่เจ้าของส่งมาเอง · ตัวเลขข้างล่างวัดจากกฎ *นั้นเป๊ะๆ* ───────
#
# ผลวัด (fb_val_bt2.py · 90,842 คู่ ตัดบอลถ้วย):
#   1X2  prob>=50 + ไม่กระจาย + ตรงทาง + Coef 1.60-2.10   n=8,584  52.7%  -5.4%  23.5/วัน
#   สูง  gavg>=2.70 + สูง>=70% + ราคา 1.60-2.10           n=3,489  56.7%  +1.1%   9.6/วัน
#   ต่ำ  gavg<=2.20 + ต่ำ>=65% + ราคา 1.60-2.10           n=6,039  53.7%  -3.7%  16.5/วัน
#   สกอร์ ตรงทาง + ห่าง 2 ลูก                             n=23,791  8.5%  วัดไม่ได้ 65.2/วัน
#                                                (ลูกรวมตรงเป๊ะ 20.9%)
#
# 📌 ข้อที่กฎ "ช่วยจริง":
#     · prob>=50%           45.8% -> 57.6%  (ช่วยแรงสุด)
#     · ห่าง 2 ลูก > 1 ลูก  จริง: ห่าง0=28.6 · 1=48.1 · 2=52.3 · 3=67.3%
# 📌 ข้อที่วัดแล้ว "ไม่มีผล":
#     · ข้ามคู่กระจาย       28,554 -> 28,554 คู่ (0 คู่ถูกตัด — prob>=50 ตัดให้หมดแล้ว)
#     · 1X2 ตรงทางกับสกอร์  28,554 -> 28,545 คู่ (ตัดได้ 9 คู่ Forebet เกือบไม่เคยขัดตัวเอง)
# 📌 ข้อที่วัดแล้ว "ทำให้แย่ลง":
#     · Coef 1.60-2.10      -1.0% -> -5.4%  ช่วงนี้เจ้ามือรีดน้ำหนักที่สุด
#                           ถ้าเอาเพดานออก (Coef>=1.60 ไม่จำกัดบน) = +1.0%
#     · สกอร์ห่าง 2 ลูก     สกอร์เป๊ะแย่ลง 12.5% (ห่าง1) -> 8.5% (ห่าง2)
RULES = {
    "1x2": dict(
        tag="1X2", emoji="🎯", name="1X2",
        n=8584, hit=52.7, roi=-5.4, per_day=23.5,
        cond="ทาย>=50% · ไม่กระจาย · ตรงทางกับสกอร์ · Coef 1.60-2.10",
        warn="⚠️ กฎชุดนี้วัดแล้วกำไร/ใบ -5.4% — ตัวถ่วงคือเพดาน Coef 2.10 (ถอดออกเป็น +1.0%)",
    ),
    "over": dict(
        tag="OV", emoji="🔺", name="บอลสูง",
        n=3489, hit=56.7, roi=+1.1, per_day=9.6,
        cond="ประตูเฉลี่ย>=2.70 · สูง>=70% · ราคา 1.60-2.10",
        warn="⚠️ +1.1% เป็นกำไรบางมาก คลาด ±0.8% — ใช้สแกน อย่าลงหนัก",
    ),
    "under": dict(
        tag="UN", emoji="🔻", name="บอลต่ำ",
        n=6039, hit=53.7, roi=-3.7, per_day=16.5,
        cond="ประตูเฉลี่ย<=2.20 · ต่ำ>=65% · ราคา 1.60-2.10",
        warn="🛑 ลองมา 17 เส้น ติดลบทุกเส้น (ดีสุด -3.6%) — ใบนี้ดูเฉยๆ",
    ),
    "score": dict(
        tag="SC", emoji="🎱", name="สกอร์รวม",
        n=23791, hit=8.5, roi=None, per_day=65.2,
        cond="1X2 ตรงทางกับสกอร์ · ห่าง 2 ลูก",
        warn="🚫 คลังไม่มีเรทสกอร์เป๊ะ -> วัดกำไรไม่ได้ ห้ามคิดเองว่าคุ้ม",
    ),
}
TOT_HIT = 20.9   # ลูกรวมตรงเป๊ะ ในกลุ่มเดียวกัน (ตรงทาง+ห่าง 2 ลูก)
CS_GAP1 = 12.5   # สกอร์เป๊ะ ถ้าห่างแค่ 1 ลูก — ดีกว่าห่าง 2 ลูก

# รหัสอากาศ Forebet — ⚠️ ยังไม่เคยวัด (คลังไม่มีฟิลด์อากาศ) โชว์ดิบๆ ห้ามเอาไปกรอง
WX = {"11": "ฝน", "12": "ฝน", "13": "ฝนปนหิมะ", "14": "หิมะ", "15": "หิมะ", "16": "หิมะ",
      "17": "ลูกเห็บ", "18": "ฝน", "19": "ฝุ่น", "20": "หมอก", "21": "หมอก", "22": "ควัน",
      "23": "ลมแรง", "24": "ลมแรง", "25": "ลูกเห็บ", "26": "เมฆมาก", "27": "เมฆมาก",
      "28": "เมฆมาก", "29": "เมฆบางส่วน", "30": "เมฆบางส่วน", "31": "แจ่มใส", "32": "แดดจัด",
      "33": "แจ่มใส", "34": "แดดจัด", "35": "ฝนปนลูกเห็บ", "36": "ร้อนจัด", "37": "ฝนฟ้าคะนอง",
      "38": "ฝนฟ้าคะนอง", "39": "ฝนฟ้าคะนอง", "40": "ฝนหนัก", "41": "หิมะหนัก",
      "42": "หิมะหนัก", "43": "หิมะหนัก", "44": "เมฆมาก", "45": "ฝน", "46": "หิมะ",
      "47": "ฝนฟ้าคะนอง"}
COEF_LO, COEF_HI = 1.60, 2.10


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _now():
    return datetime.now(BKK)


def _ko(m):
    """DATE_BAH ของฟีดเป็นเวลายุโรป (CEST) — ไทย = +5 ชม."""
    try:
        return datetime.strptime(str(m.get("DATE_BAH") or "").strip(),
                                 "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone(timedelta(hours=2))).astimezone(BKK)
    except ValueError:
        return None


def _wx(m):
    """เอาเฉพาะอุณหภูมิ — ตาราง WX (ฝน/แดด) เดาเอา ยังไม่เคยเทียบของจริง
    (เจอ 'ลูกเห็บ 33°' = แปลรหัสผิดแน่ๆ) ห้ามโชว์ชื่อสภาพอากาศจนกว่าจะยืนยัน"""
    t = m.get("weather_high") or m.get("weather_low")
    return f"{t}°" if t else ""


def _league(m, lg):
    """ชื่อเต็ม 'ประเทศ · ลีก' จากตารางลีกของฟีด · ไม่มีก็ใช้ short_tag"""
    row = (lg or {}).get(str(m.get("league_id") or ""))
    if isinstance(row, (list, tuple)) and len(row) >= 2:
        return f"{str(row[0]).strip()} · {str(row[1]).strip()}"
    return (m.get("short_tag") or "?").strip()


# ── ราคาสด (Live coef. ของจริง) ──────────────────────────────────────────────
def live_odds():
    """ราคาสดจากหน้า today -> {match_id: (o1, oX, o2)} · มีเฉพาะคู่ที่เตะไปแล้ว"""
    try:
        r = requests.get(FB_HOME, timeout=45, headers={
            "User-Agent": FB_UA, "Accept-Language": "en-US,en;q=0.9"})
        if r.status_code != 200:
            print(f"⚠️ ราคาสด: HTTP {r.status_code}", flush=True)
            return {}
        t = re.sub(r"\s+", " ", r.text)
    except Exception as e:
        print(f"⚠️ ราคาสด: {e}", flush=True)
        return {}
    out, slots = {}, 0
    for blk in re.findall(r'class="la_prmod[^"]*">(.*?)</div>\s*</div>', t):
        mid = re.search(r"getHodd\(this,(\d+),'lp'\)", blk)
        if not mid:
            continue
        slots += 1
        od = [_f(x) for x in re.findall(r"<span>\s*([\d.]+)\s*</span>", blk)]
        if len(od) >= 3 and all(od[:3]):
            out[mid.group(1)] = tuple(od[:3])
    print(f"📡 ราคาสด: {len(out)}/{slots} ช่องมีราคา (ที่เหลือยังไม่เตะ = '-')", flush=True)
    return out


# ── ค่ากลางของแต่ละคู่ (ใช้ทั้งคัดใบและเทตาราง) ───────────────────────────────
def mid(m, lg):
    pr = [_i(m.get("Pred_1")), _i(m.get("Pred_X")), _i(m.get("Pred_2"))]
    od = [_f(m.get("best_odd_1")), _f(m.get("best_odd_X")), _f(m.get("best_odd_2"))]
    hpr, gpr = _i(m.get("host_sc_pr")), _i(m.get("guest_sc_pr"))
    d = dict(
        id=str(m.get("id") or ""), ko=_ko(m),
        host=(m.get("HOST_NAME") or "?").strip(), guest=(m.get("GUEST_NAME") or "?").strip(),
        lg=_league(m, lg), short=(m.get("short_tag") or "?").strip(),
        cup=str(m.get("isCup") or "").strip() in ("1", "true", "True"),
        p=pr, o=od, hpr=hpr, gpr=gpr,
        pro=_i(m.get("pr_over")), pru=_i(m.get("pr_under")),
        bo=_f(m.get("best_over")), bu=_f(m.get("best_under")),
        gg=_i(m.get("Pred_gg")), ngg=_i(m.get("Pred_no_gg")),
        ogy=_f(m.get("odds_gg_y")), ogn=_f(m.get("odds_gg_n")),
        gavg=_f(m.get("goalsavg")), wx=_wx(m),
        HS=_i(m.get("Host_SC")), GS=_i(m.get("Guest_SC")),
        ft=str(m.get("comment") or "").strip(),
    )
    if None not in pr:
        d["top"] = pr.index(max(pr))
        d["mx"] = max(pr)
        d["spread"] = max(pr) - min(pr)
        d["gap2"] = max(pr) - sorted(pr, reverse=True)[1]
    if hpr is not None and gpr is not None and "top" in d:
        s = hpr - gpr
        d["agree"] = (d["top"] == 0 and s > 0) or (d["top"] == 1 and s == 0) or (
            d["top"] == 2 and s < 0)
        d["sgap"] = abs(s)
    return d


def _no(stat, why):
    stat[why] = stat.get(why, 0) + 1


def pick_1x2(d, stat):
    if d["cup"]:
        return _no(stat, "บอลถ้วย")
    if "top" not in d or None in d["o"]:
        return _no(stat, "ข้อมูลไม่ครบ")
    if d["top"] == 1:
        return _no(stat, "ทายเสมอ")
    if d["mx"] < 50:
        return _no(stat, "ทาย<50%")
    if d["spread"] <= 10:
        return _no(stat, "คู่กระจาย")
    if not d.get("agree"):
        return _no(stat, "ไม่ตรงทางกับสกอร์")
    c = d["o"][d["top"]]
    if not c or not (COEF_LO <= c <= COEF_HI):
        return _no(stat, f"Coef นอก {COEF_LO}-{COEF_HI}")
    p = dict(d, kind="1x2", coef=c, prob=d["mx"], edge=d["mx"] / 100 * c - 1)
    p["kelly"] = p["edge"] / (c - 1)
    return p


def pick_over(d, stat):
    if d["cup"]:
        return _no(stat, "บอลถ้วย")
    if d["gavg"] is None or d["pro"] is None or d["bo"] is None:
        return _no(stat, "ข้อมูลไม่ครบ")
    if d["gavg"] < 2.70:
        return _no(stat, "ประตูเฉลี่ย<2.70")
    if d["pro"] < 70:
        return _no(stat, "สูง<70%")
    if not (COEF_LO <= d["bo"] <= COEF_HI):
        return _no(stat, f"ราคานอก {COEF_LO}-{COEF_HI}")
    p = dict(d, kind="over", coef=d["bo"], prob=d["pro"], edge=d["pro"] / 100 * d["bo"] - 1)
    p["kelly"] = p["edge"] / (d["bo"] - 1)
    return p


def pick_under(d, stat):
    if d["cup"]:
        return _no(stat, "บอลถ้วย")
    if d["gavg"] is None or d["pru"] is None or d["bu"] is None:
        return _no(stat, "ข้อมูลไม่ครบ")
    if d["gavg"] > 2.20:
        return _no(stat, "ประตูเฉลี่ย>2.20")
    if d["pru"] < 65:
        return _no(stat, "ต่ำ<65%")
    if not (COEF_LO <= d["bu"] <= COEF_HI):
        return _no(stat, f"ราคานอก {COEF_LO}-{COEF_HI}")
    p = dict(d, kind="under", coef=d["bu"], prob=d["pru"], edge=d["pru"] / 100 * d["bu"] - 1)
    p["kelly"] = p["edge"] / (d["bu"] - 1)
    return p


def pick_score(d, stat):
    # ตัดบอลถ้วยเหมือนด่านอื่น — วัดแล้วบอลถ้วยลากสกอร์เป๊ะจาก 8.5% ลงเหลือ 7.3%
    if d["cup"]:
        return _no(stat, "บอลถ้วย")
    if d["hpr"] is None or d["gpr"] is None:
        return _no(stat, "ไม่มีสกอร์ที่ทาย")
    if not d.get("agree"):
        return _no(stat, "ไม่ตรงทางกับสกอร์")
    if d.get("sgap") != 2:
        return _no(stat, "ไม่ได้ห่าง 2 ลูก")
    return dict(d, kind="score", coef=None, prob=None, edge=None, kelly=None)


PICKERS = {"1x2": pick_1x2, "over": pick_over, "under": pick_under, "score": pick_score}


# ── การ์ด (หน้าตาตามที่เจ้าของสั่งมา) ─────────────────────────────────────────
def fmt(p, live=None):
    r = RULES[p["kind"]]
    L = [f"⚽ {p['ko']:%H:%M} · [{r['tag']}] {p['lg']}",
         f"{p['host']} vs {p['guest']}"]

    if p["kind"] == "over":
        L.append(f"🔺 บอลสูง — สูง {p['pro']}% · ประตูเฉลี่ย {p['gavg']:.2f}")
        L.append(f"💱 ราคาสูง {p['coef']:.2f} · ค่าคุ้ม {p['edge'] * 100:+.0f}%")
    elif p["kind"] == "under":
        L.append(f"🔻 บอลต่ำ — ต่ำ {p['pru']}% · ประตูเฉลี่ย {p['gavg']:.2f}")
        L.append(f"💱 ราคาต่ำ {p['coef']:.2f} · ค่าคุ้ม {p['edge'] * 100:+.0f}%")
    elif p["kind"] == "1x2":
        L.append(f"🔢 บ้าน {p['p'][0]}% · เสมอ {p['p'][1]}% · เยือน {p['p'][2]}% "
                 f"(ห่างอันดับ 2 = {p['gap2']} จุด)")
        L.append(f"🎯 ทายไว้ {p['hpr']}-{p['gpr']}")
        L.append(f"💱 Coef {p['coef']:.2f} · ค่าคุ้ม {p['edge'] * 100:+.0f}%")
    else:
        L.append(f"🎯 ทาย {p['hpr']}-{p['gpr']} · ประตูเฉลี่ย {p['gavg']:.2f}")
        L.append(f"📈 ทาย 1X2 กับสกอร์ไปทางเดียวกัน · ห่าง {p['sgap']} ลูก")

    # 📊 บรรทัดวัดจริง — ทุกใบต้องมี (ดูหมายเหตุ RULES ว่าตัวเลขมาจากไหน)
    if r["roi"] is None:
        L.append(f"📊 กลุ่มนี้ย้อนหลัง {r['n']:,} ใบ · สกอร์เป๊ะ {r['hit']:.1f}% "
                 f"· ลูกรวมเป๊ะ {TOT_HIT:.1f}%")
    else:
        L.append(f"📊 กลุ่มนี้ย้อนหลัง {r['n']:,} ใบ · เข้า {r['hit']:.1f}% "
                 f"· กำไร/ใบ {r['roi']:+.1f}%")
    L.append(r["warn"])
    if p.get("wx"):
        L.append(f"🌦 {p['wx']} (ยังไม่เคยวัด โชว์เฉยๆ)")
    lo = (live or {}).get(p["id"])
    if lo:
        L.append(f"🔴 ราคาสด {lo[0]:.2f} / {lo[1]:.2f} / {lo[2]:.2f} (เตะไปแล้ว)")
    return "\n".join(L)


# ── ตารางกลาง (เทค่าดิบทุกคู่ให้ตรวจเอง) ──────────────────────────────────────
DUMP_HEAD = (
    "เวลา  ลีก        คู่                                        "
    "p1/px/p2   o1/ox/o2        ทาย  pro/pru bo/bu      gavg  ผลจริง  ด่านที่ผ่าน"
)


def dump_rows(rows):
    out = [DUMP_HEAD, "─" * len(DUMP_HEAD)]
    for d in rows:
        p = "/".join(str(x if x is not None else "-") for x in d["p"])
        o = "/".join(f"{x:.2f}" if x else "  -  " for x in d["o"])
        pred = f"{d['hpr']}-{d['gpr']}" if d["hpr"] is not None else " - "
        real = (f"{d['HS']}-{d['GS']}" if d["ft"] == "FT" and d["HS"] is not None else "  -  ")
        tags = "".join([
            "OV" if d.get("_ok_over") else "  ",
            "UN" if d.get("_ok_under") else "  ",
            "1X" if d.get("_ok_1x2") else "  ",
            "SC" if d.get("_ok_score") else "  ",
        ])
        bo = f"{d['bo']:.2f}" if d["bo"] else "  -  "
        bu = f"{d['bu']:.2f}" if d["bu"] else "  -  "
        gavg = f"{d['gavg']:.2f}" if d["gavg"] is not None else " -  "
        prou = f"{str(d['pro'] if d['pro'] is not None else '-'):>3s}/" \
               f"{str(d['pru'] if d['pru'] is not None else '-'):<3s}"
        out.append(
            f"{d['ko']:%H:%M} {d['short'][:9]:9s} "
            f"{(d['host'] + ' v ' + d['guest'])[:41]:41s} "
            f"{p:>10s} {o:>17s} {pred:>4s} {prou} "
            f"{bo:>5s}/{bu:<5s} {gavg:>4s} {real:>6s}  {tags}"
        )
    return "\n".join(out)


# ── ส่ง (ก๊อปทางส่งจาก fb_value — ตั้งใจซ้ำ ไม่ import ข้ามไฟล์) ───────────────
def _piktax_url():
    u = (os.environ.get("PIKTAX_STATE_URL") or "").split("?")[0]
    if not u and os.path.exists(DEPLOY_FILE):
        u = ("https://script.google.com/macros/s/"
             + open(DEPLOY_FILE, encoding="utf-8").read().strip().split()[0] + "/exec")
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


def send(p, live=None, dry=False):
    text = fmt(p, live)
    if dry:
        print("─" * 52 + "\n" + text, flush=True)
        return True
    url, key = _piktax_url(), _admin_key()
    if not url or not key:
        print("⚠️ ไม่มี PIKTAX_STATE_URL / PIKTAX_ADMIN_KEY", flush=True)
        return False
    try:
        # 🚫 ห้ามใช้ f5alert — นั่นเขียนชีต FABEL5 ของเครื่องเตือนสด จะทำเส้นฐานเพี้ยน
        r = requests.get(url, params={"admin": key, "action": "notify", "text": text},
                         timeout=90)
    except Exception as e:
        print(f"⚠️ ส่งไม่ผ่าน: {e}", flush=True)
        return False
    return r.status_code == 200


def log(p):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(
                sent=_now().isoformat(timespec="seconds"), kind=p["kind"], id=p["id"],
                ko=p["ko"].isoformat(timespec="minutes"), lg=p["lg"],
                host=p["host"], guest=p["guest"], prob=p["prob"], coef=p["coef"],
                edge=(round(p["edge"], 3) if p["edge"] is not None else None),
                claim=RULES[p["kind"]]["hit"],
            ), ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_seen():
    try:
        return set(json.load(open(SEEN, encoding="utf-8")).get(date.today().isoformat()) or [])
    except Exception:
        return set()


def save_seen(ids):
    try:
        json.dump({date.today().isoformat(): sorted(ids)},
                  open(SEEN, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ เขียน seen ไม่ได้: {e}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="พิมพ์เฉยๆ ไม่ส่ง")
    ap.add_argument("--type", default="over,under,1x2,score", help="1x2,over,under,score")
    ap.add_argument("--lead", type=int, default=20, help="ต้องเหลืออีกกี่นาทีขึ้นไป")
    ap.add_argument("--lead-max", type=int, default=0, help="เพดานล่วงหน้า นาที (0=ทั้งวัน)")
    ap.add_argument("--max", type=int, default=4, help="ส่งได้มากสุดกี่ใบต่อประเภท")
    ap.add_argument("--live", action="store_true", help="แนบราคาสดของคู่ที่เตะไปแล้ว")
    ap.add_argument("--day", default="", help="ดูวันอื่น YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="ไม่สนไฟล์ seen")
    ap.add_argument("--dump", default="", help="เทตารางกลางทุกคู่ลงไฟล์นี้")
    a = ap.parse_args()

    kinds = [k.strip() for k in a.type.split(",") if k.strip() in PICKERS]
    if not kinds:
        print("⚠️ --type ต้องเป็น 1x2 / over / under / score", flush=True)
        return 1

    day = a.day.strip() or _now().date().isoformat()
    fb, lgs = fb_fetch_day(day, markets=["1x2", "uo"])
    if not fb:
        print("⏸ ดึงฟีดไม่ได้", flush=True)
        return 1

    live = live_odds() if a.live else {}
    seen = set() if a.all else load_seen()
    now = _now()
    mids = [mid(m, lgs) for m in fb.values()]
    mids = [d for d in mids if d["ko"]]
    mids.sort(key=lambda d: d["ko"])

    out = {}
    for k in kinds:
        stat, got = {}, []
        for d in mids:
            if not a.dump:
                if d["ko"] <= now + timedelta(minutes=a.lead):
                    _no(stat, "จวนเตะ/เตะแล้ว")
                    continue
                if a.lead_max and d["ko"] > now + timedelta(minutes=a.lead_max):
                    _no(stat, "ยังอีกไกล")
                    continue
            p = PICKERS[k](d, stat)
            if isinstance(p, dict):
                d[f"_ok_{k}"] = True
                if p["id"] and f"{k}:{p['id']}" not in seen:
                    got.append(p)
        got.sort(key=lambda x: (-(x["edge"] or 0), x["ko"]))
        out[k] = got
        print(f"\n═══ {RULES[k]['emoji']} {RULES[k]['name']} — ผ่านด่าน {len(got)} ใบ "
              f"(คลังบอก {RULES[k]['per_day']:.1f} ใบ/วัน)", flush=True)
        if stat:
            print("   ตกด่าน: " + " · ".join(
                f"{x} {y}" for x, y in sorted(stat.items(), key=lambda kv: -kv[1])[:6]),
                flush=True)

    if a.dump:
        txt = dump_rows(mids)
        with open(a.dump, "w", encoding="utf-8") as f:
            f.write(f"ตารางกลาง Forebet วันที่ {day} · {len(mids)} คู่\n"
                    f"p=Prob% · o=Coef · ทาย=สกอร์ที่ Forebet ทาย · pro/pru=%สูง/ต่ำ\n"
                    f"bo/bu=ราคาสูง/ต่ำ · gavg=ประตูเฉลี่ย · ด่านที่ผ่าน=OV/UN/1X/SC\n\n"
                    + txt + "\n")
        print(f"\n📄 เทตารางลง {a.dump} ({len(mids)} คู่)", flush=True)
        return 0

    for k in kinds:
        for p in out[k][:a.max]:
            if send(p, live, dry=a.dry):
                seen.add(f"{k}:{p['id']}")
                if not a.dry:
                    log(p)
    if not a.dry:
        save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())

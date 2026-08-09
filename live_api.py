# -*- coding: utf-8 -*-
"""live_api.py — สกอร์สด + สกอร์ครึ่งแรก จาก LiveScore แล้วต่อเข้ากับรายการของ Forebet

ทำไมต้องมีไฟล์นี้:
  Forebet ไม่ให้ข้อมูลระหว่างเกมเลย (พิสูจน์ 8 ส.ค. 69 · วัด 1,342 คู่ 2 วัน)
    ช่อง comment มีแค่ None/FT/Postp./Pen./Delayed/Cancl./AET — ไม่มีเลขนาทีสักตัว
    สกอร์ครึ่งแรกโผล่พร้อม FT เท่านั้น · tp=live คืน [[],[]] ตลอด
  → FABEL5 เลยยิงไม่ได้เลยแม้แต่ใบเดียว ไม่ใช่เพราะไม่มีบอล แต่เพราะไม่มีข้อมูล
  ตัวนี้เอา "นาที + สกอร์ครึ่งแรก" มาจาก LiveScore ส่วนสมองคัด (Pred_*, fb_trust) ยังเป็นของ Forebet เหมือนเดิม

⚖️ ด่านจับคู่เป็นแบบ fail-closed — จับไม่มั่นใจ = ทิ้ง ไม่เตือน
  เคยพังมาแล้วตอน 7m↔Nowgoal (จับด้วยเวลาคิกออฟล้วน → ชน 24/35 + จับผิดคู่)
  วัดจริง 4 วัน 703 คู่: ลีกที่ LiveScore มีถ่ายทอด จับติด 80-100%
  ที่หลุดคือลีกที่ LiveScore ไม่มีเลย (เอฟเอคัพรอบคัดเลือก, ลีกเยาวชน, ลีกภูมิภาค) — ไม่ใช่จับพลาด

⚠️ เอาเฉพาะคู่ที่ Forebet มีในรายการ ไม่ดึงทั้งกระบิ
"""
import collections
import datetime as dt
import difflib
import json
import os
import re
import unicodedata

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
JOINLOG = os.path.join(HERE, "fb_join_log.jsonl")   # ไว้สุ่มตาดูว่าจับคู่ถูกจริงไหม

LS_URL = "https://prod-cdn-mev-api.livescore.com/v1/api/app/date/soccer/{day}/0"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# ── ด่านจับคู่: ตัวเลขพวกนี้บีบไว้แน่นตั้งใจ ยอมพลาดดีกว่าจับผิด ──────────────
NAME_MIN = 0.80      # ชื่อต้องเหมือนกัน ≥80% ทั้งสองฝั่ง (ที่ 0.70 เคยจับผิดคู่มาแล้ว)
NAME_GAP = 0.10      # ต้องทิ้งห่างคู่อันดับ 2 อย่างน้อยเท่านี้ ไม่งั้นแปลว่าก้ำกึ่ง
TIME_WIN = 20        # ยอมให้เวลาคิกออฟเพี้ยนได้กี่นาที

# คำท้ายชื่อทีมที่แต่ละเว็บใส่ไม่เหมือนกัน — ตัดทิ้งก่อนเทียบ
DROP = {"fc", "afc", "cf", "sc", "ac", "if", "fk", "sk", "bk", "cd", "ud", "us", "as",
        "ss", "club", "calcio", "de", "the", "team", "ii", "b", "1"}

# สถานะที่แปลว่า "ไม่ได้กำลังเตะ"
DEAD = {"ns", "ft", "aet", "ap", "pen.", "pen", "postp.", "canc.", "cancl.", "aban.",
        "susp.", "awarded", "delayed", "int.", "tbd", "-", ""}


def norm(s):
    """ชื่อทีมแบบเทียบได้ — ตัดวรรณยุกต์/เครื่องหมาย/คำท้ายทิ้ง"""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    return " ".join(t for t in re.split(r"[^a-z0-9]+", s) if t and t not in DROP)


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _i(v):
    try:
        return int(str(v).strip())
    except Exception:
        return None


def parse_minute(eps):
    """แปลงสถานะ LiveScore เป็นนาที · None = ไม่ได้กำลังเตะ

    ที่เห็นจริง: "56'" · "90+3'" · "HT" · "FT" · "NS" · "Postp."
    "45+2'" คืน 45 — ยังอยู่ทดครึ่งแรก สกอร์ครึ่งแรกยังไม่นิ่ง (check_rules จะกันเองเพราะยังไม่มี Trh)
    """
    s = str(eps or "").strip()
    if s.lower() in DEAD:
        return None
    if s.upper() in ("HT", "HALF TIME", "HALF-TIME"):
        return 45
    m = re.match(r"^(\d{1,3})\s*(?:\+\s*\d+)?\s*'?$", s)
    if m:
        n = int(m.group(1))
        return n if 0 <= n <= 130 else None
    return None


# ── ดึงจาก LiveScore ──────────────────────────────────────────────────────────
def ls_fetch(day, timeout=40):
    """day = 'YYYYMMDD' · คืน list ของคู่ในวันนั้น (เวลาเป็น UTC)"""
    j = requests.get(LS_URL.format(day=day), headers=UA, timeout=timeout).json()
    out = []
    for st in j.get("Stages", []):
        lg, cn = st.get("Snm"), st.get("Cnm")
        for e in st.get("Events", []):
            try:
                h = e["T1"][0]["Nm"]
                a = e["T2"][0]["Nm"]
                ko = dt.datetime.strptime(str(e.get("Esd")), "%Y%m%d%H%M%S")
            except Exception:
                continue
            out.append({
                "eid": e.get("Eid"), "h": h, "a": a, "lg": lg, "cn": cn, "ko": ko,
                "eps": e.get("Eps"),
                "HS": _i(e.get("Tr1")), "GS": _i(e.get("Tr2")),
                "HH": _i(e.get("Trh1")), "GH": _i(e.get("Trh2")),
                "nh": norm(h), "na": norm(a),
            })
    return out


def ls_pool(center=None):
    """ดึง 3 วัน (เมื่อวาน/วันนี้/พรุ่งนี้) — คู่ดึกเวลายุโรปไปโผล่คนละวันใน UTC"""
    c = center or dt.date.today()
    pool = []
    for k in (-1, 0, 1):
        d = (c + dt.timedelta(days=k)).strftime("%Y%m%d")
        try:
            pool += ls_fetch(d)
        except Exception as e:
            print(f"⚠️ LiveScore {d}: {e}", flush=True)
    return pool


# ── ด่านจับคู่ ────────────────────────────────────────────────────────────────
def _fb_time(m):
    try:
        return dt.datetime.strptime(str(m.get("DATE_BAH"))[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _learn_offset(fb, pool, fallback=-2.0):
    """หาว่านาฬิกา Forebet ต่างจาก LiveScore กี่ชั่วโมง — วัดเอาจากคู่ที่ชื่อตรงเป๊ะ

    ไม่ฮาร์ดโค้ด เพราะยุโรปเปลี่ยนเวลาปีละ 2 ครั้ง (CEST +2 / CET +1)
    """
    idx = collections.defaultdict(list)
    for g in pool:
        idx[g["nh"] + "|" + g["na"]].append(g)
    deltas = []
    for m in fb.values():
        t = _fb_time(m)
        if not t:
            continue
        c = idx.get(norm(m.get("HOST_NAME")) + "|" + norm(m.get("GUEST_NAME")))
        if c and len(c) == 1:
            deltas.append(round((c[0]["ko"] - t).total_seconds() / 900) * 0.25)   # ปัดลง 15 นาที
    if len(deltas) < 5:
        return fallback, len(deltas)
    return collections.Counter(deltas).most_common(1)[0][0], len(deltas)


def join(fb, pool=None, log=True):
    """ต่อรายการ Forebet เข้ากับ LiveScore

    fb   = {id: ฟิลด์ Forebet}  (ผลจาก fb_fetch_day)
    คืน (joined, stat) · joined = {id: แถวของ LiveScore}
    """
    pool = ls_pool() if pool is None else pool
    off, nseen = _learn_offset(fb, pool)

    exact = collections.defaultdict(list)
    tbuck = collections.defaultdict(list)
    for g in pool:
        exact[g["nh"] + "|" + g["na"]].append(g)
        tbuck[g["ko"].strftime("%Y%m%d%H%M")].append(g)

    joined, stat = {}, collections.Counter()
    rows = []
    for mid, m in fb.items():
        H, A = norm(m.get("HOST_NAME")), norm(m.get("GUEST_NAME"))
        c = exact.get(H + "|" + A)
        if c and len(c) == 1:
            joined[mid] = c[0]
            stat["ชื่อตรงเป๊ะ"] += 1
            rows.append((mid, m, c[0], 1.0, "exact"))
            continue

        t = _fb_time(m)
        if t is None:
            stat["ไม่มีเวลาคิกออฟ"] += 1
            continue
        t = t + dt.timedelta(hours=off)
        cand = []
        for o in range(-TIME_WIN, TIME_WIN + 1, 5):
            cand += tbuck.get((t + dt.timedelta(minutes=o)).strftime("%Y%m%d%H%M"), [])
        if not cand:
            stat["LiveScore ไม่มีคู่ในเวลานั้น"] += 1
            continue

        sc = sorted(((min(_sim(H, g["nh"]), _sim(A, g["na"])), g) for g in cand),
                    key=lambda x: -x[0])
        top = sc[0][0]
        second = sc[1][0] if len(sc) > 1 else 0.0
        if top >= NAME_MIN and top - second >= NAME_GAP:
            joined[mid] = sc[0][1]
            stat["เวลา+ชื่อ"] += 1
            rows.append((mid, m, sc[0][1], round(top, 2), "time"))
        else:
            stat["ชื่อไม่เหมือนพอ"] += 1

    stat["_offset_hr"] = off
    stat["_offset_from"] = nseen
    if log and rows:
        _log_join(rows)
    return joined, stat


def _log_join(rows):
    """จดคู่ที่ต่อได้ ไว้ให้สุ่มตาดูว่าจับถูกจริงไหม"""
    now = dt.datetime.now().isoformat(timespec="seconds")
    try:
        with open(JOINLOG, "a", encoding="utf-8") as f:
            for mid, m, g, sc, how in rows:
                f.write(json.dumps({
                    "at": now, "id": mid, "how": how, "score": sc,
                    "fb": f"{m.get('HOST_NAME')} v {m.get('GUEST_NAME')}",
                    "ls": f"{g['h']} v {g['a']}",
                    "fb_lg": m.get("short_tag"), "ls_lg": g["lg"],
                    "eps": g["eps"], "HT": f"{g['HH']}-{g['GH']}", "SC": f"{g['HS']}-{g['GS']}",
                }, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as e:
        print(f"⚠️ จด join log ไม่ได้: {e}", flush=True)


def reached_ht(eps):
    """ครึ่งแรกจบแล้วจริงหรือยัง

    ⚠️ สำคัญ: LiveScore เติม Trh1/Trh2 ตั้งแต่ยังเตะครึ่งแรกอยู่ (เห็นจริง นาที 43 ขึ้น "ครึ่งแรก 1-0")
    ถ้าไม่กันตรงนี้ นาที 45+2 จะถูกอ่านเป็น "สกอร์ครึ่งแรก" ทั้งที่ยังยิงกันได้อีก
    → เลขทุกตัวใน fb_trust วัดจาก "พักครึ่ง → จบ" เท่านั้น ใช้สกอร์ที่ยังไม่นิ่งไม่ได้
    """
    s = str(eps or "").strip()
    if s.upper() in ("HT", "HALF TIME", "HALF-TIME"):
        return True
    n = parse_minute(s)
    return n is not None and n >= 46


def apply_live(m, g):
    """ทับสกอร์/สกอร์ครึ่งแรกของ Forebet ด้วยของ LiveScore แล้วแปะนาทีไว้ที่ _min

    ทับเฉพาะ 4 ช่องนี้ — ช่องทำนาย (Pred_*, pr_over, goalsavg) ยังเป็นของ Forebet ทั้งหมด
    ยังไม่ถึงพักครึ่ง = ยกสกอร์ครึ่งแรกเป็น None ให้ check_rules ตีตกเอง (มีด่าน HH is None อยู่แล้ว)
    """
    m = dict(m)
    ht = reached_ht(g["eps"])
    m["Host_SC"], m["Guest_SC"] = g["HS"], g["GS"]
    m["Host_SC_HT"] = g["HH"] if ht else None
    m["Guest_SC_HT"] = g["GH"] if ht else None
    m["_min"] = parse_minute(g["eps"])
    m["_ht"] = ht
    m["_eps"] = g["eps"]
    m["_ls"] = f"{g['h']} v {g['a']}"
    m["_ls_lg"] = g["lg"]
    m["_ko"] = g.get("ko")          # เวลาเตะจริง (UTC) — ใบเตือนเอาไป +7 เป็นเวลาไทย
    return m


if __name__ == "__main__":
    # เทสต์: ดึง Forebet วันนี้ → ต่อ LiveScore → โชว์คู่ที่กำลังเตะ
    import forebet_api as fbapi

    day = dt.date.today().isoformat()
    fbm, _ = fbapi.fb_fetch_day(day, markets=("1x2", "uo", "bts"), sess=fbapi.fb_session())
    jo, st = join(fbm)
    off = st.pop("_offset_hr", None)
    nfrom = st.pop("_offset_from", 0)
    print(f"\n🔗 Forebet {len(fbm)} คู่ → ต่อติด {len(jo)} "
          f"· นาฬิกาต่างกัน {off:+g} ชม. (วัดจาก {nfrom} คู่ที่ชื่อตรงเป๊ะ)")
    print("   " + " · ".join(f"{k} {v}" for k, v in st.most_common()))

    live = [(mid, apply_live(fbm[mid], g)) for mid, g in jo.items()
            if parse_minute(g["eps"]) is not None]
    print(f"\n🔴 กำลังเตะ {len(live)} คู่")
    for mid, m in sorted(live, key=lambda x: -(x[1]["_min"] or 0))[:25]:
        print(f"   {str(m['_eps']):>6} · {m.get('HOST_NAME')} {m['Host_SC']}-{m['Guest_SC']} "
              f"{m.get('GUEST_NAME')} (ครึ่งแรก {m['Host_SC_HT']}-{m['Guest_SC_HT']}) · {m['_ls_lg']}")

# -*- coding: utf-8 -*-
"""สถิติออโต้รายวัน — จดใบที่หน้า "ด่านแรก" แนะนำไว้ก่อนบอลเตะ แล้วเกรดเองเมื่อผลออก

ทำไมต้องมี: เลขทุกตัวบนหน้ารายงานมาจาก **คลังย้อนหลัง 90,842 คู่** (อดีตล้วน)
            ตัวนี้คือ "ของจริงตั้งแต่วันที่เริ่มจด" ไว้เทียบว่าคลังยังใช้ได้อยู่ไหม

ที่เก็บ: `stats/picks.jsonl` — **คอมมิตกลับเข้า repo** โดย workflow fb-report
        (CLAUDE.md: ของที่เก็บไว้วัดทีหลัง ห้ามค้างบน runner — หายทุกรอบ)

1 บรรทัด = 1 ใบ (1 คู่ที่ผ่านด่าน >=1 อัน หรือค่าคุ้ม >=30%)
🔒 กฎกันโกงตัวเอง: จดเฉพาะคู่ที่ **ยังไม่เตะ** ตอนที่รายงานสร้าง
   ห้ามจดย้อนหลัง ไม่งั้นเท่ากับเลือกใบทีหลังตอนรู้ผลแล้ว = เลขสวยแต่ใช้ไม่ได้
"""
import json
import os
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "stats")
PATH = os.path.join(DIR, "picks.jsonl")
BKK = timezone(timedelta(hours=7))

GRADE_AFTER_H = 3.5   # หลังเที่ยงคืนของวันนั้นอีกกี่ชม.ถึงเริ่มเกรด (เผื่อคู่ดึกสุดจบ)
VOID_AFTER_D = 3      # เกินกี่วันยังไม่มีผล = เลื่อน/ยกเลิก → ตัดทิ้ง ไม่นับเข้าสถิติ
KEEP_DAYS = 14        # โชว์รายวันย้อนหลังกี่วันบนหน้า

# เส้นเทียบจากคลัง (fb_val_bt2.py · 90,842 คู่ ตัดบอลถ้วย)
#   lv3/lv2 = บล็อก COMBO ใน fb_report.py · ev30/dog30 = บล็อก VALCUM/DOG
#   over/under/1x2/score ดึงจาก RULES ตอนรัน (จะได้ไม่มีเลขซ้ำสองที่แล้วหลุดกัน)
GROUPS = [
    ("lv3",   "🟢 น่าลงทุนสุด — OV ป้ายเดียวโดดๆ", 1995, 57.0, +2.0),
    ("lv2",   "🟡 พอไปดูได้ — OV + SC",             1203, 56.8, +0.8),
    ("over",  "🔺 OV ทุกใบ",                        None, None, None),
    ("under", "🔻 UN",                              None, None, None),
    ("1x2",   "🎯 1X",                              None, None, None),
    ("score", "🎱 SC — สกอร์เป๊ะ",                   None, None, None),
    ("ev30",  "💰 ค่าคุ้ม >=30%",                    4630, 34.4, +30.3),
    ("dog30", "🐶 รอง + ค่าคุ้ม >=30%",              2092, 32.5, +65.6),
]


def _base(key):
    """เส้นเทียบจากคลัง (n, hit%, roi%)"""
    for k, _nm, n, hit, roi in GROUPS:
        if k == key:
            if n is not None:
                return n, hit, roi
            from fb_pick import RULES
            r = RULES[key]
            return r["n"], r["hit"], r["roi"]
    return None, None, None


def _name(key):
    for k, nm, *_ in GROUPS:
        if k == key:
            return nm
    return key


# ── แฟ้ม ──────────────────────────────────────────────────────────────────────
def _load():
    if not os.path.exists(PATH):
        return []
    out = []
    with open(PATH, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                pass          # บรรทัดพังข้ามไป ดีกว่าพาทั้งหน้าล้ม
    return out


def _save(recs):
    os.makedirs(DIR, exist_ok=True)
    recs.sort(key=lambda r: (r.get("d", ""), r.get("ko", ""), str(r.get("id"))))
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, PATH)


# ── จด ────────────────────────────────────────────────────────────────────────
def record(rows, level_fn, edge_fn, now=None):
    """จดใบของคู่ที่ยังไม่เตะ · คู่เดิมจดครั้งเดียว (กันรอบถัดไปจดซ้ำ)"""
    now = now or datetime.now(BKK)
    recs = _load()
    have = {(r.get("d"), str(r.get("id"))) for r in recs}
    add = 0
    for d in rows:
        ko = d.get("ko")
        if not ko or ko <= now:
            continue                                   # เตะไปแล้ว = ไม่จด
        tg = [k for k in ("over", "under", "1x2", "score") if d.get(f"_ok_{k}")]
        ev = edge_fn(d)
        ev = -1.0 if ev is None else round(ev, 1)
        if not tg and ev < 30:
            continue                                   # ไม่ผ่านอะไรเลย = ไม่ต้องจด
        key = (ko.date().isoformat(), str(d["id"]))
        if key in have:
            continue
        oo = [x for x in d["o"] if x]
        top = d.get("top")
        recs.append({
            "d": key[0], "id": key[1], "ko": f"{ko:%H:%M}",
            "lg": d.get("short") or "", "h": d.get("host") or "", "a": d.get("guest") or "",
            "lv": level_fn(d), "tg": tg, "ev": ev,
            "dog": bool(top is not None and len(oo) == 3 and d["o"][top] == max(oo)),
            "top": top, "o": [round(x, 2) if x else None for x in d["o"]],
            "bo": d.get("bo"), "bu": d.get("bu"),
            "pr": [d.get("hpr"), d.get("gpr")],
            "at": f"{now:%Y-%m-%d %H:%M}",
            "r": None,
        })
        have.add(key)
        add += 1
    if add:
        _save(recs)
    print(f"📝 สถิติออโต้: จดใหม่ {add} ใบ (ทั้งแฟ้ม {len(recs)} ใบ)")
    return add


# ── เกรด ──────────────────────────────────────────────────────────────────────
def grade(max_days=2, now=None):
    """เกรดวันที่ผ่านไปแล้ว · ใช้ฟีด 1x2 วันละ 1 ครั้ง (ฟีดนี้ตัวเดียวที่มีสกอร์จริง)"""
    now = now or datetime.now(BKK)
    recs = _load()
    pend = {}
    for r in recs:
        if r.get("r") is None:
            pend.setdefault(r["d"], []).append(r)
    if not pend:
        return 0

    ready = []
    for day in sorted(pend):
        try:
            dd = datetime.fromisoformat(day).replace(tzinfo=BKK)
        except Exception:
            continue
        if now >= dd + timedelta(days=1, hours=GRADE_AFTER_H):
            ready.append(day)
    if not ready:
        print(f"⏳ สถิติออโต้: รอผลอยู่ {sum(len(v) for v in pend.values())} ใบ (ยังไม่ถึงเวลาเกรด)")
        return 0

    from fb_pick import mid
    from forebet_api import fb_fetch_day
    got = 0
    for day in ready[:max_days]:
        try:
            fb, lgs = fb_fetch_day(day, markets=["1x2"])
        except Exception as e:
            print(f"⚠️ เกรด {day} ไม่ได้ ({type(e).__name__}: {e}) → ไว้รอบหน้า")
            continue
        res = {}
        for m in fb.values():
            g = mid(m, lgs)
            res[str(g["id"])] = g
        old = (now.date() - datetime.fromisoformat(day).date()).days > VOID_AFTER_D
        nv = 0
        for r in pend[day]:
            g = res.get(str(r["id"]))
            if g and g["ft"] == "FT" and g["HS"] is not None and g["GS"] is not None:
                r["r"] = [g["HS"], g["GS"]]
                got += 1
            elif old:
                r["r"] = "void"       # เลื่อน/ยกเลิก — ตัดทิ้ง ไม่ให้ค้างถ่วงตัวเลข
                nv += 1
        print(f"📊 เกรด {day}: ได้ผล {got} ใบ" + (f" · ตัดทิ้ง(เลื่อน) {nv} ใบ" if nv else ""))
    _save(recs)
    return got


# ── นับ ───────────────────────────────────────────────────────────────────────
def _member(r, key):
    if key == "lv3":
        return r.get("lv") == 3
    if key == "lv2":
        return r.get("lv") == 2
    if key == "ev30":
        return (r.get("ev") or -1) >= 30
    if key == "dog30":
        return (r.get("ev") or -1) >= 30 and r.get("dog")
    return key in (r.get("tg") or [])


def _outcome(r, key):
    """คืน (ถูกไหม, เรทที่ได้) · None = ตัดสินไม่ได้ ข้ามใบนี้"""
    res = r.get("r")
    if not isinstance(res, list) or len(res) != 2:
        return None
    hs, gs = res
    tot = hs + gs
    if key in ("lv3", "lv2", "over"):
        c = r.get("bo")
        return (tot >= 3, c) if c else None
    if key == "under":
        c = r.get("bu")
        return (tot <= 2, c) if c else None
    if key == "score":
        pr = r.get("pr") or [None, None]
        return ((hs, gs) == (pr[0], pr[1]), None) if pr[0] is not None else None
    if key in ("1x2", "ev30", "dog30"):
        top, o = r.get("top"), r.get("o") or []
        if top is None or len(o) != 3 or not o[top]:
            return None
        side = 0 if hs > gs else (1 if hs == gs else 2)
        return (side == top, o[top])
    return None


def tally(recs, key, days=None):
    """คืน dict: n ใบที่เกรดแล้ว · win · hit% · roi% · pend ใบที่รอผล"""
    n = win = pend = 0
    pl = 0.0
    have_coef = 0
    for r in recs:
        if days is not None and r.get("d") not in days:
            continue
        if not _member(r, key):
            continue
        if r.get("r") is None:
            pend += 1
            continue
        oc = _outcome(r, key)
        if oc is None:
            continue                      # void / ข้อมูลไม่พอ
        ok, c = oc
        n += 1
        win += 1 if ok else 0
        if c:
            have_coef += 1
            pl += (c - 1) if ok else -1
    return {
        "n": n, "win": win, "pend": pend,
        "hit": (win / n * 100) if n else None,
        "roi": (pl / have_coef * 100) if have_coef else None,
    }


# ── หน้าเว็บ ──────────────────────────────────────────────────────────────────
def _num(v, plus=False):
    if v is None:
        return '<span class="sn">—</span>'
    c = "up" if v > 0 else ("down" if v < 0 else "")
    return f'<span class="num {c}">{v:+.1f}%</span>' if plus else \
           f'<span class="num {c}">{v:.1f}%</span>'


def stat_html():
    recs = _load()
    if not recs:
        return ""
    alld = sorted({r["d"] for r in recs})
    graded = [r for r in recs if r.get("r") is not None]
    pend_all = sum(1 for r in recs if r.get("r") is None)
    done_days = sorted({r["d"] for r in graded})

    steps = []
    for key, nm, *_ in GROUPS:
        t = tally(recs, key)
        bn, bh, br = _base(key)
        if t["n"] == 0:
            steps.append(
                f'<div class="step"><div class="si"></div><div class="sl"><span>{nm}</span>'
                f'<span class="chip flat">ยังไม่มีใบที่รู้ผล</span></div>'
                f'<div class="sr"><span class="sn">รอผล {t["pend"]} ใบ</span>'
                f'<span class="sn">คลัง {bh:.1f}%'
                + (f" · {br:+.1f}%" if br is not None else "") + "</span></div></div>")
            continue
        # เทียบกับคลัง: ใบยังน้อย = ห้ามสรุป (ตัวเลขเด้งเป็นสิบ % จากใบเดียว)
        if t["n"] < 30:
            chip = '<span class="chip flat">ใบยังน้อย ยังสรุปไม่ได้</span>'
        else:
            mine = t["hit"] if br is None else t["roi"]
            base = bh if br is None else br
            if mine is None:
                chip = '<span class="chip flat">วัดกำไรไม่ได้</span>'
            elif mine >= base:
                chip = '<span class="chip ok">ดีกว่าคลัง</span>'
            else:
                chip = '<span class="chip bad">แย่กว่าคลัง</span>'
        steps.append(
            f'<div class="step"><div class="si"></div>'
            f'<div class="sl"><span>{nm}</span>{chip}</div>'
            f'<div class="sr"><span class="sn">{t["win"]}/{t["n"]} ใบ</span>'
            f'{_num(t["hit"])}{_num(t["roi"], True)}'
            f'<span class="sn">คลัง {bh:.1f}%'
            + (f" · {br:+.1f}%" if br is not None else "") + "</span>"
            + (f'<span class="sn">รอผล {t["pend"]}</span>' if t["pend"] else "")
            + "</div></div>")

    drows = []
    for day in done_days[-KEEP_DAYS:][::-1]:
        one = [r for r in recs if r["d"] == day]
        cells = []
        for key, ic in (("lv3", "🟢"), ("over", "🔺"), ("ev30", "💰")):
            t = tally(one, key)
            cells.append(f'<span class="sn">{ic} '
                         + (f'{t["win"]}/{t["n"]}' if t["n"] else "–") + "</span>")
        ng = sum(1 for r in one if isinstance(r.get("r"), list))
        np_ = sum(1 for r in one if r.get("r") is None)
        drows.append(
            f'<div class="step"><div class="si"></div>'
            f'<div class="sl"><span>{day}</span>'
            f'<span class="sn">{ng} ใบรู้ผล'
            + (f" · รอ {np_}" if np_ else "") + "</span></div>"
            f'<div class="sr">{"".join(cells)}</div></div>')

    tl3 = tally(recs, "lv3")
    return f"""
<section>
  <div><p class="eyebrow">สถิติออโต้ · เริ่มจด {alld[0]} · {len(recs)} ใบ</p>
  <h2>ของจริงตั้งแต่วันที่เริ่มจด</h2>
  <p class="lede">ทุกรอบที่หน้านี้อัพเดท มันจะ<b>จดใบที่ผ่านด่านลงแฟ้มก่อน</b>
    (เฉพาะคู่ที่ยังไม่เตะ) พอวันนั้นจบก็ไปดึงสกอร์จริงมาเกรดเอง —
    ไม่มีใครมาเลือกทีหลังว่าจะนับใบไหน แฟ้มอยู่ใน repo ที่
    <span class="mono">stats/picks.jsonl</span> เปิดตรวจย้อนได้ทุกใบ</p></div>
  <div class="panel"><h3>ของจริง เทียบกับ คลังย้อนหลัง</h3>{"".join(steps)}</div>
  <div class="note"><b>อ่านยังไง:</b> ช่องซ้ายคือ <b>ถูก/ทั้งหมด</b> ของจริง ·
    ถัดมา % เข้า และกำไรต่อใบ · ขวาสุดคือเลขเดิมจากคลัง 90,842 คู่ ไว้เทียบ<br>
    เลขจะเชื่อได้ต่อเมื่อสะสมพอ — ต่ำกว่า <b>30 ใบ</b> ยังไม่ตัดสิน
    ({tl3["n"]} ใบใน 🟢 ตอนนี้) · ที่จริงต้อง <b>300+ ใบ</b> ถึงจะแยกออกว่า
    +2% กับ −2% ต่างกันจริงไหม<br>
    <b>รอผลอยู่ {pend_all} ใบ</b> · ใบที่บอลเลื่อน/ยกเลิก เกิน {VOID_AFTER_D} วันไม่มีผล
    จะถูกตัดทิ้ง ไม่นับเข้าสถิติ</div>
  {'<div class="panel"><h3>รายวัน (ล่าสุด ' + str(KEEP_DAYS) + ' วัน)</h3>'
   + "".join(drows) + "</div>" if drows else ""}
</section>
"""

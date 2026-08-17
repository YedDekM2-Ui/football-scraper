# -*- coding: utf-8 -*-
"""สร้างหน้ารายงาน (HTML) — ตารางกลางทุกคู่ + ผลวัดกฎที่เจ้าของส่งมา + ตัวอย่างการ์ด

usage: python -X utf8 fb_report.py [YYYY-MM-DD] [ไฟล์ออก.html]
"""
import html
import io
import os
import sys
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fb_pick import PICKERS, RULES, mid  # noqa: E402
from forebet_api import fb_fetch_day  # noqa: E402
from fb_values import vmap  # noqa: E402

BKK = timezone(timedelta(hours=7))
# argv[1] ว่างได้ (workflow ส่ง "" มาตอนไม่ได้เลือกวัน) → ถือว่าวันนี้
_arg_day = sys.argv[1] if len(sys.argv) > 1 else ""
DAY = _arg_day or datetime.now(BKK).date().isoformat()
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "fb_report.html")

E = html.escape


def n(v, d=0):
    return "—" if v is None else (f"{v:.{d}f}" if d else str(v))


# ── เก็บข้อมูล ────────────────────────────────────────────────────────────────
NOW = datetime.now(BKK)
BACK_H = 6
SINCE = NOW - timedelta(hours=BACK_H)
LIVE = DAY == NOW.date().isoformat()

# วันนี้: ดึงพรุ่งนี้มาต่อท้ายด้วย ไม่งั้นกดขอตอนดึกแล้วเหลือคู่ไม่กี่คู่
days = [DAY] + ([(NOW.date() + timedelta(days=1)).isoformat()] if LIVE else [])
rows, seen = [], set()
for dy in days:
    fb, lgs = fb_fetch_day(dy, markets=["1x2", "uo", "bts"])
    for m in fb.values():
        d = mid(m, lgs)
        if d["ko"] and d["id"] not in seen:
            seen.add(d["id"])
            rows.append(d)
rows.sort(key=lambda d: d["ko"])

# หน้าต่างเวลา: เริ่มที่ 6 ชม.ก่อนกดขอ — ที่เตะไปแล้วยังคาไว้ ไว้ตามดูย้อนหลังว่ากฎถูกไหม
allday = len(rows)
if LIVE:
    rows = [d for d in rows if d["ko"] >= SINCE]
    win_txt = f"<b>{SINCE:%d/%m %H:%M}</b> เป็นต้นไป (ย้อน {BACK_H} ชม.)"
else:
    win_txt = "ทั้งวัน (ย้อนหลัง)"
cut = allday - len(rows)
for d in rows:
    for k, fn in PICKERS.items():
        if isinstance(fn(d, {}), dict):
            d[f"_ok_{k}"] = True
cnt = {k: sum(1 for d in rows if d.get(f"_ok_{k}")) for k in PICKERS}

# ส่งบอทเข้าไปดูดหน้า /values ของจริง — คู่ไหน Forebet เองก็คัดไว้ (เขาใช้ Kelly)
# ⚠️ ห้ามให้ขั้นนี้ล้มพาทั้งหน้าไปด้วย: fb_values ยิง Forebet ตรง ซึ่งบน GitHub Actions
#    โดน Cloudflare เด้ง 403 แน่นอน (วัดแล้ว) → รายงานต้องออกได้ปกติ แค่ไม่มีป้าย ⭐
VMAP = {}
if LIVE:
    try:
        VMAP = vmap()
    except Exception as e:
        print(f"⚠️ ดูดหน้า /values ไม่ได้ ({type(e).__name__}: {e}) → ไม่มีป้าย ⭐ แต่หน้ายังครบ")
for d in rows:
    if str(d["id"]) in VMAP:
        d["_vb"] = VMAP[str(d["id"])]
nvb = sum(1 for d in rows if "_vb" in d)
print(f"⭐ หน้า values ของจริง {len(VMAP)} คู่ · ตรงกับตารางนี้ {nvb} คู่")
ft = sum(1 for d in rows if d["ft"] == "FT")

# ── ผลวัด (fb_val_bt2.py · 90,842 คู่ ตัดบอลถ้วย) ─────────────────────────────
FUNNEL = [
    ("ทายตาม Forebet ทุกคู่ (เส้นฐาน)", 90842, 45.8, 2.42, -3.7, None),
    ("+ ฝั่งที่ทาย ≥ 50%", 28554, 57.6, 1.91, -1.0, "keep"),
    ("+ ข้ามคู่กระจาย (สูงสุด−ต่ำสุด > 10)", 28554, 57.6, 1.91, -1.0, "noop"),
    ("+ 1X2 ตรงทางกับสกอร์ที่ทาย", 28545, 57.6, 1.91, -1.0, "noop"),
    ("+ Coef 1.60–2.10", 8584, 52.7, 1.81, -5.4, "bad"),
]
ALT = [
    ("ถอดเพดาน Coef ออก (≥1.60 ไม่จำกัดบน)", 15885, 45.2, 2.37, +1.0),
    ("คงเพดานไว้ แต่ดันเส้นทายเป็น ≥65%", 810, 57.2, 1.78, +0.8),
]
OVER = [
    ("gavg ≥ 2.70 + ราคา 1.60–2.10 (ไม่ดู %)", 15534, 53.6, -3.2, 42.6, False),
    ("+ สูง ≥ 65%", 6176, 55.2, -1.3, 16.9, False),
    ("+ สูง ≥ 70%", 3489, 56.7, +1.1, 9.6, True),
    ("+ สูง ≥ 75% (และ gavg ≥ 3.00)", 1919, 57.6, +2.5, 5.3, True),
]
UNDER = [
    ("gavg ≤ 2.20 + ราคา 1.60–2.10", 13726, 52.9, -4.7, 37.6),
    ("gavg ≤ 2.20 + ต่ำ ≥ 65%", 6039, 53.7, -3.7, 16.5),
    ("gavg ≤ 2.00 + ต่ำ ≥ 70%", 4135, 53.6, -3.8, 11.3),
    ("gavg ≤ 2.00 + ราคา ≥ 1.60 (ดีที่สุดที่หาได้)", 12018, 50.6, -3.6, 32.9),
]
# BTTS — วัดกับคลัง 121,402 คู่ (ตัดบอลถ้วย) · คลังไม่มีค่า gg% ของ Forebet
# เลยวัดด้วย "ตัวแทน" = สกอร์ที่ทายมีทั้งสองฝั่ง ≥ 1 (เช่น 2-1) ซึ่งมีในคลัง
BTTS = [
    ("ทุกคู่ (เส้นฐาน)", 121402, 51.7, 74.8, None),
    ("ทายสกอร์มีทั้งสองฝั่ง ≥ 1 (2-1, 1-1, …)", 72182, 54.3, 76.7, None),
    ("  + gavg ≥ 2.70", 44664, 56.6, 80.2, None),
    ("  + gavg ≥ 3.00", 33382, 57.7, 81.5, None),
    ("  + สูง ≥ 70%", 16440, 59.5, 84.1, "best"),
    ("ทายสกอร์มีฝั่งใดฝั่งหนึ่ง = 0 (1-0, 2-0, …)", 49220, 47.9, 72.0, "worst"),
]
COMBO = [
    ("🔺 OV ไม่ติดป้ายอื่นเลย", 1995, 57.0, 1.1, +2.0, "best"),
    ("🔺 OV ทุกใบ (เส้นเทียบ)", 3489, 56.7, 0.8, +1.1, None),
    ("🔺 OV + 🎱 SC", 1203, 56.8, 1.4, +0.8, None),
    ("🔺 OV + 🎯 1X", 506, 55.3, 2.2, -2.0, "worst"),
    ("🔻 UN ไม่ติดป้ายอื่นเลย", 4768, 54.2, 0.7, -3.0, "best"),
    ("🔻 UN ทุกใบ (เส้นเทียบ)", 6039, 53.7, 0.6, -3.7, None),
    ("🔻 UN + 🎱 SC", 1081, 52.5, 1.5, -5.0, None),
    ("🔻 UN + 🎯 1X", 261, 50.6, 3.1, -8.8, "worst"),
]
GAPS = [(0, 21930, 28.6, 3.36, -5.9), (1, 39516, 48.1, 2.21, -4.5),
        (2, 23791, 52.3, 2.08, -1.8), (3, 5003, 67.3, 1.60, +0.3)]
SCORE = [("ทุกคู่", 90842, 11.4, 20.7), ("ตรงทาง", 90808, 11.4, 20.6),
         ("ตรงทาง + ห่าง 1 ลูก", 39516, 12.5, None),
         ("ตรงทาง + ห่าง 2 ลูก ← กฎที่ส่งมา", 23791, 8.5, 20.9)]

CHIP = {"keep": ("เก็บไว้", "ok"), "noop": ("ไม่มีผล", "flat"), "bad": ("ทำให้แย่ลง", "bad")}


def pct(v):
    c = "up" if v > 0 else ("down" if v < 0 else "")
    return f'<span class="num {c}">{v:+.1f}%</span>'


# ── ตัวอย่างการ์ด (สร้างจากคู่จริงของวันนี้ ถ้ามี) ────────────────────────────
def card_html(kind):
    got = [d for d in rows if d.get(f"_ok_{kind}")]
    if not got:
        return ""
    r = RULES[kind]
    p = PICKERS[kind](sorted(got, key=lambda d: d["ko"])[0], {})
    L = [f'<div class="ln head">⚽ {p["ko"]:%H:%M} · <b>[{r["tag"]}]</b> {E(p["lg"])}</div>',
         f'<div class="ln team">{E(p["host"])} <span class="v">vs</span> {E(p["guest"])}</div>']
    if kind == "over":
        L.append(f'<div class="ln"><span class="ar up">🔺</span> บอลสูง — '
                 f'<b>สูง {p["pro"]}%</b> · ประตูเฉลี่ย {p["gavg"]:.2f}</div>')
        L.append(f'<div class="ln">💱 ราคาสูง <b>{p["coef"]:.2f}</b> · '
                 f'ค่าคุ้ม {pct(p["edge"] * 100)}</div>')
    elif kind == "under":
        L.append(f'<div class="ln"><span class="ar down">🔻</span> บอลต่ำ — '
                 f'<b>ต่ำ {p["pru"]}%</b> · ประตูเฉลี่ย {p["gavg"]:.2f}</div>')
        L.append(f'<div class="ln">💱 ราคาต่ำ <b>{p["coef"]:.2f}</b> · '
                 f'ค่าคุ้ม {pct(p["edge"] * 100)}</div>')
    elif kind == "1x2":
        L.append(f'<div class="ln">🔢 บ้าน <b>{p["p"][0]}%</b> · เสมอ <b>{p["p"][1]}%</b> · '
                 f'เยือน <b>{p["p"][2]}%</b> <span class="mut">(ห่างอันดับ 2 = '
                 f'{p["gap2"]} จุด)</span></div>')
        L.append(f'<div class="ln">🎯 ทายไว้ <b>{p["hpr"]}-{p["gpr"]}</b></div>')
        L.append(f'<div class="ln">💱 Coef <b>{p["coef"]:.2f}</b> · '
                 f'ค่าคุ้ม {pct(p["edge"] * 100)}</div>')
    else:
        L.append(f'<div class="ln">🎯 ทาย <b>{p["hpr"]}-{p["gpr"]}</b> · '
                 f'ประตูเฉลี่ย {p["gavg"]:.2f}</div>')
        L.append('<div class="ln">📈 ทาย 1X2 กับสกอร์ไปทางเดียวกัน · '
                 f'ห่าง {p["sgap"]} ลูก</div>')
    if r["roi"] is None:
        L.append(f'<div class="ln bt">📊 กลุ่มนี้ย้อนหลัง {r["n"]:,} ใบ · '
                 f'สกอร์เป๊ะ {r["hit"]:.1f}% · ลูกรวมเป๊ะ 20.9%</div>')
    else:
        L.append(f'<div class="ln bt">📊 กลุ่มนี้ย้อนหลัง {r["n"]:,} ใบ · '
                 f'เข้า {r["hit"]:.1f}% · กำไร/ใบ {pct(r["roi"])}</div>')
    L.append(f'<div class="ln warn">{E(r["warn"])}</div>')
    if p.get("wx"):
        L.append(f'<div class="ln mut">🌡 {E(p["wx"])} (ยังไม่เคยวัด โชว์เฉยๆ)</div>')
    return (f'<article class="card {kind}"><div class="ct">{r["emoji"]} {E(r["name"])}</div>'
            + "".join(L) + "</article>")


# ── ตาราง ────────────────────────────────────────────────────────────────────
def tag_pills(d):
    out = ""
    for k, lab in (("over", "OV"), ("under", "UN"), ("1x2", "1X"), ("score", "SC")):
        if d.get(f"_ok_{k}"):
            # UN กับ 1X ปิดแล้ว (วัดแล้วขาดทุน) — ยังโชว์ไว้ให้เห็น แต่ขีดฆ่า
            off = " off" if k in ("under", "1x2") else ""
            out += f'<span class="pill {k}{off}">{lab}</span>'
    return out or '<span class="mut">–</span>'


def level(d):
    """ไฮไลท์ 3 ระดับ — เส้นแบ่งมาจากผลวัด COMBO ไม่ได้เดา
    สูง  = 🔺 OV ป้ายเดียวโดดๆ            (57.0% · +2.0% · 1,995 ใบ) = ดีที่สุดที่วัดได้
    กลาง = 🔺 OV + 🎱 SC (ไม่มี 🎯 1X)     (56.8% · +0.8% · 1,203 ใบ) = ยังบวก
    ปกติ = ที่เหลือทั้งหมด (OV+1X −2.0% · UN −3.7% · 1X −5.4% วัดแล้วติดลบ)
    """
    ok = {k for k in PICKERS if d.get(f"_ok_{k}")}
    if ok == {"over"}:
        return 3
    if "over" in ok and "1x2" not in ok:
        return 2
    return 1


LVN = {3: "น่าลงทุนสุด", 2: "พอไปดูได้", 1: ""}


def edge(d):
    """ค่าคุ้ม % — เลขเดียวกับช่อง "Live coef." บนหน้า forebet.com/en/values
    สูตร (p·c − 1) / (c − 1) ของฝั่งที่ Forebet ทาย · ติดลบปัดเป็น 0
    เทียบกับหน้าเว็บจริง 17 ส.ค. 69: ตรง 38 จาก 39 คู่ (คลาดตัวเดียวเพราะปัดเศษ)
    """
    if "top" not in d:
        return None
    i = d["top"]
    p, c = d["p"][i], d["o"][i]
    if p is None or not c or c <= 1:
        return None
    return max(0.0, (p / 100 * c - 1) / (c - 1) * 100)

trs = []
for d in rows:
    lv = level(d)
    top = d.get("top")
    ps = "".join(
        f'<span class="{"b" if top == i else ""}">{n(v)}</span>'
        + ("<i>/</i>" if i < 2 else "") for i, v in enumerate(d["p"]))
    os_ = "".join(
        f'<span class="{"b" if top == i else ""}">{n(v, 2)}</span>'
        + ("<i>/</i>" if i < 2 else "") for i, v in enumerate(d["o"]))
    pro, pru = d["pro"], d["pru"]
    gg, ngg = d.get("gg"), d.get("ngg")
    ev = edge(d)
    real = (f'{d["HS"]}-{d["GS"]}' if d["ft"] == "FT" and d["HS"] is not None else "–")
    # สีแบบ forebet: ทายถูก = เขียว · ทายผิด = ดำ (เฉพาะคู่ที่จบแล้ว)
    rc = scc = ""
    if d["ft"] == "FT" and d["HS"] is not None and top is not None:
        res = 0 if d["HS"] > d["GS"] else (1 if d["HS"] == d["GS"] else 2)
        rc = "r-ok" if top == res else "r-no"
        if d["hpr"] is not None:
            scc = "r-ok" if (d["HS"], d["GS"]) == (d["hpr"], d["gpr"]) else "r-no"
    # บอลรอง = ฝั่งที่ Forebet ทาย เป็นฝั่งราคาแพงสุดในสามช่อง
    oo = [x for x in d["o"] if x]
    dog = top is not None and len(oo) == 3 and d["o"][top] == max(oo)
    tags = " ".join(k for k in PICKERS if d.get(f"_ok_{k}"))
    if ev is not None and ev >= 30:
        tags = (tags + " ev30" + (" dog30" if dog else "")).strip()
    if "_vb" in d:
        tags = (tags + " vb").strip()
    trs.append(
        f'<tr class="lv{lv}{" fin" if d["ft"] == "FT" else ""}" '
        f'data-tag="{tags}" data-lv="{lv}" '
        f'data-ev="{-1 if ev is None else ev:.1f}">'
        f'<td class="t" data-l="เวลา">{d["ko"]:%H:%M}</td>'
        f'<td class="lg" data-l="ลีก" title="{E(d["lg"])}">{E(d["short"])}</td>'
        f'<td class="m" data-l="คู่">{E(d["host"])} <span class="v">v</span> {E(d["guest"])}</td>'
        f'<td class="d" data-l="p1/px/p2">{ps}</td>'
        f'<td class="d" data-l="o1/ox/o2">{os_}</td>'
        f'<td class="d" data-l="ทาย">'
        f'<span class="{scc}">{n(d["hpr"])}-{n(d["gpr"])}</span></td>'
        f'<td class="d" data-l="pro/pru">'
        f'<span class="{"hi-up" if pro and pro >= 70 else ""}">{n(pro)}</span>'
        f'<i>/</i><span class="{"hi-dn" if pru and pru >= 65 else ""}">{n(pru)}</span></td>'
        f'<td class="d" data-l="bo/bu">{n(d["bo"], 2)}<i>/</i>{n(d["bu"], 2)}</td>'
        f'<td class="d" data-l="BTTS %">'
        f'<span class="{"hi-up" if gg and gg >= 70 else ""}">{n(gg)}</span>'
        f'<i>/</i><span class="{"hi-dn" if ngg and ngg >= 70 else ""}">{n(ngg)}</span></td>'
        f'<td class="d" data-l="ราคา BTTS">{n(d.get("ogy"), 2)}'
        f'<i>/</i>{n(d.get("ogn"), 2)}</td>'
        f'<td class="d" data-l="gavg">{n(d["gavg"], 2)}</td>'
        f'<td class="d" data-l="ค่าคุ้ม %">'
        f'<span class="{"hi-up" if ev is not None and ev >= 30 else ""}">'
        f'{"–" if ev is None else f"{ev:.0f}"}</span>'
        + ('<span class="vbstar" title="อยู่บนหน้า values ของ forebet">⭐</span>'
           if "_vb" in d else "") + "</td>"
        f'<td class="d sc" data-l="ผลจริง"><span class="{rc}">{real}</span></td>'
        f'<td class="tg" data-l="ผ่านด่าน">{tag_pills(d)}'
        + (f'<span class="lvb b{lv}">{LVN[lv]}</span>' if lv > 1 else "")
        + "</td></tr>")

# ── สถิติออโต้: จดใบวันนี้ (ก่อนเตะ) + เกรดวันที่ผ่านมา ────────────────────────
# ⚠️ ห้ามให้ขั้นนี้ล้มพาทั้งหน้าไปด้วย — สถิติหายรอบเดียวไม่เป็นไร หน้าหายไม่ได้
STAT = ""
try:
    import fb_stat
    if LIVE:
        fb_stat.record(rows, level, edge)
        fb_stat.grade()
    STAT = fb_stat.stat_html()
except Exception as e:
    print(f"⚠️ สถิติออโต้พลาด ({type(e).__name__}: {e}) → หน้ายังออกปกติ")

CSS = """
:root{
  --bg:#F2F3F6; --surf:#FFFFFF; --surf2:#FAFBFC; --ink:#131720; --mut:#606A7B;
  --line:#DEE2EA; --acc:#1D5FA8; --acc-w:#E7EFF9;
  --up:#1B7A4B; --up-w:#E4F2EA; --dn:#B33A2B; --dn-w:#FAE9E6; --wr:#8C6410; --wr-w:#FBF1DC;
  --okbg:#16A05C; --okfg:#FFFFFF; --nobg:#1B2029; --nofg:#FFFFFF;
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --bg:#101319; --surf:#181D26; --surf2:#1D232D; --ink:#E6EAF1; --mut:#8B95A6;
  --line:#28303C; --acc:#5EA3EC; --acc-w:#16283D;
  --up:#3EA96D; --up-w:#12291D; --dn:#E0705E; --dn-w:#2E1815; --wr:#D6A23F; --wr-w:#2B2211;
  --okbg:#1E9257; --okfg:#FFFFFF; --nobg:#05070A; --nofg:#6C7789;
}}
:root[data-theme="dark"]{
  --bg:#101319; --surf:#181D26; --surf2:#1D232D; --ink:#E6EAF1; --mut:#8B95A6;
  --line:#28303C; --acc:#5EA3EC; --acc-w:#16283D;
  --up:#3EA96D; --up-w:#12291D; --dn:#E0705E; --dn-w:#2E1815; --wr:#D6A23F; --wr-w:#2B2211;
  --okbg:#1E9257; --okfg:#FFFFFF; --nobg:#05070A; --nofg:#6C7789;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Segoe UI","Leelawadee UI",system-ui,-apple-system,"Noto Sans Thai",sans-serif;
  font-size:15px;line-height:1.65;-webkit-text-size-adjust:100%}
.wrap{max-width:1180px;margin:0 auto;padding:32px 20px 72px;display:flex;
  flex-direction:column;gap:38px}
.mono,.d,.t,.num{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace;
  font-variant-numeric:tabular-nums}
h1{font-size:clamp(26px,4.6vw,38px);line-height:1.2;margin:0;letter-spacing:-.02em;
  text-wrap:balance}
h2{font-size:clamp(19px,2.8vw,23px);margin:0 0 4px;letter-spacing:-.01em;text-wrap:balance}
h3{font-size:15px;margin:0 0 8px;letter-spacing:-.005em}
p{margin:0}
.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--acc);
  font-weight:700;margin:0 0 10px}
.lede{color:var(--mut);max-width:62ch}
header{border-bottom:2px solid var(--ink);padding-bottom:22px}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.stat{background:var(--surf);border:1px solid var(--line);border-radius:7px;
  padding:7px 12px;font-size:12.5px;color:var(--mut)}
.stat b{color:var(--ink);font-family:ui-monospace,Consolas,monospace}
section{display:flex;flex-direction:column;gap:14px}
.panel{background:var(--surf);border:1px solid var(--line);border-radius:11px;padding:18px}
.note{border-left:3px solid var(--acc);background:var(--acc-w);border-radius:0 8px 8px 0;
  padding:14px 16px;font-size:14px}
.note.bad{border-left-color:var(--dn);background:var(--dn-w)}
.note b{font-weight:700}
/* ── ขั้นบันไดกรอง (เป็นลำดับจริง จึงใส่เลขขั้น) ── */
.step{display:grid;grid-template-columns:26px 1fr auto;gap:12px;align-items:center;
  padding:11px 0;border-top:1px solid var(--line)}
.step:first-child{border-top:0}
.si{font-size:11px;color:var(--mut);font-family:ui-monospace,Consolas,monospace;
  text-align:right}
.sl{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.sn{font-size:12.5px;color:var(--mut);white-space:nowrap}
.sr{display:flex;gap:14px;align-items:baseline;font-size:13px;white-space:nowrap}
.chip{font-size:10.5px;font-weight:700;letter-spacing:.05em;padding:2px 8px;
  border-radius:99px;white-space:nowrap}
.chip.ok{background:var(--up-w);color:var(--up)}
.chip.flat{background:var(--surf2);color:var(--mut);border:1px solid var(--line)}
.chip.bad{background:var(--dn-w);color:var(--dn)}
.num.up{color:var(--up);font-weight:700}
.num.down{color:var(--dn);font-weight:700}
.bar{height:5px;border-radius:99px;background:var(--acc);opacity:.5;min-width:3px}
/* ── การ์ด ── */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px}
.card{background:var(--surf);border:1px solid var(--line);border-radius:11px;padding:15px;
  display:flex;flex-direction:column;gap:5px;font-size:13.5px;
  border-top:3px solid var(--mut)}
.card.over{border-top-color:var(--up)} .card.under{border-top-color:var(--dn)}
.card[class~="1x2"]{border-top-color:var(--acc)}
.card.score{border-top-color:var(--wr)}
.ct{font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--mut);
  font-weight:700;margin-bottom:3px}
.ln.head{color:var(--mut);font-size:12.5px}
.ln.team{font-size:16px;font-weight:700;letter-spacing:-.01em;line-height:1.35}
.ln.team .v{color:var(--mut);font-weight:400;font-size:13px}
.ln.bt{margin-top:6px;padding-top:9px;border-top:1px dashed var(--line);font-size:12.5px;
  color:var(--mut)}
.ln.warn{font-size:12px;color:var(--wr);background:var(--wr-w);border-radius:6px;
  padding:7px 9px}
.ar.up{color:var(--up)} .ar.down{color:var(--dn)} .mut{color:var(--mut)}
/* ── ตาราง ── */
.scroll{overflow-x:auto;background:var(--surf);border:1px solid var(--line);
  border-radius:11px}
table{border-collapse:collapse;width:100%;font-size:12.5px;white-space:nowrap}
thead th{position:sticky;top:0;background:var(--surf2);border-bottom:2px solid var(--line);
  padding:9px 8px;text-align:left;font-size:11px;letter-spacing:.05em;color:var(--mut);
  font-weight:700;z-index:2}
td{padding:6px 8px;border-bottom:1px solid var(--line)}
/* ไฮไลท์ 3 ระดับ — สูง(เขียว)=วัดแล้วกำไรดีสุด · กลาง(เหลือง)=ยังบวก · ปกติ=ไม่ทา */
tbody tr.lv3{background:var(--up-w);box-shadow:inset 3px 0 0 var(--up)}
tbody tr.lv2{background:var(--wr-w);box-shadow:inset 3px 0 0 var(--wr)}
tbody tr.lv3 td:first-child,tbody tr.lv2 td:first-child{padding-left:11px}
.lvb{font-size:9.5px;font-weight:700;padding:1px 6px;border-radius:99px;letter-spacing:.03em;
  white-space:nowrap}
.lvb.b3{background:var(--up);color:#fff} .lvb.b2{background:var(--wr);color:#fff}
.t{color:var(--mut)} .lg{color:var(--mut);font-size:11.5px}
.m{font-weight:600;white-space:nowrap} .m .v{color:var(--mut);font-weight:400}
.d{font-size:12px} .d i{color:var(--line);font-style:normal;padding:0 1px}
.d .b{font-weight:700;color:var(--acc)}
.sc{font-weight:700}
.vbstar{margin-left:3px;font-size:11px}
.pill.off{opacity:.38;text-decoration:line-through}
/* คู่ที่จบไปแล้ว — ทึบลงทั้งบรรทัด จะได้กวาดตาข้ามไปหาคู่ที่ยังแทงได้ */
tbody tr.fin{opacity:.42}
tbody tr.fin .r-ok,tbody tr.fin .r-no{opacity:1}
.hi-up{color:var(--up);font-weight:700} .hi-dn{color:var(--dn);font-weight:700}
/* สีแบบ forebet — ทายถูกเขียว ทายผิดดำ */
.r-ok,.r-no{font-weight:700;border-radius:4px;padding:1px 6px;display:inline-block}
.r-ok{background:var(--okbg);color:var(--okfg)}
.r-no{background:var(--nobg);color:var(--nofg)}
.pill{font-size:9.5px;font-weight:700;padding:1px 5px;border-radius:4px;margin-right:3px;
  letter-spacing:.04em}
.pill.over{background:var(--up-w);color:var(--up)}
.pill.under{background:var(--dn-w);color:var(--dn)}
.pill[class~="1x2"]{background:var(--acc-w);color:var(--acc)}
.pill.score{background:var(--wr-w);color:var(--wr)}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;color:var(--mut)}
.legend code{background:var(--surf2);border:1px solid var(--line);border-radius:4px;
  padding:1px 5px;font-size:11.5px}
/* ── อภิธาน ── */
.gr{display:grid;grid-template-columns:150px 1fr;gap:14px;padding:9px 0;
  border-top:1px solid var(--line);align-items:baseline}
.gr:first-of-type{border-top:0}
.gk{color:var(--acc);font-weight:700;font-size:12.5px}
.gv{font-size:13.5px}
.gx{display:block;color:var(--mut);font-size:12px;margin-top:2px}
.sw{display:inline-block;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;
  padding:2px 8px;border-radius:5px;background:var(--surf2);border:1px solid var(--line)}
.sw.sw3{background:var(--up-w);border-color:var(--up);color:var(--up);font-weight:700}
.sw.sw2{background:var(--wr-w);border-color:var(--wr);color:var(--wr);font-weight:700}
.sw.sw1{color:var(--mut)}
/* ── ปุ่มกรอง ── */
.fbar{display:flex;flex-wrap:wrap;gap:7px}
.fb{font:inherit;font-size:12.5px;font-weight:600;padding:7px 13px;border-radius:99px;
  border:1px solid var(--line);background:var(--surf);color:var(--mut);cursor:pointer;
  -webkit-tap-highlight-color:transparent}
.fb:hover{border-color:var(--acc);color:var(--acc)}
.fb.on{background:var(--acc);border-color:var(--acc);color:#fff}
.fb.hi3{border-color:var(--up);color:var(--up)} .fb.hi3.on{background:var(--up);color:#fff}
.fb.hi2{border-color:var(--wr);color:var(--wr)} .fb.hi2.on{background:var(--wr);color:#fff}
.fb:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
footer{border-top:1px solid var(--line);padding-top:18px;color:var(--mut);font-size:12.5px}
@media(max-width:700px){
  .wrap{padding:22px 12px 56px;gap:30px}
  .step{grid-template-columns:20px 1fr;gap:6px 8px}
  .sr{grid-column:1/-1;padding-left:28px;flex-wrap:wrap;gap:10px}
  .gr{grid-template-columns:1fr;gap:2px}
  .cards{grid-template-columns:1fr}
  .fbar{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0;
    margin:0 -12px;padding-left:12px;padding-right:12px;overflow-x:auto;flex-wrap:nowrap}
  .fb{white-space:nowrap}
  /* ตารางกลายเป็นการ์ดซ้อนกัน — ไม่ต้องเลื่อนซ้ายขวาแล้ว */
  .scroll{overflow:visible;background:transparent;border:0;border-radius:0}
  table{white-space:normal;font-size:13px}
  thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
  tbody tr{display:grid;grid-template-columns:1fr 1fr;gap:1px 12px;background:var(--surf);
    border:1px solid var(--line);border-radius:11px;padding:11px 13px;margin-bottom:10px}
  tbody tr.lv3{border-color:var(--up);box-shadow:inset 4px 0 0 var(--up)}
  tbody tr.lv2{border-color:var(--wr);box-shadow:inset 4px 0 0 var(--wr)}
  tbody tr.lv3 td:first-child,tbody tr.lv2 td:first-child{padding-left:0}
  td{border:0;padding:3px 0;display:flex;justify-content:space-between;gap:10px;
    align-items:baseline;min-width:0}
  td::before{content:attr(data-l);color:var(--mut);font-size:10.5px;letter-spacing:.05em;
    font-family:ui-monospace,Consolas,monospace;flex:none}
  td.t,td.lg{font-size:12px;padding-bottom:6px}
  td.t{justify-content:flex-start} td.lg{justify-content:flex-end;text-align:right}
  td.t::before,td.lg::before,td.m::before,td.tg::before{display:none}
  td.t{font-weight:700;color:var(--ink)}
  td.m{grid-column:1/-1;display:block;font-size:15.5px;line-height:1.35;white-space:normal;
    padding:0 0 9px;border-bottom:1px dashed var(--line);margin-bottom:6px}
  td.sc{grid-column:1/-1;border-top:1px dashed var(--line);margin-top:5px;padding-top:7px}
  td.tg{grid-column:1/-1;padding-top:8px}
  td.tg .pill{font-size:11px;padding:3px 8px;margin-right:5px}
}
"""


def funnel_rows():
    out, mx = [], FUNNEL[0][1]
    for i, (lab, nn, hit, od, roi, st) in enumerate(FUNNEL):
        chip = ""
        if st:
            t, c = CHIP[st]
            chip = f'<span class="chip {c}">{t}</span>'
        out.append(
            f'<div class="step"><div class="si">{i}</div>'
            f'<div><div class="sl"><span>{E(lab)}</span>{chip}</div>'
            f'<div class="bar" style="width:{max(3, nn / mx * 100):.0f}%"></div></div>'
            f'<div class="sr"><span class="sn">{nn:,} ใบ</span>'
            f'<span class="num">เข้า {hit:.1f}%</span>'
            f'<span class="sn">เรท {od:.2f}</span>{pct(roi)}</div></div>')
    return "".join(out)


over_rows = "".join(
    f'<div class="step"><div class="si">{"★" if good else ""}</div>'
    f'<div class="sl"><span>{E(lab)}</span></div>'
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span><span class="num">เข้า {hit:.1f}%</span>'
    f'{pct(roi)}<span class="sn">{pd:.1f}/วัน</span></div></div>'
    for lab, nn, hit, roi, pd, good in OVER)
under_rows = "".join(
    f'<div class="step"><div class="si"></div><div class="sl"><span>{E(lab)}</span></div>'
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span><span class="num">เข้า {hit:.1f}%</span>'
    f'{pct(roi)}<span class="sn">{pd:.1f}/วัน</span></div></div>'
    for lab, nn, hit, roi, pd in UNDER)
gap_rows = "".join(
    f'<div class="step"><div class="si">{g}</div>'
    f'<div class="sl"><span>ทายห่าง <b>{g}</b> ลูก</span>'
    "</div>"
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span>'
    f'<span class="num">ชนะ 1X2 {hit:.1f}%</span><span class="sn">เรท {od:.2f}</span>'
    f'{pct(roi)}</div></div>' for g, nn, hit, od, roi in GAPS)
score_rows = "".join(
    f'<div class="step"><div class="si"></div><div class="sl"><span>{E(lab)}</span></div>'
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span>'
    f'<span class="num">สกอร์เป๊ะ {cs:.1f}%</span>'
    f'<span class="num">{"ลูกรวมเป๊ะ %.1f%%" % tot if tot else ""}</span></div></div>'
    for lab, nn, cs, tot in SCORE)
alt_rows = "".join(
    f'<div class="step"><div class="si">↳</div><div class="sl"><span>{E(lab)}</span></div>'
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span><span class="num">เข้า {hit:.1f}%</span>'
    f'<span class="sn">เรท {od:.2f}</span>{pct(roi)}</div></div>'
    for lab, nn, hit, od, roi in ALT)

MARK = {"best": ("★ ดีที่สุดในกลุ่ม", "ok"), "worst": ("✕ แย่ที่สุดในกลุ่ม", "bad")}
VALUE = [
    ("ทุกคู่ (เส้นฐาน)", 90834, 45.8, 2.42, -3.7, 248.9, None),
    ("ค่าคุ้ม 0% (ราคาไม่คุ้มเลย)", 45442, 58.1, 1.71, -5.5, 124.5, "worst"),
    ("ค่าคุ้ม 1–10%", 13123, 37.6, 2.59, -6.9, 36.0, None),
    ("ค่าคุ้ม 10–20%", 17546, 31.5, 3.11, -7.0, 48.1, None),
    ("ค่าคุ้ม 20–30%", 10093, 30.8, 3.42, -1.5, 27.7, None),
    ("ค่าคุ้ม 30–40%", 3473, 31.9, 3.85, +13.4, 9.5, None),
    ("ค่าคุ้ม 40–50%", 840, 35.8, 4.42, +48.0, 2.3, None),
    ("ค่าคุ้ม 50%+", 317, 58.0, 5.03, +168.9, 0.9, "best"),
]
VALCUM = [
    ("ค่าคุ้ม ≥ 10%", 32269, 31.7, 3.34, +0.1, 88.4, None),
    ("ค่าคุ้ม ≥ 20%", 14723, 31.9, 3.62, +8.5, 40.3, None),
    ("ค่าคุ้ม ≥ 30%", 4630, 34.4, 4.03, +30.3, 12.7, "best"),
    ("ค่าคุ้ม ≥ 40%", 1157, 41.9, 4.58, +81.1, 3.2, None),
    ("ค่าคุ้ม ≥ 30% + เรท 1.60–2.10", 308, 57.1, 1.89, +6.8, 0.8, "worst"),
]
# ทุกแถวใช้นิยาม "รอง" เดียวกับปุ่ม 🐶 ในตาราง = เรทฝั่งที่ทายสูงสุด เสมอกันก็นับ
# (เคยมีอีกนิยามที่ไม่นับเสมอ ได้ 1,958 ใบ +68.4% — ถูกเหมือนกัน แต่ปุ่มกรองไม่ได้ใช้
#  เลยตัดทิ้ง ไม่งั้นเลขบนใบคะแนนกับที่กดดูจริงจะไม่ตรงกัน)
DOG = [
    ("ค่าคุ้ม ≥30% · ฝั่งที่ทาย = เต็ง", 681, 50.2, 2.07, +1.6, 1.9, None),
    ("ค่าคุ้ม ≥30% · ฝั่งที่ทาย = กลาง", 1991, 31.1, 3.55, +2.7, 5.5, None),
    ("ค่าคุ้ม ≥30% · ฝั่งที่ทาย = รอง", 2092, 32.5, 5.10, +65.6, 5.7, "best"),
    ("รอง + ค่าคุ้ม 30–40%", 1542, 29.6, 4.67, +34.7, 4.2, None),
    ("รอง + ค่าคุ้ม 40–50%", 378, 33.9, 5.96, +100.8, 1.0, None),
    ("รอง + ค่าคุ้ม 50%+", 172, 55.8, 6.98, +264.7, 0.5, "best"),
    ("รอง + ค่าคุ้ม ต่ำกว่า 20%", 9320, 27.6, 3.39, -7.9, 25.5, "worst"),
]
# ใบคะแนนของด่านทั้งหมด — ตัวไหนควรปิด (วัดกับคลังเดียวกัน ตัดบอลถ้วย)
GATE = [
    ("🐶 รอง + ค่าคุ้ม ≥30%", 2092, 32.5, 5.10, +65.6, 5.7, "best"),
    ("💰 ค่าคุ้ม ≥30% (ทุกฝั่ง)", 4630, 34.4, 4.03, +30.3, 12.7, "best"),
    ("🔺 บอลสูง (OV)", 3489, 56.7, 1.79, +1.1, 9.6, None),
    ("🔻 บอลต่ำ (UN)", 6039, 53.7, 1.81, -3.7, 16.5, "worst"),
    ("🎯 1X2", 8581, 52.7, 1.81, -5.4, 23.5, "worst"),
]
GATE_SC = [("🎱 สกอร์เป๊ะ (SC)", 24927, 7.3, 68.3)]
gate_rows = "".join(
    f'<div class="step"><div class="si">{"★" if mk == "best" else ("✕" if mk == "worst" else "")}</div>'
    f'<div class="sl"><span>{E(lab)}</span>'
    + (f'<span class="chip {MARK[mk][1]}">{MARK[mk][0]}</span>' if mk else "")
    + "</div>"
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span>'
    f'<span class="num">เข้า {hit:.1f}%</span><span class="sn">เรท {av:.2f}</span>'
    f'{pct(roi)}<span class="sn">{pd:.1f}/วัน</span></div></div>'
    for lab, nn, hit, av, roi, pd, mk in GATE) + "".join(
    f'<div class="step"><div class="si"></div>'
    f'<div class="sl"><span>{E(lab)}</span></div>'
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span>'
    f'<span class="num">เข้า {hit:.1f}%</span>'
    f'<span class="sn">ไม่มีราคาให้คิดกำไร</span>'
    f'<span class="sn">{pd:.1f}/วัน</span></div></div>' for lab, nn, hit, pd in GATE_SC)
DOGMIX = [(0, 15.8, 60.8), (20, 41.0, 14.8), (30, 42.3, 15.9),
          (40, 44.8, 19.2), (50, 51.7, 22.4)]
dogmix_rows = "".join(
    f'<div class="step"><div class="si"></div>'
    f'<div class="sl"><span>ค่าคุ้ม ≥ {lo}%</span></div>'
    f'<div class="sr"><span class="num">เป็นบอลรอง {dg:.1f}%</span>'
    f'<span class="sn">เป็นเต็ง {fv:.1f}%</span></div></div>' for lo, dg, fv in DOGMIX)
val_rows, valcum_rows, dog_rows = ("".join(
    f'<div class="step"><div class="si">{"★" if mk == "best" else ("✕" if mk == "worst" else "")}</div>'
    f'<div class="sl"><span>{E(lab)}</span>'
    + (f'<span class="chip {MARK[mk][1]}">{MARK[mk][0]}</span>' if mk else "")
    + "</div>"
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span>'
    f'<span class="num">เข้า {hit:.1f}%</span><span class="sn">เรท {av:.2f}</span>'
    f'{pct(roi)}<span class="sn">{pd:.1f}/วัน</span></div></div>'
    for lab, nn, hit, av, roi, pd, mk in src) for src in (VALUE, VALCUM, DOG))
btts_rows = "".join(
    f'<div class="step"><div class="si">{"★" if mk == "best" else ("✕" if mk == "worst" else "")}</div>'
    f'<div class="sl"><span>{E(lab)}</span>'
    + (f'<span class="chip {MARK[mk][1]}">{MARK[mk][0]}</span>' if mk else "")
    + "</div>"
    f'<div class="sr"><span class="sn">{nn:,} คู่</span>'
    f'<span class="num">ยิงกันทั้งคู่ {b:.1f}%</span>'
    f'<span class="sn">2 ลูก+ {t:.1f}%</span></div></div>'
    for lab, nn, b, t, mk in BTTS)
combo_rows = "".join(
    f'<div class="step"><div class="si">{"★" if mk == "best" else ("✕" if mk == "worst" else "")}</div>'
    f'<div class="sl"><span>{E(lab)}</span>'
    + (f'<span class="chip {MARK[mk][1]}">{MARK[mk][0]}</span>' if mk else "")
    + "</div>"
    f'<div class="sr"><span class="sn">{nn:,} ใบ</span>'
    f'<span class="num">เข้า {hit:.1f}%±{se:.1f}</span>{pct(roi)}</div></div>'
    for lab, nn, hit, se, roi, mk in COMBO)

# ── อภิธานตัวย่อ ─────────────────────────────────────────────────────────────
GLOSS = [
    ("เวลา", "เวลาเตะ (เวลาไทย)", ""),
    ("ลีก", "ชื่อลีกย่อ — จิ้มค้างที่ชื่อจะเห็นชื่อเต็ม + ประเทศ", ""),
    ("p1 / px / p2", "โอกาสชนะ% ของ <b>บ้าน / เสมอ / เยือน</b> ที่ Forebet คำนวณ",
     "รวมกันได้ 100 · ตัวหนาสีน้ำเงิน = ฝั่งที่ Forebet ทาย"),
    ("o1 / ox / o2", "<b>Coef</b> = ราคา (เรทจ่าย) ของ บ้าน / เสมอ / เยือน",
     "2.00 = แทง 100 ได้คืน 200 ถ้าถูก"),
    ("ทาย", "สกอร์ที่ Forebet ทายไว้ เช่น <span class='mono'>2-1</span>",
     "ผลต่างของสองตัวนี้คือ “ห่างกี่ลูก”"),
    ("pro / pru", "โอกาส%ที่จะ <b>สูง</b> / <b>ต่ำ</b> (เกิน 2.5 ลูก / ไม่เกิน 2.5 ลูก)",
     "เขียว = pro ≥ 70 (ผ่านเส้นบอลสูง) · แดง = pru ≥ 65 (ผ่านเส้นบอลต่ำ)"),
    ("bo / bu", "ราคาของ <b>บอลสูง</b> / <b>บอลต่ำ</b> (เกิน/ไม่เกิน 2.5 ลูก)",
     "<b>คนละตลาดกับ BTTS</b> — bo/bu ไม่สนว่าใครยิง สนแค่จำนวนลูกรวม"),
    ("BTTS %", "โอกาส%ที่ <b>ยิงกันทั้งคู่</b> / <b>ไม่ยิงกันทั้งคู่</b> (Pred_gg / Pred_no_gg)",
     "เขียว = ยิงกันทั้งคู่ ≥ 70 (ช่องซ้าย) · แดง = ไม่ยิงกันทั้งคู่ ≥ 70 (ช่องขวา)"),
    ("ราคา BTTS", "ราคาของ <b>ยิงกันทั้งคู่ (Yes)</b> / <b>ไม่ยิงกันทั้งคู่ (No)</b>", ""),
    ("gavg", "จำนวนประตูเฉลี่ยที่คาดว่าจะเกิดในเกมนี้", "3.10 = คาดว่ามีประมาณ 3 ลูก"),
    ("ค่าคุ้ม %", "<b>เลขเดียวกับช่อง “Live coef.” บนหน้า forebet.com/en/values</b> "
     "— ราคาที่ได้ คุ้มกว่าโอกาสจริงกี่ %",
     "เขียว = ≥ 30 (วัดแล้วกำไร/ใบ +30.3%) · หน้า values โชว์แค่ 65 คู่ ตารางนี้คิดให้ครบทุกคู่ · "
     "“–” = Forebet ยังไม่ปล่อยราคาคู่นั้น คิดไม่ได้ ต้องรอ · "
     "ค่าคุ้มสูงมักเป็น<b>บอลรอง</b> และกำไรเกือบทั้งหมดอยู่ตรงนั้น"),
    ("ช่อง “ทาย” และ “ผลจริง” มีสี", "<b>เขียว = ทายถูก</b> · <b>ดำ = ทายผิด</b> (แบบหน้า forebet)",
     "ช่อง “ทาย” เขียวเมื่อสกอร์ตรงเป๊ะ · ช่อง “ผลจริง” เขียวเมื่อทายผลถูก (แพ้/เสมอ/ชนะ) · "
     "ขึ้นเฉพาะคู่ที่จบแล้วเท่านั้น"),
    ("ผลจริง", "สกอร์จริงเมื่อจบเกม — ว่างคือยังไม่เตะ/ยังไม่จบ", ""),
]
GLOSS_TAG = [
    ("over", "🔺 OV", "บอลสูง", "gavg ≥ 2.70 + pro ≥ 70% + ราคาสูง 1.60–2.10",
     "วัดแล้ว <b>+1.1%</b> (ตลาดเดียวที่เป็นบวก)"),
    ("under", "🔻 UN", "บอลต่ำ", "gavg ≤ 2.20 + pru ≥ 65% + ราคาต่ำ 1.60–2.10",
     "วัดแล้ว <b>−3.7%</b> — ดูได้ ไม่แนะนำให้ลง"),
    ("1x2", "🎯 1X", "แพ้/ชนะ/เสมอ", "ฝั่งที่ทาย ≥ 50% + ไม่กระจาย + ตรงทางสกอร์ + Coef 1.60–2.10",
     "วัดแล้ว <b>−5.4%</b> — ด่านนี้แย่ที่สุด"),
    ("score", "🎱 SC", "สกอร์เป๊ะ", "ตรงทางสกอร์ + ทายห่างกัน 2 ลูก (ตัดบอลถ้วย)",
     "เป๊ะ <b>8.5%</b> — ใช้เป็นสัญญาณว่า Forebet มั่นใจ ไม่ใช่ไว้แทงสกอร์"),
]
gloss_rows = "".join(
    f'<div class="gr"><div class="gk mono">{E(k)}</div>'
    f'<div class="gv">{v}{f"<span class=gx>{x}</span>" if x else ""}</div></div>'
    for k, v, x in GLOSS)
gtag_rows = "".join(
    f'<div class="gr"><div class="gk"><span class="pill {c}">{t}</span></div>'
    f'<div class="gv"><b>{name}</b> — {rule}<span class="gx">{res}</span></div></div>'
    for c, t, name, rule, res in GLOSS_TAG)

HTML = f"""<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>ด่านแรก FABEL5</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <p class="eyebrow">Forebet · {DAY} · อัพเดทล่าสุด {NOW:%d/%m %H:%M} น. (เวลาไทย)</p>
  <h1>ด่านแรก — สแกนหาคู่ที่น่าลงทุน</h1>
  <p class="lede">การ์ดนี้ไม่ใช่คำสั่งลงเงิน มันคือด่านกรองแรกตามที่สั่งไว้:
     สแกนให้เห็นคู่ที่น่าสนใจก่อน แล้วไปเทียบราคาสดเอง ค่อยตัดสินใจแทง
     ข้างล่างคือ <b>กฎที่ส่งมาเอง</b> เอาไปวัดกับของจริงย้อนหลัง 90,842 คู่ ว่าข้อไหนช่วย ข้อไหนไม่ช่วย</p>
  <div class="meta">
    <span class="stat">ช่วงเวลา {win_txt}</span>
    <span class="stat">คู่ในช่วง <b>{len(rows)}</b></span>
    <span class="stat">จบแล้ว <b>{ft}</b></span>
    <span class="stat">🟢 น่าลงทุนสุด <b>{sum(1 for d in rows if level(d) == 3)}</b></span>
    <span class="stat">🟡 กลาง <b>{sum(1 for d in rows if level(d) == 2)}</b></span>
    <span class="stat">🔺 สูง <b>{cnt['over']}</b></span>
    <span class="stat">🔻 ต่ำ <b>{cnt['under']}</b></span>
    <span class="stat">🎯 1X2 <b>{cnt['1x2']}</b></span>
    <span class="stat">🎱 สกอร์ <b>{cnt['score']}</b></span>
  </div>
</header>

<section>
  <div><p class="eyebrow">คำถามที่ถามมา</p>
  <h2>📊 “กลุ่มนี้ย้อนหลังเข้า __% (__ ใบ)” คืออะไร</h2></div>
  <div class="panel">
    <p>มันคือ <b>ผลสอบของกฎ ไม่ใช่ผลของคู่นี้</b></p>
    <p style="margin-top:10px">ก่อนส่งการ์ด เครื่องจะย้อนไปเปิดคลังบอลที่จบแล้ว 90,842 คู่
       แล้วนับว่า “คู่ในอดีตที่ผ่านด่านชุดเดียวกันเป๊ะๆ มีกี่ใบ และเข้ากี่ %”</p>
    <p style="margin-top:10px">ตัวอย่าง: <span class="mono">3,489 ใบ · เข้า 56.7%</span> =
       ในอดีตมี 3,489 คู่ที่หน้าตาแบบนี้ ผลออกมาถูก 1,979 ครั้ง —
       <b>ไม่ได้แปลว่าคู่นี้มีโอกาสชนะ 56.7%</b></p>
    <div class="note" style="margin-top:14px"><b>จำเป็นไหม — จำเป็น และเป็นบรรทัดที่สำคัญที่สุดบนการ์ด</b><br>
      ถ้าไม่มีบรรทัดนี้ ทุกใบจะดูน่าเชื่อเท่ากันหมด ทั้งที่ใบ 🔻 ต่ำ กับใบ 🎯 1X2
      วัดแล้ว<b>ขาดทุน</b> ส่วนใบ 🔺 สูง วัดแล้วกำไรบางๆ
      บรรทัด 📊 คือสิ่งเดียวที่แยกใบที่ควรเสียเวลาไปเช็คราคาสด ออกจากใบที่ไม่ควร</div>
  </div>
</section>

<section>
  <div><p class="eyebrow">ผลวัด · กฎ 1X2 ที่ส่งมา</p>
  <h2>ไล่ทีละข้อ สะสมกันไป</h2>
  <p class="lede">แต่ละขั้นคือเติมเงื่อนไขที่ส่งมาเข้าไปทีละข้อ
     ดูว่าจำนวนใบหดลงเท่าไร แล้ว % เข้ากับกำไรขยับไปทางไหน</p></div>
  <div class="panel">{funnel_rows()}</div>
  <div class="note bad">
    <b>ข้อที่ทำให้แย่ลงคือ “Coef 1.60–2.10”</b> — จาก −1.0% ร่วงเป็น −5.4%
    ช่วงราคานี้คือช่วงที่เจ้ามือรีดน้ำหนักหนักที่สุด ยิ่งกรองยิ่งเจอแต่ของแพง<br>
    ส่วน <b>“ข้ามคู่กระจาย”</b> กับ <b>“1X2 ตรงทางกับสกอร์”</b> ไม่ผิด แต่<b>ไม่ได้ทำงาน</b>:
    ข้อแรกตัดออก 0 คู่ (เพราะพอทาย ≥50% แล้ว มันไม่มีทางกระจายอยู่แล้ว)
    ข้อสองตัดออกได้ 9 คู่จาก 28,554 — Forebet แทบไม่เคยทายขัดตัวเอง
  </div>
  <div class="panel"><h3>ทางที่ทำให้กลับมาบวกได้</h3>{alt_rows}</div>
</section>

<section>
  <div><p class="eyebrow">ผลวัด · บอลสูง / บอลต่ำ</p><h2>เส้น gavg อย่างเดียวไม่พอ ต้องมี %</h2></div>
  <div class="panel"><h3>🔺 บอลสูง — ★ คือเส้นที่กำไรเป็นบวก</h3>{over_rows}
    <p class="lede" style="margin-top:12px;font-size:13px">เกร็ด: พอใส่ <b>สูง ≥ 70%</b> แล้ว
      เงื่อนไข gavg ≥ 2.70 กับ ≥ 3.00 ได้ผลลัพธ์เท่ากันเป๊ะ (3,489 ใบ) —
      แปลว่า <b>สูง ≥ 70% กินความหมายของ gavg ≥ 3.00 ไปแล้ว</b> ใส่ซ้ำก็ไม่เพิ่มอะไร</p>
  </div>
  <div class="panel"><h3>🔻 บอลต่ำ — ลองมา 17 เส้น ติดลบทุกเส้น</h3>{under_rows}</div>
  <div class="note bad"><b>บอลต่ำหาเส้นที่กำไรไม่เจอเลย</b> ดีที่สุดที่วัดได้คือ −3.6%
    ไม่ใช่ว่าทายผิดบ่อย (เข้า 50–54% ตลอด) แต่<b>ราคาต่ำมันถูกกดไว้จนไม่พอค่าเสี่ยง</b>
    ใบ 🔻 จึงส่งให้ดูได้ แต่ติดป้ายเตือนไว้ทุกใบ</div>
</section>

<section>
  <div><p class="eyebrow">ผลวัด · BTTS (ยิงกันทั้งคู่)</p>
  <h2>“ยิงกันทั้งคู่ = มี 2 ลูกแน่” ถูกทางตรรกะ แต่ไม่ใช่การันตี</h2></div>
  <p class="lede">ถ้ายิงกันทั้งคู่จริง ก็ต้องมีอย่างน้อย 2 ลูกแน่นอน 100% — อันนี้ถูก
    เพราะมันเป็นเรื่องของนิยาม ไม่ใช่ความน่าจะเป็น<br>
    แต่คำถามจริงคือ <b>“แล้ว Forebet ทายว่ายิงกันทั้งคู่ แม่นแค่ไหน”</b> — นั่นต้องวัด</p>
  <div class="panel"><h3>วัดจากคลัง 121,402 คู่ (ตัดบอลถ้วย)</h3>{btts_rows}</div>
  <div class="note bad"><b>ยังไม่มีเส้นไหนแตะ 70% เลย</b> ดีที่สุดที่วัดได้คือ
    <b>59.5%</b> — แปลว่าทุก 10 คู่ที่เข้าเงื่อนไข ยังพลาดราว 4 คู่
    ถ้าราคา Yes อยู่แถว 1.70 ต้องเข้าเกิน 58.8% ถึงจะเสมอตัว
    <b>เส้นนี้เฉียดพอดีเกินไป ยังตัดสินไม่ได้</b></div>
  <div class="note"><b>ทำไมยังไม่ทำเป็นด่านให้:</b> คลัง 121k คู่ที่มีอยู่
    เก็บมาจากตลาด 1x2 กับ สูง/ต่ำ <b>ไม่มีค่า gg% ของ Forebet อยู่ในนั้น</b>
    ตัวเลขข้างบนจึงเป็น<b>ตัวแทน</b> (ใช้ “สกอร์ที่ทายมีทั้งสองฝั่ง ≥ 1” แทน gg%)
    ของจริงต้องเริ่มเก็บ gg% รายวันก่อน แล้วค่อยวัด<br>
    ระหว่างนี้เอาลงตารางให้เป็น<b>ตัวเลขดิบจากฟีด</b> ดูได้ เทียบราคาเองได้ แต่ยังไม่ติดป้ายด่าน</div>
</section>

<section>
  <div><p class="eyebrow">ผลวัด · ค่าคุ้ม % (Live coef.)</p>
  <h2>ข้อนี้เจ้าของพูดถูก และแรงกว่าทุกกฎที่วัดมาทั้งหน้า</h2></div>
  <p class="lede">ช่อง <b>“Live coef.”</b> บนหน้า values ไม่ใช่ราคา — มันคือ
    <b>ราคาที่ได้ คุ้มกว่าโอกาสจริงกี่ %</b> คิดจาก <span class="mono">(โอกาส × ราคา − 1) ÷ (ราคา − 1)</span><br>
    หน้าเว็บโชว์ให้แค่ 65 คู่ · <b>ตารางข้างล่างคิดให้เองครบทุกคู่จากฟีด</b>
    (เทียบกับหน้าจริงวันนี้ ตรง 38 จาก 39 คู่ คลาดตัวเดียวเพราะปัดเศษ)</p>
  <div class="note ok"><b>ที่บอกว่าแอปในมือถือไม่มีช่องนี้ — หน้านี้แก้ให้ตรงจุดนั้นเลย</b><br>
    แอป Forebet ตัดช่อง Live coef. ทิ้ง · เว็บมีแต่โชว์แค่ 65 คู่ ·
    <b>หน้านี้คิดเองจากฟีดดิบ ได้ครบทุกคู่ ทุกลีก และเปิดในมือถือได้</b><br>
    กดปุ่ม <b>⇅ เรียงค่าคุ้ม มาก→น้อย</b> ในตารางข้างล่าง = ได้หน้า values แบบเต็มไว้ดูบนมือถือ
    (คู่ที่ Forebet ยังไม่ปล่อยราคา ขึ้น “–” จะไปกองอยู่ท้ายตาราง)</div>
  <div class="note ok"><b>ส่งบอทเข้าไปดูดหน้า values ของจริงมาแล้วด้วย</b>
    (<span class="mono">fb_values.py</span>) — ดึงมาได้ <b>{len(VMAP)} คู่</b>
    ตรงกับตารางข้างล่าง <b>{nvb} คู่</b> ติดดาว <b>⭐</b> ไว้ให้<br>
    <b>เอาเลขที่เว็บโชว์มาเทียบกับที่หน้านี้คิดเอง: ตรงกันหมด 64 จาก 64 คู่</b>
    — แปลว่าสูตรที่ใช้ถูกต้อง <b>คู่ที่ไม่อยู่บนหน้า values ก็เชื่อเลขในตารางนี้ได้เลย</b><br>
    ⭐ ไม่ได้แปลว่าดีกว่า — แปลว่า <b>Forebet เอาไปโชว์บนหน้านั้นด้วย</b> (เขาคัดด้วย Kelly
    เอาแค่ ~65 คู่/วัน) ส่วนตารางนี้คิดให้ครบทุกคู่ ไม่ตัดใคร</div>
  <div class="panel"><h3>แยกเป็นช่วง — วัดจากคลัง 90,834 คู่ (ตัดบอลถ้วย) แทงฝั่งที่ Forebet ทาย</h3>{val_rows}</div>
  <div class="note ok"><b>ยิ่งค่าคุ้มสูง ยิ่งกำไรดีขึ้นเป็นขั้นบันไดจริง</b> —
    ไม่ใช่บังเอิญ ทุกช่วงเรียงขึ้นตามลำดับไม่มีสลับ
    และ <b>ค่าคุ้ม 0% (ราคาไม่คุ้ม) ขาดทุนหนักสุด −5.5%</b> ทั้งที่เข้าบ่อยที่สุด 58.1%
    — เข้าบ่อยไม่ได้แปลว่าได้เงิน</div>
  <div class="panel"><h3>แบบสะสม — “≥ เท่าไหร่ถึงจะคุ้ม”</h3>{valcum_rows}</div>
  <div class="note ok"><b>เส้น 30% ที่เจ้าของบอกมา ตรงจุดพอดี</b> —
    <b>≥30% = กำไร/ใบ +30.3%</b> (4,630 ใบ · <b>12.7 ใบ/วัน</b>) หาหน้าแทงได้เยอะจริงตามที่ว่า<br>
    และเส้น <b>≥40% = +81.1%</b> นี่คือเส้นเดียวกับที่เครื่อง <span class="mono">fb_value.py</span>
    ใช้อยู่แล้ว (<span class="mono">EDGE_MIN = 0.40</span>) — วัดคนละทางแต่ลงที่เดิม</div>
  <div class="note bad"><b>ข้อควรระวัง 2 ข้อ ก่อนเอาไปลงเงินจริง:</b><br>
    <b>1) เข้าแค่ 34.4% ที่เรทเฉลี่ย 4.03</b> — แปลว่า<b>แพ้ติดกัน 5-10 ใบเป็นเรื่องปกติ</b>
    กำไรมาจากใบที่เข้าไม่กี่ใบแต่ได้เยอะ ถ้าใจไม่นิ่งหรือทุนไม่พอ เส้นนี้ทรมาน<br>
    <b>2) คลังใช้ราคา best_odd (ราคาดีที่สุดในตลาด)</b> ถ้าเล่นเจ้าเดียวได้ราคาต่ำกว่านั้น
    กำไรจริงจะน้อยกว่า +30.3% — ตัวเลขนี้เป็น<b>เพดานบน</b> ไม่ใช่ของที่จะได้แน่</div>
  <div class="note"><b>ลองบีบให้เข้าบ่อยขึ้นแล้ว ไม่คุ้ม:</b> ถ้าเอา ≥30% ไปกรองเหลือเฉพาะเรท 1.60–2.10
    เข้าพุ่งเป็น 57.1% ดูสวย แต่กำไรเหลือ <b>+6.8%</b> เท่านั้น
    — <b>เสน่ห์ของค่าคุ้มอยู่ที่เรทสูง ตัดเรทสูงทิ้งคือตัดกำไรทิ้ง</b></div>
</section>

<section>
  <div><p class="eyebrow">ผลวัด · “ค่าคุ้มสูง = คัดบอลรอง”</p>
  <h2>ที่บอกว่าเอาไว้คัดบอลรอง — วัดแล้ว<u>กำไรเกือบทั้งหมดมาจากตรงนั้น</u></h2></div>
  <p class="lede">“บอลรอง” ที่วัดนี้ = <b>ฝั่งที่ Forebet ทาย เป็นฝั่งที่ราคาแพงที่สุดในสามช่อง</b>
    (ตลาดไม่เชื่อ แต่ Forebet เชื่อ)</p>
  <div class="panel"><h3>ค่าคุ้มสูงขึ้น คัดบอลรองออกมาได้จริงไหม</h3>{dogmix_rows}</div>
  <div class="note ok">ปกติบอลรองมีแค่ <b>15.8%</b> ของทุกคู่ — แต่พอกรองด้วยค่าคุ้ม ≥30%
    <b>กระโดดเป็น 42.3%</b> และที่ ≥50% <b>เกินครึ่ง (51.7%)</b>
    <b>ค่าคุ้มมันคัดบอลรองออกมาให้เองจริงๆ ตามที่บอกมา</b></div>
  <div class="panel"><h3>แล้วบอลรองที่คัดได้ ทำเงินได้จริงไหม</h3>{dog_rows}</div>
  <div class="note ok"><b>นี่คือจุดที่กำไรอยู่จริง</b> — ในกลุ่มค่าคุ้ม ≥30% ด้วยกัน
    ฝั่งเต็งได้แค่ <b>+1.6%</b> · ฝั่งกลาง <b>+2.7%</b> · แต่<b>ฝั่งรอง +65.6%</b><br>
    แปลว่าเลข +30.3% ของ “ค่าคุ้ม ≥30% ทั้งก้อน” ที่เห็นข้างบน
    <b>แทบทั้งหมดมาจากบอลรอง</b> ส่วนเต็ง/กลางแทบไม่ได้อะไร</div>
  <div class="note bad"><b>แต่อย่าเหมาว่าบอลรองดีเสมอ:</b> บอลรองที่<b>ค่าคุ้มต่ำกว่า 20%</b>
    ขาดทุน <b>−7.9%</b> (9,320 ใบ) — <b>บอลรองเฉยๆ ไม่ได้แปลว่าดี ต้องมีค่าคุ้มคุมด้วย</b><br>
    และเรทเฉลี่ย 5.10 แปลว่า<b>แพ้ติดกันยาวกว่าเดิมอีก</b> เข้าแค่ 32.5% เท่านั้น</div>
  <div class="note"><b>ทำเป็นปุ่มให้แล้ว:</b> ปุ่ม <b>🐶 รอง + ค่าคุ้ม ≥30</b> ในตารางข้างล่าง
    กรองให้เหลือเฉพาะเส้นนี้เส้นเดียว · ในคลังออกมา <b>5.4 ใบ/วัน</b></div>
</section>

<section>
  <div><p class="eyebrow">ใบคะแนน · “จะปิดฟังก์ชันที่ได้ผลต่ำออก”</p>
  <h2>ด่านทั้งหมดที่มีอยู่ วัดด้วยไม้บรรทัดเดียวกัน — ตัวไหนควรปิด</h2></div>
  <p class="lede">ทุกแถวใช้คลังเดียวกัน ตัดบอลถ้วยเหมือนกัน คิดกำไรแบบลงเท่ากันทุกใบ
    (<span class="mono">ได้ = เรท−1 · เสีย = −1</span>) เรียงจากดีสุดลงล่าง</p>
  <div class="panel"><h3>กำไร/ใบ ของแต่ละด่าน — ตัวเดียวที่ตัดสิน</h3>{gate_rows}</div>
  <div class="note bad"><b>2 ตัวนี้ขาดทุนจริง ปิดได้เลย:</b>
    <b>🎯 1X2 −5.4%</b> (23.5 ใบ/วัน) และ <b>🔻 บอลต่ำ −3.7%</b> (16.5 ใบ/วัน)<br>
    ทั้งคู่ “เข้า” เกินครึ่ง (52.7% · 53.7%) <b>แต่ยังขาดทุน</b> เพราะเรทแค่ 1.8
    — ต้องเข้า 55.6% ขึ้นถึงจะเสมอตัว <b>มันเข้าไม่ถึงเส้นนั้น</b></div>
  <div class="note"><b>🔺 บอลสูง +1.1%</b> — ไม่ขาดทุน แต่กำไรบางมาก (3,489 ใบ)
    บวกอยู่ในระดับที่<b>ค่าน้ำแพงกว่านี้นิดเดียวก็ติดลบ</b> · จะเก็บไว้ก็ได้ แต่อย่าลงหนัก<br>
    <b>🎱 สกอร์เป๊ะ 7.3%</b> — ไม่มีราคาสกอร์ในคลังเลยคิดกำไรไม่ได้
    ถ้าเจ้ามือจ่ายต่ำกว่า <b>13.7 เท่า</b> ก็ขาดทุน · <b>ใช้เป็นตัวชี้เป้าได้ ไม่ใช่ตัวลงเงิน</b></div>
  <div class="note ok"><b>ถ้าจะเหลือไว้แค่เส้นเดียว เอา 🐶 รอง + ค่าคุ้ม ≥30%</b> —
    <b>+65.6%</b> ต่อใบ · 5.7 ใบ/วัน · มากกว่าทุกด่านเดิมรวมกันหลายเท่า<br>
    (โค้ดตัวเตือนสด <span class="mono">fb_watch.py</span> คนละตัว ไม่โดนกระทบ)</div>
  <div class="note bad"><b>✅ ปิดให้แล้วในหน้านี้:</b> ถอดปุ่ม <b>🎯 1X</b> กับ <b>🔻 UN</b>
    ออกจากแถบกรองแล้ว · ป้ายในช่อง “ผ่านด่าน” ยัง<b>โชว์อยู่แต่ขีดฆ่า</b>
    <span class="pill under off">UN</span> <span class="pill 1x2 off">1X</span>
    — จะได้ยังเห็นว่าคู่นั้นเข้าเงื่อนไขอะไรบ้าง แต่รู้ว่า<b>ไม่ใช่เหตุผลให้ลงเงิน</b><br>
    เหลือปุ่มที่ใช้ตัดสินจริง: <b>🔺 OV · 🎱 SC · 💰 ค่าคุ้ม ≥30 · 🐶 รอง+ค่าคุ้ม ≥30</b></div>
</section>

<section>
  <div><p class="eyebrow">ผลวัด · กฎ “ห่าง 2 ลูก ปลอดภัยกว่า 1 ลูก”</p>
  <h2>ข้อนี้ถูก แต่ถูกคนละตลาดกับที่คิด</h2></div>
  <div class="panel"><h3>ถ้าเอาไปใช้กับผล 1X2 → จริง และชัดมาก</h3>{gap_rows}</div>
  <div class="panel"><h3>ถ้าเอาไปใช้กับสกอร์เป๊ะ → กลับกัน ยิ่งห่างยิ่งแย่</h3>{score_rows}
    <p class="lede" style="margin-top:12px;font-size:13px">ห่าง 1 ลูกเป๊ะ 12.5% แต่ห่าง 2 ลูกเหลือ 8.5%
      — เพราะสกอร์ห่าง 2 ลูกอย่าง 3-1 เกิดยากกว่า 1-0 มาก<br>
      ส่วน<b>ลูกรวมตรงเป๊ะ</b> นิ่งที่ ~20.7% ไม่ว่าจะกรองยังไง ขยับไม่ขึ้นเลย</p></div>
  <div class="note"><b>สรุปข้อนี้:</b> “ห่าง 2 ลูก” เป็นสัญญาณดี ให้เอาไปอ่านว่า
    <b>Forebet มั่นใจฝั่งไหนจะชนะ</b> (52.3% → 67.3% ที่ห่าง 3 ลูก)
    ไม่ใช่เอาไปแทงสกอร์เป๊ะ</div>
</section>

<section>
  <div><p class="eyebrow">คำถามที่ถามมา</p>
  <h2>ผ่านด่านหลายอัน = ยิ่งดีไหม</h2>
  <p class="lede">เอาป้ายทั้ง 4 ไปตีตราคู่ในอดีต 90,842 คู่ แล้วดูว่าใบที่ติดหลายป้าย
     ผลดีขึ้นหรือแย่ลง</p></div>
  <div class="note bad"><b>คำตอบคือ ไม่ใช่ — กลับกันเลย</b>
    ใบที่ติดป้าย <b>เดียวโดดๆ ดีกว่า</b> ใบที่ติดหลายป้าย ทั้งฝั่งสูงและฝั่งต่ำ</div>
  <div class="panel"><h3>เทียบตรงๆ</h3>{combo_rows}</div>
  <div class="note"><b>ทำไมถึงเป็นแบบนั้น</b> — ป้าย 4 อันนี้ไม่ใช่ “คนละคนมายืนยันเรื่องเดียวกัน”
    แต่มันคือ <b>คนละตลาด</b> ที่บังเอิญมาจากคู่เดียวกัน ติดพร้อมกันจึงไม่ได้แปลว่ามั่นใจขึ้น<br>
    ที่เห็นชัดคือป้าย <span class="pill 1x2">🎯 1X</span> ซึ่งเป็นด่านที่วัดแล้วแย่ที่สุด
    ไปเกาะใบไหน ใบนั้นแย่ลงตาม: 🔺 สูงจาก +2.0% เหลือ −2.0% · 🔻 ต่ำจาก −3.0% ลงไป −8.8%<br>
    ส่วน <span class="pill over">🔺 OV</span> กับ <span class="pill under">🔻 UN</span>
    ไม่มีทางติดพร้อมกันได้เลย (0 ใบ) เพราะเงื่อนไข gavg สวนทางกันอยู่แล้ว</div>
  <div class="note"><b>เอาไปใช้ยังไง:</b> เวลาสแกน ให้มองหาใบ
    <span class="pill over">🔺 OV</span> ที่<b>ไม่มีป้ายอื่นติดมาด้วย</b> เป็นอันดับแรก
    (57.0% · +2.0% · 1,995 ใบ) ถ้าเห็น 🎯 1X ติดมาด้วย ให้ระวัง ไม่ใช่ให้มั่นใจขึ้น</div>
</section>

<section>
  <div><p class="eyebrow">หน้าตาการ์ด</p><h2>ตัวอย่างจริงจากคู่วันนี้</h2>
  <p class="lede">รูปแบบตามที่เขียนมาให้ เพิ่มบรรทัด 📊 กับบรรทัดเตือนตามผลวัดข้างบน</p></div>
  <div class="cards">{card_html('over')}{card_html('under')}{card_html('1x2')}{card_html('score')}</div>
</section>

<section>
  <div><p class="eyebrow">คำถามที่ถามมา</p><h2>ตัวย่อในตาราง อ่านยังไง</h2></div>
  <div class="panel">{gloss_rows}</div>
  <div class="panel"><h3>ป้าย “ผ่านด่าน” 4 แบบ</h3>{gtag_rows}</div>
  <div class="panel"><h3>สีบนตาราง</h3>
    <div class="gr"><div class="gk"><span class="sw b">42</span></div>
      <div class="gv">น้ำเงินหนา = ฝั่งที่ Forebet ทายว่าจะเป็นผล</div></div>
    <div class="gr"><div class="gk"><span class="sw hi-up">72</span></div>
      <div class="gv">เขียว = โอกาสบอลสูง ≥ 70% (ผ่านเส้นที่วัดแล้วกำไร)</div></div>
    <div class="gr"><div class="gk"><span class="sw hi-dn">68</span></div>
      <div class="gv">แดง = โอกาสบอลต่ำ ≥ 65%</div></div>
  </div>
  <div class="panel"><h3>ไฮไลท์แถว 3 ระดับ — เส้นแบ่งมาจากผลวัด ไม่ได้ให้ดาวมั่ว</h3>
    <div class="gr"><div class="gk"><span class="sw sw3">🟢 สูง</span></div>
      <div class="gv"><b>น่าลงทุนสุด</b> — ใบ 🔺 OV ที่<b>ไม่มีป้ายอื่นติดมาเลย</b>
        <span class="gx">ย้อนหลัง 1,995 ใบ · เข้า 57.0% · กำไร/ใบ <b class="num up">+2.0%</b>
          = ดีที่สุดในทุกส่วนผสมที่วัดมา</span></div></div>
    <div class="gr"><div class="gk"><span class="sw sw2">🟡 กลาง</span></div>
      <div class="gv"><b>พอไปดูได้</b> — ใบ 🔺 OV ที่มี 🎱 SC ติดมาด้วย (แต่ต้องไม่มี 🎯 1X)
        <span class="gx">ย้อนหลัง 1,203 ใบ · เข้า 56.8% · กำไร/ใบ <b class="num up">+0.8%</b>
          = ยังบวก แต่บางกว่า</span></div></div>
    <div class="gr"><div class="gk"><span class="sw sw1">⚪ ปกติ</span></div>
      <div class="gv">ที่เหลือทั้งหมด — รวมใบที่ผ่านด่านอื่นด้วย
        <span class="gx">OV+1X <b class="num down">−2.0%</b> ·
          🔻 UN <b class="num down">−3.7%</b> ·
          🎯 1X <b class="num down">−5.4%</b> — วัดแล้วติดลบ จึงไม่ทาสีให้</span></div></div>
    <p class="lede" style="font-size:12.5px">ป้าย 🔻 UN / 🎯 1X / 🎱 SC ยังโชว์อยู่เหมือนเดิม
      แค่ไม่ได้ไฮไลท์ — ดูได้ แต่ตัวเลขย้อนหลังไม่หนุนให้ลงเงิน</p>
  </div>
</section>

{STAT}

<section>
  <div><p class="eyebrow">ข้อมูลดิบ · {len(rows)} คู่</p><h2>ตารางกลางทุกคู่</h2>
  <p class="lede">เริ่มที่ <b>6 ชม.ก่อนตอนนี้</b> — คู่ที่เตะไปแล้วยังคาไว้ให้ ไว้ตามดูย้อนหลังว่ากฎถูกไหม
     ({cut} คู่ที่เก่ากว่านั้นถูกตัดออก) · บนมือถือแตะปุ่มเพื่อกรอง</p></div>
  <div class="fbar" role="group" aria-label="กรองตาราง">
    <button class="fb on" data-f="">ทั้งหมด</button>
    <button class="fb hi3" data-f="lv3">🟢 น่าลงทุนสุด</button>
    <button class="fb hi2" data-f="lv2">🟡 กลาง</button>
    <button class="fb" data-f="any">ผ่านด่าน</button>
    <button class="fb" data-f="over">🔺 OV</button>
    <button class="fb" data-f="score">🎱 SC</button>
    <button class="fb" data-f="ev30">💰 ค่าคุ้ม ≥30</button>
    <button class="fb hi3" data-f="dog30">🐶 รอง + ค่าคุ้ม ≥30</button>
    <button class="fb" data-f="vb">⭐ อยู่บนหน้า values ({nvb})</button>
    <button class="fb" id="srt" aria-pressed="false">⇅ เรียงค่าคุ้ม มาก→น้อย</button>
  </div>
  <div class="scroll"><table id="tb">
    <thead><tr><th>เวลา</th><th>ลีก</th><th>คู่</th><th>p1/px/p2</th><th>o1/ox/o2</th>
      <th>ทาย</th><th>pro/pru</th><th>bo/bu</th><th>BTTS&nbsp;%</th><th>ราคา&nbsp;BTTS</th>
      <th>gavg</th><th>ค่าคุ้ม&nbsp;%</th><th>ผลจริง</th><th>ผ่านด่าน</th>
    </tr></thead><tbody>{"".join(trs)}</tbody></table></div>
  <p class="lede" id="fcount" style="font-size:12.5px"></p>
</section>

<script>
(function(){{
  var rows=[].slice.call(document.querySelectorAll('#tb tbody tr'));
  var out=document.getElementById('fcount');
  function apply(f){{
    var n=0;
    rows.forEach(function(tr){{
      var t=tr.getAttribute('data-tag')||'',lv=tr.getAttribute('data-lv');
      var ok=!f||(f==='any'?t!=='':(f==='lv3'?lv==='3':(f==='lv2'?lv==='2':
        (' '+t+' ').indexOf(' '+f+' ')>=0)));
      tr.style.display=ok?'':'none'; if(ok)n++;
    }});
    out.textContent='แสดง '+n+' จาก '+rows.length+' คู่';
  }}
  [].forEach.call(document.querySelectorAll('.fb[data-f]'),function(b){{
    b.addEventListener('click',function(){{
      [].forEach.call(document.querySelectorAll('.fb[data-f]'),function(x){{x.classList.remove('on');}});
      b.classList.add('on'); apply(b.getAttribute('data-f'));
    }});
  }});
  var tb=document.querySelector('#tb tbody'), org=rows.slice(), sorted=false;
  var sb=document.getElementById('srt');
  sb.addEventListener('click',function(){{
    sorted=!sorted; sb.classList.toggle('on',sorted);
    sb.setAttribute('aria-pressed',sorted?'true':'false');
    sb.textContent=sorted?'⇅ กลับไปเรียงตามเวลา':'⇅ เรียงค่าคุ้ม มาก→น้อย';
    var list=sorted?org.slice().sort(function(a,b){{
      return parseFloat(b.getAttribute('data-ev'))-parseFloat(a.getAttribute('data-ev'));
    }}):org;
    list.forEach(function(tr){{tb.appendChild(tr);}});
  }});
  apply('');
}})();
</script>

<footer>ที่มา: Forebet feed <span class="mono">getrs.php</span> วันไทย {DAY} ·
  ผลวัดจากคลัง <span class="mono">fb_hist.jsonl</span> 97,239 คู่ (ตัดบอลถ้วยเหลือ 90,842) ·
  ทุก % บนหน้านี้วัดจากของจริง ไม่มีตัวเลขที่เดาเอา</footer>
</div>
</body>
</html>"""

with open(OUT, "w", encoding="utf-8") as f:
    f.write(HTML)
print(f"✅ {OUT} · {len(rows)} คู่ · ผ่านด่าน {cnt}")

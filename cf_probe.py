# -*- coding: utf-8 -*-
"""cf_probe.py — เทสต์เดียว: IP ของ GitHub Actions ทะลุ Cloudflare ของ Forebet ได้ไหม

ทำไมต้องมี: สายหลักใหม่ (forebet_api.py → getrs.php) ได้ 374-413 คู่/วัน
แต่ยังไม่เคยพิสูจน์ว่ายิงจาก runner ผ่าน — บนเครื่องผู้ใช้ (IP บ้าน) ผ่านแน่นอน
ถ้า runner ไม่ผ่าน main.py จะถอยไปสาย Jina (42 คู่) เงียบๆ โดยเราไม่รู้ตัว

ผลลัพธ์ออก 3 ทาง: log ของ Actions · probe_out.md (commit กลับ repo) · Telegram
รันเดี่ยวๆ ไม่แตะ Gemini เลย → ไม่กินโควตา
"""

import os
import sys
import time
import datetime

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import forebet_api as fb
except Exception as e:  # นี่ก็เป็นผลเทสต์อย่างหนึ่ง (ไฟล์ไม่ได้ push ขึ้นมา)
    fb = None
    IMPORT_ERR = str(e)
else:
    IMPORT_ERR = ""

L = []          # บรรทัดรายงาน
VERDICT = []    # สรุปสั้นสำหรับ Telegram


def say(s):
    print(s, flush=True)
    L.append(s)


def _utc():
    return datetime.datetime.now(datetime.timezone.utc)


def bkk_today():
    return (_utc() + datetime.timedelta(hours=7)).strftime("%Y-%m-%d")


def main():
    day = bkk_today()
    say(f"# CF Probe · {_utc():%Y-%m-%d %H:%M} UTC · วันที่ทดสอบ {day}")
    say("")

    # ---------- 0) IP ของ runner (ไว้อ้างอิงเวลา Cloudflare เปลี่ยนใจ) ----------
    ip = "?"
    try:
        ip = requests.get("https://api.ipify.org", timeout=15).text.strip()
    except Exception as e:
        ip = f"อ่านไม่ได้ ({e})"
    say(f"- **IP runner**: `{ip}`")

    if fb is None:
        say(f"- ❌ **import forebet_api ไม่ได้**: {IMPORT_ERR} → สายหลักใช้ไม่ได้แน่นอน")
        VERDICT.append("❌ ไม่มี forebet_api.py บน repo")
        finish()
        return

    # ---------- 1) ยิงตรงไม่มีคุกกี้ (คาดว่า 403 — เป็นตัวยืนยันว่า Cloudflare ยังเปิดยาม) ----------
    try:
        r = requests.get(fb.FB_API, params={"ln": "en", "tp": "1x2", "in": day, "ord": "0",
                                            "tz": "+420", "tzs": "0", "tze": "0"},
                         headers={"User-Agent": fb.FB_UA}, timeout=45)
        naked = r.status_code
        jm = "Just a moment" in r.text[:2000]
        say(f"- **ยิงตรงไม่ priming คุกกี้**: HTTP {naked}{' (เจอหน้า Just a moment)' if jm else ''} · {len(r.content)} bytes")
    except Exception as e:
        say(f"- **ยิงตรงไม่ priming คุกกี้**: พัง — {e}")

    # ---------- 2) เปิดหน้า HTML เพื่อรับคุกกี้ ----------
    t0 = time.time()
    try:
        sess = fb.fb_session()
        say(f"- ✅ **เปิดหน้า HTML (priming)**: ผ่าน · {time.time() - t0:.1f}s · "
            f"คุกกี้ที่ได้: {', '.join(sess.cookies.keys()) or 'ไม่มีเลย'}")
    except Exception as e:
        say(f"- ❌ **เปิดหน้า HTML (priming)**: ไม่ผ่าน — {e}")
        VERDICT.append("❌ Cloudflare บล็อกตั้งแต่หน้า HTML → IP Actions ใช้สายหลักไม่ได้")
        jina_check()
        finish()
        return

    # ---------- 3) ยิง API จริง ทุกตลาด ----------
    total = {}
    okmk, badmk = [], []
    for i, tp in enumerate(fb.FB_MARKETS):
        t1 = time.time()
        try:
            rows, lg = fb.fb_feed(sess, tp, day)
            okmk.append(f"{tp}:{len(rows)}")
            say(f"- ✅ `tp={tp}` → {len(rows)} แถว · {len(lg)} ลีก · {time.time() - t1:.1f}s")
            for r in rows:
                mid = str(r.get("id") or "")
                if mid:
                    total[mid] = 1
        except Exception as e:
            badmk.append(tp)
            say(f"- ❌ `tp={tp}` → {e}")
        if i < len(fb.FB_MARKETS) - 1:
            time.sleep(1.0)

    say("")
    say(f"- **รวมคู่ไม่ซ้ำ (วัน {day} เวลายุโรป)**: **{len(total)}** คู่")

    if len(total) >= 300:
        VERDICT.append(f"✅ ผ่าน! Actions ยิง getrs.php ได้ {len(total)} คู่ ({' '.join(okmk)})")
    elif total:
        VERDICT.append(f"⚠️ ผ่านแบบไม่เต็ม: {len(total)} คู่ · ตลาดที่ล่ม: {', '.join(badmk) or '-'}")
    else:
        VERDICT.append("❌ priming ผ่าน แต่ getrs.php ไม่คืนข้อมูลเลย")

    jina_check()
    finish()


def jina_check():
    """เช็คสายสำรองด้วย — Jina ยิงตรงจาก IP Actions เคยโดนลิมิต (ต้องอ้อม PIKTAX ?ff=)"""
    try:
        r = requests.get("https://r.jina.ai/https://www.forebet.com/en/football-tips-and-predictions-for-today",
                         timeout=60)
        say(f"- **สายสำรอง Jina ยิงตรงจาก runner**: HTTP {r.status_code} · {len(r.content)} bytes")
    except Exception as e:
        say(f"- **สายสำรอง Jina ยิงตรงจาก runner**: พัง — {e}")

    base = os.environ.get("PIKTAX_STATE_URL", "").strip()
    if base:
        try:
            import urllib.parse
            u = base + "?ff=" + urllib.parse.quote(
                "https://www.forebet.com/en/football-tips-and-predictions-for-today", safe="")
            r = requests.get(u, timeout=90)
            say(f"- **สายสำรองอ้อม PIKTAX `?ff=`**: HTTP {r.status_code} · {len(r.content)} bytes")
        except Exception as e:
            say(f"- **สายสำรองอ้อม PIKTAX `?ff=`**: พัง — {e}")
    else:
        say("- **สายสำรองอ้อม PIKTAX `?ff=`**: ไม่ได้ตั้ง secret PIKTAX_STATE_URL → ข้าม")


def finish():
    body = "\n".join(L) + "\n"
    try:
        with open("probe_out.md", "w", encoding="utf-8") as f:
            f.write(body)
        print("📝 เขียน probe_out.md แล้ว", flush=True)
    except Exception as e:
        print(f"⚠️ เขียนไฟล์ไม่ได้: {e}", flush=True)

    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    cid = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tok and cid:
        msg = "🧪 <b>ผลเทสต์ IP GitHub Actions ↔ Cloudflare (Forebet)</b>\n" + "\n".join(VERDICT or ["ไม่มีผลสรุป"])
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": cid, "text": msg, "parse_mode": "HTML"}, timeout=30)
            print(f"📨 ส่ง Telegram: HTTP {r.status_code}", flush=True)
        except Exception as e:
            print(f"⚠️ ส่ง Telegram ไม่ได้: {e}", flush=True)


if __name__ == "__main__":
    main()

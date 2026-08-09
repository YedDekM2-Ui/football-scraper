# -*- coding: utf-8 -*-
"""ls_probe.py — เทสต์ว่า IP ของ GitHub Actions ยิง LiveScore CDN ได้ไหม

ทำไมต้องเทสต์: Forebet โดน Cloudflare เด้ง 403 จาก IP Actions มาแล้ว (cf_probe.py)
ถ้า LiveScore โดนด้วย = ย้ายขึ้นคลาวด์ไม่ได้ ต้องคิดใหม่

วัด 3 อย่าง: ต่อติดไหม · ช้าแค่ไหน · ได้ของครบไหม (มีนาที/สกอร์ครึ่งแรกจริง)
ผลออกเป็น ls_probe_out.md → เอาไปแปะใน Summary ของ run
"""
import datetime as dt
import json
import os
import sys
import time

import requests

URL = "https://prod-cdn-mev-api.livescore.com/v1/api/app/date/soccer/{day}/0"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ls_probe_out.md")

L = []


def say(s):
    print(s, flush=True)
    L.append(s)


def probe(day):
    u = URL.format(day=day)
    t0 = time.time()
    try:
        r = requests.get(u, headers=UA, timeout=45)
    except Exception as e:
        say(f"- ❌ `{day}` ต่อไม่ได้เลย: `{type(e).__name__}: {e}`")
        return False
    ms = int((time.time() - t0) * 1000)
    size = len(r.content)

    if r.status_code != 200:
        say(f"- ❌ `{day}` HTTP **{r.status_code}** · {size:,} bytes · {ms} ms")
        say(f"  - ตัวอย่างที่ได้กลับมา: `{r.text[:200]!r}`")
        return False

    try:
        j = r.json()
    except Exception:
        head = r.text[:200]
        blocked = any(k in head.lower() for k in ("just a moment", "cloudflare", "<!doctype"))
        say(f"- ❌ `{day}` ได้ 200 แต่ไม่ใช่ JSON "
            f"({'โดน Cloudflare กั้น' if blocked else 'รูปแบบเพี้ยน'}) · {size:,} bytes")
        say(f"  - `{head!r}`")
        return False

    stages = j.get("Stages") or []
    ev = live = withht = 0
    sample = []
    for st in stages:
        for e in st.get("Events", []):
            ev += 1
            eps = str(e.get("Eps") or "")
            if eps.rstrip("'").split("+")[0].strip().isdigit() or eps.upper() == "HT":
                live += 1
                if e.get("Trh1") is not None:
                    withht += 1
                if len(sample) < 3:
                    sample.append(f"{e['T1'][0]['Nm']} {e.get('Tr1')}-{e.get('Tr2')} "
                                  f"{e['T2'][0]['Nm']} · {eps} · ครึ่งแรก {e.get('Trh1')}-{e.get('Trh2')}")
    say(f"- ✅ `{day}` HTTP 200 · {size:,} bytes · **{ms} ms** · "
        f"ลีก {len(stages)} · คู่ {ev} · กำลังเตะ {live} · มีสกอร์ครึ่งแรก {withht}")
    for s in sample:
        say(f"  - {s}")
    return True


def main():
    now = dt.datetime.utcnow()
    say(f"# เทสต์ LiveScore จาก IP GitHub Actions")
    say(f"เวลา UTC: `{now:%Y-%m-%d %H:%M:%S}`")
    try:
        ip = requests.get("https://api.ipify.org", timeout=15).text.strip()
        say(f"IP ที่ใช้ยิง: `{ip}`")
    except Exception as e:
        say(f"IP: อ่านไม่ได้ ({e})")
    say("")
    say("## LiveScore CDN")
    ok = 0
    for k in (-1, 0, 1):
        if probe((now.date() + dt.timedelta(days=k)).strftime("%Y%m%d")):
            ok += 1

    say("")
    say("## Forebet ยิงตรง (คาดว่าโดน 403 — เช็คว่ายังต้องอ้อม ?ff= อยู่ไหม)")
    try:
        r = requests.get("https://www.forebet.com/scripts/getrs.php?ln=en&tp=1x2&"
                         f"in={now:%Y-%m-%d}&ord=0", headers=UA, timeout=30)
        body = r.text[:120]
        say(f"- HTTP **{r.status_code}** · {len(r.content):,} bytes · `{body!r}`")
    except Exception as e:
        say(f"- ❌ `{type(e).__name__}: {e}`")

    say("")
    say(f"## สรุป: LiveScore ผ่าน **{ok}/3** วัน → "
        + ("**ย้ายขึ้นคลาวด์ได้**" if ok >= 2 else "**ยังย้ายไม่ได้**"))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    sys.exit(0 if ok >= 2 else 1)


if __name__ == "__main__":
    main()

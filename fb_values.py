# -*- coding: utf-8 -*-
"""ดูดหน้า forebet.com/en/values ตรงๆ — เอาช่อง "Live coef." ของจริงมา

ทำไมต้องมี: แอป Forebet ในมือถือไม่มีช่องนี้ · เว็บมีแต่โชว์แค่ ~65 คู่
ตัวนี้ดูดของจริงมาให้ แล้ว fb_report.py เอาไปติดป้าย "อยู่บนหน้า values"
ให้รู้ว่าคู่ไหน Forebet เองก็คัดไว้ (เขาใช้ Kelly Criterion คัด)

ค่าที่ดูดได้ = เลขเดียวกับที่ fb_report คิดเอง — เทียบแล้ว 17 ส.ค. 69 ตรง 64/64 คู่
ฉะนั้นถ้าดูดไม่ได้ (เน็ตล่ม/โดนบล็อก) หน้ารายงานยังใช้ได้ปกติ แค่ไม่มีป้ายเฉยๆ

ใช้: python -X utf8 fb_values.py        (โชว์ที่ดูดได้)
"""
import gzip
import re
import urllib.request

URL = "https://www.forebet.com/en/values"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
IDX = {"1": 0, "X": 1, "2": 2}


def _get(timeout=45):
    req = urllib.request.Request(URL, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def parse(html):
    """แกะตาราง values → list ของ dict {id, nm, p, pred, lc, co, o}"""
    out = []
    for b in re.split(r"<div class='rcnt", html)[1:]:
        mid = re.search(r'href="/en/football/matches/[^"]*?-(\d+)"', b)
        lc = re.search(r'class="la_prmod tabonly">(-?\d+)%</div>', b)
        if not (mid and lc):
            continue
        nm = re.search(r'itemprop="name" content="(.*?)"', b)
        pr = re.findall(r"<div class=.fprc.>(.*?)</div>", b, re.S)
        pred = re.search(r'class="forepr"><span>(.*?)</span>', b)
        co = re.search(r'class="lscrsp"[^>]*>([\d.]+)</span>', b)
        ha = re.search(r'class="haodd">(.*?)</div>', b, re.S)
        ps = re.findall(r"<span[^>]*>(\d+)</span>", pr[0]) if pr else []
        od = re.findall(r"<span>([\d.]*)</span>", ha.group(1)) if ha else []
        out.append({
            "id": mid.group(1), "nm": nm.group(1) if nm else "",
            "p": [int(x) for x in ps[:3]], "pred": pred.group(1) if pred else "",
            "lc": int(lc.group(1)), "co": float(co.group(1)) if co else None,
            "o": [float(x) if x else None for x in od[:3]]})
    return out


def fetch_values(timeout=45):
    """ดูดหน้า values · พังก็คืน [] ไม่โยน error ออกไปล้มตัวเรียก"""
    try:
        return parse(_get(timeout))
    except Exception:
        return []


def vmap(timeout=45):
    """{match_id: live_coef%} — ใช้ติดป้ายในรายงาน"""
    return {r["id"]: r["lc"] for r in fetch_values(timeout)}


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    rows = fetch_values()
    print(f"ดูดจาก {URL} ได้ {len(rows)} คู่\n")
    ok = bad = skip = 0
    for r in sorted(rows, key=lambda x: -x["lc"]):
        i = IDX.get(r["pred"])
        chk = ""
        if i is None or len(r["p"]) < 3 or not r["o"] or not r["o"][i]:
            skip += 1
        else:
            c = r["o"][i]
            ev = max(0.0, (r["p"][i] / 100 * c - 1) / (c - 1) * 100) if c > 1 else 0.0
            if abs(round(ev) - r["lc"]) <= 1:
                ok += 1
            else:
                bad += 1
                chk = f"  ⚠️ คิดเองได้ {ev:.0f}%"
        print(f"  {r['lc']:4d}%  {r['nm'][:44]:44s} ทาย {r['pred']} @ "
              f"{r['co'] or 0:5.2f}{chk}")
    print(f"\nเทียบสูตรที่รายงานใช้: ตรง {ok} · คลาด {bad} · เช็คไม่ได้ {skip}")

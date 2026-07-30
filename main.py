import os
import re
import json
import time
import urllib.parse
import requests

# ⭐ ทางหลัก: ดึงจาก JSON API หลังปุ่ม "More" ของ Forebet (ครบทุกคู่) · Jina เหลือไว้เป็นตัวสำรอง
try:
    import forebet_api as fbapi
except Exception:
    fbapi = None

# SDK ใหม่ (google-genai) — ถ้าไม่มี/ลงไม่สำเร็จ ยังวิ่งต่อได้ด้วยเส้นทาง REST (ดูหัวข้อ 4.95)
try:
    from google import genai
except Exception:
    genai = None

# ==========================================
# 1. ค่าความลับจาก Environment Variables (GitHub Secrets)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# URL ของ PIKTAX (GAS) — ใช้ดึง Forebet ทะลุ Cloudflare (?ff=) และอ่านประวัติ pick (?fbhist=)
PIKTAX_STATE_URL = os.environ.get("PIKTAX_STATE_URL", "")
JINA_PREFIX = "https://r.jina.ai/"
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")  # ไม่ใส่ก็ได้

if not GEMINI_API_KEY:
    raise ValueError("❌ Error: ไม่พบ GEMINI_API_KEY ใน GitHub Secrets")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("❌ Error: ไม่พบข้อมูล Telegram Bot หรือ Chat ID ใน GitHub Secrets")

# เวลาไทยตอนที่ "อ่านสกอร์/ข้อมูลจาก Forebet" รอบนี้ — แปะท้ายทีเด็ดเพื่อให้เทียบกับนาฬิกา Telegram
# แล้วรู้ทันทีว่าข้อมูลเก่ากี่นาที (บางคู่สกอร์เปลี่ยนไปแล้วระหว่างที่ Gemini กำลังคิด)
FB_SNAP = ""

TELEGRAM_LIMIT = 4000  # เผื่อจากเพดานจริง 4096
MAX_MATCHES = 20       # คัดคู่เด่นสูงสุดกี่คู่ (แล้วแต่วัน บางวันน้อยกว่าได้ · เด่นสุดไว้บน)
# รุ่น Gemini (ฟรี) · flash-latest = alias รุ่นล่าสุด · ตัดรุ่นซ้ำ/ตายออก (กันเผาโควตา 20/วัน)
# ⚠️ ฟรี = 20 requests/วัน/รุ่น — ห้ามใส่รุ่นซ้ำ (flash-latest กับ 3.5-flash คือตัวเดียวกัน = รีทราย 429 เปล่า)
GEMINI_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]

# ==========================================
# 2. ส่งข้อความเข้า Telegram (ตัดยาวอัตโนมัติ · เรียกมือล้วน = ส่งพร้อมเสียงปกติเสมอ)
# ==========================================
def _post(text, use_markdown=True):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if use_markdown:
        payload["parse_mode"] = "Markdown"
    return requests.post(url, json=payload, timeout=15)

def _split_text(text, limit=TELEGRAM_LIMIT):
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks

def send_telegram_message(text):
    chunks = _split_text(text)
    for part in chunks:
        try:
            resp = _post(part, use_markdown=True)
            if resp.status_code != 200:
                resp = _post(part, use_markdown=False)
            print("✅ ส่งสำเร็จ" if resp.status_code == 200 else f"❌ ส่งไม่ผ่าน: {resp.text}")
        except Exception as e:
            print(f"❌ เกิดข้อผิดพลาดในการส่ง Telegram: {e}")

# ==========================================
# 4. ดึงข้อมูล Forebet (ผ่าน PIKTAX → Jina · IP GitHub โดน Cloudflare/Jina บล็อก)
# ==========================================
def _clean(text):
    if not text:
        return None
    t = text.strip()
    if not t or t.startswith(("BAD_URL", "FETCH_ERR", "HTTP_")):
        return None
    return t

def _compact(raw):
    """ตัด boilerplate (โลโก้/เมนู/รูป/อุณหภูมิ/ตัวเลขลอย) ออก เก็บแต่คู่+prob+ทีเด็ด+เหตุผล
       → 20+ ตลาดยัดเข้า Gemini ได้ครบ ไม่โดนตัด"""
    out, blank = [], False
    for l in raw.splitlines():
        s = l.strip()
        if not s:
            if not blank:
                out.append(""); blank = True
            continue
        blank = False
        if s.startswith("![Image") or s.startswith("[![Image"):
            continue
        if re.match(r'^\[[^\]]+\]\(https?://[^)]+\)\S*$', s) and "/matches/" not in s:
            continue  # ลิงก์ nav/เมนูภาษา (เก็บลิงก์ /matches/ ที่มีชื่อคู่)
        if "°" in s:
            continue  # สภาพอากาศ
        if re.match(r'^[+-]?\d+(?:\.\d+)?$', s):
            continue  # ตัวเลขลอย (avg goals / coef อเมริกัน)
        if s in ("no", "yes", "-", "no no no"):
            continue
        out.append(s)
    return "\n".join(out)

def scrape_football_data(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    if JINA_API_KEY:
        headers["Authorization"] = "Bearer " + JINA_API_KEY

    # วิธีหลัก: ผ่าน PIKTAX (Google IP → Jina → Forebet)
    if PIKTAX_STATE_URL:
        try:
            base = PIKTAX_STATE_URL.split("?")[0]
            proxy_url = base + "?ff=" + urllib.parse.quote(url, safe="")
            r = requests.get(proxy_url, headers=headers, timeout=90)
            data = _clean(r.text) if r.status_code == 200 else None
            if data:
                return data
            print(f"⚠️ ผ่าน PIKTAX ไม่ได้ (code={r.status_code}) ลอง Jina ตรง: {url}")
        except Exception as e:
            print(f"⚠️ ผ่าน PIKTAX error ({e}) ลอง Jina ตรง: {url}")

    # สำรอง: Jina ตรง
    try:
        r2 = requests.get(JINA_PREFIX + url, headers=headers, timeout=60)
        if r2.status_code != 200:
            print(f"❌ ดึงไม่สำเร็จ (Status: {r2.status_code}) : {url}")
            return None
        return _clean(r2.text)
    except Exception as e:
        print(f"❌ Error ในการดึงเว็บ {url}: {e}")
        return None

# ==========================================
# 4.5 แกะหน้า Asian Handicap → ตารางเส้นจริง (กัน Gemini มั่วราคา)
#     block: [ชื่อคู่ DD/MM/YYYY HH:MM](.../matches/slug-id) \n NN% \n Side line score
# ==========================================
# 🔗 ลิงก์คู่บอลของ Forebet — รูปแบบเปลี่ยนแล้ว (ก.ค. 2026)
#    เดิม: [ชื่อคู่ DD/MM/YYYY HH:MM](.../matches/slug-id)      ← เวลายุโรป 24 ชม.
#    ใหม่: [ชื่อคู่ MM/DD/YYYY h:mm AM/PM](.../matches/slug-id)  ← เวลา UTC 12 ชม. แบบอเมริกา
#    ของเดิมจับไม่ได้เลยสักคู่ → ตารางเวลาว่าง → prompt สั่ง "ไม่มีเวลา=ตัดทิ้ง" → บอลหายหมด
#    ตัวใหม่รับได้ทั้ง 2 แบบ + ดึง "เลข id ท้ายลิงก์" ออกมาด้วย (= MatchID ตัวจริง ใช้เป็นคีย์ถาวร)
#    ⚠️ กับดักที่ทำบอลหายเป็นลีก (แก้ 25 ก.ค. 2026): slug เดิมรับแค่ [a-z0-9.-]
#       ชื่อทีมที่มีสระ/อักษรพิเศษ → Jina เขียน URL เป็น %C3%A1 ฯลฯ → ไม่มี % ในคลาส = ไม่ติด
#       = ลีกที่ชื่อทีมมี accent หายทั้งลีก (เม็กซิโก Atlético/Mazatlán/Querétaro · ชิลี Ñublense/Unión ·
#         บราซิล Criciúma · เยอรมัน/สวีเดน ä ö ü ß) → ไม่มีเวลา → prompt สั่ง "ไม่มีเวลา=ตัดทิ้ง" → เงียบ
_SLUG = r'[A-Za-z0-9.%\-]'
_LINK_PAT = (r'\[(.+?)\s+(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})\s*([AP]M)?\]'
             r'\(https://www\.forebet\.com/en/football/matches/(' + _SLUG + r'+?)-(\d+)\)')
#    รูปแบบที่ 2 ที่ Forebet ใช้สลับกันในหน้าเดียวกัน: วันเวลาอยู่ "นอก" วงเล็บ + ชื่อทีมคั่นด้วย " - "
#       [Home - Away](.../matches/slug-id)21/07/2026 18:00
_LINK_PAT2 = (r'\[(.+?)\]\(https://www\.forebet\.com/en/football/matches/(' + _SLUG + r'+?)-(\d+)\)'
              r'\s*(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})\s*([AP]M)?')

def _to_thai_time(date_str, tm_str, ampm=None):
    """แปลงเวลาที่ Forebet แสดง → เวลาไทย · คืน HH:MM ล้วน
    มี AM/PM = รูปแบบใหม่ MM/DD/YYYY แบบ UTC   (ยืนยันแล้ว: อาร์เจนตินาเตะ 20:35 ท้องถิ่น = 23:35 UTC, ชิลี 20:30 = 00:30 UTC)
    ไม่มี AM/PM = รูปแบบเดิม DD/MM/YYYY เวลายุโรป
    """
    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        if ampm:
            dt = datetime.strptime(f"{date_str} {tm_str} {ampm.upper()}", "%m/%d/%Y %I:%M %p")
            src = "UTC"
        else:
            dt = datetime.strptime(f"{date_str} {tm_str}", "%d/%m/%Y %H:%M")
            src = "Europe/Paris"
        return dt.replace(tzinfo=ZoneInfo(src)).astimezone(ZoneInfo("Asia/Bangkok")).strftime("%H:%M")
    except Exception:
        # สำรอง เผื่อ zoneinfo ไม่มี: UTC→ไทย +7 · ยุโรป→ไทย +5
        try:
            h, m = tm_str.split(":")
            h = int(h)
            if ampm:
                p = ampm.upper()
                if p == "PM" and h != 12:
                    h += 12
                elif p == "AM" and h == 12:
                    h = 0
                return f"{(h + 7) % 24:02d}:{m}"
            return f"{(h + 5) % 24:02d}:{m}"
        except Exception:
            return tm_str

def parse_ah_table(raw):
    if not raw:
        return ""
    lines = [l.strip() for l in raw.splitlines()]
    link_re = re.compile('^' + _LINK_PAT + '$')
    pick_re = re.compile(r'^(Home|Away|Draw)\s+([+-]?\d+(?:\.\d+)?)\s+(\d+-\d+)$')
    prob_re = re.compile(r'^(\d{1,3})%$')
    rows = []
    for i, l in enumerate(lines):
        m = link_re.match(l)
        if not m:
            continue
        names, date_str, tm_raw, ampm, slug, mid = m.groups()
        tm = _to_thai_time(date_str, tm_raw, ampm)
        prob, side, line, pscore = "", "", "", ""
        for j in range(i + 1, min(i + 10, len(lines))):
            pm = prob_re.match(lines[j])
            if pm and not prob:
                prob = pm.group(1)
            km = pick_re.match(lines[j])
            if km:
                side, line, pscore = km.group(1), km.group(2), km.group(3)
                break
        if not side:      # Forebet ยังไม่ออกเรทคู่นี้ → ข้าม (จะไม่มีเส้นให้มั่ว)
            continue
        # 🔧 ทิศทางจริง = ดูจาก "สกอร์คาด" (ตัวชี้ว่า Forebet คิดว่าใครชนะ) ไม่ใช่เครื่องหมายเส้น AH ที่ Forebet เขียนจากมุมฝั่งที่มันเชียร์
        #   สกอร์คาดเจ้าบ้าน>เยือน = เจ้าบ้านต่อ · เยือน>เจ้าบ้าน = เยือนต่อ · เท่ากัน = คาดเสมอ (ไม่มีต่อ)
        try:
            ph, pa = map(int, pscore.split("-"))
        except Exception:
            ph = pa = 0
        if ph > pa:
            fav = "Home"
        elif pa > ph:
            fav = "Away"
        else:
            fav = "เสมอ"
        # เส้น AH แสดงจากมุมทีมต่อ (ติดลบ) · คาดเสมอ = เส้น 0
        try:
            mag = abs(float(line))
        except Exception:
            mag = 0.0
        fline = "0" if fav == "เสมอ" or not mag else f"-{mag:g}"
        # วันบอลนับ 10:00 → 09:59 เช้าวันถัดไป = วันเดียว → คู่ดึกข้ามเที่ยงคืน (ตี1-9) อยู่ท้ายลิสต์
        try:
            h, mm = map(int, tm.split(":"))
            order = (h * 60 + mm - 600) % 1440   # 10:00=0 ... 09:59=1439
        except Exception:
            order = 9999
        rows.append((order, f"{tm} | {names} | ฝั่งแนะนำ={fav} เส้น={fline} | สกอร์คาด {pscore} | เชื่อมั่น {prob}% | id={mid}"))
    if not rows:
        return ""
    rows.sort(key=lambda r: r[0])
    return ("===ตารางราคาแฮนดิแคปจริงจาก Forebet (แหล่งเดียวของเส้น HDP+เวลา+สกอร์คาด · ใช้ตรงนี้เท่านั้น)===\n"
            "(เวลาไทยแล้ว · วันบอล 10:00→09:59 · ฝั่งแนะนำ=ทีมที่ Forebet ให้เล่น · เส้นติดลบ=ฝั่งนั้นต่อ · เส้นเป็นบวก=ฝั่งนั้นรองรับแต้ม · เส้น 0=ลูกเปล่า · สกอร์คาด=เจ้าบ้าน-เยือน)\n"
            + "\n".join(r[1] for r in rows))

# ==========================================
# 4.7 ตารางเวลาแข่งกลาง — ดึงจาก "ทุกลิงก์" (เวลาไทย) ทุกทีเด็ดจะมีเวลาเสมอ
#     ไม่ว่า Gemini เลือกคู่จากตลาดไหน (แก้ปัญหาเวลาหายในคู่ที่ไม่อยู่ในตาราง AH)
# ==========================================
_LINK_RE = re.compile(_LINK_PAT)
_LINK_RE2 = re.compile(_LINK_PAT2)

def collect_times(raw, tmap):
    """เก็บ MatchID (เลขท้ายลิงก์) → (ชื่อคู่, เวลาไทย)
    ⭐ คีย์เป็น 'เลข id' ไม่ใช่ slug — slug เป็นแค่ของประดับ (ลองยิง slug ผิดกับ id ถูก เว็บก็คืนคู่ตาม id)
       id เดียวกันทุกวัน ทุกภาษา → ใช้เป็นคีย์ถาวรของชีตได้ ไม่หลุดเวลาชื่อทีมสะกดต่าง (อังกฤษ/ไทย)"""
    if not raw:
        return
    for m in _LINK_RE.finditer(raw):
        names, d, t, ampm, slug, mid = m.groups()
        if mid not in tmap:
            tmap[mid] = (names.strip(), _to_thai_time(d, t, ampm))
    for m in _LINK_RE2.finditer(raw):      # แบบวันเวลาอยู่นอกวงเล็บ
        names, slug, mid, d, t, ampm = m.groups()
        if mid not in tmap:
            tmap[mid] = (names.replace(" - ", " ").strip(), _to_thai_time(d, t, ampm))

def fmt_time_table(tmap):
    if not tmap:
        return ""
    rows = []
    for mid, (names, t) in tmap.items():
        try:
            h, mm = map(int, t.split(":"))
            order = (h * 60 + mm - 600) % 1440
        except Exception:
            order = 9999
        rows.append((order, f"{t} | {names} | id={mid}"))
    rows.sort(key=lambda r: r[0])
    return ("===ตารางเวลาแข่งทุกคู่ (เวลาไทยแล้ว · วันบอล 10:00→09:59 · ทุกทีเด็ดต้องมีเวลาจากตารางนี้)===\n"
            "(id = รหัสคู่ถาวรของ Forebet · ต้องคัดลอกใส่ช่อง \"id\" ใน ===DATA=== ให้ตรงเป๊ะ ห้ามแต่งเลขเอง)\n"
            + "\n".join(r[1] for r in rows))

# ==========================================
# 4.75 ธงชาติตามลีก + สกอร์สด/นาที  → เติมเข้าข้อความ "ด้วย Python หลัง Gemini ตอบ"
#      ทำเอง ไม่ให้ AI ทำ เพราะ (ก) ไม่ต้องเปลืองโทเคนสอน (ข) AI มั่ว/ลืมได้ แต่โค้ดไม่ลืม
#      ธงเอาจากรูปธงของ Forebet เอง (images/fc/mx.png → mx → 🇲🇽) แม่นกว่าเดาจากชื่อลีก
# ==========================================
#      ตัวย่อใต้ธงบอกด้วยว่าเป็น "ชาติ" หรือ "รายการรวมชาติ":
#        Kr3 / AuA / CzC  = ตัวแรกใหญ่ตัวสองเล็ก → ธงชาติ
#        CLW / EL / WCQ    = ใหญ่ติดกัน ≥2 ตัว    → ถ้วย 🏆 (ไม่ใช่ของชาติใดชาติเดียว)
_FLAG_OR_LINK = re.compile(
    r'images/fc/(?P<cc>[a-z0-9_\-]+)\.png'
    r'|^\s*(?P<ab>[A-Z]{2,4}\d?)\s*$'
    r'|football/matches/' + _SLUG + r'+?-(?P<mid>\d+)\)', re.M)
# ธงย่อยของอังกฤษ (ไม่มีใน regional-indicator ปกติ) + ธงรวมทวีป/ฟุตบอลโลก (Forebet ใช้เลข)
_FLAG_SPECIAL = {
    "gb-en": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "gb-eng": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "gb-sct": "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "gb-wls": "🏴󠁧󠁢󠁷󠁬󠁳󠁿", "gb-nir": "🇬🇧", "eu": "🇪🇺", "world": "🌍",
}

def cc_to_flag(cc):
    """รหัสประเทศ 2 ตัว → อิโมจิธง · เลข/ไม่รู้จัก = ถ้วยรวมทวีป"""
    if not cc:
        return ""
    cc = cc.lower()
    if cc in _FLAG_SPECIAL:
        return _FLAG_SPECIAL[cc]
    if len(cc) == 2 and cc.isalpha():
        return chr(0x1F1E6 + ord(cc[0]) - 97) + chr(0x1F1E6 + ord(cc[1]) - 97)
    return "🏆"      # เลข (13.png) = รายการรวมหลายชาติ เช่น UEFA/CONMEBOL

#    ตัวสำรอง: ถ้าอ่านรูปธงไม่เจอ → เทียบจาก "ชื่อลีก/ชื่อประเทศ" ที่ Forebet พิมพ์ไว้
#    (อิโมจิธงสร้างจากรหัส 2 ตัวได้ทุกประเทศ ไม่ต้องมีตารางอิโมจิ — ต้องรู้แค่รหัสประเทศ)
_LEAGUE_CC = {
    "england": "gb-en", "premier league": "gb-en", "efl": "gb-en", "scotland": "gb-sct",
    "wales": "gb-wls", "northern ireland": "gb-nir", "ireland": "ie", "spain": "es",
    "laliga": "es", "la liga": "es", "italy": "it", "serie a": "it", "germany": "de",
    "bundesliga": "de", "france": "fr", "ligue 1": "fr", "netherlands": "nl",
    "eredivisie": "nl", "portugal": "pt", "belgium": "be", "turkey": "tr", "greece": "gr",
    "switzerland": "ch", "austria": "at", "denmark": "dk", "norway": "no", "sweden": "se",
    "finland": "fi", "poland": "pl", "czech": "cz", "romania": "ro", "bulgaria": "bg",
    "croatia": "hr", "serbia": "rs", "slovakia": "sk", "slovenia": "si", "hungary": "hu",
    "ukraine": "ua", "russia": "ru", "estonia": "ee", "latvia": "lv", "lithuania": "lt",
    "iceland": "is", "israel": "il", "cyprus": "cy", "albania": "al", "armenia": "am",
    "azerbaijan": "az", "georgia": "ge", "kazakhstan": "kz", "belarus": "by",
    "brazil": "br", "argentina": "ar", "chile": "cl", "mexico": "mx", "colombia": "co",
    "peru": "pe", "uruguay": "uy", "paraguay": "py", "bolivia": "bo", "ecuador": "ec",
    "venezuela": "ve", "usa": "us", "united states": "us", "mls": "us", "canada": "ca",
    "costa rica": "cr", "guatemala": "gt", "honduras": "hn", "panama": "pa",
    "el salvador": "sv", "jamaica": "jm", "japan": "jp", "korea": "kr", "china": "cn",
    "australia": "au", "new zealand": "nz", "india": "in", "indonesia": "id",
    "thailand": "th", "vietnam": "vn", "malaysia": "my", "singapore": "sg",
    "philippines": "ph", "hong kong": "hk", "uzbekistan": "uz", "iran": "ir", "iraq": "iq",
    "saudi": "sa", "qatar": "qa", "uae": "ae", "emirates": "ae", "kuwait": "kw",
    "bahrain": "bh", "oman": "om", "jordan": "jo", "lebanon": "lb", "syria": "sy",
    "egypt": "eg", "morocco": "ma", "algeria": "dz", "tunisia": "tn", "nigeria": "ng",
    "ghana": "gh", "south africa": "za", "kenya": "ke", "tanzania": "tz", "zambia": "zm",
    "zimbabwe": "zw", "cameroon": "cm", "senegal": "sn", "ivory coast": "ci",
    "somalia": "so", "uefa": "eu", "conmebol": "13", "concacaf": "13", "afc": "13",
    "caf": "13", "world": "world", "friendly": "world", "international": "world",
}

def league_to_cc(name):
    """ชื่อลีก → รหัสประเทศ (จับคำยาวก่อน กัน 'ireland' ชน 'northern ireland')"""
    low = (name or "").lower()
    for key in sorted(_LEAGUE_CC, key=len, reverse=True):
        if key in low:
            return _LEAGUE_CC[key]
    return None

def collect_flags(raw, fmap):
    """เก็บ id คู่ → ธง โดยยึด 'รูปธงที่โผล่ก่อนลิงก์คู่นั้น' (Forebet วางธงหัวลีกไว้เหนือคู่)"""
    if not raw:
        return
    cc = None
    just_flag = False       # ตัวย่อเชื่อได้เฉพาะตัวที่ตามหลังรูปธงติดกัน (กัน HT/COEF หลอก)
    for m in _FLAG_OR_LINK.finditer(raw):
        if m.group("cc"):
            cc, just_flag = m.group("cc"), True
            continue
        if m.group("ab"):
            if just_flag:
                cc = "13"   # ตัวย่อใหญ่ทั้งคำ (CLW/EL/WCQ) = รายการรวมชาติ → ถ้วย
            just_flag = False
            continue
        just_flag = False
        if cc:
            fmap.setdefault(m.group("mid"), cc)

# ==========================================
# 4.75 เก็บ "เรทน้ำ" (คอลัมน์ Coef. ของ Forebet) → id → เรทแบบทศนิยม (1.65, 2.68)
#      วัดจากหน้า asian-handicap จริง (scratchpad/p_ah.txt) แถวหนึ่งเรียงแบบนี้ นับจากบรรทัดลิงก์:
#        +0 ลิงก์ · +2 '66%' · +4 'Away +0.25 1-1' · +6 สกอร์คาด '1 - 1'
#        +8 'Avg. goals' (2.40) ← ❌ ไม่ใช่ราคา! เคยเข้าใจผิด · ยืนยัน: สกอร์คาด 1-1→~2.0, 5-0→5.76
#        +10 อากาศ · +12 Coef. '+168'/'-154'/' - ' ← ✅ ราคาจริง · +14 สถานะ '90'/'HT' · +16 สกอร์จริง
#      Forebet เสิร์ฟราคาเป็น "American odds" (มีตัวติดหัวเสมอ) → แปลงเป็นทศนิยมเอง
#        +A → 1+A/100  ·  -A → 1+100/A     (ตั้งค่า COEF บนเว็บเปลี่ยนรูปแบบได้ → รับทศนิยมตรงๆ ด้วย)
#      ยืนยันว่าเป็นราคาของ pick AH จริง: แปลงทั้งหน้าแล้วความน่าจะเป็นแฝงเฉลี่ย 60.0%
#        เทียบ % ของ Forebet เฉลี่ย 57.7% → ต่างกัน ~2% = ค่าน้ำเจ้ามือของตลาด AH เป๊ะ
#      ⚠️ มีราคาแค่ ~23/44 คู่ (ที่เหลือ Forebet ขึ้น ' - ') → คู่ไม่มีราคาจะเว้นว่าง ไม่นับตอนคิดกำไร
#      ⚠️ ยังไม่ให้ Gemini เห็นราคา — เก็บไว้วัด "กำไรจริง" ของวิธีคัดแบบเดิมก่อน
#         (ถ้าใส่ให้ AI เห็นวันนี้ = พฤติกรรมเปลี่ยนทันที แล้วเทียบก่อน/หลังไม่ได้)
_AM_ODDS_RE = re.compile(r'^([+-])(\d{2,4})$')
_DEC_ODDS_RE = re.compile(r'^(\d{1,2}\.\d{1,2})$')

def collect_odds(raw, omap):
    if not raw:
        return
    lines = [l.strip() for l in raw.splitlines()]
    link_re = re.compile('^' + _LINK_PAT + '$')
    for i, l in enumerate(lines):
        m = link_re.match(l)
        if not m:
            continue
        mid = m.group(6)
        if mid in omap:
            continue
        for j in range(i + 11, min(i + 15, len(lines))):   # ล็อกช่วงแคบรอบคอลัมน์ Coef. (กันไปโดน Avg.goals ที่ +8)
            am = _AM_ODDS_RE.match(lines[j])
            if am:
                a = int(am.group(2))
                omap[mid] = f"{(1 + a / 100) if am.group(1) == '+' else (1 + 100 / a):.2f}"
                break
            de = _DEC_ODDS_RE.match(lines[j])
            if de and float(de.group(1)) >= 1.01:
                omap[mid] = de.group(1)
                break

# ==========================================
# 4.8 เก็บ "ตัวเลขดิบทุกตลาดต่อคู่" → id → {x2, ah, ou, btts} → ส่งไปเก็บในชีต
#     ไว้ให้ฝั่ง GAS ตรวจ "ธงขัดแย้งข้ามตลาด" เอง (กฎอยู่ที่ GAS → แก้กฎได้โดยไม่ต้อง push scraper ใหม่)
#     วัดจากหน้าจริงที่ dump มา (นับ offset จากบรรทัดลิงก์คู่):
#       under-over-goals : +2 '69 31' = Under% Over% (เส้นมาตรฐาน 2.5) · +8 '1.53' = avg goals
#       both-to-score    : +2 '51 49' = No% Yes%   ← ยืนยันจากแถวที่ pick 'No 3-0' กับ 'Yes 1-2'
#       asian-handicap   : +2 '34%' = ความมั่นใจ · +4 'Home -1.5 3-0' = ฝั่ง|เส้น|สกอร์คาด
#       top-football-tips: +2 '71 20 10' = 1x2 บ้าน/เสมอ/เยือน ← หน้าเดียวที่ 1x2 ผูกกับลิงก์คู่ได้
#     ℹ️ ส่วนนี้ใช้เฉพาะ "สายสำรอง Jina" แล้ว — สายหลักดึงจาก JSON API (forebet_api.py) ซึ่งได้ 1x2 ครบทุกคู่
#     (ของเดิม: หน้า predictions-1x2 ผ่าน Jina ไม่มีลิงก์/ชื่อทีม จับคู่ id ไม่ได้ → 1x2 เลยขาด แก้ด้วย API แล้ว)
#        1x2 จึงมีแค่คู่ที่ติดหน้า top (~22 คู่) — คู่อื่นเว้นว่าง ไม่ใช่บั๊ก
#     ⚠️ ยังไม่ให้ Gemini เห็นตัวเลขชุดนี้ (เหมือนตอนเพิ่มเรท) — เก็บวัดก่อนว่าธงขัดแย้งทำนายพลาดจริงไหม
_NUM2_RE = re.compile(r'^(\d{1,3})\s+(\d{1,3})$')
_NUM3_RE = re.compile(r'^(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})$')
_CONF_RE = re.compile(r'^(\d{1,3})%$')
_AVG_RE = re.compile(r'^(\d{1,2}\.\d{1,2})$')
_AHPICK_RE = re.compile(r'^(Home|Away)\s+([+-]?\d+(?:\.\d+)?)\b')

def collect_markets(raw, url, mkt):
    if not raw:
        return
    if "under-over" in url:
        kind = "ou"
    elif "both-to-score" in url:
        kind = "btts"
    elif "asian-handicap" in url:
        kind = "ah"
    elif "top-football-tips" in url:
        kind = "x2"
    else:
        return
    lines = [l.strip() for l in raw.splitlines()]
    link_re = re.compile('^' + _LINK_PAT + '$')
    for i, l in enumerate(lines):
        m = link_re.match(l)
        if not m:
            continue
        d = mkt.setdefault(m.group(6), {})
        if kind in d:      # คู่ซ้ำในหน้าเดียวกัน (Forebet ลิสต์ทั้งวันนี้/พรุ่งนี้) → เอาแถวแรกพอ
            continue
        c2 = lines[i + 2] if i + 2 < len(lines) else ""
        c4 = lines[i + 4] if i + 4 < len(lines) else ""
        c8 = lines[i + 8] if i + 8 < len(lines) else ""
        if kind == "ou":
            n = _NUM2_RE.match(c2)
            if n:
                a = _AVG_RE.match(c8)
                d["ou"] = n.group(1) + "/" + n.group(2) + ("|" + a.group(1) if a else "")
        elif kind == "btts":
            n = _NUM2_RE.match(c2)
            if n:
                d["btts"] = n.group(1) + "/" + n.group(2)
        elif kind == "x2":
            n = _NUM3_RE.match(c2)
            if n:
                d["x2"] = n.group(1) + "/" + n.group(2) + "/" + n.group(3)
        elif kind == "ah":
            a = _AHPICK_RE.match(c4)
            if a:
                p = _CONF_RE.match(c2)
                d["ah"] = a.group(1) + "|" + a.group(2) + ("|" + p.group(1) if p else "")

# ---------- หน้าบอลสด: id → (นาที, สกอร์, สกอร์ครึ่งแรก) ----------
LIVE_URL = "https://www.forebet.com/en/live-football-tips"
_LIVE_SCORE = re.compile(r'^\*\*(\d+)\s*[-–]\s*(\d+)\*\*\s*(?:\((\d+)\s*[-–]\s*(\d+)\))?')
_LIVE_MIN = re.compile(r'^(\d{1,3})$|^(HT|FT|AET|Pen\.?)$', re.I)

def parse_live_table(raw):
    """คืน {id: 'LIVE:<นาที>"  h - a (hth - hta)'} จากหน้า live ของ Forebet
       รูปแบบจริง: [ชื่อคู่ วันเวลา](.../matches/slug-id) ... <นาที> ... **h - a**(hth - hta)"""
    out = {}
    if not raw:
        return out
    lines = [l.strip() for l in raw.splitlines()]
    mid_re = re.compile(r'football/matches/' + _SLUG + r'+?-(\d+)\)')
    for i, l in enumerate(lines):
        mm = mid_re.search(l)
        if not mm:
            continue
        mid = mm.group(1)
        for j in range(i + 1, min(i + 28, len(lines))):
            sm = _LIVE_SCORE.match(lines[j])
            if not sm:
                continue
            minute = ""
            for k in range(j - 1, max(i, j - 5), -1):     # นาทีคือบรรทัดมีเนื้อความก่อนสกอร์
                if not lines[k]:
                    continue
                km = _LIVE_MIN.match(lines[k])
                if km:
                    minute = (km.group(1) or km.group(2)).upper()
                break
            if minute.isdigit() and int(minute) > 90:      # 93 → 90+3 (ตามที่พี่ให้มา)
                minute = f"90+{int(minute) - 90}"
            h, a, hh, ha = sm.groups()
            # ครึ่งแรกยังไม่จบ = ไม่มีวงเล็บ · พักครึ่ง = สกอร์เท่ากับครึ่งแรก ไม่ต้องซ้ำ
            ht = f" ({hh} - {ha})" if hh is not None and (hh, ha) != (h, a) else ""
            tag = f'{minute}"' if minute.isdigit() or "+" in minute else minute   # HT/FT ไม่ต้องมี "
            out[mid] = f'LIVE:{tag}  {h} - {a}{ht}' if minute else f'LIVE:  {h} - {a}{ht}'
            break
    return out

_HEAD_RE = re.compile(r'^(?:[🔥🥇🥈🥉]\s*)?\d+\.\s*\d{1,2}:\d{2}\s')

def _nm(s):
    return re.sub(r'[^a-z0-9]', '', (s or "").lower())

# ==========================================
# 4.78 เติมฟิลด์ "id" ให้ทีเด็ดเอง (ไม่พึ่ง Gemini)
#      🩸 วัดของจริง 24 ก.ค. 69: ชีต 67/67 แถว id ว่างเปล่า → พังต่อกันเป็นโดมิโน
#         id ว่าง → คีย์ชีตถอยไปใช้ชื่อทีม (เกรดผลติดแค่ 13/67 · ค้าง 241 คู่)
#                 → แปะเรทไม่ได้เลย (odds_map คีย์เป็น id) → ROI ไม่มีวันมีข้อมูล
#                 → ธง/LIVE ก็ fallback (fmap/live_map คีย์เป็น id เหมือนกัน)
#         prompt สั่งย้ำ 2 บรรทัดว่า "คัดลอก id มาให้ตรง" แล้ว มันก็ยังไม่ทำ
#      → เลิกขอ: จับ id เองจากตารางเวลา (ชื่อคู่ที่ Gemini อ่านมาก็มาจากลิงก์ชุดเดียวกัน)
#        หลักเดียวกับเรท/ธง — "ทำเอง ไม่ให้ AI ทำ" เพราะโค้ดไม่ลืมและไม่มั่ว
#      ⚠️ กำกวม (เข้าหลายคู่) = ปล่อยว่าง ดีกว่าเดาผิดแล้วไปเกรดผลของคู่อื่น
# ==========================================
def patch_ids(tips_raw, time_map):
    """คืน tips_raw ที่เติม id แล้ว (สตริง JSON เดิม ไม่แตะรูปแบบ ให้ฟังก์ชันปลายทางอ่านต่อได้เหมือนเดิม)"""
    if not tips_raw or not time_map:
        return tips_raw
    m = re.search(r"\[.*\]", tips_raw.strip().strip("`"), re.S)
    if not m:
        return tips_raw
    try:
        tips = json.loads(m.group(0))
    except Exception:
        return tips_raw            # JSON เพี้ยน → ปล่อยตามเดิม ให้ปลายทางไปบ่นเอง

    idx = [(_nm(nm), mid) for mid, (nm, _t) in time_map.items()]
    had = fixed = ambig = miss = 0
    for t in tips:
        if str(t.get("id") or "").strip() in time_map:
            had += 1                       # id ที่ AI ให้มา "มีจริงในตาราง" → เชื่อได้
            continue
        h, a = _nm(t.get("home")), _nm(t.get("away"))
        if len(h) < 3 or len(a) < 3:       # ชื่อไทย/สั้นเกิน → _nm เหลือแทบว่าง เทียบไม่ได้
            t["id"] = ""
            miss += 1
            continue
        hits = [mid for nn, mid in idx if h in nn and a in nn]
        if len(hits) > 1:                  # ชื่อซ้ำ (ทีมสำรอง/ลีกเยาวชน) → คัดด้วยลำดับ บ้านต้องมาก่อนเยือน
            om = {mid: nn for nn, mid in idx}
            hits = [mid for mid in hits if om[mid].index(h) < om[mid].index(a)]
        if len(hits) == 1:
            t["id"] = hits[0]
            fixed += 1
        else:
            t["id"] = ""                   # 0 = จับไม่ได้ · >1 = ยังกำกวม → ล้างของ AI ทิ้ง (มันมั่วเลขมา)
            ambig += 1 if hits else 0
            miss += 0 if hits else 1
    print(f"🆔 id ทีเด็ด: AI ให้มาถูก {had} · จับให้เอง {fixed} · กำกวม {ambig} · ไม่เจอ {miss} (จาก {len(tips)} คู่)")
    return json.dumps(tips, ensure_ascii=False)

def decorate_tips(text, tips_raw, fmap, live_map):
    """เติม (ก) ธงชาติหลังเวลา (ข) บรรทัด LIVE ใต้หัวคู่ — จับคู่ด้วยชื่อทีมจาก ===DATA==="""
    if not text or not tips_raw:
        return text
    try:
        m = re.search(r'\[.*\]', tips_raw, re.S)
        tips = json.loads(m.group(0)) if m else []
    except Exception:
        return text
    meta = []
    for t in tips:
        mid = str(t.get("id") or "")
        cc = fmap.get(mid) or league_to_cc(t.get("league"))   # ธงจากรูป → ไม่มีก็เทียบชื่อลีก
        meta.append((_nm(t.get("home")), _nm(t.get("away")),
                     cc_to_flag(cc), live_map.get(mid, "")))
    out = []
    for line in text.splitlines():
        out.append(line)
        if not _HEAD_RE.match(line):
            continue
        key = _nm(line)
        for home, away, flag, live in meta:
            if not home or home not in key or (away and away not in key):
                continue
            if flag and flag not in line:      # ธง: แทรกหลัง HH:MM
                out[-1] = re.sub(r'^((?:[🔥🥇🥈🥉]\s*)?\d+\.\s*\d{1,2}:\d{2})\s+', r'\1 ' + flag + ' ', out[-1])
            if live:
                out.append(live)
            break
    return "\n".join(out)

# ==========================================
# 4.85 ⛔️ ตัดทีเด็ดชนิดที่ผู้ใช้สั่งห้ามถาวร: 'เสมอ' + ฝั่งต่ำ (ต่ำแรก/ต่ำเต็ม/Under)
#      วัดจากของจริง 30 วัน: เสมอ 5.6% (1/18) · ต่ำ 42.3% (11/26) — ขาดทุนทั้งคู่
#      prompt สั่งห้ามแล้วชั้นหนึ่ง แต่ AI ดื้อได้ → กรองซ้ำตรงนี้ก่อนส่ง/ก่อนลงชีต
# ==========================================
_SEP_LINE = "---------------------------"
_BAN_LOW_RE = re.compile(r'ต่ำ|under', re.I)
# 'เสมอ' ที่เป็นคำแนะนำจริงเท่านั้น — เว้น "เสมอราคา" / "ปิดเสมอ" / "ยันเสมอ" ที่เป็นคำพูดปกติ
_BAN_DRAW_RE = re.compile(r'(?<!ปิด)(?<!ยัน)(?<!ราคา)เสมอ(?!ราคา)')

def _is_banned_pick(s):
    s = str(s or "")
    return bool(_BAN_LOW_RE.search(s) or _BAN_DRAW_RE.search(s))

def drop_banned_tips(text, tips_raw):
    """คืน (text, tips_raw, จำนวนคู่ที่เหลือ) หลังตัดทีเด็ด เสมอ/ต่ำ ออกทั้งการ์ดและ JSON"""
    banned_pairs, cut_json = set(), 0
    if tips_raw:
        m = re.search(r'\[.*\]', tips_raw, re.S)
        if m:
            try:
                tips = json.loads(m.group(0))
            except Exception:
                tips = None
            if tips is not None:
                keep = []
                for t in tips:
                    if _is_banned_pick(t.get("pick")):
                        banned_pairs.add((_nm(t.get("home")), _nm(t.get("away"))))
                    else:
                        keep.append(t)
                cut_json = len(tips) - len(keep)
                if cut_json:
                    tips_raw = json.dumps(keep, ensure_ascii=False)

    if not text:
        return text, tips_raw, 0

    lines = text.splitlines()
    idx = [i for i, l in enumerate(lines) if _HEAD_RE.match(l.strip())]
    if not idx:
        return text, tips_raw, -1          # -1 = จับการ์ดไม่ได้ (รูปแบบเพี้ยน) → ปล่อยผ่าน อย่าบล็อกการส่ง

    def _trim(block):                       # ตัดเส้นคั่น/บรรทัดว่างท้ายบล็อกทิ้ง
        while block and (not block[-1].strip() or block[-1].strip() == _SEP_LINE):
            block.pop()
        return block

    pre = _trim(lines[:idx[0]])
    cards = []
    for n, s in enumerate(idx):
        e = idx[n + 1] if n + 1 < len(idx) else len(lines)
        cards.append(_trim(lines[s:e]))

    kept = []
    for c in cards:
        pick = next((l for l in c if l.strip().startswith("🎯")), "")
        bad = _is_banned_pick(pick)
        if not bad and banned_pairs:        # กันเคส 🎯 กับ JSON ไม่ตรงกัน → เทียบชื่อทีมซ้ำ
            key = _nm(c[0])
            bad = any(h and h in key and (not a or a in key) for h, a in banned_pairs)
        if not bad:
            kept.append(c)

    dropped = len(cards) - len(kept)
    if not dropped and not cut_json:
        return text, tips_raw, len(cards)
    print(f"⛔️ ตัดทีเด็ดต้องห้าม (เสมอ/ต่ำ): การ์ด {dropped} คู่ · JSON {cut_json} คู่ → เหลือ {len(kept)} คู่")

    medals = ["🥇", "🥈", "🥉"]
    fixed = []
    for i, c in enumerate(kept):            # เรียงเลขใหม่ + แจกเหรียญใหม่ ไม่ให้เลขขาดช่วง
        head = re.sub(r'^(?:[🔥🥇🥈🥉]\s*)*', '', c[0].strip())
        head = re.sub(r'^\d+\.\s*', '', head)
        fixed.append("\n".join([(medals[i] + " " if i < 3 else "") + f"{i+1}. {head}"] + c[1:]))

    body = ("\n" + _SEP_LINE + "\n").join(fixed)
    out = ("\n".join(pre) + "\n\n" + body) if pre else body      # หัวข้อ → เว้นบรรทัด → การ์ด (เส้นคั่นใช้ระหว่างคู่เท่านั้น)
    return out.strip(), tips_raw, len(kept)

# ==========================================
# 4.9 รวบรวม Gemini keys หลายตัว (สลับเมื่อ key เต็มโควตา 20/วัน → คูณโควตา)
#     ใส่ได้ 2 แบบ: (ก) GEMINI_API_KEY = "key1,key2,key3" คั่นคอมมา  (ข) GEMINI_API_KEY2, GEMINI_API_KEY3...
# ==========================================
def gemini_keys():
    keys, seen = [], set()
    for k in os.environ.get("GEMINI_API_KEY", "").split(","):
        k = k.strip()
        if k and k not in seen:
            keys.append(k); seen.add(k)
    for i in range(2, 8):
        k = os.environ.get(f"GEMINI_API_KEY{i}", "").strip()
        if k and k not in seen:
            keys.append(k); seen.add(k)
    return keys

# ==========================================
# 4.92 ยิง Gemini แบบ "หลายเส้นทาง" — รองรับคีย์ยุคใหม่ AQ.* ของ Google
#      Google ย้ายจาก Standard key (AIza...) → Auth key (AQ....) และ AI Studio ออกคีย์ AQ ให้อัตโนมัติแล้ว
#      ปัญหาคือ SDK/ไลบรารีรุ่นเก่าบางตัว "ตรวจว่าต้องขึ้นต้น AIza" → เด้งทั้งที่คีย์ถูก
#      แก้: ลอง 3 เส้นทางไล่ลงมา ถ้าเส้นทางแรกไม่ผ่านเพราะ 'ปัญหาการยืนยันตัวตน' ค่อยลองเส้นถัดไป
#        1) SDK google-genai (เร็วสุด · ใช้ได้ทั้ง AIza และ AQ ถ้า SDK ใหม่พอ)
#        2) REST + header x-goog-api-key   ← เส้นทางที่ทางการแนะนำสำหรับคีย์ AQ
#        3) REST + ?key=... ท้าย URL       ← เส้นทางเก่า เผื่อบัญชียังใช้แบบเดิม
#      429/โควตาเต็ม = ไม่ใช่ปัญหาคีย์ → โยน QuotaFull ออกไปให้ตัวสลับคีย์จัดการเหมือนเดิม
# ==========================================
GEMINI_REST = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

class QuotaFull(Exception):
    """โควตาวันนี้ของคีย์นี้เต็ม (429) — ให้สลับคีย์ ไม่ใช่สลับเส้นทาง"""
    pass

def _is_quota_err(msg):
    m = (msg or "").upper()
    return "429" in m or "RESOURCE_EXHAUSTED" in m or "QUOTA" in m

def _is_auth_err(msg):
    m = (msg or "").upper()
    return any(s in m for s in (
        "ACCESS_TOKEN_TYPE_UNSUPPORTED", "API_KEY_INVALID", "INVALID_API_KEY",
        "UNAUTHENTICATED", "PERMISSION_DENIED", "EXPECTED OAUTH",
        "401", "403", "MUST START WITH", "AIZA",
    ))

def _rest_generate(key, model, prompt, temperature, use_header):
    """ยิงตรงผ่าน REST · use_header=True → x-goog-api-key (แนะนำสำหรับ AQ) · False → ?key="""
    url = GEMINI_REST.format(model=model)
    headers = {"Content-Type": "application/json"}
    params = {}
    if use_header:
        headers["x-goog-api-key"] = key
    else:
        params["key"] = key
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    r = requests.post(url, headers=headers, params=params, json=body, timeout=180)
    if r.status_code == 429:
        raise QuotaFull(r.text[:300])
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError(f"ตอบกลับว่างเปล่า: {json.dumps(data)[:300]}")
    return text

def gemini_generate(key, model, prompt, temperature=0.15):
    """คืน (ข้อความ, ชื่อเส้นทางที่ใช้ได้) · โยน QuotaFull ถ้าโควตาเต็ม"""
    errors = []
    routes = []
    if genai is not None:
        routes.append("sdk")
    routes += ["rest-header", "rest-query"]
    for route in routes:
        try:
            if route == "sdk":
                client = genai.Client(api_key=key)
                kwargs = {}
                try:
                    from google.genai import types as _gt
                    kwargs["config"] = _gt.GenerateContentConfig(temperature=temperature)
                except Exception:
                    pass
                resp = client.models.generate_content(model=model, contents=prompt, **kwargs)
                text = (getattr(resp, "text", "") or "").strip()
                if not text:
                    raise RuntimeError("SDK ตอบกลับว่างเปล่า")
                return text, route
            return _rest_generate(key, model, prompt, temperature, route == "rest-header"), route
        except QuotaFull:
            raise
        except Exception as e:
            msg = str(e)
            if _is_quota_err(msg):
                raise QuotaFull(msg)
            errors.append(f"{route}: {msg[:160]}")
            # ไม่ใช่ปัญหายืนยันตัวตน (เช่นเน็ตหลุด/โมเดลไม่มี) → ลองเส้นอื่นก็ไม่ช่วย
            if not _is_auth_err(msg):
                break
    hint = ""
    if str(key).startswith("AQ."):
        hint = ("\n   💡 คีย์นี้เป็นแบบใหม่ (AQ.) — ถ้าเด้งทุกเส้นทางแปลว่าตัวคีย์เองยังไม่เปิดสิทธิ์ "
                "ให้ไปเปิด Generative Language API ในโปรเจกต์ Google Cloud ของคีย์นั้น หรือออกคีย์ใหม่จาก AI Studio")
    raise RuntimeError("ยิง Gemini ไม่ผ่านสักเส้นทาง →\n   " + "\n   ".join(errors) + hint)

# ==========================================
# 4.95 ดึงประวัติทีเด็ด "รอบก่อนๆ วันนี้" จากชีต PIKTAX เพื่อวัด "ความนิ่ง"
#       (คู่ที่บอกคำเดิมซ้ำหลายรอบ = ราคานิ่ง = มั่นใจ · เด้งไปมา = แกว่ง = ระวัง)
# ==========================================
def fetch_history_block():
    if not PIKTAX_STATE_URL:
        return ""
    try:
        base = PIKTAX_STATE_URL.split("?")[0]
        r = requests.get(base + "?fbhist=1", timeout=20)
        arr = json.loads(r.text) if r.status_code == 200 else []
    except Exception as e:
        print(f"⚠️ ดึงประวัตินิ่งไม่ได้ (ข้ามรอบนี้): {e}")
        return ""
    rows = []
    for it in arr:
        h, a = str(it.get("home", "")).strip(), str(it.get("away", "")).strip()
        pk = str(it.get("pick", "")).strip()
        if not h or not a or not pk:
            continue
        st = int(it.get("streak", 1) or 1)
        res = str(it.get("result", "")).strip()
        tag = f" [ผลจริงรอบก่อน: {res}]" if res in ("ถูก", "ผิด") else ""
        try:
            pct_old = int(float(it.get("pct", 0) or 0))
        except Exception:
            pct_old = 0
        ptxt = f" | %เดิม={pct_old}" if pct_old else ""
        mid = str(it.get("mid", "") or "").strip()
        mtxt = f" | id={mid}" if mid else ""
        rows.append(f"{h} vs {a}{mtxt} | pick เดิม='{pk}'{ptxt} | นิ่งมาแล้ว {st} รอบ{tag}")
    if not rows:
        return ""
    print(f"🔁 โหลดประวัตินิ่ง {len(rows)} คู่")
    return ("===ประวัติทีเด็ดรอบก่อนๆ ของวันนี้ (ใช้วัด 'ความนิ่ง' + 'ทิศทางความมั่นใจ' · จับคู่ด้วย id ก่อน ถ้าไม่มีค่อยใช้ชื่อทีม)===\n"
            "(ถ้า pick รอบนี้ของคู่นั้น = 'pick เดิม' → นิ่งเพิ่มเป็น เดิม+1 รอบ · ถ้าต่างจากเดิม = เพิ่งเปลี่ยน รีเซ็ตเป็น 1 · ไม่มีในนี้ = รอบแรกของวัน)\n"
            "(🔺เดลต้า = %รอบนี้ − %เดิม ของคู่เดียวกันที่ pick ไม่เปลี่ยน · ให้คิดเป็นตัวเลขจริง แล้วปรับดาวตามนี้:\n"
            "   เดลต้า ≥ +10 = ยิ่งมั่นใจขึ้นชัด → +0.5 ดาว\n"
            "   +5 ถึง +9    = ขยับขึ้นเล็กน้อย → คงดาว\n"
            "   −4 ถึง +4    = นิ่ง → คงดาว\n"
            "   −5 ถึง −9    = เริ่มแกว่ง → −0.5 ดาว\n"
            "   ≤ −10        = ความมั่นใจร่วงแรง → −1 ดาว และห้ามได้เหรียญ 🥇)\n"
            + "\n".join(rows))

# ==========================================
# 4.96 การ์ด feedback — "เกณฑ์ไหนวัดแล้วจริง เกณฑ์ไหนหลอกเรา"
#       ดึงผลจริงจากชีต (?fbcrit=45) แล้วแปลงเป็นคำสั่งให้ Gemini
#       จุดสำคัญ: ไม่ได้แค่โชว์ตัวเลข แต่ "สั่งตัดเกณฑ์ที่วัดแล้วแย่กว่าค่ากลาง" อัตโนมัติ
#       → พอเก็บผลไปเรื่อยๆ ระบบจะเลิกใช้กฎที่หลอกเราเอง โดยไม่ต้องมานั่งแก้ prompt มือ
# ==========================================
FB_MIN_N = 25      # ต่ำกว่านี้ = ตัวอย่างน้อยเกินจะสรุป (แกว่งจนไร้ความหมาย)
FB_GAP = 8         # ห่างจากค่ากลางกี่จุดถึงเรียกว่า "มีนัย"

def fetch_feedback_block(days=45):
    if not PIKTAX_STATE_URL:
        return ""
    try:
        base = PIKTAX_STATE_URL.split("?")[0]
        r = requests.get(f"{base}?fbcrit={days}", timeout=25)
        d = json.loads(r.text) if r.status_code == 200 else {}
    except Exception as e:
        print(f"⚠️ ดึงสถิติเกณฑ์ไม่ได้ (ข้ามรอบนี้): {e}")
        return ""
    base_pct, n_all = d.get("base"), d.get("n") or 0
    if not base_pct or n_all < 40:
        print(f"ℹ️ ผลจริงยังน้อย ({n_all} คู่) — ยังไม่ใส่การ์ด feedback")
        return ""
    bad, good, plain = [], [], []
    for it in d.get("g") or []:
        n, p = it.get("n") or 0, it.get("p") or 0
        gap = round(p - base_pct, 1)
        roi = it.get("r")
        rt = f" · ROI {roi:+.1f}%" if roi is not None else ""
        line = f"[{it.get('c')}] {it.get('k')}: แม่น {p}% ({gap:+.1f} จุดจากค่ากลาง) จาก {n} คู่{rt}"
        if n < FB_MIN_N:
            continue
        (bad if gap <= -FB_GAP else good if gap >= FB_GAP else plain).append((gap, line, roi))
    if not (bad or good):
        print(f"ℹ️ ยังไม่มีเกณฑ์ไหนต่างจากค่ากลางเกิน {FB_GAP} จุด (n≥{FB_MIN_N})")
        return ""
    bad.sort(key=lambda x: x[0])
    good.sort(key=lambda x: -x[0])
    roi_txt = f" · กำไรจริงรวม ROI {d['roi']:+.1f}%" if d.get("roi") is not None else ""
    L = [f"===ผลจริงที่วัดมาแล้ว {d.get('days')} วัน ({n_all} คู่ที่รู้ผลแล้ว · ค่ากลาง {base_pct}%{roi_txt})===",
         "นี่คือผลจริงของ *ทีเด็ดที่ระบบนี้เคยส่งไป* ไม่ใช่ทฤษฎี — ให้เชื่อตัวเลขชุดนี้เหนือความรู้สึก"]
    if bad:
        L.append("❌ เกณฑ์ที่วัดแล้ว **แย่กว่าค่ากลาง** — รอบนี้ให้ **หลีกเลี่ยง/หักดาว 0.5-1 ดาว** และ **ห้ามให้เหรียญ 🥇**:")
        L += ["   " + b[1] for b in bad[:8]]
    if good:
        L.append("✅ เกณฑ์ที่วัดแล้ว **ดีกว่าค่ากลาง** — รอบนี้ให้ **เน้น/บวกดาวได้ 0.5 ดาว**:")
        L += ["   " + g[1] for g in good[:8]]
    L.append("⚖️ กติกาใช้การ์ดนี้: ตัวเลขนี้ชนะกฎที่เขียนไว้ข้างบนเสมอเมื่อขัดกัน · "
            f"กลุ่มที่ไม่ได้อยู่ในสองรายการนี้ = ยังไม่ต่างจากค่ากลาง ใช้ตามปกติ · "
            f"ทุกบรรทัดผ่านเกณฑ์ ≥{FB_MIN_N} คู่แล้ว ไม่ใช่ตัวอย่างน้อย")
    print(f"🧪 การ์ด feedback: แย่ {len(bad)} · ดี {len(good)} · กลางๆ {len(plain)} (ฐาน {n_all} คู่)")
    return "\n".join(L)

# ==========================================
# 5. วิเคราะห์ + คัดคู่เด่น 1-20 ด้วย Gemini (เงื่อนไข Football Live Analyst)
# ==========================================
def analyze_with_gemini(raw_text, ah_table="", time_table="", history="", flip="", crit=""):
    try:
        prompt = f"""คุณคือ Football Market Analyst — ไม่ใช่แค่ "คนรายงานราคา" แต่เป็น "นักวิเคราะห์ขี้สงสัย" ที่: (1) จดจำราคารอบก่อน (2) เอาทุกตลาดมา "หักล้างกันเอง" หาว่าราคาไหนจริง/ราคาไหนหลอก (3) เตือนล่วงหน้าก่อนบอลเตะ ว่าคู่ไหนล็อกได้ คู่ไหนน่าระวัง — จากข้อมูล Forebet (หลายตลาด: 1x2, สูง/ต่ำ, ครึ่งแรก, HT/FT, ทั้งคู่ยิง, Double Chance, Asian Handicap, TOP Predictions) ตามเงื่อนไขนี้:

⭐ สำคัญสุด: เลือกเฉพาะ "คู่เด่นที่สุด 1-{MAX_MATCHES} คู่" ของวันนั้นเท่านั้น (บางวันมีน้อยกว่า {MAX_MATCHES} ได้ ไม่ต้องฝืนให้ครบ · ต่ำกว่า 3 ดาวไม่ต้องเอา)
🔴 ข้อมูลที่ให้มา = **คู่ที่ยังเล่นได้เท่านั้น** (ระบบตัดคู่ที่จบแล้ว/เตะไปเกิน 2 ชม./เลื่อน/ยกเลิก ออกให้หมดแล้ว ไม่มีสกอร์จบส่งมาให้เลย)
   • ห้ามพูดถึง "ผลจบ/สกอร์จริง/เกมจบ X-Y/ผลออกมาแล้ว" เด็ดขาด — คุณไม่รู้ผล และคู่ที่รู้ผลแล้วไม่ใช่งานของคำสั่งนี้ (ผลย้อนหลังอยู่ใน /สถิติบอล แยกไปแล้ว)
   • คู่ที่มีคำว่า "กำลังเตะอยู่" = เพิ่งเริ่มไม่เกิน 2 ชม. แต่ฟีดยังไม่มีสกอร์ → วิเคราะห์จากราคา/โมเดลเหมือนคู่ปกติ ห้ามเดาสกอร์ว่าตอนนี้เท่าไร
🔴 การเรียงลำดับ: เรียงคู่ที่มั่นใจมากสุด (ดาวเยอะ · หลายตลาดชี้ตรงกัน · คู่ที่ได้เหรียญ 🥇🥈🥉) ไว้บนสุด · ไม่เกิน {MAX_MATCHES} คู่
   • คู่ที่ยังไม่เตะ = ของดี เพราะทันเล่นเต็มเวลา → ถ้าคุณภาพเท่ากัน ให้คู่ที่ยังไม่เตะอยู่บนกว่าคู่ที่กำลังเตะ

1. สถานะเกม: 🚫 **ห้ามเขียนบรรทัดสกอร์เองเด็ดขาด** — ถ้าคู่ไหนกำลังเตะจริง ระบบจะเติมบรรทัด LIVE:นาที  X - Y ให้เองอัตโนมัติใต้ชื่อคู่ (คุณไม่มีสกอร์อยู่ในมือ อย่าเดา)
2. รูปแบบคำแนะนำ (ใช้คำเหล่านี้เท่านั้น):
   • ผลแพ้ชนะ/แฮนดิแคป: 'เยือนไม่แพ้', 'บ้านไม่แพ้', 'หาผู้ชนะ'
   • สูงประตู: 'สูงแรก' (ครึ่งแรกเกินเส้น), 'สูงเต็ม' (เต็มเวลาเกินเส้น) — ระบุเส้น Over/Under จากตลาด สูง/ต่ำ + ครึ่งแรก (เช่น 'สูงเต็ม 2.5')
   ⛔️ **ห้ามฟัน 'เสมอ' และห้ามฟันฝั่งต่ำทุกแบบ ('ต่ำแรก' / 'ต่ำเต็ม' / Under) เด็ดขาด** — สถิติจริงของระบบเอง: เสมอ 5.6% (1/18) · ต่ำ 42.3% (11/26) ขาดทุนทั้งคู่ ผู้ใช้สั่งตัดทิ้งถาวร · คู่ไหนคิดแล้วออกมาเป็นเสมอหรือต่ำ = **ข้ามคู่นั้นไปเลย** ห้ามดันฟันเป็นตัวอื่นแทนแบบมั่วๆ (ระบบมีตัวกรองตัดซ้ำอีกชั้น ฟันมาก็หายอยู่ดี)
   • ทั้งคู่ยิง (BTTS): 'ช่วยกันยิงหรือจบ2+' (สองทีมรวมกันยิงตั้งแต่ 2 ลูกขึ้นไป — 1-1 เข้า 2-0 ก็เข้า) · 'ยิงฝั่งเดียว' (มีทีมเดียวที่ยิงได้ อีกทีมยิงไม่ได้เลย เช่น 1-0 / 2-0 — สกอร์ 0-0 ไม่เข้า) — ไม่ต้องมีเลขเส้น
   🚫 **ห้ามมั่วเส้น HDP เด็ดขาด** — เส้นแฮนดิแคป (เช่น -0.75, +0.25, -1.5) ต้องก๊อปตรงจาก "ตารางราคาแฮนดิแคปจริง" ด้านบนเท่านั้น (จับคู่ด้วยชื่อทีม) · ฝั่งแนะนำ (Home/Away) ก็ยึดตามตาราง · เครื่องหมายของเส้นบอกเองว่าฝั่งนั้นต่อ (ลบ) หรือรอง (บวก) · ถ้าคู่ไหนไม่มีในตาราง = **ใส่แค่คำแนะนำ ไม่ต้องมีเส้น** ห้ามเดา ห้ามเขียน "+0.5" ลอยๆ ห้ามเขียนคำว่า "Asian handicap"/"HDP" แทนตัวเลข
3. เกณฑ์ดาว: 4 ดาว (80-99%), 3.5 ดาว (65-79%), 3 ดาว (50-64%) · เรียง 4 ดาวไว้บนสุด
4. ประเมินข้ามทุกตลาดที่ให้มา (1x2, สูง/ต่ำ, ครึ่งแรก, HT/FT, ทั้งคู่ยิง, Double Chance, AH, Corners, Scorers ฯลฯ) — **คู่ที่หลายตลาดชี้ตรงกัน = มั่นใจสูง เรียงบน** · ตลาดขัดกันเอง/ชี้คนละทาง = ลดดาวหรือข้าม · ยึด 'บอลวันนี้' + 'TOP Predictions' เป็นหลัก
5. กระชับ อ่านบนมือถือง่าย เหมาะส่ง Telegram (ระบบมีปุ่มเปิด/ปิดเสียงให้แล้ว ไม่ต้องเขียนปุ่มเอง)
6. 🗣️ **ภาษาต้องเป็นภาษาคนพูดปกติ ห้ามเป็นภาษา AI** (ข้อนี้ผู้ใช้ย้ำมาเอง — ผิดข้อนี้ถือว่าใช้ไม่ได้):
   • ห้ามใช้คำพวกนี้เด็ดขาด: "เกาะผลเสมอได้ดี", "เกาะทางแน่น", "เกมน่าออดแอดก้ำกึ่ง", "การันตีความมั่นใจ", "สอดคล้องกับ", "ยืนยันที่", "หนุนตามเป้าสด", "โน้มเอียงไปทาง" และคำแต่งสวยทำนองนี้
   • เขียนแบบนี้แทน: "เชื่อว่าอย่างน้อยยันเสมอได้", "หรือแบ่งแต้มกันไป", "สองทีมสูสีกันมาก", "เกมน่าฝืด ยิงกันยาก", "ราคาไม่ขยับมา 2 รอบแล้ว", "ตลาดส่วนใหญ่ชี้ไปทางเดียวกัน"
   • ประโยคสั้น ตรงๆ เหมือนเพื่อนเล่าให้ฟัง ไม่ต้องหรูหรา
7. ✍️ **ตรวจคำผิดก่อนส่งทุกครั้ง** — อ่านทวนทั้งข้อความก่อนตอบ ห้ามมีตัวอักษรซ้ำเกิน (เช่น "ยยืนยัน", "สสูง"), ห้ามพิมพ์ตก, ห้ามมีคำอังกฤษปนมั่วในประโยคไทย

🧠 วิธีคิด (สำคัญมาก — ประเมินทีละคู่จาก "ทุกตลาด" ที่อ่านมา แล้วหักล้างจนเหลือทีเด็ด 1 อย่างที่หลายตลาดหนุนตรงกันมากสุด):
สัญญาณต่อคู่: 1x2 %(เหย้า/เสมอ/เยือน) · AH ฝั่งแนะนำ+เส้น · สูง/ต่ำ + avg goals · BTTS(ทั้งคู่ยิง) · ครึ่งแรก HT · HT/FT · Double Chance · สกอร์คาด
📏 วิธีอ่าน % ให้ถูกทาง (สำคัญ ห้ามอ่านสลับ): (ก) 1x2/AH → ฝั่งที่ % สูงสุด = ฝั่งที่ Forebet คาดว่าชนะ (เหย้า/เสมอ/เยือน) (ข) สูง/ต่ำ (Under/Over รอบเส้น 2.5) → %ฝั่งซ้าย(Under)มาก=โน้มต่ำ · %ฝั่งขวา(Over)มาก=โน้มสูง (ค) ทิศทางผลแพ้ชนะให้ยึด "สกอร์คาด" เป็นตัวตัดสิน (เจ้าบ้าน>เยือน=เจ้าบ้านต่อ · เท่ากัน=สูสีจนหาทางฟันไม่ได้ → ข้ามคู่นั้น)
แมพเป็นคำแนะนำ (ต้องมีตลาดรองรับ ห้ามเดาลอยๆ):
• หาผู้ชนะ = ฝ่ายเดียวเด่นชัด → 1x2 ชนะ ≥55% + AH เป็นต่อ ≥ -0.75 + สกอร์คาดไม่เสมอ (ฟันฝั่งนั้นชนะ)
• บ้านไม่แพ้ / เยือนไม่แพ้ = ฝั่งนั้นเต็ง/สูสีแต่ไม่ขาด → AH เส้นบาง (0 / -0.25 / +0.25) หรือ Double Chance 1X/X2 แรง · เลือกฝั่งที่ (%ชนะ+%เสมอ) รวมสูงกว่า
• สูงเต็ม = ตลาดสูง/ต่ำเชียร์ Over + avg goals >2.7 + BTTS ใช่ + สกอร์คาดรวมเยอะ
• สูงแรก = ดูตลาดครึ่งแรก (HT) — ครึ่งแรกมีลุ้นเกม
⛔️ เจอเกมที่ทุกตลาดชี้ไปทาง **เสมอ** (สกอร์คาดเท่ากัน + %เสมอเด่น + เส้น 0) หรือชี้ไปทาง **ต่ำ** (เชียร์ Under + avg goals <2.3 + สกอร์คาดน้อย) = **ข้ามคู่นั้นทิ้งเลย ไม่ต้องแสดง** ห้ามแปลงเป็น 'บ้านไม่แพ้/เยือนไม่แพ้' หรือตัวอื่นเพื่อให้มีคู่ครบ — คู่น้อยดีกว่าคู่มั่ว
• ช่วยกันยิงหรือจบ2+ = ตลาด "ทั้งคู่ยิง" ฝั่ง Yes ≥55% หรือ Over% หนุน + สกอร์คาดรวม ≥2 (เช่น 1-1, 2-1, 2-0) + avg goals ≥2.4 — เกมมีประตูแน่ ไม่ต้องเดาว่าใครเป็นคนยิง (ทั้งคู่ยิงก็เข้า ฝั่งเดียวซัด 2 ลูกก็เข้า)
• ยิงฝั่งเดียว = BTTS ฝั่ง No ≥55% + สกอร์คาดมีฝั่งยิง 0 แต่ห้ามเป็น 0-0 (ต้อง 1-0 / 2-0 / 3-0) + มีฝั่งต่อขาด (เส้น ≥ -1.5 = เต็งกินรวบ) หรือ avg goals ≤2.3 · ⚠️ ถ้าเกมส่อจืดยิงกันไม่ออกทั้งคู่ (สกอร์คาด 0-0) ห้ามฟันตัวนี้ → **ข้ามคู่นั้นไปเลย** (ห้ามเปลี่ยนไปฟันต่ำ ตัวต่ำถูกตัดออกจากระบบแล้ว)
🔴 **BTTS สดตอนพักครึ่ง/ครึ่งหลัง (กฎผู้ใช้ ใช้ได้เฉพาะคู่ที่เตะไปแล้ว):** ครึ่งแรกจบ **1-0** + เดินมาถึง **นาที 70+** + สกอร์ยังไม่เปลี่ยน + **%เสมอ ของ 1x2 สูง (≥30)** = ฝั่งที่ตามอยู่กำลังบี้ตีเสมอ → **'ช่วยกันยิงหรือจบ2+' ตามได้ (ดันดาวขึ้น — สกอร์ 1-0 มีไปแล้ว 1 ลูก เหลือประตูเดียวจากฝั่งไหนก็ได้ก็จบงาน)** · ⚠️ ตรงข้าม: 1-0 นาที 70+ แต่ %เสมอ ต่ำ (<25) หรือฝั่งนำเป็นต่อขาด = เกมปิดแล้ว → 'ยิงฝั่งเดียว' แทน (สกอร์ 1-0 เข้าเงื่อนไขอยู่แล้ว แค่ให้ฝั่งตามยิงไม่ได้) · สกอร์ 0-0 นาที 70+ = ห้ามฟันทั้งสองตัว (2+ ต้องยิง 2 ลูกใน 20 นาที · ยิงฝั่งเดียวก็ยังไม่มีใครยิงเลย)
⭐ ดาว/ความมั่นใจ = จำนวนตลาดที่ยืนยัน "ตรงทาง" กัน (ยิ่งหลายตลาดชี้ตรงกัน ยิ่งมั่นใจ ดาวยิ่งเยอะ %ยิ่งสูง) · ตลาดขัดกันเอง/ชี้คนละทาง = ลดดาวหรือข้ามคู่นั้น
🥇 **ตัวเลือกหลักของแต่ละคู่ = ผลที่ % สูงสุดเท่าที่หาเจอ ณ ตอนนั้น** จากทุกหน้า (5 ตลาด: 1x2 / สูงต่ำ / ครึ่งแรก / HT-FT / ทั้งคู่ยิง) — หยิบ "ตัวที่โอกาสมากสุดที่มีข้อมูลจริง" มาแสดง 1 อย่างต่อคู่ · ตลาดไหนไม่มีข้อมูลตอนนั้น = ข้ามตลาดนั้น (ห้ามเดา) เอาเท่าที่หาได้ · **บังคับแนบเหตุผลทุกคู่ในบรรทัด 📌 เสมอ** ว่าทำไมเลือกตัวนี้ = ตลาดไหนหนุน + % เท่าไร + (ถ้าเป็นบอลสด) สกอร์สดกำลังตามได้ไหม
🔎 ตรวจความผิดปกติ (market monitor — เจอแล้ว "บอก" ไม่ใช่ซ่อน): ถ้าตลาดในคู่เดียวกันขัดกันเองชัดๆ ให้เติมธง ⚠️ ไว้ต้นบรรทัด 📌 พร้อมบอกสั้นๆ ว่าขัดตรงไหน แล้ว "หั่นดาวลง" (อย่าฟันเต็ม) เคสที่ต้องจับ:
   • 1x2 เชียร์ฝั่งหนึ่งชนะ ≥55% แต่ AH กลับเปิดอีกฝั่งเป็นต่อ / หรือสกอร์คาดสวนทาง 1x2 → ⚠️ ทิศทางไม่ตรง
   • สกอร์คาดโน้มต่ำ (รวม ≤1) แต่ Over% สูง/avg goals >2.7 (หรือกลับกัน สกอร์คาดยิงเยอะแต่เชียร์ Under) → ⚠️ สูงต่ำขัดสกอร์คาด
   • BTTS ว่าทั้งคู่ยิง แต่สกอร์คาดมีฝั่งยิง 0 → ⚠️ BTTS ขัดสกอร์คาด
   • ราคา AH สวน %: %ชนะสูงมาก (≥65) แต่เปิดต่อเส้นบาง/ราคาพอกัน (หรือ %สูสีแต่เปิดต่อครึ่งควบลูก) → ⚠️ ราคาไม่ล้อ%
   📉 **หักดาวเป็นตัวเลขจริง ห้ามหักลอยๆ** (นับธง ⚠️ ที่คู่นั้นติดก่อน แล้วหักตามนี้เป๊ะ):
      • ติด ⚠️ 0 อัน  = ไม่หัก (คู่นี้เท่านั้นที่มีสิทธิ์ได้เหรียญ 🥇)
      • ติด ⚠️ 1 อัน  = **หัก 0.5 ดาว** และ **ห้ามได้เหรียญ 🥇** (ยังแสดงได้ ถ้าหักแล้วยังถึง 3 ดาว)
      • ติด ⚠️ ตั้งแต่ 2 อันขึ้นไป = **ตัดคู่นั้นทิ้ง ห้ามแสดง** (พยานขัดกันเองหลายปาก = ไม่ใช่ทีเด็ด)
      • หักดาวแล้วต่ำกว่า 3 ดาว = ตัดทิ้งเช่นกัน (ดาวขั้นต่ำที่ส่งได้คือ 3)
      • % ที่แสดง ให้ลดตามดาวที่ถูกหักด้วย (หัก 0.5 ดาว ≈ ลด % ลง 7-8 จุด) — ห้ามหักดาวแต่ปล่อย % ค้างสูงเหมือนเดิม
   สรุป: คู่ที่ทุกตลาดไปทางเดียว = ดาวเต็ม เชียร์ได้ · คู่ที่ติด ⚠️ 1 อัน = โชว์พร้อมเตือน + หักดาวจริง · ติด ≥2 อัน = ไม่ต้องโชว์

⚖️ วิธี "หักล้างราคา" (หัวใจ — คิดแบบขี้สงสัย ไม่ใช่ลอกตามตลาดใดตลาดหนึ่ง): มองแต่ละตลาดเป็น "พยาน" คนละปาก แล้วเอามาชนกัน — ตลาดที่หนุนทางเดียวกัน = บวกความมั่นใจ · ตลาดที่สวนกัน = หักออก (ไม่ใช่เมิน) เหลือ "ทางที่พยานส่วนใหญ่ยืนตรงกันหลังหักลบแล้ว" จึงเป็นทีเด็ด · ถ้าหักแล้วก้ำกึ่ง = ดาวต่ำ/ข้าม อย่าฝืนเชียร์
🕒 ความนิ่งข้ามรอบ (ใช้ "ประวัติทีเด็ดรอบก่อนๆ" ด้านล่าง ถ้ามี): คู่ที่ pick รอบนี้ = pick เดิม = ราคานิ่ง = **บวกความมั่นใจ** · คู่ที่ pick เด้งสวนของเดิม = ราคาแกว่ง = **เตือน/หักความมั่นใจ** (บอลใกล้เตะแต่ราคายังไม่นิ่ง = เสี่ยง)
🥇🥈🥉 เหรียญ (เตือนล่วงหน้าว่า "อันไหนมั่นใจสุด" — ใช้แทนสัญลักษณ์ตัวล็อกเดิม): เรียงคู่ทั้งหมดตามความมั่นใจ (ดาว → ไม่มีธง ⚠️ → 🔒 นิ่งหลายรอบ) แล้วแปะเหรียญให้ **3 อันดับแรกเท่านั้น**
   • อันดับ 1 = 🥇 นำหน้าเลขลำดับ · อันดับ 2 = 🥈 · อันดับ 3 = 🥉 · อันดับ 4 เป็นต้นไป = ไม่มีเหรียญ
   • 🥇 คือระดับสูงสุด · แปะได้อันละคู่เท่านั้น ห้ามซ้ำ ห้ามข้ามลำดับ (มี 🥈 ต้องมี 🥇 ก่อน)
   • คู่ที่ติดธง ⚠️ ห้ามได้ 🥇 เด็ดขาด · ถ้าคัดมาได้น้อยกว่า 3 คู่ ก็แปะเท่าที่มี (2 คู่ = 🥇🥈)
   • เหรียญไม่เกี่ยวกับการจัดกลุ่มบอลสด/ยังไม่เตะ — ยังเรียงบอลสดไว้บนเหมือนเดิม เหรียญแค่บอกว่าคู่ไหนมั่นใจสุด

⚠️ **วันไหนของไม่สวย ห้ามบอกว่า "ไม่มี" — ให้ส่งคู่ที่ดีที่สุดเท่าที่มี แล้วบอกตรงๆ ว่าไม่สวย เสี่ยงด้วย**:
   หลังหักดาวตามกฎ ⚠️ ข้างบนแล้ว ถ้า **ไม่เหลือคู่ไหนถึง 3 ดาว เลยสักคู่** ห้ามตอบว่าไม่มีคู่ · ให้หยิบคู่ที่ดีที่สุด 2-3 คู่มาส่งเหมือนเดิมทุกอย่าง (ดาวเท่าที่มีจริง เช่น 2.5 ดาว) แต่ต้องขึ้นบรรทัดเตือนไว้ใต้หัวข้อแบบนี้:
   ⚽ ทีเด็ดบอลวันนี้
   ---------------------------
   ⚠️ วันนี้มีแต่ไม่สวยนะ เสี่ยงด้วย — <บอกสั้นๆ 1 บรรทัดว่าทำไม เช่น "คู่ส่วนใหญ่ราคาสวน %" หรือ "ลีกเล็กล้วน ข้อมูลบาง">
   ---------------------------
   (แล้วต่อด้วยรายการคู่ตามฟอร์แมตปกติ)
   🚫 ห้ามเค้นให้ครบ {MAX_MATCHES} คู่ — ของไม่สวยเอาแค่ 2-3 คู่พอ
   ===DATA===
   []
   (ย้ำ: จำนวนคู่ที่ส่งได้ = 0 ถึง {MAX_MATCHES} คู่ · ไม่มีขั้นต่ำ · เหลือคู่เดียวก็ส่งคู่เดียว · ไม่เหลือเลยก็ตอบแบบข้างบน)

🔴 กฎเหล็กเรื่องสกอร์: **ไม่มีสกอร์ส่งมาให้คุณเลย ไม่ว่าคู่ไหน** (ฟีดนี้สกอร์โผล่ตอนจบเกมเท่านั้น และคู่ที่จบแล้วถูกคัดออกหมดแล้ว)
   • ห้ามเขียนสกอร์ปัจจุบัน/สกอร์จบ ห้ามเขียนว่า "ผลจริง...", "เกมจบ...", "ตามคาด", "เก็บงานเรียบร้อย" เด็ดขาด — เขียนเมื่อไหร่ = ทีเด็ดใช้ไม่ได้ทันที
   • บรรทัด 📌 ให้เขียนเป็น "เหตุผลก่อนเตะ" เสมอ (ราคาบอกอะไร ตลาดไหนหนุน ทำไมถึงเชื่อ) ไม่ใช่การเล่าผลที่เกิดไปแล้ว
   • 🔮 คาดสกอร์ = ตัวเลขที่ "คาดว่าจะเกิด" จากสกอร์คาดของ Forebet เท่านั้น ไม่ใช่สกอร์ที่รู้มาแล้ว

รูปแบบผลลัพธ์ (ทำตามนี้เป๊ะ · หัวข้อ/เหตุผลภาษาไทย · ชื่อทีมภาษาอังกฤษ):
บรรทัดแรกสุด:  ⚽ ทีเด็ดบอลวันนี้
บรรทัดถัดไป:  ---------------------------
แล้วขึ้นหัวข้อ:  ### สรุปทีเด็ดบอลวันนี้ (เรียง ⭐ มากสุดก่อน)

จากนั้นแต่ละคู่ ใส่เลขลำดับ 1. 2. 3. (เรียงดาวมากสุด/มั่นใจสุดไว้บน · เหรียญ 🥇🥈🥉 แปะให้ 3 คู่ที่มั่นใจสุดของทั้งรายการ) รูปแบบ:
N. HH:MM ทีมเหย้า VS ทีมเยือน   (3 คู่มั่นใจสุดให้ขึ้นต้นด้วยเหรียญ 🥇 N. / 🥈 N. / 🥉 N. แทน · ใช้คำว่า "VS" คั่นชื่อทีมเสมอ ห้ามใช้คำว่า "พบ")
⭐ X ดาว (YY%)     ← บรรทัดดาวต้องอยู่ **ถัดจากชื่อคู่ทันที** (ถ้าเป็นบอลสด ให้อยู่ถัดจากบรรทัดสกอร์สด) ห้ามย้ายไปไว้ล่าง
🎯 <คำแนะนำ: เยือนไม่แพ้ / บ้านไม่แพ้ / หาผู้ชนะ / สูงแรก / สูงเต็ม / ช่วยกันยิงหรือจบ2+ / ยิงฝั่งเดียว> + เส้นตัวเลขเท่านั้น (⛔️ ห้ามมี 'เสมอ' / 'ต่ำแรก' / 'ต่ำเต็ม' / 'Under' เด็ดขาด) (เช่น 'เยือนไม่แพ้ +0.25' · 'สูงเต็ม 2.5' · BTTS ไม่ต้องมีเลข)
   🚫 บรรทัด 🎯 นี้ห้ามมีคำว่า Home/Away/HDP/ราคา/ปิดเสมอ ปนเด็ดขาด (ทำให้มั่ว) — เอาแค่คำแนะนำไทย + เลขเส้น
⚖️ บรรทัดราคา — อ่านจากคอลัมน์ "ฝั่งแนะนำ=" กับ "เส้น=" ในตารางราคาจริง แล้วเขียนตามเครื่องหมายของเส้นเป๊ะๆ:
   • เส้นติดลบ (เช่น -0.5, -1.75) = ทีมนั้น "ต่อ" → เขียน  ⚖️ ต่อ: <ชื่อทีม> <เส้น>   เช่น 'ต่อ: FC Anyang -0.5'
   • เส้นเป็นบวก (เช่น +0.25, +1) = ทีมนั้น "รอง รับแต้ม" → เขียน  ⚖️ รอง: <ชื่อทีม> <เส้น>   เช่น 'รอง: Villa San Carlos +0.25'
   • เส้น 0 พอดี = ⚖️ ลูกเปล่า (เสมอราคา)
   • คู่นั้นไม่มีในตารางเลยจริงๆ เท่านั้น = ⚖️ เสมอราคา (ยังไม่เปิดต่อรอง)
   แปลง Home=ชื่อเจ้าบ้าน · Away=ชื่อเยือน เสมอ · ห้ามแปะคำว่า Home/Away ลอยๆ · ห้ามเขียน "ยังไม่เปิดต่อรอง" ถ้าคู่นั้นมีเส้นอยู่ในตาราง
🔮 คาด H-A  (เขียนแบบนี้เป๊ะ: 🔮 เว้นวรรค คำว่า "คาด" แล้วตามด้วยสกอร์ · 🚫 ห้ามมีคำว่า Forebet) · ใช้ค่า "สกอร์คาด" จากตารางราคา AH ของคู่นั้นก่อน · ถ้าคู่นั้นไม่มีในตาราง ค่อยดูจากข้อมูลดิบ · ใส่ทุกคู่ให้ผู้ใช้ดูเทียบเอง · หาไม่เจอจริงๆ ค่อยเว้น
📌 เหตุผล **สั้นๆ บรรทัดเดียวจบ** (≤ 2 บรรทัดเด็ดขาด · เอาเฉพาะเหตุผลหลัก ไม่ต้องท้าวความ ไม่ต้องเตือน)
   🚫 การ์ดหนึ่งใบมีได้แค่ 6 บรรทัดนี้เท่านั้น: หัวคู่ / ⭐ / 🎯 / ⚖️ / 🔮 / 📌 — ห้ามเพิ่มบรรทัดอื่นใดๆ
      (ความนิ่ง 🔒 · ธงเตือน ⚠️ · คำอธิบายเสริม = ใช้ "คิด" เพื่อปรับดาวเท่านั้น ห้ามพิมพ์ออกมาเป็นบรรทัด)
      ยกเว้นบอลสด: แทรกบรรทัดสกอร์สดที่ระบบเติมให้เองได้ (คุณไม่ต้องเขียน)

⏰ เวลา HH:MM (บังคับทุกคู่ ห้ามลืมเด็ดขาด): ดึงจาก "ตารางเวลาแข่งทุกคู่" ด้านบน (จับคู่ด้วยชื่อทีม) · เป็นเวลาไทยแล้ว **เอาแต่ HH:MM ห้ามใส่วันที่** · บอลสด/เตะไปแล้ว = ใส่เวลาเตะ + สกอร์สดใต้ชื่อคู่ · หาเวลาไม่เจอจริงๆ = **ตัดคู่นั้นทิ้ง** (ห้ามส่งคู่ที่ไม่มีเวลา)
🚫 ไม่ต้องใส่ชื่อลีก ในบรรทัดหัวคู่ (ทำให้รก) — แต่ยังใส่ league ใน JSON ท้ายได้
👥 ชื่อทีม: **ใช้ภาษาอังกฤษตาม Forebet ตรงๆ ไม่ต้องแปลเป็นไทย** (อ่านง่ายกว่า จับคู่กับตารางง่ายกว่า) · 🚫 ห้ามเขียน "ทีมเยือน"/"เจ้าบ้าน"/ชื่อลอยๆ · หาชื่อครบ 2 ทีมไม่ได้ = ตัดคู่นั้นทิ้ง

**คั่นระหว่างแต่ละคู่ด้วยเส้นนี้ทุกคู่:**  ---------------------------

🔑 ราคาจริง: ยึดจากคอลัมน์ "ฝั่งแนะนำ=Home/Away" + "เส้น=" ในตารางราคาจริงเท่านั้น (Home=เจ้าบ้าน · Away=เยือน) → แปลงเป็น**ชื่อทีม** แล้วเขียนบรรทัด ⚖️ ตามกฎเครื่องหมายด้านบน (ลบ=ต่อ · บวก=รอง · 0=ลูกเปล่า) · ตารางนี้มาจาก Forebet ครบทุกคู่ทั้งวันแล้ว คู่ที่ไม่มีจริงๆ มีน้อยมาก — ถ้าไม่มีค่อยดู 1X2 (ฝั่ง % สูงกว่า=ต่อ) แล้วเขียน ⚖️ เสมอราคา (ยังไม่เปิดต่อรอง)
   ⚠️ ย้ำ: คู่ที่ Forebet คาดผลเสมอ (เช่น 1-1) **ไม่ได้แปลว่าไม่มีราคา** — Forebet จะเปิดให้ฝั่งรองรับแต้ม (เส้นเป็นบวก) ต้องเขียน ⚖️ รอง: <ทีม> +<เส้น> ห้ามเหมาว่า "ยังไม่เปิดต่อรอง"
- ชื่อลีกใส่ทุกคู่ถ้ามีในข้อมูล
- **ห้ามมีข้อความเกริ่นนำ / คำอธิบายเพิ่มใดๆ** — แสดงเฉพาะหัวข้อ + รายการคู่ตามรูปแบบ แล้วจบ
- 🚫 **ห้ามมีบรรทัดคำเตือนปิดท้าย** (เช่น 'เรทพวกนี้ไม่รวมกรณีใบแดง') — ผู้ใช้รู้อยู่แล้ว เอาออกทั้งหมด

📦 ท้ายสุด (หลังรายการทั้งหมด) ให้ขึ้นบรรทัด "===DATA===" แล้วตามด้วย JSON array ของคู่ที่แนะนำ (เฉพาะที่แสดง) สำหรับบันทึกลงชีต — 1 object ต่อ 1 คู่ ฟิลด์:
{{"date":"YYYY-MM-DD","time":"HH:MM","league":"...","home":"เจ้าบ้าน","away":"เยือน","fav":"ทีมที่เป็นต่อ","pick":"คำแนะนำ","stars":"3.5","pct":"69","id":"2419777"}}
JSON ต้องถูก syntax (double quote) · ส่วนนี้ผู้ใช้ไม่เห็น ระบบเอาไปบันทึกอย่างเดียว
🆔 ฟิลด์ "id" = เลข id ของคู่นั้นจาก "ตารางเวลาแข่งทุกคู่" ด้านบน — **คัดลอกมาตรงๆ ห้ามแต่งเลขเอง ห้ามเดา** · หาไม่เจอให้ใส่ "" (สตริงว่าง)
   (id นี้ระบบใช้เป็นคีย์ถาวรของคู่ ไว้ตามผลจริงมากรอกให้อัตโนมัติ — ใส่ผิด = ตามผลไม่เจอ)
ไม่มีคู่ไหนผ่านเกณฑ์เลย = ใส่ [] (array ว่าง) ห้ามละบรรทัด ===DATA=== ทิ้ง

{time_table}

{ah_table}

{history}

{flip}

{crit}

ข้อมูลดิบ (หลายตลาดรวมกัน — ใช้ประกอบเหตุผล/ดาว · แต่เวลา ยึดตารางเวลา · เส้น HDP ยึดตาราง AH เท่านั้น):
{raw_text}
"""
        keys = gemini_keys()
        if not keys:
            print("❌ ไม่มี Gemini key")
            return None
        # ล็อกให้ตอบ "นิ่ง" รอบต่อรอบ (temperature ต่ำ) — ไม่งั้นมันสุ่มเปลี่ยนคำเอง ทั้งที่ราคาเท่าเดิม → วัดความนิ่งไม่ได้
        last_err = None
        # สลับทีละ key: key เต็มโควตา (429) → ข้ามไป key ถัดไป (โควตาใหม่)
        for ki, key in enumerate(keys, 1):
            quota_full = False
            for model in GEMINI_MODELS:
                try:
                    text, route = gemini_generate(key, model, prompt, temperature=0.15)
                    print(f"🤖 key#{ki}/{len(keys)} · รุ่น {model} · เส้นทาง {route}")
                    return text
                except QuotaFull as eq:
                    last_err = eq
                    print(f"🛑 key#{ki} โควตาเต็ม (20/วัน) → สลับ key ถัดไป")
                    quota_full = True
                    break     # ออกจาก loop รุ่น ไป key ถัดไป
                except Exception as em:
                    last_err = em
                    print(f"⚠️ key#{ki} รุ่น {model}: {str(em)[:200]}")
                    continue      # 404/อื่นๆ → ลองรุ่นถัดไปของ key เดิม
            if not quota_full and len(keys) > 1:
                continue          # key นี้ล้มด้วยเหตุอื่น ลอง key ถัดไปเผื่อได้
        print(f"❌ Gemini ล้มทุก key/รุ่น (โควตาเต็มหมด/ข้ามรอบนี้เงียบๆ): {str(last_err)[:200]}")
        return None
    except Exception as e:
        print(f"❌ Error ในการเรียก Gemini AI: {e}")
        return None

# ==========================================
# 6. การทำงานหลัก — ดึงทุกตลาด → รวม → วิเคราะห์ครั้งเดียว → ส่งข้อความเดียว
# ==========================================
# ==========================================
# 4.85 สร้างโครงสร้างเดิมทั้งหมดจาก "JSON API หลังปุ่ม More"  (forebet_api.py)
#      ทำไมต้องมี: Jina อ่านได้แค่หน้าแรกของตาราง (~42 คู่/ตลาด) และหน้า predictions-1x2
#      ถูกตัดลิงก์+ชื่อทีมทิ้ง → 1x2 มีไม่ครบทุกคู่ · API ได้ครบทั้งวัน (วัดจริง 374-413 คู่)
#      และ id ตรงกันเป๊ะทุกตลาด → ธง/เรท/ทุกตลาด/ราคา AH ครบทุกคู่ ไม่ต้องเดา
# ==========================================
def _api_time(dbah):
    """DATE_BAH = 'YYYY-MM-DD HH:MM:SS' เป็น "เวลายุโรป" เสมอ (พารามิเตอร์ tz ไม่ขยับค่านี้)
    ยืนยันแล้ว 44/44 แถวว่าตรงกับเวลาที่ markdown เคยแสดง → ส่งเข้า _to_thai_time สายเดิมได้เลย"""
    try:
        d, t = str(dbah).split(" ")
        y, mo, dd = d.split("-")
        return _to_thai_time(f"{dd}/{mo}/{y}", t[:5])
    except Exception:
        return ""

def _api_bkk(dbah):
    """DATE_BAH → datetime เวลาไทย (ไว้คัดว่าอยู่ใน 'วันบอล' รอบนี้ไหม)"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return (datetime.strptime(str(dbah), "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=ZoneInfo("Europe/Paris")).astimezone(ZoneInfo("Asia/Bangkok")))

def _api_ah(r):
    """คืน (ฝั่งที่ Forebet เชียร์, เส้นแบบมีเครื่องหมาย) — ตรวจกับ markdown จริงแล้ว 42/42 แถว

    AH_type เก็บ "เส้น" อย่างเดียว ไม่มีข้อมูลฝั่ง → ต้องหาฝั่งเอง:
      สกอร์คาดไม่เท่ากัน  → ฝั่งที่คาดว่ายิงมากกว่า = ฝั่งต่อ (AH_type ติดลบ = จ่ายแต้ม)
      สกอร์คาดเท่ากัน     → Forebet เชียร์ "ฝั่งรอง รับแต้ม" (AH_type เป็นบวก) · ฝั่งรอง = ฝั่งที่ % ชนะน้อยกว่า
                            (ของจริง: แถวเส้นบวก 74 แถว เป็นแถวคาดเสมอทั้ง 74 แถว ไม่มีข้อยกเว้น)
      AH_type = 0        → ลูกเปล่า เสมอราคาจริงๆ
    """
    try:
        ph, pa = int(r.get("host_sc_pr") or 0), int(r.get("guest_sc_pr") or 0)
    except Exception:
        ph = pa = 0
    line = str(r.get("AH_type") or "").strip()
    if line in ("", "None"):
        return "", ""
    if ph > pa:
        side = "Home"
    elif pa > ph:
        side = "Away"
    else:
        try:
            side = "Away" if int(r.get("Pred_2") or 0) < int(r.get("Pred_1") or 0) else "Home"
        except Exception:
            side = "Home"
    return side, line

def build_from_api(matches, leagues, time_map, flag_map, odds_map, mkt_map):
    """เติมโครงสร้างเดิม (time/flag/odds/mkt) + คืน (ah_table, combined) ที่หน้าตาเหมือนของเดิมเป๊ะ"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    _n = datetime.now(ZoneInfo("Asia/Bangkok"))
    now_order = (_n.hour * 60 + _n.minute - 600) % 1440   # ตำแหน่ง "ตอนนี้" บนไทม์ไลน์วันบอล
    ah_rows, txt_rows = [], []
    for mid, r in matches.items():
        host, guest = (r.get("HOST_NAME") or "").strip(), (r.get("GUEST_NAME") or "").strip()
        if not host or not guest:
            continue
        tm = _api_time(r.get("DATE_BAH"))
        if not tm:
            continue
        names = f"{host} - {guest}"
        time_map.setdefault(mid, (names, tm))

        cc = (r.get("code") or "").strip()
        if not cc:
            lg = leagues.get(str(r.get("league_id") or ""), [])
            cc = (lg[5] if len(lg) > 5 else "") or ""
        if cc:
            flag_map.setdefault(mid, cc)

        # เรท: odds_ah (หน้า AH) ก่อน แล้วค่อย best_odd — API ให้มาเป็นทศนิยมอยู่แล้ว ไม่ต้องแปลงจากอเมริกัน
        for key in ("odds_ah", "best_odd"):
            v = str(r.get(key) or "").strip()
            try:
                if v and float(v) >= 1.01:
                    odds_map.setdefault(mid, f"{float(v):.2f}")
                    break
            except Exception:
                pass

        d = mkt_map.setdefault(mid, {})
        p1, px, p2 = r.get("Pred_1"), r.get("Pred_X"), r.get("Pred_2")
        if p1 not in (None, "") and px not in (None, "") and p2 not in (None, ""):
            d["x2"] = f"{p1}/{px}/{p2}"
        un, ov = r.get("pr_under"), r.get("pr_over")
        if un not in (None, "") and ov not in (None, ""):
            avg = str(r.get("goalsavg") or "").strip()
            d["ou"] = f"{un}/{ov}" + (f"|{avg}" if avg else "")
        ng, gg = r.get("Pred_no_gg"), r.get("Pred_gg")
        if ng not in (None, "") and gg not in (None, ""):
            d["btts"] = f"{ng}/{gg}"
        side, line = _api_ah(r)
        if side:
            d["ah"] = f"{side}|{line}" + (f"|{r.get('predAH')}" if r.get("predAH") else "")

        try:
            h, mm = map(int, tm.split(":"))
            order = (h * 60 + mm - 600) % 1440   # วันบอล 10:00=0 ... 09:59=1439
        except Exception:
            order = 9999

        pscore = f"{r.get('host_sc_pr', '')}-{r.get('guest_sc_pr', '')}"
        lg = leagues.get(str(r.get("league_id") or ""), [])
        lgname = lg[1] if len(lg) > 1 else (r.get("short_tag") or "")
        if side:
            ah_rows.append((order, f"{tm} | {names} | ฝั่งแนะนำ={side} เส้น={line} | "
                                   f"สกอร์คาด {pscore} | เชื่อมั่น {r.get('predAH', '')}% | id={mid}"))

        # ข้อมูลดิบต่อคู่ (แทน markdown ที่ compact มา) — บรรทัดเดียวจบ อ่านง่าย ประหยัดโทเคน
        # 🚫 ห้ามส่งสกอร์เข้า prompt เด็ดขาด: ฟีดนี้สกอร์ = "ผลจบ" อย่างเดียว (ไม่มีสกอร์สด)
        #    คู่ที่จบ/เลื่อนถูกคัดทิ้งตั้งแต่ fetch_via_api แล้ว เหลือแค่ยังไม่เตะ + เพิ่งเตะไม่เกิน 2 ชม.
        st = ""
        sc = " | กำลังเตะอยู่ (ฟีดยังไม่มีสกอร์)" if order <= now_order else ""
        txt_rows.append((order,
                         f"{tm} | {lgname} | {names} | 1x2 {d.get('x2', '-')} | "
                         f"สูงต่ำ {d.get('ou', '-')} | ทั้งคู่ยิง {d.get('btts', '-')} | "
                         f"AH {d.get('ah', '-')} | สกอร์คาด {pscore} | เรท {odds_map.get(mid, '-')}"
                         f"{sc}{' | ' + st if st and st != 'None' else ''} | id={mid}"))

    ah_rows.sort(key=lambda x: x[0])
    txt_rows.sort(key=lambda x: x[0])
    ah_table = ""
    if ah_rows:
        ah_table = ("===ตารางราคาแฮนดิแคปจริงจาก Forebet (แหล่งเดียวของเส้น HDP+เวลา+สกอร์คาด · ใช้ตรงนี้เท่านั้น)===\n"
                    "(เวลาไทยแล้ว · วันบอล 10:00→09:59 · เส้นติดลบ = ทีมนั้นเป็น \"ต่อ\" ต้องจ่ายแต้ม · "
                    "เส้นเป็นบวก = ทีมนั้นเป็น \"รอง\" ได้รับแต้ม (คู่ที่คาดผลเสมอ Forebet จะเชียร์ฝั่งรองรับแต้ม) · "
                    "เส้น 0 = ลูกเปล่า เสมอราคาจริง · สกอร์คาด=เจ้าบ้าน-เยือน)\n"
                    + "\n".join(r[1] for r in ah_rows))
    combined = ""
    if txt_rows:
        combined = ("\n\n===== ข้อมูลทุกตลาดรวมต่อคู่ (จาก Forebet API ครบทุกคู่ทั้งวัน) =====\n"
                    "(1x2 = บ้าน/เสมอ/เยือน % · สูงต่ำ = ต่ำ/สูง %|เฉลี่ยลูก · ทั้งคู่ยิง = ไม่ยิง/ยิง % · "
                    "AH = ฝั่งที่เชียร์|เส้น|มั่นใจ%)\n"
                    + "\n".join(r[1] for r in txt_rows))
    return ah_table, combined


# ---------- สถิติจริง "ครึ่งแรก → เต็มเวลา" (สร้างไว้ล่วงหน้าโดย ht_history.py) ----------
HT_FLIP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ht_flip.json")
HT_BLOCK = ""   # บล็อกสถิติที่จะยัดเข้า prompt (เติมตอน fetch เพราะต้องรู้ว่าวันนี้มีลีกอะไรบ้าง)

def _p(a, b):
    return round(a / b * 100) if b else 0

def ht_flip_block(keep, leagues, min_n=25, max_lines=16):
    """บล็อกสถิติพลิกครึ่งแรก — เอาเฉพาะลีกที่ "มีคู่ในวันนี้" และเบี้ยวจากค่ากลางจริงๆ
    (ไม่งั้นยัด 900 ลีกเข้า prompt = เปลืองโทเคนเปล่าและ Gemini ไม่ได้ใช้)"""
    try:
        with open(HT_FLIP_PATH, encoding="utf-8") as f:
            db = json.load(f)
    except Exception as e:
        print(f"ℹ️ ไม่มีสถิติ HT→FT ({e})")
        return ""
    a = db.get("all") or {}
    if not a.get("lead"):
        return ""
    base_keep = _p(a["lw"], a["lead"])      # นำครึ่งแรกแล้วยังชนะจบ (ค่ากลางทุกลีก)
    todays = {str(v.get("league_id") or "0") for v in keep.values()}
    rows = []
    for lid in todays:
        lg = (db.get("leagues") or {}).get(lid)
        if not lg or lg.get("n", 0) < min_n or not lg.get("lead"):
            continue
        k = _p(lg["lw"], lg["lead"])
        rows.append((k - base_keep, lg, k))
    rows.sort(key=lambda x: x[0])          # ลีกที่ "นำแล้วเอาไม่อยู่" มากสุดขึ้นก่อน
    # เอาสองหัวท้าย: ลีกพลิกบ่อยสุด (ระวัง) + ลีกนำแล้วอยู่ยาวสุด (ตามได้) — ตรงกลางคือค่ากลาง ไม่ต้องบอก
    lo = [r for r in rows if r[0] <= -6][:max_lines * 2 // 3]
    hi = [r for r in rows if r[0] >= 6][-(max_lines - len(lo)):] if len(rows) > len(lo) else []
    pick = lo + hi
    if not pick:
        pick = rows[:6]
    if not pick:
        return ""
    L = [f"===สถิติผลจริงย้อนหลัง {len(db.get('dates') or [])} วัน ({a['n']:,} คู่) — ครึ่งแรกอยู่ไหมถึงจบเกม===",
         f"ค่ากลางทุกลีก: นำครึ่งแรก → ชนะจบ {base_keep}% · โดนตีเสมอ {_p(a['ld'], a['lead'])}% · "
         f"แพ้พลิก {_p(a['ll'], a['lead'])}% | เสมอครึ่งแรก → จบมีผลแพ้ชนะ {_p(a['drd'], a['dr'])}% | "
         f"ลูกครึ่งแรก {a['hg']/(a['n'] or 1):.2f} จากทั้งเกม {a['fg']/(a['n'] or 1):.2f}",
         "วิธีใช้: ลีกที่ 'นำแล้วอยู่' ต่ำกว่าค่ากลาง = อย่าเพิ่งฟันตามฝั่งที่นำอยู่ตอนพักครึ่ง (ลดดาว/เลี่ยง) · "
         "ลีกที่สูงกว่าค่ากลาง = ตามฝั่งที่นำได้มั่นใจขึ้น · ลูกครึ่งหลังเยอะกว่าปกติ = หนุน 'สูงเต็ม' มากกว่า 'สูงแรก'",
         "(ลีก | นำ HT แล้วชนะจบ | ตีเสมอ | แพ้พลิก | เสมอ HT→มีผล | ลูก HT→เต็มเกม | จำนวนคู่)"]
    for dev, lg, k in pick:
        L.append(f"{lg.get('name', '?')} | {k}% ({dev:+d}) | {_p(lg['ld'], lg['lead'])}% | "
                 f"{_p(lg['ll'], lg['lead'])}% | {_p(lg['drd'], lg['dr'])}% | "
                 f"{lg['hg']/lg['n']:.1f}→{lg['fg']/lg['n']:.1f} | {lg['n']}")
    print(f"🔄 สถิติ HT→FT: ใส่ prompt {len(pick)} ลีก (จาก {len(todays)} ลีกที่มีคู่วันนี้ · คลัง {len(db.get('leagues') or {})} ลีก)")
    return "\n".join(L)


def fetch_via_api(time_map, flag_map, odds_map, mkt_map):
    """ดึง 2 วัน (วันนี้+พรุ่งนี้ตามเวลาไทย) แล้วคัดเฉพาะคู่ที่อยู่ใน 'วันบอล' รอบนี้
    ต้องดึง 2 วันเพราะฟีดของ Forebet 1 วัน = ยุโรป 17:00 วันก่อน → 19:00 วันนั้น
    ครอบไม่ถึงคู่ดึกฝั่งอเมริกา (ไทยตี 1-9) ที่ยังนับเป็นวันบอลเดียวกัน"""
    if not fbapi:
        return "", "", None
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Bangkok"))
    # วันบอลเริ่ม 10:00 ไทย → ก่อน 10:00 ถือว่ายังเป็นวันบอลของ "เมื่อวาน"
    start = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now.hour < 10:
        start -= timedelta(days=1)
    end = start + timedelta(days=1)
    try:
        sess = fbapi.fb_session()
    except Exception as e:
        print(f"⚠️ เปิด session Forebet ไม่ได้: {e}")
        return "", "", None
    matches, leagues = {}, {}
    global FB_SNAP
    for off in (0, 1):
        day = (start + timedelta(days=off)).strftime("%Y-%m-%d")
        m, lg = fbapi.fb_fetch_day(day, sess=sess)
        if off == 0:
            # ก้อนวันนี้ = ก้อนที่มีสกอร์สด/ผลจบ → จับเวลาตรงนี้เป็น "เวลาข้อมูล" ของรอบ
            FB_SNAP = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%H:%M")
        leagues.update(lg)
        for k, v in m.items():
            if k in matches:
                for kk, vv in v.items():
                    if kk not in matches[k] or matches[k][kk] in (None, "", []):
                        matches[k][kk] = vv
            else:
                matches[k] = v
    # คัดเฉพาะคู่ในวันบอลรอบนี้ (กันคู่เมื่อวาน/มะรืนมาปนจนตารางบวม)
    # + ตัด "คู่ที่เตะไม่ได้แล้ว" ทิ้งตั้งแต่ต้นทาง (ผลจริง 26/07: ฟีดนี้ไม่มีสกอร์สด —
    #   Host_SC จะโผล่ก็ต่อเมื่อจบแล้วเท่านั้น ปล่อยผ่านไป AI จะเห็นผลจบแล้วเชียร์ย้อนหลัง
    #   เช่นรอบ 22:20 แนะนำคู่ 17:00/18:00/19:00 ที่จบไปแล้ว พร้อมเล่าสกอร์จบเป็นเหตุผล)
    DONE_ST = {"ft", "aet", "pen", "awarded"}                      # จบแล้ว
    OFF_ST = {"postp", "cancl", "canc", "susp", "abn", "abandoned", "int"}  # เลื่อน/ยกเลิก/พัก
    keep = {}
    cut = {"เตะเกิน2ชม": 0, "จบแล้ว": 0, "เลื่อน/ยกเลิก": 0}
    for k, v in matches.items():
        try:
            t = _api_bkk(v.get("DATE_BAH"))
        except Exception:
            continue
        if not (start <= t < end):
            continue
        st = str(v.get("comment") or "").strip().lower().rstrip(".")
        if (now - t).total_seconds() > 2 * 3600:     # เตะไปเกิน 2 ชม. = จบไปแล้วแน่ๆ
            cut["เตะเกิน2ชม"] += 1
            continue
        if st in OFF_ST:
            cut["เลื่อน/ยกเลิก"] += 1
            continue
        if st in DONE_ST or v.get("Host_SC") not in (None, ""):
            cut["จบแล้ว"] += 1
            continue
        keep[k] = v
    print(f"🗓️ วันบอล {start:%d/%m %H:%M} → {end:%d/%m %H:%M} (ไทย) · ยังเล่นได้ {len(keep)}/{len(matches)} คู่ "
          f"· ตัดออก " + " · ".join(f"{k} {v}" for k, v in cut.items() if v))
    if not keep:
        return "", "", sess
    global HT_BLOCK
    HT_BLOCK = ht_flip_block(keep, leagues)
    ah_table, combined = build_from_api(keep, leagues, time_map, flag_map, odds_map, mkt_map)
    return ah_table, combined, sess


def main():
    print("🚀 เริ่มดึงข้อมูล Forebet + คัดคู่เด่น...")

    combined = ""
    ok = 0
    ah_raw = ""
    time_map = {}   # id -> (ชื่อคู่, เวลาไทย)
    flag_map = {}   # id -> รหัสประเทศ (จากรูปธงของ Forebet)
    odds_map = {}   # id -> เรทน้ำ (coef.) → ส่งไปเก็บในชีต ใช้คิดกำไรจริง ไม่ใช่แค่ถูก/ผิด
    mkt_map = {}    # id -> {x2, ah, ou, btts} ตัวเลขดิบทุกตลาด → ชีตเอาไปตรวจธงขัดแย้ง

    # ---------- ทางหลัก: JSON API หลังปุ่ม More (ครบทุกคู่ทั้งวัน) ----------
    ah_table, combined, _sess = fetch_via_api(time_map, flag_map, odds_map, mkt_map)
    if time_map:
        ok = 1
        print(f"✅ ใช้ Forebet API: {len(time_map)} คู่ · ครบทุกตลาดในคำขอเดียว (ไม่ต้องพึ่ง Jina)")

    # ---------- ตัวสำรอง: อ่าน markdown ผ่าน Jina แบบเดิม (เผื่อ Cloudflare บล็อก IP ของ runner) ----------
    urls = []
    if not time_map:
        print("⚠️ API ไม่ได้ผล → ถอยไปใช้ Jina แบบเดิม")
        urls_file = "urls.txt"
        if os.path.exists(urls_file):
            with open(urls_file, "r", encoding="utf-8") as f:
                urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        if not urls:
            print("⚠️ ไม่พบ URL ในไฟล์ urls.txt")
            send_telegram_message("⚠️ ดึงข้อมูล Forebet ไม่ได้เลย (ทั้ง API และ Jina) — ข้ามรอบนี้ครับ")
            return
    for index, url in enumerate(urls, 1):
        print(f"{index}/{len(urls)} ดึง: {url}")
        raw = scrape_football_data(url)
        if raw:
            ok += 1
            label = url.rstrip("/").split("/")[-1]
            combined += f"\n\n===== ตลาด: {label} =====\n{_compact(raw)}"
            collect_times(raw, time_map)      # เก็บเวลาแข่งจากทุกหน้า
            collect_flags(raw, flag_map)      # เก็บธงชาติตามลีก
            collect_markets(raw, url, mkt_map)  # เก็บ %/เส้น ของแต่ละตลาด (หน้าที่ไม่เกี่ยวจะ return ทันที)
            if "asian-handicap" in url:
                ah_raw = raw   # เก็บดิบไว้ให้ parser (ก่อน compact)
                collect_odds(raw, odds_map)   # เรทเอาจากหน้านี้เท่านั้น (วัดตำแหน่งคอลัมน์มาแล้ว หน้าอื่นเลย์เอาต์ต่าง)
        time.sleep(3)  # กันชนลิมิต

    # หน้าบอลสด → นาที + สกอร์ ณ ขณะนั้น (เอาไปแปะใต้หัวคู่)
    live_map = {}
    live_raw = scrape_football_data(LIVE_URL)
    if live_raw:
        collect_flags(live_raw, flag_map)
        live_map = parse_live_table(live_raw)
    nx2 = sum(1 for v in mkt_map.values() if v.get("x2"))
    print(f"🔴 บอลสด: {len(live_map)} คู่ · 🏳️ ธง: {len(flag_map)} คู่ · 💰 เรท: {len(odds_map)} คู่")
    print(f"📐 ตัวเลขตลาด: {len(mkt_map)} คู่ (มี 1x2 {nx2} คู่)")

    if not ah_table:
        ah_table = parse_ah_table(ah_raw)   # สายสำรอง Jina เท่านั้น (สาย API สร้างตารางมาให้แล้ว)
    time_table = fmt_time_table(time_map)
    print(f"🕐 ตารางเวลาแข่ง: {len(time_map)} คู่ · 📊 ราคา AH: {max(0, len(ah_table.splitlines()) - 3) if ah_table else 0} คู่")
    # 🚨 กันเงียบ: prompt สั่งว่า "หาเวลาไม่เจอ = ตัดคู่ทิ้ง" → ถ้าตารางเวลาว่าง Gemini จะตัดทิ้งหมดแล้วเราไม่รู้ตัว
    #    (เคยเกิดจริง: Forebet เปลี่ยนรูปแบบวันที่ → regex จับไม่ได้สักคู่ → บอทเงียบไปเฉยๆ)
    if not time_map:
        print("❌ ดึงเวลาแข่งไม่ได้สักคู่ — น่าจะ Forebet เปลี่ยนรูปแบบลิงก์/วันที่อีกแล้ว (ดู _LINK_PAT)")
        send_telegram_message("⚠️ วันนี้อ่านเวลาแข่งจาก Forebet ไม่ได้สักคู่ (เว็บน่าจะเปลี่ยนรูปแบบ) — ข้ามรอบนี้ ยังไม่ส่งทีเด็ดครับ")
        return

    if not combined.strip():
        print("⚠️ ดึงข้อมูลไม่ได้เลย")
        send_telegram_message("⚠️ วันนี้ดึงข้อมูล Forebet ไม่ได้ ลองใหม่รอบถัดไปครับ")
        return

    history = fetch_history_block()  # ประวัติรอบก่อนๆ วันนี้ → วัดความนิ่ง (บอกซ้ำ = มั่นใจ)
    crit = fetch_feedback_block()    # ผลจริงย้อนหลัง → สั่งตัดเกณฑ์ที่วัดแล้วแย่กว่าค่ากลาง
    print(f"🤖 แหล่งข้อมูล {ok} ชุด → ให้ Gemini คัดคู่เด่น 1-{MAX_MATCHES}...")
    print(f"📦 ข้อมูลรวมหลัง compact: {len(combined):,} ตัวอักษร (~{len(combined)//4:,} tokens)")
    result = analyze_with_gemini(combined[:1200000], ah_table, time_table, history, HT_BLOCK, crit)  # cap ~300K tokens รับ 20+ ตลาด (Gemini flash context 1M)

    # AI ไม่พร้อม/โควตาเต็ม → ข้ามรอบนี้เงียบๆ (ไม่สแปม error เข้า Telegram ทุก 2 ชม.)
    if not result or not result.strip():
        print("⚠️ ไม่มีผลวิเคราะห์ (AI ไม่พร้อม/โควตาเต็ม) — ข้ามรอบนี้ ไม่ส่ง Telegram")
        return

    # แยกส่วน DATA (JSON สำหรับบันทึกชีต) ออกจากข้อความที่ส่ง Telegram
    tips_raw = None
    if "===DATA===" in result:
        text_part, _, tips_raw = result.partition("===DATA===")
        result = text_part.strip()

    tips_raw = patch_ids(tips_raw, time_map)   # เติม id ก่อนใครใช้ — ธง/LIVE/เรท/คีย์ชีต คีย์เป็น id ทั้งหมด
    result = decorate_tips(result, tips_raw, flag_map, live_map)  # เติมธง + บรรทัด LIVE (ทำเอง ไม่ให้ AI มั่ว)

    # ⛔️ ตัด เสมอ/ต่ำ ทิ้งจริง (ไม่พึ่ง prompt อย่างเดียว) — ตัดหมดทั้งรายการ = ไม่ต้องส่ง
    result, tips_raw, n_left = drop_banned_tips(result, tips_raw)
    if n_left == 0:
        print("⚠️ ไม่เหลือทีเด็ดหลังตัด เสมอ/ต่ำ — ข้ามรอบนี้ ไม่ส่ง Telegram")
        return

    # 🕒 เวลาข้อมูลจริงจาก Forebet (ไม่ใช่เวลาที่กดสั่ง) — วางบนสุด เห็นก่อนเลยว่าข้อมูลสดแค่ไหน
    if FB_SNAP:
        result = f"🕒 ข้อมูลจาก Forebet ณ {FB_SNAP} น.\n" + result.lstrip()

    print("📲 ส่งเข้า Telegram...")
    send_telegram_message(result)  # Gemini คุมหัวข้อ+รูปแบบทั้งหมดตาม prompt แล้ว
    if tips_raw:
        log_tips_to_piktax(tips_raw, odds_map, mkt_map)


def log_tips_to_piktax(raw, odds_map=None, mkt_map=None):
    """ส่ง JSON ทีเด็ดไปบันทึกชีตที่ PIKTAX (doPost -> logFootballTips_)"""
    if not PIKTAX_STATE_URL:
        return
    m = re.search(r"\[.*\]", raw.strip().strip("`"), re.S)  # ดึง JSON array (ตัด code fence)
    if not m:
        print("⚠️ ไม่พบ JSON tips สำหรับบันทึก")
        return
    try:
        tips = json.loads(m.group(0))
    except Exception as e:
        print(f"⚠️ JSON tips ผิดรูปแบบ: {e}")
        return
    # แปะเรทน้ำเข้าไปเอง (ไม่ให้ AI กรอก — มันจะมั่วเลข) · หาไม่เจอปล่อยว่าง ชีตจะข้ามคู่นั้นตอนคิดกำไร
    nod = nmk = 0
    for t in tips:
        mid = str(t.get("id") or "").strip()
        o = (odds_map or {}).get(mid, "")
        if o:
            t["odds"] = o
            nod += 1
        mk = (mkt_map or {}).get(mid) or {}
        if mk:
            # ชื่อคีย์สั้นๆ ฝั่ง GAS อ่านตรงนี้ (logFootballTips_) → คอลัมน์ O..R แล้วคิดธงขัดที่ S
            if mk.get("x2"):
                t["m1x2"] = mk["x2"]
            if mk.get("ah"):
                t["mah"] = mk["ah"]
            if mk.get("ou"):
                t["mou"] = mk["ou"]
            if mk.get("btts"):
                t["mbtts"] = mk["btts"]
            nmk += 1
    try:
        base = PIKTAX_STATE_URL.split("?")[0]
        requests.post(base, json={"fbtips": tips}, timeout=60)
        print(f"📝 ส่งบันทึก {len(tips)} คู่ลงชีตแล้ว (มีเรท {nod}/{len(tips)} · มีตัวเลขตลาด {nmk}/{len(tips)})")
    except Exception as e:
        print(f"⚠️ ส่งบันทึกชีตไม่ได้: {e}")

if __name__ == "__main__":
    main()

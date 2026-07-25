import os
import re
import json
import time
import urllib.parse
import requests

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
        rows.append((order, f"{tm} | {names} | ฝั่งต่อ={fav} เส้น={fline} | สกอร์คาด {pscore} | เชื่อมั่น {prob}% | id={mid}"))
    if not rows:
        return ""
    rows.sort(key=lambda r: r[0])
    return ("===ตารางราคาแฮนดิแคปจริงจาก Forebet (แหล่งเดียวของเส้น HDP+เวลา+สกอร์คาด · ใช้ตรงนี้เท่านั้น)===\n"
            "(เวลาไทยแล้ว · วันบอล 10:00→09:59 · ฝั่งต่อ=ทีมที่ Forebet คาดว่าชนะ (ดูจากสกอร์คาด) เส้นติดลบ · ฝั่งต่อ=เสมอ เส้น=0 คือคาดผลเสมอ ไม่มีต่อ · สกอร์คาด=เจ้าบ้าน-เยือน)\n"
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

_HEAD_RE = re.compile(r'^(?:🔥\s*)?\d+\.\s*\d{1,2}:\d{2}\s')

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
                out[-1] = re.sub(r'^((?:🔥\s*)?\d+\.\s*\d{1,2}:\d{2})\s+', r'\1 ' + flag + ' ', out[-1])
            if live:
                out.append(live)
            break
    return "\n".join(out)

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
            "   ≤ −10        = ความมั่นใจร่วงแรง → −1 ดาว และห้ามติด 🔥 ตัวล็อก)\n"
            + "\n".join(rows))

# ==========================================
# 5. วิเคราะห์ + คัดคู่เด่น 1-20 ด้วย Gemini (เงื่อนไข Football Live Analyst)
# ==========================================
def analyze_with_gemini(raw_text, ah_table="", time_table="", history=""):
    try:
        prompt = f"""คุณคือ Football Market Analyst — ไม่ใช่แค่ "คนรายงานราคา" แต่เป็น "นักวิเคราะห์ขี้สงสัย" ที่: (1) จดจำราคารอบก่อน (2) เอาทุกตลาดมา "หักล้างกันเอง" หาว่าราคาไหนจริง/ราคาไหนหลอก (3) เตือนล่วงหน้าก่อนบอลเตะ ว่าคู่ไหนล็อกได้ คู่ไหนน่าระวัง — จากข้อมูล Forebet (หลายตลาด: 1x2, สูง/ต่ำ, ครึ่งแรก, HT/FT, ทั้งคู่ยิง, Double Chance, Asian Handicap, TOP Predictions) ตามเงื่อนไขนี้:

⭐ สำคัญสุด: เลือกเฉพาะ "คู่เด่นที่สุด 1-{MAX_MATCHES} คู่" ของวันนั้นเท่านั้น (บางวันมีน้อยกว่า {MAX_MATCHES} ได้ ไม่ต้องฝืนให้ครบ · ต่ำกว่า 3 ดาวไม่ต้องเอา)
🔴 การเรียงลำดับ (สำคัญสุด ทำก่อนอย่างอื่น): แบ่งเป็น 2 กลุ่มแล้ววางตามนี้เสมอ —
   (1) **บอลสด = กำลังเตะ/เตะไปแล้ว (มีสกอร์สด) → วางไว้บนสุดทั้งหมดก่อน**
   (2) **บอลวันนี้ที่ยังไม่เตะ → วางต่อจากกลุ่มบอลสด**
   ภายในแต่ละกลุ่มค่อยเรียงคู่ที่มั่นใจมากสุด (🔥 ตัวล็อก → ดาวเยอะ) ไว้บน · รวมสองกลุ่มไม่เกิน {MAX_MATCHES} คู่

1. สถานะเกม: ถ้ามีข้อมูลสด ('เกมหยุด' / 'เลื่อน' / 'จบ') ให้แสดงสกอร์สด + เวลาปัจจุบันใต้ชื่อคู่ · ถ้าเลื่อน/หยุด ขึ้นเตือนตัวหนา: ⚠️ **[บอลเลื่อน/หยุด]**
2. รูปแบบคำแนะนำ (ใช้คำเหล่านี้เท่านั้น):
   • ผลแพ้ชนะ/แฮนดิแคป: 'เยือนไม่แพ้', 'บ้านไม่แพ้', 'เสมอ', 'หาผู้ชนะ'
   • สูง/ต่ำประตู: 'สูงแรก' (ครึ่งแรกเกินเส้น), 'สูงเต็ม' (เต็มเวลาเกินเส้น), 'ต่ำแรก' (ครึ่งแรกไม่ถึงเส้น), 'ต่ำเต็ม' (เต็มเวลาไม่ถึงเส้น) — ระบุเส้น Over/Under จากตลาด สูง/ต่ำ + ครึ่งแรก (เช่น 'สูงเต็ม 2.5')
   • ทั้งคู่ยิง (BTTS): 'ยิงกันทั้งคู่' (ทั้ง 2 ทีมยิงได้อย่างน้อยทีมละ 1) · 'ไม่ยิงกันทั้งคู่' (มีทีมใดทีมหนึ่งยิงไม่ได้เลย) — ไม่ต้องมีเลขเส้น
   🚫 **ห้ามมั่วเส้น HDP เด็ดขาด** — เส้นแฮนดิแคป (เช่น -0.75, +0.25, -1.5) ต้องก๊อปตรงจาก "ตารางราคาแฮนดิแคปจริง" ด้านบนเท่านั้น (จับคู่ด้วยชื่อทีม) · ฝั่งต่อ (Home/Away) ก็ยึดตามตาราง · ถ้าคู่ไหนไม่มีในตาราง = **ใส่แค่คำแนะนำ ไม่ต้องมีเส้น** ห้ามเดา ห้ามเขียน "+0.5" ลอยๆ ห้ามเขียนคำว่า "Asian handicap"/"HDP" แทนตัวเลข
3. เกณฑ์ดาว: 4 ดาว (80-99%), 3.5 ดาว (65-79%), 3 ดาว (50-64%) · เรียง 4 ดาวไว้บนสุด
4. ประเมินข้ามทุกตลาดที่ให้มา (1x2, สูง/ต่ำ, ครึ่งแรก, HT/FT, ทั้งคู่ยิง, Double Chance, AH, Corners, Scorers ฯลฯ) — **คู่ที่หลายตลาดชี้ตรงกัน = มั่นใจสูง เรียงบน** · ตลาดขัดกันเอง/ชี้คนละทาง = ลดดาวหรือข้าม · ยึด 'บอลวันนี้' + 'TOP Predictions' เป็นหลัก
5. กระชับ อ่านบนมือถือง่าย เหมาะส่ง Telegram (ระบบมีปุ่มเปิด/ปิดเสียงให้แล้ว ไม่ต้องเขียนปุ่มเอง)

🧠 วิธีคิด (สำคัญมาก — ประเมินทีละคู่จาก "ทุกตลาด" ที่อ่านมา แล้วหักล้างจนเหลือทีเด็ด 1 อย่างที่หลายตลาดหนุนตรงกันมากสุด):
สัญญาณต่อคู่: 1x2 %(เหย้า/เสมอ/เยือน) · AH ฝั่งต่อ+เส้น · สูง/ต่ำ + avg goals · BTTS(ทั้งคู่ยิง) · ครึ่งแรก HT · HT/FT · Double Chance · สกอร์คาด
📏 วิธีอ่าน % ให้ถูกทาง (สำคัญ ห้ามอ่านสลับ): (ก) 1x2/AH → ฝั่งที่ % สูงสุด = ฝั่งที่ Forebet คาดว่าชนะ (เหย้า/เสมอ/เยือน) (ข) สูง/ต่ำ (Under/Over รอบเส้น 2.5) → %ฝั่งซ้าย(Under)มาก=โน้มต่ำ · %ฝั่งขวา(Over)มาก=โน้มสูง (ค) ทิศทางผลแพ้ชนะให้ยึด "สกอร์คาด" เป็นตัวตัดสิน (เจ้าบ้าน>เยือน=เจ้าบ้านต่อ · เท่ากัน=เสมอ)
แมพเป็นคำแนะนำ (ต้องมีตลาดรองรับ ห้ามเดาลอยๆ):
• หาผู้ชนะ = ฝ่ายเดียวเด่นชัด → 1x2 ชนะ ≥55% + AH เป็นต่อ ≥ -0.75 + สกอร์คาดไม่เสมอ (ฟันฝั่งนั้นชนะ)
• บ้านไม่แพ้ / เยือนไม่แพ้ = ฝั่งนั้นเต็ง/สูสีแต่ไม่ขาด → AH เส้นบาง (0 / -0.25 / +0.25) หรือ Double Chance 1X/X2 แรง · เลือกฝั่งที่ (%ชนะ+%เสมอ) รวมสูงกว่า
• เสมอ = %เสมอเด่น (≥33 และไล่เลี่ย/นำ) + AH เส้น 0 ราคาพอกัน + สกอร์คาดเสมอ (เช่น 1-1)
• สูงเต็ม = ตลาดสูง/ต่ำเชียร์ Over + avg goals >2.7 + BTTS ใช่ + สกอร์คาดรวมเยอะ
• ต่ำเต็ม = เชียร์ Under + avg goals <2.3 + BTTS ไม่ + สกอร์คาดน้อย (0-0 / 1-0)
• สูงแรก / ต่ำแรก = ดูตลาดครึ่งแรก (HT) — ครึ่งแรกมีลุ้นเกม / เกมฝืด
• ยิงกันทั้งคู่ (BTTS) = ตลาด "ทั้งคู่ยิง" ฝั่ง Yes ≥55% + สกอร์คาดมีประตูทั้ง 2 ฝั่ง (เช่น 1-1, 2-1) + avg goals ≥2.4 + ไม่มีฝั่งไหนต่อขาด (เส้น AH ไม่เกิน -1) — เกมสูสีต่างยิงกันได้
• ไม่ยิงกันทั้งคู่ = BTTS ฝั่ง No ≥55% + สกอร์คาดมีฝั่งยิง 0 (1-0 / 0-0 / 2-0) + avg goals ≤2.3 หรือมีฝั่งต่อขาด (เส้น ≥ -1.5 = เต็งกินรวบ)
🔴 **BTTS สดตอนพักครึ่ง/ครึ่งหลัง (กฎผู้ใช้ ใช้ได้เฉพาะคู่ที่เตะไปแล้ว):** ครึ่งแรกจบ **1-0** + เดินมาถึง **นาที 70+** + สกอร์ยังไม่เปลี่ยน + **%เสมอ ของ 1x2 สูง (≥30)** = ฝั่งที่ตามอยู่กำลังบี้ตีเสมอ → **'ยิงกันทั้งคู่' ตามได้ (ดันดาวขึ้น เพราะเหลือประตูเดียวก็จบงาน)** · ⚠️ ตรงข้าม: 1-0 นาที 70+ แต่ %เสมอ ต่ำ (<25) หรือฝั่งนำเป็นต่อขาด = เกมปิดแล้ว → 'ไม่ยิงกันทั้งคู่' แทน · สกอร์ 0-0 นาที 70+ = ห้ามฟัน BTTS ยิงกันทั้งคู่ (ต้องยิง 2 ลูกใน 20 นาที)
⭐ ดาว/ความมั่นใจ = จำนวนตลาดที่ยืนยัน "ตรงทาง" กัน (ยิ่งหลายตลาดชี้ตรงกัน ยิ่งมั่นใจ ดาวยิ่งเยอะ %ยิ่งสูง) · ตลาดขัดกันเอง/ชี้คนละทาง = ลดดาวหรือข้ามคู่นั้น
🥇 **ตัวเลือกหลักของแต่ละคู่ = ผลที่ % สูงสุดเท่าที่หาเจอ ณ ตอนนั้น** จากทุกหน้า (5 ตลาด: 1x2 / สูงต่ำ / ครึ่งแรก / HT-FT / ทั้งคู่ยิง) — หยิบ "ตัวที่โอกาสมากสุดที่มีข้อมูลจริง" มาแสดง 1 อย่างต่อคู่ · ตลาดไหนไม่มีข้อมูลตอนนั้น = ข้ามตลาดนั้น (ห้ามเดา) เอาเท่าที่หาได้ · **บังคับแนบเหตุผลทุกคู่ในบรรทัด 📌 เสมอ** ว่าทำไมเลือกตัวนี้ = ตลาดไหนหนุน + % เท่าไร + (ถ้าเป็นบอลสด) สกอร์สดกำลังตามได้ไหม
🔎 ตรวจความผิดปกติ (market monitor — เจอแล้ว "บอก" ไม่ใช่ซ่อน): ถ้าตลาดในคู่เดียวกันขัดกันเองชัดๆ ให้เติมธง ⚠️ ไว้ต้นบรรทัด 📌 พร้อมบอกสั้นๆ ว่าขัดตรงไหน แล้ว "หั่นดาวลง" (อย่าฟันเต็ม) เคสที่ต้องจับ:
   • 1x2 เชียร์ฝั่งหนึ่งชนะ ≥55% แต่ AH กลับเปิดอีกฝั่งเป็นต่อ / หรือสกอร์คาดสวนทาง 1x2 → ⚠️ ทิศทางไม่ตรง
   • สกอร์คาดโน้มต่ำ (รวม ≤1) แต่ Over% สูง/avg goals >2.7 (หรือกลับกัน สกอร์คาดยิงเยอะแต่เชียร์ Under) → ⚠️ สูงต่ำขัดสกอร์คาด
   • BTTS ว่าทั้งคู่ยิง แต่สกอร์คาดมีฝั่งยิง 0 → ⚠️ BTTS ขัดสกอร์คาด
   • ราคา AH สวน %: %ชนะสูงมาก (≥65) แต่เปิดต่อเส้นบาง/ราคาพอกัน (หรือ %สูสีแต่เปิดต่อครึ่งควบลูก) → ⚠️ ราคาไม่ล้อ%
   📉 **หักดาวเป็นตัวเลขจริง ห้ามหักลอยๆ** (นับธง ⚠️ ที่คู่นั้นติดก่อน แล้วหักตามนี้เป๊ะ):
      • ติด ⚠️ 0 อัน  = ไม่หัก (คู่นี้เท่านั้นที่มีสิทธิ์เป็น 🔥 ตัวล็อก)
      • ติด ⚠️ 1 อัน  = **หัก 0.5 ดาว** และ **ห้ามติด 🔥 ตัวล็อก** (ยังแสดงได้ ถ้าหักแล้วยังถึง 3 ดาว)
      • ติด ⚠️ ตั้งแต่ 2 อันขึ้นไป = **ตัดคู่นั้นทิ้ง ห้ามแสดง** (พยานขัดกันเองหลายปาก = ไม่ใช่ทีเด็ด)
      • หักดาวแล้วต่ำกว่า 3 ดาว = ตัดทิ้งเช่นกัน (ดาวขั้นต่ำที่ส่งได้คือ 3)
      • % ที่แสดง ให้ลดตามดาวที่ถูกหักด้วย (หัก 0.5 ดาว ≈ ลด % ลง 7-8 จุด) — ห้ามหักดาวแต่ปล่อย % ค้างสูงเหมือนเดิม
   สรุป: คู่ที่ทุกตลาดไปทางเดียว = ดาวเต็ม เชียร์ได้ · คู่ที่ติด ⚠️ 1 อัน = โชว์พร้อมเตือน + หักดาวจริง · ติด ≥2 อัน = ไม่ต้องโชว์

⚖️ วิธี "หักล้างราคา" (หัวใจ — คิดแบบขี้สงสัย ไม่ใช่ลอกตามตลาดใดตลาดหนึ่ง): มองแต่ละตลาดเป็น "พยาน" คนละปาก แล้วเอามาชนกัน — ตลาดที่หนุนทางเดียวกัน = บวกความมั่นใจ · ตลาดที่สวนกัน = หักออก (ไม่ใช่เมิน) เหลือ "ทางที่พยานส่วนใหญ่ยืนตรงกันหลังหักลบแล้ว" จึงเป็นทีเด็ด · ถ้าหักแล้วก้ำกึ่ง = ดาวต่ำ/ข้าม อย่าฝืนเชียร์
🕒 ความนิ่งข้ามรอบ (ใช้ "ประวัติทีเด็ดรอบก่อนๆ" ด้านล่าง ถ้ามี): คู่ที่ pick รอบนี้ = pick เดิม = ราคานิ่ง = **บวกความมั่นใจ** · คู่ที่ pick เด้งสวนของเดิม = ราคาแกว่ง = **เตือน/หักความมั่นใจ** (บอลใกล้เตะแต่ราคายังไม่นิ่ง = เสี่ยง)
🔥 ตัวล็อก (เตือนล่วงหน้าว่า "อันนี้แหละมั่นใจสุด"): คู่ที่เข้าครบ 3 ข้อ — (ก) ดาว ≥3.5 (ข) ไม่มีธง ⚠️ เลย (ค) 🔒 นิ่ง ≥2 รอบ — ให้ใส่ 🔥 นำหน้าเลขลำดับ แล้วดันขึ้น **บนสุดในกลุ่มของมัน (กลุ่มบอลสด หรือกลุ่มยังไม่เตะ)** · ถ้ายังไม่มีคู่ไหนครบ (เช่นรอบแรกของวัน ยังไม่มีความนิ่ง) = ไม่ต้องมี 🔥 ห้ามแปะมั่ว

❌ **วันไหนไม่มีของดี ให้บอกว่าไม่มี — ห้ามเค้นคู่มาส่งให้ครบๆ** (สำคัญมาก · การไม่แทงคือการตัดสินใจอย่างหนึ่ง):
   หลังหักดาวตามกฎ ⚠️ ข้างบนแล้ว ถ้า **ไม่เหลือคู่ไหนถึง 3 ดาว เลยสักคู่** ให้ตอบสั้นๆ แค่นี้ ห้ามแต่งคู่มาเติม:
   ⚽ ทีเด็ดบอลวันนี้
   ---------------------------
   ❌ ไม่มีคู่น่าเล่นวันนี้ — ข้อมูลยังไม่นิ่ง/ตลาดขัดกันเองเกือบทุกคู่ พักวันนี้ดีกว่า
   📌 <บอกสั้นๆ 1 บรรทัดว่าทำไม เช่น "คู่ส่วนใหญ่ราคาสวน %" หรือ "ลีกเล็กล้วน ข้อมูลบาง">
   ---------------------------
   ⚠️ คำเตือน: เรทพวกนี้ไม่รวมถึงกรณีใบแดง
   ===DATA===
   []
   (ย้ำ: จำนวนคู่ที่ส่งได้ = 0 ถึง {MAX_MATCHES} คู่ · ไม่มีขั้นต่ำ · เหลือคู่เดียวก็ส่งคู่เดียว · ไม่เหลือเลยก็ตอบแบบข้างบน)

🔴 กฎสกอร์สด "ตามได้" (ใช้กับ **คู่บอลสด/เตะไปแล้วเท่านั้น** — หัวใจของการล็อก): อ่าน 3 ค่าพร้อมกัน = (1) เวลาที่เตะไปแล้ว (2) % ของตลาด (3) สกอร์สดตอนนั้น แล้วตัดสินว่า "คำทายกำลังจะเป็นจริงไหม":
   • ✅ สกอร์สด **นำไปทางที่ Forebet ทายแล้ว** (เช่นทายเยือนชนะ→เยือนนำอยู่ · ทายต่ำ→ยังยิงกันน้อย) = "ตามได้" → **ยิ่งเตะไปเยอะ + % ยิ่งสูง = ยิ่งพลิกยาก = ดันเป็นตัวล็อก 🔥 บนสุดของกลุ่มบอลสด**
   • ❌ สกอร์สด **สวนคำทาย** (เช่นทายเสมอ แต่มีทีมนำแล้ว · ทายบ้านชนะ แต่เยือนนำ) = ตลาดหน้านั้น "ตายแล้ว" → **หั่นดาวหนักหรือตัดคู่ทิ้ง** + ติดธง ⚠️ บอกว่าสกอร์สวน (ห้ามเชียร์ต่อทั้งที่สกอร์สวน)
   • ⏳ สกอร์ยัง **ไม่ขยับ/ยังเร็ว** (เช่น 0-0 เพิ่งเริ่ม) = ยังไม่การันตี → ให้ดาวตามโมเดลปกติ อย่าเพิ่งดันเป็นตัวล็อก รอสกอร์ยืนยันก่อน
   เหตุผลว่าทำไม %แกว่ง: ที่ % บางหน้าพุ่งขึ้นเพราะเวลาเดินไปแล้วสกอร์ยังหนุนคำทาย · บางหน้า % ร่วงเพราะสกอร์สวนคำทายของหน้านั้น → เชื่อ "หน้าที่สกอร์กำลังตามได้" มากกว่าหน้าที่สกอร์สวน

รูปแบบผลลัพธ์ (ทำตามนี้เป๊ะ · หัวข้อ/เหตุผลภาษาไทย · ชื่อทีมภาษาอังกฤษ):
บรรทัดแรกสุด:  ⚽ ทีเด็ดบอลวันนี้
บรรทัดถัดไป:  ---------------------------
แล้วขึ้นหัวข้อ:  ### สรุปทีเด็ดบอลวันนี้ (เรียง ⭐ มากสุดก่อน)

จากนั้นแต่ละคู่ ใส่เลขลำดับ 1. 2. 3. (เรียง: **บอลสด/เตะไปแล้ว ไว้บนสุดก่อน → แล้วบอลวันนี้ที่ยังไม่เตะ** · ในแต่ละกลุ่ม 🔥 ตัวล็อกบนสุด → ดาวมากสุด) รูปแบบ:
N. HH:MM ทีมเหย้า VS ทีมเยือน   (คู่ที่เป็นตัวล็อกให้ขึ้นต้นเป็น 🔥 N. แทน · ใช้คำว่า "VS" คั่นชื่อทีมเสมอ ห้ามใช้คำว่า "พบ")
🎯 <คำแนะนำ: เยือนไม่แพ้ / บ้านไม่แพ้ / เสมอ / หาผู้ชนะ / สูงแรก / สูงเต็ม / ต่ำแรก / ต่ำเต็ม / ยิงกันทั้งคู่ / ไม่ยิงกันทั้งคู่> + เส้นตัวเลขเท่านั้น (เช่น 'เยือนไม่แพ้ +0.25' · 'สูงเต็ม 2.5' · BTTS ไม่ต้องมีเลข)
   🚫 บรรทัด 🎯 นี้ห้ามมีคำว่า Home/Away/HDP/ราคา/ปิดเสมอ ปนเด็ดขาด (ทำให้มั่ว) — เอาแค่คำแนะนำไทย + เลขเส้น
⚖️ ต่อ: <ชื่อทีมที่เป็นต่อ> <เส้น>  (แปลง Home=ชื่อเจ้าบ้าน · Away=ชื่อเยือน · เช่น 'ต่อ: FC Anyang -0.5') · ถ้าตารางเป็น Draw หรือเส้น 0 หรือคู่นี้ไม่มีในตาราง = เขียน "เสมอราคา (ยังไม่เปิดต่อรอง)" ห้ามแปะ Home/Away ลอยๆ
🔮 Forebet คาด H-A  (ใช้ค่า "สกอร์คาด" จากตารางราคา AH ของคู่นั้นก่อน · ถ้าคู่นั้นไม่มีในตาราง ค่อยดูจากข้อมูลดิบ · ใส่ทุกคู่ให้ผู้ใช้ดูเทียบเอง · หาไม่เจอจริงๆ ค่อยเว้น)
🔒 นิ่ง N รอบ / 🔀 เพิ่งเปลี่ยน (เดิม: <pick เดิม>)  (ดูจาก "ประวัติทีเด็ดรอบก่อนๆ" · pick รอบนี้ตรงของเดิม→🔒 นิ่ง เดิม+1 รอบ · ต่าง→🔀 เพิ่งเปลี่ยน · คู่ที่ไม่มีในประวัติ/รอบแรกของวัน = **เว้นบรรทัดนี้ไปเลย** ไม่ต้องเขียน)
⭐ X ดาว (YY%)
📌 เหตุผลสั้น 1-2 บรรทัด (ถ้าติด ⚠️ ให้ขึ้นต้น 📌 ด้วย ⚠️ + บอกว่าตลาดไหนขัด = เตือนล่วงหน้า)

⏰ เวลา HH:MM (บังคับทุกคู่ ห้ามลืมเด็ดขาด): ดึงจาก "ตารางเวลาแข่งทุกคู่" ด้านบน (จับคู่ด้วยชื่อทีม) · เป็นเวลาไทยแล้ว **เอาแต่ HH:MM ห้ามใส่วันที่** · บอลสด/เตะไปแล้ว = ใส่เวลาเตะ + สกอร์สดใต้ชื่อคู่ · หาเวลาไม่เจอจริงๆ = **ตัดคู่นั้นทิ้ง** (ห้ามส่งคู่ที่ไม่มีเวลา)
🚫 ไม่ต้องใส่ชื่อลีก ในบรรทัดหัวคู่ (ทำให้รก) — แต่ยังใส่ league ใน JSON ท้ายได้
👥 ชื่อทีม: **ใช้ภาษาอังกฤษตาม Forebet ตรงๆ ไม่ต้องแปลเป็นไทย** (อ่านง่ายกว่า จับคู่กับตารางง่ายกว่า) · 🚫 ห้ามเขียน "ทีมเยือน"/"เจ้าบ้าน"/ชื่อลอยๆ · หาชื่อครบ 2 ทีมไม่ได้ = ตัดคู่นั้นทิ้ง

**คั่นระหว่างแต่ละคู่ด้วยเส้นนี้ทุกคู่:**  ---------------------------

🔑 ฝั่งต่อ (favorite): ยึดจากคอลัมน์ "ฝั่งต่อ=Home/Away" ในตารางราคาจริง (Home=เจ้าบ้านต่อ · Away=เยือนต่อ) → เอาไปแสดงเป็น**ชื่อทีม**ในบรรทัด ⚖️ ต่อ · ถ้าคู่นั้นไม่มีในตาราง ค่อยดู 1X2 (ราคาน้อยกว่า=ต่อ) · ราคา 2 ฝั่งพอกัน/เส้น 0/Draw = บรรทัด ⚖️ เขียน "เสมอราคา (ยังไม่เปิดต่อรอง)" (ยังโชว์คู่ได้ถ้าตลาดอื่นหนุน แต่ถ้าไม่มีตลาดอื่นหนุนเลย = ข้ามคู่นั้น)
- ชื่อลีกใส่ทุกคู่ถ้ามีในข้อมูล
- **ห้ามมีข้อความเกริ่นนำ / คำอธิบายเพิ่มใดๆ** — แสดงเฉพาะหัวข้อ + รายการคู่ตามรูปแบบ · ปิดท้ายได้เฉพาะบรรทัดคำเตือนใบแดงด้านล่างเท่านั้น
⚠️ บรรทัดปิดท้าย (บังคับใส่เป็นบรรทัดสุดท้ายก่อน ===DATA=== เสมอ ทุกรอบ ห้ามลืม) — ขึ้นเส้นคั่นแล้วตามด้วยข้อความนี้เป๊ะๆ:
---------------------------
⚠️ คำเตือน: เรทพวกนี้ไม่รวมถึงกรณีใบแดง

📦 ท้ายสุด (หลังรายการทั้งหมด) ให้ขึ้นบรรทัด "===DATA===" แล้วตามด้วย JSON array ของคู่ที่แนะนำ (เฉพาะที่แสดง) สำหรับบันทึกลงชีต — 1 object ต่อ 1 คู่ ฟิลด์:
{{"date":"YYYY-MM-DD","time":"HH:MM","league":"...","home":"เจ้าบ้าน","away":"เยือน","fav":"ทีมที่เป็นต่อ","pick":"คำแนะนำ","stars":"3.5","pct":"69","id":"2419777"}}
JSON ต้องถูก syntax (double quote) · ส่วนนี้ผู้ใช้ไม่เห็น ระบบเอาไปบันทึกอย่างเดียว
🆔 ฟิลด์ "id" = เลข id ของคู่นั้นจาก "ตารางเวลาแข่งทุกคู่" ด้านบน — **คัดลอกมาตรงๆ ห้ามแต่งเลขเอง ห้ามเดา** · หาไม่เจอให้ใส่ "" (สตริงว่าง)
   (id นี้ระบบใช้เป็นคีย์ถาวรของคู่ ไว้ตามผลจริงมากรอกให้อัตโนมัติ — ใส่ผิด = ตามผลไม่เจอ)
ไม่มีคู่ไหนผ่านเกณฑ์เลย = ใส่ [] (array ว่าง) ห้ามละบรรทัด ===DATA=== ทิ้ง

{time_table}

{ah_table}

{history}

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
def main():
    print("🚀 เริ่มดึงข้อมูล Forebet + คัดคู่เด่น...")

    urls_file = "urls.txt"
    if not os.path.exists(urls_file):
        print(f"❌ ไม่พบไฟล์ {urls_file}")
        return

    with open(urls_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not urls:
        print("⚠️ ไม่พบ URL ในไฟล์ urls.txt")
        return

    combined = ""
    ok = 0
    ah_raw = ""
    time_map = {}   # slug -> (ชื่อคู่, เวลาไทย) จากทุกลิงก์
    flag_map = {}   # id -> รหัสประเทศ (จากรูปธงของ Forebet)
    odds_map = {}   # id -> เรทน้ำ (coef.) → ส่งไปเก็บในชีต ใช้คิดกำไรจริง ไม่ใช่แค่ถูก/ผิด
    for index, url in enumerate(urls, 1):
        print(f"{index}/{len(urls)} ดึง: {url}")
        raw = scrape_football_data(url)
        if raw:
            ok += 1
            label = url.rstrip("/").split("/")[-1]
            combined += f"\n\n===== ตลาด: {label} =====\n{_compact(raw)}"
            collect_times(raw, time_map)      # เก็บเวลาแข่งจากทุกหน้า
            collect_flags(raw, flag_map)      # เก็บธงชาติตามลีก
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
    print(f"🔴 บอลสด: {len(live_map)} คู่ · 🏳️ ธง: {len(flag_map)} คู่ · 💰 เรท: {len(odds_map)} คู่")

    ah_table = parse_ah_table(ah_raw)
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
    print(f"🤖 รวม {ok}/{len(urls)} ตลาด → ให้ Gemini คัดคู่เด่น 1-{MAX_MATCHES}...")
    print(f"📦 ข้อมูลรวมหลัง compact: {len(combined):,} ตัวอักษร (~{len(combined)//4:,} tokens)")
    result = analyze_with_gemini(combined[:1200000], ah_table, time_table, history)  # cap ~300K tokens รับ 20+ ตลาด (Gemini flash context 1M)

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

    print("📲 ส่งเข้า Telegram...")
    send_telegram_message(result)  # Gemini คุมหัวข้อ+รูปแบบทั้งหมดตาม prompt แล้ว
    if tips_raw:
        log_tips_to_piktax(tips_raw, odds_map)


def log_tips_to_piktax(raw, odds_map=None):
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
    nod = 0
    if odds_map:
        for t in tips:
            o = odds_map.get(str(t.get("id") or "").strip(), "")
            if o:
                t["odds"] = o
                nod += 1
    try:
        base = PIKTAX_STATE_URL.split("?")[0]
        requests.post(base, json={"fbtips": tips}, timeout=60)
        print(f"📝 ส่งบันทึก {len(tips)} คู่ลงชีตแล้ว (มีเรท {nod}/{len(tips)})")
    except Exception as e:
        print(f"⚠️ ส่งบันทึกชีตไม่ได้: {e}")

if __name__ == "__main__":
    main()

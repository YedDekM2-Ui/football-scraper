import os
import re
import json
import time
import urllib.parse
import requests
from google import genai

# ==========================================
# 1. ค่าความลับจาก Environment Variables (GitHub Secrets)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# URL ของ PIKTAX (GAS) — ใช้ทั้งเช็คสถานะเสียง (?fb=state) และดึง Forebet ทะลุ Cloudflare (?ff=)
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
# 2. เช็คสถานะเสียง (sticky · ค่าเริ่มต้น = เงียบ จนกว่าจะกดเปิด)
# ==========================================
def get_sound_on():
    if not PIKTAX_STATE_URL:
        return False
    try:
        base = PIKTAX_STATE_URL.split("?")[0]
        r = requests.get(base + "?fb=state", timeout=10)
        return r.status_code == 200 and r.text.strip() == "1"
    except Exception as e:
        print(f"⚠️ เช็คสถานะเสียงไม่ได้ (จะส่งแบบเงียบ): {e}")
        return False

# ==========================================
# 3. ส่งข้อความเข้า Telegram (ตัดยาวอัตโนมัติ + ปุ่มสลับเสียง sticky)
# ==========================================
def _toggle_button(sound_on):
    if sound_on:
        btn = {"text": "🔕 ปิดเสียงแจ้งเตือน", "callback_data": "fb:mute"}
    else:
        btn = {"text": "🔔 เปิดเสียงแจ้งเตือน", "callback_data": "fb:sound"}
    return {"inline_keyboard": [[btn]]}

def _post(text, disable_notification, use_markdown=True, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_notification": disable_notification}
    if use_markdown:
        payload["parse_mode"] = "Markdown"
    if reply_markup:
        payload["reply_markup"] = reply_markup
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
    sound_on = get_sound_on()
    silent = not sound_on
    chunks = _split_text(text)
    for i, part in enumerate(chunks):
        markup = _toggle_button(sound_on) if i == len(chunks) - 1 else None
        try:
            resp = _post(part, silent, use_markdown=True, reply_markup=markup)
            if resp.status_code != 200:
                resp = _post(part, silent, use_markdown=False, reply_markup=markup)
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
def _to_thai_time(date_str, tm_str):
    """Forebet ผ่าน Jina = เวลายุโรป (CET/CEST) → แปลงเป็นเวลาไทย · คืน HH:MM ล้วน"""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.strptime(f"{date_str} {tm_str}", "%d/%m/%Y %H:%M")
        dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris")).astimezone(ZoneInfo("Asia/Bangkok"))
        return dt.strftime("%H:%M")
    except Exception:
        # สำรอง: บวก 5 ชม. ตรงๆ (CEST+2 → ไทย+7) เผื่อ zoneinfo ไม่มี
        try:
            h, m = tm_str.split(":")
            return f"{(int(h) + 5) % 24:02d}:{m}"
        except Exception:
            return tm_str

def parse_ah_table(raw):
    if not raw:
        return ""
    lines = [l.strip() for l in raw.splitlines()]
    link_re = re.compile(r'^\[(.+?)\s+(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2})\]\(https://www\.forebet\.com/en/football/matches/([a-z0-9-]+?)-\d+\)$')
    pick_re = re.compile(r'^(Home|Away|Draw)\s+([+-]?\d+(?:\.\d+)?)\s+\d+-\d+$')
    prob_re = re.compile(r'^(\d{1,3})%$')
    rows = []
    for i, l in enumerate(lines):
        m = link_re.match(l)
        if not m:
            continue
        names, date_str, tm_raw, slug = m.groups()
        tm = _to_thai_time(date_str, tm_raw)
        prob, side, line = "", "", ""
        for j in range(i + 1, min(i + 10, len(lines))):
            pm = prob_re.match(lines[j])
            if pm and not prob:
                prob = pm.group(1)
            km = pick_re.match(lines[j])
            if km:
                side, line = km.group(1), km.group(2)
                break
        if not side:      # Forebet ยังไม่ออกเรทคู่นี้ → ข้าม (จะไม่มีเส้นให้มั่ว)
            continue
        # วันบอลนับ 10:00 → 09:59 เช้าวันถัดไป = วันเดียว → คู่ดึกข้ามเที่ยงคืน (ตี1-9) อยู่ท้ายลิสต์
        try:
            h, mm = map(int, tm.split(":"))
            order = (h * 60 + mm - 600) % 1440   # 10:00=0 ... 09:59=1439
        except Exception:
            order = 9999
        rows.append((order, f"{tm} | {names} | ฝั่งต่อ={side} เส้น={line} | เชื่อมั่น {prob}%"))
    if not rows:
        return ""
    rows.sort(key=lambda r: r[0])
    return ("===ตารางราคาแฮนดิแคปจริงจาก Forebet (แหล่งเดียวของเส้น HDP+เวลา · ใช้ตรงนี้เท่านั้น)===\n"
            "(เวลาไทยแล้ว · วันบอลนับ 10:00 ถึง 09:59 เช้าวันถัดไป = วันเดียวกัน · เรียงตามเวลาเตะจริง)\n"
            + "\n".join(r[1] for r in rows))

# ==========================================
# 4.7 ตารางเวลาแข่งกลาง — ดึงจาก "ทุกลิงก์" (เวลาไทย) ทุกทีเด็ดจะมีเวลาเสมอ
#     ไม่ว่า Gemini เลือกคู่จากตลาดไหน (แก้ปัญหาเวลาหายในคู่ที่ไม่อยู่ในตาราง AH)
# ==========================================
_LINK_RE = re.compile(r'\[(.+?)\s+(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2})\]\(https://www\.forebet\.com/en/football/matches/([a-z0-9-]+?)-\d+\)')

def collect_times(raw, tmap):
    if not raw:
        return
    for m in _LINK_RE.finditer(raw):
        names, d, t, slug = m.groups()
        if slug not in tmap:
            tmap[slug] = (names.strip(), _to_thai_time(d, t))

def fmt_time_table(tmap):
    if not tmap:
        return ""
    rows = []
    for names, t in tmap.values():
        try:
            h, mm = map(int, t.split(":"))
            order = (h * 60 + mm - 600) % 1440
        except Exception:
            order = 9999
        rows.append((order, f"{t} | {names}"))
    rows.sort(key=lambda r: r[0])
    return ("===ตารางเวลาแข่งทุกคู่ (เวลาไทยแล้ว · วันบอล 10:00→09:59 · ทุกทีเด็ดต้องมีเวลาจากตารางนี้)===\n"
            + "\n".join(r[1] for r in rows))

# ==========================================
# 5. วิเคราะห์ + คัดคู่เด่น 1-10 ด้วย Gemini (เงื่อนไข Football Live Analyst)
# ==========================================
def analyze_with_gemini(raw_text, ah_table="", time_table=""):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""คุณคือ Football Live Analyst สรุปทีเด็ดจากข้อมูล Forebet (รวมหลายตลาด: 1x2, สูง/ต่ำ, ครึ่งแรก, HT/FT, ทั้งคู่ยิง, Double Chance, Asian Handicap, TOP Predictions) ตามเงื่อนไขนี้:

⭐ สำคัญสุด: เลือกเฉพาะ "คู่เด่นที่สุด 1-{MAX_MATCHES} คู่" ของวันนั้นเท่านั้น (บางวันมีน้อยกว่า {MAX_MATCHES} ได้ ไม่ต้องฝืนให้ครบ · ต่ำกว่า 3 ดาวไม่ต้องเอา) · เรียงคู่ที่มั่นใจมากสุดไว้บนสุด

1. สถานะเกม: ถ้ามีข้อมูลสด ('เกมหยุด' / 'เลื่อน' / 'จบ') ให้แสดงสกอร์สด + เวลาปัจจุบันใต้ชื่อคู่ · ถ้าเลื่อน/หยุด ขึ้นเตือนตัวหนา: ⚠️ **[บอลเลื่อน/หยุด]**
2. รูปแบบคำแนะนำ (ใช้คำเหล่านี้เท่านั้น):
   • ผลแพ้ชนะ/แฮนดิแคป: 'เยือนไม่แพ้', 'บ้านไม่แพ้', 'เสมอ', 'หาผู้ชนะ'
   • สูง/ต่ำประตู: 'สูงแรก' (ครึ่งแรกเกินเส้น), 'สูงเต็ม' (เต็มเวลาเกินเส้น), 'ต่ำแรก' (ครึ่งแรกไม่ถึงเส้น), 'ต่ำเต็ม' (เต็มเวลาไม่ถึงเส้น) — ระบุเส้น Over/Under จากตลาด สูง/ต่ำ + ครึ่งแรก (เช่น 'สูงเต็ม 2.5')
   🚫 **ห้ามมั่วเส้น HDP เด็ดขาด** — เส้นแฮนดิแคป (เช่น -0.75, +0.25, -1.5) ต้องก๊อปตรงจาก "ตารางราคาแฮนดิแคปจริง" ด้านบนเท่านั้น (จับคู่ด้วยชื่อทีม) · ฝั่งต่อ (Home/Away) ก็ยึดตามตาราง · ถ้าคู่ไหนไม่มีในตาราง = **ใส่แค่คำแนะนำ ไม่ต้องมีเส้น** ห้ามเดา ห้ามเขียน "+0.5" ลอยๆ ห้ามเขียนคำว่า "Asian handicap"/"HDP" แทนตัวเลข
3. เกณฑ์ดาว: 4 ดาว (80-99%), 3.5 ดาว (65-79%), 3 ดาว (50-64%) · เรียง 4 ดาวไว้บนสุด
4. ประเมินข้ามทุกตลาดที่ให้มา (1x2, สูง/ต่ำ, ครึ่งแรก, HT/FT, ทั้งคู่ยิง, Double Chance, AH, Corners, Scorers ฯลฯ) — **คู่ที่หลายตลาดชี้ตรงกัน = มั่นใจสูง เรียงบน** · ตลาดขัดกันเอง/ชี้คนละทาง = ลดดาวหรือข้าม · ยึด 'บอลวันนี้' + 'TOP Predictions' เป็นหลัก
5. กระชับ อ่านบนมือถือง่าย เหมาะส่ง Telegram (ระบบมีปุ่มเปิด/ปิดเสียงให้แล้ว ไม่ต้องเขียนปุ่มเอง)

🧠 วิธีคิด (สำคัญมาก — ประเมินทีละคู่จาก "ทุกตลาด" ที่อ่านมา แล้วหักล้างจนเหลือทีเด็ด 1 อย่างที่หลายตลาดหนุนตรงกันมากสุด):
สัญญาณต่อคู่: 1x2 %(เหย้า/เสมอ/เยือน) · AH ฝั่งต่อ+เส้น · สูง/ต่ำ + avg goals · BTTS(ทั้งคู่ยิง) · ครึ่งแรก HT · HT/FT · Double Chance · สกอร์คาด
แมพเป็นคำแนะนำ (ต้องมีตลาดรองรับ ห้ามเดาลอยๆ):
• หาผู้ชนะ = ฝ่ายเดียวเด่นชัด → 1x2 ชนะ ≥55% + AH เป็นต่อ ≥ -0.75 + สกอร์คาดไม่เสมอ (ฟันฝั่งนั้นชนะ)
• บ้านไม่แพ้ / เยือนไม่แพ้ = ฝั่งนั้นเต็ง/สูสีแต่ไม่ขาด → AH เส้นบาง (0 / -0.25 / +0.25) หรือ Double Chance 1X/X2 แรง · เลือกฝั่งที่ (%ชนะ+%เสมอ) รวมสูงกว่า
• เสมอ = %เสมอเด่น (≥33 และไล่เลี่ย/นำ) + AH เส้น 0 ราคาพอกัน + สกอร์คาดเสมอ (เช่น 1-1)
• สูงเต็ม = ตลาดสูง/ต่ำเชียร์ Over + avg goals >2.7 + BTTS ใช่ + สกอร์คาดรวมเยอะ
• ต่ำเต็ม = เชียร์ Under + avg goals <2.3 + BTTS ไม่ + สกอร์คาดน้อย (0-0 / 1-0)
• สูงแรก / ต่ำแรก = ดูตลาดครึ่งแรก (HT) — ครึ่งแรกมีลุ้นเกม / เกมฝืด
⭐ ดาว/ความมั่นใจ = จำนวนตลาดที่ยืนยัน "ตรงทาง" กัน (ยิ่งหลายตลาดชี้ตรงกัน ยิ่งมั่นใจ ดาวยิ่งเยอะ %ยิ่งสูง) · ตลาดขัดกันเอง/ชี้คนละทาง = ลดดาวหรือข้ามคู่นั้น

รูปแบบผลลัพธ์ (ทำตามนี้เป๊ะ · หัวข้อ/เหตุผลภาษาไทย · ชื่อทีมภาษาอังกฤษ):
บรรทัดแรกสุด:  ⚽ ทีเด็ดบอลวันนี้
บรรทัดถัดไป:  ---------------------------
แล้วขึ้นหัวข้อ:  ### สรุปทีเด็ดบอลวันนี้ (เรียง ⭐ มากสุดก่อน)

จากนั้นแต่ละคู่ ใส่เลขลำดับ 1. 2. 3. (เรียงดาวมากสุดไว้บน) รูปแบบ:
N. HH:MM ทีมเหย้า พบ ทีมเยือน
🎯 <คำแนะนำ: เยือนไม่แพ้ / บ้านไม่แพ้ / เสมอ / หาผู้ชนะ / สูงแรก / สูงเต็ม / ต่ำแรก / ต่ำเต็ม> + เส้นจริง (HDP จากตาราง / Over-Under จากตลาดสูงต่ำ)
⭐ X ดาว (YY%)
📌 เหตุผลสั้น 1-2 บรรทัด

⏰ เวลา HH:MM (บังคับทุกคู่ ห้ามลืมเด็ดขาด): ดึงจาก "ตารางเวลาแข่งทุกคู่" ด้านบน (จับคู่ด้วยชื่อทีม) · เป็นเวลาไทยแล้ว **เอาแต่ HH:MM ห้ามใส่วันที่** · บอลสด/เตะไปแล้ว = ใส่เวลาเตะ + สกอร์สดใต้ชื่อคู่ · หาเวลาไม่เจอจริงๆ = **ตัดคู่นั้นทิ้ง** (ห้ามส่งคู่ที่ไม่มีเวลา)
🚫 ไม่ต้องใส่ชื่อลีก ในบรรทัดหัวคู่ (ทำให้รก) — แต่ยังใส่ league ใน JSON ท้ายได้
👥 ชื่อทีม: **ใช้ภาษาอังกฤษตาม Forebet ตรงๆ ไม่ต้องแปลเป็นไทย** (อ่านง่ายกว่า จับคู่กับตารางง่ายกว่า) · 🚫 ห้ามเขียน "ทีมเยือน"/"เจ้าบ้าน"/ชื่อลอยๆ · หาชื่อครบ 2 ทีมไม่ได้ = ตัดคู่นั้นทิ้ง

**คั่นระหว่างแต่ละคู่ด้วยเส้นนี้ทุกคู่:**  ---------------------------

🔑 ฝั่งต่อ (favorite): ยึดจากคอลัมน์ "ฝั่งต่อ=Home/Away" ในตารางราคาจริง (Home=เจ้าบ้านต่อ · Away=เยือนต่อ) · ถ้าคู่นั้นไม่มีในตาราง ค่อยดู 1X2 (ราคาน้อยกว่า=ต่อ) · ราคา 2 ฝั่งพอกัน/ไม่มีต่อรอง = ข้ามคู่นั้น
- ชื่อลีกใส่ทุกคู่ถ้ามีในข้อมูล
- **ห้ามมีข้อความเกริ่นนำ / สรุปปิดท้าย / คำอธิบายเพิ่มใดๆ** — แสดงเฉพาะหัวข้อ + รายการคู่ตามรูปแบบเท่านั้น

📦 ท้ายสุด (หลังรายการทั้งหมด) ให้ขึ้นบรรทัด "===DATA===" แล้วตามด้วย JSON array ของคู่ที่แนะนำ (เฉพาะที่แสดง) สำหรับบันทึกลงชีต — 1 object ต่อ 1 คู่ ฟิลด์:
{{"date":"YYYY-MM-DD","time":"HH:MM","league":"...","home":"เจ้าบ้าน","away":"เยือน","fav":"ทีมที่เป็นต่อ","pick":"คำแนะนำ","stars":"3.5","pct":"69"}}
JSON ต้องถูก syntax (double quote) · ส่วนนี้ผู้ใช้ไม่เห็น ระบบเอาไปบันทึกอย่างเดียว

{time_table}

{ah_table}

ข้อมูลดิบ (หลายตลาดรวมกัน — ใช้ประกอบเหตุผล/ดาว · แต่เวลา ยึดตารางเวลา · เส้น HDP ยึดตาราง AH เท่านั้น):
{raw_text}
"""
        last_err = None
        for model in GEMINI_MODELS:
            try:
                response = client.models.generate_content(model=model, contents=prompt)
                if response.text:
                    print(f"🤖 ใช้รุ่น {model}")
                    return response.text
            except Exception as em:
                last_err = em
                msg = str(em)
                print(f"⚠️ รุ่น {model} ใช้ไม่ได้: {msg[:120]}")
                # โควตาเต็ม (429) = ทุกรุ่นแชร์โควตาโปรเจกต์เดียวกัน → หยุดเลย ไม่ต้องรีทรายเผาโควตา
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    print("🛑 โควตา Gemini ฟรีวันนี้เต็ม (20/วัน) — ข้ามรอบนี้เงียบๆ")
                    return None
                continue
        print(f"❌ Gemini ล้มทุกรุ่น: {last_err}")
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
    for index, url in enumerate(urls, 1):
        print(f"{index}/{len(urls)} ดึง: {url}")
        raw = scrape_football_data(url)
        if raw:
            ok += 1
            label = url.rstrip("/").split("/")[-1]
            combined += f"\n\n===== ตลาด: {label} =====\n{_compact(raw)}"
            collect_times(raw, time_map)      # เก็บเวลาแข่งจากทุกหน้า
            if "asian-handicap" in url:
                ah_raw = raw   # เก็บดิบไว้ให้ parser (ก่อน compact)
        time.sleep(3)  # กันชนลิมิต

    ah_table = parse_ah_table(ah_raw)
    time_table = fmt_time_table(time_map)
    print(f"🕐 ตารางเวลาแข่ง: {len(time_map)} คู่ · 📊 ราคา AH: {max(0, len(ah_table.splitlines()) - 2) if ah_table else 0} คู่")

    if not combined.strip():
        print("⚠️ ดึงข้อมูลไม่ได้เลย")
        send_telegram_message("⚠️ วันนี้ดึงข้อมูล Forebet ไม่ได้ ลองใหม่รอบถัดไปครับ")
        return

    print(f"🤖 รวม {ok}/{len(urls)} ตลาด → ให้ Gemini คัดคู่เด่น 1-{MAX_MATCHES}...")
    print(f"📦 ข้อมูลรวมหลัง compact: {len(combined):,} ตัวอักษร (~{len(combined)//4:,} tokens)")
    result = analyze_with_gemini(combined[:1200000], ah_table, time_table)  # cap ~300K tokens รับ 20+ ตลาด (Gemini flash context 1M)

    # AI ไม่พร้อม/โควตาเต็ม → ข้ามรอบนี้เงียบๆ (ไม่สแปม error เข้า Telegram ทุก 2 ชม.)
    if not result or not result.strip():
        print("⚠️ ไม่มีผลวิเคราะห์ (AI ไม่พร้อม/โควตาเต็ม) — ข้ามรอบนี้ ไม่ส่ง Telegram")
        return

    # แยกส่วน DATA (JSON สำหรับบันทึกชีต) ออกจากข้อความที่ส่ง Telegram
    tips_raw = None
    if "===DATA===" in result:
        text_part, _, tips_raw = result.partition("===DATA===")
        result = text_part.strip()

    print("📲 ส่งเข้า Telegram...")
    send_telegram_message(result)  # Gemini คุมหัวข้อ+รูปแบบทั้งหมดตาม prompt แล้ว
    if tips_raw:
        log_tips_to_piktax(tips_raw)


def log_tips_to_piktax(raw):
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
    try:
        base = PIKTAX_STATE_URL.split("?")[0]
        requests.post(base, json={"fbtips": tips}, timeout=60)
        print(f"📝 ส่งบันทึก {len(tips)} คู่ลงชีตแล้ว")
    except Exception as e:
        print(f"⚠️ ส่งบันทึกชีตไม่ได้: {e}")

if __name__ == "__main__":
    main()

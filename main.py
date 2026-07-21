import os
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
MAX_MATCHES = 10       # คัดคู่เด่นสูงสุดกี่คู่ (แล้วแต่วัน บางวันน้อยกว่าได้)
# รุ่น Gemini (ฟรี) — ลองไล่จากบนลงล่าง · gemini-flash-latest = alias ชี้รุ่นล่าสุดเสมอ กันโดนปลดรุ่น
GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]

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
    return t[:15000]

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
# 5. วิเคราะห์ + คัดคู่เด่น 1-10 ด้วย Gemini (เงื่อนไข Football Live Analyst)
# ==========================================
def analyze_with_gemini(raw_text):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""คุณคือ Football Live Analyst สรุปทีเด็ดจากข้อมูล Forebet (รวมหลายตลาด: 1x2, สูง/ต่ำ, ครึ่งแรก, HT/FT, ทั้งคู่ยิง, Double Chance, Asian Handicap, TOP Predictions) ตามเงื่อนไขนี้:

⭐ สำคัญสุด: เลือกเฉพาะ "คู่เด่นที่สุด 1-{MAX_MATCHES} คู่" ของวันนั้นเท่านั้น (บางวันมีน้อยกว่า {MAX_MATCHES} ได้ ไม่ต้องฝืนให้ครบ · ต่ำกว่า 3 ดาวไม่ต้องเอา) · เรียงคู่ที่มั่นใจมากสุดไว้บนสุด

1. สถานะเกม: ถ้ามีข้อมูลสด ('เกมหยุด' / 'เลื่อน' / 'จบ') ให้แสดงสกอร์สด + เวลาปัจจุบันใต้ชื่อคู่ · ถ้าเลื่อน/หยุด ขึ้นเตือนตัวหนา: ⚠️ **[บอลเลื่อน/หยุด]**
2. รูปแบบคำแนะนำ (ใช้คำเหล่านี้เท่านั้น): 'เยือนไม่แพ้', 'บ้านไม่แพ้', 'เสมอ', 'หาผู้ชนะ' — พร้อมระบุ HDP/Over ให้ชัดเจน
3. เกณฑ์ดาว: 4 ดาว (80-99%), 3.5 ดาว (65-79%), 3 ดาว (50-64%) · เรียง 4 ดาวไว้บนสุด
4. ยึด 'บอลวันนี้' เป็นหลัก + เสริมด้วย 'TOP Predictions'
5. กระชับ อ่านบนมือถือง่าย เหมาะส่ง Telegram (ระบบมีปุ่มเปิด/ปิดเสียงให้แล้ว ไม่ต้องเขียนปุ่มเอง)

รูปแบบผลลัพธ์ (ทำตามนี้เป๊ะ · ภาษาไทย):
บรรทัดแรกสุด:  ⚽ ทีเด็ดบอลวันนี้
บรรทัดถัดไป:  ---------------------------
แล้วขึ้นหัวข้อ:  ### สรุปทีเด็ดบอลวันนี้ (เรียง ⭐ มากสุดก่อน)

จากนั้นแต่ละคู่ ใส่เลขลำดับ 1. 2. 3. (เรียงดาวมากสุดไว้บน) รูปแบบ:
N. HH:MM ทีมเหย้า พบ [ทีมที่เป็นต่อ] (ชื่อลีก)
🎯 <คำแนะนำ: เยือนไม่แพ้ / บ้านไม่แพ้ / เสมอ / หาผู้ชนะ> พร้อม HDP หรือ Over ถ้ามี
⭐ X ดาว (YY%)
📌 เหตุผลสั้น 1-2 บรรทัด

**คั่นระหว่างแต่ละคู่ด้วยเส้นนี้ทุกคู่:**  ---------------------------

🔑 กติกาวงเล็บ [ ] (สำคัญมาก): ครอบชื่อ "ทีมที่เป็นต่อ (ผู้ต่อรอง/favorite)" — เป็นแค่ **ข้อมูลบอกว่าใครเป็นต่อตามราคาเปิด** (ให้ผู้ใช้รู้ว่าใครเต็ง/ใครต่อรอง) · **ไม่ใช่คำสั่งให้เล่นฝั่งนั้น** — คำแนะนำเล่นจริงอยู่ที่บรรทัด 🎯 เท่านั้น
   วิธีหาฝั่งต่อ: **ดูราคา 1X2 — ฝั่งไหน "น้ำ (odds/ราคา) น้อยกว่า" = ฝั่งต่อ** (เต็งกว่า ราคาต่ำกว่า) · ถ้าราคา 2 ฝั่งใกล้เคียงกันมาก (ไม่มีต่อรอง/เสมอราคา) = **ข้ามคู่นั้นไปเลย ไม่ต้องแสดง** (ห้ามเขียนกำกับหรือหมายเหตุใดๆ)
- ต้องมี เวลาเตะ (HH:MM) และ ชื่อลีก ทุกคู่ถ้าข้อมูลมี · ไม่มีเวลาให้เว้นไว้
- **ห้ามมีข้อความเกริ่นนำ / สรุปปิดท้าย / คำอธิบายเพิ่มใดๆ** — แสดงเฉพาะหัวข้อ + รายการคู่ตามรูปแบบเท่านั้น

ข้อมูลดิบ (หลายตลาดรวมกัน):
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
                print(f"⚠️ รุ่น {model} ใช้ไม่ได้: {em}")
                continue
        print(f"❌ Gemini ล้มทุกรุ่น: {last_err}")
        return "เกิดข้อผิดพลาดในการวิเคราะห์ข้อมูลด้วย AI"
    except Exception as e:
        print(f"❌ Error ในการเรียก Gemini AI: {e}")
        return "เกิดข้อผิดพลาดในการวิเคราะห์ข้อมูลด้วย AI"

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
    for index, url in enumerate(urls, 1):
        print(f"{index}/{len(urls)} ดึง: {url}")
        raw = scrape_football_data(url)
        if raw:
            ok += 1
            label = url.rstrip("/").split("/")[-1]
            combined += f"\n\n===== ตลาด: {label} =====\n{raw}"
        time.sleep(3)  # กันชนลิมิต

    if not combined.strip():
        print("⚠️ ดึงข้อมูลไม่ได้เลย")
        send_telegram_message("⚠️ วันนี้ดึงข้อมูล Forebet ไม่ได้ ลองใหม่รอบถัดไปครับ")
        return

    print(f"🤖 รวม {ok}/{len(urls)} ตลาด → ให้ Gemini คัดคู่เด่น 1-{MAX_MATCHES}...")
    result = analyze_with_gemini(combined[:120000])  # จำกัดความยาวกันเกิน context
    print("📲 ส่งเข้า Telegram...")
    send_telegram_message(result)  # Gemini คุมหัวข้อ+รูปแบบทั้งหมดตาม prompt แล้ว

if __name__ == "__main__":
    main()

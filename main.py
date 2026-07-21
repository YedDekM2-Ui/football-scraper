import os
import requests
from bs4 import BeautifulSoup
from google import genai

# ==========================================
# 1. ดึงค่าความลับจาก Environment Variables
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
# URL ของ PIKTAX (GAS) สำหรับเช็คสถานะเสียงแจ้งเตือน — เช่น https://script.google.com/macros/s/XXX/exec?fb=state
PIKTAX_STATE_URL = os.environ.get("PIKTAX_STATE_URL", "")

if not GEMINI_API_KEY:
    raise ValueError("❌ Error: ไม่พบ GEMINI_API_KEY ใน GitHub Secrets")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("❌ Error: ไม่พบข้อมูล Telegram Bot หรือ Chat ID ใน GitHub Secrets")

TELEGRAM_LIMIT = 4000  # เผื่อจากเพดานจริง 4096

# ==========================================
# 2. เช็คสถานะเสียงแจ้งเตือน (sticky · ค่าเริ่มต้น = เงียบ)
#    '1' = เปิดเสียง · อื่นๆ/ล้มเหลว = เงียบ (ปิดจนกว่าจะกดเปิด)
# ==========================================
def get_sound_on():
    if not PIKTAX_STATE_URL:
        return False
    try:
        r = requests.get(PIKTAX_STATE_URL, timeout=10)
        return r.status_code == 200 and r.text.strip() == "1"
    except Exception as e:
        print(f"⚠️ เช็คสถานะเสียงไม่ได้ (จะส่งแบบเงียบ): {e}")
        return False

# ==========================================
# 3. ส่งข้อความเข้า Telegram (ตัดยาวอัตโนมัติ + ปุ่มสลับเสียง sticky)
# ==========================================
def _toggle_button(sound_on):
    # ถ้าตอนนี้เปิดเสียงอยู่ → ให้ปุ่ม "ปิดเสียง" · ถ้าเงียบอยู่ → ให้ปุ่ม "เปิดเสียง"
    if sound_on:
        btn = {"text": "🔕 ปิดเสียงแจ้งเตือน", "callback_data": "fb:mute"}
    else:
        btn = {"text": "🔔 เปิดเสียงแจ้งเตือน", "callback_data": "fb:sound"}
    return {"inline_keyboard": [[btn]]}

def _post(text, disable_notification, use_markdown=True, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_notification": disable_notification,
    }
    if use_markdown:
        payload["parse_mode"] = "Markdown"
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return requests.post(url, json=payload, timeout=15)

def _split_text(text, limit=TELEGRAM_LIMIT):
    """ตัดเป็นก้อน ≤ limit โดยพยายามตัดที่ขึ้นบรรทัดใหม่"""
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
    silent = not sound_on  # เงียบ = ปิดเสียง (ค่าเริ่มต้น)
    chunks = _split_text(text)
    for i, part in enumerate(chunks):
        # แนบปุ่มสลับเสียงเฉพาะก้อนสุดท้าย
        markup = _toggle_button(sound_on) if i == len(chunks) - 1 else None
        try:
            resp = _post(part, silent, use_markdown=True, reply_markup=markup)
            if resp.status_code != 200:
                # Markdown ของ AI อาจไม่สมดุล → ลองใหม่แบบ plain text
                resp = _post(part, silent, use_markdown=False, reply_markup=markup)
            if resp.status_code == 200:
                print(f"✅ ส่งสำเร็จ (เงียบ={silent})")
            else:
                print(f"❌ ส่ง Telegram ไม่ผ่าน: {resp.text}")
        except Exception as e:
            print(f"❌ เกิดข้อผิดพลาดในการส่ง Telegram: {e}")

# ==========================================
# 4. ดึงข้อมูลจากเว็บไซต์ (Scraper)
# ==========================================
def scrape_football_data(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"❌ โหลดหน้าเว็บไม่สำเร็จ (Status: {response.status_code}) สำหรับ URL: {url}")
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        content = soup.get_text(separator="\n", strip=True)
        return content[:15000]
    except Exception as e:
        print(f"❌ Error ในการดึงเว็บ {url}: {e}")
        return None

# ==========================================
# 5. วิเคราะห์ + ทายผลด้วย Gemini AI
# ==========================================
def analyze_with_gemini(raw_text):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""คุณคือนักวิเคราะห์ฟุตบอลมืออาชีพ วิเคราะห์ข้อมูลทีเด็ด/สถิติจากข้อความด้านล่างนี้ แล้ว "ทายผล" ออกมาให้ชัดเจน อ่านง่ายบนมือถือ

สำหรับแต่ละคู่ที่เจอ ให้สรุปแบบนี้:
⚽ ทีมเหย้า พบ ทีมเยือน
🎯 ทายผล: (เลือกอย่างใดอย่างหนึ่งให้ชัด เช่น เจ้าบ้านต่อ / เยือนต่อ / สูง / ต่ำ / ทั้งคู่ยิง / เสมอ) พร้อม HDP หรือเส้นสูงต่ำถ้ามี
⭐ ความมั่นใจ: ให้ดาว 3–5 ดาว (5=มั่นสุด) และ %โดยประมาณ
📌 เหตุผลสั้นๆ: 1 บรรทัด เน้นโอกาสทำประตู โดยเฉพาะครึ่งแรก

กติกา:
- เรียงคู่ที่มั่นใจมากสุด (ดาวเยอะ) ไว้บนสุด
- ถ้าข้อมูลไม่พอทายไม่ได้ ให้ข้ามคู่นั้น ไม่ต้องเดามั่ว
- กระชับ ไม่ต้องยาว เหมาะส่งเข้า Telegram

ข้อมูลดิบ:
{raw_text}
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"❌ Error ในการเรียก Gemini AI: {e}")
        return "เกิดข้อผิดพลาดในการวิเคราะห์ข้อมูลด้วย AI"

# ==========================================
# 6. การทำงานหลัก
# ==========================================
def main():
    print("🚀 เริ่มต้นกระบวนการ Scraper และทายผลบอล...")

    urls_file = "urls.txt"
    if not os.path.exists(urls_file):
        print(f"❌ ไม่พบไฟล์ {urls_file}")
        return

    with open(urls_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not urls:
        print("⚠️ ไม่พบ URL ในไฟล์ urls.txt")
        return

    all_results = ""
    for index, url in enumerate(urls, 1):
        print(f"{index}. กำลังดึงข้อมูลจาก: {url}")
        raw_data = scrape_football_data(url)
        if raw_data:
            print("🤖 กำลังให้ Gemini ทายผล...")
            analysis = analyze_with_gemini(raw_data)
            all_results += f"📊 *ผลวิเคราะห์คู่ที่ {index}*\n{analysis}\n\n-------------------\n\n"
        else:
            all_results += f"⚠️ *คู่ที่ {index}*: ไม่สามารถดึงข้อมูลจากลิงก์นี้ได้\n\n-------------------\n\n"

    if all_results:
        print("📲 กำลังส่งสรุปผลเข้า Telegram...")
        send_telegram_message(all_results)
    else:
        print("⚠️ ไม่มีข้อมูลสรุปที่จะส่ง")

if __name__ == "__main__":
    main()

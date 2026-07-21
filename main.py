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

# ตรวจสอบความพร้อมของค่าความลับ
if not GEMINI_API_KEY:
    raise ValueError("❌ Error: ไม่พบ GEMINI_API_KEY ใน GitHub Secrets")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("❌ Error: ไม่พบข้อมูล Telegram Bot หรือ Chat ID ใน GitHub Secrets")

# ==========================================
# 2. ฟังก์ชันส่งข้อความเข้า Telegram
# ==========================================
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ ส่งข้อความเข้า Telegram สำเร็จ")
        else:
            print(f"❌ ส่ง Telegram ไม่ผ่าน: {response.text}")
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการส่ง Telegram: {e}")

# ==========================================
# 3. ฟังก์ชันดึงข้อมูลจากเว็บไซต์ (Scraper)
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
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # ดึงเนื้อหาข้อความจากเว็บมาวิเคราะห์ (ปรับแก้ Selector ได้ตามโครงสร้างเว็บเป้าหมาย)
        content = soup.get_text(separator="\n", strip=True)
        # ตัดข้อความให้สั้นลงหน่อยเพื่อไม่ให้ยาวเกินไปตอนส่งให้ AI
        return content[:15000] 
    except Exception as e:
        print(f"❌ Error ในการดึงเว็บ {url}: {e}")
        return None

# ==========================================
# 4. ฟังก์ชันวิเคราะห์ด้วย Gemini AI
# ==========================================
def analyze_with_gemini(raw_text):
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"""
คุณคือนักวิเคราะห์ฟุตบอลมืออาชีพ กรุณาวิเคราะห์ข้อมูลการแข่งขันจากข้อความด้านล่างนี้ 
เน้นวิเคราะห์โอกาสทำประตู สถิติ และฟอร์มการเล่น โดยเฉพาะโอกาสทำประตูในครึ่งแรก 
สรุปผลออกมาให้กระชับ อ่านง่าย และน่าสนใจสำหรับส่งเข้า Telegram:

{raw_text}
"""
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text
    except Exception as e:
        print(f"❌ Error ในการเรียก Gemini AI: {e}")
        return "เกิดข้อผิดพลาดในการวิเคราะห์ข้อมูลด้วย AI"

# ==========================================
# 5. การทำงานหลัก (Main Execution)
# ==========================================
def main():
    print("🚀 เริ่มต้นกระบวนการ Scraper และวิเคราะห์บอล...")
    
    # อ่านรายการ URL จากไฟล์ urls.txt
    urls_file = "urls.txt"
    if not os.path.exists(urls_file):
        print(f"❌ ไม่พบไฟล์ {urls.txt}")
        return

    with open(urls_file, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not urls:
        print("⚠️ ไม่พบ URL ในไฟล์ urls.txt")
        return

    all_analysis_results = ""

    for index, url in enumerate(urls, 1):
        print(str(index) + ". กำลังดึงข้อมูลจาก: " + url)
        raw_data = scrape_football_data(url)
        
        if raw_data:
            print("🤖 กำลังให้ Gemini วิเคราะห์ข้อมูล...")
            analysis = analyze_with_gemini(raw_data)
            all_analysis_results += f"📊 **ผลวิเคราะห์คู่ที่ {index}**\n{analysis}\n\n-------------------\n\n"
        else:
            all_analysis_results += f"⚠️ **คู่ที่ {index}**: ไม่สามารถดึงข้อมูลจากลิงก์นี้ได้\n\n-------------------\n\n"

    # ส่งผลลัพธ์ทั้งหมดเข้า Telegram
    if all_analysis_results:
        print("📲 กำลังส่งสรุปผลทั้งหมดเข้า Telegram...")
        send_telegram_message(all_analysis_results)
    else:
        print("⚠️ ไม่มีข้อมูลสรุปที่จะส่ง")

if __name__ == "__main__":
    main()

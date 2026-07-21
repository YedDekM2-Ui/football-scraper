import os
import requests
from bs4 import BeautifulSoup
from google import genai

# --- ดึงค่าลับ (API Key & Telegram Token) จาก GitHub Secrets ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MASTER_PROMPT = """
คุณคือผู้เชี่ยวชาญด้านการวิเคราะห์ข้อมูลฟุตบอลและการลงทุน
หน้าที่ของคุณคือ:
1. วิเคราะห์ข้อมูลทีเด็ดฟุตบอลที่รวมค่าสถิติและเรตติ้งจากหลายๆ หมวดหมู่เข้าด้วยกัน
2. คัดเลือกคู่ที่มีความน่าจะเป็นสูงและมีความคุ้มค่าที่สุด
3. สรุปผลการวิเคราะห์ออกมาเป็นภาษาไทยที่กระชับ อ่านง่าย และตรงประเด็น
"""

def parse_forebet_table(html_content):
    matches_data = {}
    soup = BeautifulSoup(html_content, 'html.parser')
    rows = soup.find_all('div', class_='rcrow')
    
    for row in rows:
        try:
            home_team = row.find('span', class_='homeTeam').get_text(strip=True)
            away_team = row.find('span', class_='awayTeam').get_text(strip=True)
            time_elem = row.find('time')
            match_time = time_elem.get_text(strip=True) if time_elem else ""

            match_key = f"{match_time}_{home_team}_vs_{away_team}"

            probs = [p.get_text(strip=True) for p in row.find_all('span', class_='prob')]
            pred_elem = row.find('span', class_='pred')
            prediction = pred_elem.get_text(strip=True) if pred_elem else ""

            score_elem = row.find('span', class_='cor_sc')
            correct_score = score_elem.get_text(strip=True) if score_elem else ""

            avg_goals_elem = row.find('span', class_='avg_g')
            avg_goals = avg_goals_elem.get_text(strip=True) if avg_goals_elem else ""

            coef_elem = row.find('span', class_='coef')
            coef = coef_elem.get_text(strip=True) if coef_elem else ""

            match_score_elem = row.find('span', class_='match_sc')
            match_score = match_score_elem.get_text(strip=True) if match_score_elem else ""

            match_info = {
                "time": match_time,
                "home": home_team,
                "away": away_team,
                "data_pieces": f"Prob%: {probs} | Pred: {prediction} | สกอร์คาด: {correct_score} | Avg.goals: {avg_goals} | Coef: {coef} | Score: {match_score}"
            }
            matches_data[match_key] = match_info
        except Exception:
            continue

    return matches_data

def send_telegram_message(message):
    """ฟังก์ชันส่งข้อความเข้า Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ยังไม่ได้ตั้งค่า Telegram Token หรือ Chat ID ใน Secrets")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("ส่งข้อความเข้า Telegram สำเร็จ!")
        else:
            print(f"ส่ง Telegram ไม่สำเร็จ: {response.text}")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการส่ง Telegram: {e}")

def main():
    try:
        with open("urls.txt", "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("ไม่พบไฟล์ urls.txt")
        return

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    master_matches = {}

    for url in urls:
        print(f"กำลังดึงข้อมูลจาก: {url}")
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                page_data = parse_forebet_table(response.text)
                for key, val in page_data.items():
                    if key in master_matches:
                        master_matches[key]["data_pieces"] += f" | {val['data_pieces']}"
                    else:
                        master_matches[key] = val
        except Exception as e:
            print(f"Error กับลิงก์ {url}: {e}")

    formatted_text = ""
    for key, val in master_matches.items():
        formatted_text += f"เวลา: {val['time']} | {val['home']} vs {val['away']} --> [{val['data_pieces']}]\n"

    final_payload = f"{MASTER_PROMPT}\n\nข้อมูลการแข่งขัน:\n{formatted_text}"
    
    # --- ส่งข้อมูลให้ Gemini AI วิเคราะห์ ---
    analysis_result = "ไม่สามารถวิเคราะห์ได้"
    if GEMINI_API_KEY:
        try:
            print("กำลังส่งข้อมูลให้ Gemini AI วิเคราะห์...")
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=final_payload,
            )
            analysis_result = response.text
            print("AI วิเคราะห์สำเร็จ!")
        except Exception as e:
            analysis_result = f"เกิดข้อผิดพลาดในการเรียก Gemini API: {e}"
    else:
        analysis_result = "ไม่ได้ใส่ GEMINI_API_KEY ไว้ใน GitHub Secrets"

    # --- ส่งผลลัพธ์ทั้งหมดเข้า Telegram ---
    print("กำลังส่งผลลัพธ์เข้า Telegram...")
    send_telegram_message(analysis_result)

if __name__ == "__main__":
    main()

import os
import time
import requests

TELEGRAM_BOT_TOKEN = ""
GITHUB_TOKEN = ""
GITHUB_REPO = "YedDekM2-Ui/football-scraper"  # เช่น YedDekM2-Ui/football-scraper

def trigger_github_action():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    data = {"event_type": "run_scraper_command"}
    response = requests.post(url, json=data, headers=headers)
    return response.status_code == 204

def check_telegram_messages():
    offset = 0
    print("บอทกำลังสแตนด์บายรอคำสั่ง /run จากคุณ...")
    while True:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
        try:
            response = requests.get(url, timeout=35)
            data = response.json()
            if "result" in data:
                for update in data["result"]:
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "")
                    chat_id = message.get("chat", {}).get("id")

                    if text == "/run":
                        print("ได้รับคำสั่ง /run กำลังสั่ง GitHub Actions...")
                        success = trigger_github_action()
                        if success:
                            send_msg(chat_id, "🚀 รับทราบ! สั่ง GitHub Actions ให้เริ่มดึงข้อมูลและวิเคราะห์แล้ว รอรับผลทางนี้ได้เลย")
                        else:
                            send_msg(chat_id, "❌ สั่งงาน GitHub ไม่สำเร็จ เช็ก Token อีกที")
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(2)

def send_msg(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

if __name__ == "__main__":
    check_telegram_messages()

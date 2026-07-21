import datetime

def main():
    print("GitHub Actions กำลังทำงานอัตโนมัติ...")
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"เวลาปัจจุบัน: {current_time}")
    
    # ตรงนี้คุณสามารถใส่โค้ด Python สำหรับดึงข้อมูลเว็บ, 
    # วิเคราะห์บอล หรือส่งแจ้งเตือนเข้า Telegram/Line ได้ตามต้องการ

if __name__ == "__main__":
    main()

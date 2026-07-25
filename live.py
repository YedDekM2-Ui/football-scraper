import os
import json
import time
import urllib.parse
import requests

# SDK ใหม่ (google-genai) — ไม่มีก็ยังวิ่งต่อได้ด้วย REST (รองรับคีย์ยุคใหม่ AQ.* ด้วย)
try:
    from google import genai
except Exception:
    genai = None

# ===== บอลสด (Live) — ดึง forebet live-football-tips → วิเคราะห์สด → เตือนเฉพาะที่เข้าเกณฑ์ =====
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
PIKTAX_STATE_URL = os.environ.get("PIKTAX_STATE_URL", "")
JINA_PREFIX = "https://r.jina.ai/"
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")

if not GEMINI_API_KEY:
    raise ValueError("❌ ไม่พบ GEMINI_API_KEY")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("❌ ไม่พบ Telegram Bot/Chat ID")

TELEGRAM_LIMIT = 4000
GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
LIVE_MIN_PCT = 70   # แจ้งเฉพาะสัญญาณ % ≥ ค่านี้ (สัญญาณแรง)
LIVE_SOURCES = [
    "https://www.forebet.com/en/live-football-tips",                                                # สกอร์สด/นาที/สถานะ
    "https://www.forebet.com/en/football-tips-and-predictions-for-today/double-chance-predictions",  # ค่า "ไม่แพ้" (1X/X2)
    "https://www.forebet.com/en/football-tips-and-predictions-for-today/predictions-under-over-goals",# ค่า สูง/ต่ำ
]

# ---------- คีย์ Gemini หลายตัว + ยิงหลายเส้นทาง (รองรับคีย์ยุคใหม่ AQ.*) ----------
#   Google ย้ายจาก Standard key (AIza...) → Auth key (AQ....) และ AI Studio ออกคีย์ AQ ให้อัตโนมัติแล้ว
#   SDK/ไลบรารีรุ่นเก่าบางตัวตรวจว่า "ต้องขึ้นต้น AIza" → เด้งทั้งที่คีย์ถูก
#   แก้: ลอง 3 เส้นทางไล่ลงมา (SDK → REST+header → REST+?key) เฉพาะตอนที่เด้งเพราะ "ยืนยันตัวตน"
#   (โค้ดชุดนี้ก๊อปมาจาก main.py หัวข้อ 4.9/4.92 ตรงๆ — ตั้งใจให้ซ้ำ จะได้ไม่ต้องมีไฟล์กลางที่ลืมอัปแล้วพังทั้งคู่)
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
            if not _is_auth_err(msg):
                break
    hint = ""
    if str(key).startswith("AQ."):
        hint = ("\n   💡 คีย์นี้เป็นแบบใหม่ (AQ.) — ถ้าเด้งทุกเส้นทางแปลว่าตัวคีย์เองยังไม่เปิดสิทธิ์ "
                "ให้ไปเปิด Generative Language API ในโปรเจกต์ Google Cloud ของคีย์นั้น หรือออกคีย์ใหม่จาก AI Studio")
    raise RuntimeError("ยิง Gemini ไม่ผ่านสักเส้นทาง →\n   " + "\n   ".join(errors) + hint)

# ---------- สถานะเสียง (sticky · default เงียบ) ----------
def get_sound_on():
    if not PIKTAX_STATE_URL:
        return False
    try:
        base = PIKTAX_STATE_URL.split("?")[0]
        r = requests.get(base + "?fb=state", timeout=10)
        return r.status_code == 200 and r.text.strip() == "1"
    except Exception:
        return False

# ---------- ส่ง Telegram (เงียบ + ปุ่มเสียง + ตัดยาว) ----------
def _toggle_button(sound_on):
    btn = {"text": "🔕 ปิดเสียงแจ้งเตือน", "callback_data": "fb:mute"} if sound_on \
        else {"text": "🔔 เปิดเสียงแจ้งเตือน", "callback_data": "fb:sound"}
    return {"inline_keyboard": [[btn]]}

def _post(text, silent, use_markdown=True, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_notification": silent}
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
        chunks.append(text[:cut]); text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks

def send_telegram_message(text):
    sound_on = get_sound_on(); silent = not sound_on
    chunks = _split_text(text)
    for i, part in enumerate(chunks):
        markup = _toggle_button(sound_on) if i == len(chunks) - 1 else None
        try:
            resp = _post(part, silent, True, markup)
            if resp.status_code != 200:
                resp = _post(part, silent, False, markup)
            print("✅ ส่งสำเร็จ" if resp.status_code == 200 else f"❌ ส่งไม่ผ่าน: {resp.text}")
        except Exception as e:
            print(f"❌ ส่ง Telegram error: {e}")

# ---------- ดึงผ่าน PIKTAX proxy (ทะลุ Cloudflare) ----------
def _clean(t):
    if not t:
        return None
    t = t.strip()
    if not t or t.startswith(("BAD_URL", "FETCH_ERR", "HTTP_")):
        return None
    return t[:15000]

def scrape(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    if JINA_API_KEY:
        headers["Authorization"] = "Bearer " + JINA_API_KEY
    if PIKTAX_STATE_URL:
        try:
            base = PIKTAX_STATE_URL.split("?")[0]
            r = requests.get(base + "?ff=" + urllib.parse.quote(url, safe=""), headers=headers, timeout=90)
            d = _clean(r.text) if r.status_code == 200 else None
            if d:
                return d
        except Exception as e:
            print(f"⚠️ proxy error: {e}")
    try:
        r2 = requests.get(JINA_PREFIX + url, headers=headers, timeout=60)
        return _clean(r2.text) if r2.status_code == 200 else None
    except Exception as e:
        print(f"❌ scrape error: {e}")
        return None

# ---------- สมอง AI บอลสด (หลักการ Forebet live) ----------
def analyze_live(raw_text):
    prompt = f"""คุณคือ AI วิเคราะห์บอลสด (real-time) ใช้หลักการเดียวกับ Forebet Live Predictions:
- ปรับความน่าจะเป็นตาม "เวลาที่เหลือ" (เกมผ่านไปนานยังไม่มีสกอร์ → โอกาสทำประตูลดลง)
- หักลบทันทีเมื่อมีเหตุการณ์สด: ใบแดง (ลด xG ทีมนั้น), ประตู, เปลี่ยนตัว
- ประเมินแรงกดดัน/รูปเกมสดว่าใครจะพังหรือรักษาสกอร์ได้

ข้อมูลด้านล่าง = หน้า "บอลสด" ของ Forebet (มีสกอร์สด นาที และผลคาดการณ์)

แหล่งข้อมูล: หน้า live = สกอร์สด/นาที/สถานะ · ตลาด double-chance = คอลัมน์ Prob.% (1X / X2 / 12), Pred, Coef. · ตลาด over/under = ค่าสูง/ต่ำ · จับคู่ด้วยชื่อทีม

🎯 หัวใจการคัดสัญญาณ (ต้องเข้าครบทั้ง 3 ข้อถึงแจ้ง):
(ก) **Prob.% ของ double-chance (1X / X2 / 12) ต้องสูง (≥70%)**
(ข) **Pred (ที่ Forebet คาด) ต้อง "ตรงข้าม" กับผลสดตอนนี้** — ฝั่งที่ Forebet คาดว่าไม่แพ้/ชนะ กำลัง "ตามหลัง/แพ้อยู่" ในสกอร์สด = สัญญาณ (ลุ้นกลับมาไม่แพ้) · ถ้าผลสดเป็นไปตาม Pred แล้ว (เป็นไปตามคาด) = ไม่ใช่สัญญาณ ให้ข้ามทิ้ง
(ค) **Coef. ยิ่งต่ำยิ่งดี** (ราคาต่ำ = เต็งแรง มั่นใจกว่า) — เรียงคู่ที่ Coef. ต่ำไว้บนสุด

กติกา (ทำตามเป๊ะ):
1. เลือกเฉพาะแมตช์ที่ "กำลังแข่งอยู่" (มีสกอร์สด/นาที) และเข้าเกณฑ์ (ก)(ข)(ค) ครบ — ไม่ครบไม่ต้องแจ้ง
2. สถานะพิเศษ: ถ้าเจอ 'เลื่อน' / 'เกมหยุด' → หักคะแนน + ขึ้นป้ายตัวหนา  ⚠️ **[สถานะพิเศษ: บอลเลื่อน/หยุด]**
3. เรตติ้งดาว: ⭐4 (80-99%) จัดบนสุดเสมอ · ⭐3.5 (65-79%) · ⭐3 (50-64%)
4. คำแนะนำผล 1X2 ใช้ 4 คำนี้เท่านั้น: 'เยือนไม่แพ้' / 'บ้านไม่แพ้' / 'เสมอ' / 'หาผู้ชนะ' + พ่วง HDP
   ถ้ามีจังหวะ สูง/ต่ำ (Over/Under) ให้เพิ่มคำแนะนำ โดยเลือก "เส้น" จากค่านี้เท่านั้น: 1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3, 3.25 (เช่น 'สูง 2.5', 'ต่ำ 1.75')
5. [วงเล็บ] ครอบทีมที่เป็นต่อ (น้ำน้อยกว่า) — เป็นข้อมูลบอกใครต่อ ไม่ใช่คำสั่งเล่น
6. **ถ้าตอนนี้ไม่มีแมตช์สดเข้าเกณฑ์เลย ให้ตอบแค่คำเดียวว่า:  NONE**  (ห้ามมีข้อความอื่น)

รูปแบบ (เรียง % มากสุดบน · คั่นแต่ละคู่ด้วย ---------------------------) กระชับ:
⚽ ทีเด็ดบอลสด
---------------------------
N. เจ้าบ้าน  H - A  เยือน   (นาที X')
🎯 <คำแนะนำ 1X2: เยือนไม่แพ้/บ้านไม่แพ้/เสมอ/หาผู้ชนะ> · <ลุ้นสูง/ลุ้นต่ำ เส้น X ถ้ามี>
⭐ X ดาว (YY%)
📊 Forebet: <ผลเดิม>
---------------------------

ตัวอย่าง:  แมนยู 1 - 0 เชลซี (นาที 63')  →  🎯 เยือนไม่แพ้ · ลุ้นสูง 2.5
ห้ามมีเกริ่นนำ/ปิดท้าย
ข้อมูลดิบ:
{raw_text}
"""
    for key in gemini_keys():
        for model in GEMINI_MODELS:
            try:
                text, route = gemini_generate(key, model, prompt)
                print(f"🤖 ใช้รุ่น {model} · เส้นทาง {route}")
                return text
            except QuotaFull:
                print("🛑 คีย์นี้โควตาเต็ม → สลับคีย์ถัดไป")
                break
            except Exception as em:
                print(f"⚠️ รุ่น {model} ใช้ไม่ได้: {str(em)[:200]}")
    return "NONE"

def main():
    print("🚀 บอลสด: ดึงหลายแหล่ง (live + double-chance + over/under)...")
    combined = ""
    for u in LIVE_SOURCES:
        d = scrape(u)
        if d:
            label = u.rstrip("/").split("/")[-1]
            combined += f"\n\n===== {label} =====\n{d}"
        time.sleep(2)
    if not combined.strip():
        print("⚠️ ดึงข้อมูลบอลสดไม่ได้ (ไม่ส่ง)")
        return
    result = analyze_live(combined[:80000]).strip()
    if not result or result.upper().startswith("NONE") or len(result) < 40:
        print("⏸️ ตอนนี้ไม่มีบอลสดเข้าเกณฑ์ (ไม่ส่ง)")
        return
    print("📲 ส่งบอลสดเข้า Telegram...")
    send_telegram_message(result)

if __name__ == "__main__":
    main()

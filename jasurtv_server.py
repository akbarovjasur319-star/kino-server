"""
╔══════════════════════════════════════════════════════════╗
║   JasurTV — Live TV Backend Server                       ║
║   Render.com ga deploy qilinadi                          ║
╚══════════════════════════════════════════════════════════╝

Bu fayl mavjud server-1.py (Birga ko'rish) bilan BIRGA ishlaydi.
Ikkalasini ham bitta FastAPI ilovaga ulash uchun "import_into"
funksiyasidan foydalaning, yoki shu faylni mustaqil ishga
tushiring (uvicorn jasurtv_server:app).

Asosiy vazifa:
  - Bot /jasurtv/sync_schedule orqali kunlik jadvalni yuboradi
  - HTML player /jasurtv/current dan joriy ko'rsatuvni so'raydi
  - Telegram file_id URL 1 soatda eskiradi — shu sabab har bir
    so'rovda serverning o'zi getFile chaqirib YANGI url yasaydi
    (cache 50 daqiqa, undan keyin avtomatik yangilanadi)
  - Agar getFile xato bersa (masalan file_id butunlay eskirgan/
    o'chirilgan) — channel_msg_id orqali bot Storage kanaldan
    qayta forward qilib yangi file_id oladi (bot tomonidagi
    jtv_refresh_file_id funksiyasi shu uchun)

ENV o'zgaruvchilar:
    BOT_TOKEN              — Telegram bot tokeni
    WATCH_SERVER_URL        — bu serverning o'z manzili (ixtiyoriy)
"""

import os, json, time, logging
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jasurtv_server")

BOT_TOKEN = os.getenv("BOT_TOKEN", "8620168512:AAEtqbj_2lL5_eKfHTjM_BmZC4HihidStVg")

app = FastAPI(title="JasurTV Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ════════════════════════════════════════════════════════
#  XOTIRA / FAYL SAQLASH
# ════════════════════════════════════════════════════════

SCHEDULE_FILE = "jtv_schedule_cache.json"
VIEWERS_FILE  = "jtv_viewers_cache.json"

def _load(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"load {path}: {e}")
    return default

def _save(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"save {path}: {e}")

# schedule_cache[date_str] = {"active": bool, "items": [...]}
schedule_cache: dict = _load(SCHEDULE_FILE, {})

# viewers[uid] = last_ping_timestamp
viewers: dict = {}

# Video URL cache — file_id -> {"url":..., "expires": ts}
_url_cache: dict = {}
URL_CACHE_SECONDS = 50 * 60   # 50 daqiqa (Telegram URL ~1soat yashaydi)


# ════════════════════════════════════════════════════════
#  YORDAMCHI FUNKSIYALAR
# ════════════════════════════════════════════════════════

async def resolve_video_url(file_id: str) -> str | None:
    """
    Telegram file_id dan yuklab olinadigan URL yasaydi.
    Cache orqali keraksiz getFile chaqiruvlarini kamaytiradi.
    """
    if not file_id:
        return None

    cached = _url_cache.get(file_id)
    if cached and cached["expires"] > time.time():
        return cached["url"]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": file_id}
            )
            data = r.json()
            if data.get("ok"):
                path = data["result"]["file_path"]
                url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
                _url_cache[file_id] = {"url": url, "expires": time.time() + URL_CACHE_SECONDS}
                return url
            else:
                logger.warning(f"getFile xato file_id={file_id[:20]}...: {data}")
    except Exception as e:
        logger.warning(f"resolve_video_url xato: {e}")
    return None


async def refresh_file_id_from_channel(channel_msg_id: int) -> str | None:
    """
    Agar file_id butunlay eskirgan bo'lsa (getFile 400 qaytarsa),
    Storage kanaldagi xabarni forward qilib YANGI file_id olamiz.
    Bu Telegram file_id ning o'zi cheksiz amal qiladi degan
    tamoyilga asoslangan — faqat URL eskiradi, file_id emas.
    Shu funksiya orqali kanal xabaridan asl video qayta tasdiqlanadi.
    """
    channel_id = int(os.getenv("JTV_STORAGE_CHANNEL", "-1003957698317"))
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/forwardMessage",
                json={
                    "chat_id": channel_id,
                    "from_chat_id": channel_id,
                    "message_id": channel_msg_id,
                    "disable_notification": True
                }
            )
            data = r.json()
            if not data.get("ok"):
                logger.warning(f"forwardMessage xato: {data}")
                return None
            msg = data["result"]
            video = msg.get("video") or msg.get("document")
            new_fid = video.get("file_id") if video else None
            new_msg_id = msg.get("message_id")

            # Vaqtinchalik forward xabarni o'chirib tashlaymiz
            if new_msg_id:
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                        json={"chat_id": channel_id, "message_id": new_msg_id}
                    )
                except Exception:
                    pass

            return new_fid
    except Exception as e:
        logger.warning(f"refresh_file_id_from_channel xato: {e}")
        return None


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _find_current_and_next(date_str: str):
    """Bugungi jadvaldan joriy va keyingi elementni topadi."""
    day = schedule_cache.get(date_str, {})
    items = day.get("items", [])
    now = datetime.now().strftime("%H:%M")

    current, nxt = None, None
    for it in items:
        t = it.get("time", "99:99")
        if t <= now:
            current = it
        elif nxt is None:
            nxt = it
    return current, nxt, items


def _position_seconds(item: dict) -> int:
    """Joriy ko'rsatuv boshlanganidan beri o'tgan vaqt (soniya)."""
    if not item:
        return 0
    t = item.get("time", "00:00")
    try:
        h, m = map(int, t.split(":"))
    except Exception:
        return 0
    now = datetime.now()
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    delta = (now - start).total_seconds()
    return max(0, int(delta))


# ════════════════════════════════════════════════════════
#  ENDPOINTLAR
# ════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"status": "ok", "service": "JasurTV Server"}


@app.post("/jasurtv/sync_schedule")
async def sync_schedule(data: dict):
    """
    Bot tomonidan chaqiriladi — kunlik jadvalni serverga yozadi.
    Body: {"date": "2026-06-20", "items": [ {...}, {...} ]}
    """
    date_str = data.get("date")
    items    = data.get("items", [])
    if not date_str:
        raise HTTPException(400, "date kerak")

    schedule_cache[date_str] = {"active": True, "items": items}
    _save(SCHEDULE_FILE, schedule_cache)
    logger.info(f"sync_schedule: {date_str} -> {len(items)} ta item")
    return {"ok": True, "date": date_str, "count": len(items)}


@app.get("/jasurtv/current")
async def jasurtv_current():
    """
    HTML player shu yerdan joriy ko'rsatuvni so'raydi.
    Telegram URL eskirgan bo'lsa avtomatik yangilanadi:
      1) getFile bilan urinish (cache orqali tez)
      2) Agar getFile xato bersa va channel_msg_id bor bo'lsa,
         kanaldan forward qilib yangi file_id olib, qayta urinish
    """
    today = _today_str()
    current, nxt, items = _find_current_and_next(today)

    # Agar bugun hech narsa topilmasa, kechagi kun oxirigacha tekshirib ko'ramiz
    if not current and not items:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        current, nxt, items = _find_current_and_next(yesterday)

    if not current:
        return {"current": None, "next": nxt}

    file_id = current.get("file_id")
    video_url = current.get("video_url") or None

    if file_id and not video_url:
        video_url = await resolve_video_url(file_id)

        # getFile ishlamadi — kanaldan qayta tiklashga urinamiz
        if not video_url and current.get("channel_msg_id"):
            new_fid = await refresh_file_id_from_channel(current["channel_msg_id"])
            if new_fid:
                video_url = await resolve_video_url(new_fid)
                # Cache + jadvalni yangi file_id bilan yangilab qo'yamiz
                if video_url:
                    current["file_id"] = new_fid
                    _save(SCHEDULE_FILE, schedule_cache)

    result_current = dict(current)
    result_current["video_url"] = video_url
    result_current["position_seconds"] = _position_seconds(current)

    return {"current": result_current, "next": nxt}


@app.post("/jasurtv/ping")
async def jasurtv_ping(data: dict):
    """Tomoshabin onlayn ekanini bildiradi (har 30 soniyada chaqiriladi)."""
    uid = data.get("uid") or data.get("user_id")
    if uid is not None:
        viewers[str(uid)] = time.time()

    # 5 daqiqadan ko'p ping yubormaganlarni faol hisoblamaymiz
    now = time.time()
    active = sum(1 for ts in viewers.values() if now - ts < 300)

    return {"ok": True, "viewers": active}


@app.get("/jasurtv/viewers")
async def jasurtv_viewers():
    now = time.time()
    active = sum(1 for ts in viewers.values() if now - ts < 300)
    return {"viewers": active}


# ════════════════════════════════════════════════════════
#  MUSTAQIL ISHGA TUSHIRISH
# ════════════════════════════════════════════════════════
# uvicorn jasurtv_server:app --host 0.0.0.0 --port $PORT

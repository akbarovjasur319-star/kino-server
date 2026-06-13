"""
╔══════════════════════════════════════════════════════╗
║   BIRGA KO'RISH — WebSocket Server                  ║
║   Render.com ga deploy qiling (bepul)                ║
╚══════════════════════════════════════════════════════╝

O'rnatish:
    pip install fastapi uvicorn websockets httpx python-telegram-bot

Ishga tushirish:
    uvicorn server:app --host 0.0.0.0 --port 8000

Render.com uchun:
    - Start Command: uvicorn server:app --host 0.0.0.0 --port $PORT
    - Environment: BOT_TOKEN, BOT_USERNAME
"""

import os, json, asyncio, time, logging
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.getenv("BOT_TOKEN",    "8620168512:AAEtqbj_2lL5_eKfHTjM_BmZC4HihidStVg")
BOT_USERNAME = os.getenv("BOT_USERNAME", "uzbek_kino_uzb_bot").lstrip("@")

app = FastAPI(title="KinoUz Birga Ko'rish Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static fayllar (watch.html) ──
import pathlib
webapp_dir = pathlib.Path("webapp")
if not webapp_dir.exists():
    webapp_dir.mkdir(parents=True, exist_ok=True)
    # watch.html yo'q bo'lsa placeholder yaratamiz
    placeholder = webapp_dir / "watch.html"
    if not placeholder.exists():
        placeholder.write_text("<html><body>Loading...</body></html>")

app.mount("/webapp", StaticFiles(directory="webapp", html=True), name="webapp")

# ════════════════════════════════════════════════════════
#  XOTIRA: Sessiyalar va WebSocket ulanishlar
# ════════════════════════════════════════════════════════

# sessions[sid] = {
#   "host_id": int,
#   "guest_id": int | None,
#   "movie": {id, title, file_id, poster},
#   "status": "waiting" | "ready" | "playing" | "ended",
#   "created": float,
#   "play_state": {"playing": bool, "time": float, "updated": float}
# }
sessions: Dict[str, dict] = {}

# ws_connections[sid][user_id] = WebSocket
ws_connections: Dict[str, Dict[int, WebSocket]] = {}


# ════════════════════════════════════════════════════════
#  REST API — Bot tomonidan chaqiriladi
# ════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {"status": "ok", "service": "KinoUz Birga Ko'rish"}

@app.post("/session/create")
async def create_session(data: dict):
    """Bot yangi sessiya yaratadi."""
    sid      = data["sid"]
    host_id  = int(data["host_id"])
    movie    = data["movie"]          # {id, title, file_id, poster}

    sessions[sid] = {
        "host_id":    host_id,
        "guest_id":   None,
        "movie":      movie,
        "status":     "waiting",
        "created":    time.time(),
        "play_state": {"playing": False, "time": 0.0, "updated": time.time()}
    }
    ws_connections[sid] = {}
    logger.info(f"Session created: {sid} by {host_id}")
    return {"ok": True, "sid": sid}

@app.get("/session/{sid}")
async def get_session(sid: str):
    """Sessiya ma'lumotlarini qaytaradi."""
    if sid not in sessions:
        raise HTTPException(404, "Sessiya topilmadi")
    s = sessions[sid]
    return {
        "sid":      sid,
        "status":   s["status"],
        "movie":    s["movie"],
        "host_id":  s["host_id"],
        "guest_id": s["guest_id"],
        "members":  len(ws_connections.get(sid, {}))
    }

# In-memory storage for movies and users (bot sends data here)
_movies_cache: dict = {}
_users_cache: dict = {}

@app.post("/sync/movies")
async def sync_movies(data: dict):
    """Bot movies.json ni servega yuboradi."""
    global _movies_cache
    _movies_cache = data.get("movies", {})
    return {"ok": True, "count": len(_movies_cache)}

@app.post("/sync/users")
async def sync_users(data: dict):
    """Bot users.json ni servega yuboradi."""
    global _users_cache
    _users_cache = data.get("users", {})
    return {"ok": True, "count": len(_users_cache)}

@app.get("/movies")
async def get_movies():
    """Kinolar ro'yxatini qaytaradi."""
    result = []
    for mid, m in _movies_cache.items():
        result.append({
            "id":      mid,
            "title":   m.get("title", ""),
            "year":    m.get("year", ""),
            "genre":   m.get("genre", ""),
            "rating":  m.get("rating", 0),
            "poster":  m.get("poster", ""),
            "file_id": m.get("file_id", ""),
        })
    return {"movies": sorted(result, key=lambda x: x["rating"], reverse=True)}

@app.get("/users")
async def get_users():
    """Foydalanuvchilar ro'yxatini qaytaradi."""
    result = []
    for uid, u in _users_cache.items():
        if u.get("username"):
            result.append({
                "id":         int(uid),
                "first_name": u.get("first_name") or u.get("name", ""),
                "username":   u.get("username", ""),
            })
    return {"users": result}

@app.post("/invite")
async def send_invite(data: dict):
    """
    Bot orqali 2-userga taklif xabari yuboradi.
    data: {sid, guest_id, host_name, movie_title, webapp_url}
    """
    sid        = data["sid"]
    guest_id   = int(data["guest_id"])
    host_name  = data["host_name"]
    movie_title = data["movie_title"]
    webapp_url = data.get("webapp_url", "")

    if sid not in sessions:
        raise HTTPException(404, "Sessiya topilmadi")

    sessions[sid]["guest_id"] = guest_id

    # Bot orqali xabar yuborish
    text = (
        f"🎬 <b>BIRGA KO'RISH TAKLIFI!</b>\n\n"
        f"╔══════════════════╗\n"
        f"║  🎬 BIRGA TOMOSHA ║\n"
        f"╚══════════════════╝\n\n"
        f"🎬 Kino: <b>{movie_title}</b>\n"
        f"👤 Taklif qiluvchi: <b>{host_name}</b>\n\n"
        f"👇 Qo'shilish uchun tugmani bosing:"
    )

    kb = {
        "inline_keyboard": [[
            {"text": "✅ Qabul qilaman", "callback_data": f"wt_webapp_join_{sid}"},
            {"text": "❌ Rad etaman",    "callback_data": f"wt_webapp_reject_{sid}"}
        ]]
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id":      guest_id,
                "text":         text,
                "parse_mode":   "HTML",
                "reply_markup": kb
            }
        )
    result = r.json()
    if not result.get("ok"):
        raise HTTPException(400, f"Telegram xatosi: {result}")

    return {"ok": True}


# ════════════════════════════════════════════════════════
#  WEBSOCKET — Sinxronizatsiya
# ════════════════════════════════════════════════════════

@app.websocket("/ws/{sid}/{user_id}")
async def websocket_endpoint(ws: WebSocket, sid: str, user_id: int):
    await ws.accept()

    if sid not in sessions:
        await ws.send_json({"type": "error", "msg": "Sessiya topilmadi"})
        await ws.close()
        return

    session = sessions[sid]

    # Ulanish ro'yxatga qo'shish
    if sid not in ws_connections:
        ws_connections[sid] = {}
    ws_connections[sid][user_id] = ws

    # Kim ekanligini aniqlash
    role = "host" if user_id == session["host_id"] else "guest"
    logger.info(f"WS connect: sid={sid}, uid={user_id}, role={role}")

    # Joriy holat yuborish
    await ws.send_json({
        "type":       "init",
        "role":       role,
        "movie":      session["movie"],
        "play_state": session["play_state"],
        "members":    len(ws_connections[sid]),
        "status":     session["status"]
    })

    # Boshqa userga "ulandi" xabari
    await _broadcast(sid, user_id, {
        "type":    "user_joined",
        "user_id": user_id,
        "role":    role,
        "members": len(ws_connections[sid])
    })

    # Ikkalasi ulansa — ready
    if len(ws_connections[sid]) >= 2 and session["status"] == "waiting":
        session["status"] = "ready"
        await _broadcast_all(sid, {"type": "both_ready", "msg": "Ikkalangiz ulandingiz! Boshlashingiz mumkin."})

    try:
        async for raw in ws.iter_text():
            msg = json.loads(raw)
            await _handle_ws_message(sid, user_id, msg, session)
    except WebSocketDisconnect:
        pass
    finally:
        ws_connections[sid].pop(user_id, None)
        await _broadcast(sid, user_id, {
            "type":    "user_left",
            "user_id": user_id,
            "members": len(ws_connections.get(sid, {}))
        })
        logger.info(f"WS disconnect: sid={sid}, uid={user_id}")


async def _handle_ws_message(sid: str, user_id: int, msg: dict, session: dict):
    """WebSocket xabarlarini qayta ishlash."""
    t = msg.get("type")

    if t == "play":
        # Foydalanuvchi bosdi — hammaga yuboriladi
        session["play_state"] = {
            "playing": True,
            "time":    msg.get("time", 0),
            "updated": time.time()
        }
        await _broadcast(sid, user_id, {
            "type": "play",
            "time": msg.get("time", 0),
            "by":   user_id
        })

    elif t == "pause":
        session["play_state"] = {
            "playing": False,
            "time":    msg.get("time", 0),
            "updated": time.time()
        }
        await _broadcast(sid, user_id, {
            "type": "pause",
            "time": msg.get("time", 0),
            "by":   user_id
        })

    elif t == "seek":
        session["play_state"]["time"]    = msg.get("time", 0)
        session["play_state"]["updated"] = time.time()
        await _broadcast(sid, user_id, {
            "type": "seek",
            "time": msg.get("time", 0),
            "by":   user_id
        })

    elif t == "reaction":
        await _broadcast_all(sid, {
            "type":     "reaction",
            "emoji":    msg.get("emoji", "❤️"),
            "user_id":  user_id
        })

    elif t == "chat":
        await _broadcast_all(sid, {
            "type":    "chat",
            "text":    msg.get("text", "")[:200],
            "user_id": user_id
        })

    elif t == "end":
        session["status"] = "ended"
        await _broadcast_all(sid, {"type": "session_ended"})


async def _broadcast(sid: str, sender_id: int, msg: dict):
    """Jo'natuvchidan boshqa hammaga yuborish."""
    conns = ws_connections.get(sid, {})
    for uid, ws in list(conns.items()):
        if uid != sender_id:
            try:
                await ws.send_json(msg)
            except:
                pass

async def _broadcast_all(sid: str, msg: dict):
    """Barcha ulanganlarга yuborish."""
    conns = ws_connections.get(sid, {})
    for ws in list(conns.values()):
        try:
            await ws.send_json(msg)
        except:
            pass



# ╔══════════════════════════════════════════════════════════════╗
# ║                  📺 JASUR TV — SERVER API                   ║
# ╚══════════════════════════════════════════════════════════════╝

import boto3
from botocore.client import Config
from datetime import datetime, timedelta

# B2 kalitlar
B2_KEY_ID     = os.getenv("B2_KEY_ID", "")
B2_APP_KEY    = os.getenv("B2_APP_KEY", "")
B2_BUCKET     = os.getenv("B2_BUCKET_NAME", "jasurtv-videos")
B2_ENDPOINT   = os.getenv("B2_ENDPOINT", "s3.us-east-005.backblazeb2.com")

def get_b2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{B2_ENDPOINT}",
        aws_access_key_id=B2_KEY_ID,
        aws_secret_access_key=B2_APP_KEY,
        config=Config(signature_version="s3v4")
    )

# JasurTV ma'lumotlar
tv_schedule: dict = {}        # {"2026-06-20": {"items": [...], "active": True}}
tv_viewers:  dict = {}        # {user_id: last_ping_time}
tv_ws_conns: set  = set()     # WebSocket ulanishlar

# ── B2 UPLOAD ──
@app.post("/tv/upload-url")
async def get_upload_url(data: dict):
    """Bot video yuklash uchun presigned URL beradi."""
    filename = data.get("filename", f"video_{int(time.time())}.mp4")
    try:
        s3 = get_b2_client()
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": B2_BUCKET, "Key": filename, "ContentType": "video/mp4"},
            ExpiresIn=3600
        )
        video_url = f"https://{B2_BUCKET}.{B2_ENDPOINT}/{filename}"
        return {"ok": True, "upload_url": url, "video_url": video_url, "filename": filename}
    except Exception as e:
        raise HTTPException(500, f"B2 xatosi: {e}")

# ── JADVAL ──
@app.post("/tv/schedule")
async def save_schedule(data: dict):
    """Bot jadval yuboradi."""
    global tv_schedule
    date  = data.get("date")
    items = data.get("items", [])
    if not date:
        raise HTTPException(400, "date kerak")
    tv_schedule[date] = {"items": items, "active": True}
    return {"ok": True, "date": date, "count": len(items)}

@app.get("/tv/schedule/{date}")
async def get_schedule(date: str):
    return tv_schedule.get(date, {"items": [], "active": False})

@app.get("/tv/schedule")
async def get_all_schedules():
    return {"schedules": list(tv_schedule.keys())}

@app.delete("/tv/schedule/{date}")
async def delete_schedule(date: str):
    tv_schedule.pop(date, None)
    return {"ok": True}

# ── HOZIRGI EFIR ──
@app.get("/tv/current")
async def get_current():
    """Hozir qaysi kino/ko'rsatuv ijro etilayotganini hisoblaydi."""
    today = datetime.now().strftime("%Y-%m-%d")
    sched = tv_schedule.get(today, {})
    
    if not sched.get("active"):
        # Boshqa sanalarni ham tekshirish (faol jadval)
        for date, s in sorted(tv_schedule.items()):
            if s.get("active") and date >= today:
                sched = s
                break
    
    items = sched.get("items", [])
    if not items:
        return {"current": None, "schedule": [], "offset_seconds": 0, "viewers": active_viewers()}

    now     = datetime.now()
    now_min = now.hour * 60 + now.minute + now.second / 60

    current    = None
    offset_sec = 0

    for i, item in enumerate(items):
        t = item.get("time", "00:00")
        h, m = map(int, t.split(":"))
        item_min = h * 60 + m
        duration = item.get("duration_min", 90)

        if item_min <= now_min < item_min + duration:
            current    = item
            offset_sec = int((now_min - item_min) * 60)
            break
        elif item_min > now_min:
            # Hali boshlanmagan — oxirgi tugagan item
            if i > 0:
                prev = items[i-1]
                ph, pm = map(int, prev.get("time","00:00").split(":"))
                prev_min = ph * 60 + pm
                pdur     = prev.get("duration_min", 90)
                if prev_min + pdur > now_min:
                    current    = prev
                    offset_sec = int((now_min - prev_min) * 60)
            break

    return {
        "current":        current,
        "schedule":       items,
        "offset_seconds": offset_sec,
        "viewers":        active_viewers()
    }

# ── KO'RUVCHILAR ──
@app.post("/tv/ping")
async def tv_ping(data: dict):
    uid = str(data.get("user_id", "anon"))
    tv_viewers[uid] = time.time()
    return {"ok": True, "viewers": active_viewers()}

def active_viewers() -> int:
    now = time.time()
    return sum(1 for t in tv_viewers.values() if now - t < 30)

# ── WEBSOCKET — TV ──
@app.websocket("/tv/ws/{user_id}")
async def tv_websocket(ws: WebSocket, user_id: int):
    await ws.accept()
    tv_ws_conns.add(ws)
    tv_viewers[str(user_id)] = time.time()

    try:
        while True:
            await asyncio.sleep(10)
            tv_viewers[str(user_id)] = time.time()
            count = active_viewers()
            try:
                await ws.send_json({"type": "viewers", "count": count})
            except:
                break
            # Barcha ko'ruvchilarga yuborish
            for conn in list(tv_ws_conns):
                if conn != ws:
                    try:
                        await conn.send_json({"type": "viewers", "count": count})
                    except:
                        tv_ws_conns.discard(conn)
    except WebSocketDisconnect:
        pass
    finally:
        tv_ws_conns.discard(ws)
        tv_viewers.pop(str(user_id), None)

async def tv_broadcast(msg: dict):
    """Barcha TV ko'ruvchilarga xabar yuborish."""
    for conn in list(tv_ws_conns):
        try:
            await conn.send_json(msg)
        except:
            tv_ws_conns.discard(conn)



# ════════════════════════════════════════════════════════
#  TOZALASH — 2 soatdan eski sessiyalarni o'chirish
# ════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    asyncio.create_task(_cleanup_loop())

async def _cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        expired = [sid for sid, s in sessions.items() if now - s["created"] > 7200]
        for sid in expired:
            sessions.pop(sid, None)
            ws_connections.pop(sid, None)
        if expired:
            logger.info(f"Cleaned {len(expired)} expired sessions")


# ╔══════════════════════════════════════════════════════════════╗
# ║                  📺 JASURTV API                             ║
# ╚══════════════════════════════════════════════════════════════╝

import boto3
from botocore.config import Config
from datetime import datetime, timedelta
import uuid

B2_KEY_ID     = os.getenv("B2_KEY_ID",     "")
B2_APP_KEY    = os.getenv("B2_APP_KEY",    "")
B2_BUCKET     = os.getenv("B2_BUCKET_NAME","jasurtv-videos")
B2_ENDPOINT   = os.getenv("B2_ENDPOINT",   "s3.us-east-005.backblazeb2.com")

JASURTV_SCHEDULE_FILE = "jasurtv_schedule.json"
JASURTV_VIEWERS_FILE  = "jasurtv_viewers.json"
JASURTV_CONFIG_FILE   = "jasurtv_config.json"

VIP_PRICES = {"1": "29,900 so'm", "3": "79,900 so'm", "12": "249,900 so'm"}

# ── Storage ──
_schedule_cache: dict = {}
_viewers: dict = {}
_vip_cache: dict = {}

def get_b2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{B2_ENDPOINT}",
        aws_access_key_id=B2_KEY_ID,
        aws_secret_access_key=B2_APP_KEY,
        config=Config(signature_version="s3v4")
    )

# ── SYNC: Bot dan jadval qabul qiladi ──
@app.post("/jasurtv/sync_schedule")
async def sync_schedule(data: dict):
    global _schedule_cache
    date  = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    items = data.get("items", [])
    _schedule_cache[date] = items
    # Faylga ham saqlash
    try:
        if os.path.exists(JASURTV_SCHEDULE_FILE):
            with open(JASURTV_SCHEDULE_FILE,"r",encoding="utf-8") as f:
                all_schedules = json.load(f)
        else:
            all_schedules = {}
        all_schedules[date] = items
        with open(JASURTV_SCHEDULE_FILE,"w",encoding="utf-8") as f:
            json.dump(all_schedules, f, ensure_ascii=False, indent=2)
    except: pass
    return {"ok": True, "date": date, "count": len(items)}

# ── Jadval olish ──
@app.get("/jasurtv/schedule")
async def get_schedule(date: str = ""):
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    # Cache dan
    if date in _schedule_cache:
        return {"date": date, "items": _schedule_cache[date]}
    # Fayldan
    try:
        if os.path.exists(JASURTV_SCHEDULE_FILE):
            with open(JASURTV_SCHEDULE_FILE,"r",encoding="utf-8") as f:
                all_s = json.load(f)
            items = all_s.get(date, [])
            _schedule_cache[date] = items
            return {"date": date, "items": items}
    except: pass
    return {"date": date, "items": []}

# ── VIP tekshirish ──
@app.get("/jasurtv/check_vip")
async def check_vip(user_id: int = 0):
    if not user_id:
        return {"is_vip": False}
    uid = str(user_id)
    if uid in _vip_cache:
        vdata = _vip_cache[uid]
        try:
            exp = datetime.strptime(vdata.get("expire","2000-01-01"),"%Y-%m-%d")
            return {"is_vip": exp > datetime.now()}
        except:
            return {"is_vip": False}
    return {"is_vip": False}

# ── Bot VIP sync ──
@app.post("/jasurtv/sync_vip")
async def sync_vip(data: dict):
    global _vip_cache
    _vip_cache = data.get("vip", {})
    return {"ok": True, "count": len(_vip_cache)}

# ── VIP narxlar ──
@app.get("/jasurtv/vip_prices")
async def vip_prices():
    return VIP_PRICES

# ── B2 signed URL ──
@app.get("/jasurtv/get_url")
async def get_signed_url(file: str = ""):
    if not file or not B2_KEY_ID:
        raise HTTPException(400, "File or credentials missing")
    try:
        s3 = get_b2_client()
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": B2_BUCKET, "Key": file},
            ExpiresIn=7200  # 2 soat
        )
        return {"url": url}
    except Exception as e:
        raise HTTPException(500, str(e))

# ── B2 upload URL (bot uchun) ──
@app.post("/jasurtv/upload_url")
async def get_upload_url(data: dict):
    filename   = data.get("filename", f"{uuid.uuid4()}.mp4")
    content_type = data.get("content_type", "video/mp4")
    if not B2_KEY_ID:
        raise HTTPException(400, "B2 credentials missing")
    try:
        s3 = get_b2_client()
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": B2_BUCKET, "Key": filename, "ContentType": content_type},
            ExpiresIn=3600
        )
        file_url = f"https://{B2_ENDPOINT}/{B2_BUCKET}/{filename}"
        return {"upload_url": url, "file_key": filename, "file_url": file_url}
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Viewers ──
_active_viewers: dict = {}  # {user_id: last_seen}

@app.post("/jasurtv/viewer")
async def register_viewer(data: dict):
    uid = str(data.get("user_id", "anon"))
    _active_viewers[uid] = time.time()
    return {"ok": True}

@app.get("/jasurtv/viewers")
async def get_viewers():
    # 5 daqiqadan eski viewerlarni o'chirish
    now = time.time()
    active = {k: v for k, v in _active_viewers.items() if now - v < 300}
    _active_viewers.clear()
    _active_viewers.update(active)
    return {"count": len(active)}


# ── 24/7 LIVE PLAYER UCHUN ──

def _jtv_load_schedule():
    try:
        if os.path.exists(JASURTV_SCHEDULE_FILE):
            with open(JASURTV_SCHEDULE_FILE,"r",encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return {}

def _jtv_now():
    """Tashkent vaqti (UTC+5) bo'yicha hozirgi vaqt."""
    return datetime.utcnow() + timedelta(hours=5)

def _jtv_today_items():
    today = _jtv_now().strftime("%Y-%m-%d")
    sched = _jtv_load_schedule()
    day   = sched.get(today, [])
    if isinstance(day, dict):
        return day.get("items", [])
    if isinstance(day, list):
        return day
    return []

def _jtv_current_and_next():
    now_str = _jtv_now().strftime("%H:%M")
    items   = _jtv_today_items()
    current, nxt = None, None
    for it in items:
        t = it.get("time","99:99")
        if t <= now_str:
            current = it
        elif nxt is None:
            nxt = it
    return current, nxt

def _jtv_position_seconds(item):
    if not item: return 0
    now = _jtv_now()
    t   = item.get("time","00:00")
    try:
        h,m = map(int, t.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return max(0, int((now-start).total_seconds()))
    except:
        return 0

def _jtv_video_url(item):
    """Item uchun video URL. video_url > B2 > Telegram file_id."""
    if not item:
        return None
    if item.get("video_url"):
        return item["video_url"]

    b2_filename = item.get("b2_filename")
    if b2_filename:
        try:
            s3 = boto3.client(
                's3',
                endpoint_url=f"https://{B2_ENDPOINT}",
                aws_access_key_id=B2_KEY_ID,
                aws_secret_access_key=B2_APP_KEY,
                config=Config(signature_version='s3v4')
            )
            return s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': B2_BUCKET, 'Key': b2_filename},
                ExpiresIn=21600  # 6 soat
            )
        except Exception as e:
            logging.warning(f"B2 presigned url error: {e}")

    # Telegram file_id orqali (vaqtinchalik, ~1 soat amal qiladi)
    file_id = item.get("file_id")
    if file_id:
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                    params={"file_id": file_id}
                )
                data = r.json()
                if data.get("ok"):
                    file_path = data["result"]["file_path"]
                    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        except Exception as e:
            logging.warning(f"Telegram getFile error: {e}")

    return None


@app.get("/jasurtv/current")
async def jasurtv_current():
    """
    24/7 Live Player uchun: hozir nima ijro etilayotgani
    va qaysi sekunddan boshlash kerakligini qaytaradi.
    """
    current, nxt = _jtv_current_and_next()

    result = {"current": None, "next": None}

    if current:
        result["current"] = {
            "id":               current.get("id"),
            "type":             current.get("type","movie"),
            "title":            current.get("title",""),
            "time":             current.get("time",""),
            "video_url":        _jtv_video_url(current),
            "position_seconds": _jtv_position_seconds(current),
        }

    if nxt:
        result["next"] = {
            "id":    nxt.get("id"),
            "type":  nxt.get("type","movie"),
            "title": nxt.get("title",""),
            "time":  nxt.get("time",""),
        }

    return result


@app.post("/jasurtv/ping")
async def jasurtv_ping(data: dict):
    """User JasurTV ko'rayotganini bildiradi (viewer count uchun)."""
    uid = str(data.get("uid", "0"))
    _active_viewers[uid] = time.time()

    now = time.time()
    active = {k: v for k, v in _active_viewers.items() if now - v < 300}
    _active_viewers.clear()
    _active_viewers.update(active)

    return {"ok": True, "viewers": len(active)}


# ── Admin: jadvallar ro'yxati ──
@app.get("/jasurtv/schedules")
async def list_schedules():
    try:
        if os.path.exists(JASURTV_SCHEDULE_FILE):
            with open(JASURTV_SCHEDULE_FILE,"r",encoding="utf-8") as f:
                all_s = json.load(f)
            return {"dates": sorted(all_s.keys()), "schedules": all_s}
    except: pass
    return {"dates": [], "schedules": {}}


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

# Fayl yo'llari — Render restartda ham saqlanadi
MOVIES_FILE = "data_movies.json"
USERS_FILE  = "data_users.json"

def _load_file(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}

def _save_file(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Fayl saqlash xato: {e}")

# Ishga tushganda fayldan o'qib oladi
_movies_cache: dict = _load_file(MOVIES_FILE)
_users_cache: dict  = _load_file(USERS_FILE)

@app.post("/sync/movies")
async def sync_movies(data: dict):
    """Bot movies.json ni servega yuboradi."""
    global _movies_cache
    _movies_cache = data.get("movies", {})
    _save_file(MOVIES_FILE, _movies_cache)
    return {"ok": True, "count": len(_movies_cache)}

@app.post("/sync/users")
async def sync_users(data: dict):
    """Bot users.json ni servega yuboradi."""
    global _users_cache
    _users_cache = data.get("users", {})
    _save_file(USERS_FILE, _users_cache)
    return {"ok": True, "count": len(_users_cache)}

@app.get("/video/{file_id}")
async def get_video_url(file_id: str):
    """Telegram file_id dan video URL oladi."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": file_id},
                timeout=10
            )
            data = r.json()
            if data.get("ok"):
                path = data["result"]["file_path"]
                url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
                return {"url": url}
    except Exception as e:
        logger.warning(f"Video URL xato: {e}")
    raise HTTPException(404, "Video topilmadi")

@app.get("/movies")
async def get_movies():
    """Kinolar ro'yxatini qaytaradi."""
    result = []
    for mid, m in _movies_cache.items():
        result.append({
            "id":        mid,
            "title":     m.get("title", ""),
            "year":      m.get("year", ""),
            "genre":     m.get("genre", ""),
            "rating":    m.get("rating", 0),
            "poster":    m.get("poster", ""),
            "file_id":   m.get("file_id", ""),
            "video_url": m.get("video_url", ""),
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

@app.get("/video/{file_id:path}")
async def get_video_url(file_id: str):
    """file_id dan video URL oladi (Telegram API orqali)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": file_id}
            )
            data = r.json()
            if data.get("ok"):
                path = data["result"]["file_path"]
                url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
                return {"ok": True, "url": url}
    except Exception as e:
        logger.warning(f"getFile xato: {e}")
    raise HTTPException(404, "Video topilmadi")


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

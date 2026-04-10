from fastapi import FastAPI, Request, Response, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import json, os, uuid

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

USERS_FILE = "users.json"
SESSIONS: dict[str, str] = {}

def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users: dict):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/register")
async def register(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    confirm = data.get("confirm", "").strip()

    if not username or not password or not confirm:
        return JSONResponse({"ok": False, "error": "Заполни все поля"})
    if len(username) < 3:
        return JSONResponse({"ok": False, "error": "Логин минимум 3 символа"})
    if len(password) < 4:
        return JSONResponse({"ok": False, "error": "Пароль минимум 4 символа"})
    if password != confirm:
        return JSONResponse({"ok": False, "error": "Пароли не совпадают"})

    users = load_users()
    if username in users:
        return JSONResponse({"ok": False, "error": "Этот логин уже занят"})

    users[username] = password
    save_users(users)

    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = username
    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    return response


@app.post("/api/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return JSONResponse({"ok": False, "error": "Заполни все поля"})

    users = load_users()
    if username not in users:
        return JSONResponse({"ok": False, "error": "Пользователь не найден"})
    if users[username] != password:
        return JSONResponse({"ok": False, "error": "Неверный пароль"})

    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = username
    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    return response


@app.post("/api/logout")
async def logout(response: Response, session_id: str = Cookie(default=None)):
    if session_id and session_id in SESSIONS:
        del SESSIONS[session_id]
    response = JSONResponse({"ok": True})
    response.delete_cookie("session_id")
    return response


@app.get("/api/me")
async def me(session_id: str = Cookie(default=None)):
    if session_id and session_id in SESSIONS:
        return JSONResponse({"ok": True, "username": SESSIONS[session_id]})
    return JSONResponse({"ok": False})
from future import annotations

import glob
import random
import uuid

from fastapi import Cookie, Depends, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import Session as DBSession, User

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@app.on_event("startup")
def on_startup():
    init_db()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def get_all_memes() -> list[str]:
    patterns = ["static/*.png", "static/*.jpg", "static/*.jpeg", "static/*.gif", "static/*.webp"]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    return [f.replace("\\", "/") for f in files]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/meme")
async def get_meme(
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not session_id:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    row = db.query(DBSession).filter(DBSession.session_id == session_id).first()
    if not row:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    memes = get_all_memes()
    if not memes:
        return JSONResponse({"ok": False, "error": "no memes found"}, status_code=404)
    return JSONResponse({"ok": True, "url": "/" + random.choice(memes), "total": len(memes)})


@app.post("/api/register")
async def register(request: Request, db: Session = Depends(get_db)):
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

    if db.query(User).filter(User.username == username).first():
        return JSONResponse({"ok": False, "error": "Этот логин уже занят"})

    db.add(User(username=username, password_hash=hash_password(password)))
    db.commit()

    sid = str(uuid.uuid4())
    db.add(DBSession(session_id=sid, username=username))
    db.commit()

    resp = JSONResponse({"ok": True, "username": username})
    resp.set_cookie("session_id", sid, httponly=True, samesite="lax")
    return resp


@app.post("/api/login")
async def login(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return JSONResponse({"ok": False, "error": "Заполни все поля"})

    user = db.query(User).filter(User.username == username).first()
    if not user:
        return JSONResponse({"ok": False, "error": "Пользователь не найден"})
    if not verify_password(password, user.password_hash):
        return JSONResponse({"ok": False, "error": "Неверный пароль"})

    sid = str(uuid.uuid4())
    db.add(DBSession(session_id=sid, username=username))
    db.commit()

    resp = JSONResponse({"ok": True, "username": username})
    resp.set_cookie("session_id", sid, httponly=True, samesite="lax")
    return resp
[22.04.2026 21:42] ••𝓝𝓲𝓰𝓱𝓽☾: @app.post("/api/logout")
async def logout(
    response: Response,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if session_id:
        db.query(DBSession).filter(DBSession.session_id == session_id).delete()
        db.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session_id")
    return resp


@app.get("/api/me")
async def me(session_id: str = Cookie(default=None), db: Session = Depends(get_db)):
    if session_id:
        row = db.query(DBSession).filter(DBSession.session_id == session_id).first()
        if row:
            return JSONResponse({"ok": True, "username": row.username})
    return JSONResponse({"ok": False})


@app.get("/api/users")
async def list_users(session_id: str = Cookie(default=None), db: Session = Depends(get_db)):
    if not session_id:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    if not db.query(DBSession).filter(DBSession.session_id == session_id).first():
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    users = db.query(User).all()
    return JSONResponse({
        "ok": True,
        "users": [{"id": u.id, "username": u.username, "created_at": str(u.created_at)} for u in users],
    })
from __future__ import annotations

import json
import random
import re
import uuid
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import SessionLocal, get_db, init_db
from models import Administrator, MediaAsset, Meme, Session as DBSession, User

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
meme_pool_by_session: dict[str, list[str]] = {}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@app.on_event("startup")
def on_startup():
    init_db()
    with SessionLocal() as db:
        sync_users_from_json(db)
        ensure_night_admin(db)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or f"meme-{uuid.uuid4().hex[:10]}"


def create_unique_slug(db: Session, base_title: str) -> str:
    base = slugify(base_title)
    slug = base
    counter = 2
    while db.query(Meme.id).filter(Meme.slug == slug).first():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def sync_users_from_json(db: Session) -> None:
    users_file = Path(__file__).resolve().parent / "users.json"
    if not users_file.exists():
        return
    try:
        payload = json.loads(users_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    if not isinstance(payload, dict):
        return

    created = False
    for username, password in payload.items():
        if not isinstance(username, str) or not isinstance(password, str):
            continue
        normalized = username.strip()
        if not normalized:
            continue
        exists = db.query(User).filter(User.username == normalized).first()
        if exists:
            continue
        db.add(User(username=normalized, display_name=normalized, password_hash=hash_password(password)))
        created = True
    if created:
        db.commit()


def ensure_night_admin(db: Session) -> None:
    user = db.query(User).filter(User.username == "night").first()
    if not user:
        return
    if user.admin_profile:
        return
    db.add(Administrator(user_id=user.id, role="owner", notes="Назначен администратором из users.json"))
    db.commit()


def get_current_session(session_id: str | None, db: Session) -> DBSession | None:
    if not session_id:
        return None
    row = db.query(DBSession).filter(DBSession.session_id == session_id).first()
    if row:
        row.last_seen_at = func.current_timestamp()
        db.commit()
    return row


def get_current_user(session_id: str | None, db: Session) -> User | None:
    session_row = get_current_session(session_id, db)
    return session_row.user if session_row else None


def require_user(session_id: str | None, db: Session) -> User:
    user = get_current_user(session_id, db)
    if not user:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user


def require_admin(session_id: str | None, db: Session) -> User:
    user = require_user(session_id, db)
    if not user.admin_profile:
        raise HTTPException(status_code=403, detail="admin required")
    return user


def get_all_static_memes() -> list[str]:
    static_dir = Path(__file__).resolve().parent / "static"
    if not static_dir.exists():
        return []
    allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    return sorted(
        [p.name for p in static_dir.iterdir() if p.is_file() and p.suffix.lower() in allowed]
    )


def get_next_meme_for_session(session_id: str, meme_ids: list[str]) -> tuple[str, int, bool]:
    existing_pool = meme_pool_by_session.get(session_id)
    pool = [] if existing_pool is None else [m for m in existing_pool if m in meme_ids]
    restarted = existing_pool is not None and len(pool) == 0

    if not pool:
        pool = meme_ids.copy()
        random.shuffle(pool)

    next_meme = pool.pop()
    meme_pool_by_session[session_id] = pool
    return next_meme, len(pool), restarted


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/meme")
async def get_meme(
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    user = get_current_user(session_id, db)
    if not user:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    meme_ids = get_all_static_memes()
    if not meme_ids:
        return JSONResponse({"ok": False, "error": "no memes found"}, status_code=404)
    meme_filename, remaining, restarted = get_next_meme_for_session(session_id, meme_ids)
    return JSONResponse({
        "ok": True,
        "url": f"/static/{meme_filename}",
        "total": len(meme_ids),
        "remaining": remaining,
        "restarted": restarted,
    })


@app.get("/meme/{meme_id}")
async def serve_meme(
    meme_id: int,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    raise HTTPException(status_code=404, detail="meme endpoint deprecated; use /static/*")


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

    user = User(username=username, display_name=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)

    sid = str(uuid.uuid4())
    db.add(
        DBSession(
            session_id=sid,
            user_id=user.id,
            user_agent=request.headers.get("user-agent", "")[:255],
            ip_address=(request.client.host if request.client else ""),
        )
    )
    db.commit()

    resp = JSONResponse({"ok": True, "username": username, "is_admin": bool(user.admin_profile)})
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
    db.add(
        DBSession(
            session_id=sid,
            user_id=user.id,
            user_agent=request.headers.get("user-agent", "")[:255],
            ip_address=(request.client.host if request.client else ""),
        )
    )
    db.commit()

    resp = JSONResponse({"ok": True, "username": username, "is_admin": bool(user.admin_profile)})
    resp.set_cookie("session_id", sid, httponly=True, samesite="lax")
    return resp
@app.post("/api/logout")
async def logout(
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if session_id:
        db.query(DBSession).filter(DBSession.session_id == session_id).delete()
        db.commit()
        meme_pool_by_session.pop(session_id, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session_id")
    return resp


@app.get("/api/me")
async def me(session_id: str = Cookie(default=None), db: Session = Depends(get_db)):
    user = get_current_user(session_id, db)
    if user:
        return JSONResponse({"ok": True, "username": user.username, "is_admin": bool(user.admin_profile)})
    return JSONResponse({"ok": False})


@app.get("/api/users")
async def list_users(session_id: str = Cookie(default=None), db: Session = Depends(get_db)):
    require_admin(session_id, db)
    users = db.query(User).all()
    return JSONResponse({
        "ok": True,
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "created_at": str(u.created_at),
                "is_admin": bool(u.admin_profile),
            }
            for u in users
        ],
    })


@app.get("/api/admin/overview")
async def admin_overview(session_id: str = Cookie(default=None), db: Session = Depends(get_db)):
    require_admin(session_id, db)
    users_count = db.query(User).count()
    admins_count = db.query(Administrator).count()
    memes_count = db.query(Meme).count()
    assets_size = db.query(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).scalar() or 0
    latest_memes = (
        db.query(Meme)
        .order_by(Meme.created_at.desc())
        .limit(20)
        .all()
    )
    return JSONResponse({
        "ok": True,
        "stats": {
            "users": users_count,
            "admins": admins_count,
            "memes": memes_count,
            "storage_bytes": int(assets_size),
        },
        "memes": [
            {
                "id": m.id,
                "title": m.title,
                "slug": m.slug,
                "views_count": m.views_count,
                "created_at": str(m.created_at),
            }
            for m in latest_memes
        ],
    })


@app.post("/api/admin/memes")
async def admin_upload_meme(
    title: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    require_admin(session_id, db)
    return JSONResponse(
        {"ok": False, "error": "Загрузка из ПК отключена. Используй папку static."},
        status_code=400,
    )


@app.delete("/api/admin/memes/{meme_id}")
async def admin_delete_meme(
    meme_id: int,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    require_admin(session_id, db)
    meme = db.query(Meme).filter(Meme.id == meme_id).first()
    if not meme:
        return JSONResponse({"ok": False, "error": "Мем не найден"}, status_code=404)
    db.delete(meme.media_asset)
    db.delete(meme)
    db.commit()
    return JSONResponse({"ok": True})


@app.get("/api/admin/admins")
async def admin_list_admins(
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    require_admin(session_id, db)
    rows = db.query(Administrator).all()
    return JSONResponse({
        "ok": True,
        "admins": [
            {
                "id": a.id,
                "role": a.role,
                "username": a.user.username,
                "notes": a.notes,
                "created_at": str(a.created_at),
            }
            for a in rows
        ],
    })


@app.post("/api/admin/admins")
async def admin_add_admin(
    request: Request,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    require_admin(session_id, db)
    payload = await request.json()
    username = payload.get("username", "").strip()
    role = payload.get("role", "moderator").strip() or "moderator"
    notes = payload.get("notes", "").strip()
    if not username:
        return JSONResponse({"ok": False, "error": "Укажи логин"}, status_code=400)
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return JSONResponse({"ok": False, "error": "Пользователь не найден"}, status_code=404)
    if user.admin_profile:
        return JSONResponse({"ok": False, "error": "Уже администратор"}, status_code=400)
    db.add(Administrator(user_id=user.id, role=role[:32], notes=notes))
    db.commit()
    return JSONResponse({"ok": True})
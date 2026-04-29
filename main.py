from __future__ import annotations

import hashlib
import json
import mimetypes
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

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
DEFAULT_CONTENT_TYPE = "application/octet-stream"


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
    return sorted(
        [p.name for p in static_dir.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS]
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


def detect_content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or DEFAULT_CONTENT_TYPE


def import_static_memes_into_db(db: Session) -> dict[str, int]:
    static_dir = Path(__file__).resolve().parent / "static"
    if not static_dir.exists():
        return {"imported": 0, "skipped": 0, "total": 0}

    meme_files = sorted(
        [
            p
            for p in static_dir.iterdir()
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
        ]
    )
    imported = 0
    skipped = 0

    for meme_path in meme_files:
        file_bytes = meme_path.read_bytes()
        checksum = hashlib.sha256(file_bytes).hexdigest()

        existing_asset = db.query(MediaAsset).filter(MediaAsset.checksum == checksum).first()
        if existing_asset:
            skipped += 1
            continue

        asset = MediaAsset(
            storage_key=f"static-import:{uuid.uuid4()}",
            file_name=meme_path.name,
            content_type=detect_content_type(meme_path),
            size_bytes=len(file_bytes),
            checksum=checksum,
            data=file_bytes,
        )
        db.add(asset)
        db.flush()

        title = meme_path.stem.replace("_", " ").strip() or "Imported meme"
        slug = create_unique_slug(db, title)
        db.add(
            Meme(
                title=title,
                slug=slug,
                description="Imported from static folder",
                media_asset_id=asset.id,
                is_published=True,
            )
        )
        imported += 1

    if imported:
        db.commit()

    return {"imported": imported, "skipped": skipped, "total": len(meme_files)}


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


@app.get("/api/admin/users")
async def admin_list_users(session_id: str = Cookie(default=None), db: Session = Depends(get_db)):
    require_admin(session_id, db)
    users = db.query(User).order_by(User.created_at.desc()).all()
    return JSONResponse(
        {
            "ok": True,
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "display_name": u.display_name,
                    "is_active": bool(u.is_active),
                    "is_admin": bool(u.admin_profile),
                    "created_at": str(u.created_at),
                    "updated_at": str(u.updated_at),
                }
                for u in users
            ],
        }
    )


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(
    user_id: int,
    request: Request,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    require_admin(session_id, db)
    payload = await request.json()
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse({"ok": False, "error": "Пользователь не найден"}, status_code=404)

    if "display_name" in payload:
        display_name = str(payload.get("display_name") or "").strip()
        user.display_name = display_name[:100]
    if "is_active" in payload:
        user.is_active = bool(payload.get("is_active"))
    if payload.get("password"):
        user.password_hash = hash_password(str(payload["password"]))

    db.commit()
    return JSONResponse({"ok": True})


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    current = require_admin(session_id, db)
    if current.id == user_id:
        return JSONResponse({"ok": False, "error": "Нельзя удалить самого себя"}, status_code=400)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse({"ok": False, "error": "Пользователь не найден"}, status_code=404)

    db.delete(user)
    db.commit()
    return JSONResponse({"ok": True})


@app.get("/api/admin/memes")
async def admin_list_memes(session_id: str = Cookie(default=None), db: Session = Depends(get_db)):
    require_admin(session_id, db)
    rows = db.query(Meme).order_by(Meme.created_at.desc()).limit(200).all()
    return JSONResponse(
        {
            "ok": True,
            "memes": [
                {
                    "id": m.id,
                    "title": m.title,
                    "slug": m.slug,
                    "description": m.description,
                    "is_published": bool(m.is_published),
                    "views_count": int(m.views_count),
                    "file_name": m.media_asset.file_name if m.media_asset else None,
                    "content_type": m.media_asset.content_type if m.media_asset else None,
                    "size_bytes": int(m.media_asset.size_bytes) if m.media_asset else None,
                    "created_at": str(m.created_at),
                }
                for m in rows
            ],
        }
    )


@app.patch("/api/admin/memes/{meme_id}")
async def admin_update_meme(
    meme_id: int,
    request: Request,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    require_admin(session_id, db)
    payload = await request.json()
    meme = db.query(Meme).filter(Meme.id == meme_id).first()
    if not meme:
        return JSONResponse({"ok": False, "error": "Мем не найден"}, status_code=404)

    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            return JSONResponse({"ok": False, "error": "title обязателен"}, status_code=400)
        meme.title = title[:150]
    if "description" in payload:
        meme.description = str(payload.get("description") or "")
    if "is_published" in payload:
        meme.is_published = bool(payload.get("is_published"))
    if "slug" in payload:
        new_slug = slugify(str(payload.get("slug") or ""))
        if not new_slug:
            return JSONResponse({"ok": False, "error": "slug обязателен"}, status_code=400)
        exists = db.query(Meme.id).filter(Meme.slug == new_slug, Meme.id != meme.id).first()
        if exists:
            return JSONResponse({"ok": False, "error": "slug уже занят"}, status_code=400)
        meme.slug = new_slug[:180]

    db.commit()
    return JSONResponse({"ok": True})


@app.post("/api/admin/memes/import-static")
async def admin_import_static_memes(session_id: str = Cookie(default=None), db: Session = Depends(get_db)):
    require_admin(session_id, db)
    result = import_static_memes_into_db(db)
    return JSONResponse({"ok": True, **result})


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


@app.get("/api/admin/admins/{admin_id}")
async def admin_get_admin(
    admin_id: int,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    require_admin(session_id, db)
    row = db.query(Administrator).filter(Administrator.id == admin_id).first()
    if not row:
        return JSONResponse({"ok": False, "error": "Администратор не найден"}, status_code=404)
    return JSONResponse(
        {
            "ok": True,
            "admin": {
                "id": row.id,
                "role": row.role,
                "username": row.user.username,
                "user_id": row.user_id,
                "notes": row.notes,
                "created_at": str(row.created_at),
            },
        }
    )


@app.patch("/api/admin/admins/{admin_id}")
async def admin_update_admin(
    admin_id: int,
    request: Request,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    require_admin(session_id, db)
    payload = await request.json()
    row = db.query(Administrator).filter(Administrator.id == admin_id).first()
    if not row:
        return JSONResponse({"ok": False, "error": "Администратор не найден"}, status_code=404)

    if "role" in payload:
        role = str(payload.get("role") or "").strip() or "moderator"
        row.role = role[:32]
    if "notes" in payload:
        row.notes = str(payload.get("notes") or "").strip()

    db.commit()
    return JSONResponse({"ok": True})


@app.delete("/api/admin/admins/{admin_id}")
async def admin_delete_admin(
    admin_id: int,
    session_id: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    current_user = require_admin(session_id, db)
    row = db.query(Administrator).filter(Administrator.id == admin_id).first()
    if not row:
        return JSONResponse({"ok": False, "error": "Администратор не найден"}, status_code=404)
    if row.user_id == current_user.id:
        return JSONResponse({"ok": False, "error": "Нельзя снять админку с самого себя"}, status_code=400)

    db.delete(row)
    db.commit()
    return JSONResponse({"ok": True})
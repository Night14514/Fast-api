from __future__ import annotations

import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://fastapi_user:fastapi_pass@db:5432/fastapi_db"
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    # Lightweight compatibility migration for existing databases created
    # with older versions of this project.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(100) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"))
        conn.execute(
            text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE "
                "NOT NULL DEFAULT CURRENT_TIMESTAMP"
            )
        )
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id BIGINT"))
        conn.execute(
            text(
                "DO $$ "
                "BEGIN "
                "IF EXISTS ("
                "    SELECT 1 FROM information_schema.columns "
                "    WHERE table_name = 'sessions' AND column_name = 'username'"
                ") THEN "
                "    ALTER TABLE sessions ALTER COLUMN username DROP NOT NULL; "
                "END IF; "
                "END $$;"
            )
        )
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_agent VARCHAR(255) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45) NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITHOUT TIME ZONE"))
        conn.execute(
            text(
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITHOUT TIME ZONE "
                "NOT NULL DEFAULT CURRENT_TIMESTAMP"
            )
        )
        conn.execute(text("ALTER TABLE memes ADD COLUMN IF NOT EXISTS title VARCHAR(150) NOT NULL DEFAULT 'Imported meme'"))
        conn.execute(text("ALTER TABLE memes ADD COLUMN IF NOT EXISTS slug VARCHAR(180)"))
        conn.execute(text("ALTER TABLE memes ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''"))
        conn.execute(text("ALTER TABLE memes ADD COLUMN IF NOT EXISTS media_asset_id BIGINT"))
        conn.execute(text("ALTER TABLE memes ADD COLUMN IF NOT EXISTS created_by BIGINT"))
        conn.execute(text("ALTER TABLE memes ADD COLUMN IF NOT EXISTS is_published BOOLEAN NOT NULL DEFAULT TRUE"))
        conn.execute(text("ALTER TABLE memes ADD COLUMN IF NOT EXISTS views_count INTEGER NOT NULL DEFAULT 0"))
        conn.execute(text("UPDATE memes SET slug = CONCAT('legacy-meme-', id::text) WHERE slug IS NULL OR slug = ''"))
        conn.execute(
            text(
                "DO $$ "
                "BEGIN "
                "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='sessions' AND column_name='username') "
                "AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='sessions' AND column_name='user_id') THEN "
                "    UPDATE sessions s "
                "    SET user_id = u.id "
                "    FROM users u "
                "    WHERE s.user_id IS NULL AND s.username IS NOT NULL AND s.username = u.username; "
                "END IF; "
                "END $$;"
            )
        )
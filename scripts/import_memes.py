from __future__ import annotations

import mimetypes
import sys
import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import SessionLocal, init_db
from models import MediaAsset, Meme

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
DEFAULT_CONTENT_TYPE = "application/octet-stream"


def detect_content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or DEFAULT_CONTENT_TYPE


def load_memes_from_static(static_dir: Path) -> list[Path]:
    if not static_dir.exists() or not static_dir.is_dir():
        return []
    return sorted(
        [
            p
            for p in static_dir.iterdir()
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
        ]
    )


def import_memes() -> None:
    init_db()
    static_dir = ROOT / "static"
    meme_files = load_memes_from_static(static_dir)

    if not meme_files:
        print(f"No meme files found in {static_dir}")
        return

    imported = 0
    skipped = 0

    with SessionLocal() as db:
        for meme_path in meme_files:
            filename = meme_path.name
            file_bytes = meme_path.read_bytes()
            checksum = hashlib.sha256(file_bytes).hexdigest()

            exists = db.execute(select(MediaAsset.id).where(MediaAsset.checksum == checksum)).first()
            if exists:
                skipped += 1
                continue

            asset = MediaAsset(
                storage_key=f"legacy-import:{uuid.uuid4()}",
                file_name=filename,
                content_type=detect_content_type(meme_path),
                size_bytes=len(file_bytes),
                checksum=checksum,
                data=file_bytes,
            )
            db.add(asset)
            db.flush()

            db.add(
                Meme(
                    title=meme_path.stem.replace("_", " "),
                    slug=f"legacy-{meme_path.stem.lower().replace(' ', '-')}-{asset.id}",
                    description="Imported from static folder",
                    media_asset_id=asset.id,
                )
            )
            imported += 1

        db.commit()

    print(f"Done. Imported: {imported}, skipped existing: {skipped}")


if __name__ == "__main__":
    import_memes()

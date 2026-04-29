from __future__ import annotations

from database import engine
from models import Base


def rebuild_schema() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("Database schema rebuilt successfully.")


if __name__ == "__main__":
    rebuild_schema()

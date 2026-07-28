from __future__ import annotations

from pathlib import Path

BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
EXPORTS_DIR: Path = DATA_DIR / "exports"
LOGS_DIR: Path = DATA_DIR / "logs"

for folder in (DATA_DIR, EXPORTS_DIR, LOGS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

DATABASE_PATH: Path = DATA_DIR / "office_assistant.db"

APP_NAME: str = "Office Assistant"
APP_VERSION: str = "0.1.0"

HOST: str = "0.0.0.0"
PORT: int = 8000

# storage_file.py
# JSON file storage with file locking + atomic writes.

import json
import os
import time
from typing import Any, Dict

from filelock import FileLock

DATA_DIR = "_data"
ROOMS_PATH = os.path.join(DATA_DIR, "rooms.json")
LOCK_PATH = ROOMS_PATH + ".lock"

os.makedirs(DATA_DIR, exist_ok=True)

def _ensure_file():
    if not os.path.exists(ROOMS_PATH):
        with open(ROOMS_PATH, "w", encoding="utf-8") as f:
            json.dump({"rooms": {}}, f)

def _read_all() -> Dict[str, Any]:
    _ensure_file()
    with open(ROOMS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            # Recover from a corrupt file
            return {"rooms": {}}

def _write_all(payload: Dict[str, Any]) -> None:
    tmp = ROOMS_PATH + f".tmp.{int(time.time()*1000)}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, ROOMS_PATH)  # atomic on POSIX

def read_rooms() -> Dict[str, Any]:
    with FileLock(LOCK_PATH, timeout=5):
        return _read_all()

def write_rooms(data: Dict[str, Any]) -> None:
    with FileLock(LOCK_PATH, timeout=5):
        _write_all(data)

# storage_helpers_file.py
# File-backed room storage with locking + optimistic concurrency.
# Works with app.py imports: get_room/save_room/patch_room and db_* aliases.

import os
import json
import time
from typing import Dict, Any, Optional
from filelock import FileLock

DATA_DIR = "_data"
ROOMS_PATH = os.path.join(DATA_DIR, "rooms.json")
LOCK_PATH = ROOMS_PATH + ".lock"

os.makedirs(DATA_DIR, exist_ok=True)

def _ensure_file() -> None:
    if not os.path.exists(ROOMS_PATH):
        with open(ROOMS_PATH, "w", encoding="utf-8") as f:
            json.dump({"rooms": {}}, f)

def _read_all() -> Dict[str, Any]:
    _ensure_file()
    with open(ROOMS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"rooms": {}}

def _write_all(payload: Dict[str, Any]) -> None:
    tmp = ROOMS_PATH + f".tmp.{int(time.time()*1000)}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, ROOMS_PATH)

def _now() -> int:
    return int(time.time())

def get_room(code: str) -> Optional[Dict[str, Any]]:
    """Fetch a room by code."""
    with FileLock(LOCK_PATH, timeout=5):
        data = _read_all()
        room = data.get("rooms", {}).get(code)
        return room.copy() if room else None

def save_room(room: Dict[str, Any]) -> None:
    """
    Full upsert (overwrite) of a room.
    - Requires room['code'].
    - Sets/bumps version and updated_at.
    """
    if "code" not in room:
        raise ValueError("room must contain a 'code' key")

    with FileLock(LOCK_PATH, timeout=5):
        data = _read_all()
        rooms = data.setdefault("rooms", {})
        code = room["code"]
        payload = room.copy()
        payload["version"] = int(payload.get("version", 0)) + 1
        payload["updated_at"] = _now()
        rooms[code] = payload
        _write_all(data)

def patch_room(room: Dict[str, Any], patch: Dict[str, Any]) -> bool:
    """
    Optimistic concurrency patch:
    - Looks up current room by room['code'].
    - Only writes if current version == room['version'].
    - Shallow merges `patch`, bumps version, updates updated_at.
    Returns True on success, False on version mismatch/missing.
    """
    if "code" not in room:
        return False
    code = room["code"]
    expected_version = int(room.get("version", 1))

    with FileLock(LOCK_PATH, timeout=5):
        data = _read_all()
        rooms = data.setdefault("rooms", {})
        current = rooms.get(code)
        if not current:
            return False
        current_version = int(current.get("version", 1))
        if current_version != expected_version:
            return False

        new_payload = current.copy()
        new_payload.update(patch)
        new_payload["version"] = current_version + 1
        new_payload["updated_at"] = _now()
        rooms[code] = new_payload
        _write_all(data)
        return True

# --- Backwards-compatible aliases that your app may import ---
db_get_room = get_room
db_save_room = save_room
db_patch_room = patch_room

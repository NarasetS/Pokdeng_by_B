import json
import os

STORAGE_FILE = "rooms.json"


def _load_all():
    if not os.path.exists(STORAGE_FILE):
        return {}
    with open(STORAGE_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_all(data):
    with open(STORAGE_FILE, "w") as f:
        json.dump(data, f)


def db_get_room(room_code: str):
    data = _load_all()
    return data.get(room_code)


def db_save_room(room_code: str, room: dict):
    data = _load_all()
    data[room_code] = room
    _save_all(data)


def db_patch_room(room: dict, updates: dict):
    """Patch a room dict with updates and save it back."""
    room.update(updates)
    db_save_room(room.get("code", ""), room)


def add_player(room_code: str, user_id: str, username: str):
    data = _load_all()
    room = data.get(room_code)
    if not room:
        return None
    if "users" not in room:
        room["users"] = {}
    room["users"][user_id] = {
        "name": username,
        "bankroll": 100,
        "hand": [],
        "bet": 0,
        "acted": False,
        "ready": False,
    }
    data[room_code] = room
    _save_all(data)
    return room


def update_player(room_code: str, user_id: str, updates: dict):
    data = _load_all()
    room = data.get(room_code)
    if not room or "users" not in room or user_id not in room["users"]:
        return None
    room["users"][user_id].update(updates)
    data[room_code] = room
    _save_all(data)
    return room

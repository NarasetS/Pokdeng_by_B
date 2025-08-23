# app.py — Pok Deng (ป๊อกเด้ง) Multiplayer — file-backed, identity persistence, Pok & Deng rules, real-time chat
# New in this version:
#  - 💬 Dealer "Clear chat now" button (always visible to dealer)
#  - ⚡ Quick reaction buttons under chat input: Ready / Nice hand! / GG / 😂
#  - Configurable chat limits (display/store caps)
#
# Highlights kept:
# - Identity persistence: ?room=CODE&uid=TOKEN via st.query_params; reclaim-by-name on Join
# - Heartbeat presence: last_seen; lobby shows 🟢 Active / 🟡 Away
# - Full Pok & Deng rules; winner’s multiplier applies
# - Auto-actions: player Pok (8/9) auto-acts; dealer Pok (8/9) auto-settles entire table
# - Partial settlement flow: settle 3-card first, dealer draw, settle remaining
# - Multiplier labels in UI (2 เด้ง, 3 เด้ง, ตอง, etc.) & detailed results
# - Unique widget keys; auto-refresh every 5s
#
# Requirements:
#   pip install streamlit==1.48.1 streamlit-autorefresh==1.0.1 filelock==3.15.4
# No external sources used.

import os, json, time, uuid, random, datetime
from typing import Dict, List, Optional, Tuple

import streamlit as st
from filelock import FileLock
from streamlit_autorefresh import st_autorefresh

# =========================
# Tunables
# =========================
POLL_INTERVAL_MS = 2000        # chat/game auto-refresh
ACTIVE_WINDOW = 20             # seconds for Active/Away
CHAT_SHOW_MAX = 100            # how many msgs to render
CHAT_STORE_MAX = 200           # how many msgs to keep in file
QUICK_REACTIONS = ["Ready", "Nice hand!", "GG", "😂"]

# =========================
# Storage (file-backed)
# =========================
DATA_DIR = "_data"
ROOMS_FILE = os.path.join(DATA_DIR, "rooms.json")
LOCK_FILE = ROOMS_FILE + ".lock"
os.makedirs(DATA_DIR, exist_ok=True)

def _ensure_file():
    if not os.path.exists(ROOMS_FILE):
        with open(ROOMS_FILE, "w", encoding="utf-8") as f:
            json.dump({"rooms": {}}, f)

def _read_all() -> Dict:
    _ensure_file()
    with open(ROOMS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"rooms": {}}

def _write_all(payload: Dict) -> None:
    tmp = ROOMS_FILE + f".tmp.{int(time.time()*1000)}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, ROOMS_FILE)

def read_rooms() -> Dict:
    with FileLock(LOCK_FILE, timeout=5):
        return _read_all()

def write_rooms(data: Dict) -> None:
    with FileLock(LOCK_FILE, timeout=5):
        _write_all(data)

def get_room(code: str) -> Optional[Dict]:
    return read_rooms().get("rooms", {}).get(code)

def save_room(room: Dict) -> None:
    if "code" not in room:
        raise ValueError("room must contain 'code'")
    data = read_rooms()
    rooms = data.setdefault("rooms", {})
    room["version"] = int(room.get("version", 0)) + 1
    room["updated_at"] = int(time.time())
    rooms[room["code"]] = room
    write_rooms(data)

# =========================
# Cards & Scoring
# =========================
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

def card_label(n: int) -> str:
    r = (n - 1) % 13
    s = (n - 1) // 13
    return RANKS[r] + SUITS[s]

def ranks_suits(hand: List[int]) -> Tuple[List[int], List[int]]:
    ranks = [((c - 1) % 13) + 1 for c in hand]  # 1..13
    suits = [((c - 1) // 13) for c in hand]     # 0..3
    return ranks, suits

def hand_to_str(hand: List[int]) -> str:
    return " ".join(card_label(c) for c in hand) if hand else "-"

def facedown_str(n: int) -> str:
    return " ".join(["🂠"] * n) if n > 0 else "-"

def ensure_deck(room: Dict):
    if not room.get("deck"):
        room["deck"] = list(range(1, 53))
        random.shuffle(room["deck"])

def draw_card(room: Dict) -> int:
    ensure_deck(room)
    return room["deck"].pop()

def hand_points(hand: List[int]) -> int:
    total = 0
    for c in hand:
        rank = (c - 1) % 13  # 0..12
        if rank == 0:        # A
            total += 1
        elif 1 <= rank <= 8: # 2..9
            total += (rank + 1)
        else:                # 10/J/Q/K
            total += 0
    return total % 10

def is_pok(hand: List[int]) -> Tuple[bool, int]:
    if len(hand) != 2:
        return False, 0
    pts = hand_points(hand)
    return (pts in (8, 9), pts)

def is_three_of_a_kind(ranks: List[int]) -> bool:
    return len(ranks) == 3 and len(set(ranks)) == 1

def is_flush(suits: List[int]) -> bool:
    return len(suits) == 3 and len(set(suits)) == 1

def is_jqk(ranks: List[int]) -> bool:
    return sorted(ranks) == [11, 12, 13]

def is_straight(ranks: List[int]) -> bool:
    if len(ranks) != 3:
        return False
    r = sorted(ranks)
    if r[1] == r[0] + 1 and r[2] == r[1] + 1:
        return True
    return r == [1, 2, 3] or sorted(r) == [1, 12, 13]  # A-2-3 or Q-K-A

def deng_multiplier(hand: List[int]) -> Tuple[int, str]:
    ranks, suits = ranks_suits(hand)
    if len(hand) == 2:
        if ranks[0] == ranks[1] or suits[0] == suits[1]:
            return 2, "2 เด้ง"
        return 1, "x1"
    if len(hand) == 3:
        if is_three_of_a_kind(ranks):
            return 5, "5 เด้ง (ตอง)"
        if is_straight(ranks) or is_flush(suits) or is_jqk(ranks):
            return 3, "3 เด้ง"
        return 1, "x1"
    return 1, "x1"

def mult_label(hand: List[int]) -> str:
    m, lbl = deng_multiplier(hand)
    return "" if m == 1 else f" — {lbl}"

# =========================
# Settlement (Pok + Deng)
# =========================
def settle_one(player_pts: int, player_pok: bool, player_mult: int,
               dealer_pts: int, dealer_pok: bool, dealer_mult: int,
               bet: int) -> Tuple[str, int]:
    # Pok priority
    if player_pok or dealer_pok:
        if player_pok and dealer_pok:
            if player_pts > dealer_pts:
                return "win", bet * player_mult
            elif player_pts < dealer_pts:
                return "lose", -bet * dealer_mult
            else:
                return "draw", 0
        elif player_pok:
            return "win", bet * player_mult
        else:
            return "lose", -bet * dealer_mult
    # Normal compare
    if player_pts > dealer_pts:
        return "win", bet * player_mult
    if player_pts < dealer_pts:
        return "lose", -bet * dealer_mult
    return "draw", 0

def settle_players(room: Dict, target_uids: List[str]) -> List[Dict]:
    """Settle a subset of players vs current dealer hand; append detailed results with labels."""
    dealer_id = room["dealer"]
    if not dealer_id:
        return []
    room.setdefault("settled_players", [])
    room.setdefault("last_results", [])

    settled_set = set(room["settled_players"])
    d_hand = room["users"][dealer_id]["hand"]
    d_pts = hand_points(d_hand)
    d_pok, _ = is_pok(d_hand)
    d_mult, d_lbl = deng_multiplier(d_hand)

    results = []
    for uid in target_uids:
        if uid in settled_set:
            continue
        u = room["users"][uid]
        p_hand = u["hand"]
        p_pts = hand_points(p_hand)
        p_pok, _ = is_pok(p_hand)
        p_mult, p_lbl = deng_multiplier(p_hand)
        bet = max(1, int(u.get("bet", 1)))

        outcome, payout = settle_one(p_pts, p_pok, p_mult, d_pts, d_pok, d_mult, bet)

        # Apply bankroll transfer
        if payout > 0:
            u["bankroll"] += payout
            room["users"][dealer_id]["bankroll"] -= payout
        elif payout < 0:
            u["bankroll"] += payout  # negative
            room["users"][dealer_id]["bankroll"] -= payout  # minus negative = add to dealer

        res = {
            "player_id": uid,
            "player_name": u["name"],
            "outcome": outcome,
            "payout": payout,
            "player_pts": p_pts,
            "dealer_pts": d_pts,
            "player_mult_label": p_lbl if p_mult != 1 else "",
            "dealer_mult_label": d_lbl if d_mult != 1 else "",
        }
        results.append(res)
        room["last_results"].append(res)
        settled_set.add(uid)

    room["settled_players"] = list(settled_set)
    return results

# =========================
# Identity helpers
# =========================
def normalize_name(x: str) -> str:
    return (x or "").strip().lower()

def find_user_by_name(room: Dict, name: str) -> Optional[str]:
    target = normalize_name(name)
    for uid, u in room.get("users", {}).items():
        if normalize_name(u.get("name")) == target:
            return uid
    return None

def update_heartbeat(room: Dict, uid: str) -> None:
    if uid in room.get("users", {}):
        room["users"][uid]["last_seen"] = int(time.time())
        save_room(room)

def is_active(u: Dict) -> bool:
    ts = u.get("last_seen", 0)
    return (int(time.time()) - int(ts)) <= ACTIVE_WINDOW

# =========================
# App
# =========================
def main():
    st.set_page_config(page_title="Pok Deng — Multiplayer", page_icon="🎴", layout="wide")
    st_autorefresh(interval=POLL_INTERVAL_MS, key="pokdeng-refresh")

    # Reclaim identity from URL params (new API)
    params = st.query_params
    url_uid = params.get("uid")
    url_room = params.get("room")

    # Session identity
    if "user_id" not in st.session_state:
        st.session_state.user_id = url_uid or uuid.uuid4().hex
    user_id = st.session_state.user_id

    st.title("🎴 Pok Deng — Multiplayer")
    st.caption("File-backed JSON storage; auto-refresh every 5s. (No external sources used.)")

    # Layout: main + chat
    left, right = st.columns([2, 1])

    # Sidebar: join/create
    with st.sidebar:
        st.header("Join or Create")
        name = st.text_input("Your name", value=st.session_state.get("display_name", "Player"))
        st.session_state["display_name"] = name
        room_code = st.text_input("Room code", value=st.session_state.get("room_code", url_room or "")).upper()

        c1, c2 = st.columns(2)
        if c1.button("Create", key="btn_create"):
            if not room_code:
                st.warning("Enter a room code first (e.g., ABCD1).")
            else:
                now = int(time.time())
                room = {
                    "code": room_code,
                    "created_at": now,
                    "updated_at": now,
                    "version": 1,
                    "status": "lobby",
                    "dealer": None,
                    "deck": [],
                    "users": {},
                    "order": [],
                    "last_results": [],
                    "settled_players": [],
                    "chat": []
                }
                save_room(room)
                st.session_state["room_code"] = room_code
                # write query params
                try:
                    st.query_params["room"] = room_code
                    st.query_params["uid"] = user_id
                except Exception:
                    pass
                st.success(f"Room {room_code} created.")
                st.rerun()
        if c2.button("Join", key="btn_join"):
            if not room_code:
                st.warning("Enter a room code.")
            else:
                room = get_room(room_code)
                if not room:
                    st.error("Room not found. Create it first.")
                else:
                    # Try reclaim by name
                    existing_uid = find_user_by_name(room, name)
                    if existing_uid:
                        st.session_state["user_id"] = existing_uid
                        user_id_new = existing_uid
                        room["users"].setdefault(user_id_new, {
                            "name": name, "bankroll": 1000, "hand": [],
                            "bet": 10, "acted": False, "ready": False
                        })
                        room["users"][user_id_new]["name"] = name
                        save_room(room)
                    else:
                        if user_id not in room.get("users", {}):
                            room["users"][user_id] = {
                                "name": name, "bankroll": 1000, "hand": [],
                                "bet": 10, "acted": False, "ready": False
                            }
                            save_room(room)
                        else:
                            room["users"][user_id]["name"] = name
                            save_room(room)
                    st.session_state["room_code"] = room_code
                    try:
                        st.query_params["room"] = room_code
                        st.query_params["uid"] = st.session_state["user_id"]
                    except Exception:
                        pass
                    st.success(f"Joined room {room_code}.")
                    st.rerun()

    # Need a room
    room_code = st.session_state.get("room_code") or url_room
    if not room_code:
        left.info("Enter a room code and click Create or Join.")
        with right:
            st.subheader("💬 Table Chat")
            st.info("Join a room to start chatting.")
        return

    room = get_room(room_code)
    if not room:
        left.error("Room not found or removed.")
        with right:
            st.subheader("💬 Table Chat")
            st.info("Room not found.")
        return

    # Ensure code exists and URL reflects (room, uid)
    if "code" not in room:
        room["code"] = room_code
        save_room(room)
    try:
        st.query_params["room"] = room_code
        st.query_params["uid"] = st.session_state.user_id
    except Exception:
        pass

    # Ensure this user exists (reclaim by URL uid if needed)
    if st.session_state.user_id not in room["users"]:
        existing_uid = find_user_by_name(room, st.session_state.get("display_name", "Player"))
        if existing_uid:
            st.session_state.user_id = existing_uid
        else:
            room["users"][st.session_state.user_id] = {
                "name": st.session_state.get("display_name", "Player"),
                "bankroll": 1000, "hand": [], "bet": 10, "acted": False, "ready": False
            }
            save_room(room)
        room = get_room(room_code)

    user_id = st.session_state.user_id
    dealer_id = room.get("dealer")

    # Heartbeat
    update_heartbeat(room, user_id)
    room = get_room(room_code)

    # ======= Left: Game UI =======
    with left:
        st.subheader(f"Room {room['code']} — {room['status']}")
        st.caption(f"Dealer: {room['users'][dealer_id]['name'] if dealer_id else '-'} | Players: {len(room['users']) - (1 if dealer_id else 0)}")

        # LOBBY
        if room["status"] == "lobby":
            st.markdown("**Players**")
            for uid, u in room["users"].items():
                ready_txt = " ✅ Ready" if u.get("ready") else ""
                role_txt = " (Dealer)" if uid == dealer_id else ""
                live_txt = "🟢 Active" if is_active(u) else "🟡 Away"
                st.write(f"👤 {u['name']}{role_txt} — bankroll {u.get('bankroll', 1000)} — {live_txt}{ready_txt}")

            st.divider()
            st.markdown("#### My settings")
            me = room["users"][user_id]
            try:
                current_bet = int(me.get("bet", 10))
            except Exception:
                current_bet = 10
            if current_bet < 1:
                current_bet = 10
            bet_val = st.number_input("My bet", min_value=1, value=int(current_bet), step=1, key=f"bet_{user_id}")
            ready_val = st.toggle("I'm ready", value=bool(me.get("ready", False)), key=f"ready_{user_id}")
            if int(bet_val) != me.get("bet") or bool(ready_val) != me.get("ready"):
                me["bet"] = int(bet_val); me["ready"] = bool(ready_val)
                save_room(room); room = get_room(room_code)

            if dealer_id is None and st.button("Become Dealer", key=f"btn_dealer_{room_code}"):
                room = get_room(room_code)
                room["dealer"] = user_id
                save_room(room)
                st.rerun()

            st.caption(f"Your reconnect token: `{user_id}` — bookmark this page (URL now includes `?room={room_code}&uid={user_id}`)")

            if user_id == dealer_id and st.button("Start Round", key=f"btn_start_{room_code}"):
                latest = get_room(room_code)
                if not latest:
                    st.error("Room missing")
                else:
                    ready_players = [uid for uid, u in latest["users"].items() if uid != dealer_id and u.get("ready")]
                    if not ready_players:
                        st.warning("No ready players yet.")
                    else:
                        latest["deck"] = list(range(1, 53)); random.shuffle(latest["deck"])
                        latest["last_results"] = []; latest["settled_players"] = []
                        latest["chat"] = []  # 🔄 clear chat each round start
                        for uid, u in latest["users"].items():
                            u["hand"] = []; u["acted"] = False
                            try:
                                if int(u.get("bet", 0)) < 1: u["bet"] = 10
                            except Exception:
                                u["bet"] = 10
                        for uid in ready_players:
                            latest["users"][uid]["hand"] = [draw_card(latest), draw_card(latest)]
                            p_pok, _ = is_pok(latest["users"][uid]["hand"])
                            if p_pok: latest["users"][uid]["acted"] = True
                        if dealer_id:
                            latest["users"][dealer_id]["hand"] = [draw_card(latest), draw_card(latest)]
                            d_pok, _ = is_pok(latest["users"][dealer_id]["hand"])
                            if d_pok:
                                remaining = ready_players[:]
                                if remaining:
                                    settle_players(latest, remaining)
                                latest["status"] = "settlement"
                                for uid in ready_players: latest["users"][uid]["ready"] = False
                                save_room(latest); st.rerun()
                        latest["status"] = "player_action"
                        latest["order"] = ready_players
                        for uid in ready_players: latest["users"][uid]["ready"] = False
                        save_room(latest); st.rerun()

        # PLAYER ACTION
        elif room["status"] == "player_action":
            st.markdown("### Players decide")
            if dealer_id:
                dealer_hand = room["users"][dealer_id]["hand"]
                if user_id == dealer_id:
                    d_lbl = mult_label(dealer_hand)
                    st.write(f"Dealer: {hand_to_str(dealer_hand)} ({hand_points(dealer_hand)} pts){d_lbl}")
                else:
                    st.write(f"Dealer: {facedown_str(len(dealer_hand))}")

            for uid in room["order"]:
                u = room["users"][uid]
                tag = " (me)" if uid == user_id else ""
                p_pok, _ = is_pok(u["hand"]) if len(u["hand"]) == 2 else (False, 0)
                pok_badge = " 🔥 Pok!" if p_pok else ""
                if uid == user_id:
                    p_lbl = mult_label(u["hand"])
                    st.write(f"👤 {u['name']}{tag} — {hand_to_str(u['hand'])} ({hand_points(u['hand'])} pts){p_lbl}{pok_badge}")
                    c1, c2 = st.columns(2)
                    if c1.button("Stay", key=f"btn_stay_{uid}") and not u.get("acted"):
                        latest = get_room(room_code); latest["users"][uid]["acted"] = True; save_room(latest); st.rerun()
                    if c2.button("Draw", key=f"btn_draw_{uid}") and len(u["hand"]) < 3 and not u.get("acted") and not p_pok:
                        latest = get_room(room_code); latest["users"][uid]["hand"].append(draw_card(latest)); latest["users"][uid]["acted"] = True; save_room(latest); st.rerun()
                else:
                    status_txt = "✅ Acted" if u.get("acted") else "⌛ Waiting"
                    st.write(f"👤 {u['name']}{tag} — {facedown_str(len(u['hand']))} — {status_txt}{pok_badge}")

            latest = get_room(room_code)
            if latest and all(latest["users"][uid].get("acted") for uid in latest["order"]):
                latest["status"] = "dealer_action"; save_room(latest); st.rerun()

        # DEALER ACTION
        elif room["status"] == "dealer_action":
            st.markdown("### Dealer Phase")
            dealer = room["users"].get(room.get("dealer")) if room.get("dealer") else None
            dealer_id = room.get("dealer")
            if not dealer:
                st.warning("No dealer assigned. Back to lobby?")
            else:
                d_pok, _ = is_pok(dealer["hand"]) if len(dealer["hand"]) == 2 else (False, 0)

                if user_id == dealer_id:
                    d_lbl = mult_label(dealer["hand"])
                    extra = " 🔥 Pok!" if d_pok else ""
                    st.write(f"Dealer: {hand_to_str(dealer['hand'])} ({hand_points(dealer['hand'])} pts){d_lbl}{extra}")
                else:
                    st.write(f"Dealer: {facedown_str(len(dealer['hand']))}")

                if user_id == dealer_id and d_pok:
                    latest = get_room(room_code)
                    remaining = [uid for uid in latest["order"] if uid not in latest.get("settled_players", [])]
                    if remaining: settle_players(latest, remaining)
                    latest["status"] = "settlement"; save_room(latest); st.rerun()

                st.markdown("**Players**")
                for uid in room["order"]:
                    u = room["users"][uid]
                    tag = " (me)" if uid == user_id else ""
                    settled_badge = " ✅ Settled" if uid in room.get("settled_players", []) else ""
                    p_pok, _ = is_pok(u["hand"]) if len(u["hand"]) == 2 else (False, 0)
                    pok_b = " 🔥 Pok!" if p_pok else ""
                    if uid == user_id:
                        p_lbl = mult_label(u["hand"])
                        st.write(f"👤 {u['name']}{tag} — {hand_to_str(u['hand'])} ({hand_points(u['hand'])} pts){p_lbl}{pok_b}{settled_badge}")
                    else:
                        st.write(f"👤 {u['name']}{tag} — {facedown_str(len(u['hand']))}{settled_badge}{pok_b}")

                # AUTO: settle all 2-card Pok players first (dealer not Pok)
                if user_id == dealer_id and not d_pok:
                    latest = get_room(room_code)
                    pok_targets = [uid for uid in latest["order"]
                                   if uid not in latest.get("settled_players", [])
                                   and len(latest["users"][uid]["hand"]) == 2
                                   and is_pok(latest["users"][uid]["hand"])[0]]
                    if pok_targets:
                        settle_players(latest, pok_targets)
                        save_room(latest)
                        st.rerun()

                    st.divider()
                    st.markdown("#### Dealer controls")
                    latest = get_room(room_code)
                    pending_3 = len([uid for uid in latest["order"]
                                     if len(latest["users"][uid]["hand"]) == 3 and uid not in latest.get("settled_players", [])])
                    c1, c2, c3, c4 = st.columns(4)
                    if c1.button(f"Settle vs 3-card players ({pending_3})", key=f"btn_settle3_{room_code}"):
                        latest = get_room(room_code)
                        targets = [uid for uid in latest["order"]
                                   if len(latest["users"][uid]["hand"]) == 3 and uid not in latest.get("settled_players", [])]
                        if targets: settle_players(latest, targets); save_room(latest)
                        else: st.info("No 3-card players to settle right now.")
                        st.rerun()
                    if c2.button("Dealer Draw", key=f"btn_ddraw_{room_code}"):
                        latest = get_room(room_code)
                        if len(latest["users"][dealer_id]["hand"]) < 3:
                            latest["users"][dealer_id]["hand"].append(draw_card(latest)); save_room(latest)
                        st.rerun()
                    if c3.button("Settle Remaining", key=f"btn_settlerest_{room_code}"):
                        latest = get_room(room_code)
                        remaining = [uid for uid in latest["order"] if uid not in latest.get("settled_players", [])]
                        if remaining: settle_players(latest, remaining)
                        latest["status"] = "settlement"; save_room(latest); st.rerun()
                    if c4.button("Back to Lobby", key=f"btn_back_dealer_{room_code}"):
                        latest = get_room(room_code)
                        for uid in latest["users"]:
                            latest["users"][uid]["hand"] = []; latest["users"][uid]["acted"] = False; latest["users"][uid]["ready"] = False
                        latest["status"] = "lobby"; latest["last_results"] = []; latest["settled_players"] = []; save_room(latest); st.rerun()
                elif user_id != dealer_id:
                    st.write("Waiting for dealer decisions…")

                if room.get("last_results"):
                    st.divider()
                    st.markdown("#### Results so far")
                    for res in room["last_results"]:
                        pl = f" {res['player_mult_label']}" if res.get("player_mult_label") else ""
                        dl = f" {res['dealer_mult_label']}" if res.get("dealer_mult_label") else ""
                        st.write(f"{room['users'][res['player_id']]['name']}: {res['outcome']} (payout {res['payout']}) — P{pl} vs D{dl}")

        # SETTLEMENT
        elif room["status"] == "settlement":
            st.markdown("### Showdown")
            dealer_id = room["dealer"]
            dealer = room["users"][dealer_id]
            d_lbl = mult_label(dealer["hand"])
            st.write(f"Dealer: {hand_to_str(dealer['hand'])} ({hand_points(dealer['hand'])} pts){d_lbl}")
            for uid in room["order"]:
                u = room["users"][uid]
                p_lbl = mult_label(u["hand"])
                st.write(f"👤 {u['name']} — {hand_to_str(u['hand'])} ({hand_points(u['hand'])} pts){p_lbl}")

            st.divider()
            st.markdown("**Results**")
            for res in room.get("last_results", []):
                player = room["users"][res["player_id"]]
                pl = f" {res['player_mult_label']}" if res.get("player_mult_label") else ""
                dl = f" {res['dealer_mult_label']}" if res.get("dealer_mult_label") else ""
                st.write(f"{player['name']} — {res['outcome']} (payout {res['payout']}) — P{pl} vs D{dl}")

            if st.button("Back to Lobby", key=f"btn_back_settlement_{room_code}"):
                latest = get_room(room_code)
                for uid in latest["users"]:
                    latest["users"][uid]["hand"] = []; latest["users"][uid]["acted"] = False; latest["users"][uid]["ready"] = False
                latest["status"] = "lobby"; latest["last_results"] = []; latest["settled_players"] = []; save_room(latest); st.rerun()

    # ======= Right: Real-time Chat =======
    with right:
        st.subheader("💬 Table Chat")

        # Dealer admin action: clear chat now
        room = get_room(room_code)
        dealer_id = room.get("dealer")
        if user_id == dealer_id:
            if st.button("🧹 Clear chat now", key=f"btn_clear_chat_{room_code}"):
                latest = get_room(room_code)
                latest["chat"] = []
                save_room(latest)
                st.rerun()

        # Render chat
        room = get_room(room_code)  # refresh to reflect admin clears
        chat = room.get("chat", [])

        # Show up to CHAT_SHOW_MAX messages, newest at the top
        for msg in reversed(chat[-CHAT_SHOW_MAX:]):
            ts = datetime.datetime.fromtimestamp(msg.get("ts", int(time.time()))).strftime("%H:%M:%S")
            with st.chat_message("user"):
                st.markdown(f"**{msg.get('name','?')}** · {ts}\n\n{msg.get('text','')}")

        # Chat input + quick reactions
        message = st.chat_input("Type a message")
        # quick reactions row
        cols = st.columns(len(QUICK_REACTIONS))
        sent_reaction = None
        for i, label in enumerate(QUICK_REACTIONS):
            if cols[i].button(label, key=f"react_{label}_{room_code}"):
                sent_reaction = label

        to_send = message.strip() if message else None
        if sent_reaction:
            to_send = sent_reaction if not to_send else to_send

        if to_send:
            latest = get_room(room_code)
            latest.setdefault("chat", [])
            sender_name = latest["users"].get(user_id, {}).get("name", "Player")
            latest["chat"].append({
                "uid": user_id,
                "name": sender_name,
                "text": to_send,
                "ts": int(time.time())
            })
            if len(latest["chat"]) > CHAT_STORE_MAX:
                latest["chat"] = latest["chat"][-CHAT_STORE_MAX:]
            save_room(latest)
            st.rerun()

if __name__ == "__main__":
    main()

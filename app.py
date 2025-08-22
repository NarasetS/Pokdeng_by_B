"""
🎴 Pok Deng — Multiplayer (Private-Info + Ready Signals) — File-backed

What’s new (vs. previous demo):
- Hidden information: players only see *their own cards*; others see facedown counts. Dealer cannot see any player cards until showdown.
- Privacy: players only see *their own bankroll/P&L*. Dealer doesn’t see bankroll either; settlement still updates server-side values.
- Ready system: in the Lobby, each player toggles Ready. Dealer sees who’s ready and can start only when conditions are met.
- Flow: Deal -> Players act (Hit/Stand) -> Dealer phase -> Showdown -> Settlement -> Back to Lobby
- Auto-refresh: 1s polling; no manual sync needed.

Storage: file-backed JSON with locking (see storage_file.py / storage_helpers_file.py from previous canvas).
This file expects `storage_helpers_file.py` in the same repo.
"""

import time
import uuid
import random
import string
from typing import Dict, List, Tuple

import streamlit as st
from streamlit_autorefresh import st_autorefresh
try:
    from storage_helpers_file import db_get_room, db_save_room, db_patch_room
except ImportError:
    # Fallback to newer helper names
    from storage_helpers_file import get_room as db_get_room, save_room as db_save_room, patch_room as db_patch_room

# ----------------------
# Card utilities
# ----------------------
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
RANK_VALUE = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 0, "J": 0, "Q": 0, "K": 0}
Card = Tuple[str, str]

def make_deck():
    return [(r, s) for s in SUITS for r in RANKS]

def shuffle_deck(deck):
    random.shuffle(deck)

def card_points(rank: str) -> int:
    return RANK_VALUE[rank]

def hand_points(hand: List[Card]) -> int:
    return sum(card_points(r) for r, _ in hand) % 10

def is_pok(hand: List[Card]):
    if len(hand) != 2:
        return False, 0
    pts = hand_points(hand)
    return (pts in (8, 9), pts)

def is_straight(ranks: List[str]) -> bool:
    idx = [RANKS.index(r) + 1 for r in ranks]
    idx.sort()
    return idx[1] == idx[0] + 1 and idx[2] == idx[1] + 1

def is_flush(suits: List[str]) -> bool:
    return len(set(suits)) == 1

def is_three_of_a_kind(ranks: List[str]) -> bool:
    return len(set(ranks)) == 1

def is_jqk(ranks: List[str]) -> bool:
    return set(ranks) == {"J", "Q", "K"}

def deng_multiplier(hand: List[Card]):
    if len(hand) == 2:
        r1, s1 = hand[0]; r2, s2 = hand[1]
        if r1 == r2 or s1 == s2:
            return 2, "2 เด้ง"
        return 1, "x1"
    if len(hand) == 3:
        ranks = [r for r, _ in hand]; suits = [s for _, s in hand]
        if is_three_of_a_kind(ranks):
            return 5, "5 เด้ง (ตอง)"
        if is_straight(ranks) or is_flush(suits) or is_jqk(ranks):
            return 3, "3 เด้ง"
        return 1, "x1"
    return 1, "x1"

# ----------------------
# Room helpers
# ----------------------

def gen_code(n=5):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def room_dealer_id(room) -> str:
    for uid, u in room["users"].items():
        if u.get("role") == "dealer":
            return uid
    return None


def ensure_deck(room):
    if not room.get("deck"):
        room["deck"] = make_deck()
        shuffle_deck(room["deck"])


def draw_card(room) -> Card:
    ensure_deck(room)
    return room["deck"].pop()


def reset_round(room):
    room["status"] = "dealing"
    room["deck"] = make_deck(); shuffle_deck(room["deck"])
    for u in room["users"].values():
        u["acted"] = False
        u["hand"] = []
        # keep bets and bankroll; clear ready for next round after settlement


def deal_initial(room):
    dealer_id = room_dealer_id(room)
    # deal two to each player, then dealer
    for uid in room["order"]:
        room["users"][uid]["hand"] = [draw_card(room), draw_card(room)]
    room["users"][dealer_id]["hand"] = [draw_card(room), draw_card(room)]

    # If any pok, we still proceed with player_actions (players with pok auto-acted)
    any_pok = False
    for uid in room["order"] + [dealer_id]:
        pok, _ = is_pok(room["users"][uid]["hand"])
        any_pok = any_pok or pok
        if uid != dealer_id and pok:
            room["users"][uid]["acted"] = True

    room["status"] = "player_actions"


def all_players_acted(room):
    dealer_id = room_dealer_id(room)
    return all(room["users"][uid].get("acted", False) for uid in room["order"] if uid != dealer_id)


def dealer_play(room):
    dealer_id = room_dealer_id(room)
    dh = room["users"][dealer_id]["hand"]
    pts = hand_points(dh)
    if len(dh) == 2 and pts <= 4:
        dh.append(draw_card(room))
    room["status"] = "showdown"


def settle_room(room):
    dealer_id = room_dealer_id(room)
    duser = room["users"][dealer_id]
    dpts = hand_points(duser["hand"]) 
    d_pok, _ = is_pok(duser["hand"]) if len(duser["hand"]) == 2 else (False, 0)
    dmult, _ = deng_multiplier(duser["hand"])  # only used when dealer wins

    results = []
    for uid in list(room["order"]):
        u = room["users"][uid]
        bet = int(u.get("bet", room["settings"]["min_bet"]))
        ppts = hand_points(u["hand"]) 
        p_pok, _ = is_pok(u["hand"]) if len(u["hand"]) == 2 else (False, 0)
        pmult, _ = deng_multiplier(u["hand"])  # used when player wins
        outcome = "push"; payout = 0
        if p_pok or d_pok:
            if p_pok and d_pok:
                if ppts > dpts: outcome, payout = "win", bet * pmult
                elif ppts < dpts: outcome, payout = "lose", -bet * dmult
            elif p_pok: outcome, payout = "win", bet * pmult
            else: outcome, payout = "lose", -bet * dmult
        else:
            if ppts > dpts: outcome, payout = "win", bet * pmult
            elif ppts < dpts: outcome, payout = "lose", -bet * dmult
        u["bankroll"] = int(u.get("bankroll", room["settings"]["starting_bankroll"])) + payout
        duser["bankroll"] = int(duser.get("bankroll", room["settings"]["starting_bankroll"])) - payout
        results.append({
            "player_id": uid,
            "player_name": u["name"],
            "outcome": outcome,
            "payout": payout,
            "player_pts": ppts,
            "dealer_pts": dpts,
        })
    room["status"] = "settlement"
    return results

# ----------------------
# UI helpers
# ----------------------

def hand_to_str(hand: List[Card]) -> str:
    return "  ".join([f"{r}{s}" for r, s in hand]) if hand else "-"

FACEDOWN = "🂠"

def facedown_str(n: int) -> str:
    return " ".join([FACEDOWN] * n) if n > 0 else "-"

# ----------------------
# App
# ----------------------

def main():
    st.set_page_config(page_title="Pok Deng — Private Multiplayer", page_icon="🎴", layout="wide")
    if "user_id" not in st.session_state:
        st.session_state.user_id = uuid.uuid4().hex
    user_id = st.session_state.user_id

    st.title("🎴 Pok Deng — Multiplayer (Private Info + Ready Signals)")
    st.caption("No external sources used. File-backed storage; auto-refresh every 5s.")

    with st.sidebar:
        st.header("Profile")
        name = st.text_input("Display name", value=st.session_state.get("display_name", "Player"))
        st.session_state["display_name"] = name
        desired_role = st.radio("Desired role", ["player", "dealer"], index=0, horizontal=True)

        st.subheader("Create room")
        max_players = st.number_input("Max players", 1, 9, 6)
        min_bet = st.number_input("Min bet", 1, 1_000_000, 10)
        starting_bankroll = st.number_input("Starting bankroll", 1, 10_000_000, 1000)
        if st.button("Create"):
            code = gen_code()
            now = int(time.time())
            room = {
                "code": code,
                "created_at": now,
                "owner_id": user_id,
                "settings": {"max_players": int(max_players), "min_bet": int(min_bet), "starting_bankroll": int(starting_bankroll)},
                "status": "lobby",
                "deck": [],
                "users": {
                    user_id: {"name": name or f"User-{user_id[:4]}", "role": "dealer", "bankroll": int(starting_bankroll), "bet": int(min_bet), "acted": False, "hand": [], "ready": False}
                },
                "order": [],
                "version": 1,
                "updated_at": now,
            }
            db_save_room(room)
            st.session_state["room_code"] = code

        st.subheader("Join room")
        room_code = st.text_input("Room code", value=st.session_state.get("room_code", "")).upper()
        if st.button("Join") and room_code:
            room = db_get_room(room_code)
            if not room:
                st.error("Room not found")
            else:
                users = room["users"]
                role = "player"
                if desired_role == "dealer" and not any(u.get("role") == "dealer" for u in users.values()):
                    role = "dealer"
                users[user_id] = users.get(user_id, {"name": name, "role": role, "bankroll": room["settings"]["starting_bankroll"], "bet": room["settings"]["min_bet"], "acted": False, "hand": [], "ready": False})
                users[user_id]["name"] = name
                # maintain order
                if role == "player" and user_id not in room["order"]:
                    room["order"].append(user_id)
                if role == "dealer" and user_id in room["order"]:
                    room["order"].remove(user_id)
                db_save_room(room)
                st.session_state["room_code"] = room_code

    room_code = st.session_state.get("room_code")
    if not room_code:
        st.info("Create or join a room to start.")
        return

    # Auto-poll every 1s
    st_autorefresh(interval=2500, key=f"poll-{room_code}")

    room = db_get_room(room_code)
    if not room:
        st.error("Room not found or removed")
        return

    dealer_id = room_dealer_id(room)
    me = room["users"].get(user_id)
    if not me:
        st.warning("You are not in this room. Join from the sidebar.")
        return

    # Header
    st.subheader(f"Room {room['code']} — {room['status']}")
    st.caption(f"Dealer: {room['users'][dealer_id]['name'] if dealer_id else '-'} | Players: {len(room['order'])}")

    # My bankroll (private)
    st.markdown(f"**My bankroll:** {int(me.get('bankroll', room['settings']['starting_bankroll']))}")

    # LOBBY
    if room["status"] == "lobby":
        cols = st.columns(2)
        with cols[0]:
            st.markdown("**Players (hidden info)**")
            for uid in room["order"]:
                u = room["users"][uid]
                tag = " (me)" if uid == user_id else ""
                ready = "✅ Ready" if u.get("ready") else "⌛ Waiting"
                st.write(f"👤 {u['name']}{tag} — {ready}")
        with cols[1]:
            st.markdown("**Dealer**")
            if dealer_id:
                st.write(f"🂠 {room['users'][dealer_id]['name']}")
            else:
                st.write("(no dealer)")

        # My settings (private)
        st.divider()
        st.markdown("#### My settings")
        min_bet = int(room["settings"]["min_bet"])
        me["bet"] = int(st.number_input("My bet", min_value=min_bet, value=int(me.get("bet", min_bet)), step=min_bet))
        me["ready"] = st.toggle("I'm ready", value=bool(me.get("ready", False)))
        db_patch_room(room, {"users": room["users"]})

        # Dealer can start when at least one ready player exists
        if user_id == dealer_id:
            ready_players = [uid for uid in room["order"] if room["users"][uid].get("ready")]
            if len(ready_players) == 0:
                st.info("Wait for at least one player to be Ready.")
            if st.button("Start round", disabled=len(ready_players) == 0):
                # Robust start with optimistic retry
                started = False
                for _ in range(2):
                    latest = db_get_room(room_code)
                    if not latest:
                        st.error("Room missing"); break
                    # Recompute ready list from latest to avoid stale state
                    ready_now = [uid for uid in latest["order"] if latest["users"][uid].get("ready")]
                    if not ready_now:
                        st.warning("No ready players right now.")
                        break
                    latest["order"] = ready_now
                    reset_round(latest)
                    deal_initial(latest)
                    # Clear ready flags for this round
                    for uid in latest["order"] + ([dealer_id] if dealer_id else []):
                        latest["users"][uid]["ready"] = False
                    ok = db_patch_room(latest, {
                        "status": latest["status"],
                        "users": latest["users"],
                        "deck": latest["deck"],
                        "order": latest["order"]
                    })
                    if ok:
                        started = True
                        break
                    time.sleep(0.1)
                if started:
                    st.success("Round started!")
                    st.rerun()
                else:
                    st.error("Could not start the round due to a concurrent update. Please tap Start again.")

    # PLAYER ACTIONS
    elif room["status"] == "player_actions":
        # Dealer hand visibility: dealer sees own cards; others see facedown until showdown
        st.markdown("**Dealer**")
        dealer_cards = room["users"][dealer_id]["hand"]
        if user_id == dealer_id:
            st.write(f"{hand_to_str(dealer_cards)} — pts {hand_points(dealer_cards)}")
        else:
            st.write(facedown_str(len(dealer_cards)))

        # Players view
        st.markdown("**Players**")
        for uid in room["order"]:
            u = room["users"][uid]
            tag = " (me)" if uid == user_id else ""
            if uid == user_id:
                st.write(f"👤 {u['name']}{tag} — {hand_to_str(u['hand'])} — pts {hand_points(u['hand'])}")
            else:
                st.write(f"👤 {u['name']}{tag} — {facedown_str(len(u['hand']))} — {'✅ Acted' if u.get('acted') else '⌛ Waiting'}")

        # My actions (private)
        if me.get("role") == "player":
            pok, _ = is_pok(me["hand"]) if len(me["hand"]) == 2 else (False, 0)
            c1, c2 = st.columns(2)
            if c1.button("Hit", disabled=me.get("acted") or len(me["hand"]) != 2 or pok):
                latest = db_get_room(room_code)
                # draw a card for me only if still 2 cards
                if len(latest["users"][user_id]["hand"]) == 2:
                    latest["users"][user_id]["hand"].append(draw_card(latest))
                latest["users"][user_id]["acted"] = True
                if not db_patch_room(latest, {"users": latest["users"], "deck": latest["deck"]}):
                    st.warning("Conflict — refreshing")
                    st.rerun()
            if c2.button("Stand", disabled=me.get("acted") or pok):
                latest = db_get_room(room_code)
                latest["users"][user_id]["acted"] = True
                if not db_patch_room(latest, {"users": latest["users"]}):
                    st.warning("Conflict — refreshing")
                    st.rerun()

        # Dealer proceeds when all acted (auto-advance)
        latest = db_get_room(room_code)
        if latest and user_id == dealer_id and all_players_acted(latest):
            latest["status"] = "dealer_action"
            db_patch_room(latest, {"status": latest["status"]})
            st.rerun()

    # DEALER PHASE
    elif room["status"] == "dealer_action":
        st.markdown("**Dealer**")
        dh = room["users"][dealer_id]["hand"]
        if user_id == dealer_id:
            st.write(f"{hand_to_str(dh)} — pts {hand_points(dh)}")
        else:
            st.write(facedown_str(len(dh)))
        # Show players (hidden to others)
        st.markdown("**Players**")
        for uid in room["order"]:
            u = room["users"][uid]
            tag = " (me)" if uid == user_id else ""
            if uid == user_id:
                st.write(f"👤 {u['name']}{tag} — {hand_to_str(u['hand'])} — pts {hand_points(u['hand'])}")
            else:
                st.write(f"👤 {u['name']}{tag} — {facedown_str(len(u['hand']))}")

        # Dealer controls: manual Draw / Stand
        if user_id == dealer_id:
            c1, c2 = st.columns(2)
            if c1.button("Dealer: Draw"):
                latest = db_get_room(room_code)
                if not latest:
                    st.error("Room missing"); return
                # draw at most one extra card
                if len(latest["users"][dealer_id]["hand"]) < 3:
                    latest["users"][dealer_id]["hand"].append(draw_card(latest))
                    db_patch_room(latest, {"users": latest["users"], "deck": latest["deck"]})
                st.rerun()
            if c2.button("Dealer: Stand / Go to Showdown"):
                latest = db_get_room(room_code)
                if not latest:
                    st.error("Room missing"); return
                latest["status"] = "showdown"
                db_patch_room(latest, {"status": latest["status"]})
                st.rerun()

    # SHOWDOWN & SETTLEMENT
    elif room["status"] in ("showdown", "settlement"):
        latest = db_get_room(room_code)
        results = settle_room(latest)
        if not db_patch_room(latest, {"users": latest["users"], "status": latest["status"]}):
            st.warning("Conflict — refreshing")
            st.rerun()

        st.markdown("### Showdown")
        # Reveal all hands now
        dealer_cards = latest["users"][dealer_id]["hand"]
        st.write(f"Dealer: {hand_to_str(dealer_cards)} — pts {hand_points(dealer_cards)}")
        for uid in latest["order"]:
            u = latest["users"][uid]
            tag = " (me)" if uid == user_id else ""
            st.write(f"👤 {u['name']}{tag} — {hand_to_str(u['hand'])} — pts {hand_points(u['hand'])}")

        st.divider()
        # Private P&L: show only my outcome
        my_result = next((r for r in results if r["player_id"] == user_id), None)
        if my_result is not None:
            icon = "✅" if my_result["outcome"] == "win" else ("❌" if my_result["outcome"] == "lose" else "⚖️")
            st.success(f"My result: {icon} {my_result['outcome']} — payout {my_result['payout']}")
            st.info(f"My updated bankroll: {latest['users'][user_id]['bankroll']}")
        else:
            st.caption("Results are private per player.")

        if user_id == dealer_id and st.button("Back to Lobby"):
            # Clear hands & acted; keep bankrolls. Reset to lobby.
            latest = db_get_room(room_code)
            for uid in latest["order"] + ([dealer_id] if dealer_id else []):
                latest["users"][uid]["hand"] = []
                latest["users"][uid]["acted"] = False
                latest["users"][uid]["ready"] = False
            latest["status"] = "lobby"
            db_patch_room(latest, {"users": latest["users"], "status": latest["status"]})

if __name__ == "__main__":
    main()

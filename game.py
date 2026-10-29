"""Game rules and state. Flask routes call into this module; it calls ai.py
and drops to fallback.py whenever the model fails."""

import base64
import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
import zlib

from cryptography.fernet import Fernet, InvalidToken

import ai
import fallback

ACTIONS_BY_DIFFICULTY = {"easy": 18, "normal": 14, "hard": 10}
COST = {"question": 1, "search": 1, "hint": 2}
MAX_QUESTION = 300
MAX_REASONING = 600
MAX_THEME = 60
HISTORY_TURNS = 6  # question/answer pairs we send back to the model per suspect
MAX_GAMES = 200
TOKEN_TTL = 24 * 3600  # a sealed game stays valid for a day


class GameError(Exception):
    """Something the player did that we have to refuse. Carries an HTTP status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class CaseError(Exception):
    """The model produced a case we can't play."""


# ------------------------------------------------------------ validation


def clean_text(value, limit):
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()
    return re.sub(r"\s{2,}", " ", text)[:limit]


def _str(obj, key):
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"missing text field '{key}'")
    return value.strip()


def normalize_case(raw):
    """Check a generated case is actually solvable and tidy it up.

    Models get the shape right most of the time, but not always, so anything
    that would break the game (no culprit, two culprits, clues in places that
    don't exist) gets rejected and we ask again.
    """
    if not isinstance(raw, dict):
        raise CaseError("case is not an object")

    case = {k: _str(raw, k) for k in ("title", "setting", "intro", "crime", "solution")}
    victim = raw.get("victim")
    case["victim"] = {"name": _str(victim, "name"), "description": _str(victim, "description")}

    locations = raw.get("locations")
    if not isinstance(locations, list) or not 3 <= len(locations) <= 6:
        raise CaseError("need 3 to 6 locations")
    case["locations"] = [{"name": _str(l, "name"), "description": _str(l, "description")} for l in locations]
    by_lower = {l["name"].lower(): l["name"] for l in case["locations"]}
    if len(by_lower) != len(case["locations"]):
        raise CaseError("duplicate location names")

    suspects = raw.get("suspects")
    if not isinstance(suspects, list) or not 3 <= len(suspects) <= 5:
        raise CaseError("need 3 to 5 suspects")
    case["suspects"] = []
    for s in suspects:
        fields = ("id", "name", "role", "personality", "relationship", "alibi", "secret", "motive")
        entry = {k: _str(s, k) for k in fields}
        culprit = s.get("is_culprit")
        entry["is_culprit"] = culprit is True or str(culprit).lower() == "true"
        case["suspects"].append(entry)
    ids = {s["id"] for s in case["suspects"]}
    if len(ids) != len(case["suspects"]):
        raise CaseError("duplicate suspect ids")
    culprits = [s for s in case["suspects"] if s["is_culprit"]]
    if len(culprits) != 1:
        raise CaseError(f"expected exactly one culprit, got {len(culprits)}")

    clues = raw.get("clues")
    if not isinstance(clues, list) or not 4 <= len(clues) <= 8:
        raise CaseError("need 4 to 8 clues")
    case["clues"] = []
    for c in clues:
        loc = by_lower.get(_str(c, "location").lower())
        if not loc:
            raise CaseError(f"clue placed in unknown location '{c.get('location')}'")
        points = c.get("points_to")
        points = points.strip() if isinstance(points, str) else None
        if points in ("", "null", "none"):
            points = None
        if points is not None and points not in ids:
            raise CaseError(f"clue points to unknown suspect '{points}'")
        case["clues"].append({"id": _str(c, "id"), "location": loc, "text": _str(c, "text"), "points_to": points})
    if len({c["id"] for c in case["clues"]}) != len(case["clues"]):
        raise CaseError("duplicate clue ids")

    culprit_id = culprits[0]["id"]
    if sum(c["points_to"] == culprit_id for c in case["clues"]) < 2:
        raise CaseError("not enough clues point at the culprit to make it solvable")
    return case


# ------------------------------------------------------------ game state


class Game:
    def __init__(self, case, difficulty, source):
        self.id = uuid.uuid4().hex
        self.case = case
        self.difficulty = difficulty
        self.source = source  # "ai" or "offline"
        self.actions_left = ACTIONS_BY_DIFFICULTY[difficulty]
        self.found = []
        self.searched = []
        self.history = {s["id"]: [] for s in case["suspects"]}
        self.transcript = {s["id"]: [] for s in case["suspects"]}
        self.over = False
        self.result = None
        self.created = time.time()
        self.lock = threading.Lock()

    def suspect(self, suspect_id):
        for s in self.case["suspects"]:
            if s["id"] == suspect_id:
                return s
        raise GameError("That suspect isn't part of this case.", 404)

    def found_clues(self):
        return [c for c in self.case["clues"] if c["id"] in self.found]

    def spend(self, kind):
        if self.over:
            raise GameError("This case is closed. Start a new one.", 409)
        cost = COST[kind]
        if self.actions_left < cost:
            if self.actions_left == 0:
                raise GameError("You're out of time. Make your accusation.", 409)
            raise GameError(f"That needs {cost} actions and you only have {self.actions_left} left.", 409)
        self.actions_left -= cost

    def public(self):
        """What the browser gets to see. No culprit flags, secrets or unfound clues."""
        c = self.case
        state = {
            "id": self.id,
            "source": self.source,
            "difficulty": self.difficulty,
            "title": c["title"],
            "setting": c["setting"],
            "intro": c["intro"],
            "victim": c["victim"],
            "crime": c["crime"],
            "locations": [
                {**l, "searched": l["name"] in self.searched} for l in c["locations"]
            ],
            "suspects": [
                {k: s[k] for k in ("id", "name", "role", "personality")} for s in c["suspects"]
            ],
            "clues": [{"id": cl["id"], "location": cl["location"], "text": cl["text"]} for cl in self.found_clues()],
            "transcript": self.transcript,
            "actions_left": self.actions_left,
            "costs": COST,
            "over": self.over,
            "result": self.result,
        }
        return state


    # -- saving the whole game into a token the browser carries

    FIELDS = ("id", "case", "difficulty", "source", "actions_left", "found", "searched",
              "history", "transcript", "over", "result", "created")

    def to_dict(self):
        return {k: getattr(self, k) for k in self.FIELDS}

    @classmethod
    def from_dict(cls, data):
        g = cls.__new__(cls)
        for k in cls.FIELDS:
            setattr(g, k, data[k])
        g.lock = threading.Lock()
        return g


# On a serverless host like Vercel each request can land on a different
# instance, so memory alone can't hold a game. After every action the server
# encrypts the full game state and hands it to the browser, which sends it back
# with the next request. It's encrypted, not just signed, because the state
# holds the culprit and every secret.


def _fernet():
    secret = os.getenv("SESSION_SECRET", "").strip()
    if not secret:
        # no separate secret configured: derive one from the API key, which is
        # already secret and identical on every instance
        secret = "red-herring-session:" + os.getenv("GROQ_API_KEY", "").strip()
        if secret.endswith(":"):
            secret += _LOCAL_SECRET
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


_LOCAL_SECRET = uuid.uuid4().hex  # only used when there's no key at all (offline play)


def seal(game):
    raw = zlib.compress(json.dumps(game.to_dict(), separators=(",", ":")).encode())
    return _fernet().encrypt(raw).decode()


def unseal(token):
    try:
        raw = _fernet().decrypt(str(token).encode(), ttl=TOKEN_TTL)
        return Game.from_dict(json.loads(zlib.decompress(raw)))
    except (InvalidToken, ValueError, KeyError, TypeError, zlib.error):
        raise GameError("This saved case is invalid or has expired. Start a new one.", 404)


_games = {}
_games_lock = threading.Lock()


def _store(game):
    with _games_lock:
        if len(_games) >= MAX_GAMES:
            oldest = min(_games.values(), key=lambda g: g.created)
            del _games[oldest.id]
        _games[game.id] = game


def get_game(game_id, token=None):
    """Find a game. The browser's token wins, since this instance's memory may be stale."""
    if token:
        game = unseal(token)
        if game_id and game.id != str(game_id):
            raise GameError("That saved case doesn't match this one.", 400)
        _store(game)
        return game
    game = _games.get(str(game_id or ""))
    if not game:
        raise GameError("Case not found. It may have expired when the server restarted, so start a new one.", 404)
    return game


# ------------------------------------------------------------ actions


def new_game(theme, difficulty):
    difficulty = difficulty if difficulty in ACTIONS_BY_DIFFICULTY else "normal"
    theme = clean_text(theme, MAX_THEME) or "a classic country manor in the 1930s"

    notice = None
    if not ai.is_configured():
        notice = "No Groq API key found, so you're playing the built-in offline case."
    else:
        problem = None
        for _ in range(2):
            try:
                case = normalize_case(ai.generate_case(theme, difficulty))
            except (ai.AIError, CaseError) as exc:
                problem = str(exc)
                if isinstance(exc, ai.AIError) and "API key" in problem:
                    break
                continue
            game = Game(case, difficulty, "ai")
            _store(game)
            return game, None
        notice = f"The AI couldn't build a case ({problem}), so you're playing the built-in offline case."

    game = Game(copy.deepcopy(fallback.OFFLINE_CASE), difficulty, "offline")
    _store(game)
    return game, notice


def ask(game, suspect_id, question):
    question = clean_text(question, MAX_QUESTION + 1)
    if not question:
        raise GameError("Type a question first.")
    if len(question) > MAX_QUESTION:
        raise GameError(f"Keep questions under {MAX_QUESTION} characters.")
    suspect = game.suspect(suspect_id)

    with game.lock:
        game.spend("question")
        notice = None
        scripted = game.source == "offline"
        if not scripted:
            try:
                reply, mood = ai.interrogate(
                    game.case, suspect, game.history[suspect["id"]][-HISTORY_TURNS * 2:], question, game.found_clues()
                )
            except ai.AIError as exc:
                scripted = True
                notice = f"The AI didn't answer ({exc}), so this reply is scripted."
        if scripted:
            reply, mood = fallback.scripted_reply(game.case, suspect, question, game.found_clues())

        if mood not in ("calm", "nervous", "angry", "defensive", "cooperative", "sad"):
            mood = "calm"
        game.history[suspect["id"]] += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": reply},
        ]
        game.transcript[suspect["id"]] += [
            {"from": "you", "text": question},
            {"from": "them", "text": reply, "mood": mood, "scripted": scripted},
        ]
        return {"reply": reply, "mood": mood, "scripted": scripted, "notice": notice}


def search(game, location):
    names = {l["name"].lower(): l for l in game.case["locations"]}
    loc = names.get(clean_text(location, 100).lower())
    if not loc:
        raise GameError("That place isn't part of this case.", 404)

    with game.lock:
        if loc["name"] in game.searched:
            # repeat searches are free so a misclick doesn't cost anything
            if game.over:
                raise GameError("This case is closed. Start a new one.", 409)
            return {"new_clues": [], "message": f"You've already turned {loc['name']} upside down."}
        game.spend("search")
        game.searched.append(loc["name"])
        new = [c for c in game.case["clues"] if c["location"] == loc["name"] and c["id"] not in game.found]
        game.found += [c["id"] for c in new]
        message = f"You found {len(new)} clue{'s' if len(new) != 1 else ''} in {loc['name']}." if new else f"Nothing useful in {loc['name']}."
        return {
            "new_clues": [{"id": c["id"], "location": c["location"], "text": c["text"]} for c in new],
            "message": message,
        }


def get_hint(game):
    with game.lock:
        game.spend("hint")
        unsearched = [l["name"] for l in game.case["locations"] if l["name"] not in game.searched]
        if game.source == "ai":
            try:
                return {"hint": ai.hint(game.case, game.found_clues(), unsearched), "scripted": False, "notice": None}
            except ai.AIError as exc:
                notice = f"The AI didn't answer ({exc}), so this hint is scripted."
        else:
            notice = None
        return {"hint": fallback.scripted_hint(game.case, game.found, game.searched), "scripted": True, "notice": notice}


def accuse(game, suspect_id, reasoning):
    reasoning = clean_text(reasoning, MAX_REASONING)
    accused = game.suspect(suspect_id)

    with game.lock:
        if game.over:
            raise GameError("This case is already closed.", 409)
        culprit = next(s for s in game.case["suspects"] if s["is_culprit"])
        correct = accused["id"] == culprit["id"]
        culprit_clues = sum(c["points_to"] == culprit["id"] for c in game.found_clues())

        notice = None
        scripted = game.source == "offline"
        if not scripted:
            try:
                scene, feedback = ai.ending(game.case, accused, correct, reasoning, game.found_clues())
            except ai.AIError as exc:
                scripted = True
                notice = f"The AI didn't answer ({exc}), so the ending is scripted."
        if scripted:
            scene, feedback = fallback.scripted_ending(game.case, accused, correct, culprit_clues)

        score = (100 + 5 * game.actions_left + 10 * culprit_clues) if correct else 5 * culprit_clues
        game.over = True
        game.result = {
            "correct": correct,
            "accused": accused["name"],
            "culprit": culprit["name"],
            "scene": scene,
            "feedback": feedback,
            "solution": game.case["solution"],
            "score": score,
            "clues_found": len(game.found),
            "clues_total": len(game.case["clues"]),
            "scripted": scripted,
        }
        return {"result": game.result, "notice": notice}

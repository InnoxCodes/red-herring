import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai  # noqa: E402
import app as webapp  # noqa: E402
import fallback  # noqa: E402
import game  # noqa: E402


def ai_case():
    # the offline case doubles as a realistic "model output"
    case = copy.deepcopy(fallback.OFFLINE_CASE)
    case["title"] = "Generated Case"
    return case


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


@pytest.fixture
def fake_ai(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(ai, "generate_case", lambda theme, difficulty: ai_case())
    monkeypatch.setattr(ai, "interrogate", lambda *a: ("I was in the library, detective.", "nervous"))
    monkeypatch.setattr(ai, "hint", lambda *a: "Look at the shoes.")
    monkeypatch.setattr(ai, "ending", lambda *a: ("The culprit breaks down.", "Sharp work."))


# ------------------------------------------------------------ case validation


def test_offline_case_is_valid():
    case = game.normalize_case(copy.deepcopy(fallback.OFFLINE_CASE))
    assert sum(s["is_culprit"] for s in case["suspects"]) == 1


def test_rejects_two_culprits():
    case = ai_case()
    case["suspects"][0]["is_culprit"] = True
    with pytest.raises(game.CaseError, match="exactly one culprit"):
        game.normalize_case(case)


def test_rejects_clue_in_unknown_location():
    case = ai_case()
    case["clues"][0]["location"] = "The Moon"
    with pytest.raises(game.CaseError, match="unknown location"):
        game.normalize_case(case)


def test_location_match_ignores_case_and_null_strings():
    case = ai_case()
    case["clues"][0]["location"] = "the dome"
    case["clues"][2]["points_to"] = "null"
    out = game.normalize_case(case)
    assert out["clues"][0]["location"] == "The Dome"
    assert out["clues"][2]["points_to"] is None


def test_rejects_unsolvable_case():
    case = ai_case()
    for c in case["clues"]:
        c["points_to"] = None
    with pytest.raises(game.CaseError, match="solvable"):
        game.normalize_case(case)


def test_rejects_non_object():
    with pytest.raises(game.CaseError):
        game.normalize_case(["not", "a", "case"])


# ------------------------------------------------------------ new game


def test_no_key_uses_offline_case(no_key):
    g, notice = game.new_game("anything", "normal")
    assert g.source == "offline"
    assert "offline" in notice


def test_ai_case_used_when_key_present(fake_ai):
    g, notice = game.new_game("space station", "hard")
    assert g.source == "ai"
    assert g.case["title"] == "Generated Case"
    assert g.actions_left == 10
    assert notice is None


def test_bad_ai_output_falls_back(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    calls = []

    def broken(theme, difficulty):
        calls.append(1)
        return {"title": "half a case"}

    monkeypatch.setattr(ai, "generate_case", broken)
    g, notice = game.new_game("x", "normal")
    assert g.source == "offline"
    assert len(calls) == 2  # retried once before giving up
    assert "couldn't build a case" in notice


def test_unknown_difficulty_defaults_to_normal(no_key):
    g, _ = game.new_game("", "impossible")
    assert g.difficulty == "normal"


def test_public_state_hides_answers(no_key):
    g, _ = game.new_game("", "normal")
    pub = g.public()
    for s in pub["suspects"]:
        assert "is_culprit" not in s and "secret" not in s
    assert pub["clues"] == []
    assert "solution" not in pub


# ------------------------------------------------------------ actions


def test_ask_validates_input(no_key):
    g, _ = game.new_game("", "normal")
    with pytest.raises(game.GameError, match="Type a question"):
        game.ask(g, "s1", "   ")
    with pytest.raises(game.GameError, match="under 300"):
        game.ask(g, "s1", "x" * 301)
    with pytest.raises(game.GameError) as err:
        game.ask(g, "s99", "hello")
    assert err.value.status == 404
    assert g.actions_left == 14  # none of that cost anything


def test_ask_uses_ai_and_costs_one(fake_ai):
    g, _ = game.new_game("", "normal")
    out = game.ask(g, "s2", "Where were you?")
    assert out["reply"] == "I was in the library, detective."
    assert out["scripted"] is False
    assert g.actions_left == 13
    assert len(g.history["s2"]) == 2


def test_ask_falls_back_when_ai_fails(fake_ai, monkeypatch):
    def down(*a):
        raise ai.AIError("Groq rate limit hit")

    monkeypatch.setattr(ai, "interrogate", down)
    g, _ = game.new_game("", "normal")
    out = game.ask(g, "s3", "Where were you during the blackout?")
    assert out["scripted"] is True
    assert "rate limit" in out["notice"]
    assert "Kitchen" in out["reply"]


def test_offline_culprit_gets_rattled_by_their_clue(no_key):
    g, _ = game.new_game("", "normal")
    game.search(g, "Kitchen")
    out = game.ask(g, "s3", "Why is a bottle of sleeping draught with your name hidden in the kitchen?")
    assert out["mood"] == "nervous"


def test_search_reveals_clues_and_repeat_is_free(no_key):
    g, _ = game.new_game("", "normal")
    out = game.search(g, "generator shed")
    assert len(out["new_clues"]) == 2
    assert g.actions_left == 13
    again = game.search(g, "Generator Shed")
    assert again["new_clues"] == []
    assert g.actions_left == 13


def test_search_unknown_place(no_key):
    g, _ = game.new_game("", "normal")
    with pytest.raises(game.GameError):
        game.search(g, "Narnia")


def test_hint_costs_two(fake_ai):
    g, _ = game.new_game("", "normal")
    out = game.get_hint(g)
    assert out["hint"] == "Look at the shoes."
    assert g.actions_left == 12


def test_running_out_of_actions_blocks_everything_but_accuse(no_key):
    g, _ = game.new_game("", "hard")
    for _ in range(10):
        game.ask(g, "s1", "Where were you?")
    with pytest.raises(game.GameError, match="out of time"):
        game.ask(g, "s1", "One more?")
    with pytest.raises(game.GameError):
        game.get_hint(g)
    out = game.accuse(g, "s3", "")
    assert out["result"]["correct"] is True


def test_correct_accusation_scores_and_closes(fake_ai):
    g, _ = game.new_game("", "normal")
    game.search(g, "Kitchen")
    out = game.accuse(g, "s3", "The bottle has his name on it.")
    r = out["result"]
    assert r["correct"] and r["culprit"] == "Julian Marsh"
    assert r["score"] == 100 + 5 * 13 + 10
    with pytest.raises(game.GameError, match="closed"):
        game.ask(g, "s1", "hello?")
    with pytest.raises(game.GameError, match="already closed"):
        game.accuse(g, "s1", "")


def test_wrong_accusation(no_key):
    g, _ = game.new_game("", "normal")
    r = game.accuse(g, "s4", "the scarf")["result"]
    assert r["correct"] is False
    assert r["culprit"] == "Julian Marsh"
    assert r["score"] == 0


# ------------------------------------------------------------ ai.chat error handling


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_chat_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    responses = [FakeResp(429), FakeResp(200, {"choices": [{"message": {"content": '{"reply": "hi"}'}}]})]
    monkeypatch.setattr(ai.requests, "post", lambda *a, **kw: responses.pop(0))
    assert ai.chat([], json_mode=True) == {"reply": "hi"}


def test_chat_bad_key_does_not_retry(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    calls = []

    def post(*a, **kw):
        calls.append(1)
        return FakeResp(401)

    monkeypatch.setattr(ai.requests, "post", post)
    with pytest.raises(ai.AIError, match="API key"):
        ai.chat([])
    assert len(calls) == 1


def test_chat_broken_json_raises(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        ai.requests, "post", lambda *a, **kw: FakeResp(200, {"choices": [{"message": {"content": "not json {"}}]})
    )
    with pytest.raises(ai.AIError, match="broken JSON"):
        ai.chat([], json_mode=True)


def test_chat_network_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)

    def boom(*a, **kw):
        raise ai.requests.ConnectionError("down")

    monkeypatch.setattr(ai.requests, "post", boom)
    with pytest.raises(ai.AIError, match="could not reach Groq"):
        ai.chat([])


# ------------------------------------------------------------ http layer


@pytest.fixture
def client(no_key):
    return webapp.app.test_client()


def test_http_full_round(client):
    r = client.post("/api/new-case", json={"theme": "test", "difficulty": "easy"})
    assert r.status_code == 200
    case_id = r.get_json()["state"]["id"]

    r = client.post("/api/ask", json={"case_id": case_id, "suspect_id": "s1", "question": "Where were you?"})
    assert r.status_code == 200 and r.get_json()["actions_left"] == 17

    r = client.post("/api/accuse", json={"case_id": case_id, "suspect_id": "s3"})
    assert r.get_json()["result"]["correct"] is True


def test_http_errors_are_json(client):
    assert client.post("/api/ask", data="nope", content_type="text/plain").status_code == 400
    r = client.post("/api/ask", json={"case_id": "missing", "suspect_id": "s1", "question": "hi"})
    assert r.status_code == 404 and "not found" in r.get_json()["error"]
    r = client.get("/api/new-case")
    assert r.status_code == 405 and "error" in r.get_json()


# ------------------------------------------------------------ no dashes in AI text


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("keep it safe—I saw her", "keep it safe, I saw her"),
        ("from 10–11 pm", "from 10-11 pm"),
        ("and then—.", "and then."),
        ("—Wait", "Wait"),
        ("no dashes here", "no dashes here"),
    ],
)
def test_no_dashes(raw, clean):
    assert ai.no_dashes(raw) == clean


def test_chat_strips_dashes_from_model_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    content = '{"reply": "I was there\\u2014briefly", "clues": ["a\\u2013b"]}'
    monkeypatch.setattr(
        ai.requests, "post", lambda *a, **kw: FakeResp(200, {"choices": [{"message": {"content": content}}]})
    )
    out = ai.chat([], json_mode=True)
    assert out == {"reply": "I was there, briefly", "clues": ["a, b"]}
    assert "—" not in str(out) and "–" not in str(out)


# ------------------------------------------------------------ serverless: game lives in an encrypted token


def test_token_round_trip_keeps_everything(no_key):
    g, _ = game.new_game("", "normal")
    game.search(g, "Kitchen")
    game.ask(g, "s3", "Where were you?")
    back = game.unseal(game.seal(g))
    assert back.to_dict() == g.to_dict()


def test_token_hides_the_secrets(no_key):
    g, _ = game.new_game("", "normal")
    token = game.seal(g)
    assert "Julian" not in token and "culprit" not in token
    import base64
    assert b"Julian" not in base64.urlsafe_b64decode(token + "==")


def test_tampered_or_garbage_token_is_rejected(no_key):
    g, _ = game.new_game("", "normal")
    token = game.seal(g)
    broken = token[:-5] + ("A" if token[-5] != "A" else "B") + token[-4:]
    for bad in (broken, "not-a-token", ""):
        with pytest.raises(game.GameError):
            game.unseal(bad)


def test_token_wins_over_stale_memory(no_key):
    g, _ = game.new_game("", "normal")
    old = game.seal(g)
    game.search(g, "Kitchen")
    newer = game.seal(g)
    game._games.clear()
    assert game.get_game(g.id, newer).actions_left == 13
    with pytest.raises(game.GameError, match="doesn't match"):
        other, _ = game.new_game("", "normal")
        game.get_game(other.id, newer)


def test_http_works_when_every_request_hits_a_fresh_instance(client):
    def fresh():
        game._games.clear()  # what a cold serverless instance looks like

    r = client.post("/api/new-case", json={"theme": "x", "difficulty": "easy"}).get_json()
    token, case_id = r["token"], r["state"]["id"]
    for loc in ("Kitchen", "The Dome"):
        fresh()
        r = client.post("/api/search", json={"case_id": case_id, "location": loc, "token": token}).get_json()
        token = r["token"]
    fresh()
    r = client.post("/api/ask", json={"case_id": case_id, "suspect_id": "s3", "question": "Why?", "token": token}).get_json()
    assert r["actions_left"] == 15
    token = r["token"]
    fresh()
    state = client.post("/api/resume", json={"token": token}).get_json()["state"]
    assert len(state["clues"]) == 2 and state["actions_left"] == 15
    fresh()
    r = client.post("/api/accuse", json={"case_id": case_id, "suspect_id": "s3", "token": token}).get_json()
    assert r["result"]["correct"] is True

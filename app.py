import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import ai  # noqa: E402  (needs the env loaded first)
import game  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("red-herring")

app = Flask(__name__, static_folder="static", static_url_path="/static")


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise game.GameError("Expected a JSON body.")
    return data


@app.errorhandler(game.GameError)
def handle_game_error(exc):
    return jsonify(error=str(exc)), exc.status


@app.errorhandler(404)
def handle_404(_exc):
    return jsonify(error="Not found."), 404


@app.errorhandler(Exception)
def handle_crash(exc):
    if isinstance(exc, HTTPException):
        return jsonify(error=exc.description), exc.code
    log.exception("unhandled error")
    return jsonify(error="Something went wrong on the server. Try that again."), 500


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    return jsonify(ai_enabled=ai.is_configured(), model=ai.model_name() if ai.is_configured() else None)


@app.post("/api/new-case")
def new_case():
    data = body()
    g, notice = game.new_game(data.get("theme", ""), data.get("difficulty", "normal"))
    if notice:
        log.warning(notice)
    return jsonify(state=g.public(), notice=notice, token=game.seal(g))


@app.post("/api/resume")
def resume():
    g = game.get_game(None, body().get("token"))
    return jsonify(state=g.public(), token=game.seal(g))


def current_game(data):
    return game.get_game(data.get("case_id"), data.get("token"))


@app.post("/api/ask")
def ask():
    data = body()
    g = current_game(data)
    out = game.ask(g, data.get("suspect_id"), data.get("question"))
    return jsonify(**out, actions_left=g.actions_left, token=game.seal(g))


@app.post("/api/search")
def search():
    data = body()
    g = current_game(data)
    out = game.search(g, data.get("location"))
    return jsonify(**out, actions_left=g.actions_left, token=game.seal(g))


@app.post("/api/hint")
def hint():
    data = body()
    g = current_game(data)
    out = game.get_hint(g)
    return jsonify(**out, actions_left=g.actions_left, token=game.seal(g))


@app.post("/api/accuse")
def accuse():
    data = body()
    g = current_game(data)
    out = game.accuse(g, data.get("suspect_id"), data.get("reasoning", ""))
    return jsonify(**out, token=game.seal(g))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5050"))
    mode = f"Groq ({ai.model_name()})" if ai.is_configured() else "offline (no GROQ_API_KEY)"
    print(f"\n  Red Herring running at http://127.0.0.1:{port}  [AI: {mode}]\n")
    app.run(host="127.0.0.1", port=port, debug=False)

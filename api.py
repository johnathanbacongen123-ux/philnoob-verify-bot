from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import json

app = Flask(__name__)
CORS(app)

HCAPTCHA_SECRET = os.getenv("HCAPTCHA_SECRET")

# shared token store (in memory)
tokens = {}

@app.route("/verify", methods=["POST"])
def verify():
    data  = request.get_json()
    token = data.get("token")
    uid   = data.get("uid")

    if not token or not uid:
        return jsonify({"success": False, "error": "Missing token or uid"})

    # verify with hCaptcha
    r = requests.post("https://api.hcaptcha.com/siteverify", data={
        "secret":   HCAPTCHA_SECRET,
        "response": token
    })
    result = r.json()

    if result.get("success"):
        # store token for bot to pick up
        tokens[str(uid)] = token
        # save to file so bot can read it
        try:
            existing = {}
            if os.path.exists("captcha_tokens.json"):
                with open("captcha_tokens.json") as f:
                    existing = json.load(f)
            existing[str(uid)] = token
            with open("captcha_tokens.json", "w") as f:
                json.dump(existing, f)
        except Exception:
            pass
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Captcha failed"})

@app.route("/token/<uid>", methods=["GET"])
def get_token(uid):
    token = tokens.get(uid)
    if token:
        del tokens[uid]
        return jsonify({"success": True, "token": token})
    return jsonify({"success": False})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

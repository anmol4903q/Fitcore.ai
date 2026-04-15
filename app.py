import os
import json
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Initialize DB early (IMPORTANT)
DB_FILE = "fitcore_memory.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id TEXT PRIMARY KEY,
            profile_data TEXT
        )
    """)
    conn.commit()
    conn.close()

# 👉 THIS WAS YOUR MISSING PIECE
init_db()

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are FITCORE.AI, a smart and friendly mental and physical fitness assistant.

You remember everything the user has told you in past conversations — their name, age, fitness goals, health conditions, diet preferences, past workout plans, and progress.

Be practical, structured, and refer to past info naturally.
"""

def get_or_create_user(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not user:
        conn.execute(
            "INSERT INTO users (user_id, name, created_at) VALUES (?, ?, ?)",
            (user_id, "User", datetime.now().isoformat())
        )
        conn.commit()
    conn.close()

def save_message(user_id, role, content):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, role, content, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_history(user_id, limit=40):
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

def get_profile(user_id):
    conn = get_db()
    row = conn.execute("SELECT profile_data FROM user_profile WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if row:
        return json.loads(row["profile_data"])
    return {}

def save_profile(user_id, profile_data):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO user_profile (user_id, profile_data) VALUES (?, ?)",
        (user_id, json.dumps(profile_data))
    )
    conn.commit()
    conn.close()

def extract_profile_info(user_id, message):
    profile = get_profile(user_id)

    prompt = f"""Extract user info from message.

Current profile: {json.dumps(profile)}
Message: "{message}"

Return updated JSON only."""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        updated = json.loads(response.choices[0].message.content.strip())
        save_profile(user_id, updated)
    except:
        pass

def build_memory_context(user_id):
    profile = get_profile(user_id)
    if not profile:
        return ""
    lines = ["User info:"]
    for k, v in profile.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    message = data.get("message", "").strip()
    user_id = data.get("user_id", "default_user")

    if not message:
        return jsonify({"error": "Empty message"}), 400

    get_or_create_user(user_id)
    extract_profile_info(user_id, message)

    history = get_history(user_id)
    memory = build_memory_context(user_id)

    system = SYSTEM_PROMPT + ("\n\n" + memory if memory else "")

    save_message(user_id, "user", message)

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": message}
        ],
        max_tokens=1024,
    )

    reply = response.choices[0].message.content
    save_message(user_id, "assistant", reply)

    return jsonify({"reply": reply})

@app.route("/history", methods=["GET"])
def history():
    user_id = request.args.get("user_id", "default_user")
    return jsonify({"history": get_history(user_id, 100)})

@app.route("/clear", methods=["POST"])
def clear():
    user_id = request.json.get("user_id", "default_user")
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "cleared"})

@app.route("/profile", methods=["GET"])
def profile():
    user_id = request.args.get("user_id", "default_user")
    return jsonify(get_profile(user_id))

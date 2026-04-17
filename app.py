import os
import json
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            weight REAL,
            workout_done INTEGER DEFAULT 0,
            date TEXT NOT NULL,
            UNIQUE(user_id, date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            task_text TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            date TEXT NOT NULL,
            week_number INTEGER NOT NULL,
            day_type TEXT NOT NULL,
            UNIQUE(user_id, date, task_text)
        )
    """)
    conn.commit()
    conn.close()

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

def get_week_number(date_str=None):
    if date_str:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        d = datetime.now()
    return d.isocalendar()[1]

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

@app.route("/progress", methods=["POST"])
def save_progress():
    data = request.json
    user_id = data.get("user_id", "default_user")
    weight = data.get("weight")
    workout_done = 1 if data.get("workout_done") else 0
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO progress (user_id, weight, workout_done, date) VALUES (?, ?, ?, ?)",
        (user_id, weight, workout_done, today)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "date": today})

@app.route("/progress", methods=["GET"])
def get_progress():
    user_id = request.args.get("user_id", "default_user")
    conn = get_db()
    rows = conn.execute(
        "SELECT weight, workout_done, date FROM progress WHERE user_id = ? ORDER BY date ASC",
        (user_id,)
    ).fetchall()
    conn.close()
    return jsonify({"progress": [dict(r) for r in rows]})

# ── TASKS ──────────────────────────────────────────────

@app.route("/tasks", methods=["POST"])
def save_tasks():
    data = request.json
    user_id = data.get("user_id", "default_user")
    tasks = data.get("tasks", [])  # list of {task_text, day_type}
    today = datetime.now().strftime("%Y-%m-%d")
    week_number = get_week_number()

    conn = get_db()
    # Delete today's existing tasks for this user (replace)
    conn.execute("DELETE FROM tasks WHERE user_id = ? AND date = ?", (user_id, today))
    for t in tasks:
        conn.execute(
            """INSERT OR IGNORE INTO tasks (user_id, task_text, completed, date, week_number, day_type)
               VALUES (?, ?, 0, ?, ?, ?)""",
            (user_id, t["task_text"], today, week_number, t.get("day_type", "general"))
        )
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "count": len(tasks), "date": today, "week": week_number})

@app.route("/tasks", methods=["GET"])
def get_tasks():
    user_id = request.args.get("user_id", "default_user")
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    conn = get_db()
    rows = conn.execute(
        "SELECT id, task_text, completed, date, week_number, day_type FROM tasks WHERE user_id = ? AND date = ? ORDER BY id ASC",
        (user_id, date)
    ).fetchall()
    conn.close()
    return jsonify({"tasks": [dict(r) for r in rows], "date": date})

@app.route("/tasks/update", methods=["POST"])
def update_task():
    data = request.json
    user_id = data.get("user_id", "default_user")
    task_id = data.get("task_id")
    completed = 1 if data.get("completed") else 0
    today = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    # Only allow updating today's tasks
    task = conn.execute(
        "SELECT date FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)
    ).fetchone()
    if not task or task["date"] != today:
        conn.close()
        return jsonify({"error": "Cannot edit past tasks"}), 403

    conn.execute(
        "UPDATE tasks SET completed = ? WHERE id = ? AND user_id = ?",
        (completed, task_id, user_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "updated"})

@app.route("/tasks/history", methods=["GET"])
def tasks_history():
    user_id = request.args.get("user_id", "default_user")
    conn = get_db()

    # Completion % grouped by date
    by_date = conn.execute("""
        SELECT date,
               ROUND(100.0 * SUM(completed) / COUNT(*), 1) as pct
        FROM tasks
        WHERE user_id = ?
        GROUP BY date
        ORDER BY date ASC
    """, (user_id,)).fetchall()

    # Completion % grouped by week
    by_week = conn.execute("""
        SELECT week_number,
               ROUND(100.0 * SUM(completed) / COUNT(*), 1) as pct
        FROM tasks
        WHERE user_id = ?
        GROUP BY week_number
        ORDER BY week_number ASC
    """, (user_id,)).fetchall()

    # Average completion % grouped by day_type
    by_day_type = conn.execute("""
        SELECT day_type,
               ROUND(100.0 * SUM(completed) / COUNT(*), 1) as pct,
               COUNT(*) as total_tasks
        FROM tasks
        WHERE user_id = ?
        GROUP BY day_type
    """, (user_id,)).fetchall()

    # Weekly day breakdown (for the weekly planner view)
    weekly_days = conn.execute("""
        SELECT date, day_type, week_number,
               COUNT(*) as total,
               SUM(completed) as done
        FROM tasks
        WHERE user_id = ?
        GROUP BY date, day_type, week_number
        ORDER BY date ASC
    """, (user_id,)).fetchall()

    conn.close()
    return jsonify({
        "by_date": [dict(r) for r in by_date],
        "by_week": [dict(r) for r in by_week],
        "by_day_type": [dict(r) for r in by_day_type],
        "weekly_days": [dict(r) for r in weekly_days],
    })

@app.route("/tasks/ai-feedback", methods=["POST"])
def tasks_ai_feedback():
    data = request.json
    user_id = data.get("user_id", "default_user")
    completion_pct = data.get("completion_pct", 0)
    day_type = data.get("day_type", "workout")
    tasks_done = data.get("tasks_done", [])
    tasks_missed = data.get("tasks_missed", [])

    prompt = f"""The user just completed their {day_type} day tasks.
Completion rate: {completion_pct}%
Completed: {', '.join(tasks_done) if tasks_done else 'none'}
Missed: {', '.join(tasks_missed) if tasks_missed else 'none'}

Give 2-3 sentences of honest, motivating feedback. Be specific to the day type and completion rate. Keep it concise and energizing."""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200,
        )
        reply = response.choices[0].message.content
    except Exception as e:
        reply = "Great effort today! Keep showing up consistently and results will follow."

    return jsonify({"feedback": reply})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

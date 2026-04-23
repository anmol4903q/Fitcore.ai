import os
import json
import sqlite3
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq

# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins="*")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fitcore")

DB_FILE = os.environ.get("DB_FILE", "fitcore.db")

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # better concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _add_col_if_missing(conn, table, col, typedef):
    """Safe ALTER TABLE — silently skips if column already exists."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
    except Exception:
        pass


def init_db():
    conn = get_db()

    # ── Users ──────────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id      TEXT PRIMARY KEY,
            name         TEXT,
            email        TEXT UNIQUE,
            password_hash TEXT,
            provider     TEXT DEFAULT 'guest',
            created_at   TEXT
        )
    """)
    for col, td in [
        ("email",         "TEXT"),
        ("password_hash", "TEXT"),
        ("provider",      "TEXT DEFAULT 'guest'"),
    ]:
        _add_col_if_missing(conn, "users", col, td)

    # ── Messages (chat history) ────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            role       TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            page       TEXT    DEFAULT 'chat',
            timestamp  TEXT    NOT NULL
        )
    """)
    _add_col_if_missing(conn, "messages", "page", "TEXT DEFAULT 'chat'")

    # ── User profile (free-form JSON blob) ────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id      TEXT PRIMARY KEY,
            profile_data TEXT
        )
    """)

    # ── Progress (weight log) ──────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT    NOT NULL,
            weight       REAL,
            workout_done INTEGER DEFAULT 0,
            date         TEXT    NOT NULL,
            UNIQUE(user_id, date)
        )
    """)

    # ── Tasks (daily workout checklist) ───────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT    NOT NULL,
            task_text    TEXT    NOT NULL,
            completed    INTEGER DEFAULT 0,
            date         TEXT    NOT NULL,
            week_number  INTEGER NOT NULL,
            day_type     TEXT    NOT NULL DEFAULT 'general',
            badge        TEXT    DEFAULT 'workout',
            UNIQUE(user_id, date, task_text)
        )
    """)
    _add_col_if_missing(conn, "tasks", "badge", "TEXT DEFAULT 'workout'")

    # ── Workout plan (full structured plan per user) ───────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_plans (
            user_id     TEXT PRIMARY KEY,
            plan_json   TEXT NOT NULL,
            raw_text    TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)

    # ── Mood log ───────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mood_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            date      TEXT NOT NULL,
            score     INTEGER NOT NULL,
            emoji     TEXT,
            label     TEXT,
            note      TEXT,
            timestamp TEXT NOT NULL,
            UNIQUE(user_id, date)
        )
    """)

    # ── Journal ────────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            date      TEXT NOT NULL,
            text      TEXT NOT NULL,
            mood      TEXT,
            timestamp TEXT NOT NULL
        )
    """)

    # ── Food log ───────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            date      TEXT NOT NULL,
            name      TEXT NOT NULL,
            cals      REAL DEFAULT 0,
            protein   REAL DEFAULT 0,
            carbs     REAL DEFAULT 0,
            fat       REAL DEFAULT 0,
            timestamp TEXT NOT NULL
        )
    """)

    # ── Macro targets ──────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_targets (
            user_id  TEXT PRIMARY KEY,
            calories INTEGER,
            protein  INTEGER,
            carbs    INTEGER,
            fat      INTEGER,
            goal     TEXT DEFAULT 'maintain',
            updated_at TEXT
        )
    """)

    # ── Water tracker ──────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS water_log (
            user_id TEXT NOT NULL,
            date    TEXT NOT NULL,
            cups    INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, date)
        )
    """)

    conn.commit()
    conn.close()
    log.info("✅ Database initialised: %s", DB_FILE)


init_db()

# ─────────────────────────────────────────────
# GROQ CLIENT
# ─────────────────────────────────────────────
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are FITCORE.AI — a smart, friendly, and knowledgeable AI fitness and wellness coach.

You remember everything the user has shared: name, age, goals, workout history, diet preferences, progress, and past plans.

Your capabilities:
- Build personalised workout plans (Push/Pull/Legs/Core/Rest structure)
- Calculate macros, calories, and nutrition plans
- Provide mental wellness support, breathing guidance, and motivation
- Track and respond to user progress

Guidelines:
- Be concise but thorough — avoid walls of text
- Use markdown: **bold**, bullet points, headers for plans
- Always align advice with the user's stated goal
- Never provide advice outside fitness, nutrition, and mental wellness
- When generating workout plans, always include all 7 days (Monday–Sunday) with day types clearly labeled
"""

# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────
def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def get_week_number(date_str=None):
    d = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    return int(d.strftime("%V"))


def hash_password(password: str) -> str:
    salt = os.environ.get("PASSWORD_SALT", "fitcore_secure_salt_2024")
    return hashlib.sha256((password + salt).encode()).hexdigest()


def get_or_create_user(user_id: str):
    conn = get_db()
    user = conn.execute(
        "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not user:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, provider, created_at) VALUES (?, 'guest', ?)",
            (user_id, datetime.now().isoformat())
        )
        conn.commit()
    conn.close()


def get_profile(user_id: str) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT profile_data FROM user_profile WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    try:
        return json.loads(row["profile_data"]) if row else {}
    except Exception:
        return {}


def save_profile(user_id: str, profile: dict):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO user_profile (user_id, profile_data) VALUES (?, ?)",
        (user_id, json.dumps(profile))
    )
    conn.commit()
    conn.close()


def extract_profile_info(user_id: str, message: str):
    """Silently extract and save user info from their message using a lightweight LLM call."""
    profile = get_profile(user_id)
    prompt = (
        f"Extract any personal info (name, age, weight, height, goal, fitness level, "
        f"diet preference, health conditions) from this message.\n"
        f"Current profile: {json.dumps(profile)}\n"
        f"Message: \"{message[:400]}\"\n"
        f"Return ONLY a valid JSON object with updated fields. "
        f"Return the exact same JSON if nothing new is found."
    )
    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1,
        )
        text = res.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        updated = json.loads(text)
        if isinstance(updated, dict) and updated:
            save_profile(user_id, updated)
    except Exception:
        pass  # Never crash the chat endpoint due to profile extraction


def get_history(user_id: str, page: str = None, limit: int = 40) -> list:
    conn = get_db()
    if page:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE user_id = ? AND page = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, page, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_message(user_id: str, role: str, content: str, page: str = "chat"):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (user_id, role, content, page, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, role, content, page, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def build_memory_context(user_id: str) -> str:
    profile = get_profile(user_id)
    if not profile:
        return ""
    lines = ["User profile:"]
    for k, v in profile.items():
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# ── AUTH ENDPOINTS ──────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/auth/register", methods=["POST"])
def auth_register():
    data     = request.json or {}
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    name     = (data.get("name") or email.split("@")[0]).strip()
    uid      = (data.get("uid") or "").strip()

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400

    conn = get_db()
    existing = conn.execute(
        "SELECT user_id FROM users WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "An account with this email already exists. Please sign in."}), 409

    if not uid:
        uid = "e_" + secrets.token_hex(12)

    conn.execute(
        "INSERT INTO users (user_id, name, email, password_hash, provider, created_at) "
        "VALUES (?, ?, ?, ?, 'email', ?)",
        (uid, name, email, hash_password(password), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    log.info("New user registered: %s (%s)", uid, email)
    return jsonify({"uid": uid, "name": name, "email": email})


@app.route("/auth/login", methods=["POST"])
def auth_login():
    data     = request.json or {}
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    conn.close()

    if not user:
        return jsonify({"error": "No account found with this email. Please register first."}), 401
    if user["password_hash"] != hash_password(password):
        return jsonify({"error": "Incorrect password. Please try again."}), 401

    return jsonify({
        "uid":   user["user_id"],
        "name":  user["name"] or email.split("@")[0],
        "email": user["email"],
    })


@app.route("/auth/google", methods=["POST"])
def auth_google():
    """Register or return existing Google user."""
    data  = request.json or {}
    uid   = (data.get("uid") or data.get("user_id") or "").strip()
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not uid:
        return jsonify({"error": "Missing uid"}), 400

    conn = get_db()
    existing = conn.execute(
        "SELECT user_id FROM users WHERE user_id = ?", (uid,)
    ).fetchone()

    if not existing:
        # Check if this Google email maps to an existing email account
        if email:
            by_email = conn.execute(
                "SELECT user_id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if by_email:
                conn.close()
                return jsonify({"uid": by_email["user_id"], "name": name, "email": email})

        conn.execute(
            "INSERT INTO users (user_id, name, email, provider, created_at) VALUES (?, ?, ?, 'google', ?)",
            (uid, name, email, datetime.now().isoformat())
        )
        conn.commit()
        log.info("Google user registered: %s (%s)", uid, email)

    conn.close()
    return jsonify({"uid": uid, "name": name, "email": email})


# ═══════════════════════════════════════════════
# ── PROFILE ─────────────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/profile/save", methods=["POST"])
def profile_save():
    data    = request.json or {}
    user_id = data.get("user_id", "")
    profile = data.get("profile", {})
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    save_profile(user_id, profile)
    return jsonify({"status": "saved"})


@app.route("/profile/load", methods=["GET"])
def profile_load():
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    return jsonify({"profile": get_profile(user_id)})


# ═══════════════════════════════════════════════
# ── CHAT ────────────────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/chat", methods=["POST"])
def chat():
    data    = request.json or {}
    message = (data.get("message") or "").strip()
    user_id = data.get("user_id", "default_user")
    page    = data.get("page", "chat")

    if not message:
        return jsonify({"error": "Empty message"}), 400

    get_or_create_user(user_id)

    # Non-blocking profile extraction (lightweight)
    try:
        extract_profile_info(user_id, message)
    except Exception:
        pass

    # Build context-aware system prompt
    memory  = build_memory_context(user_id)
    system  = SYSTEM_PROMPT
    if memory:
        system += f"\n\n{memory}"

    # Override system prompt if page provides one
    system_override = data.get("system_override", "")
    if system_override:
        system = system_override + "\n\n" + memory if memory else system_override

    # Fetch DB history (last 30 messages for this page)
    db_history = get_history(user_id, page=page, limit=30)

    # Merge with client-provided history (client is source of truth for current session)
    client_history = data.get("history", [])
    history = client_history if client_history else db_history

    # Save user message
    save_message(user_id, "user", message, page)

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system},
                *history[-20:],
                {"role": "user", "content": message},
            ],
            max_tokens=1500,
            temperature=0.7,
        )
        reply = response.choices[0].message.content
    except Exception as e:
        log.error("Groq API error: %s", e)
        return jsonify({"error": "AI service unavailable", "reply": "I'm having trouble connecting to the AI right now. Please try again in a moment."}), 503

    save_message(user_id, "assistant", reply, page)
    return jsonify({"reply": reply})


@app.route("/history", methods=["GET"])
def history():
    user_id = request.args.get("user_id", "default_user")
    page    = request.args.get("page", None)
    return jsonify({"history": get_history(user_id, page=page, limit=100)})


@app.route("/clear", methods=["POST"])
def clear_history():
    data    = request.json or {}
    user_id = data.get("user_id", "default_user")
    page    = data.get("page", None)
    conn    = get_db()
    if page:
        conn.execute("DELETE FROM messages WHERE user_id = ? AND page = ?", (user_id, page))
    else:
        conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "cleared"})


# ═══════════════════════════════════════════════
# ── WORKOUT PLAN ────────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/plan/save", methods=["POST"])
def plan_save():
    """
    Save a structured workout plan to the DB.
    Body: { user_id, plan: { monday: [...], tuesday: [...], ... }, raw: "..." }
    """
    data    = request.json or {}
    user_id = data.get("user_id", "")
    plan    = data.get("plan", {})
    raw     = data.get("raw", "")

    if not user_id or not plan:
        return jsonify({"error": "user_id and plan required"}), 400

    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO workout_plans
           (user_id, plan_json, raw_text, created_at, updated_at)
           VALUES (?, ?, ?, COALESCE(
               (SELECT created_at FROM workout_plans WHERE user_id = ?), ?
           ), ?)""",
        (user_id, json.dumps(plan), raw, user_id, now, now)
    )
    conn.commit()
    conn.close()
    log.info("Plan saved for user %s (%d days)", user_id, len(plan))
    return jsonify({"status": "saved", "days": list(plan.keys())})


@app.route("/plan/load", methods=["GET"])
def plan_load():
    """Load the user's saved workout plan."""
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    row  = conn.execute(
        "SELECT plan_json, raw_text, created_at, updated_at FROM workout_plans WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"plan": None, "raw": None, "created_at": None})

    try:
        plan = json.loads(row["plan_json"])
    except Exception:
        plan = {}

    return jsonify({
        "plan":       plan,
        "raw":        row["raw_text"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    })


# ═══════════════════════════════════════════════
# ── TASKS ───────────────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/tasks", methods=["POST"])
def save_tasks():
    """
    Save today's tasks (replaces existing ones for today).
    Body: { user_id, tasks: [{ task_text, day_type, badge }] }
    """
    data    = request.json or {}
    user_id = data.get("user_id", "default_user")
    tasks   = data.get("tasks", [])
    date    = data.get("date", today_str())
    week    = get_week_number(date)

    get_or_create_user(user_id)

    conn = get_db()
    # Clear today's tasks first to allow fresh plan saves
    conn.execute(
        "DELETE FROM tasks WHERE user_id = ? AND date = ?", (user_id, date)
    )
    inserted = 0
    for t in tasks:
        text = (t.get("task_text") or "").strip()
        if not text:
            continue
        try:
            conn.execute(
                """INSERT OR IGNORE INTO tasks
                   (user_id, task_text, completed, date, week_number, day_type, badge)
                   VALUES (?, ?, 0, ?, ?, ?, ?)""",
                (
                    user_id, text, date, week,
                    t.get("day_type", "general"),
                    t.get("badge", "workout"),
                )
            )
            inserted += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "count": inserted, "date": date, "week": week})


@app.route("/tasks", methods=["GET"])
def get_tasks():
    """Get tasks for a specific date (default: today)."""
    user_id = request.args.get("user_id", "default_user")
    date    = request.args.get("date", today_str())

    conn = get_db()
    rows = conn.execute(
        """SELECT id, task_text, completed, date, week_number, day_type, badge
           FROM tasks WHERE user_id = ? AND date = ? ORDER BY id ASC""",
        (user_id, date)
    ).fetchall()
    conn.close()
    return jsonify({"tasks": [dict(r) for r in rows], "date": date})


@app.route("/tasks/update", methods=["POST"])
def update_task():
    """Toggle a task's completed state. Only allows editing today's tasks."""
    data      = request.json or {}
    user_id   = data.get("user_id", "default_user")
    task_id   = data.get("task_id")
    completed = 1 if data.get("completed") else 0

    if not task_id:
        return jsonify({"error": "task_id required"}), 400

    conn = get_db()
    task = conn.execute(
        "SELECT date FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, user_id)
    ).fetchone()

    if not task:
        conn.close()
        return jsonify({"error": "Task not found"}), 404

    # Allow updating tasks from the last 7 days (more forgiving)
    task_date = task["date"]
    cutoff    = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if task_date < cutoff:
        conn.close()
        return jsonify({"error": "Cannot edit tasks older than 7 days"}), 403

    conn.execute(
        "UPDATE tasks SET completed = ? WHERE id = ? AND user_id = ?",
        (completed, task_id, user_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "updated", "completed": completed})


@app.route("/tasks/history", methods=["GET"])
def tasks_history():
    """Aggregate task data for charts and weekly view."""
    user_id = request.args.get("user_id", "default_user")
    conn    = get_db()

    by_date = conn.execute("""
        SELECT
            date,
            COUNT(*) as total,
            SUM(completed) as done,
            ROUND(100.0 * SUM(completed) / MAX(COUNT(*), 1), 1) as pct
        FROM tasks WHERE user_id = ?
        GROUP BY date ORDER BY date ASC
        LIMIT 60
    """, (user_id,)).fetchall()

    by_week = conn.execute("""
        SELECT
            week_number,
            ROUND(100.0 * SUM(completed) / MAX(COUNT(*), 1), 1) as pct
        FROM tasks WHERE user_id = ?
        GROUP BY week_number ORDER BY week_number ASC
    """, (user_id,)).fetchall()

    by_day_type = conn.execute("""
        SELECT
            day_type,
            ROUND(100.0 * SUM(completed) / MAX(COUNT(*), 1), 1) as pct,
            COUNT(*) as total_tasks
        FROM tasks WHERE user_id = ?
        GROUP BY day_type
        ORDER BY pct DESC
    """, (user_id,)).fetchall()

    weekly_days = conn.execute("""
        SELECT
            date, day_type, week_number,
            COUNT(*) as total,
            SUM(completed) as done
        FROM tasks WHERE user_id = ?
        GROUP BY date, day_type, week_number
        ORDER BY date ASC
        LIMIT 60
    """, (user_id,)).fetchall()

    conn.close()
    return jsonify({
        "by_date":     [dict(r) for r in by_date],
        "by_week":     [dict(r) for r in by_week],
        "by_day_type": [dict(r) for r in by_day_type],
        "weekly_days": [dict(r) for r in weekly_days],
    })


@app.route("/tasks/ai-feedback", methods=["POST"])
def tasks_ai_feedback():
    """Generate personalised AI feedback after task completion."""
    data           = request.json or {}
    user_id        = data.get("user_id", "default_user")
    completion_pct = data.get("completion_pct", 0)
    day_type       = data.get("day_type", "workout")
    tasks_done     = data.get("tasks_done", [])
    tasks_missed   = data.get("tasks_missed", [])

    profile = get_profile(user_id)
    name    = profile.get("name", "")

    prompt = (
        f"The user{' (' + name + ')' if name else ''} just completed their {day_type} day.\n"
        f"Completion: {completion_pct}%\n"
        f"Done: {', '.join(tasks_done[:8]) if tasks_done else 'none'}\n"
        f"Missed: {', '.join(tasks_missed[:4]) if tasks_missed else 'none'}\n\n"
        f"Write 2–3 sentences of honest, specific, motivating feedback. "
        f"Reference the day type and completion rate. Be direct and energising. "
        f"If 100%, celebrate genuinely. If <70%, encourage without being soft."
    )

    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=250,
            temperature=0.8,
        )
        feedback = res.choices[0].message.content
    except Exception:
        feedback = "Great effort today! Every workout completed is progress compounding. Stay consistent and the results will follow. 💪"

    return jsonify({"feedback": feedback})


# ═══════════════════════════════════════════════
# ── PROGRESS (WEIGHT LOG) ───────────────────────
# ═══════════════════════════════════════════════

@app.route("/progress", methods=["POST"])
def save_progress():
    data         = request.json or {}
    user_id      = data.get("user_id", "default_user")
    weight       = data.get("weight")
    workout_done = 1 if data.get("workout_done") else 0
    date         = data.get("date", today_str())

    get_or_create_user(user_id)

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO progress (user_id, weight, workout_done, date) VALUES (?, ?, ?, ?)",
        (user_id, weight, workout_done, date)
    )
    conn.commit()
    conn.close()

    # Update log count
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM progress WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    conn.close()

    return jsonify({"status": "saved", "date": date, "total_entries": count})


@app.route("/progress", methods=["GET"])
def get_progress():
    user_id = request.args.get("user_id", "default_user")
    limit   = int(request.args.get("limit", 90))
    conn    = get_db()
    rows    = conn.execute(
        "SELECT weight, workout_done, date FROM progress WHERE user_id = ? "
        "ORDER BY date ASC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return jsonify({"progress": [dict(r) for r in rows]})


# ═══════════════════════════════════════════════
# ── MOOD LOG ────────────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/mood", methods=["POST"])
def save_mood():
    data    = request.json or {}
    user_id = data.get("user_id", "default_user")
    score   = data.get("score")
    emoji   = data.get("emoji", "")
    label   = data.get("label", "")
    note    = data.get("note", "")
    date    = data.get("date", today_str())

    if score is None:
        return jsonify({"error": "score required"}), 400

    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO mood_log
           (user_id, date, score, emoji, label, note, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, date, score, emoji, label, note, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "date": date})


@app.route("/mood", methods=["GET"])
def get_mood():
    user_id = request.args.get("user_id", "default_user")
    limit   = int(request.args.get("limit", 30))
    conn    = get_db()
    rows    = conn.execute(
        "SELECT * FROM mood_log WHERE user_id = ? ORDER BY date DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return jsonify({"mood_log": [dict(r) for r in rows]})


# ═══════════════════════════════════════════════
# ── JOURNAL ─────────────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/journal", methods=["POST"])
def save_journal():
    data    = request.json or {}
    user_id = data.get("user_id", "default_user")
    text    = (data.get("text") or "").strip()
    mood    = data.get("mood", "")
    date    = data.get("date", today_str())

    if not text:
        return jsonify({"error": "text required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO journal (user_id, date, text, mood, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, date, text, mood, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "date": date})


@app.route("/journal", methods=["GET"])
def get_journal():
    user_id = request.args.get("user_id", "default_user")
    limit   = int(request.args.get("limit", 20))
    conn    = get_db()
    rows    = conn.execute(
        "SELECT * FROM journal WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return jsonify({"entries": [dict(r) for r in rows]})


# ═══════════════════════════════════════════════
# ── FOOD LOG ────────────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/food", methods=["POST"])
def save_food():
    data    = request.json or {}
    user_id = data.get("user_id", "default_user")
    items   = data.get("items", [])
    date    = data.get("date", today_str())

    if not items:
        return jsonify({"error": "items required"}), 400

    conn = get_db()
    for item in items:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        conn.execute(
            """INSERT INTO food_log
               (user_id, date, name, cals, protein, carbs, fat, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, date, name,
                item.get("cals", 0),
                item.get("protein", 0),
                item.get("carbs", 0),
                item.get("fat", 0),
                datetime.now().isoformat(),
            )
        )
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "date": date})


@app.route("/food", methods=["GET"])
def get_food():
    user_id = request.args.get("user_id", "default_user")
    date    = request.args.get("date", today_str())
    conn    = get_db()
    rows    = conn.execute(
        "SELECT * FROM food_log WHERE user_id = ? AND date = ? ORDER BY timestamp ASC",
        (user_id, date)
    ).fetchall()
    conn.close()
    return jsonify({"items": [dict(r) for r in rows], "date": date})


@app.route("/food/<int:item_id>", methods=["DELETE"])
def delete_food(item_id):
    user_id = request.args.get("user_id", "default_user")
    conn    = get_db()
    conn.execute(
        "DELETE FROM food_log WHERE id = ? AND user_id = ?", (item_id, user_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})


# ═══════════════════════════════════════════════
# ── MACRO TARGETS ───────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/macros", methods=["POST"])
def save_macros():
    data     = request.json or {}
    user_id  = data.get("user_id", "default_user")
    calories = data.get("calories")
    protein  = data.get("protein")
    carbs    = data.get("carbs")
    fat      = data.get("fat")
    goal     = data.get("goal", "maintain")

    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO macro_targets
           (user_id, calories, protein, carbs, fat, goal, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, calories, protein, carbs, fat, goal, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "saved"})


@app.route("/macros", methods=["GET"])
def get_macros():
    user_id = request.args.get("user_id", "default_user")
    conn    = get_db()
    row     = conn.execute(
        "SELECT * FROM macro_targets WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return jsonify({"targets": dict(row) if row else None})


# ═══════════════════════════════════════════════
# ── WATER LOG ───────────────────────────────────
# ═══════════════════════════════════════════════

@app.route("/water", methods=["POST"])
def save_water():
    data    = request.json or {}
    user_id = data.get("user_id", "default_user")
    cups    = int(data.get("cups", 0))
    date    = data.get("date", today_str())

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO water_log (user_id, date, cups) VALUES (?, ?, ?)",
        (user_id, date, cups)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "cups": cups, "date": date})


@app.route("/water", methods=["GET"])
def get_water():
    user_id = request.args.get("user_id", "default_user")
    date    = request.args.get("date", today_str())
    conn    = get_db()
    row     = conn.execute(
        "SELECT cups FROM water_log WHERE user_id = ? AND date = ?",
        (user_id, date)
    ).fetchone()
    conn.close()
    return jsonify({"cups": row["cups"] if row else 0, "date": date})


# ═══════════════════════════════════════════════
# ── HEALTH / DIAGNOSTICS ────────────────────────
# ═══════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    """Health check — used by Render to detect a live server."""
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    return jsonify({
        "status":  "ok" if db_ok else "degraded",
        "db":      "ok" if db_ok else "error",
        "version": "2.0.0",
        "time":    datetime.now().isoformat(),
    }), 200 if db_ok else 503


@app.route("/stats", methods=["GET"])
def stats():
    """Basic stats for debugging."""
    user_id = request.args.get("user_id", "")
    conn    = get_db()

    result = {
        "total_users":    conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"],
        "total_messages": conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"],
        "total_tasks":    conn.execute("SELECT COUNT(*) as c FROM tasks").fetchone()["c"],
    }
    if user_id:
        result["user_messages"] = conn.execute(
            "SELECT COUNT(*) as c FROM messages WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        result["user_tasks"] = conn.execute(
            "SELECT COUNT(*) as c FROM tasks WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]

    conn.close()
    return jsonify(result)


# ─────────────────────────────────────────────
# ROOT
# ─────────────────────────────────────────────
@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "name":    "FITCORE.AI Backend",
        "version": "2.0.0",
        "status":  "running",
        "endpoints": [
            "POST /auth/register",
            "POST /auth/login",
            "POST /auth/google",
            "GET|POST /profile/load|save",
            "POST /chat",
            "GET  /history",
            "POST /clear",
            "GET|POST /plan/load|save",
            "GET|POST /tasks",
            "POST /tasks/update",
            "GET  /tasks/history",
            "POST /tasks/ai-feedback",
            "GET|POST /progress",
            "GET|POST /mood",
            "GET|POST /journal",
            "GET|POST /food",
            "DELETE /food/<id>",
            "GET|POST /macros",
            "GET|POST /water",
            "GET  /health",
            "GET  /stats",
        ]
    })


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    log.info("🚀 FITCORE.AI Backend starting on port %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)

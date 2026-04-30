"""
╔══════════════════════════════════════════════════════════════╗
║           FITCORE.AI — Production Backend v3.0               ║
║   Flask + Groq AI + SQLite (WAL) + Supabase-aware auth       ║
║                                                              ║
║  Endpoints:                                                   ║
║   Auth      : /auth/register /auth/login /auth/google        ║
║   Profile   : /profile/save  /profile/load                   ║
║   Chat      : /chat  /history  /clear                        ║
║   Plan      : /plan/save  /plan/load                         ║
║   Tasks     : /tasks  /tasks/update  /tasks/history          ║
║              /tasks/ai-feedback                              ║
║   Progress  : /progress (GET/POST)                           ║
║   Mood      : /mood (GET/POST)                               ║
║   Journal   : /journal (GET/POST)                            ║
║   Food      : /food (GET/POST)  /food/<id> (DELETE)          ║
║   Macros    : /macros (GET/POST)                             ║
║   Water     : /water (GET/POST)                              ║
║   Health    : /health  /stats                                ║
╚══════════════════════════════════════════════════════════════╝
"""

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

# ══════════════════════════════════════════════════════════════
# APP INITIALISATION
# ══════════════════════════════════════════════════════════════
app = Flask(__name__)

# Allow all origins — required for GitHub Pages → Render communication
CORS(app, origins="*", supports_credentials=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("fitcore")

DB_FILE = os.environ.get("DB_FILE", "fitcore.db")

# ══════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # concurrent reads + writes
    conn.execute("PRAGMA synchronous=NORMAL")    # safe + fast
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _add_col(conn, table, col, typedef):
    """Add column if it doesn't exist — safe to call on every boot."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
    except Exception:
        pass


def init_db():
    conn = get_db()

    # ── Users ──────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       TEXT PRIMARY KEY,
            name          TEXT,
            email         TEXT,
            password_hash TEXT,
            provider      TEXT DEFAULT 'guest',
            picture       TEXT,
            created_at    TEXT,
            last_seen     TEXT
        )
    """)
    for col, td in [
        ("email",         "TEXT"),
        ("password_hash", "TEXT"),
        ("provider",      "TEXT DEFAULT 'guest'"),
        ("picture",       "TEXT"),
        ("last_seen",     "TEXT"),
    ]:
        _add_col(conn, "users", col, td)

    # Unique email index (non-breaking if already exists)
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)"
        )
    except Exception:
        pass

    # ── Chat messages ──────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            role       TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            page       TEXT    DEFAULT 'chat',
            created_at TEXT    NOT NULL
        )
    """)
    _add_col(conn, "messages", "page", "TEXT DEFAULT 'chat'")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_msgs_user ON messages(user_id, page, id)"
    )

    # ── User profile (free-form JSON) ──────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id      TEXT PRIMARY KEY,
            profile_data TEXT DEFAULT '{}'
        )
    """)

    # ── Workout plans ──────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workout_plans (
            user_id    TEXT PRIMARY KEY,
            plan_json  TEXT NOT NULL,
            raw_text   TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # ── Daily tasks ────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT    NOT NULL,
            task_text   TEXT    NOT NULL,
            completed   INTEGER DEFAULT 0,
            date        TEXT    NOT NULL,
            week_number INTEGER NOT NULL,
            day_type    TEXT    DEFAULT 'general',
            badge       TEXT    DEFAULT 'workout',
            created_at  TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, date, task_text)
        )
    """)
    _add_col(conn, "tasks", "badge",      "TEXT DEFAULT 'workout'")
    _add_col(conn, "tasks", "created_at", "TEXT DEFAULT (datetime('now'))")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_user_date ON tasks(user_id, date)"
    )

    # ── Weight / workout progress ──────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT    NOT NULL,
            weight       REAL,
            workout_done INTEGER DEFAULT 0,
            date         TEXT    NOT NULL,
            created_at   TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, date)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_progress_user ON progress(user_id, date)"
    )

    # ── Mood log ───────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mood_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            date       TEXT NOT NULL,
            score      INTEGER NOT NULL,
            emoji      TEXT,
            label      TEXT,
            note       TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, date)
        )
    """)

    # ── Journal ────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            date       TEXT NOT NULL,
            text       TEXT NOT NULL,
            mood       TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── Food log ───────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            date       TEXT NOT NULL,
            name       TEXT NOT NULL,
            cals       REAL DEFAULT 0,
            protein    REAL DEFAULT 0,
            carbs      REAL DEFAULT 0,
            fat        REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_food_user_date ON food_log(user_id, date)"
    )

    # ── Macro targets ──────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_targets (
            user_id    TEXT PRIMARY KEY,
            calories   INTEGER,
            protein    INTEGER,
            carbs      INTEGER,
            fat        INTEGER,
            goal       TEXT DEFAULT 'maintain',
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── Water log ──────────────────────────────────────────────
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
    log.info("✅  Database ready: %s", DB_FILE)


init_db()

# ══════════════════════════════════════════════════════════════
# GROQ AI CLIENT
# ══════════════════════════════════════════════════════════════
_groq_api_key = os.environ.get("GROQ_API_KEY", "")
client = Groq(api_key=_groq_api_key) if _groq_api_key else None

SYSTEM_PROMPT = """You are FITCORE.AI — a smart, friendly, expert AI fitness and wellness coach.

You remember everything the user has shared across sessions: name, age, weight, goals, fitness level, dietary preferences, past plans, health conditions, and progress.

Your specialities:
• Build personalised workout plans (Push/Pull/Legs/Core/Rest structure, 7-day)
• Calculate macros using Mifflin-St Jeor formula
• Provide evidence-based nutrition and supplement guidance
• Support mental wellness: stress, sleep, anxiety, motivation
• Track and respond to user progress and consistency

Response guidelines:
• Be concise but thorough — no unnecessary filler
• Use markdown: **bold**, bullet points, headers for plans and lists
• Always align advice with the user's stated goal and profile
• When generating weekly plans, always label every day clearly:
  "Monday — Push Day", "Tuesday — Pull Day", etc.
• Only discuss fitness, nutrition, and mental wellness topics
• Never give medical diagnoses or replace professional medical advice
"""

# ══════════════════════════════════════════════════════════════
# UTILITIES
# ══════════════════════════════════════════════════════════════
def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def now_iso():
    return datetime.now().isoformat()


def get_week_number(date_str=None):
    d = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    return int(d.strftime("%V"))


def hash_password(password: str) -> str:
    salt = os.environ.get("PASSWORD_SALT", "fitcore_secure_salt_v2_2024")
    return hashlib.sha256((password + salt).encode()).hexdigest()


def safe_json(text: str) -> dict:
    """Parse JSON from LLM output, stripping markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text  = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        return json.loads(text)
    except Exception:
        return {}


# ──────────────────────────────────────────────
# User helpers
# ──────────────────────────────────────────────
def get_or_create_user(user_id: str):
    conn = get_db()
    exists = conn.execute(
        "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, provider, created_at, last_seen) "
            "VALUES (?, 'guest', ?, ?)",
            (user_id, now_iso(), now_iso())
        )
        conn.commit()
    else:
        conn.execute(
            "UPDATE users SET last_seen = ? WHERE user_id = ?",
            (now_iso(), user_id)
        )
        conn.commit()
    conn.close()


# ──────────────────────────────────────────────
# Profile helpers
# ──────────────────────────────────────────────
def get_profile(user_id: str) -> dict:
    conn = get_db()
    row  = conn.execute(
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
        (user_id, json.dumps(profile, ensure_ascii=False))
    )
    conn.commit()
    conn.close()


def extract_and_save_profile(user_id: str, message: str):
    """Silently extract user info from message and update profile."""
    if not client:
        return
    profile = get_profile(user_id)
    prompt = (
        f"Extract personal fitness info from this message.\n"
        f"Existing profile: {json.dumps(profile)}\n"
        f"Message: \"{message[:400]}\"\n"
        f"Return ONLY a valid JSON object. Include all existing fields plus any new ones found. "
        f"Return the same JSON unchanged if nothing new is found."
    )
    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.1,
        )
        updated = safe_json(res.choices[0].message.content)
        if isinstance(updated, dict) and updated:
            save_profile(user_id, updated)
    except Exception:
        pass  # Never crash main chat for profile extraction


def build_memory_context(user_id: str) -> str:
    profile = get_profile(user_id)
    if not profile:
        return ""
    lines = ["User profile:"]
    for k, v in profile.items():
        if v:
            lines.append(f"  · {k}: {v}")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# Message history helpers
# ──────────────────────────────────────────────
def get_history(user_id: str, page: str = None, limit: int = 30) -> list:
    conn = get_db()
    if page:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE user_id = ? AND page = ? ORDER BY id DESC LIMIT ?",
            (user_id, page, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_message(user_id: str, role: str, content: str, page: str = "chat"):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (user_id, role, content, page, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, role, content, page, now_iso())
    )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════
# ── AUTH ENDPOINTS ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

@app.route("/auth/register", methods=["POST"])
def auth_register():
    data     = request.json or {}
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "")
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
        "INSERT INTO users (user_id, name, email, password_hash, provider, created_at, last_seen) "
        "VALUES (?, ?, ?, ?, 'email', ?, ?)",
        (uid, name, email, hash_password(password), now_iso(), now_iso())
    )
    conn.commit()
    conn.close()
    log.info("✅  Registered: %s (%s)", uid, email)
    return jsonify({"uid": uid, "name": name, "email": email})


@app.route("/auth/login", methods=["POST"])
def auth_login():
    data     = request.json or {}
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "")

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

    # Update last seen
    conn = get_db()
    conn.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now_iso(), user["user_id"]))
    conn.commit(); conn.close()

    log.info("✅  Login: %s", email)
    return jsonify({
        "uid":     user["user_id"],
        "name":    user["name"] or email.split("@")[0],
        "email":   user["email"],
        "picture": user["picture"],
    })


@app.route("/auth/google", methods=["POST"])
def auth_google():
    """Register or return existing Google-authenticated user."""
    data  = request.json or {}
    uid   = (data.get("uid") or data.get("user_id") or "").strip()
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    pic   = (data.get("picture") or "").strip()

    if not uid:
        return jsonify({"error": "Missing uid"}), 400

    conn = get_db()
    existing = conn.execute(
        "SELECT user_id FROM users WHERE user_id = ?", (uid,)
    ).fetchone()

    if not existing:
        # Merge with existing email account if email matches
        if email:
            by_email = conn.execute(
                "SELECT user_id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if by_email:
                # Update provider and picture, return original uid
                conn.execute(
                    "UPDATE users SET provider='google', picture=?, last_seen=? WHERE email=?",
                    (pic, now_iso(), email)
                )
                conn.commit(); conn.close()
                return jsonify({"uid": by_email["user_id"], "name": name, "email": email, "picture": pic})

        conn.execute(
            "INSERT INTO users (user_id, name, email, provider, picture, created_at, last_seen) "
            "VALUES (?, ?, ?, 'google', ?, ?, ?)",
            (uid, name, email, pic, now_iso(), now_iso())
        )
        conn.commit()
        log.info("✅  Google user: %s (%s)", uid, email)
    else:
        # Update picture and last seen
        conn.execute(
            "UPDATE users SET picture=?, last_seen=? WHERE user_id=?",
            (pic, now_iso(), uid)
        )
        conn.commit()

    conn.close()
    return jsonify({"uid": uid, "name": name, "email": email, "picture": pic})


# ══════════════════════════════════════════════════════════════
# ── PROFILE ───────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════
# ── CHAT ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

@app.route("/chat", methods=["POST"])
def chat():
    data    = request.json or {}
    message = (data.get("message") or "").strip()
    user_id = data.get("user_id", "default_user")
    page    = data.get("page", "chat")

    if not message:
        return jsonify({"error": "Empty message"}), 400

    if not client:
        return jsonify({
            "reply": "⚠️ AI service is not configured. Please set the GROQ_API_KEY environment variable on your Render backend."
        }), 503

    get_or_create_user(user_id)

    # Profile extraction (non-blocking, best-effort)
    try:
        extract_and_save_profile(user_id, message)
    except Exception:
        pass

    # Build system prompt with memory context
    memory = build_memory_context(user_id)
    system = SYSTEM_PROMPT
    if memory:
        system += f"\n\n{memory}"

    # Allow page-specific system override (e.g., mental.html mode switching)
    system_override = (data.get("system_override") or "").strip()
    if system_override:
        system = system_override
        if memory:
            system += f"\n\n{memory}"

    # Use client-provided history if available, otherwise load from DB
    client_history = data.get("history", [])
    history = client_history if client_history else get_history(user_id, page=page, limit=30)

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
            temperature=0.72,
        )
        reply = response.choices[0].message.content
    except Exception as e:
        log.error("Groq API error: %s", e)
        return jsonify({
            "error": "AI service temporarily unavailable",
            "reply": "I'm having trouble reaching the AI service right now. Please wait a moment and try again."
        }), 503

    save_message(user_id, "assistant", reply, page)
    return jsonify({"reply": reply})


@app.route("/history", methods=["GET"])
def history_endpoint():
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
    conn.commit(); conn.close()
    return jsonify({"status": "cleared"})


# ══════════════════════════════════════════════════════════════
# ── WORKOUT PLAN ──────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

@app.route("/plan/save", methods=["POST"])
def plan_save():
    """
    Save a structured workout plan.
    Body: { user_id, plan: { monday: [...], ... }, raw: "..." }
    """
    data    = request.json or {}
    user_id = data.get("user_id", "")
    plan    = data.get("plan", {})
    raw     = data.get("raw", "")

    if not user_id or not plan:
        return jsonify({"error": "user_id and plan required"}), 400

    now = now_iso()
    conn = get_db()

    # Preserve original created_at if plan already exists
    existing = conn.execute(
        "SELECT created_at FROM workout_plans WHERE user_id = ?", (user_id,)
    ).fetchone()
    created = existing["created_at"] if existing else now

    conn.execute(
        "INSERT OR REPLACE INTO workout_plans "
        "(user_id, plan_json, raw_text, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, json.dumps(plan, ensure_ascii=False), raw, created, now)
    )
    conn.commit(); conn.close()
    log.info("📋  Plan saved for %s (%d days)", user_id, len(plan))
    return jsonify({"status": "saved", "days": list(plan.keys()), "updated_at": now})


@app.route("/plan/load", methods=["GET"])
def plan_load():
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    conn = get_db()
    row  = conn.execute(
        "SELECT plan_json, raw_text, created_at, updated_at "
        "FROM workout_plans WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"plan": None, "raw": None, "created_at": None, "updated_at": None})

    try:
        plan = json.loads(row["plan_json"])
    except Exception:
        plan = {}

    return jsonify({
        "plan":       plan,
        "raw":        row["raw_text"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    })


# ══════════════════════════════════════════════════════════════
# ── TASKS ─────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

@app.route("/tasks", methods=["POST"])
def save_tasks():
    """
    Replace today's tasks for a user.
    Body: { user_id, tasks: [{ task_text, day_type, badge }], date? }
    """
    data    = request.json or {}
    user_id = data.get("user_id", "default_user")
    tasks   = data.get("tasks", [])
    date    = data.get("date", today_str())
    week    = get_week_number(date)

    get_or_create_user(user_id)

    conn = get_db()
    # Delete today's tasks first so regenerating gives a clean slate
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
                "INSERT OR IGNORE INTO tasks "
                "(user_id, task_text, completed, date, week_number, day_type, badge) "
                "VALUES (?, ?, 0, ?, ?, ?, ?)",
                (
                    user_id, text, date, week,
                    t.get("day_type", "general"),
                    t.get("badge", "workout"),
                )
            )
            inserted += 1
        except Exception:
            pass

    conn.commit(); conn.close()
    return jsonify({"status": "saved", "count": inserted, "date": date, "week": week})


@app.route("/tasks", methods=["GET"])
def get_tasks():
    """Get tasks for a specific date (default: today)."""
    user_id = request.args.get("user_id", "default_user")
    date    = request.args.get("date", today_str())

    conn = get_db()
    rows = conn.execute(
        "SELECT id, task_text, completed, date, week_number, day_type, badge "
        "FROM tasks WHERE user_id = ? AND date = ? ORDER BY id ASC",
        (user_id, date)
    ).fetchall()
    conn.close()
    return jsonify({"tasks": [dict(r) for r in rows], "date": date})


@app.route("/tasks/update", methods=["POST"])
def update_task():
    """Toggle a single task's completed state."""
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

    # Allow edits for tasks up to 7 days old
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    if task["date"] < cutoff:
        conn.close()
        return jsonify({"error": "Cannot edit tasks older than 7 days"}), 403

    conn.execute(
        "UPDATE tasks SET completed = ? WHERE id = ? AND user_id = ?",
        (completed, task_id, user_id)
    )
    conn.commit(); conn.close()
    return jsonify({"status": "updated", "completed": completed})


@app.route("/tasks/history", methods=["GET"])
def tasks_history():
    """Aggregated task stats for charts and weekly view."""
    user_id = request.args.get("user_id", "default_user")
    conn    = get_db()

    by_date = conn.execute("""
        SELECT
            date,
            COUNT(*)          AS total,
            SUM(completed)    AS done,
            ROUND(100.0 * SUM(completed) / MAX(COUNT(*), 1), 1) AS pct
        FROM tasks WHERE user_id = ?
        GROUP BY date ORDER BY date ASC LIMIT 90
    """, (user_id,)).fetchall()

    by_week = conn.execute("""
        SELECT
            week_number,
            ROUND(100.0 * SUM(completed) / MAX(COUNT(*), 1), 1) AS pct
        FROM tasks WHERE user_id = ?
        GROUP BY week_number ORDER BY week_number ASC
    """, (user_id,)).fetchall()

    by_day_type = conn.execute("""
        SELECT
            day_type,
            ROUND(100.0 * SUM(completed) / MAX(COUNT(*), 1), 1) AS pct,
            COUNT(*) AS total_tasks
        FROM tasks WHERE user_id = ?
        GROUP BY day_type ORDER BY pct DESC
    """, (user_id,)).fetchall()

    weekly_days = conn.execute("""
        SELECT
            date, day_type, week_number,
            COUNT(*)       AS total,
            SUM(completed) AS done
        FROM tasks WHERE user_id = ?
        GROUP BY date, day_type, week_number
        ORDER BY date ASC LIMIT 90
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
    """Generate AI coach feedback after workout completion."""
    data           = request.json or {}
    user_id        = data.get("user_id", "default_user")
    completion_pct = data.get("completion_pct", 0)
    day_type       = data.get("day_type", "workout")
    tasks_done     = data.get("tasks_done", [])
    tasks_missed   = data.get("tasks_missed", [])

    profile = get_profile(user_id)
    name    = profile.get("name", "")

    prompt = (
        f"The user{f' ({name})' if name else ''} just finished their {day_type} day.\n"
        f"Completion: {completion_pct}%\n"
        f"Completed: {', '.join(tasks_done[:10]) if tasks_done else 'none'}\n"
        f"Missed: {', '.join(tasks_missed[:5]) if tasks_missed else 'none'}\n\n"
        "Write 2–3 sentences of honest, specific, energising feedback. "
        "If 100%, celebrate genuinely. If <70%, encourage without sugarcoating. "
        "Reference the day type and completion rate. Be direct and motivating."
    )

    fallback = "Great effort today! Every session completed is progress compounding. Stay consistent and the results will follow. 💪"

    if not client:
        return jsonify({"feedback": fallback})

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
        feedback = fallback

    return jsonify({"feedback": feedback})


# ══════════════════════════════════════════════════════════════
# ── PROGRESS (WEIGHT LOG) ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════

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
        "INSERT OR REPLACE INTO progress (user_id, weight, workout_done, date) "
        "VALUES (?, ?, ?, ?)",
        (user_id, weight, workout_done, date)
    )
    conn.commit()

    count = conn.execute(
        "SELECT COUNT(*) AS c FROM progress WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    conn.close()

    return jsonify({"status": "saved", "date": date, "total_entries": count})


@app.route("/progress", methods=["GET"])
def get_progress():
    user_id = request.args.get("user_id", "default_user")
    limit   = min(int(request.args.get("limit", 90)), 365)
    conn    = get_db()
    rows    = conn.execute(
        "SELECT weight, workout_done, date FROM progress "
        "WHERE user_id = ? ORDER BY date ASC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return jsonify({"progress": [dict(r) for r in rows]})


# ══════════════════════════════════════════════════════════════
# ── MOOD LOG ──────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

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
        "INSERT OR REPLACE INTO mood_log "
        "(user_id, date, score, emoji, label, note) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, date, score, emoji, label, note)
    )
    conn.commit(); conn.close()
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


# ══════════════════════════════════════════════════════════════
# ── JOURNAL ───────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

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
        "INSERT INTO journal (user_id, date, text, mood) VALUES (?, ?, ?, ?)",
        (user_id, date, text, mood)
    )
    conn.commit(); conn.close()
    return jsonify({"status": "saved", "date": date})


@app.route("/journal", methods=["GET"])
def get_journal():
    user_id = request.args.get("user_id", "default_user")
    limit   = int(request.args.get("limit", 20))
    conn    = get_db()
    rows    = conn.execute(
        "SELECT * FROM journal WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return jsonify({"entries": [dict(r) for r in rows]})


# ══════════════════════════════════════════════════════════════
# ── FOOD LOG ──────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

@app.route("/food", methods=["POST"])
def save_food():
    data    = request.json or {}
    user_id = data.get("user_id", "default_user")
    items   = data.get("items", [])
    date    = data.get("date", today_str())

    if not items:
        return jsonify({"error": "items required"}), 400

    conn = get_db()
    inserted = 0
    for item in items:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        conn.execute(
            "INSERT INTO food_log (user_id, date, name, cals, protein, carbs, fat) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, date, name,
                item.get("cals", 0),
                item.get("protein", 0),
                item.get("carbs", 0),
                item.get("fat", 0),
            )
        )
        inserted += 1
    conn.commit(); conn.close()
    return jsonify({"status": "saved", "count": inserted, "date": date})


@app.route("/food", methods=["GET"])
def get_food():
    user_id = request.args.get("user_id", "default_user")
    date    = request.args.get("date", today_str())
    conn    = get_db()
    rows    = conn.execute(
        "SELECT * FROM food_log WHERE user_id = ? AND date = ? ORDER BY created_at ASC",
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
    conn.commit(); conn.close()
    return jsonify({"status": "deleted"})


# ══════════════════════════════════════════════════════════════
# ── MACRO TARGETS ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

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
        "INSERT OR REPLACE INTO macro_targets "
        "(user_id, calories, protein, carbs, fat, goal, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, calories, protein, carbs, fat, goal, now_iso())
    )
    conn.commit(); conn.close()
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


# ══════════════════════════════════════════════════════════════
# ── WATER LOG ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

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
    conn.commit(); conn.close()
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


# ══════════════════════════════════════════════════════════════
# ── HEALTH & DIAGNOSTICS ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    """Render uses this to detect a live server (keep-alive ping)."""
    db_ok  = False
    ai_ok  = client is not None
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception:
        pass

    status = "ok" if (db_ok and ai_ok) else ("degraded" if db_ok else "error")
    return jsonify({
        "status":  status,
        "db":      "ok" if db_ok else "error",
        "ai":      "ok" if ai_ok else "not_configured",
        "version": "3.0.0",
        "time":    now_iso(),
    }), 200 if db_ok else 503


@app.route("/stats", methods=["GET"])
def stats():
    """Usage statistics for debugging."""
    user_id = request.args.get("user_id", "")
    conn    = get_db()
    result  = {
        "total_users":    conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
        "total_messages": conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"],
        "total_tasks":    conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"],
        "total_progress": conn.execute("SELECT COUNT(*) AS c FROM progress").fetchone()["c"],
        "total_plans":    conn.execute("SELECT COUNT(*) AS c FROM workout_plans").fetchone()["c"],
    }
    if user_id:
        result["user"] = {
            "messages": conn.execute("SELECT COUNT(*) AS c FROM messages WHERE user_id=?", (user_id,)).fetchone()["c"],
            "tasks":    conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE user_id=?",    (user_id,)).fetchone()["c"],
            "progress": conn.execute("SELECT COUNT(*) AS c FROM progress WHERE user_id=?", (user_id,)).fetchone()["c"],
        }
    conn.close()
    return jsonify(result)


# ══════════════════════════════════════════════════════════════
# ── ROOT ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "name":    "FITCORE.AI Backend",
        "version": "3.0.0",
        "status":  "running",
        "ai":      "configured" if client else "not_configured — set GROQ_API_KEY",
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


# ══════════════════════════════════════════════════════════════
# ── ENTRY POINT ───────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    log.info("🚀  FITCORE.AI Backend v3.0 starting on port %d", port)
    log.info("🤖  AI: %s", "configured" if client else "NOT CONFIGURED — set GROQ_API_KEY")
    log.info("🗄️   DB: %s", DB_FILE)
    app.run(host="0.0.0.0", port=port, debug=debug)

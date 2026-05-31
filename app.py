
import os
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq
from supabase import create_client, Client

# ══════════════════════════════════════════════════════════════
# APP SETUP
# ══════════════════════════════════════════════════════════════
app = Flask(__name__)
CORS(app, origins="*")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("fitcore")

# ══════════════════════════════════════════════════════════════
# SUPABASE CLIENT
# ══════════════════════════════════════════════════════════════
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")  # service_role key

if not SUPABASE_URL or not SUPABASE_KEY:
    log.warning("⚠️  SUPABASE_URL or SUPABASE_SERVICE_KEY not set!")

_supabase: Client = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY
    else None
)

def sb() -> Client:
    if not _supabase:
        raise RuntimeError("Supabase not configured — set SUPABASE_URL and SUPABASE_SERVICE_KEY on Render")
    return _supabase

# ══════════════════════════════════════════════════════════════
# GROQ AI CLIENT
# ══════════════════════════════════════════════════════════════
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
groq_client  = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

if not groq_client:
    log.warning("⚠️  GROQ_API_KEY not set — AI responses will be disabled!")

SYSTEM_PROMPT = """\
You are FITCORE.AI — a smart, direct, expert AI fitness and wellness coach.

You remember the user's profile: name, age, weight, height, goal, fitness level, \
training type, and days per week. Always align advice with their stated goal.

Your specialities:
• Build personalised 7-day workout plans (Push/Pull/Legs/Core/Rest structure)
• Calculate macros using the Mifflin-St Jeor formula
• Evidence-based nutrition and supplement guidance
• Mental wellness support: stress, sleep, anxiety, motivation
• Track and respond to user progress

Response rules:
• Be concise but thorough — no filler, no fluff
• Use markdown: **bold**, bullet points, headers for plans
• When generating weekly plans label every day clearly: "Monday — Push Day"
• Include sets × reps and rest times for every exercise
• Only discuss fitness, nutrition, and mental wellness
• Never give medical diagnoses or replace professional medical advice
"""

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════
def now_iso() -> str:
    return datetime.utcnow().isoformat()

def today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")

def week_number(date_str: str = None) -> int:
    d = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.utcnow()
    return int(d.strftime("%V"))

def req_json() -> dict:
    return request.json or {}

def uid_from_req() -> str:
    d = req_json()
    return d.get("user_id") or request.args.get("user_id") or "anonymous"

# ══════════════════════════════════════════════════════════════
# PROFILE HELPERS
# ══════════════════════════════════════════════════════════════
def get_profile(user_id: str) -> dict:
    try:
        res = sb().table("user_profile") \
            .select("profile_data") \
            .eq("user_id", user_id) \
            .single() \
            .execute()
        if res.data and res.data.get("profile_data"):
            pd = res.data["profile_data"]
            return json.loads(pd) if isinstance(pd, str) else pd
    except Exception:
        pass
    return {}

def save_profile(user_id: str, profile: dict):
    try:
        sb().table("user_profile").upsert({
            "user_id":      user_id,
            "profile_data": profile,
        }, on_conflict="user_id").execute()
    except Exception as e:
        log.warning("save_profile error: %s", e)

def build_profile_context(user_id: str) -> str:
    """Build a concise profile string to inject into the system prompt."""
    p = get_profile(user_id)
    if not p:
        return ""
    goal_map   = {"bulk": "build muscle (bulk)", "cut": "lose fat (cut)",
                  "maintain": "maintain weight", "strength": "build strength",
                  "endurance": "build endurance"}
    train_map  = {"gym": "gym (full equipment)", "home_dumbbell": "home + dumbbells",
                  "calisthenics": "bodyweight/calisthenics", "hybrid": "hybrid gym+home"}
    level_map  = {"beginner": "beginner", "intermediate": "intermediate", "advanced": "advanced"}
    parts = []
    if p.get("nickname") or p.get("name"):
        parts.append(f"Name: {p.get('nickname') or p.get('name')}")
    if p.get("age"):          parts.append(f"Age: {p['age']}")
    if p.get("gender"):       parts.append(f"Gender: {p['gender']}")
    if p.get("weight"):
        unit = p.get("weight_unit", "kg")
        wkg  = round(float(p["weight"]) * 0.453592) if unit == "lbs" else p["weight"]
        parts.append(f"Weight: {wkg}kg")
    if p.get("height"):
        unit = p.get("height_unit", "cm")
        hcm  = round(float(p["height"]) * 30.48) if unit == "ft" else p["height"]
        parts.append(f"Height: {hcm}cm")
    if p.get("goal"):
        parts.append(f"Goal: {goal_map.get(p['goal'], p['goal'])}")
    if p.get("training_type"):
        parts.append(f"Trains: {train_map.get(p['training_type'], p['training_type'])}")
    if p.get("fitness_level"):
        parts.append(f"Level: {level_map.get(p['fitness_level'], p['fitness_level'])}")
    if p.get("days_per_week"):
        parts.append(f"Days/week: {p['days_per_week']}")
    if not parts:
        return ""
    return "User profile:\n" + "\n".join(f"  · {x}" for x in parts)

# ══════════════════════════════════════════════════════════════
# CHAT HISTORY HELPERS
# ══════════════════════════════════════════════════════════════
def get_history(user_id: str, page: str = "chat", limit: int = 30) -> list:
    try:
        res = sb().table("messages") \
            .select("role, content") \
            .eq("user_id", user_id) \
            .eq("page", page) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        rows = res.data or []
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except Exception as e:
        log.warning("get_history error: %s", e)
        return []

def save_message(user_id: str, role: str, content: str, page: str = "chat"):
    try:
        sb().table("messages").insert({
            "user_id":    user_id,
            "role":       role,
            "content":    content,
            "page":       page,
            "created_at": now_iso(),
        }).execute()
    except Exception as e:
        log.warning("save_message error: %s", e)

# ══════════════════════════════════════════════════════════════
# ROOT + HEALTH
# ══════════════════════════════════════════════════════════════
@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "name":    "FITCORE.AI Backend",
        "version": "5.0.0",
        "status":  "running",
        "db":      "supabase_configured" if _supabase else "not_configured",
        "ai":      "groq_configured"     if groq_client else "not_configured",
    })

@app.route("/health", methods=["GET"])
def health():
    db_ok = False
    ai_ok = groq_client is not None
    try:
        sb().table("users").select("id").limit(1).execute()
        db_ok = True
    except Exception:
        pass
    status = "ok" if (db_ok and ai_ok) else "degraded"
    return jsonify({
        "status":  status,
        "db":      "ok" if db_ok else "error_or_not_configured",
        "ai":      "ok" if ai_ok else "not_configured",
        "version": "5.0.0",
        "time":    now_iso(),
    }), 200 if status == "ok" else 503

@app.route("/stats", methods=["GET"])
def stats():
    result = {}
    try:
        result["users"]    = sb().table("users").select("id", count="exact").execute().count
        result["messages"] = sb().table("messages").select("id", count="exact").execute().count
        result["tasks"]    = sb().table("tasks").select("id", count="exact").execute().count
        result["progress"] = sb().table("progress").select("id", count="exact").execute().count
        result["plans"]    = sb().table("workout_plans").select("user_id", count="exact").execute().count
    except Exception as e:
        result["error"] = str(e)
    return jsonify(result)

# ══════════════════════════════════════════════════════════════
# USER SYNC  (called after login)
# ══════════════════════════════════════════════════════════════
@app.route("/user/sync", methods=["POST"])
def user_sync():
    """
    Called by every page after Supabase login.
    Keeps the users table in sync with the latest auth data.
    Body: { user_id, name, email, picture, provider }
    """
    data    = req_json()
    user_id = data.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    try:
        sb().table("users").upsert({
            "id":         user_id,
            "name":       data.get("name", ""),
            "email":      data.get("email", ""),
            "picture":    data.get("picture", ""),
            "provider":   data.get("provider", "email"),
            "updated_at": now_iso(),
        }, on_conflict="id").execute()
        log.info("User synced: %s", user_id[:12])
        return jsonify({"status": "synced"})
    except Exception as e:
        log.error("user_sync error: %s", e)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════
# AUTH  (email fallback — Supabase handles main auth)
# These routes exist only for environments where Supabase SDK
# isn't available. They just upsert a user row.
# ══════════════════════════════════════════════════════════════
@app.route("/auth/register", methods=["POST"])
def auth_register():
    data  = req_json()
    email = (data.get("email") or "").strip().lower()
    name  = (data.get("name")  or "").strip()
    uid   = data.get("uid") or ("e_" + email.replace("@", "_").replace(".", "_")[:30])
    if not email:
        return jsonify({"error": "email required"}), 400
    try:
        sb().table("users").upsert({
            "id":         uid,
            "name":       name,
            "email":      email,
            "provider":   "email",
            "updated_at": now_iso(),
        }, on_conflict="id").execute()
        return jsonify({"status": "registered", "uid": uid, "name": name, "email": email})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/auth/login", methods=["POST"])
def auth_login():
    data  = req_json()
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email required"}), 400
    try:
        res = sb().table("users").select("*").eq("email", email).limit(1).execute()
        if not res.data:
            return jsonify({"error": "User not found. Please register first."}), 404
        user = res.data[0]
        return jsonify({
            "status": "ok",
            "uid":    user["id"],
            "name":   user.get("name", ""),
            "email":  user.get("email", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════
# PROFILE
# ══════════════════════════════════════════════════════════════
@app.route("/profile/save", methods=["POST"])
def profile_save():
    data    = req_json()
    user_id = data.get("user_id", "")
    profile = data.get("profile", {})
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    save_profile(user_id, profile)
    log.info("Profile saved: %s — goal=%s level=%s",
             user_id[:12], profile.get("goal"), profile.get("fitness_level"))
    return jsonify({"status": "saved"})

@app.route("/profile/load", methods=["GET"])
def profile_load():
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    return jsonify({"profile": get_profile(user_id)})

# ══════════════════════════════════════════════════════════════
# CHAT
# ══════════════════════════════════════════════════════════════
@app.route("/chat", methods=["POST"])
def chat():
    data    = req_json()
    message = (data.get("message") or "").strip()
    user_id = data.get("user_id", "anonymous")
    page    = data.get("page", "chat")

    if not message:
        return jsonify({"error": "Empty message"}), 400

    if not groq_client:
        return jsonify({
            "reply": "⚠️ AI service not configured. Please set GROQ_API_KEY on Render."
        }), 503

    # Build system prompt — inject user profile from Supabase
    profile_ctx    = build_profile_context(user_id)
    system         = SYSTEM_PROMPT
    if profile_ctx:
        system += f"\n\n{profile_ctx}"

    # Allow per-mode override (mental wellness page sends custom system prompt)
    system_override = (data.get("system_override") or "").strip()
    if system_override:
        system = system_override + (f"\n\n{profile_ctx}" if profile_ctx else "")

    # Use client-provided history or load from Supabase
    client_history = data.get("history", [])
    history        = client_history if client_history else get_history(user_id, page=page, limit=24)

    # Save user message
    save_message(user_id, "user", message, page)

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system",  "content": system},
                *history[-20:],
                {"role": "user",    "content": message},
            ],
            max_tokens=1500,
            temperature=0.72,
        )
        reply = response.choices[0].message.content
    except Exception as e:
        log.error("Groq error: %s", e)
        return jsonify({
            "reply": "I'm having trouble reaching the AI right now. Please try again in a moment."
        }), 503

    # Save assistant reply
    save_message(user_id, "assistant", reply, page)
    return jsonify({"reply": reply})

@app.route("/history", methods=["GET"])
def history_endpoint():
    user_id = request.args.get("user_id", "")
    page    = request.args.get("page", "chat")
    return jsonify({"history": get_history(user_id, page=page, limit=100)})

@app.route("/clear", methods=["POST"])
def clear_history():
    data    = req_json()
    user_id = data.get("user_id", "")
    page    = data.get("page", None)
    try:
        q = sb().table("messages").delete().eq("user_id", user_id)
        if page:
            q = q.eq("page", page)
        q.execute()
        return jsonify({"status": "cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════
# WORKOUT PLAN
# ══════════════════════════════════════════════════════════════
@app.route("/plan/save", methods=["POST"])
def plan_save():
    data    = req_json()
    user_id = data.get("user_id", "")
    plan    = data.get("plan", {})
    raw     = data.get("raw", "")

    if not user_id or not plan:
        return jsonify({"error": "user_id and plan required"}), 400

    now = now_iso()
    try:
        existing = sb().table("workout_plans") \
            .select("created_at") \
            .eq("user_id", user_id) \
            .execute()
        created = existing.data[0]["created_at"] if existing.data else now

        sb().table("workout_plans").upsert({
            "user_id":    user_id,
            "plan_json":  plan,
            "raw_text":   raw,
            "created_at": created,
            "updated_at": now,
        }, on_conflict="user_id").execute()

        log.info("Plan saved: %s — %d days", user_id[:12], len(plan))
        return jsonify({"status": "saved", "days": list(plan.keys()), "updated_at": now})
    except Exception as e:
        log.error("plan_save error: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/plan/load", methods=["GET"])
def plan_load():
    user_id = request.args.get("user_id", "")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    try:
        res = sb().table("workout_plans").select("*").eq("user_id", user_id).execute()
        if not res.data:
            return jsonify({"plan": None, "raw": None})
        row = res.data[0]
        return jsonify({
            "plan":       row.get("plan_json"),
            "raw":        row.get("raw_text"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════
# TASKS (daily workout checklist)
# ══════════════════════════════════════════════════════════════
@app.route("/tasks", methods=["POST"])
def save_tasks():
    data    = req_json()
    user_id = data.get("user_id", "")
    tasks   = data.get("tasks", [])
    date    = data.get("date", today_str())
    wk      = week_number(date)

    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    try:
        # Wipe today's tasks before re-inserting (plan regenerate)
        sb().table("tasks").delete() \
            .eq("user_id", user_id) \
            .eq("date", date) \
            .execute()

        rows = []
        for t in tasks:
            text = (t.get("task_text") or "").strip()
            if text:
                rows.append({
                    "user_id":     user_id,
                    "task_text":   text,
                    "completed":   False,
                    "date":        date,
                    "week_number": wk,
                    "day_type":    t.get("day_type", "general"),
                    "badge":       t.get("badge", "workout"),
                    "created_at":  now_iso(),
                })
        if rows:
            sb().table("tasks").insert(rows).execute()

        return jsonify({"status": "saved", "count": len(rows), "date": date, "week": wk})
    except Exception as e:
        log.error("save_tasks error: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/tasks", methods=["GET"])
def get_tasks():
    user_id = request.args.get("user_id", "")
    date    = request.args.get("date", today_str())
    try:
        res = sb().table("tasks").select("*") \
            .eq("user_id", user_id) \
            .eq("date", date) \
            .order("created_at") \
            .execute()
        return jsonify({"tasks": res.data or [], "date": date})
    except Exception as e:
        return jsonify({"error": str(e), "tasks": [], "date": date}), 500

@app.route("/tasks/update", methods=["POST"])
def update_task():
    data      = req_json()
    user_id   = data.get("user_id", "")
    task_id   = data.get("task_id")
    completed = bool(data.get("completed"))

    if not task_id:
        return jsonify({"error": "task_id required"}), 400

    try:
        # Confirm the task belongs to this user
        check = sb().table("tasks").select("id, date") \
            .eq("id", task_id) \
            .eq("user_id", user_id) \
            .execute()
        if not check.data:
            return jsonify({"error": "Task not found"}), 404

        # Block edits on past dates
        task_date = check.data[0].get("date", "")
        if task_date and task_date != today_str():
            return jsonify({"error": "Past tasks cannot be edited"}), 403

        sb().table("tasks") \
            .update({"completed": completed}) \
            .eq("id", task_id) \
            .eq("user_id", user_id) \
            .execute()

        return jsonify({"status": "updated", "completed": completed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/tasks/history", methods=["GET"])
def tasks_history():
    user_id = request.args.get("user_id", "")
    try:
        res = sb().table("tasks").select("*") \
            .eq("user_id", user_id) \
            .order("date") \
            .execute()
        rows = res.data or []

        # Aggregate by date
        date_map = {}
        for r in rows:
            d = r["date"]
            if d not in date_map:
                date_map[d] = {"date": d, "total": 0, "done": 0}
            date_map[d]["total"] += 1
            if r["completed"]:
                date_map[d]["done"] += 1

        by_date = []
        for d in sorted(date_map):
            e   = date_map[d]
            pct = round(100 * e["done"] / e["total"], 1) if e["total"] else 0
            by_date.append({"date": d, "total": e["total"], "done": e["done"], "pct": pct})

        # Aggregate by day type
        dtype_map = {}
        for r in rows:
            dt = r.get("day_type", "general")
            if dt not in dtype_map:
                dtype_map[dt] = {"day_type": dt, "total": 0, "done": 0}
            dtype_map[dt]["total"] += 1
            if r["completed"]:
                dtype_map[dt]["done"] += 1

        by_day_type = []
        for dt, e in dtype_map.items():
            pct = round(100 * e["done"] / e["total"], 1) if e["total"] else 0
            by_day_type.append({"day_type": dt, "pct": pct, "total_tasks": e["total"]})

        # Weekly days summary
        weekly_map = {}
        for r in rows:
            key = r["date"]
            if key not in weekly_map:
                weekly_map[key] = {
                    "date":        r["date"],
                    "day_type":    r.get("day_type", "general"),
                    "week_number": r.get("week_number", 0),
                    "total": 0, "done": 0
                }
            weekly_map[key]["total"] += 1
            if r["completed"]:
                weekly_map[key]["done"] += 1

        weekly_days = sorted(weekly_map.values(), key=lambda x: x["date"])

        return jsonify({
            "by_date":     by_date,
            "by_day_type": by_day_type,
            "weekly_days": weekly_days,
        })
    except Exception as e:
        log.error("tasks_history error: %s", e)
        return jsonify({"by_date": [], "by_day_type": [], "weekly_days": []}), 500

@app.route("/tasks/ai-feedback", methods=["POST"])
def tasks_ai_feedback():
    """Called when user finishes their day — returns personalised feedback."""
    data           = req_json()
    user_id        = data.get("user_id", "")
    completion_pct = data.get("completion_pct", 0)
    day_type       = data.get("day_type", "workout")
    tasks_done     = data.get("tasks_done", [])
    tasks_missed   = data.get("tasks_missed", [])

    profile = get_profile(user_id)
    name    = profile.get("nickname") or profile.get("name") or ""

    prompt = (
        f"The user{f' ({name})' if name else ''} just finished their {day_type} day.\n"
        f"Completion: {completion_pct}%\n"
        f"Completed: {', '.join(tasks_done[:10]) if tasks_done else 'none'}\n"
        f"Missed: {', '.join(tasks_missed[:5]) if tasks_missed else 'none'}\n\n"
        "Give 2–3 sentences of honest, specific, energising feedback. "
        "If 100% celebrate loudly. If <70% encourage without sugarcoating. Be direct and human."
    )

    fallback = "Great effort today! Consistency compounds — keep showing up 💪"

    if not groq_client:
        return jsonify({"feedback": fallback})

    try:
        res = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=200,
            temperature=0.8,
        )
        return jsonify({"feedback": res.choices[0].message.content})
    except Exception:
        return jsonify({"feedback": fallback})

# ══════════════════════════════════════════════════════════════
# PROGRESS  (weight log)
# ══════════════════════════════════════════════════════════════
@app.route("/progress", methods=["POST"])
def save_progress():
    data         = req_json()
    user_id      = data.get("user_id", "")
    weight       = data.get("weight")
    workout_done = bool(data.get("workout_done"))
    date         = data.get("date", today_str())

    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    try:
        sb().table("progress").upsert({
            "user_id":      user_id,
            "weight":       weight,
            "workout_done": workout_done,
            "date":         date,
        }, on_conflict="user_id,date").execute()
        return jsonify({"status": "saved", "date": date})
    except Exception as e:
        log.error("save_progress error: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/progress", methods=["GET"])
def get_progress():
    user_id = request.args.get("user_id", "")
    limit   = min(int(request.args.get("limit", 90)), 365)
    try:
        res = sb().table("progress").select("weight, workout_done, date") \
            .eq("user_id", user_id) \
            .order("date") \
            .limit(limit) \
            .execute()
        return jsonify({"progress": res.data or []})
    except Exception as e:
        return jsonify({"error": str(e), "progress": []}), 500

# ══════════════════════════════════════════════════════════════
# MOOD LOG
# ══════════════════════════════════════════════════════════════
@app.route("/mood", methods=["POST"])
def save_mood():
    data    = req_json()
    user_id = data.get("user_id", "")
    score   = data.get("score")
    date    = data.get("date", today_str())

    if score is None:
        return jsonify({"error": "score required"}), 400

    try:
        sb().table("mood_log").upsert({
            "user_id": user_id,
            "date":    date,
            "score":   score,
            "emoji":   data.get("emoji", ""),
            "label":   data.get("label", ""),
            "note":    data.get("note", ""),
        }, on_conflict="user_id,date").execute()
        return jsonify({"status": "saved", "date": date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/mood", methods=["GET"])
def get_mood():
    user_id = request.args.get("user_id", "")
    limit   = int(request.args.get("limit", 30))
    try:
        res = sb().table("mood_log").select("*") \
            .eq("user_id", user_id) \
            .order("date", desc=True) \
            .limit(limit) \
            .execute()
        return jsonify({"mood_log": res.data or []})
    except Exception as e:
        return jsonify({"error": str(e), "mood_log": []}), 500

# ══════════════════════════════════════════════════════════════
# JOURNAL
# ══════════════════════════════════════════════════════════════
@app.route("/journal", methods=["POST"])
def save_journal():
    data    = req_json()
    user_id = data.get("user_id", "")
    text    = (data.get("text") or "").strip()
    date    = data.get("date", today_str())

    if not text:
        return jsonify({"error": "text required"}), 400

    try:
        sb().table("journal").insert({
            "user_id":    user_id,
            "date":       date,
            "text":       text,
            "mood":       data.get("mood", ""),
            "created_at": now_iso(),
        }).execute()
        return jsonify({"status": "saved", "date": date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/journal", methods=["GET"])
def get_journal():
    user_id = request.args.get("user_id", "")
    limit   = int(request.args.get("limit", 20))
    try:
        res = sb().table("journal").select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return jsonify({"entries": res.data or []})
    except Exception as e:
        return jsonify({"error": str(e), "entries": []}), 500

# ══════════════════════════════════════════════════════════════
# FOOD LOG
# diet.html calls /diet/foodlog  →  alias to /food
# ══════════════════════════════════════════════════════════════
def _save_food_items(user_id: str, items: list, date: str):
    rows = []
    for item in items:
        name = (item.get("name") or "").strip()
        if name:
            rows.append({
                "user_id":    user_id,
                "date":       date,
                "name":       name,
                "cals":       item.get("cals", 0),
                "protein":    item.get("protein", 0),
                "carbs":      item.get("carbs", 0),
                "fat":        item.get("fat", 0),
                "created_at": now_iso(),
            })
    if rows:
        sb().table("food_log").insert(rows).execute()
    return len(rows)

@app.route("/food", methods=["POST"])
def save_food():
    data    = req_json()
    user_id = data.get("user_id", "")
    items   = data.get("items", [])
    date    = data.get("date", today_str())
    if not items:
        return jsonify({"error": "items required"}), 400
    try:
        count = _save_food_items(user_id, items, date)
        return jsonify({"status": "saved", "count": count, "date": date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Alias route called by diet.html ──────────────────────────
@app.route("/diet/foodlog", methods=["POST"])
def diet_foodlog():
    """Alias for /food — called directly by diet.html sync."""
    data    = req_json()
    user_id = data.get("user_id", "")
    items   = data.get("items", [])
    date    = data.get("date", today_str())
    try:
        # Replace today's entire food log (sync = overwrite)
        sb().table("food_log").delete() \
            .eq("user_id", user_id) \
            .eq("date", date) \
            .execute()
        count = _save_food_items(user_id, items, date)
        return jsonify({"status": "synced", "count": count, "date": date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/food", methods=["GET"])
def get_food():
    user_id = request.args.get("user_id", "")
    date    = request.args.get("date", today_str())
    try:
        res = sb().table("food_log").select("*") \
            .eq("user_id", user_id) \
            .eq("date", date) \
            .order("created_at") \
            .execute()
        return jsonify({"items": res.data or [], "date": date})
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "date": date}), 500

@app.route("/food/<item_id>", methods=["DELETE"])
def delete_food(item_id):
    user_id = request.args.get("user_id", "")
    try:
        sb().table("food_log").delete() \
            .eq("id", item_id) \
            .eq("user_id", user_id) \
            .execute()
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════
# MACRO TARGETS
# ══════════════════════════════════════════════════════════════
@app.route("/macros", methods=["POST"])
def save_macros():
    data    = req_json()
    user_id = data.get("user_id", "")
    try:
        sb().table("macro_targets").upsert({
            "user_id":    user_id,
            "calories":   data.get("calories"),
            "protein":    data.get("protein"),
            "carbs":      data.get("carbs"),
            "fat":        data.get("fat"),
            "goal":       data.get("goal", "maintain"),
            "updated_at": now_iso(),
        }, on_conflict="user_id").execute()
        return jsonify({"status": "saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/macros", methods=["GET"])
def get_macros():
    user_id = request.args.get("user_id", "")
    try:
        res = sb().table("macro_targets").select("*").eq("user_id", user_id).execute()
        return jsonify({"targets": res.data[0] if res.data else None})
    except Exception as e:
        return jsonify({"error": str(e), "targets": None}), 500

# ══════════════════════════════════════════════════════════════
# WATER LOG
# diet.html calls /diet/water  →  alias to /water
# ══════════════════════════════════════════════════════════════
@app.route("/water", methods=["POST"])
def save_water():
    data    = req_json()
    user_id = data.get("user_id", "")
    cups    = int(data.get("cups", 0))
    date    = data.get("date", today_str())
    try:
        sb().table("water_log").upsert({
            "user_id": user_id,
            "date":    date,
            "cups":    cups,
        }, on_conflict="user_id,date").execute()
        return jsonify({"status": "saved", "cups": cups, "date": date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Alias route called by diet.html ──────────────────────────
@app.route("/diet/water", methods=["POST"])
def diet_water():
    """Alias for /water — called directly by diet.html sync."""
    return save_water()

@app.route("/water", methods=["GET"])
def get_water():
    user_id = request.args.get("user_id", "")
    date    = request.args.get("date", today_str())
    try:
        res = sb().table("water_log").select("cups") \
            .eq("user_id", user_id) \
            .eq("date", date) \
            .execute()
        cups = res.data[0]["cups"] if res.data else 0
        return jsonify({"cups": cups, "date": date})
    except Exception as e:
        return jsonify({"error": str(e), "cups": 0, "date": date}), 500

# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    log.info("🚀  FITCORE.AI Backend v5.0 — port %d", port)
    log.info("🗄️   Supabase: %s", "connected" if _supabase else "NOT CONFIGURED")
    log.info("🤖  Groq AI:  %s", "configured" if groq_client else "NOT CONFIGURED")
    app.run(host="0.0.0.0", port=port, debug=debug)

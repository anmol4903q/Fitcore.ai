# FITCORE.AI — Supabase Setup Guide
# ============================================================
# Supabase is 100% FREE for this project.
# Free tier includes: 500MB DB, 2GB bandwidth, unlimited auth users
# ============================================================

## STEP 1 — Create a Supabase Project
1. Go to https://app.supabase.com
2. Click "New Project"
3. Name it: fitcore-ai
4. Choose a region close to your users (e.g. South Asia)
5. Set a strong database password
6. Click "Create new project" — wait ~2 minutes

## STEP 2 — Get Your API Keys
1. In your project → Settings → API
2. Copy:
   - Project URL:  https://xxxx.supabase.co
   - anon (public) key: eyJhbGci...

## STEP 3 — Update supabase-config.js
Replace these lines:
```js
const SUPABASE_URL  = 'https://YOUR_PROJECT.supabase.co';
const SUPABASE_ANON = 'YOUR_ANON_KEY_HERE';
```

## STEP 4 — Run This SQL in Supabase SQL Editor
(Supabase → SQL Editor → New Query → paste below → Run)

```sql
-- ── USERS TABLE ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.users (
  id          UUID PRIMARY KEY,
  name        TEXT,
  email       TEXT UNIQUE,
  picture     TEXT,
  provider    TEXT DEFAULT 'email',
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users can read own data"   ON public.users FOR SELECT USING (auth.uid()::text = id::text);
CREATE POLICY "Users can update own data" ON public.users FOR UPDATE USING (auth.uid()::text = id::text);
CREATE POLICY "Users can insert own data" ON public.users FOR INSERT WITH CHECK (auth.uid()::text = id::text);

-- ── MESSAGES TABLE ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.messages (
  id         BIGSERIAL PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  page       TEXT DEFAULT 'chat',
  created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own messages" ON public.messages FOR ALL USING (auth.uid()::text = user_id::text);

-- ── WORKOUT PLANS TABLE ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.workout_plans (
  user_id    UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  plan_json  JSONB NOT NULL,
  raw_text   TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.workout_plans ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own plan" ON public.workout_plans FOR ALL USING (auth.uid()::text = user_id::text);

-- ── TASKS TABLE ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tasks (
  id          BIGSERIAL PRIMARY KEY,
  user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  task_text   TEXT NOT NULL,
  completed   BOOLEAN DEFAULT FALSE,
  date        DATE NOT NULL,
  week_number INT,
  day_type    TEXT DEFAULT 'general',
  badge       TEXT DEFAULT 'workout',
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, date, task_text)
);
ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own tasks" ON public.tasks FOR ALL USING (auth.uid()::text = user_id::text);

-- ── PROGRESS (WEIGHT LOG) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS public.progress (
  id           BIGSERIAL PRIMARY KEY,
  user_id      UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  weight       DECIMAL(5,1),
  workout_done BOOLEAN DEFAULT FALSE,
  date         DATE NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, date)
);
ALTER TABLE public.progress ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own progress" ON public.progress FOR ALL USING (auth.uid()::text = user_id::text);

-- ── MOOD LOG ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.mood_log (
  id         BIGSERIAL PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  date       DATE NOT NULL,
  score      INT NOT NULL CHECK (score BETWEEN 1 AND 5),
  emoji      TEXT,
  label      TEXT,
  note       TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, date)
);
ALTER TABLE public.mood_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own mood" ON public.mood_log FOR ALL USING (auth.uid()::text = user_id::text);

-- ── JOURNAL ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.journal (
  id         BIGSERIAL PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  date       DATE NOT NULL,
  text       TEXT NOT NULL,
  mood       TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.journal ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own journal" ON public.journal FOR ALL USING (auth.uid()::text = user_id::text);

-- ── FOOD LOG ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.food_log (
  id         BIGSERIAL PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  date       DATE NOT NULL,
  name       TEXT NOT NULL,
  cals       DECIMAL(7,1) DEFAULT 0,
  protein    DECIMAL(6,1) DEFAULT 0,
  carbs      DECIMAL(6,1) DEFAULT 0,
  fat        DECIMAL(6,1) DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.food_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own food" ON public.food_log FOR ALL USING (auth.uid()::text = user_id::text);

-- ── MACRO TARGETS ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.macro_targets (
  user_id    UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  calories   INT,
  protein    INT,
  carbs      INT,
  fat        INT,
  goal       TEXT DEFAULT 'maintain',
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.macro_targets ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own macros" ON public.macro_targets FOR ALL USING (auth.uid()::text = user_id::text);

-- ── WATER LOG ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.water_log (
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  date    DATE NOT NULL,
  cups    INT DEFAULT 0,
  PRIMARY KEY(user_id, date)
);
ALTER TABLE public.water_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own water" ON public.water_log FOR ALL USING (auth.uid()::text = user_id::text);

-- ── USER PROFILES (extra data) ──────────────────────────────
CREATE TABLE IF NOT EXISTS public.user_profile (
  user_id      UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  profile_data JSONB DEFAULT '{}'
);
ALTER TABLE public.user_profile ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Own profile" ON public.user_profile FOR ALL USING (auth.uid()::text = user_id::text);
```

## STEP 5 — Enable Google OAuth (optional but recommended)
1. Supabase → Authentication → Providers → Google → Enable
2. Go to https://console.cloud.google.com
3. Create OAuth credentials → Web Application
4. Authorized redirect URIs: https://YOUR_PROJECT.supabase.co/auth/v1/callback
5. Copy Client ID and Secret back to Supabase

## STEP 6 — Add Your Domain to Supabase
1. Supabase → Authentication → URL Configuration
2. Site URL: https://yourusername.github.io/fitcore-ai/
3. Redirect URLs: add your GitHub Pages URL

## STEP 7 — Add Script to All 5 HTML Files
Add these two lines to the <head> of index.html, workout.html, progress.html, diet.html, mental.html:
```html
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script src="supabase-config.js"></script>
```

And add this at the TOP of the <script> section on each page:
```javascript
// Redirect to auth if not logged in
(async function checkAuth() {
  await handleSupabaseSession();  // syncs Supabase session to localStorage
  if (!requireAuth()) return;     // redirects to auth.html if not logged in
  // ... rest of your page init
})();
```
## FREE TIER LIMITS (Supabase)
- Database: 500MB (more than enough)
- Auth users: Unlimited
- API requests: 50,000/month (unlimited for personal projects)
- Bandwidth: 2GB/month
- Storage: 1GB

## FILE STRUCTURE
fitcore-ai/
├── auth.html          ← Login/register page (new)
├── supabase-config.js ← Supabase config (update your keys here)
├── index.html         ← Main chat
├── workout.html       ← Workout planner
├── progress.html      ← Progress tracker
├── diet.html          ← Diet advisor
├── mental.html        ← Mental wellness
├── logo.png           ← Your logo image (upload this)
├── app.py             ← Backend (on Render)
└── requirements.txt

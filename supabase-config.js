// ═══════════════════════════════════════════════════════════
// FITCORE.AI — Supabase Config & Auth Utilities
// Shared across all pages — include this before any page script
// ═══════════════════════════════════════════════════════════

// ── SUPABASE SETUP ───────────────────────────────────────────
// Replace these with your actual Supabase project values from:
// https://app.supabase.com → Project Settings → API
const SUPABASE_URL    = 'https://eswkbttcgeqbuuadxnbg.supabase.co';      
const SUPABASE_ANON   = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVzd2tidHRjZ2VxYnV1YWR4bmJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzcxOTcwMTYsImV4cCI6MjA5Mjc3MzAxNn0.ujQh3jK2EMUAiO3s28Edh7mdl45KYuQ1-3TpnaPJXKY';  // public anon key

// Load Supabase client (loaded via CDN in HTML)
let _supabase = null;
function getSupabase() {
  if (!_supabase) {
    _supabase = supabase.createClient(SUPABASE_URL, SUPABASE_ANON);
  }
  return _supabase;
}

// ── AUTH STATE ───────────────────────────────────────────────
const AUTH_KEY     = 'fitcore_auth_v3';
const SESSION_KEY  = 'fitcore_session_v1';

function getLocalAuth() {
  try { return JSON.parse(localStorage.getItem(AUTH_KEY)) || null; } catch { return null; }
}
function setLocalAuth(d) { localStorage.setItem(AUTH_KEY, JSON.stringify(d)); }
function clearLocalAuth() {
  localStorage.removeItem(AUTH_KEY);
  localStorage.removeItem(SESSION_KEY);
}

// ── USER ID ──────────────────────────────────────────────────
function getUserId() {
  const a = getLocalAuth();
  if (a && a.uid) return a.uid;
  let id = localStorage.getItem('fitcore_user_id');
  if (!id) {
    function getUserId() {
  const auth = getLocalAuth();
  return auth?.uid || null;
}
  return id;
}
 
// ── GUARD: redirect to auth if not logged in ─────────────────
function requireAuth() {
  const auth = getLocalAuth();
  if (!auth || !auth.uid) {
    window.location.href = 'auth.html';
    return false;
  }
  return true;
}

// ── SUPABASE SIGN IN WITH GOOGLE ─────────────────────────────
async function supabaseGoogleLogin() {
  const sb = getSupabase();
  const { error } = await sb.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: window.location.origin + '/index.html'
    }
  });
  if (error) throw error;
}

// ── SUPABASE EMAIL SIGN UP ───────────────────────────────────
async function supabaseRegister(email, password, name) {
  const sb = getSupabase();
  const { data, error } = await sb.auth.signUp({
    email,
    password,
    options: {
      data: { full_name: name, display_name: name }
    }
  });
  if (error) throw error;
  return data;
}

// ── SUPABASE EMAIL LOGIN ─────────────────────────────────────
async function supabaseLogin(email, password) {
  const sb = getSupabase();
  const { data, error } = await sb.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return data;
}

// ── SUPABASE SIGN OUT ────────────────────────────────────────
async function supabaseSignOut() {
  const sb = getSupabase();
  await sb.auth.signOut();
  clearLocalAuth();
  window.location.href = 'auth.html';
}

// ── HANDLE SUPABASE SESSION (call on every page load) ────────
async function handleSupabaseSession() {
  const sb = getSupabase();
  const { data: { session } } = await sb.auth.getSession();

  if (session && session.user) {
    const user = session.user;
    const meta = user.user_metadata || {};
    const auth = {
      uid:     user.id,
      name:    meta.full_name || meta.name || meta.display_name || user.email.split('@')[0],
      email:   user.email,
      picture: meta.avatar_url || meta.picture || null,
      mode:    user.app_metadata?.provider || 'email',
      provider: user.app_metadata?.provider || 'email'
    };
    setLocalAuth(auth);
    return auth;
  }
  return null;
}

// ── SAVE USER PROFILE TO SUPABASE DB ─────────────────────────
async function saveUserToSupabase(auth) {
  const sb = getSupabase();
  try {
    await sb.from('users').upsert({
      id:         auth.uid,
      name:       auth.name,
      email:      auth.email,
      picture:    auth.picture,
      provider:   auth.provider || 'email',
      updated_at: new Date().toISOString()
    }, { onConflict: 'id' });
  } catch (e) {
    console.warn('Could not save user to Supabase:', e.message);
  }
}

// ── BACKEND FALLBACK (your Render backend) ───────────────────
const BACKEND = 'https://fitcore-backend-ib8k.onrender.com';

// Auto-listen for auth state changes
(function listenAuth() {
  try {
    const sb = getSupabase();
    sb.auth.onAuthStateChange(async (event, session) => {
      if (event === 'SIGNED_IN' && session) {
        const user = session.user;
        const meta = user.user_metadata || {};
        const auth = {
          uid:      user.id,
          name:     meta.full_name || meta.name || user.email.split('@')[0],
          email:    user.email,
          picture:  meta.avatar_url || null,
          mode:     user.app_metadata?.provider || 'email',
          provider: user.app_metadata?.provider || 'email'
        };
        setLocalAuth(auth);
        await saveUserToSupabase(auth);
        // Redirect to main app if on auth page
        if (window.location.pathname.includes('auth.html')) {
          window.location.href = 'index.html';
        }
      } else if (event === 'SIGNED_OUT') {
        clearLocalAuth();
        if (!window.location.pathname.includes('auth.html')) {
          window.location.href = 'auth.html';
        }
      }
    });
  } catch(e) {
    // Supabase not configured yet
  }
})();

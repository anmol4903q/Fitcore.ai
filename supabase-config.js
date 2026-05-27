// ═══════════════════════════════════════════════════════════
// FITCORE.AI — Supabase Config & Auth Utilities
// Shared across all pages — include this before any page script
// ═══════════════════════════════════════════════════════════

// ── SUPABASE SETUP ────
const SUPABASE_URL  = 'https://eswkbttcgeqbuuadxnbg.supabase.co';
const SUPABASE_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVzd2tidHRjZ2VxYnV1YWR4bmJnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzcxOTcwMTYsImV4cCI6MjA5Mjc3MzAxNn0.ujQh3jK2EMUAiO3s28Edh7mdl45KYuQ1-3TpnaPJXKY';

let _supabase = null;

function getSupabase() {
  if (!_supabase) {
    // Guard: CDN may not have loaded yet, or may be blocked
    if (typeof supabase === 'undefined' || typeof supabase.createClient !== 'function') {
      return null;
    }
    try {
      _supabase = supabase.createClient(SUPABASE_URL, SUPABASE_ANON);
    } catch (e) {
      console.warn('Supabase client init failed:', e.message);
      return null;
    }
  }
  return _supabase;
}

// ── AUTH STATE ────────────────────────────────────────────────
const AUTH_KEY    = 'fitcore_auth_v3';
const SESSION_KEY = 'fitcore_session_v1';

function getLocalAuth() {
  try { return JSON.parse(localStorage.getItem(AUTH_KEY)) || null; } catch { return null; }
}
function setLocalAuth(d) { localStorage.setItem(AUTH_KEY, JSON.stringify(d)); }
function clearLocalAuth() {
  localStorage.removeItem(AUTH_KEY);
  localStorage.removeItem(SESSION_KEY);
}

// ── USER ID ───────────────────────────────────────────────────
function getUserId() {
  const auth = getLocalAuth();
  if (auth && auth.uid) return auth.uid;
  let id = localStorage.getItem('fitcore_user_id');
  if (!id) {
    id = 'guest_' + Math.random().toString(36).slice(2, 14) + Date.now().toString(36);
    localStorage.setItem('fitcore_user_id', id);
  }
  return id;
}

// ── GUARD: redirect to auth if not logged in ──────────────────
function requireAuth() {
  const auth = getLocalAuth();
  if (!auth || !auth.uid) {
    window.location.href = 'auth.html';
    return false;
  }
  return true;
}

// ── GOOGLE LOGIN ──────────────────────────────────────────────
async function supabaseGoogleLogin() {
  const sb = getSupabase();
  if (!sb) throw new Error('Supabase not available');
  const { error } = await sb.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: window.location.origin + window.location.pathname.replace(/auth\.html$/, 'index.html')
    }
  });
  if (error) throw error;
}

// ── EMAIL REGISTER ────────────────────────────────────────────
async function supabaseRegister(email, password, name) {
  const sb = getSupabase();
  if (!sb) throw new Error('Supabase not available');
  const { data, error } = await sb.auth.signUp({
    email,
    password,
    options: { data: { full_name: name, display_name: name } }
  });
  if (error) throw error;
  return data;
}

// ── EMAIL LOGIN ───────────────────────────────────────────────
async function supabaseLogin(email, password) {
  const sb = getSupabase();
  if (!sb) throw new Error('Supabase not available');
  const { data, error } = await sb.auth.signInWithPassword({ email, password });
  if (error) throw error;
  return data;
}

// ── SIGN OUT ──────────────────────────────────────────────────
async function supabaseSignOut() {
  try {
    const sb = getSupabase();
    if (sb) await sb.auth.signOut();
  } catch (e) {
    console.warn('Supabase signOut failed:', e);
  }
  clearLocalAuth();
  window.location.href = 'auth.html';
}

// ── HANDLE SESSION ON PAGE LOAD ───────────────────────────────
async function handleSupabaseSession() {
  try {
    const sb = getSupabase();
    if (!sb) return getLocalAuth();
    const { data: { session } } = await sb.auth.getSession();
    if (session && session.user) {
      const user = session.user;
      const meta = user.user_metadata || {};
      const auth = {
        uid:      user.id,
        name:     meta.full_name || meta.name || meta.display_name || user.email.split('@')[0],
        email:    user.email,
        picture:  meta.avatar_url || meta.picture || null,
        mode:     user.app_metadata?.provider || 'email',
        provider: user.app_metadata?.provider || 'email'
      };
      setLocalAuth(auth);
      return auth;
    }
  } catch (e) {
    console.warn('Supabase session error:', e.message);
  }
  return getLocalAuth();
}

// ── SAVE USER TO SUPABASE DB ──────────────────────────────────
async function saveUserToSupabase(auth) {
  try {
    const sb = getSupabase();
    if (!sb) return;
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

// ── BACKEND URL ───────────────────────────────────────────────
const BACKEND = 'https://fitcore-backend-ib8k.onrender.com';

// ── AUTH STATE LISTENER ───────────────────────────────────────
// Runs after DOM is ready to avoid race with CDN script load
window.addEventListener('load', function () {
  try {
    const sb = getSupabase();
    if (!sb) return; // CDN blocked or not loaded — fail silently
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
  } catch (e) {
    // Supabase not configured — page still works normally
  }
});
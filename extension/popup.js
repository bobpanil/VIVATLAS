'use strict';

// VIVATLAS Clipper — pick a server, sign in (with MFA), then capture the current page
// or a pasted link. The cog panel holds the account, the server, the default "Save as"
// and sign-out; the capture view keeps only what you touch per clip.
//
// Auth: normally the extension just adopts your BROWSER's VIVATLAS session — it reads
// the vivatlas_session cookie for the configured server and signs in as you, no separate
// extension login. If you're not signed in in the browser, it falls back to /api/ext/login
// (with MFA), which returns a token stored here and sent as a Bearer header.
//
// Cross-browser: `ext` is Firefox's promise-based `browser` when present, else Chrome's
// `chrome` (whose MV3 APIs also return promises), so the same code runs on both.

const ext = (typeof browser !== 'undefined') ? browser : chrome;

const COOKIE_NAME = 'vivatlas_session';
const SESSION_DAYS = 30;

let state = { server: '', token: '', email: '', name: '', defaultVis: 'private' };
let ticket = '';        // between password and MFA; not persisted
let backupMode = false; // MFA: app code vs backup code
let visState = 'default';   // per-clip: 'default' | 'private' | 'public'
let grabbedText = '';       // the page text captured for the current clip

const $ = (id) => document.getElementById(id);

// --- storage -------------------------------------------------------------

async function load() {
  const v = await ext.storage.local.get(['server', 'token', 'email', 'name', 'defaultVis']);
  state = {
    // The download from Settings wires in the server (config.js); the source
    // checkout leaves it blank and the popup asks once.
    server: v.server || (window.VIVATLAS_SERVER || ''),
    token: v.token || '',
    email: v.email || '',
    name: v.name || '',
    defaultVis: v.defaultVis === 'public' ? 'public' : 'private',
  };
}
async function save() {
  await ext.storage.local.set(state);
}

// --- ui helpers ----------------------------------------------------------

function show(step) {
  $('cogpanel').hidden = true;
  ['step-server', 'step-login', 'step-mfa', 'step-main'].forEach((id) => {
    $(id).hidden = id !== step;
  });
  document.body.classList.toggle('signed-in', step === 'step-main');
  clearBanner();
}
function banner(msg, kind) {
  const b = $('banner');
  b.textContent = msg;
  b.className = 'banner' + (kind ? ' ' + kind : '');
  b.hidden = !msg;
}
function clearBanner() { banner('', ''); }

function normalizeServer(v) {
  v = (v || '').trim().replace(/\/+$/, '');
  if (!v) return '';
  if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
  return v;
}

// --- api -----------------------------------------------------------------

async function api(path, method, body) {
  const headers = { 'Accept': 'application/json' };
  if (body) headers['Content-Type'] = 'application/json';
  if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
  let res;
  try {
    res = await fetch(state.server + '/api/ext' + path, {
      method: method || 'GET',
      headers,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include',
    });
  } catch (e) {
    return { ok: false, status: 0, data: { error: 'Could not reach the server. Check the address.' } };
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  return { ok: res.ok, status: res.status, data };
}

// --- session cookie ------------------------------------------------------

// Adopt the browser's own VIVATLAS session: read the (HttpOnly) session cookie —
// which the cookies API can see even though page JS can't — and use it as the token.
async function readSessionCookie() {
  if (!ext.cookies || !state.server) return '';
  try {
    const c = await ext.cookies.get({ url: state.server, name: COOKIE_NAME });
    return (c && c.value) || '';
  } catch (e) { return ''; }
}
// Set/clear the session cookie so "Open VIVATLAS" lands authenticated.
async function setSessionCookie() {
  if (!ext.cookies) return;
  try {
    await ext.cookies.set({
      url: state.server,
      name: COOKIE_NAME,
      value: state.token,
      path: '/',
      httpOnly: true,
      secure: state.server.startsWith('https'),
      sameSite: 'lax',
      expirationDate: Math.floor(Date.now() / 1000) + SESSION_DAYS * 24 * 3600,
    });
  } catch (e) { /* cookie not settable — Bearer still works */ }
}
async function clearSessionCookie() {
  if (!ext.cookies) return;
  try { await ext.cookies.remove({ url: state.server, name: COOKIE_NAME }); } catch (e) { /* ignore */ }
}

// --- capture -------------------------------------------------------------

async function activeTab() {
  const tabs = await ext.tabs.query({ active: true, currentWindow: true });
  return tabs && tabs[0] ? tabs[0] : null;
}

// Grab the readable text of the active tab. url/title come from the tab even when the
// page can't be scripted (chrome://, PDF viewer, store).
async function grab() {
  const tab = await activeTab();
  const out = { url: (tab && tab.url) || '', title: (tab && tab.title) || '', text: '' };
  if (tab && tab.id != null) {
    try {
      const results = await ext.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const el = document.querySelector('main, article') || document.body;
          const text = (el && el.innerText ? el.innerText : '').replace(/\n{3,}/g, '\n\n').trim();
          return { url: location.href, title: document.title, text: text.slice(0, 8000) };
        },
      });
      const r = results && results[0] && results[0].result;
      if (r) { out.url = r.url || out.url; out.title = r.title || out.title; out.text = r.text || ''; }
    } catch (e) { /* can't inject here — keep url/title */ }
  }
  return out;
}

async function initMain() {
  $('cog-account').textContent = state.name ? (state.name + ' · ' + state.email) : state.email;
  $('cog-server').textContent = state.server;
  syncDefaultSeg();
  setVis('default');
  await refillCapture();
}

async function refillCapture() {
  $('rescan').disabled = true;
  const cap = await grab();
  $('url').value = cap.url;
  $('title').value = cap.title;
  grabbedText = cap.text || '';
  $('rescan').disabled = false;
}

// --- visibility ----------------------------------------------------------

function setVis(v) {
  visState = v;
  $('vis').setAttribute('data-vis', v);
  let label;
  if (v === 'default') label = 'Save as: default (' + (state.defaultVis === 'public' ? 'Public' : 'Private') + ')';
  else if (v === 'private') label = 'Save as: Private';
  else label = 'Save as: Public';
  $('vis').title = label;
  $('vis').setAttribute('aria-label', label);
}
function cycleVis() {
  const order = ['default', 'private', 'public'];
  setVis(order[(order.indexOf(visState) + 1) % order.length]);
}
function effectiveShared() {
  let v = visState;
  if (v === 'default') v = state.defaultVis;
  return v === 'public';
}

function syncDefaultSeg() {
  $('def-private').classList.toggle('on', state.defaultVis !== 'public');
  $('def-public').classList.toggle('on', state.defaultVis === 'public');
}
async function setDefaultVis(v) {
  state.defaultVis = v === 'public' ? 'public' : 'private';
  await save();
  syncDefaultSeg();
  if (visState === 'default') setVis('default');   // refresh the "(Private/Public)" hint
}

// --- cog panel -----------------------------------------------------------

function openCog() {
  $('step-main').hidden = true;
  $('cogpanel').hidden = false;
  clearBanner();
}
function closeCog() {
  $('cogpanel').hidden = true;
  $('step-main').hidden = false;
}

// --- flow ----------------------------------------------------------------

// True once /session confirms the current token; fills in the account and shows main.
async function trySession() {
  const r = await api('/session');
  if (!r.ok) return false;
  if (r.data.user) {
    state.email = r.data.user.email || state.email;
    state.name = r.data.user.name || state.name;
    await save();
  }
  show('step-main');
  await initMain();
  return true;
}

async function decideStart() {
  if (!state.server) return show('step-server');
  // Auto-login: adopt the browser's VIVATLAS session if there is one, so there's
  // no separate extension sign-in.
  if (!state.token) {
    const cookieTok = await readSessionCookie();
    if (cookieTok) { state.token = cookieTok; await save(); }
  }
  if (state.token && await trySession()) return;
  // Stored token is stale — maybe the browser has a fresher session (you signed in
  // again on the site). Try that once before asking for a password.
  const fresh = await readSessionCookie();
  if (fresh && fresh !== state.token) {
    state.token = fresh;
    await save();
    if (await trySession()) return;
  }
  state.token = '';
  await save();
  $('login-server').textContent = state.server;
  show('step-login');
}

async function onServerGo() {
  const server = normalizeServer($('server').value);
  if (!server) return banner('Enter a server address.', 'err');
  let origin;
  try { origin = new URL(server).origin; } catch (e) { return banner('That address looks off.', 'err'); }
  let granted = true;
  try {
    granted = await ext.permissions.request({ origins: [origin + '/*'] });
  } catch (e) { granted = false; }
  if (!granted) return banner('Permission for that server was declined.', 'err');
  state.server = server;
  await save();
  $('login-server').textContent = server;
  $('server').value = '';
  // Adopt the browser session for the new server if there is one.
  await decideStart();
}

async function onLoginGo() {
  const email = $('email').value.trim();
  const password = $('password').value;
  if (!email || !password) return banner('Email and password, please.', 'err');
  banner('Signing in…', '');
  const r = await api('/login', 'POST', { email, password });
  if (!r.ok) return banner(r.data.error || 'Sign-in failed.', 'err');
  if (r.data.mfa_required) {
    ticket = r.data.ticket || '';
    backupMode = false;
    updateMfaMode();
    $('code').value = '';
    show('step-mfa');
    return;
  }
  await onSignedIn(r.data);
}

function updateMfaMode() {
  $('mfa-label').textContent = backupMode ? 'Backup code' : 'Code';
  $('mfa-hint').textContent = backupMode
    ? 'Enter one of your backup codes.'
    : 'Enter the 6-digit code from your app.';
  $('code').placeholder = backupMode ? 'xxxx-xxxx' : '123456';
  $('mfa-toggle').textContent = backupMode ? 'Use an app code' : 'Use a backup code';
}

async function onMfaGo() {
  const code = $('code').value.trim();
  if (!code) return banner('Enter the code.', 'err');
  banner('Verifying…', '');
  const r = await api('/mfa', 'POST', { ticket, code, backup: backupMode });
  if (!r.ok) {
    if (r.data.expired) { banner('That took too long — sign in again.', 'err'); return show('step-login'); }
    return banner(r.data.error || 'Wrong code.', 'err');
  }
  await onSignedIn(r.data);
}

async function onSignedIn(data) {
  state.token = data.token || '';
  state.email = (data.user && data.user.email) || state.email;
  state.name = (data.user && data.user.name) || state.name;
  await save();
  await setSessionCookie();
  $('password').value = '';
  show('step-main');
  await initMain();
}

async function onSend() {
  const url = $('url').value.trim();
  const title = $('title').value.trim();
  if (!url && !grabbedText) return banner('Nothing to add — open a page or paste a link.', 'err');
  const shared = effectiveShared();
  $('send').disabled = true;
  banner('Sending…', '');
  const r = await api('/add', 'POST', { url, title, text: grabbedText, shared });
  $('send').disabled = false;
  if (!r.ok) {
    if (r.status === 401) { state.token = ''; await save(); banner('Session expired — sign in again.', 'err'); return show('step-login'); }
    return banner(r.data.error || 'Could not add.', 'err');
  }
  banner('Added to ' + (shared ? 'the public catalogue' : 'your private space') + ' — processing…', 'ok');
}

async function onRescan() {
  banner('Re-reading the page…', '');
  await refillCapture();
  clearBanner();
}

async function onLogout() {
  await api('/logout', 'POST');
  await clearSessionCookie();
  state.token = '';
  state.email = '';
  state.name = '';
  await save();
  show('step-login');
  $('login-server').textContent = state.server;
}

function onOpenWeb() { ext.tabs.create({ url: state.server }); }

// --- wire up -------------------------------------------------------------

function enterKey(inputId, handler) {
  $(inputId).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); handler(); } });
}

document.addEventListener('DOMContentLoaded', async () => {
  await load();

  $('server-go').addEventListener('click', onServerGo);
  enterKey('server', onServerGo);

  $('login-go').addEventListener('click', onLoginGo);
  $('login-back').addEventListener('click', () => show('step-server'));
  enterKey('email', onLoginGo);
  enterKey('password', onLoginGo);

  $('mfa-go').addEventListener('click', onMfaGo);
  $('mfa-back').addEventListener('click', () => show('step-login'));
  $('mfa-toggle').addEventListener('click', () => { backupMode = !backupMode; updateMfaMode(); });
  enterKey('code', onMfaGo);

  $('vis').addEventListener('click', cycleVis);
  $('send').addEventListener('click', onSend);
  $('rescan').addEventListener('click', onRescan);
  $('open-web').addEventListener('click', onOpenWeb);

  $('cog').addEventListener('click', openCog);
  $('cog-close').addEventListener('click', closeCog);
  $('def-private').addEventListener('click', () => setDefaultVis('private'));
  $('def-public').addEventListener('click', () => setDefaultVis('public'));
  $('logout').addEventListener('click', onLogout);

  await decideStart();
});

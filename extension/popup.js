'use strict';

// VIVATLAS Clipper — the whole flow lives here: pick a server, sign in (with MFA),
// then capture the current page or a pasted link and send it to VIVATLAS.
//
// Auth: /api/ext/login returns a token the extension stores and sends as a Bearer
// header on its own calls. We ALSO set that token as the vivatlas_session cookie via
// the cookies API, so "Open VIVATLAS" lands in the web UI already signed in.

const COOKIE_NAME = 'vivatlas_session';
const SESSION_DAYS = 30;

let state = { server: '', token: '', email: '' };
let ticket = '';        // between password and MFA; not persisted
let backupMode = false; // MFA: app code vs backup code
let visibility = 'private';

const $ = (id) => document.getElementById(id);

// --- storage -------------------------------------------------------------

function load() {
  return new Promise((res) => {
    chrome.storage.local.get(['server', 'token', 'email'], (v) => {
      state = { server: v.server || '', token: v.token || '', email: v.email || '' };
      res();
    });
  });
}
function save() {
  return new Promise((res) => chrome.storage.local.set(state, res));
}

// --- ui helpers ----------------------------------------------------------

function show(step) {
  ['step-server', 'step-login', 'step-mfa', 'step-main'].forEach((id) => {
    $(id).hidden = id !== step;
  });
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

// Set/clear the session cookie so the web UI opens authenticated.
function setSessionCookie() {
  if (!chrome.cookies) return;
  chrome.cookies.set({
    url: state.server,
    name: COOKIE_NAME,
    value: state.token,
    path: '/',
    httpOnly: true,
    secure: state.server.startsWith('https'),
    sameSite: 'lax',
    expirationDate: Math.floor(Date.now() / 1000) + SESSION_DAYS * 24 * 3600,
  }, () => void chrome.runtime.lastError);
}
function clearSessionCookie() {
  if (!chrome.cookies) return;
  chrome.cookies.remove({ url: state.server, name: COOKIE_NAME }, () => void chrome.runtime.lastError);
}

// --- capture -------------------------------------------------------------

async function activeTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs && tabs[0] ? tabs[0] : null;
}

// Grab the readable text of the active tab (like a read-later clipper). url/title come
// from the tab even when the page can't be scripted (chrome://, PDF viewer, store).
async function grab() {
  const tab = await activeTab();
  const out = { url: (tab && tab.url) || '', title: (tab && tab.title) || '', text: '' };
  if (tab && tab.id != null) {
    try {
      const results = await chrome.scripting.executeScript({
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
  $('whoami').textContent = (state.email ? state.email + ' · ' : '') + state.server;
  setVisibility('private');
  await refillCapture();
}

async function refillCapture() {
  $('grab-status').textContent = 'reading page…';
  const cap = await grab();
  $('url').value = cap.url;
  $('title').value = cap.title;
  if (cap.text) {
    $('preview').textContent = cap.text;
    $('preview').hidden = false;
    $('grab-status').textContent = 'captured ' + cap.text.length + ' chars';
  } else {
    $('preview').hidden = true;
    $('grab-status').textContent = 'no page text — the link will still be saved';
  }
}

function setVisibility(v) {
  visibility = v;
  $('vis-private').classList.toggle('on', v === 'private');
  $('vis-public').classList.toggle('on', v === 'public');
}

// --- flow ----------------------------------------------------------------

async function decideStart() {
  if (!state.server) return show('step-server');
  if (!state.token) { $('login-server').textContent = state.server; return show('step-login'); }
  const r = await api('/session');
  if (r.ok) { show('step-main'); return initMain(); }
  // token no longer valid — sign in again, keep the server.
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
  // Ask for access to this host (needed for the API calls and the cookie).
  const granted = await new Promise((res) =>
    chrome.permissions.request({ origins: [origin + '/*'] }, res));
  if (!granted) return banner('Permission for that server was declined.', 'err');
  state.server = server;
  await save();
  $('login-server').textContent = server;
  $('server').value = '';
  show('step-login');
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
  await save();
  setSessionCookie();
  $('password').value = '';
  show('step-main');
  await initMain();
}

async function onSend() {
  const url = $('url').value.trim();
  const title = $('title').value.trim();
  const text = $('preview').hidden ? '' : $('preview').textContent;
  if (!url && !text) return banner('Nothing to add — capture a page or paste a link.', 'err');
  $('send').disabled = true;
  banner('Sending…', '');
  const r = await api('/add', 'POST', { url, title, text, shared: visibility === 'public' });
  $('send').disabled = false;
  if (!r.ok) {
    if (r.status === 401) { state.token = ''; await save(); banner('Session expired — sign in again.', 'err'); return show('step-login'); }
    return banner(r.data.error || 'Could not add.', 'err');
  }
  const where = visibility === 'public' ? 'public catalogue' : 'your private space';
  banner(r.data.kind === 'import' ? 'Importing the repo into ' + where + '…' : 'Saved to ' + where + '.', 'ok');
}

async function onLogout() {
  await api('/logout', 'POST');
  clearSessionCookie();
  state.token = '';
  state.email = '';
  await save();
  show('step-login');
  $('login-server').textContent = state.server;
}

function onOpenWeb() { chrome.tabs.create({ url: state.server }); }

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

  $('grab').addEventListener('click', initMain);
  $('vis-private').addEventListener('click', () => setVisibility('private'));
  $('vis-public').addEventListener('click', () => setVisibility('public'));
  $('send').addEventListener('click', onSend);
  $('open-web').addEventListener('click', onOpenWeb);
  $('logout').addEventListener('click', onLogout);

  await decideStart();
});

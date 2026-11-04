/**
 * main.js — App shell
 * =====================
 * Auth (Google or guest), health badge + setup banner, tab routing.
 * The three tools — Speech Director, Communication Coach, Latency Lab —
 * live in app.js.
 */

import { api } from './api.js';
import { $, $$, esc, show, activateTab, hideToast } from './ui.js';

const TABS = ['director', 'coach', 'analytics'];

function setBadge(online) {
  const badge = $('#connection-badge');
  if (!badge) return;
  badge.className = `badge ${online ? 'badge--online' : 'badge--offline'}`;
  const label = badge.querySelector('.badge-label');
  if (label) label.textContent = online ? 'Online' : 'Offline';
}

async function checkAuth() {
  try {
    const data = await api.get('/api/auth/me');
    if (!data.authenticated) return false;
    const chip = $('#user-chip');
    const name = (data.user && data.user.name) || 'Guest';
    chip.innerHTML = `${data.user && data.user.picture ? `<img src="${esc(data.user.picture)}" alt="" referrerpolicy="no-referrer">` : ''}<span>${esc(name)}</span>${data.guest ? '<span class="pill pill--ghost">guest</span>' : ''}`;
    show($('#btn-logout'), !data.guest);
    return true;
  } catch (err) {
    return false;
  }
}

/** Health → badge, plus a banner when a key is missing or the configured model is gone. */
async function showSetup() {
  const banner = $('#setup-banner');
  try {
    const h = await api.get('/api/health');
    setBadge(h && h.status === 'online');
    const i = h.integrations || {};
    const problems = [];
    if (!i.groq) problems.push('Set <code>GROQ_API_KEY</code> in <code>.env</code> — the Director and Coach need it.');
    if (!i.azure_tts) problems.push('Set <code>AZURE_SPEECH_KEY</code> in <code>.env</code> — the Director cannot speak without it.');
    if (h.llm && h.llm.available === false) {
      problems.push(`Groq no longer offers <code>${esc(h.llm.model)}</code>. Pick one from <code>/api/health</code> → <code>llm.alternatives</code> and set <code>LLM_MODEL</code> in <code>.env</code>.`);
    }
    if (problems.length) {
      banner.innerHTML = problems.join('<br>');
      show(banner, true);
    }
  } catch (e) {
    setBadge(false);
  }
}

function setupTabs() {
  $$('.tab-btn').forEach((btn) => btn.addEventListener('click', () => {
    activateTab(btn.dataset.tab);
    history.replaceState(null, '', `#${btn.dataset.tab}`);
  }));
  const initial = (location.hash || '#director').slice(1);
  activateTab(TABS.includes(initial) ? initial : 'director');
}

async function boot() {
  const ok = await checkAuth();
  show($('#login-screen'), !ok);
  show($('#dashboard-ui'), ok);
  const loginBtn = $('#google-login-btn');
  if (loginBtn) loginBtn.addEventListener('click', () => { window.location.href = '/api/auth/login'; });
  if (!ok) return;

  $('#btn-logout').addEventListener('click', async () => {
    try { await api.post('/api/auth/logout'); } catch (e) { /* noop */ }
    window.location.reload();
  });
  const toastClose = $('#toast-close-btn');
  if (toastClose) toastClose.addEventListener('click', hideToast);

  setupTabs();
  showSetup();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

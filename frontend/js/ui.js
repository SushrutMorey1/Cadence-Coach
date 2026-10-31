/**
 * ui.js — Shared UI helpers (query, escaping, toast, tabs, formatting)
 */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function esc(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function show(el, visible = true) {
  if (!el) return;
  el.classList.toggle('hidden', !visible);
}

let toastTimer = null;
export function toast(message, kind = 'error', ms = 5000) {
  const el = $('#error-toast');
  const msg = $('#error-message');
  if (!el || !msg) { console[kind === 'error' ? 'error' : 'log'](message); return; }
  msg.textContent = message;
  el.classList.remove('hidden', 'toast--info', 'toast--success');
  if (kind !== 'error') el.classList.add(`toast--${kind}`);
  void el.offsetWidth;               // restart the slide-in transition
  el.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, ms);
}

export function hideToast() {
  const el = $('#error-toast');
  if (!el) return;
  el.classList.remove('visible');
  clearTimeout(toastTimer);
  setTimeout(() => el.classList.add('hidden'), 350);
}

/** Switch the main tab (buttons carry data-tab, panels are #tab-<name>). */
export function activateTab(tabId) {
  $$('.tab-btn').forEach((btn) => {
    const active = btn.dataset.tab === tabId;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active);
  });
  $$('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `tab-${tabId}`));
  document.dispatchEvent(new CustomEvent('tabchange', { detail: tabId }));
}

export function fmtMs(ms) {
  if (ms == null || Number.isNaN(ms)) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
}

/** Colour for a 0–10 score — one scale for the Coach's ring, cards and history. */
export function scoreColor(score) {
  if (score == null) return 'var(--text-muted)';
  if (score >= 8) return 'var(--color-success)';
  if (score >= 6) return 'var(--accent-mid)';
  if (score >= 4) return 'var(--color-warning)';
  return 'var(--color-danger)';
}

/* ═══════════════════════════════════════════════════════════════
   Cadence Coach — Speech Director · Communication Coach · Latency Lab
   An ES module so it shares ui.js (toast, escaping, score colours)
   with the app shell in js/main.js. Vanilla JS — zero dependencies.
   ═══════════════════════════════════════════════════════════════ */

import { esc, toast, scoreColor, fmtMs } from './js/ui.js';

// ── DOM Helpers ───────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);

// ── Cached DOM References ────────────────────────────────
const els = {
  // Tab 1 — Director
  directorText: $('#director-text'),
  directorStyle: $('#director-style'),
  directorPresets: $('#director-presets'),
  directorVoice: $('#director-voice'),
  btnRewrite: $('#btn-rewrite'),
  btnSpeak: $('#btn-speak'),
  directorPlaceholder: $('#director-placeholder'),
  directorResults: $('#director-results'),
  directorLoading: $('#director-loading'),
  directorLoadingText: $('#director-loading-text'),
  directorActions: $('#director-actions'),
  btnDirectorCopy: $('#btn-director-copy'),
  btnDirectorDownload: $('#btn-director-download'),
  btnDirectorEdit: $('#btn-director-edit'),
  btnDirectorAgain: $('#btn-director-again'),
  directorDisplayScript: $('#director-display-script'),
  directorEditBar: $('#director-edit-bar'),
  btnDirectorSpeakEdit: $('#btn-director-speak-edit'),
  btnDirectorCancelEdit: $('#btn-director-cancel-edit'),
  directorStats: $('#director-stats'),
  directorTtsScript: $('#director-tts-script'),
  directorTtsExpander: $('#director-tts-expander'),
  directorAudioCard: $('#director-audio-card'),
  directorDirectionPill: $('#director-direction-pill'),
  directorAudio: $('#director-audio'),
  directorAudioMeta: $('#director-audio-meta'),
  directorPlay: $('#director-play'),
  directorPlayLabel: $('#director-play-label'),
  directorPlayFill: $('#director-play-fill'),
  btnDirectorStop: $('#btn-director-stop'),
  directorLatency: $('#director-latency'),
  directorAudioTools: $('#director-audio-tools'),
  btnDirectorFlat: $('#btn-director-flat'),
  directorAudioDownload: $('#director-audio-download'),
  directorFlatWrap: $('#director-flat-wrap'),
  directorFlatAudio: $('#director-flat-audio'),

  // Tab 2 — Coach
  toggleText: $('#toggle-text'),
  toggleAudio: $('#toggle-audio'),
  coachTextMode: $('#coach-text-mode'),
  coachAudioMode: $('#coach-audio-mode'),
  coachText: $('#coach-text'),
  coachContext: $('#coach-context'),
  btnAnalyze: $('#btn-analyze'),
  btnTranscribe: $('#btn-transcribe'),
  canvas: $('#waveform-canvas'),
  timer: $('#recording-timer'),
  recordBtn: $('#record-btn'),
  recordLabel: $('#record-label'),
  micIcon: $('#mic-icon'),
  stopIcon: $('#stop-icon'),
  uploadZone: $('#upload-zone'),
  audioFileInput: $('#audio-file-input'),
  uploadBrowseBtn: $('#upload-browse-btn'),
  uploadFileInfo: $('#upload-file-info'),
  uploadFilename: $('#upload-filename'),
  uploadRemoveBtn: $('#upload-remove-btn'),
  coachAudioPreview: $('#coach-audio-preview'),
  coachPreviewAudio: $('#coach-preview-audio'),

  // Coach — transcription metrics
  coachTranscriptionMetrics: $('#coach-transcription-metrics'),
  speakingMetricsRow: $('#speaking-metrics-row'),
  wpmBadgeWrap: $('#wpm-badge-wrap'),
  fillerBreakdown: $('#filler-breakdown'),
  transcribedTextBody: $('#transcribed-text-body'),

  // Coach — results
  coachPlaceholder: $('#coach-placeholder'),
  coachLoading: $('#coach-loading'),
  coachLoadingText: $('#coach-loading-text'),
  coachLive: $('#coach-live'),
  coachLiveStatus: $('#coach-live-status'),
  coachLiveScore: $('#coach-live-score'),
  coachLiveList: $('#coach-live-list'),
  coachResults: $('#coach-results'),
  scoreRingFill: $('#score-ring-fill'),
  scoreValue: $('#score-value'),
  scoreSummary: $('#score-summary'),
  coachDeliveryNotes: $('#coach-delivery-notes'),
  categoryScoresRow: $('#category-scores-row'),
  actionsList: $('#actions-list'),
  coachingDetails: $('#coaching-details'),
  coachHistory: $('#coach-history'),
  coachHistoryList: $('#coach-history-list'),

  // Tab 3 — Latency Lab
  analyticsEmpty: $('#analytics-empty'),
  analyticsContent: $('#analytics-content'),
  metricsRange: $('#metrics-range'),
  btnExportMetrics: $('#btn-export-metrics'),
  kpiRow: $('#kpi-row'),
  llmDetailTable: $('#llm-detail-table tbody'),
  ttsDetailTable: $('#tts-detail-table tbody'),
  endpointTable: $('#endpoint-table tbody'),
  cycleSection: $('#cycle-section'),
  cycleTracesContainer: $('#cycle-traces-container'),
  recentTable: $('#recent-table tbody'),
  btnRefreshMetrics: $('#btn-refresh-metrics'),
  endpointSection: $('#endpoint-section'),
  recentSection: $('#recent-section'),
};

// ── State ─────────────────────────────────────────────────
const CIRCUMFERENCE = 2 * Math.PI * 52;
const MAX_RECORDING_SEC = 10 * 60;   // Whisper's 25 MB cap ≈ 10 min of webm/opus
let mediaRecorder = null;
let audioStream = null;
let audioChunks = [];
let isRecording = false;
let timerInterval = null;
let recordStartTime = 0;
let audioCtx = null;
let analyser = null;
let animFrameId = null;
let uploadedFile = null;         // File object from upload
let previewUrl = null;           // object URL for the recorded/uploaded clip
let transcribedText = '';        // Text from transcription (used by Analyze)
let transcriptionData = null;    // Full transcription response
let coachMeta = {};              // { source } for the stored run ('text' | 'audio')
let lastDirector = null;         // { text, style, voice, speak, displayScript, ttsScript, audioFilename, flatUrl }
let directorTimes = null;        // { start, scriptAt, firstAudioAt } — the latency strip
let editBackup = null;

// ═══════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════

function init() {
  loadVoices();
  renderStylePresets();
  setupWarmup();
  setupDirectorEvents();
  setupCoachEvents();
  setupAnalyticsEvents();
  drawIdleWaveform();
  // Tabs are owned by main.js (ui.activateTab); we only react to the switch.
  document.addEventListener('tabchange', (e) => { if (e.detail === 'analytics') loadAnalytics(); });
  if (location.hash === '#analytics') loadAnalytics();
}

// ═══════════════════════════════════════════════════════════
// TAB 1 — SPEECH DIRECTOR
// ═══════════════════════════════════════════════════════════

const STYLE_PRESETS = [
  ['YC pitch', 'Confident, professional YC founder answering a partner — plain words, specific numbers, no buzzwords, under 60 seconds'],
  ['Keynote', 'Inspirational keynote speaker — rule of three, strategic pauses, a rising emotional arc that lands'],
  ['Calm CEO', 'Calm, authoritative executive addressing the whole company — measured and decisive'],
  ['Warm teacher', 'Warm, patient teacher explaining to curious students — clear, encouraging, vivid examples'],
  ['Sales close', 'Consultative sales closer — direct, empathetic, builds urgency without pressure'],
  ['Storyteller', 'Intimate storyteller — slow, sensory, lets the silences do the work'],
  ['Newscast', 'Broadcast news anchor — crisp, neutral, precisely paced'],
];

function renderStylePresets() {
  if (!els.directorPresets) return;
  els.directorPresets.innerHTML = STYLE_PRESETS
    .map(([label, style]) => `<button type="button" class="preset-chip" data-style="${esc(style)}">${esc(label)}</button>`)
    .join('');
  els.directorPresets.querySelectorAll('.preset-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      els.directorStyle.value = chip.dataset.style;
      els.directorPresets.querySelectorAll('.preset-chip').forEach((c) => c.classList.toggle('is-active', c === chip));
      els.directorStyle.focus();
    });
  });
  els.directorStyle.addEventListener('input', () => {
    els.directorPresets.querySelectorAll('.preset-chip').forEach((c) => c.classList.toggle('is-active', c.dataset.style === els.directorStyle.value));
  });
}

async function loadVoices() {
  try {
    const res = await fetch('/api/voices');
    if (!res.ok) throw 0;
    const data = await res.json();
    const voices = data.available_voices || {};
    const defaultVoice = data.default_voice || '';

    els.directorVoice.innerHTML = Object.entries(voices)
      .map(([alias, full]) =>
        `<option value="${esc(alias)}" ${full === defaultVoice ? 'selected' : ''}>${esc(alias)} — ${esc(full)}</option>`
      )
      .join('');
  } catch {
    els.directorVoice.innerHTML = '<option value="davis">davis — en-US-DavisNeural</option>';
  }
}

function setupDirectorEvents() {
  els.btnRewrite.addEventListener('click', () => runDirector({ speak: false }));
  els.btnSpeak.addEventListener('click', () => runDirector({ speak: true }));
  els.btnDirectorCopy.addEventListener('click', copyScript);
  els.btnDirectorDownload.addEventListener('click', downloadScript);
  els.btnDirectorEdit.addEventListener('click', () => setEditMode(true));
  els.btnDirectorCancelEdit.addEventListener('click', () => setEditMode(false, { restore: true }));
  els.btnDirectorSpeakEdit.addEventListener('click', speakEditedScript);
  els.btnDirectorAgain.addEventListener('click', () => { if (lastDirector) runDirector({ speak: lastDirector.speak }); });
  els.btnDirectorFlat.addEventListener('click', compareFlatRead);
  els.btnDirectorStop.addEventListener('click', () => segPlayer.stop());
  // Replaying the full file must not overlap the live pieces
  els.directorAudio.addEventListener('play', () => segPlayer.stop());
}

// ── Gapless segment player ────────────────────────────────
// The server sends the performance in pieces (first sentence first, the
// rest rendering in parallel). Each MP3 piece is decoded and scheduled back
// to back on one AudioContext, so playback starts as soon as piece 1 lands
// and never gaps between pieces.
const segState = { ready: 0, done: false };
const segPlayer = {
  ctx: null, chain: Promise.resolve(), gen: 0, nextAt: 0, startedAt: null, sources: [], scheduledSec: 0, raf: null,
  ensure() {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => { /* needs a gesture */ });
    return this.ctx;
  },
  enqueue(url) {
    const gen = this.gen;
    this.chain = this.chain.then(async () => {
      const ctx = this.ensure();
      const res = await fetch(url);
      if (!res.ok) throw new Error(`segment ${res.status}`);
      const audio = await ctx.decodeAudioData(await res.arrayBuffer());
      if (gen !== this.gen) return;
      const src = ctx.createBufferSource();
      src.buffer = audio;
      src.connect(ctx.destination);
      const at = Math.max(ctx.currentTime + 0.03, this.nextAt);
      src.start(at);
      if (this.startedAt === null) this.startedAt = at;
      this.nextAt = at + audio.duration;
      this.scheduledSec += audio.duration;
      this.sources.push(src);
      this.tick();
    }).catch((err) => console.warn('segment playback', err));
    return this.chain;
  },
  stop() {
    this.gen++;
    this.sources.forEach((s) => { try { s.stop(); } catch (e) { /* already ended */ } });
    this.sources = [];
    this.nextAt = 0;
    this.startedAt = null;
    this.scheduledSec = 0;
    this.chain = Promise.resolve();
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = null;
    els.directorPlay.classList.add('hidden');
  },
  tick() {
    if (this.raf) return;
    const step = () => {
      this.raf = null;
      if (!this.ctx || this.startedAt === null) return;
      const elapsed = Math.max(0, this.ctx.currentTime - this.startedAt);
      const pct = this.scheduledSec ? Math.min(100, elapsed / this.scheduledSec * 100) : 0;
      els.directorPlayFill.style.width = `${pct.toFixed(1)}%`;
      const stillPlaying = this.ctx.currentTime < this.nextAt;
      if (stillPlaying || !segState.done) {
        this.raf = requestAnimationFrame(step);
      } else {
        els.directorPlayLabel.textContent = 'Played — use the player below to replay';
        setTimeout(() => { if (!this.raf) els.directorPlay.classList.add('hidden'); }, 1500);
      }
    };
    this.raf = requestAnimationFrame(step);
  },
};

function onAudioSegment(ev) {
  if (ev.ok === false) {
    toast(`Piece ${ev.index + 1} failed: ${ev.error || 'unknown'}`, 'info', 4000);
    return;
  }
  segState.ready += 1;
  if (directorTimes && directorTimes.firstAudioAt == null) directorTimes.firstAudioAt = performance.now();
  els.directorPlay.classList.remove('hidden');
  els.directorPlayLabel.textContent = `Playing · ${segState.ready} piece${segState.ready === 1 ? '' : 's'} rendered so far`;
  els.directorAudioMeta.innerHTML = pulsePill(`Performing — ${segState.ready} piece${segState.ready === 1 ? '' : 's'} ready`);
  segPlayer.enqueue(ev.url);
}

/** What the user actually felt, measured in the browser, all from the click. */
function renderLatencyStrip(audio) {
  if (!directorTimes) return;
  const t = directorTimes;
  const parts = [];
  if (t.firstAudioAt) parts.push(`first word <strong>${fmtMs(t.firstAudioAt - t.start)}</strong>`);
  if (t.scriptAt) parts.push(`script done ${fmtMs(t.scriptAt - t.start)}`);
  parts.push(`all audio ${fmtMs(performance.now() - t.start)}`);
  if (audio && audio.segments) parts.push(`${audio.segments} piece${audio.segments === 1 ? '' : 's'}, rendered in parallel`);
  els.directorLatency.innerHTML = parts.map((p) => `<span>${p}</span>`).join('');
}

// ── Connection warm-up ────────────────────────────────────
// The first sentence of speech used to pay ~0.8 s for the TLS + WebSocket handshake to
// Azure. Opening it while the user is still typing hides that cost; the server call is
// idempotent, so it is simply repeated at most every 40 s while they work.
let lastWarm = 0;
function warmVoice() {
  if (Date.now() - lastWarm < 40000) return;
  lastWarm = Date.now();
  fetch('/api/warm', { method: 'POST' }).catch(() => { /* best effort */ });
}

function setupWarmup() {
  [els.directorText, els.directorStyle, els.directorVoice, els.btnSpeak].forEach((el) => {
    el.addEventListener('focus', warmVoice);
    el.addEventListener('pointerenter', warmVoice, { once: true });
  });
  els.directorPresets.addEventListener('click', warmVoice);
  document.addEventListener('tabchange', (e) => { if (e.detail === 'director') warmVoice(); });
  warmVoice();
}

// ── SSE Stream Reader Helper ──────────────────────────────
async function readSSEStream(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  const handleLine = (rawLine) => {
    const line = rawLine.trim();
    if (!line.startsWith('data:')) return;
    const payloadStr = line.slice(5).trim();
    if (!payloadStr || payloadStr === '{}') return;
    let event = null;
    try { event = JSON.parse(payloadStr); } catch (e) { return; } // heartbeat / non-JSON
    if (event) onEvent(event);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop(); // keep trailing incomplete line
    for (let i = 0; i < lines.length; i++) handleLine(lines[i]);
  }
  if (buffer.trim()) handleLine(buffer);
}

function resetDirectorOutput() {
  segPlayer.stop();
  segState.ready = 0;
  segState.done = false;
  els.directorLatency.textContent = '';
  directorTimes = { start: performance.now(), scriptAt: null, firstAudioAt: null };
  // Pause, detach and reload any existing audio to prevent stale state/locks
  [els.directorAudio, els.directorFlatAudio].forEach((audio) => {
    try { audio.pause(); audio.currentTime = 0; } catch (e) { /* noop */ }
    audio.removeAttribute('src');
    audio.load();
  });
  els.directorAudio.classList.add('hidden');
  els.directorAudioMeta.textContent = '';
  els.directorAudioCard.classList.add('hidden');
  els.directorDirectionPill.classList.add('hidden');
  els.directorAudioTools.classList.add('hidden');
  els.directorFlatWrap.classList.add('hidden');
  els.directorActions.classList.add('hidden');
  els.directorStats.textContent = '';
  els.directorTtsScript.textContent = '';
  els.directorTtsExpander.removeAttribute('open');
  setEditMode(false);
  els.directorDisplayScript.innerHTML = '<span class="streaming-text"></span><span class="streaming-cursor"></span>';
  showDirectorState('results');
}

function removeCursor() {
  const cursor = els.directorDisplayScript.querySelector('.streaming-cursor');
  if (cursor) cursor.remove();
}

const pulsePill = (text) => `<span class="status-pill status-pill--pulse">${esc(text)}</span>`;

/** Groq's per-minute budget is used up: count the wait down instead of showing a frozen screen. */
let rateLimitTimer = null;
function showRateLimitWait(event, pillTarget) {
  const total = Math.ceil(event.wait_s || 15);
  let left = total;
  const msg = (s) => `Groq's per-minute token budget is used up — resuming in ${s}s (free tier: 8,000 tokens/min)`;
  toast(msg(left), 'info', (total + 1) * 1000);
  clearInterval(rateLimitTimer);
  rateLimitTimer = setInterval(() => {
    left -= 1;
    if (left <= 0) { clearInterval(rateLimitTimer); rateLimitTimer = null; return; }
    const el = $('#error-message');
    if (el && el.textContent.startsWith("Groq's per-minute")) el.textContent = msg(left);
    if (pillTarget) pillTarget.innerHTML = pulsePill(`Waiting for Groq's token budget — ${left}s`);
  }, 1000);
  if (pillTarget) pillTarget.innerHTML = pulsePill(`Waiting for Groq's token budget — ${left}s`);
}

/** One handler for both buttons — the stream is identical, speak=true just adds the audio events. */
async function runDirector({ speak }) {
  const text = els.directorText.value.trim();
  const style = els.directorStyle.value.trim();
  const voice = els.directorVoice.value;
  if (!text || !style) return toast('Please enter both text and a target style.');

  setDirectorBtns(true);
  resetDirectorOutput();
  lastDirector = { text, style, voice, speak, displayScript: '', ttsScript: '', audioFilename: null, flatUrl: null };

  if (speak) {
    segPlayer.ensure();   // created inside the click, so scheduled playback is allowed
    els.directorAudioCard.classList.remove('hidden');
    els.directorAudio.classList.add('hidden');
    els.directorAudioMeta.innerHTML = pulsePill('Drafting the script…');
  }

  const streamEl = els.directorDisplayScript.querySelector('.streaming-text');
  const startedAt = performance.now();
  let streamText = '';
  let completedNormally = false;
  let audioReady = false;

  try {
    const res = await fetch(speak ? '/api/rewrite-and-speak/stream' : '/api/rewrite/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(speak ? { user_text: text, target_style: style, voice } : { user_text: text, target_style: style }),
    });
    if (!res.ok) {
      const d = await safeJson(res);
      throw new Error(getSafeErrorMsg(d, `${speak ? 'Rewrite & speak' : 'Rewrite'} failed (${res.status})`));
    }

    await readSSEStream(res, (event) => {
      switch (event.type) {
        case 'token':
          streamText += event.text;
          if (streamEl) streamEl.textContent = streamText;
          break;
        case 'retry':
          streamText = '';
          if (streamEl) streamEl.textContent = '';
          toast('The model stumbled — taking another run at it…', 'info', 3000);
          break;
        case 'rate_limited':
          showRateLimitWait(event, speak ? els.directorAudioMeta : null);
          break;
        case 'script_done':
          onScriptDone(event, streamText);
          if (directorTimes) directorTimes.scriptAt = performance.now();
          completedNormally = true;
          break;
        case 'audio_segment':
          onAudioSegment(event);
          break;
        case 'audio_done':
          audioReady = onAudioDone(event.audio || {}, voice);
          renderLatencyStrip(event.audio || {});
          break;
        case 'audio_error':
          els.directorAudioMeta.textContent = `Audio synthesis error: ${event.message || 'unknown'}`;
          break;
        case 'done':
          renderDirectorStats(event, performance.now() - startedAt);
          break;
        case 'error':
          throw new Error(event.message || 'Stream failed');
        default:
          break;
      }
    });

    if (speak && !audioReady) els.directorAudioCard.classList.add('hidden');
  } catch (err) {
    if (!completedNormally && !streamText) showDirectorState('placeholder');
    if (!audioReady) els.directorAudioCard.classList.add('hidden');
    toast(err.message);
  } finally {
    segState.done = true;
    removeCursor();
    setDirectorBtns(false);
  }
}

function onScriptDone(event, streamText) {
  const display = event.display_script || streamText;
  els.directorDisplayScript.textContent = display;
  els.directorTtsScript.textContent = event.tts_script || '';
  lastDirector.displayScript = display;
  lastDirector.ttsScript = event.tts_script || display;
  els.directorActions.classList.remove('hidden');
  removeCursor();
}

function onAudioDone(audio, fallbackVoice) {
  if (!audio.success || !(audio.filename || audio.file_path)) return false;
  const filename = audio.filename || audio.file_path.split('/').pop().split('\\').pop();
  els.directorAudio.src = `/audio/${filename}`;
  els.directorAudio.classList.remove('hidden');
  lastDirector.audioFilename = filename;

  const dur = audio.duration ? `${Number(audio.duration).toFixed(1)}s` : '';
  const bits = [dur, `Voice: ${audio.voice || fallbackVoice}`, audio.style ? `style: ${audio.style}` : '',
    audio.failed_segments ? `${audio.failed_segments} piece(s) skipped` : ''].filter(Boolean);
  els.directorAudioMeta.textContent = bits.join('  ·  ');
  segState.done = true;

  const d = audio.direction || {};
  const cues = (d.tags_kept || 0) + Math.max(0, (d.notes || 0) - (d.notes_dropped || 0));
  els.directorDirectionPill.className = `pill ${audio.directed ? 'pill--directed' : 'pill--flat'}`;
  els.directorDirectionPill.textContent = audio.directed ? `Voice-directed · ${cues} cue${cues === 1 ? '' : 's'}` : 'Plain read';
  els.directorDirectionPill.title = audio.directed
    ? 'The engine performed the pauses, pitch shifts and emphasis VOXIS wrote into the script.'
    : 'The script carried no usable voice direction, so this is a plain read.';
  els.directorDirectionPill.classList.remove('hidden');

  els.directorAudioDownload.href = `/audio/${filename}`;
  els.directorAudioDownload.download = filename;
  els.directorAudioTools.classList.remove('hidden');
  els.btnDirectorFlat.disabled = false;
  return true;
}

function renderDirectorStats(done, elapsedMs) {
  const words = (lastDirector && lastDirector.displayScript ? lastDirector.displayScript : '').split(/\s+/).filter(Boolean).length;
  const spoken = Math.round(words / 150 * 60);
  const parts = [
    `${words} words`,
    spoken ? `≈ ${spoken}s spoken` : '',
    `${fmtMs(done.total_latency_ms || elapsedMs)} end to end`,
    done.prompt_tokens != null ? `${done.prompt_tokens}+${done.completion_tokens} tokens${done.token_source === 'estimate' ? ' (est.)' : ''}` : '',
  ].filter(Boolean);
  els.directorStats.innerHTML = parts.map((p) => `<span>${esc(p)}</span>`).join('');
}

function showDirectorState(state) {
  els.directorPlaceholder.classList.toggle('hidden', state !== 'placeholder');
  els.directorResults.classList.toggle('hidden', state !== 'results');
  els.directorLoading.classList.toggle('hidden', state !== 'loading');
}

function setDirectorBtns(disabled) {
  els.btnRewrite.disabled = disabled;
  els.btnSpeak.disabled = disabled;
  els.btnDirectorAgain.disabled = disabled;
}

// ── Script actions ────────────────────────────────────────
async function copyScript() {
  if (!lastDirector || !lastDirector.displayScript) return;
  try {
    await navigator.clipboard.writeText(lastDirector.displayScript);
    toast('Script copied.', 'success', 2000);
  } catch {
    toast('Clipboard blocked — select the text and copy manually.');
  }
}

function downloadScript() {
  if (!lastDirector || !lastDirector.displayScript) return;
  const body = `${lastDirector.displayScript}\n\n— style: ${lastDirector.style}\n— voice direction:\n${lastDirector.ttsScript}\n`;
  const blob = new Blob([body], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'cadence-script.txt';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function setEditMode(on, { restore = false } = {}) {
  const box = els.directorDisplayScript;
  if (on) {
    editBackup = box.textContent;
    box.setAttribute('contenteditable', 'true');
    box.focus();
  } else {
    if (restore && editBackup != null) box.textContent = editBackup;
    box.removeAttribute('contenteditable');
    editBackup = null;
  }
  els.directorEditBar.classList.toggle('hidden', !on);
  els.btnDirectorEdit.disabled = on;
}

/** POST /api/synthesize and return an object URL for the MP3 (plus headers). */
async function synthesizeBlob(payload) {
  const res = await fetch('/api/synthesize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const d = await safeJson(res);
    throw new Error(getSafeErrorMsg(d, `Synthesis failed (${res.status})`));
  }
  const blob = await res.blob();
  return {
    url: URL.createObjectURL(blob),
    duration: Number(res.headers.get('X-Audio-Duration')) || null,
    filename: res.headers.get('X-Audio-Filename') || '',
    directed: res.headers.get('X-Audio-Directed') === '1',
  };
}

async function speakEditedScript() {
  if (!lastDirector) return;
  const edited = els.directorDisplayScript.textContent.trim();
  if (!edited) return toast('The script is empty.');
  els.btnDirectorSpeakEdit.disabled = true;
  els.directorAudioCard.classList.remove('hidden');
  els.directorAudio.classList.add('hidden');
  els.directorAudioMeta.innerHTML = pulsePill('Performing your edit…');
  try {
    const out = await synthesizeBlob({ tts_script: edited, voice: lastDirector.voice, target_style: lastDirector.style });
    lastDirector.displayScript = edited;
    lastDirector.ttsScript = edited;
    lastDirector.flatUrl = null;
    els.directorTtsScript.textContent = edited;
    els.directorAudio.src = out.url;
    els.directorAudio.classList.remove('hidden');
    els.directorAudioMeta.textContent = `${out.duration ? `${out.duration.toFixed(1)}s  ·  ` : ''}Your edit  ·  Voice: ${lastDirector.voice}`;
    els.directorDirectionPill.className = `pill ${out.directed ? 'pill--directed' : 'pill--flat'}`;
    els.directorDirectionPill.textContent = out.directed ? 'Voice-directed' : 'Plain read';
    els.directorDirectionPill.classList.remove('hidden');
    if (out.filename) { els.directorAudioDownload.href = `/audio/${out.filename}`; els.directorAudioDownload.download = out.filename; }
    els.directorAudioTools.classList.remove('hidden');
    els.directorFlatWrap.classList.add('hidden');
    setEditMode(false);
    els.directorAudio.play().catch(() => { /* autoplay may be blocked */ });
  } catch (err) {
    toast(err.message);
    if (!lastDirector.audioFilename) els.directorAudioCard.classList.add('hidden');
  } finally {
    els.btnDirectorSpeakEdit.disabled = false;
  }
}

/** Same script, same voice, no direction — so the difference is audible, not claimed. */
async function compareFlatRead() {
  if (!lastDirector || !lastDirector.displayScript) return;
  if (lastDirector.flatUrl) {
    els.directorFlatWrap.classList.toggle('hidden');
    return;
  }
  els.btnDirectorFlat.disabled = true;
  els.btnDirectorFlat.textContent = 'Rendering flat read…';
  try {
    const out = await synthesizeBlob({ tts_script: lastDirector.displayScript, voice: lastDirector.voice, plain: true });
    lastDirector.flatUrl = out.url;
    els.directorFlatAudio.src = out.url;
    els.directorFlatWrap.classList.remove('hidden');
  } catch (err) {
    toast(err.message);
  } finally {
    els.btnDirectorFlat.disabled = false;
    els.btnDirectorFlat.textContent = 'Compare: flat read';
  }
}

// ═══════════════════════════════════════════════════════════
// TAB 2 — COMMUNICATION COACH
// ═══════════════════════════════════════════════════════════

const COACH_CATEGORIES = [
  ['vocabulary', 'Vocabulary'],
  ['tone_and_emotion', 'Tone & emotion'],
  ['voice_modulation', 'Voice modulation'],
  ['pacing_and_rhythm', 'Pacing & rhythm'],
  ['emphasis_and_delivery', 'Emphasis & delivery'],
  ['clarity_and_structure', 'Clarity & structure'],
];

function setupCoachEvents() {
  // Input mode toggle
  els.toggleText.addEventListener('click', () => setCoachInputMode('text'));
  els.toggleAudio.addEventListener('click', () => setCoachInputMode('audio'));

  // Recording
  els.recordBtn.addEventListener('click', toggleRecording);

  // File upload
  els.uploadBrowseBtn.addEventListener('click', () => els.audioFileInput.click());
  els.uploadZone.addEventListener('click', (e) => {
    if (e.target !== els.uploadBrowseBtn) els.audioFileInput.click();
  });
  els.audioFileInput.addEventListener('change', handleFileSelect);
  els.uploadRemoveBtn.addEventListener('click', clearUploadedFile);

  // Drag & drop
  els.uploadZone.addEventListener('dragover', (e) => { e.preventDefault(); els.uploadZone.classList.add('drag-over'); });
  els.uploadZone.addEventListener('dragleave', () => els.uploadZone.classList.remove('drag-over'));
  els.uploadZone.addEventListener('drop', handleFileDrop);

  // Transcribe button
  els.btnTranscribe.addEventListener('click', handleTranscribeAndAnalyze);

  // Analyze button
  els.btnAnalyze.addEventListener('click', handleAnalyze);

  loadCoachHistory();
}

function setCoachInputMode(mode) {
  els.toggleText.classList.toggle('active', mode === 'text');
  els.toggleText.setAttribute('aria-checked', mode === 'text');
  els.toggleAudio.classList.toggle('active', mode === 'audio');
  els.toggleAudio.setAttribute('aria-checked', mode === 'audio');

  els.coachTextMode.classList.toggle('hidden', mode !== 'text');
  els.coachAudioMode.classList.toggle('hidden', mode !== 'audio');
}

// ── File Upload ───────────────────────────────────────────
function handleFileSelect(e) {
  const file = e.target.files[0];
  if (file) setUploadedFile(file);
}

function handleFileDrop(e) {
  e.preventDefault();
  els.uploadZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file && (file.type.startsWith('audio/') || file.type.startsWith('video/'))) setUploadedFile(file);
  else toast('Please drop an audio file.');
}

function setUploadedFile(file) {
  uploadedFile = file;
  els.uploadFilename.textContent = `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  els.uploadFileInfo.classList.remove('hidden');
  els.uploadZone.classList.add('hidden');
  els.btnTranscribe.classList.remove('hidden');
  // Preview: hear the clip before sending it
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  els.coachPreviewAudio.src = previewUrl;
  els.coachAudioPreview.classList.remove('hidden');
}

function clearUploadedFile() {
  uploadedFile = null;
  els.audioFileInput.value = '';
  els.uploadFileInfo.classList.add('hidden');
  els.uploadZone.classList.remove('hidden');
  els.btnTranscribe.classList.add('hidden');
  els.coachAudioPreview.classList.add('hidden');
  els.coachPreviewAudio.removeAttribute('src');
  if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
}

// ── Recording ─────────────────────────────────────────────
async function toggleRecording() {
  if (isRecording) stopRecording();
  else await startRecording();
}

async function startRecording() {
  try {
    audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    toast('Microphone access denied. Please allow microphone permissions.');
    return;
  }

  audioChunks = [];
  const mimeType = getSupportedMimeType();
  const opts = mimeType ? { mimeType } : {};
  mediaRecorder = new MediaRecorder(audioStream, opts);

  mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
  mediaRecorder.onstop = () => {
    const type = mediaRecorder.mimeType || mimeType || 'audio/webm';
    const blob = new Blob(audioChunks, { type });
    setUploadedFile(new File([blob], `recording.${mimeToExt(type)}`, { type }));
  };
  mediaRecorder.onerror = () => { toast('Recording error.'); resetRecordingUI(); };

  mediaRecorder.start();
  isRecording = true;
  updateRecordingUI(true);
  startTimer();
  startWaveformVisualization();
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
  stopMediaTracks();
  isRecording = false;
  updateRecordingUI(false);
  stopTimer();
  stopWaveformVisualization();
}

function stopMediaTracks() {
  if (audioStream) { audioStream.getTracks().forEach((t) => t.stop()); audioStream = null; }
}

function getSupportedMimeType() {
  const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/ogg', 'audio/mp4'];
  for (const t of types) { if (MediaRecorder.isTypeSupported(t)) return t; }
  return '';
}

function updateRecordingUI(recording) {
  if (recording) {
    els.recordBtn.classList.add('recording');
    els.micIcon.classList.add('hidden');
    els.stopIcon.classList.remove('hidden');
    els.recordLabel.textContent = 'Stop';
    els.timer.classList.add('active');
  } else {
    els.recordBtn.classList.remove('recording');
    els.stopIcon.classList.add('hidden');
    els.micIcon.classList.remove('hidden');
    els.recordLabel.textContent = 'Record';
    els.timer.classList.remove('active');
  }
}

function resetRecordingUI() {
  isRecording = false;
  updateRecordingUI(false);
  stopTimer();
  stopWaveformVisualization();
  stopMediaTracks();
}

// ── Timer ─────────────────────────────────────────────────
function startTimer() {
  recordStartTime = Date.now();
  els.timer.textContent = '00:00';
  timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - recordStartTime) / 1000);
    els.timer.textContent = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`;
    if (elapsed >= MAX_RECORDING_SEC && isRecording) {
      stopRecording();
      toast('Recording stopped at 10 minutes — the transcriber accepts up to 25 MB.', 'info');
    }
  }, 250);
}

function stopTimer() { clearInterval(timerInterval); timerInterval = null; }

// ── Waveform ──────────────────────────────────────────────
function drawIdleWaveform() {
  const canvas = els.canvas;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const { w, h } = resizeCanvas(canvas);
  const cx = h / 2;
  ctx.clearRect(0, 0, w, h);

  const barCount = Math.floor(w / 4), barWidth = 2;
  const gap = (w - barCount * barWidth) / (barCount + 1);
  ctx.fillStyle = 'hsla(220, 15%, 25%, 0.5)';
  for (let i = 0; i < barCount; i++) {
    const x = gap + i * (barWidth + gap);
    const barH = 4 + Math.sin(i * 0.15) * 3;
    ctx.fillRect(x, cx - barH / 2, barWidth, barH);
  }
}

function startWaveformVisualization() {
  if (!audioStream) return;
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioCtx.createMediaStreamSource(audioStream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);

  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);
  const canvas = els.canvas;
  const ctx = canvas.getContext('2d');
  const { w, h } = resizeCanvas(canvas);
  const cx = h / 2;

  function draw() {
    animFrameId = requestAnimationFrame(draw);
    analyser.getByteFrequencyData(dataArray);
    ctx.clearRect(0, 0, w, h);

    const barCount = Math.min(bufferLength, Math.floor(w / 4));
    const barWidth = 2.5;
    const gap = (w - barCount * barWidth) / (barCount + 1);

    for (let i = 0; i < barCount; i++) {
      const val = dataArray[i] / 255;
      const barH = Math.max(3, val * (h * 0.8));
      const x = gap + i * (barWidth + gap);
      const ratio = i / barCount;
      const r = Math.round(110 + ratio * 19);
      const g = Math.round(231 - ratio * 91);
      const b = Math.round(183 + ratio * 65);
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${0.5 + val * 0.5})`;
      ctx.fillRect(x, cx - barH / 2, barWidth, barH);
    }
  }
  draw();
}

function stopWaveformVisualization() {
  if (animFrameId) { cancelAnimationFrame(animFrameId); animFrameId = null; }
  if (audioCtx) { audioCtx.close().catch(() => { }); audioCtx = null; analyser = null; }
  setTimeout(drawIdleWaveform, 50);
}

/** Size the backing store for the device pixel ratio and return CSS-pixel dimensions to draw in. */
function resizeCanvas(canvas) {
  const rect = canvas.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, rect.width), h = Math.max(1, rect.height);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  // setTransform (not scale) so repeated resizes don't compound the scale factor
  canvas.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
  return { w, h };
}

// ── Transcribe & Analyze ──────────────────────────────────
function speechMetricsFrom(data) {
  if (!data) return null;
  const keys = ['wpm', 'wpm_rating', 'duration_sec', 'word_count', 'filler_count', 'filler_rate_pct', 'filler_words',
    'repeated_words', 'long_pauses', 'longest_pause_sec', 'fastest_stretch'];
  const out = {};
  keys.forEach((k) => { if (data[k] != null) out[k] = data[k]; });
  return Object.keys(out).length ? out : null;
}

async function handleTranscribeAndAnalyze() {
  if (!uploadedFile) return toast('Please record or upload an audio file first.');

  showCoachState('loading');
  els.coachLoadingText.textContent = 'Transcribing your audio with Whisper…';
  els.btnTranscribe.disabled = true;

  try {
    // Step 1: Transcribe
    const formData = new FormData();
    formData.append('file', uploadedFile, uploadedFile.name);

    const trRes = await fetch('/api/transcribe', { method: 'POST', body: formData });
    if (!trRes.ok) {
      const d = await safeJson(trRes);
      throw new Error(getSafeErrorMsg(d, `Transcription failed (${trRes.status})`));
    }

    transcriptionData = await trRes.json();
    transcribedText = transcriptionData.text || '';
    renderTranscriptionMetrics(transcriptionData);

    if (transcribedText) {
      // Step 2: Auto-analyze, grounded in what was measured
      coachMeta = { source: 'audio' };
      await runCoachAnalysis(transcribedText, speechMetricsFrom(transcriptionData));
    } else {
      showCoachState('placeholder');
      toast('No speech detected in the audio.');
    }
  } catch (err) {
    showCoachState('placeholder');
    toast(err.message);
  } finally {
    els.btnTranscribe.disabled = false;
  }
}

async function handleAnalyze() {
  // Get text from either text mode or transcribed text
  const textMode = !els.coachTextMode.classList.contains('hidden');
  const text = textMode ? els.coachText.value.trim() : transcribedText;
  if (!text) return toast('Please enter some text or upload an audio file to analyze.');

  const metrics = textMode ? null : speechMetricsFrom(transcriptionData);
  coachMeta = { source: textMode ? 'text' : 'audio' };

  els.btnAnalyze.disabled = true;
  try {
    await runCoachAnalysis(text, metrics);
  } catch (err) {
    showCoachState('placeholder');
    toast(err.message);
  } finally {
    els.btnAnalyze.disabled = false;
  }
}

/** Stream the analysis; each category lights up as its score appears. */
async function runCoachAnalysis(text, speechMetrics) {
  const payload = { user_text: text };
  const context = els.coachContext.value.trim();
  if (context) payload.context = context;
  if (speechMetrics) payload.speech_metrics = speechMetrics;
  if (coachMeta.source) payload.source = coachMeta.source;

  showCoachState('live');
  resetCoachLive(speechMetrics);

  const res = await fetch('/api/coach/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const d = await safeJson(res);
    throw new Error(getSafeErrorMsg(d, `Coaching failed (${res.status})`));
  }

  let result = null;
  let modelError = null;
  let buffer = '';
  await readSSEStream(res, (event) => {
    if (event.type === 'token') { buffer += event.text; updateCoachLive(buffer); }
    else if (event.type === 'retry') { buffer = ''; resetCoachLive(speechMetrics); els.coachLiveStatus.textContent = 'Taking a second pass…'; }
    else if (event.type === 'rate_limited') { showRateLimitWait(event, null); els.coachLiveStatus.textContent = `Waiting for Groq's token budget (${Math.ceil(event.wait_s || 15)}s)…`; }
    else if (event.type === 'result') { result = event; }
    else if (event.type === 'error') { modelError = new Error(event.message || 'Coaching failed'); }
  });
  if (modelError && !result) throw modelError;
  if (!result) throw new Error('The coach returned no analysis.');

  renderCoachResults(result);
  showCoachState('results');
  loadCoachHistory();
  return result;
}

// ── Live progress while the JSON streams ──────────────────
function resetCoachLive(speechMetrics) {
  els.coachLiveList.innerHTML = COACH_CATEGORIES
    .map(([key, label]) => `<li class="coach-live-item" data-key="${key}"><span>${esc(label)}</span><span class="coach-live-val">…</span></li>`)
    .join('');
  els.coachLiveScore.textContent = '';
  els.coachLiveStatus.textContent = speechMetrics ? 'Reading your transcript and your measured delivery…' : 'Reading your text…';
}

function updateCoachLive(buf) {
  const overall = buf.match(/"overall_score"\s*:\s*([\d.]+)/);
  if (overall) {
    const s = Number(overall[1]);
    els.coachLiveScore.innerHTML = `<span style="color:${scoreColor(s)}">${s.toFixed(1)}</span><small>/10</small>`;
    els.coachLiveStatus.textContent = 'Scoring each dimension…';
  }
  COACH_CATEGORIES.forEach(([key]) => {
    const li = els.coachLiveList.querySelector(`[data-key="${key}"]`);
    if (!li || li.classList.contains('is-done')) return;
    const m = buf.match(new RegExp(`"${key}"\\s*:\\s*\\{[^{}]*?"score"\\s*:\\s*([\\d.]+)`));
    if (!m) return;
    const s = Number(m[1]);
    li.classList.add('is-done');
    const val = li.querySelector('.coach-live-val');
    val.textContent = s.toFixed(1);
    val.style.color = scoreColor(s);
  });
  if (/"top_3_actions"/.test(buf)) els.coachLiveStatus.textContent = 'Writing your action plan…';
}

// ── Render Transcription Metrics ──────────────────────────
function renderTranscriptionMetrics(data) {
  els.coachTranscriptionMetrics.classList.remove('hidden');

  // Metric cards
  const cards = [];
  if (data.wpm != null) cards.push(metricCardHTML(Math.round(data.wpm), 'Words/min'));
  if (data.duration_sec != null) cards.push(metricCardHTML(`${Number(data.duration_sec).toFixed(0)}s`, 'Duration'));
  if (data.word_count != null) cards.push(metricCardHTML(data.word_count, 'Words'));
  if (data.filler_count != null) cards.push(metricCardHTML(data.filler_count, 'Fillers'));
  if (data.long_pauses != null) cards.push(metricCardHTML(data.long_pauses, `Pauses > 0.7s${data.longest_pause_sec ? ` · max ${data.longest_pause_sec}s` : ''}`));
  if (data.repeated_words) cards.push(metricCardHTML(data.repeated_words, 'Stutters'));
  if (data.response_delay_sec != null) cards.push(metricCardHTML(`${Number(data.response_delay_sec).toFixed(1)}s`, 'Think time'));
  els.speakingMetricsRow.innerHTML = cards.join('');

  // WPM badge
  const wpmRating = data.wpm_rating || '';
  const wpmTips = {
    too_slow: 'Your pace is quite slow. Try to speed up slightly to maintain audience engagement.',
    slow: 'Slightly below average. Good for emphasis, but watch for losing momentum.',
    optimal: 'Excellent pacing — this is the sweet spot for engaging delivery.',
    fast: 'A bit fast. Try slowing down at key moments for impact.',
    too_fast: 'Too fast — your audience may struggle to absorb the message. Add deliberate pauses.',
  };
  let wpmHTML = '';
  if (wpmRating) {
    wpmHTML += `<span class="wpm-badge wpm-${esc(wpmRating)}">Pace: ${esc(titleCase(wpmRating))}</span>`
      + (wpmTips[wpmRating] ? `<p class="wpm-tip">${esc(wpmTips[wpmRating])}</p>` : '');
  }
  if (data.fastest_stretch && data.fastest_stretch.wpm) {
    wpmHTML += `<p class="stretch-note">Fastest stretch: <strong>${Math.round(data.fastest_stretch.wpm)} wpm</strong> — <q>${esc(String(data.fastest_stretch.text || '').slice(0, 140))}</q></p>`;
  }
  els.wpmBadgeWrap.innerHTML = wpmHTML;

  // Filler breakdown — every filler, not just the top four
  const fillerWords = data.filler_words || {};
  const fillerRating = data.filler_rating || '';
  const fillerRate = data.filler_rate_pct || 0;
  let fillerHTML = '';
  if (fillerRating) {
    fillerHTML += `<span class="filler-badge filler-${esc(fillerRating)}">Filler rate: ${Number(fillerRate).toFixed(1)}% (${esc(titleCase(fillerRating))})</span>`;
  }
  const fillerEntries = Object.entries(fillerWords).sort((a, b) => b[1] - a[1]);
  if (fillerEntries.length > 0) {
    fillerHTML += '<div class="filler-grid">';
    fillerEntries.forEach(([word, count]) => {
      fillerHTML += `<div class="filler-chip"><div class="filler-count">${count}</div><div class="filler-word">"${esc(word)}"</div></div>`;
    });
    fillerHTML += '</div>';
  }
  els.fillerBreakdown.innerHTML = fillerHTML;

  // Transcribed text with fillers highlighted in place
  els.transcribedTextBody.innerHTML = highlightTranscript(data.text || '', data.filler_spans || [])
    + ((data.filler_spans || []).length ? '<div class="transcript-legend"><span><mark class="filler-mark">um</mark> filler word</span></div>' : '');
}

function highlightTranscript(text, spans) {
  let out = '', pos = 0;
  spans.forEach((s) => {
    if (s.start < pos || s.end > text.length) return;
    out += esc(text.slice(pos, s.start)) + `<mark class="filler-mark" title="filler word">${esc(text.slice(s.start, s.end))}</mark>`;
    pos = s.end;
  });
  return out + esc(text.slice(pos));
}

const titleCase = (s) => String(s).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

// ── Render Coach Results ──────────────────────────────────
function renderCoachResults(data) {
  // Overall score
  animateScoreRing(data.overall_score);
  els.scoreSummary.textContent = data.summary || '';
  els.coachDeliveryNotes.textContent = data.delivery_notes || '';
  els.coachDeliveryNotes.classList.toggle('hidden', !data.delivery_notes);

  // Category score metric cards (colour = same scale as the Arena)
  els.categoryScoresRow.innerHTML = COACH_CATEGORIES
    .map(([key, label]) => {
      const score = data[key] && data[key].score;
      const value = score == null ? '—' : `<span style="color:${scoreColor(score)}">${Number(score).toFixed(1)}</span><small style="color:var(--text-muted)">/10</small>`;
      return metricCardHTML(value, label);
    })
    .join('');

  // Top 3 actions
  els.actionsList.innerHTML = (data.top_3_actions || [])
    .map((a) => `<li>${esc(a)}</li>`)
    .join('');

  // Detailed coaching sections as expanders
  let detailHTML = '';
  const para = (label, value) => value ? `<p class="coaching-label">${esc(label)}</p><p class="coaching-text">${esc(value)}</p>` : '';

  if (data.vocabulary) {
    detailHTML += buildCoachExpander('Vocabulary', data.vocabulary.score, () => {
      let html = '';
      html += listSection('Strengths', data.vocabulary.strengths);
      html += listSection('Improvements', data.vocabulary.improvements);
      if (data.vocabulary.weak_words?.length) {
        html += '<p class="coaching-label">Weak words</p><div>';
        data.vocabulary.weak_words.forEach((w) => {
          html += `<span class="weak-word-tag" title="${esc(w.reason)}">${esc(w.word)} <span class="arrow">→</span> <span class="suggestion">${esc(w.suggestion)}</span></span>`;
        });
        html += '</div>';
      }
      if (data.vocabulary.power_words_used?.length) {
        html += '<p class="coaching-label">Power words</p><div>';
        data.vocabulary.power_words_used.forEach((w) => { html += `<span class="power-word-tag">${esc(w)}</span>`; });
        html += '</div>';
      }
      return html;
    });
  }

  if (data.tone_and_emotion) {
    detailHTML += buildCoachExpander('Tone & emotion', data.tone_and_emotion.score, () =>
      para('Detected tone', data.tone_and_emotion.detected_tone)
      + para('Emotional arc', data.tone_and_emotion.emotional_arc)
      + listSection('Feedback', data.tone_and_emotion.feedback));
  }

  if (data.voice_modulation) {
    detailHTML += buildCoachExpander('Voice modulation', data.voice_modulation.score, () =>
      (data.voice_modulation.suggestions || [])
        .map((s) => `<div class="modulation-item"><p class="modulation-segment">"${esc(s.text_segment)}"</p><p class="modulation-instruction">${esc(s.instruction)}</p><span class="modulation-type">${esc(s.type)}</span></div>`)
        .join(''));
  }

  if (data.pacing_and_rhythm) {
    detailHTML += buildCoachExpander('Pacing & rhythm', data.pacing_and_rhythm.score, () =>
      para('Sentence length', data.pacing_and_rhythm.avg_sentence_length)
      + para('Variation', data.pacing_and_rhythm.variation_quality)
      + listSection('Feedback', data.pacing_and_rhythm.feedback));
  }

  if (data.emphasis_and_delivery) {
    detailHTML += buildCoachExpander('Emphasis & delivery', data.emphasis_and_delivery.score, () =>
      (data.emphasis_and_delivery.key_moments || [])
        .map((m) => `<div class="modulation-item"><p class="modulation-segment">"${esc(m.text_segment)}"</p><p class="modulation-instruction"><strong>${esc(m.technique)}</strong> — ${esc(m.reason)}</p></div>`)
        .join(''));
  }

  if (data.clarity_and_structure) {
    detailHTML += buildCoachExpander('Clarity & structure', data.clarity_and_structure.score, () =>
      para('Opening', data.clarity_and_structure.opening_strength)
      + para('Closing', data.clarity_and_structure.closing_strength)
      + para('Transitions', data.clarity_and_structure.transitions)
      + listSection('Feedback', data.clarity_and_structure.feedback));
  }

  els.coachingDetails.innerHTML = detailHTML;
}

function buildCoachExpander(title, score, contentFn) {
  const badge = score == null ? '' : `<span class="pill pill--ghost" style="color:${scoreColor(score)}">${Number(score).toFixed(1)}</span>`;
  return `<details class="expander"><summary class="expander-summary"><span class="coach-cat-row">${esc(title)} ${badge}</span></summary><div class="expander-body">${contentFn()}</div></details>`;
}

function listSection(label, items) {
  if (!items || items.length === 0) return '';
  return `<p class="coaching-label">${esc(label)}</p><ul class="coaching-list">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>`;
}

function showCoachState(state) {
  els.coachPlaceholder.classList.toggle('hidden', state !== 'placeholder');
  els.coachLoading.classList.toggle('hidden', state !== 'loading');
  els.coachLive.classList.toggle('hidden', state !== 'live');
  els.coachResults.classList.toggle('hidden', state !== 'results');
}

// ── Score history ─────────────────────────────────────────
async function loadCoachHistory() {
  try {
    const res = await fetch('/api/coach/history?limit=6');
    if (!res.ok) throw 0;
    const data = await res.json();
    const runs = data.runs || [];
    if (!runs.length) { els.coachHistory.classList.add('hidden'); return; }
    const sourceLabel = { text: 'typed', audio: 'recorded' };
    els.coachHistoryList.innerHTML = runs.map((r) => {
      const delta = r.delta == null ? '' : r.delta > 0 ? `<span class="delta delta--up">▲ ${r.delta.toFixed(1)}</span>`
        : r.delta < 0 ? `<span class="delta delta--down">▼ ${Math.abs(r.delta).toFixed(1)}</span>` : '<span class="delta delta--flat">=</span>';
      const when = r.created_at ? new Date(r.created_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
      const wpm = r.metrics && r.metrics.wpm ? `${Math.round(r.metrics.wpm)} wpm` : '';
      return `<div class="history-row">
        <div class="history-score" style="color:${scoreColor(r.overall_score)}">${r.overall_score == null ? '—' : Number(r.overall_score).toFixed(1)}</div>
        <div class="history-main">
          <div class="history-excerpt">${esc(r.text_excerpt || '')}</div>
          <div class="history-meta"><span>${esc(when)}</span><span>${esc(sourceLabel[r.source] || r.source || '')}</span>${wpm ? `<span>${esc(wpm)}</span>` : ''}</div>
        </div>
        <div>${delta}</div>
      </div>`;
    }).join('');
    els.coachHistory.classList.remove('hidden');
  } catch {
    els.coachHistory.classList.add('hidden');
  }
}

// ── Score Ring Animation ──────────────────────────────────
function animateScoreRing(score) {
  const displayScore = Number(score) || 0;
  const pct = (displayScore / 10) * 100;
  const offset = CIRCUMFERENCE - (pct / 100) * CIRCUMFERENCE;

  els.scoreRingFill.style.stroke = scoreColor(displayScore);
  els.scoreRingFill.style.strokeDashoffset = CIRCUMFERENCE;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      els.scoreRingFill.style.strokeDashoffset = offset;
    });
  });

  animateNumber(els.scoreValue, 0, displayScore, 1000, 1);
}

function animateNumber(el, start, end, duration, decimals = 0) {
  const startTime = performance.now();
  const diff = end - start;

  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = start + diff * eased;
    el.textContent = decimals > 0 ? current.toFixed(decimals) : Math.round(current);
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ═══════════════════════════════════════════════════════════
// TAB 3 — LATENCY LAB
// ═══════════════════════════════════════════════════════════

function setupAnalyticsEvents() {
  els.btnRefreshMetrics.addEventListener('click', loadAnalytics);
  els.metricsRange.addEventListener('change', loadAnalytics);
}

function rangeQuery() {
  const hours = els.metricsRange.value;
  return hours ? `hours=${encodeURIComponent(hours)}` : '';
}

async function loadAnalytics() {
  const q = rangeQuery();
  els.btnExportMetrics.href = `/api/metrics/export?format=csv${q ? `&${q}` : ''}`;
  try {
    const metricsRes = await fetch(`/api/metrics${q ? `?${q}` : ''}`);
    if (!metricsRes.ok) throw 0;
    const metrics = await metricsRes.json();

    const llm = metrics.llm || {};
    const tts = metrics.tts || {};
    const recent = metrics.recent_calls || [];
    const breakdown = metrics.endpoint_breakdown || [];
    const cycles = metrics.recent_cycles || [];

    const totalCalls = (llm.total_calls || 0) + (tts.total_calls || 0);
    if (totalCalls === 0) {
      els.analyticsEmpty.classList.remove('hidden');
      els.analyticsContent.classList.add('hidden');
      return;
    }
    els.analyticsEmpty.classList.add('hidden');
    els.analyticsContent.classList.remove('hidden');

    // KPI cards
    els.kpiRow.innerHTML = [
      metricCardHTML(llm.total_calls || 0, 'LLM calls'),
      metricCardHTML(fmtMs(llm.p50_latency_ms), 'p50 latency'),
      metricCardHTML(fmtMs(llm.p95_latency_ms), 'p95 latency'),
      metricCardHTML(Number(llm.total_tokens || 0).toLocaleString(), 'Tokens'),
      metricCardHTML(`$${Number(llm.total_cost_usd || 0).toFixed(4)}`, 'Cost'),
      metricCardHTML(tts.total_calls || 0, 'TTS calls'),
      metricCardHTML(`${Number(tts.total_duration_sec || 0).toFixed(0)}s`, 'Audio generated'),
    ].join('');

    // LLM detail table
    els.llmDetailTable.innerHTML = [
      detailRow('Prompt tokens', Number(llm.total_prompt_tokens || 0).toLocaleString()),
      detailRow('Completion tokens', Number(llm.total_completion_tokens || 0).toLocaleString()),
      detailRow('Avg latency', fmtMs(llm.avg_latency_ms)),
      detailRow('Min / max latency', `${fmtMs(llm.min_latency_ms)} / ${fmtMs(llm.max_latency_ms)}`),
      detailRow('Success rate', `${llm.success_count || 0}/${llm.total_calls || 0}`),
      detailRow('Errors', llm.error_count || 0, (llm.error_count || 0) > 0 ? 'status-error' : 'status-success'),
    ].join('');

    // TTS detail table
    els.ttsDetailTable.innerHTML = [
      detailRow('Characters spoken', Number(tts.total_characters || 0).toLocaleString()),
      detailRow('Avg clip length', `${Number(tts.avg_duration_sec || 0).toFixed(1)}s`),
      detailRow('Audio generated', `${Number(tts.total_duration_sec || 0).toFixed(1)}s`),
      detailRow('Success rate', `${tts.success_count || 0}/${tts.total_calls || 0}`),
      detailRow('Errors', tts.error_count || 0, (tts.error_count || 0) > 0 ? 'status-error' : 'status-success'),
    ].join('');

    // Endpoint breakdown table
    if (breakdown.length > 0) {
      els.endpointSection.classList.remove('hidden');
      els.endpointTable.innerHTML = breakdown
        .map((row) =>
          `<tr><td>${esc(row.endpoint)}</td><td>${row.calls}</td><td>${fmtMs(row.p50_latency_ms)}</td><td>${fmtMs(row.p95_latency_ms)}</td>`
          + `<td>${fmtMs(row.avg_latency_ms)}</td><td>${Number(row.total_tokens).toLocaleString()}</td><td>$${Number(row.total_cost_usd).toFixed(6)}</td>`
          + `<td class="${(row.errors || 0) > 0 ? 'status-error' : ''}">${row.errors || 0}</td></tr>`
        )
        .join('');
    } else {
      els.endpointSection.classList.add('hidden');
    }

    // Deep Latency Inspector (timeline waterfall)
    if (cycles.length > 0) {
      els.cycleSection.classList.remove('hidden');
      els.cycleTracesContainer.innerHTML = renderCycleWaterfalls(cycles);
      els.cycleTracesContainer.querySelectorAll('.trace-span').forEach((span) => {
        span.addEventListener('click', () => {
          span.classList.toggle('expanded');
          const meta = span.nextElementSibling;
          if (meta && meta.classList.contains('trace-meta')) meta.classList.toggle('visible');
        });
      });
      els.cycleTracesContainer.querySelectorAll('.trace-cycle-header').forEach((header) => {
        header.addEventListener('click', () => {
          const body = header.nextElementSibling;
          if (body) body.classList.toggle('hidden');
        });
      });
    } else {
      els.cycleSection.classList.add('hidden');
    }

    // Recent calls table
    if (recent.length > 0) {
      els.recentSection.classList.remove('hidden');
      els.recentTable.innerHTML = recent
        .map((row) => {
          const statusClass = row.status === 'success' ? 'status-success' : 'status-error';
          return `<tr><td>${esc(fmtTime(row.timestamp))}</td><td>${esc(row.endpoint)}</td><td>${esc(row.model || '—')}</td><td>${row.total_tokens || 0}</td><td>${fmtMs(row.latency_ms)}</td><td>$${Number(row.cost_usd || 0).toFixed(6)}</td><td class="${statusClass}">${esc(row.status)}</td></tr>`;
        })
        .join('');
    } else {
      els.recentSection.classList.add('hidden');
    }
  } catch {
    els.analyticsEmpty.classList.remove('hidden');
    els.analyticsContent.classList.add('hidden');
  }
}

// ═══════════════════════════════════════════════════════════
// WATERFALL TRACE RENDERER
// ═══════════════════════════════════════════════════════════

function renderCycleWaterfalls(cycles) {
  const legend = `<div class="trace-legend">
    <div class="trace-legend-item"><span class="trace-legend-dot cat-network"></span>Network</div>
    <div class="trace-legend-item"><span class="trace-legend-dot cat-compute"></span>Compute</div>
    <div class="trace-legend-item"><span class="trace-legend-dot cat-io"></span>I/O</div>
    <div class="trace-legend-item"><span class="trace-legend-dot cat-overhead"></span>Overhead</div>
    <div class="trace-legend-item"><span class="trace-legend-dot cat-general"></span>General</div>
  </div>`;

  return legend + cycles.map((cycle) => {
    let tasks = (cycle.tasks || []).slice();
    if (tasks.length === 0) return '';
    const totalMs = Number(cycle.total_latency_ms || 0);

    // Timeline mode when the server recorded start offsets (v3.1+); older cycles fall back to relative bars.
    const timeline = tasks.some((t) => t.start_ms != null);
    let spanMs;
    if (timeline) {
      tasks.sort((a, b) => (a.start_ms == null) - (b.start_ms == null) || (a.start_ms || 0) - (b.start_ms || 0));
      spanMs = Math.max(totalMs, ...tasks.filter((t) => t.start_ms != null).map((t) => t.start_ms + Number(t.latency_ms || 0)), 1);
    } else {
      spanMs = Math.max(...tasks.map((t) => t.latency_ms), 1);
    }

    const spansHTML = tasks.map((t) => {
      const ms = Number(t.latency_ms || 0);
      const category = t.category || 'general';
      const depth = t.depth || 0;
      const meta = t.meta || {};
      const metaKeys = Object.keys(meta);
      const isOverhead = category === 'overhead' || (timeline && t.start_ms == null);

      let barStyle;
      let barClass = 'trace-span-bar';
      if (timeline && t.start_ms != null) {
        barClass += ' is-timeline';
        barStyle = `left:${(t.start_ms / spanMs * 100).toFixed(2)}%;width:${Math.max(0.6, ms / spanMs * 100).toFixed(2)}%`;
      } else {
        barStyle = `width:${Math.max(1, ms / spanMs * 100).toFixed(1)}%`;
      }

      let metaHTML = '';
      if (metaKeys.length > 0) {
        metaHTML = `<div class="trace-meta"><div class="trace-meta-grid">${metaKeys.map((k) => `<div class="trace-meta-item"><strong>${esc(k)}:</strong> ${esc(String(meta[k]))}</div>`).join('')}</div></div>`;
      }

      return `<div class="trace-span ${isOverhead ? 'is-overhead' : ''}" data-category="${esc(category)}">
        <div class="trace-span-name" style="padding-left:${depth * 18}px" title="${esc(t.name)}"><span class="cat-dot cat-${esc(category)}"></span>${esc(t.name)}</div>
        <div class="trace-span-bar-wrap"><div class="${barClass}" style="${barStyle}"></div></div>
        <div class="trace-span-ms">${ms < 1 ? ms.toFixed(2) : ms < 10 ? ms.toFixed(1) : ms.toFixed(0)}ms</div>
      </div>${metaHTML}`;
    }).join('');

    const axis = timeline
      ? `<div class="wf-axis"><span>0</span><span>${fmtMs(spanMs / 2)}</span><span>${fmtMs(spanMs)}</span></div>`
      : '';

    return `<div class="trace-cycle">
      <div class="trace-cycle-header">
        <div class="trace-cycle-title">
          ${esc(cycle.cycle_type)}
          <span class="trace-cycle-time">${esc(fmtTime(cycle.timestamp))}</span>
          ${cycle.source === 'client' ? '<span class="pill pill--ghost trace-cycle-source">client-timed</span>' : ''}
        </div>
        <div class="trace-cycle-total">${totalMs < 1000 ? totalMs.toFixed(0) + 'ms' : (totalMs / 1000).toFixed(2) + 's'}</div>
      </div>
      <div class="trace-cycle-body">${axis}${spansHTML}</div>
    </div>`;
  }).join('');
}

// ═══════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════

function metricCardHTML(value, label) {
  return `<div class="metric-card"><div class="m-value">${value}</div><div class="m-label">${esc(label)}</div></div>`;
}

function detailRow(label, value, cls) {
  return `<tr><td>${esc(label)}</td><td${cls ? ` class="${cls}"` : ''}>${value}</td></tr>`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).length > 19 ? String(iso).slice(11, 19) : String(iso);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

async function safeJson(res) {
  try { return await res.json(); } catch { return null; }
}

function getSafeErrorMsg(d, fallback) {
  if (!d || !d.detail) return fallback;
  if (typeof d.detail === 'string') return d.detail;
  if (Array.isArray(d.detail)) return d.detail.map((e) => e.msg || JSON.stringify(e)).join(', ');
  if (typeof d.detail === 'object') return d.detail.message || JSON.stringify(d.detail);
  return String(d.detail);
}

function mimeToExt(mime) {
  if (mime.includes('webm')) return 'webm';
  if (mime.includes('ogg')) return 'ogg';
  if (mime.includes('mp4')) return 'mp4';
  return 'webm';
}

// ── Resize Handler ────────────────────────────────────────
let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (!isRecording) drawIdleWaveform(); }, 150);
});

// ── Launch ────────────────────────────────────────────────
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

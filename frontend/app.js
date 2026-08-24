/* ═══════════════════════════════════════════════════════════════
   Cadence Coach — Frontend Application Logic
   Three-tab app: Speech Director, Communication Coach, Analytics
   Vanilla JS — zero dependencies
   ═══════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  // ── DOM Helpers ───────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // ── Cached DOM References ────────────────────────────────
  const els = {
    // Header
    badge:         $('#connection-badge'),
    badgeLabel:    $('#connection-badge .badge-label'),

    // Tabs
    tabBtns:       $$('.tab-btn'),
    tabPanels:     $$('.tab-panel'),

    // Tab 1 — Director
    directorText:       $('#director-text'),
    directorStyle:      $('#director-style'),
    directorVoice:      $('#director-voice'),
    btnRewrite:         $('#btn-rewrite'),
    btnSpeak:           $('#btn-speak'),
    directorPlaceholder:$('#director-placeholder'),
    directorResults:    $('#director-results'),
    directorLoading:    $('#director-loading'),
    directorLoadingText:$('#director-loading-text'),
    directorDisplayScript: $('#director-display-script'),
    directorTtsScript:  $('#director-tts-script'),
    directorTtsExpander:$('#director-tts-expander'),
    directorAudioCard:  $('#director-audio-card'),
    directorAudio:      $('#director-audio'),
    directorAudioMeta:  $('#director-audio-meta'),

    // Tab 2 — Coach
    toggleText:     $('#toggle-text'),
    toggleAudio:    $('#toggle-audio'),
    coachTextMode:  $('#coach-text-mode'),
    coachAudioMode: $('#coach-audio-mode'),
    coachText:      $('#coach-text'),
    coachContext:   $('#coach-context'),
    btnAnalyze:     $('#btn-analyze'),
    btnTranscribe:  $('#btn-transcribe'),
    canvas:         $('#waveform-canvas'),
    timer:          $('#recording-timer'),
    recordBtn:      $('#record-btn'),
    recordLabel:    $('#record-label'),
    micIcon:        $('#mic-icon'),
    stopIcon:       $('#stop-icon'),
    uploadZone:     $('#upload-zone'),
    audioFileInput: $('#audio-file-input'),
    uploadBrowseBtn:$('#upload-browse-btn'),
    uploadFileInfo: $('#upload-file-info'),
    uploadFilename: $('#upload-filename'),
    uploadRemoveBtn:$('#upload-remove-btn'),

    // Coach — transcription metrics
    coachTranscriptionMetrics: $('#coach-transcription-metrics'),
    speakingMetricsRow: $('#speaking-metrics-row'),
    wpmBadgeWrap:   $('#wpm-badge-wrap'),
    fillerBreakdown:$('#filler-breakdown'),
    transcribedTextBody: $('#transcribed-text-body'),

    // Coach — results
    coachPlaceholder: $('#coach-placeholder'),
    coachLoading:    $('#coach-loading'),
    coachLoadingText:$('#coach-loading-text'),
    coachResults:    $('#coach-results'),
    scoreRingFill:   $('#score-ring-fill'),
    scoreValue:      $('#score-value'),
    scoreSummary:    $('#score-summary'),
    categoryScoresRow: $('#category-scores-row'),
    actionsList:     $('#actions-list'),
    coachingDetails: $('#coaching-details'),

    // Tab 3 — Analytics
    analyticsEmpty:  $('#analytics-empty'),
    analyticsContent:$('#analytics-content'),
    kpiRow:          $('#kpi-row'),
    llmDetailTable:  $('#llm-detail-table tbody'),
    ttsDetailTable:  $('#tts-detail-table tbody'),
    endpointTable:   $('#endpoint-table tbody'),
    cycleSection:    $('#cycle-section'),
    cycleTracesContainer: $('#cycle-traces-container'),
    recentTable:     $('#recent-table tbody'),
    btnRefreshMetrics: $('#btn-refresh-metrics'),
    endpointSection: $('#endpoint-section'),
    recentSection:   $('#recent-section'),

    // Toast
    errorToast:     $('#error-toast'),
    errorMessage:   $('#error-message'),
    toastCloseBtn:  $('#toast-close-btn'),
  };

  // ── State ─────────────────────────────────────────────────
  const CIRCUMFERENCE = 2 * Math.PI * 52;
  let mediaRecorder = null;
  let audioStream = null;
  let audioChunks = [];
  let isRecording = false;
  let timerInterval = null;
  let recordStartTime = 0;
  let audioCtx = null;
  let analyser = null;
  let animFrameId = null;
  let uploadedFile = null;     // File object from upload
  let transcribedText = '';    // Text from transcription (used by Analyze)
  let transcriptionData = null; // Full transcription response

  // ═══════════════════════════════════════════════════════════
  // INITIALIZATION
  // ═══════════════════════════════════════════════════════════

  function init() {
    checkAPIHealth();
    loadVoices();
    setupTabNavigation();
    setupDirectorEvents();
    setupCoachEvents();
    setupAnalyticsEvents();
    setupToastEvents();
    drawIdleWaveform();
  }

  // ── Health Check ──────────────────────────────────────────
  async function checkAPIHealth() {
    try {
      const res = await fetch('/api/health');
      if (res.ok) {
        els.badge.className = 'badge badge--online';
        els.badgeLabel.textContent = 'Online';
      } else { throw 0; }
    } catch {
      els.badge.className = 'badge badge--offline';
      els.badgeLabel.textContent = 'Offline';
    }
  }

  // ═══════════════════════════════════════════════════════════
  // TAB NAVIGATION
  // ═══════════════════════════════════════════════════════════

  function setupTabNavigation() {
    els.tabBtns.forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
  }

  function switchTab(tabId) {
    els.tabBtns.forEach((btn) => {
      const isActive = btn.dataset.tab === tabId;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-selected', isActive);
    });

    els.tabPanels.forEach((panel) => {
      panel.classList.toggle('active', panel.id === `tab-${tabId}`);
    });

    // Load analytics data when switching to that tab
    if (tabId === 'analytics') loadAnalytics();
  }

  // ═══════════════════════════════════════════════════════════
  // TAB 1 — SPEECH DIRECTOR
  // ═══════════════════════════════════════════════════════════

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
    els.btnRewrite.addEventListener('click', handleRewriteOnly);
    els.btnSpeak.addEventListener('click', handleRewriteAndSpeak);
  }

  async function handleRewriteOnly() {
    const text = els.directorText.value.trim();
    const style = els.directorStyle.value.trim();
    if (!text || !style) return showToast('Please enter both text and a target style.');

    showDirectorState('loading');
    els.directorLoadingText.textContent = 'Rewriting your text…';
    setDirectorBtns(true);
    const startRw = performance.now();

    try {
      const res = await fetch('/api/rewrite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_text: text, target_style: style }),
      });

      if (!res.ok) {
        const d = await safeJson(res);
        throw new Error(d?.detail || `Rewrite failed (${res.status})`);
      }

      const data = await res.json();
      const latencyMs = performance.now() - startRw;
      const tasks = (data.detailed_latencies && data.detailed_latencies.length > 0) 
          ? data.detailed_latencies 
          : [{name: 'LLM Rewrite Request', latency_ms: latencyMs}];
      logCycleMetrics('Rewrite Only', latencyMs, tasks);
      renderDirectorResult(data.display_script, data.tts_script, null);
    } catch (err) {
      showDirectorState('placeholder');
      showToast(err.message);
    } finally {
      setDirectorBtns(false);
    }
  }

  async function handleRewriteAndSpeak() {
    const text = els.directorText.value.trim();
    const style = els.directorStyle.value.trim();
    const voice = els.directorVoice.value;
    if (!text || !style) return showToast('Please enter both text and a target style.');

    showDirectorState('loading');
    els.directorLoadingText.textContent = 'Rewriting and synthesizing audio…';
    setDirectorBtns(true);
    const startCycle = performance.now();

    try {
      const res = await fetch('/api/rewrite-and-speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_text: text, target_style: style, voice }),
      });

      if (!res.ok) {
        const d = await safeJson(res);
        throw new Error(d?.detail || `Rewrite & Speak failed (${res.status})`);
      }

      const data = await res.json();
      const audioInfo = data.audio || {};

      let audioUrl = null;
      let audioMeta = '';

      if (audioInfo.success && audioInfo.file_path) {
        // Build URL from file path: extract filename from the path
        const filename = audioInfo.filename || audioInfo.file_path.split('/').pop().split('\\').pop();
        audioUrl = `/audio/${filename}`;
        if (audioInfo.duration) {
          audioMeta = `Duration: ${Number(audioInfo.duration).toFixed(1)}s  ·  Voice: ${audioInfo.voice || voice}`;
        }
      }

      if (data.detailed_latencies && data.detailed_latencies.length > 0) {
        logCycleMetrics('Rewrite & Speak', performance.now() - startCycle, data.detailed_latencies);
      } else if (data.latencies) {
        logCycleMetrics('Rewrite & Speak', performance.now() - startCycle, data.latencies);
      } else {
        const total = performance.now() - startCycle;
        logCycleMetrics('Rewrite & Speak', total, [{name: 'Rewrite & Speak API', latency_ms: total}]);
      }

      renderDirectorResult(data.display_script, data.tts_script, audioUrl, audioMeta);
    } catch (err) {
      showDirectorState('placeholder');
      showToast(err.message);
    } finally {
      setDirectorBtns(false);
    }
  }

  function renderDirectorResult(displayScript, ttsScript, audioUrl, audioMeta) {
    els.directorDisplayScript.textContent = displayScript || '';
    els.directorTtsScript.textContent = ttsScript || '';
    els.directorTtsExpander.removeAttribute('open');

    if (audioUrl) {
      els.directorAudio.src = audioUrl;
      els.directorAudioMeta.textContent = audioMeta || '';
      els.directorAudioCard.classList.remove('hidden');
    } else {
      els.directorAudioCard.classList.add('hidden');
    }

    showDirectorState('results');
  }

  function showDirectorState(state) {
    els.directorPlaceholder.classList.toggle('hidden', state !== 'placeholder');
    els.directorResults.classList.toggle('hidden', state !== 'results');
    els.directorLoading.classList.toggle('hidden', state !== 'loading');
  }

  function setDirectorBtns(disabled) {
    els.btnRewrite.disabled = disabled;
    els.btnSpeak.disabled = disabled;
  }

  // ═══════════════════════════════════════════════════════════
  // TAB 2 — COMMUNICATION COACH
  // ═══════════════════════════════════════════════════════════

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
    if (file && file.type.startsWith('audio/')) setUploadedFile(file);
    else showToast('Please drop an audio file.');
  }

  function setUploadedFile(file) {
    uploadedFile = file;
    els.uploadFilename.textContent = `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
    els.uploadFileInfo.classList.remove('hidden');
    els.uploadZone.classList.add('hidden');
    els.btnTranscribe.classList.remove('hidden');
  }

  function clearUploadedFile() {
    uploadedFile = null;
    els.audioFileInput.value = '';
    els.uploadFileInfo.classList.add('hidden');
    els.uploadZone.classList.remove('hidden');
    els.btnTranscribe.classList.add('hidden');
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
      showToast('Microphone access denied. Please allow microphone permissions.');
      return;
    }

    audioChunks = [];
    const mimeType = getSupportedMimeType();
    const opts = mimeType ? { mimeType } : {};
    mediaRecorder = new MediaRecorder(audioStream, opts);

    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = () => {
      const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
      setUploadedFile(new File([blob], `recording.${mimeToExt(blob.type)}`, { type: blob.type }));
    };
    mediaRecorder.onerror = () => { showToast('Recording error.'); resetRecordingUI(); };

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
    }, 250);
  }

  function stopTimer() { clearInterval(timerInterval); timerInterval = null; }

  // ── Waveform ──────────────────────────────────────────────
  function drawIdleWaveform() {
    const canvas = els.canvas;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    resizeCanvas(canvas);
    const w = canvas.width, h = canvas.height, cx = h / 2;
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
    resizeCanvas(canvas);

    function draw() {
      animFrameId = requestAnimationFrame(draw);
      analyser.getByteFrequencyData(dataArray);
      const w = canvas.width, h = canvas.height, cx = h / 2;
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
    if (audioCtx) { audioCtx.close().catch(() => {}); audioCtx = null; analyser = null; }
    setTimeout(drawIdleWaveform, 50);
  }

  function resizeCanvas(canvas) {
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.getContext('2d').scale(dpr, dpr);
  }

  // ── Transcribe & Analyze ──────────────────────────────────
  async function handleTranscribeAndAnalyze() {
    if (!uploadedFile) return showToast('Please record or upload an audio file first.');

    showCoachState('loading');
    els.coachLoadingText.textContent = 'Transcribing your audio with Whisper…';
    els.btnTranscribe.disabled = true;
    const startTr = performance.now();
    const cycleTasks = [];

    try {
      // Step 1: Transcribe
      const formData = new FormData();
      formData.append('file', uploadedFile, uploadedFile.name);

      const trRes = await fetch('/api/transcribe', { method: 'POST', body: formData });
      if (!trRes.ok) {
        const d = await safeJson(trRes);
        throw new Error(d?.detail || `Transcription failed (${trRes.status})`);
      }

      transcriptionData = await trRes.json();
      transcribedText = transcriptionData.text || '';
      const trLatency = performance.now() - startTr;
      
      if (transcriptionData.detailed_latencies && transcriptionData.detailed_latencies.length > 0) {
        cycleTasks.push(...transcriptionData.detailed_latencies);
      } else {
        cycleTasks.push({name: 'Whisper Transcribe Request', latency_ms: trLatency});
      }

      // Show transcription metrics
      renderTranscriptionMetrics(transcriptionData);

      // Auto-populate the coach text for the analyze button
      if (transcribedText) {
        // Step 2: Auto-analyze
        els.coachLoadingText.textContent = 'Analyzing your communication…';
        const startCoach = performance.now();
        const coachData = await runCoachAnalysis(transcribedText);
        
        if (coachData && coachData.detailed_latencies && coachData.detailed_latencies.length > 0) {
            cycleTasks.push(...coachData.detailed_latencies);
        } else {
            cycleTasks.push({name: 'LLM Analyze Request', latency_ms: performance.now() - startCoach});
        }
        
        logCycleMetrics('Transcribe & Analyze', performance.now() - startTr, cycleTasks);
      } else {
        showCoachState('placeholder');
        showToast('No speech detected in the audio.');
        logCycleMetrics('Transcribe Only', trLatency, cycleTasks);
      }
    } catch (err) {
      showCoachState('placeholder');
      showToast(err.message);
    } finally {
      els.btnTranscribe.disabled = false;
    }
  }

  async function handleAnalyze() {
    // Get text from either text mode or transcribed text
    const text = els.coachTextMode.classList.contains('hidden')
      ? transcribedText
      : els.coachText.value.trim();

    if (!text) return showToast('Please enter some text or upload an audio file to analyze.');

    showCoachState('loading');
    els.coachLoadingText.textContent = 'Analyzing your communication…';
    els.btnAnalyze.disabled = true;
    const startCoach = performance.now();

    try {
      const data = await runCoachAnalysis(text);
      const latencyMs = performance.now() - startCoach;
      const tasks = (data && data.detailed_latencies && data.detailed_latencies.length > 0) 
          ? data.detailed_latencies 
          : [{name: 'LLM Analyze Request', latency_ms: latencyMs}];
      logCycleMetrics('Analyze Only', latencyMs, tasks);
    } catch (err) {
      showCoachState('placeholder');
      showToast(err.message);
    } finally {
      els.btnAnalyze.disabled = false;
    }
  }

  async function runCoachAnalysis(text) {
    const payload = { user_text: text };
    const context = els.coachContext.value.trim();
    if (context) payload.context = context;

    const res = await fetch('/api/coach', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const d = await safeJson(res);
      throw new Error(d?.detail || `Coaching failed (${res.status})`);
    }

    const data = await res.json();
    renderCoachResults(data);
    showCoachState('results');
    return data;
  }

  // ── Render Transcription Metrics ──────────────────────────
  function renderTranscriptionMetrics(data) {
    els.coachTranscriptionMetrics.classList.remove('hidden');

    // Metric cards
    const cards = [];
    if (data.wpm != null) cards.push(metricCardHTML(Math.round(data.wpm), 'Words/Min'));
    if (data.duration_sec != null) cards.push(metricCardHTML(`${Number(data.duration_sec).toFixed(0)}s`, 'Duration'));
    if (data.word_count != null) cards.push(metricCardHTML(data.word_count, 'Words'));
    if (data.filler_count != null) cards.push(metricCardHTML(data.filler_count, 'Fillers'));

    els.speakingMetricsRow.innerHTML = cards.join('');

    // WPM badge
    const wpmRating = data.wpm_rating || '';
    const wpmTips = {
      too_slow: 'Your pace is quite slow. Try to speed up slightly to maintain audience engagement.',
      slow: 'Slightly below average. Good for emphasis, but watch for losing momentum.',
      optimal: 'Excellent pacing! This is the sweet spot for engaging delivery.',
      fast: 'A bit fast. Try slowing down at key moments for impact.',
      too_fast: 'Too fast — your audience may struggle to absorb the message. Add deliberate pauses.',
    };
    if (wpmRating) {
      els.wpmBadgeWrap.innerHTML =
        `<span class="wpm-badge wpm-${esc(wpmRating)}">`
        + `Pace: ${esc(wpmRating.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()))}`
        + `</span>`
        + (wpmTips[wpmRating] ? `<p class="wpm-tip">${esc(wpmTips[wpmRating])}</p>` : '');
    }

    // Filler breakdown
    const fillerWords = data.filler_words || {};
    const fillerRating = data.filler_rating || '';
    const fillerRate = data.filler_rate_pct || 0;
    let fillerHTML = '';

    if (fillerRating) {
      fillerHTML += `<span class="filler-badge filler-${esc(fillerRating)}">Filler Rate: ${Number(fillerRate).toFixed(1)}% (${esc(fillerRating.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()))})</span>`;
    }

    const fillerEntries = Object.entries(fillerWords).sort((a, b) => b[1] - a[1]);
    if (fillerEntries.length > 0) {
      fillerHTML += '<div class="filler-grid">';
      fillerEntries.slice(0, 4).forEach(([word, count]) => {
        fillerHTML += `<div class="filler-chip"><div class="filler-count">${count}</div><div class="filler-word">"${esc(word)}"</div></div>`;
      });
      fillerHTML += '</div>';
    }

    els.fillerBreakdown.innerHTML = fillerHTML;

    // Transcribed text
    els.transcribedTextBody.textContent = data.text || '';
  }

  // ── Render Coach Results ──────────────────────────────────
  function renderCoachResults(data) {
    // Overall score
    animateScoreRing(data.overall_score);
    els.scoreSummary.textContent = data.summary || '';

    // Category score metric cards
    const categories = [
      ['📖 Vocabulary', data.vocabulary?.score],
      ['🎭 Tone & Emotion', data.tone_and_emotion?.score],
      ['🎙️ Voice Modulation', data.voice_modulation?.score],
      ['⏱️ Pacing & Rhythm', data.pacing_and_rhythm?.score],
      ['🎯 Emphasis', data.emphasis_and_delivery?.score],
      ['🏗️ Clarity', data.clarity_and_structure?.score],
    ];

    els.categoryScoresRow.innerHTML = categories
      .map(([name, score]) => metricCardHTML(`${score ?? '—'}/10`, name))
      .join('');

    // Top 3 actions
    els.actionsList.innerHTML = (data.top_3_actions || [])
      .map((a) => `<li>${esc(a)}</li>`)
      .join('');

    // Detailed coaching sections as expanders
    let detailHTML = '';

    // Vocabulary
    if (data.vocabulary) {
      detailHTML += buildCoachExpander('📖 Vocabulary Analysis', () => {
        let html = '';
        html += listSection('Strengths', data.vocabulary.strengths);
        html += listSection('Improvements', data.vocabulary.improvements);
        if (data.vocabulary.weak_words?.length) {
          html += '<p class="coaching-label">Weak Words</p><div>';
          data.vocabulary.weak_words.forEach((w) => {
            html += `<span class="weak-word-tag" title="${esc(w.reason)}">${esc(w.word)} <span class="arrow">→</span> <span class="suggestion">${esc(w.suggestion)}</span></span>`;
          });
          html += '</div>';
        }
        if (data.vocabulary.power_words_used?.length) {
          html += '<p class="coaching-label">Power Words</p><div>';
          data.vocabulary.power_words_used.forEach((w) => { html += `<span class="power-word-tag">${esc(w)}</span>`; });
          html += '</div>';
        }
        return html;
      });
    }

    // Tone & Emotion
    if (data.tone_and_emotion) {
      detailHTML += buildCoachExpander('🎭 Tone & Emotion', () => {
        let html = '';
        if (data.tone_and_emotion.detected_tone) html += `<p class="coaching-label">Detected Tone</p><p style="font-size:0.88rem;color:var(--text-secondary);margin-bottom:6px;">${esc(data.tone_and_emotion.detected_tone)}</p>`;
        if (data.tone_and_emotion.emotional_arc) html += `<p class="coaching-label">Emotional Arc</p><p style="font-size:0.88rem;color:var(--text-secondary);margin-bottom:6px;">${esc(data.tone_and_emotion.emotional_arc)}</p>`;
        html += listSection('Feedback', data.tone_and_emotion.feedback);
        return html;
      });
    }

    // Voice Modulation
    if (data.voice_modulation) {
      detailHTML += buildCoachExpander('🎙️ Voice Modulation', () => {
        return (data.voice_modulation.suggestions || [])
          .map((s) => `<div class="modulation-item"><p class="modulation-segment">"${esc(s.text_segment)}"</p><p class="modulation-instruction">${esc(s.instruction)}</p><span class="modulation-type">${esc(s.type)}</span></div>`)
          .join('');
      });
    }

    // Pacing & Rhythm
    if (data.pacing_and_rhythm) {
      detailHTML += buildCoachExpander('⏱️ Pacing & Rhythm', () => {
        let html = '';
        if (data.pacing_and_rhythm.avg_sentence_length) html += `<p class="coaching-label">Avg Sentence Length</p><p style="font-size:0.88rem;color:var(--text-secondary);margin-bottom:6px;">${esc(data.pacing_and_rhythm.avg_sentence_length)}</p>`;
        if (data.pacing_and_rhythm.variation_quality) html += `<p class="coaching-label">Variation Quality</p><p style="font-size:0.88rem;color:var(--text-secondary);margin-bottom:6px;">${esc(data.pacing_and_rhythm.variation_quality)}</p>`;
        html += listSection('Feedback', data.pacing_and_rhythm.feedback);
        return html;
      });
    }

    // Emphasis & Delivery
    if (data.emphasis_and_delivery) {
      detailHTML += buildCoachExpander('🎯 Emphasis & Delivery', () => {
        return (data.emphasis_and_delivery.key_moments || [])
          .map((m) => `<div class="modulation-item"><p class="modulation-segment">"${esc(m.text_segment)}"</p><p class="modulation-instruction"><strong>${esc(m.technique)}</strong> — ${esc(m.reason)}</p></div>`)
          .join('');
      });
    }

    // Clarity & Structure
    if (data.clarity_and_structure) {
      detailHTML += buildCoachExpander('🏗️ Clarity & Structure', () => {
        let html = '';
        if (data.clarity_and_structure.opening_strength) html += `<p class="coaching-label">Opening Strength</p><p style="font-size:0.88rem;color:var(--text-secondary);margin-bottom:6px;">${esc(data.clarity_and_structure.opening_strength)}</p>`;
        if (data.clarity_and_structure.closing_strength) html += `<p class="coaching-label">Closing Strength</p><p style="font-size:0.88rem;color:var(--text-secondary);margin-bottom:6px;">${esc(data.clarity_and_structure.closing_strength)}</p>`;
        if (data.clarity_and_structure.transitions) html += `<p class="coaching-label">Transitions</p><p style="font-size:0.88rem;color:var(--text-secondary);margin-bottom:6px;">${esc(data.clarity_and_structure.transitions)}</p>`;
        html += listSection('Feedback', data.clarity_and_structure.feedback);
        return html;
      });
    }

    els.coachingDetails.innerHTML = detailHTML;
  }

  function buildCoachExpander(title, contentFn) {
    return `<details class="expander"><summary class="expander-summary">${title}</summary><div class="expander-body">${contentFn()}</div></details>`;
  }

  function listSection(label, items) {
    if (!items || items.length === 0) return '';
    return `<p class="coaching-label">${esc(label)}</p><ul class="coaching-list">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>`;
  }

  function showCoachState(state) {
    els.coachPlaceholder.classList.toggle('hidden', state !== 'placeholder');
    els.coachLoading.classList.toggle('hidden', state !== 'loading');
    els.coachResults.classList.toggle('hidden', state !== 'results');
  }

  // ── Score Ring Animation ──────────────────────────────────
  function animateScoreRing(score) {
    // Coach scores are 0–10, convert for display
    const displayScore = Number(score) || 0;
    const pct = (displayScore / 10) * 100;
    const offset = CIRCUMFERENCE - (pct / 100) * CIRCUMFERENCE;

    injectScoreGradient(pct);

    els.scoreRingFill.style.strokeDashoffset = CIRCUMFERENCE;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        els.scoreRingFill.style.strokeDashoffset = offset;
      });
    });

    animateNumber(els.scoreValue, 0, displayScore, 1000, 1);
  }

  function injectScoreGradient(pct) {
    let c1, c2;
    if (pct >= 70) { c1 = 'hsl(155,72%,55%)'; c2 = 'hsl(165,70%,60%)'; }
    else if (pct >= 40) { c1 = 'hsl(38,92%,55%)'; c2 = 'hsl(45,90%,60%)'; }
    else { c1 = 'hsl(0,72%,55%)'; c2 = 'hsl(10,72%,60%)'; }

    const svg = els.scoreRingFill.closest('svg');
    const existing = svg.querySelector('defs');
    if (existing) existing.remove();

    const ns = 'http://www.w3.org/2000/svg';
    const defs = document.createElementNS(ns, 'defs');
    const grad = document.createElementNS(ns, 'linearGradient');
    grad.id = 'scoreGrad';
    grad.setAttribute('x1', '0'); grad.setAttribute('y1', '0');
    grad.setAttribute('x2', '1'); grad.setAttribute('y2', '1');

    const s1 = document.createElementNS(ns, 'stop');
    s1.setAttribute('offset', '0%'); s1.setAttribute('stop-color', c1);
    const s2 = document.createElementNS(ns, 'stop');
    s2.setAttribute('offset', '100%'); s2.setAttribute('stop-color', c2);

    grad.appendChild(s1); grad.appendChild(s2);
    defs.appendChild(grad); svg.prepend(defs);
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
  // TAB 3 — ANALYTICS DASHBOARD
  // ═══════════════════════════════════════════════════════════

  function setupAnalyticsEvents() {
    els.btnRefreshMetrics.addEventListener('click', loadAnalytics);
  }

  async function loadAnalytics() {
    try {
      const res = await fetch('/api/metrics');
      if (!res.ok) throw 0;
      const metrics = await res.json();

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
        metricCardHTML(llm.total_calls || 0, 'LLM Calls'),
        metricCardHTML(`${Number(llm.avg_latency_ms || 0).toFixed(0)}ms`, 'Avg Latency'),
        metricCardHTML(Number(llm.total_tokens || 0).toLocaleString(), 'Total Tokens'),
        metricCardHTML(`$${Number(llm.total_cost_usd || 0).toFixed(4)}`, 'Total Cost'),
        metricCardHTML(tts.total_calls || 0, 'TTS Calls'),
        metricCardHTML(`${Number(tts.total_duration_sec || 0).toFixed(0)}s`, 'Audio Generated'),
      ].join('');

      // LLM detail table
      els.llmDetailTable.innerHTML = [
        detailRow('Total Prompt Tokens', Number(llm.total_prompt_tokens || 0).toLocaleString()),
        detailRow('Total Completion Tokens', Number(llm.total_completion_tokens || 0).toLocaleString()),
        detailRow('Min Latency', `${Number(llm.min_latency_ms || 0).toFixed(0)}ms`),
        detailRow('Max Latency', `${Number(llm.max_latency_ms || 0).toFixed(0)}ms`),
        detailRow('Success Rate', `${llm.success_count || 0}/${llm.total_calls || 0}`),
        detailRow('Errors', llm.error_count || 0, (llm.error_count || 0) > 0 ? 'color:var(--color-danger)' : 'color:var(--color-success)'),
      ].join('');

      // TTS detail table
      els.ttsDetailTable.innerHTML = [
        detailRow('Total Characters', Number(tts.total_characters || 0).toLocaleString()),
        detailRow('Avg Audio Duration', `${Number(tts.avg_duration_sec || 0).toFixed(1)}s`),
        detailRow('Total Audio Generated', `${Number(tts.total_duration_sec || 0).toFixed(1)}s`),
        detailRow('Success Rate', `${tts.success_count || 0}/${tts.total_calls || 0}`),
        detailRow('Errors', tts.error_count || 0, (tts.error_count || 0) > 0 ? 'color:var(--color-danger)' : 'color:var(--color-success)'),
      ].join('');

      // Endpoint breakdown table
      if (breakdown.length > 0) {
        els.endpointSection.classList.remove('hidden');
        els.endpointTable.innerHTML = breakdown
          .map((row) =>
            `<tr><td>${esc(row.endpoint)}</td><td>${row.calls}</td><td>${Number(row.avg_latency_ms).toFixed(1)}ms</td><td>${Number(row.total_tokens).toLocaleString()}</td><td>$${Number(row.total_cost_usd).toFixed(6)}</td></tr>`
          )
          .join('');
      } else {
        els.endpointSection.classList.add('hidden');
      }

      // Deep Latency Inspector (waterfall)
      if (cycles.length > 0) {
        els.cycleSection.classList.remove('hidden');
        els.cycleTracesContainer.innerHTML = renderCycleWaterfalls(cycles);
        // Attach click handlers for metadata expansion
        els.cycleTracesContainer.querySelectorAll('.trace-span').forEach(span => {
          span.addEventListener('click', () => {
            span.classList.toggle('expanded');
            const meta = span.nextElementSibling;
            if (meta && meta.classList.contains('trace-meta')) {
              meta.classList.toggle('visible');
            }
          });
        });
        // Collapse/expand cycle bodies
        els.cycleTracesContainer.querySelectorAll('.trace-cycle-header').forEach(header => {
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
            const time = (row.timestamp || '').length > 19 ? row.timestamp.slice(11, 19) : row.timestamp;
            const statusClass = row.status === 'success' ? 'status-success' : 'status-error';
            return `<tr><td>${esc(time)}</td><td>${esc(row.endpoint)}</td><td>${esc(row.model || '—')}</td><td>${row.total_tokens || 0}</td><td>${Number(row.latency_ms || 0).toFixed(0)}ms</td><td>$${Number(row.cost_usd || 0).toFixed(6)}</td><td class="${statusClass}">${esc(row.status)}</td></tr>`;
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
    const categoryIcons = {
      network: '🌐', compute: '⚡', io: '💾', overhead: '⚙️', general: '📌'
    };

    const legend = `<div class="trace-legend">
      <div class="trace-legend-item"><span class="trace-legend-dot cat-network"></span>Network</div>
      <div class="trace-legend-item"><span class="trace-legend-dot cat-compute"></span>Compute</div>
      <div class="trace-legend-item"><span class="trace-legend-dot cat-io"></span>I/O</div>
      <div class="trace-legend-item"><span class="trace-legend-dot cat-overhead"></span>Overhead</div>
      <div class="trace-legend-item"><span class="trace-legend-dot cat-general"></span>General</div>
    </div>`;

    return legend + cycles.map(cycle => {
      const tasks = cycle.tasks || [];
      if (tasks.length === 0) return '';
      const maxMs = Math.max(...tasks.map(t => t.latency_ms), 1);
      const totalMs = Number(cycle.total_latency_ms || 0);
      const time = (cycle.timestamp || '').length > 19 ? cycle.timestamp.slice(11, 19) : cycle.timestamp;

      const spansHTML = tasks.map(t => {
        const ms = Number(t.latency_ms || 0);
        const pct = Math.max(1, (ms / maxMs) * 100);
        const category = t.category || 'general';
        const icon = categoryIcons[category] || '📌';
        const depth = t.depth || 0;
        const indent = depth * 18;
        const meta = t.meta || {};
        const metaKeys = Object.keys(meta);

        let metaHTML = '';
        if (metaKeys.length > 0) {
          metaHTML = `<div class="trace-meta"><div class="trace-meta-grid">${
            metaKeys.map(k => `<div class="trace-meta-item"><strong>${esc(k)}:</strong> ${esc(String(meta[k]))}</div>`).join('')
          }</div></div>`;
        }

        return `<div class="trace-span" data-category="${esc(category)}">
          <div class="trace-span-name" style="padding-left:${indent}px" title="${esc(t.name)}">${icon} ${esc(t.name)}</div>
          <div class="trace-span-bar-wrap"><div class="trace-span-bar" style="width:${pct.toFixed(1)}%"></div></div>
          <div class="trace-span-ms">${ms < 1 ? ms.toFixed(2) : ms < 10 ? ms.toFixed(1) : ms.toFixed(0)}ms</div>
        </div>${metaHTML}`;
      }).join('');

      return `<div class="trace-cycle">
        <div class="trace-cycle-header">
          <div class="trace-cycle-title">
            ${esc(cycle.cycle_type)}
            <span class="trace-cycle-time">${esc(time)}</span>
          </div>
          <div class="trace-cycle-total">${totalMs < 1000 ? totalMs.toFixed(0) + 'ms' : (totalMs / 1000).toFixed(2) + 's'}</div>
        </div>
        <div class="trace-cycle-body">${spansHTML}</div>
      </div>`;
    }).join('');
  }

  // ═══════════════════════════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════════════════════════

  function metricCardHTML(value, label) {
    return `<div class="metric-card"><div class="m-value">${value}</div><div class="m-label">${esc(label)}</div></div>`;
  }

  function detailRow(label, value, style) {
    return `<tr><td>${esc(label)}</td><td${style ? ` style="${style}"` : ''}>${value}</td></tr>`;
  }

  function esc(str) {
    if (str == null) return '';
    const d = document.createElement('div');
    d.textContent = String(str);
    return d.innerHTML;
  }

  async function safeJson(res) {
    try { return await res.json(); } catch { return null; }
  }

  function logCycleMetrics(cycleType, totalLatencyMs, tasks) {
    fetch('/api/metrics/cycle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        cycle_type: cycleType,
        total_latency_ms: totalLatencyMs,
        tasks: tasks
      })
    }).catch(err => console.error('Failed to log cycle metrics:', err));
  }

  function mimeToExt(mime) {
    if (mime.includes('webm')) return 'webm';
    if (mime.includes('ogg'))  return 'ogg';
    if (mime.includes('mp4'))  return 'mp4';
    return 'webm';
  }

  // ── Toast ─────────────────────────────────────────────────
  function setupToastEvents() {
    els.toastCloseBtn.addEventListener('click', hideToast);
  }

  function showToast(msg) {
    els.errorMessage.textContent = msg;
    els.errorToast.classList.remove('hidden');
    void els.errorToast.offsetWidth;
    els.errorToast.classList.add('visible');
    clearTimeout(els.errorToast._timeout);
    els.errorToast._timeout = setTimeout(hideToast, 8000);
  }

  function hideToast() {
    els.errorToast.classList.remove('visible');
    clearTimeout(els.errorToast._timeout);
    setTimeout(() => els.errorToast.classList.add('hidden'), 350);
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
})();

// Run this immediately when the page loads
document.addEventListener('DOMContentLoaded', () => {
    
    // Check with the backend if the user has an active session cookie
    fetch('/api/auth/me')
        .then(response => response.json())
        .then(data => {
            if (!data.authenticated) {
                // If NOT logged in: Hide the dashboard, show a Login UI
                document.getElementById('dashboard-ui').style.display = 'none';
                document.getElementById('login-screen').style.display = 'flex';
            } else {
                // If LOGGED in: Show the dashboard, maybe display their name
                document.getElementById('login-screen').style.display = 'none';
                document.getElementById('dashboard-ui').style.display = 'block';
                console.log("Welcome back,", data.user?.name);
            }
        })
        .catch(err => {
            console.error("Auth check failed:", err);
            // Default to showing login on error
            document.getElementById('dashboard-ui').style.display = 'none';
            document.getElementById('login-screen').style.display = 'flex';
        });

    // Attach this to your "Sign in with Google" button in the HTML
    // We put this inside DOMContentLoaded to ensure the element exists
    const loginBtn = document.getElementById('google-login-btn');
    if (loginBtn) {
        loginBtn.addEventListener('click', () => {
            // This redirects the browser to your backend login route, which hands off to Google
            window.location.href = '/api/auth/login'; 
        });
    }
});

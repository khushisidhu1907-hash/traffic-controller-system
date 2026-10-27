(() => {
  'use strict';
  const API = '';
  const HISTORY_LIMIT = 72;
  const STORAGE_KEY = 'flowstate-dashboard-settings';
  const allowedIntervals = [3000, 5000, 10000];
  const savedSettings = (() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch { return {}; }
  })();
  let refreshMs = Number(savedSettings.refreshMs) || 3000;
  if (!allowedIntervals.includes(refreshMs)) refreshMs = 3000;
  let queueThreshold = Number.isInteger(savedSettings.queueThreshold) && savedSettings.queueThreshold >= 1 && savedSettings.queueThreshold <= 100 ? savedSettings.queueThreshold : 8;
  let waitThreshold = Number.isInteger(savedSettings.waitThreshold) && savedSettings.waitThreshold >= 1 && savedSettings.waitThreshold <= 3600 ? savedSettings.waitThreshold : 60;
  let liveTimer;
  let historyTimer;
  let countdownTimer;
  let phaseRefreshTimer;
  let toastTimer;
  let latestMetrics = null;
  let latestController = null;
  let latestSimulation = null;
  let lastHistoryAt = 0;
  let lastSummaryAt = 0;
  let cachedSummary = null;
  let historyFailure = false;
  let liveRefreshInFlight = false;
  let phaseRefreshInFlight = false;
  let historyRefreshInFlight = false;
  let performanceFailure = false;
  let latestPerformance = null;
  let lastPerformanceAt = 0;
  let observedEvents = [];
  let activeAlerts = new Set();
  let lastObservedPhase = null;
  let lastObservedMode = null;
  let lastBackendState = null;
  let lastTelemetryState = null;
  let lastSuccessfulMetricsAt = 0;

  const $ = (id) => document.getElementById(id);
  const THEME_STORAGE_KEY = 'flowstate-theme';
  const fmt = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
  const parseApiDate = (value) => {
    if (!value) return null;
    const text = String(value);
    const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text);
    const date = new Date(hasZone ? text : `${text}Z`);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  async function api(path, options = {}) {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 4500);
    let response;
    try {
      response = await fetch(`${API}${path}`, {
        cache: 'no-store', ...options, signal: abort.signal,
        headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
      });
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Request timed out after 4.5 seconds');
      throw error;
    } finally { clearTimeout(timer); }
    let body;
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok) throw new Error(body?.detail || `Request failed (${response.status})`);
    return body;
  }

  function toast(message, isError = false) {
    const node = $('toast');
    node.textContent = message;
    node.classList.toggle('error', isError);
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 3200);
  }

  function setConnection(backendOnline, telemetryOnline, reason = '') {
    const backendBadge = $('backendBadge');
    backendBadge.classList.toggle('connected', backendOnline);
    backendBadge.classList.toggle('disconnected', !backendOnline);
    backendBadge.querySelector('span:last-child').textContent = backendOnline ? 'Backend healthy' : 'Backend unavailable';
    const telemetryBadge = $('telemetryBadge');
    telemetryBadge.classList.toggle('connected', telemetryOnline);
    telemetryBadge.classList.toggle('disconnected', !telemetryOnline);
    telemetryBadge.querySelector('span:last-child').textContent = telemetryOnline ? 'Simulation connected' : backendOnline ? 'Telemetry stale / offline' : 'Simulation status unknown';
    $('sidebarPulse').style.background = !backendOnline ? 'var(--red)' : telemetryOnline ? 'var(--green)' : 'var(--amber)';
    $('sidebarConnection').textContent = !backendOnline ? 'API offline' : telemetryOnline ? 'Live connection' : 'No telemetry';
    const banner = $('connectionBanner');
    const down = !backendOnline || !telemetryOnline;
    banner.classList.toggle('hidden', !down);
    if (down) {
      $('bannerTitle').textContent = !backendOnline ? 'Backend is unreachable' : 'Simulation telemetry is disconnected';
      $('bannerMessage').textContent = reason || (!backendOnline ? 'Check that FastAPI is running, then retry.' : 'Start the Pygame simulation to resume real-time updates.');
    }
    document.querySelectorAll('.metric-card').forEach((card) => card.classList.toggle('is-stale', !telemetryOnline));
    $('livePill').textContent = telemetryOnline ? 'LIVE' : latestMetrics ? 'STALE' : '—';
    $('metricFreshness').textContent = telemetryOnline ? 'Live detected volume' : latestMetrics ? 'Last received snapshot · stale' : 'Awaiting real telemetry';
    $('topbarMode').textContent = latestController?.active_mode ? `MODE ${latestController.active_mode.toUpperCase()}${latestController.pending ? ' · PENDING' : ''}` : 'MODE —';
    observeConnection(backendOnline, telemetryOnline);
  }

  function renderMetrics(data) {
    latestMetrics = data;
    lastSuccessfulMetricsAt = Date.now();
    $('totalVehicles').textContent = data.total_vehicles;
    $('averageWait').textContent = fmt(data.average_waiting_time, 1);
    $('vehiclesPassed').textContent = data.vehicles_passed;
    $('maximumQueue').textContent = data.maximum_queue_length;
    $('motorcycleCount').textContent = data.motorcycle_count;
    $('carCount').textContent = data.car_count;
    $('truckCount').textContent = data.truck_count;
    const queues = data.queues || {};
    const entries = Object.values(queues).map(Number).filter(Number.isFinite);
    const max = Math.max(1, ...entries);
    for (const direction of ['north', 'south', 'east', 'west']) {
      const value = Number(queues[direction]);
      const row = document.querySelector(`.queue-row[data-direction="${direction}"]`);
      row.querySelector('strong').textContent = Number.isFinite(value) ? value : '—';
      row.querySelector('.queue-track i').style.width = `${Number.isFinite(value) ? Math.min(100, (value / max) * 100) : 0}%`;
    }
    $('avgQueue').textContent = entries.length ? fmt(entries.reduce((sum, n) => sum + n, 0) / entries.length, 1) : '—';
    const updated = parseApiDate(data.received_at || data.simulation_timestamp);
    $('updatedAt').textContent = updated ? `Updated ${updated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}` : 'Received telemetry';
    const phase = latestController?.simulation_connected ? latestController.phase : data.signal_phase;
    updateDirectionalStates(phase, Boolean(latestController?.simulation_connected));
  }

  function setSignal(node, state) {
    node.parentElement.classList.remove('active-green', 'active-yellow', 'active-red');
    node.parentElement.classList.add(`active-${state}`);
  }

  function renderController(data) {
    latestController = data;
    const connected = Boolean(data.simulation_connected);
    const active = data.active_mode;
    const phase = data.phase || latestMetrics?.signal_phase || '';
    const axis = phase.startsWith('NS') ? 'NS' : phase.startsWith('EW') ? 'EW' : '';
    const transitional = phase.includes('YELLOW');
    $('activePhase').textContent = !connected ? 'Unavailable' : phase === 'ALL_RED' ? 'All red clearance' : axis ? `${axis === 'NS' ? 'North–South' : 'East–West'}${transitional ? ' · Yellow' : ''}` : phase || '—';
    $('greenDuration').textContent = connected && data.green_duration != null ? `${fmt(data.green_duration, 1)} sec` : '—';
    renderPhaseCountdown(data);
    $('modePill').textContent = active ? `${active.toUpperCase()} MODE` : 'MODE UNAVAILABLE';
    $('modePill').className = `mode-pill ${active || 'unavailable'}`;
    for (const id of ['signalNorth', 'signalSouth']) setSignal($(id), !connected ? 'unknown' : phase === 'ALL_RED' ? 'red' : axis === 'NS' && transitional ? 'yellow' : axis === 'NS' ? 'green' : 'red');
    for (const id of ['signalEast', 'signalWest']) setSignal($(id), !connected ? 'unknown' : phase === 'ALL_RED' ? 'red' : axis === 'EW' && transitional ? 'yellow' : axis === 'EW' ? 'green' : 'red');
    updateDirectionalStates(phase, connected);

    const decision = active === 'adaptive' ? data.last_decision : null;
    $('decisionReason').textContent = decision?.reason || (active === 'adaptive' ? 'Waiting for the adaptive controller’s next safe-boundary decision.' : active === 'fixed' ? 'Fixed-time schedule is active; no adaptive decision controls this phase.' : 'Waiting for the simulator to report its active controller mode.');
    $('decisionTime').textContent = decision?.decided_at ? new Date(decision.decided_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
    const select = $('modeSelect');
    if (document.activeElement !== select && data.requested_mode) select.value = data.requested_mode;
    select.disabled = !connected;
    const feedback = $('controlFeedback');
    if (!connected) {
      feedback.textContent = data.pending ? 'Mode request queued; waiting for the simulator to connect.' : 'Mode control requires a connected simulator.';
      feedback.className = 'control-feedback';
    } else if (data.pending) {
      feedback.textContent = `Requested ${data.requested_mode}; waiting for a safe phase boundary (active: ${active || 'not yet acknowledged'}).`;
      feedback.className = 'control-feedback';
    } else {
      feedback.textContent = `Simulator confirms ${active} mode is active.`;
      feedback.className = 'control-feedback success';
    }
    $('topbarMode').textContent = active ? `MODE ${active.toUpperCase()}${data.pending ? ' · PENDING' : ''}` : 'MODE —';
    observeController(data);
  }

  function phaseLabel(phase) {
    if (phase === 'ALL_RED') return 'All-red clearance';
    const axis = phase?.startsWith('NS') ? 'North–South' : phase?.startsWith('EW') ? 'East–West' : '';
    if (!axis) return 'Unavailable';
    return `${axis} · ${phase.endsWith('_YELLOW') ? 'Yellow' : 'Green'}`;
  }

  function phasePreviewLabel(phase) {
    if (phase === 'NS') return 'North–South · Green';
    if (phase === 'EW') return 'East–West · Green';
    if (phase === 'ADAPTIVE_SELECTION') return 'Adaptive selection at boundary';
    return 'Awaiting simulator preview';
  }

  function renderPhaseCountdown(data = latestController) {
    const monitor = $('phaseMonitor');
    if (!monitor) return;
    const currentPhase = String(data?.phase || '');
    const updatedAt = parseApiDate(data?.updated_at);
    const age = updatedAt ? Math.max(0, (Date.now() - updatedAt.getTime()) / 1000) : Infinity;
    const connected = Boolean(data?.simulation_connected) && age <= 5;
    const duration = Number(data?.phase_duration_seconds);
    const elapsed = Number(data?.phase_elapsed_seconds);
    const timingAvailable = connected && updatedAt && Number.isFinite(duration) && duration > 0
      && Number.isFinite(elapsed) && elapsed >= 0 && Boolean(currentPhase);
    const paused = connected && Boolean(data.paused);
    const remaining = timingAvailable
      ? Math.max(0, duration - elapsed - (paused ? 0 : age))
      : null;
    const phase = connected ? phaseLabel(currentPhase) : 'Unavailable';
    const signal = !connected ? 'Unavailable' : currentPhase === 'ALL_RED' || currentPhase.endsWith('_YELLOW')
      ? currentPhase === 'ALL_RED' ? 'Red' : 'Yellow'
      : ['NS', 'EW'].includes(currentPhase) ? 'Green' : 'Unavailable';
    const nextPhaseSeconds = Number(data?.next_phase_in_seconds);
    const previewAvailable = connected && updatedAt && Number.isFinite(nextPhaseSeconds) && nextPhaseSeconds >= 0
      && Boolean(data?.next_phase_preview);
    const nextGreenIn = previewAvailable
      ? Math.max(0, nextPhaseSeconds - (paused ? 0 : age))
      : null;
    const preview = connected ? phasePreviewLabel(data?.next_phase_preview) : 'Unavailable';
    const phaseStatus = !connected ? 'DISCONNECTED'
      : paused ? 'PAUSED'
        : currentPhase.includes('YELLOW') || currentPhase === 'ALL_RED' || remaining === 0
          ? 'TRANSITIONING' : 'ACTIVE';

    monitor.classList.toggle('disconnected', !connected);
    monitor.classList.toggle('paused', paused);
    monitor.classList.toggle('final-seconds', remaining !== null && remaining <= 3 && remaining > 0);
    monitor.dataset.signal = signal.toLowerCase();
    $('activePhase').textContent = phase;
    $('phaseStatusLabel').textContent = phaseStatus;
    $('phaseSignalState').textContent = signal;
    $('phaseCountdown').textContent = remaining === null ? '—' : String(Math.ceil(remaining)).padStart(2, '0');
    $('phaseTransitionCountdown').textContent = remaining === null ? '—' : `in ${Math.ceil(remaining)} sec`;
    $('greenDuration').textContent = timingAvailable ? `${fmt(duration, 1)} sec` : '—';
    $('nextPhasePreview').textContent = preview;
    $('nextPhaseTime').textContent = nextGreenIn === null ? '—' : `in ${Math.ceil(nextGreenIn)} sec`;

    const progress = remaining === null ? 0 : Math.min(1, remaining / duration);
    $('phaseProgress').style.strokeDashoffset = String(251.2 * (1 - progress));
    const gaugeLabel = timingAvailable
      ? `${phase}, ${Math.ceil(remaining)} seconds remaining, ${phaseStatus.toLowerCase()}`
      : `${phase}, countdown unavailable because simulator timing is ${connected ? 'missing' : 'disconnected'}`;
    $('phaseGauge').setAttribute('aria-label', gaugeLabel);
    const warning = $('phaseConnectionWarning');
    warning.classList.toggle('hidden', Boolean(timingAvailable));
    $('phaseLastUpdate').textContent = updatedAt
      ? `Last simulator update ${updatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}. ${connected ? 'Waiting for valid phase timing.' : 'Reconnect the simulator to resume the countdown.'}`
      : 'No simulator timing data has been received.';
  }

  function renderSimulation(data) {
    latestSimulation = data;
    const connected = Boolean(data.telemetry_connected);
    const pending = Boolean(data.pending);
    const notice = $('simulationNotice');
    notice.innerHTML = connected
      ? `<strong>Simulation ${data.paused ? 'is paused' : 'is running'}.</strong> Pause, resume, reset, and density changes are confirmed by the connected simulator.`
      : '<strong>Simulation must be running.</strong> Start Pygame directly; controls apply after it connects.';
    const pauseButton = $('pauseResumeButton');
    pauseButton.disabled = !connected || pending;
    pauseButton.textContent = data.requested_paused ? 'Resume simulation' : 'Pause simulation';
    const resetButton = $('resetSimulationButton');
    resetButton.disabled = !connected || pending;
    const density = $('densityInput');
    density.disabled = !connected || pending;
    if (document.activeElement !== density) density.value = String(data.requested_density ?? data.density ?? 3);
    $('densityValue').textContent = data.requested_density ?? data.density ?? '—';
    const status = $('scenarioState');
    status.textContent = !connected ? 'SIMULATOR OFFLINE · launch Pygame to enable controls' : pending ? `COMMAND PENDING · waiting for simulator (applied revision ${data.applied_revision})` : `SIMULATOR ${data.paused ? 'PAUSED' : 'RUNNING'} · density ${data.density}`;
    status.className = `scenario-state${!connected ? ' offline' : pending ? ' pending' : ' connected'}`;
    $('scenarioFeedback').textContent = pending ? 'Waiting for the Pygame main loop to apply and acknowledge this change.' : connected ? `Simulator confirms ${data.paused ? 'paused' : 'running'} at density ${data.density}.` : 'Controls unlock when the simulator heartbeat is connected.';
    $('scenarioFeedback').className = `control-feedback${connected && !pending ? ' success' : ''}`;
  }

  function updateDirectionalStates(phase, connected) {
    const axis = String(phase || '').startsWith('NS') ? 'NS' : String(phase || '').startsWith('EW') ? 'EW' : '';
    const yellow = String(phase || '').includes('YELLOW');
    for (const direction of ['north', 'south', 'east', 'west']) {
      const row = document.querySelector(`.queue-row[data-direction="${direction}"]`);
      if (!row) continue;
      const directionAxis = ['north', 'south'].includes(direction) ? 'NS' : 'EW';
      const state = !connected ? 'Unavailable' : phase === 'ALL_RED' ? 'All red' : axis === directionAxis && yellow ? 'Yellow' : axis === directionAxis ? 'Green' : 'Red';
      const label = row.querySelector('.direction-state');
      if (label) label.textContent = state;
      row.classList.toggle('is-active', connected && axis === directionAxis && !yellow);
    }
  }

  function recordEvent(severity, category, message, key) {
    const event = { id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, at: new Date().toISOString(), severity, category, message, key };
    observedEvents.unshift(event);
    observedEvents = observedEvents.slice(0, 60);
    renderEvents();
  }

  function observeConnection(backendOnline, telemetryOnline) {
    if (lastBackendState !== null && backendOnline !== lastBackendState) {
      recordEvent(backendOnline ? 'info' : 'critical', 'Backend', backendOnline ? 'FastAPI backend connection restored.' : 'FastAPI backend stopped responding.', `backend-${backendOnline}`);
    }
    if (lastTelemetryState !== null && telemetryOnline !== lastTelemetryState) {
      recordEvent(telemetryOnline ? 'info' : 'warning', 'Telemetry', telemetryOnline ? 'Fresh simulator telemetry resumed.' : 'Simulator telemetry is disconnected or stale.', `telemetry-${telemetryOnline}`);
    }
    lastBackendState = backendOnline;
    lastTelemetryState = telemetryOnline;
  }

  function observeController(data) {
    const phase = data.phase;
    if (data.simulation_connected && phase && lastObservedPhase !== null && phase !== lastObservedPhase) {
      recordEvent('info', 'Signal phase', `Simulator reported phase ${phase}.`, `phase-${phase}-${Date.now()}`);
    }
    if (data.simulation_connected && data.active_mode && lastObservedMode !== null && data.active_mode !== lastObservedMode && !data.pending) {
      recordEvent('info', 'Controller mode', `Simulator confirmed ${data.active_mode} mode.`, `mode-${data.active_mode}-${Date.now()}`);
    }
    if (data.simulation_connected && phase) lastObservedPhase = phase;
    if (data.simulation_connected && data.active_mode && !data.pending) lastObservedMode = data.active_mode;
    const decision = data.last_decision;
    if (decision?.decided_at && decision.decided_at !== observeController.lastDecisionAt) {
      observeController.lastDecisionAt = decision.decided_at;
      recordEvent('info', 'Controller decision', `${decision.selected_phase} selected for ${fmt(decision.green_duration, 1)} sec. ${decision.reason}`, `decision-${decision.decided_at}`);
    }
  }

  function evaluateAlerts(metrics, controller) {
    if (!metrics) return;
    const queues = metrics.queues || {};
    const worst = Math.max(0, ...Object.values(queues).map((value) => Number(value) || 0));
    const queueActive = worst >= queueThreshold;
    const waitActive = Number(metrics.average_waiting_time) >= waitThreshold;
    toggleAlert('queue', queueActive, 'warning', 'Queue', `Approach queue reached ${worst} vehicles (warning threshold ${queueThreshold}).`);
    toggleAlert('wait', waitActive, 'warning', 'Waiting time', `Average waiting time reached ${fmt(metrics.average_waiting_time, 1)} sec (warning threshold ${waitThreshold} sec).`);
    const fallback = controller?.last_decision?.reason?.toLowerCase().includes('fallback') || false;
    toggleAlert('fallback', fallback, 'warning', 'Controller fallback', controller?.last_decision?.reason || 'Controller used safe fallback.');
  }

  function toggleAlert(key, active, severity, category, message) {
    const wasActive = activeAlerts.has(key);
    if (active && !wasActive) {
      activeAlerts.add(key);
      recordEvent(severity, category, message, `alert-${key}-${Date.now()}`);
    } else if (!active && wasActive) {
      activeAlerts.delete(key);
      recordEvent('info', category, `${category} alert cleared.`, `clear-${key}-${Date.now()}`);
    }
  }

  function renderEvents() {
    const filter = $('eventFilter')?.value || 'all';
    const filtered = filter === 'all' ? observedEvents : observedEvents.filter((event) => event.severity === filter);
    const list = $('eventList');
    if (!list) return;
    if (!filtered.length) {
      list.innerHTML = `<div class="empty-note">${observedEvents.length ? 'No events match this filter.' : 'No events observed since this page was opened.'}</div>`;
      return;
    }
    list.innerHTML = filtered.slice(0, 15).map((event) => {
      const date = new Date(event.at);
      const timestamp = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      return `<details class="event-row"><summary class="event-row-summary"><span class="event-severity ${esc(event.severity)}"></span><span class="event-copy"><span class="event-meta"><span>${esc(event.category)}</span><time datetime="${esc(event.at)}">${esc(timestamp)}</time></span><span class="event-message">${esc(event.message)}</span></span><span class="event-tag ${esc(event.severity)}">${esc(event.severity.toUpperCase())}</span></summary><div class="event-details"><strong>${esc(event.category)} · ${esc(event.severity.toUpperCase())}</strong><time datetime="${esc(event.at)}">${esc(date.toLocaleString())}</time><p>${esc(event.message)}</p></div></details>`;
    }).join('');
  }

  const seriesColors = { north: '#58d4cf', south: '#7da8ff', east: '#e9bb73', west: '#c195ee', wait: '#e9bb73', throughput: '#67d7a9', motorcycle: '#58d4cf', car: '#7da8ff', truck: '#e9bb73' };
  function drawChart(svgId, series, unit = '', emptyText = 'No historical snapshots available.') {
    const svg = $(svgId);
    const panel = svg.closest('.chart-panel');
    const state = panel.querySelector('.chart-state');
    const valid = series.filter((item) => item.values.some((point) => Number.isFinite(Number(point.value))));
    if (!valid.length) {
      svg.innerHTML = '';
      state.textContent = emptyText;
      state.classList.remove('hidden');
      return;
    }
    state.classList.add('hidden');
    const W = 520, H = 190, left = 36, right = 10, top = 11, bottom = 25;
    const innerW = W - left - right, innerH = H - top - bottom;
    let all = valid.flatMap((item) => item.values.map((point) => Number(point.value))).filter(Number.isFinite);
    let min = Math.min(0, ...all), max = Math.max(...all);
    if (max === min) max = min + 1;
    const pad = (max - min) * .12;
    max += pad;
    const xAt = (index, size) => left + (size <= 1 ? innerW : index / (size - 1) * innerW);
    const yAt = (value) => top + (max - value) / (max - min) * innerH;
    let content = '';
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const value = min + (max - min) * (1 - i / ticks);
      const y = top + innerH * i / ticks;
      content += `<line class="chart-gridlines" x1="${left}" y1="${y}" x2="${W - right}" y2="${y}"/><text class="chart-axis" x="${left - 8}" y="${y + 3}" text-anchor="end">${fmt(value, max - min < 10 ? 1 : 0)}</text>`;
    }
    for (const item of valid) {
      const values = item.values.map((point, i) => [xAt(i, item.values.length), yAt(Number(point.value))]).filter((point) => point.every(Number.isFinite));
      if (!values.length) continue;
      const path = values.map((point, i) => `${i ? 'L' : 'M'}${point[0].toFixed(1)},${point[1].toFixed(1)}`).join(' ');
      const color = seriesColors[item.name];
      if (valid.length === 1 && values.length > 1) content += `<path class="chart-area" fill="${color}" d="${path} L${values[values.length - 1][0]},${top + innerH} L${values[0][0]},${top + innerH} Z"/>`;
      content += `<path class="chart-line" stroke="${color}" d="${path}"/>`;
      const last = values[values.length - 1];
      content += `<circle class="chart-point" cx="${last[0]}" cy="${last[1]}" r="3.5" fill="${color}"/>`;
    }
    const firstLabel = valid[0].values[0]?.timestamp;
    const lastLabel = valid[0].values[valid[0].values.length - 1]?.timestamp;
    const timeLabel = (value) => parseApiDate(value)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) || '';
    content += `<text class="chart-axis" x="${left}" y="${H - 5}">${esc(timeLabel(firstLabel))}</text><text class="chart-axis" x="${W - right}" y="${H - 5}" text-anchor="end">${esc(timeLabel(lastLabel))}</text>`;
    svg.innerHTML = content;
  }

  function renderHistory(rows, summary) {
    const chronological = Array.isArray(rows) ? [...rows].reverse() : [];
    const range = $('chartRange')?.value || 'all';
    const cutoff = range === 'all' ? 0 : Date.now() - Number(range) * 60 * 1000;
    const data = chronological.filter((row) => {
      const timestamp = parseApiDate(row.simulation_timestamp);
      return timestamp && (!cutoff || timestamp.getTime() >= cutoff);
    });
    const latestSnapshotDate = data.length ? parseApiDate(data[data.length - 1].received_at || data[data.length - 1].simulation_timestamp) : null;
    $('historyMeta').textContent = data.length ? `${data.length} snapshots in selected window · latest ${latestSnapshotDate?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) || 'time unavailable'}` : chronological.length ? 'No snapshots in this time window' : 'No saved telemetry yet';
    const makeValues = (pick) => data.map((row) => ({ value: pick(row), timestamp: row.simulation_timestamp }));
    drawChart('queueChart', ['north', 'south', 'east', 'west'].map((direction) => ({ name: direction, values: makeValues((row) => row.queues?.[direction]) })), 'vehicles', 'No queue history has been recorded yet.');
    drawChart('waitChart', [{ name: 'wait', values: makeValues((row) => row.average_waiting_time) }], 'seconds', 'No waiting-time history has been recorded yet.');
    drawChart('throughputChart', [{ name: 'throughput', values: makeValues((row) => row.vehicles_passed) }], 'vehicles', 'No throughput history has been recorded yet.');
    drawChart('compositionChart', ['motorcycle', 'car', 'truck'].map((kind) => ({ name: kind, values: makeValues((row) => row[`${kind}_count`]) })), 'vehicles', 'No vehicle composition history has been recorded yet.');
    renderPhaseHistory(data);
    const strip = $('historySummary');
    if (summary && summary.snapshot_count > 0) {
      strip.innerHTML = `<span class="history-strip-icon">◷</span><span><strong>${summary.snapshot_count.toLocaleString()} stored snapshots</strong> · historical average ${fmt(summary.average_vehicles, 1)} vehicles on road · average wait ${fmt(summary.average_waiting_time, 1)} sec · peak queue ${summary.maximum_observed_queue}.</span>`;
    } else {
      strip.innerHTML = '<span class="history-strip-icon">◷</span><span>Historical analytics unavailable until real simulation snapshots have been saved.</span>';
    }
    $('sessionSnapshotCount').textContent = summary ? summary.snapshot_count.toLocaleString() : 'Unavailable';
    $('sessionLastSample').textContent = summary?.last_snapshot ? (parseApiDate(summary.last_snapshot)?.toLocaleString() || 'Time unavailable') : 'No saved samples';
  }

  function renderPerformance(result) {
    latestPerformance = result;
    const table = $('performanceTable');
    if (!result?.modes) {
      table.innerHTML = '<div class="empty-note">Recorded mode comparison is unavailable.</div>';
      performanceFailure = true;
      return;
    }
    performanceFailure = false;
    lastPerformanceAt = Date.now();
    table.innerHTML = ['fixed', 'adaptive'].map((mode) => {
      const data = result.modes[mode];
      if (!data || !data.snapshot_count) return `<div class="performance-mode"><strong>${mode}</strong><span class="muted">No recorded ${mode} snapshots yet.</span></div>`;
      return `<div class="performance-mode"><strong>${mode}</strong><span>${data.snapshot_count.toLocaleString()} samples</span><span>Avg wait <b>${fmt(data.average_waiting_time_seconds, 1)} sec</b></span><span>Avg queue <b>${fmt(data.average_queue_length_per_approach, 1)} / approach</b></span><span>Max queue <b>${data.maximum_observed_queue}</b></span></div>`;
    }).join('') + `<p class="comparison-note">${esc(result.note || 'Recorded measurements only; differences are not a controlled experiment.')}</p>`;
  }

  function renderPhaseHistory(rows) {
    const container = $('phaseHistory');
    const card = container.closest('.chart-panel');
    const state = card.querySelector('.chart-state');
    if (!rows.length) {
      container.innerHTML = '';
      state.textContent = 'No signal phase history recorded in this time window.';
      state.classList.remove('hidden');
      return;
    }
    state.classList.add('hidden');
    container.innerHTML = rows.map((row) => {
      const phase = String(row.signal_phase || 'unknown');
      const axis = phase.startsWith('NS') ? 'NS' : phase.startsWith('EW') ? 'EW' : 'clear';
      const date = parseApiDate(row.simulation_timestamp);
      const label = `${phase} · ${date?.toLocaleString() || 'timestamp unavailable'}`;
      return `<span class="phase-segment ${axis.toLowerCase()}" title="${esc(label)}" aria-label="${esc(label)}"></span>`;
    }).join('');
  }

  async function refreshLive() {
    if (liveRefreshInFlight) return;
    liveRefreshInFlight = true;
    try {
    const results = await Promise.allSettled([api('/api/metrics'), api('/api/status'), api('/api/controller/status'), api('/api/simulation/status')]);
    const [metricsResult, apiStatusResult, controllerResult, simulationResult] = results;
    const backendOnline = apiStatusResult.status === 'fulfilled' || metricsResult.status === 'fulfilled' || controllerResult.status === 'fulfilled' || simulationResult.status === 'fulfilled';
    if (metricsResult.status === 'fulfilled') renderMetrics(metricsResult.value);
    if (controllerResult.status === 'fulfilled') renderController(controllerResult.value);
    if (simulationResult.status === 'fulfilled') renderSimulation(simulationResult.value);
    const telemetryOnline = Boolean(apiStatusResult.status === 'fulfilled' && apiStatusResult.value.simulation_connected && controllerResult.status === 'fulfilled' && controllerResult.value.simulation_connected);
    let error = '';
    if (!backendOnline) error = metricsResult.reason?.message || apiStatusResult.reason?.message || 'The FastAPI service did not respond.';
    else if (metricsResult.status === 'rejected' && metricsResult.reason?.message) error = metricsResult.reason.message;
    setConnection(backendOnline, telemetryOnline, error);
    if (metricsResult.status === 'fulfilled' && telemetryOnline) evaluateAlerts(metricsResult.value, controllerResult.status === 'fulfilled' ? controllerResult.value : null);
    if (metricsResult.status === 'rejected' && metricsResult.reason?.message.includes('not available yet')) {
      $('updatedAt').textContent = 'Waiting for first telemetry snapshot…';
    }
    if (backendOnline && !telemetryOnline) $('updatedAt').textContent = latestMetrics ? 'Telemetry is stale' : 'Waiting for telemetry…';
    } finally {
      liveRefreshInFlight = false;
    }
  }

  async function refreshPhase() {
    if (phaseRefreshInFlight || document.hidden) return;
    phaseRefreshInFlight = true;
    try {
      renderController(await api('/api/controller/status'));
    } catch (error) {
      renderPhaseCountdown(latestController ? { ...latestController, simulation_connected: false } : null);
      $('phaseLastUpdate').textContent = `Unable to refresh controller state: ${error.message}`;
    } finally {
      phaseRefreshInFlight = false;
    }
  }

  async function refreshHistory() {
    if (historyRefreshInFlight) return;
    historyRefreshInFlight = true;
    try {
      const selectedRange = $('chartRange')?.value || 'all';
      const historyLimit = selectedRange === '5' ? 300 : selectedRange === '15' ? 900 : HISTORY_LIMIT;
      const requests = [api(`/api/metrics/history?limit=${historyLimit}&offset=0`)];
      if (!cachedSummary || Date.now() - lastSummaryAt >= 60000) requests.push(api('/api/analytics/summary'));
      const [rows, summary] = await Promise.all(requests);
      if (summary) {
        cachedSummary = summary;
        lastSummaryAt = Date.now();
      }
      renderHistory(rows, cachedSummary);
      try {
        if (!latestPerformance || Date.now() - lastPerformanceAt >= 60000) {
          renderPerformance(await api('/api/controller/performance'));
        }
      }
      catch (performanceError) {
        performanceFailure = true;
        lastPerformanceAt = Date.now();
        $('performanceTable').innerHTML = `<div class="empty-note">Recorded mode comparison unavailable: ${esc(performanceError.message)}</div>`;
      }
      historyFailure = false;
      lastHistoryAt = Date.now();
    } catch (error) {
      historyFailure = true;
      $('historyMeta').textContent = 'Historical data unavailable';
      $('historySummary').innerHTML = `<span class="history-strip-icon">!</span><span>Could not load saved analytics: ${esc(error.message)}. Live metrics remain separate.</span>`;
      document.querySelectorAll('.chart-state').forEach((state) => { state.textContent = 'Historical data request failed. Retry when the API is available.'; state.classList.remove('hidden'); });
    } finally {
      historyRefreshInFlight = false;
    }
  }

  async function changeMode(event) {
    const mode = event.target.value;
    event.target.disabled = true;
    $('controlFeedback').textContent = `Requesting ${mode} mode…`;
    $('controlFeedback').className = 'control-feedback';
    try {
      const state = await api('/api/controller/mode', { method: 'POST', body: JSON.stringify({ mode }) });
      renderController(state);
      toast(state.pending ? `${mode} mode requested; awaiting safe-boundary application.` : `${mode} mode is active.`);
      await refreshLive();
    } catch (error) {
      toast(`Mode change failed: ${error.message}`, true);
      $('controlFeedback').textContent = `Request failed: ${error.message}`;
      $('controlFeedback').className = 'control-feedback error';
      if (latestController?.requested_mode) event.target.value = latestController.requested_mode;
    } finally {
      event.target.disabled = !latestController?.simulation_connected;
    }
  }

  async function sendSimulationCommand(path, body = {}) {
    $('pauseResumeButton').disabled = true;
    $('resetSimulationButton').disabled = true;
    $('densityInput').disabled = true;
    $('scenarioFeedback').textContent = 'Sending command to the connected simulator…';
    try {
      const result = await api(`/api/simulation/${path}`, { method: 'POST', body: JSON.stringify({ requested_by: 'dashboard', ...body }) });
      $('scenarioState').textContent = `COMMAND PENDING · ${path}`;
      $('scenarioState').className = 'scenario-state pending';
      $('scenarioFeedback').textContent = result.message || 'Waiting for simulator acknowledgement.';
      toast(result.message || 'Command sent; awaiting simulator acknowledgement.');
      await refreshLive();
    } catch (error) {
      $('scenarioFeedback').textContent = `Command failed: ${error.message}`;
      $('scenarioFeedback').className = 'control-feedback error';
      toast(`Simulation command failed: ${error.message}`, true);
      await refreshLive();
    }
  }

  function schedule() {
    clearInterval(liveTimer);
    clearInterval(historyTimer);
    liveTimer = setInterval(refreshLive, refreshMs);
    historyTimer = setInterval(refreshHistory, Math.max(15000, refreshMs * 5));
    if (!countdownTimer) countdownTimer = setInterval(renderPhaseCountdown, 250);
    if (!phaseRefreshTimer) phaseRefreshTimer = setInterval(refreshPhase, 500);
    $('refreshIntervalLabel').textContent = `${refreshMs / 1000} seconds`;
  }

  function savePreferences(event) {
    event.preventDefault();
    if (event.submitter?.value === 'cancel') {
      $('settingsDialog').close();
      return;
    }
    const refresh = Number($('settingsRefresh').value);
    const queue = Number($('queueThreshold').value);
    const waiting = Number($('waitThreshold').value);
    if (!allowedIntervals.includes(refresh) || !Number.isInteger(queue) || queue < 1 || queue > 100 || !Number.isInteger(waiting) || waiting < 1 || waiting > 3600) {
      toast('Check refresh interval and alert thresholds.', true);
      return;
    }
    refreshMs = refresh;
    queueThreshold = queue;
    waitThreshold = waiting;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ refreshMs, queueThreshold, waitThreshold }));
    schedule();
    $('settingsDialog').close();
    toast('Dashboard preferences saved in this browser.');
    if (latestMetrics) evaluateAlerts(latestMetrics, latestController);
  }

  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
      else await document.exitFullscreen();
    } catch { toast('Fullscreen is unavailable in this browser context.', true); }
    $('fullscreenButton').setAttribute('aria-label', document.fullscreenElement ? 'Exit fullscreen' : 'Enter fullscreen');
  }

  function syncSettingsForm() {
    $('settingsRefresh').value = String(refreshMs);
    $('queueThreshold').value = String(queueThreshold);
    $('waitThreshold').value = String(waitThreshold);
  }

  function applyTheme(theme) {
    const isLight = theme === 'light';
    document.documentElement.dataset.theme = isLight ? 'light' : 'dark';
    const button = $('themeToggle');
    button.textContent = isLight ? '☀️' : '🌙';
    button.title = `${isLight ? 'Light' : 'Dark'} mode`;
    button.setAttribute('aria-label', `${isLight ? 'Light' : 'Dark'} mode active. Switch to ${isLight ? 'dark' : 'light'} mode`);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', isLight ? '#f3f6f8' : '#0b1118');
  }

  function toggleTheme() {
    const theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    applyTheme(theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      toast('Theme changed, but this browser could not save the preference.', true);
    }
  }

  applyTheme(document.documentElement.dataset.theme || 'dark');
  $('themeToggle').addEventListener('click', toggleTheme);
  $('modeSelect').addEventListener('change', changeMode);
  $('pauseResumeButton').addEventListener('click', () => sendSimulationCommand(latestSimulation?.paused ? 'start' : 'pause'));
  $('resetSimulationButton').addEventListener('click', () => {
    if (window.confirm('Reset vehicles and the spawned/passed counters? Signal mode and current density will be kept.')) sendSimulationCommand('reset');
  });
  $('densityInput').addEventListener('change', (event) => sendSimulationCommand('density', { density: Number(event.target.value) }));
  $('eventFilter').addEventListener('change', renderEvents);
  $('chartRange').addEventListener('change', refreshHistory);
  $('settingsButton').addEventListener('click', () => { syncSettingsForm(); $('settingsDialog').showModal(); });
  $('settingsForm').addEventListener('submit', savePreferences);
  $('fullscreenButton').addEventListener('click', toggleFullscreen);
  document.addEventListener('fullscreenchange', () => $('fullscreenButton').setAttribute('aria-label', document.fullscreenElement ? 'Exit fullscreen' : 'Enter fullscreen'));
  document.querySelectorAll('.nav-link[href^="#"]').forEach((link) => link.addEventListener('click', () => {
    document.querySelectorAll('.nav-link').forEach((item) => item.classList.remove('active'));
    link.classList.add('active');
  }));
  $('refreshButton').addEventListener('click', () => { refreshLive(); if (Date.now() - lastHistoryAt > 4000 || historyFailure) refreshHistory(); });
  $('retryButton').addEventListener('click', () => { refreshLive(); refreshHistory(); });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      renderPhaseCountdown();
      refreshLive();
      refreshPhase();
    }
  });
  $('refreshRateButton').addEventListener('click', () => {
    const next = allowedIntervals[(allowedIntervals.indexOf(refreshMs) + 1) % allowedIntervals.length];
    refreshMs = next;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ refreshMs, queueThreshold, waitThreshold }));
    schedule();
    toast(`Dashboard refresh set to ${refreshMs / 1000} seconds.`);
  });
  syncSettingsForm();
  refreshLive();
  refreshHistory();
  schedule();
})();

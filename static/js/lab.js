(() => {
  const configElement = document.querySelector('#lab-config');
  if (!configElement) return;
  const config = JSON.parse(configElement.textContent);
  const editor = Boolean(config.editor);
  const audio = document.querySelector('#lab-audio');
  const sourceSelect = document.querySelector('#audio-source');
  const canvas = document.querySelector('#analysis-canvas');
  const context = canvas.getContext('2d');
  const confidenceInput = document.querySelector('#confidence-filter');
  const confidenceValue = document.querySelector('#confidence-value');
  const lowOnlyInput = document.querySelector('#low-confidence-only');
  const counts = document.querySelector('#lab-counts');
  const inspector = document.querySelector('#event-inspector');
  const qualityPanel = document.querySelector('#quality-panel');
  const saveStatus = document.querySelector('#save-status');
  const channelSelect = document.querySelector('#review-channel');
  const drumStatsPanel = document.querySelector('#drum-stats');
  const drumToolbar = document.querySelector('#drum-toolbar');
  const trackScrubber = document.querySelector('#track-scrubber');
  const scrubberPreview = document.querySelector('#scrubber-preview');
  const channels = ['drums', 'bass', 'guitar', 'piano', 'vocals', 'other'];
  const noteChannels = ['bass', 'guitar', 'piano'];
  const drumPieces = ['unassigned', 'kick', 'snare', 'hi_hat', 'tom', 'crash', 'splash', 'ride', 'cymbal', 'unknown'];
  const drumLabels = {unassigned: 'UNASSIGNED', kick: 'KICK', snare: 'SNARE', hi_hat: 'HI-HAT', tom: 'TOM', crash: 'CRASH', splash: 'SPLASH', ride: 'RIDE', cymbal: 'CYMBAL', unknown: 'UNKNOWN'};
  const data = {raw: {}, processed: {}, reviewed: {}};
  const indices = {raw: {}, processed: {}, reviewed: {}};
  let deletedDrums = [];
  let stage = document.querySelector('input[name="artifact-stage"]:checked')?.value || 'processed';
  let minimumConfidence = 0;
  let drawCache = [];
  let selectedId = null;
  let selectedObject = null;
  let selectedIds = new Set();
  let selectionAnchorTime = null;
  let selectedChannel = channelSelect?.value || 'drums';
  let mergePartnerId = null;
  let reviewVersion = config.review?.version || 0;
  const timelineZoomInput = document.querySelector('#timeline-zoom');
  const timelineZoomValue = document.querySelector('#timeline-zoom-value');
  let zoomMs = Number(timelineZoomInput?.value || 15) * 1000;
  let renderWindow = {startMs: 0, endMs: 15000, plotLeft: 118, plotWidth: 900};
  let pointerState = null;
  let marquee = null;
  let auditionState = null;
  let scrubbing = false;

  const collectionFor = payload => payload?.events || payload?.notes || payload?.frames || [];
  const eventTime = item => item.timeMs ?? item.startMs ?? 0;
  const automaticDrumType = item => item.automaticType || item.automatic?.type || item.detectedType || (item.source === 'human' ? null : item.type) || 'unassigned';
  const aiConfidence = item => item.automatic?.confidence ?? item.detectedConfidence ?? item.confidence ?? item.pitchConfidence ?? null;
  const effectiveDrumType = item => item.reviewedType || item.effectiveType || automaticDrumType(item) || 'unknown';
  const drumLane = item => effectiveDrumType(item);
  const drumReviewState = (item, deleted = false) => {
    if (deleted || item.deleted) return 'DELETED';
    if (item.reviewStatus) return item.reviewStatus;
    if (item.source === 'human') return 'MANUAL';
    if (item.reviewMetadata?.confirmedAutomaticByHuman) return 'CONFIRMED';
    if (item.reviewedType) return item.reviewedType === automaticDrumType(item) ? 'CONFIRMED' : 'OVERRIDDEN';
    return 'UNREVIEWED';
  };
  const escapeHtml = value => String(value).replace(/[&<>'"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[character]));
  const lowerBound = (array, value) => { let low = 0; let high = array.length; while (low < high) { const mid = (low + high) >> 1; if (eventTime(array[mid]) < value) low = mid + 1; else high = mid; } return low; };
  const csrfToken = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const formatTime = seconds => { const value = Math.max(0, Number(seconds) || 0); const minutes = Math.floor(value / 60); const rest = value - minutes * 60; return `${String(minutes).padStart(2, '0')}:${rest.toFixed(3).padStart(6, '0')}`; };
  const checked = id => Boolean(document.querySelector(id)?.checked);
  const isDrumLaneMode = () => editor && selectedChannel === 'drums';

  config.audioSources.forEach(source => {
    const option = new Option(source.label, source.url);
    option.dataset.key = source.key;
    sourceSelect.add(option);
  });
  audio.src = config.audioSources[0]?.url || '';
  sourceSelect.addEventListener('change', () => switchAudioSource(sourceSelect.value));

  function scrubberDuration() {
    return Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : Math.max(0, Number(config.durationMs || 0) / 1000);
  }

  function configureScrubber() {
    if (!trackScrubber) return;
    const duration = scrubberDuration();
    trackScrubber.max = String(duration || 1);
    if (!scrubbing) trackScrubber.value = String(Math.min(audio.currentTime || 0, duration || 1));
  }

  audio.addEventListener('loadedmetadata', configureScrubber);
  trackScrubber?.addEventListener('pointerdown', () => { scrubbing = true; });
  trackScrubber?.addEventListener('input', () => {
    scrubbing = true;
    const target = Math.max(0, Math.min(scrubberDuration(), Number(trackScrubber.value)));
    audio.currentTime = target;
    if (scrubberPreview) scrubberPreview.textContent = formatTime(target);
  });
  const finishScrubbing = () => { scrubbing = false; configureScrubber(); };
  trackScrubber?.addEventListener('change', finishScrubbing);
  trackScrubber?.addEventListener('pointerup', finishScrubbing);
  trackScrubber?.addEventListener('pointercancel', finishScrubbing);

  function waitForMetadata() {
    if (audio.readyState >= 1) return Promise.resolve();
    return new Promise((resolve, reject) => {
      audio.addEventListener('loadedmetadata', resolve, {once: true});
      audio.addEventListener('error', reject, {once: true});
    });
  }

  async function switchAudioSource(url, restore = null) {
    const position = restore?.time ?? audio.currentTime;
    const rate = restore?.rate ?? audio.playbackRate;
    const wasPlaying = restore ? !restore.paused : !audio.paused;
    if (sourceSelect.value !== url) sourceSelect.value = url;
    audio.src = url;
    try {
      await waitForMetadata();
      audio.currentTime = Math.min(position, audio.duration || position);
      audio.playbackRate = rate;
      if (wasPlaying) await audio.play();
    } catch (error) {
      setSaveStatus(`Audio error: ${error.message}`, 'error');
    }
  }

  document.querySelector('#playback-speed')?.addEventListener('change', event => { audio.playbackRate = Number(event.target.value); });
  function setTimelineZoom(seconds) {
    const value = Math.max(0.25, Math.min(30, Number(seconds) || 15));
    zoomMs = value * 1000;
    if (timelineZoomInput) timelineZoomInput.value = String(value);
    if (timelineZoomValue) timelineZoomValue.textContent = `${value.toFixed(2)} s`;
  }
  setTimelineZoom(zoomMs / 1000);
  timelineZoomInput?.addEventListener('input', event => setTimelineZoom(event.target.value));
  channelSelect?.addEventListener('change', event => { selectedChannel = event.target.value; clearSelection(); updateEditorMode(); renderInspector(); });
  document.querySelectorAll('input[name="artifact-stage"]').forEach(input => input.addEventListener('change', event => { stage = event.target.value; clearSelection(); updatePanels(); renderInspector(); }));
  confidenceInput?.addEventListener('input', () => { minimumConfidence = Number(confidenceInput.value); confidenceValue.value = minimumConfidence.toFixed(2); });
  lowOnlyInput?.addEventListener('change', () => { if (lowOnlyInput.checked && minimumConfidence === 0) { minimumConfidence = 0.5; confidenceInput.value = '0.5'; confidenceValue.value = '0.50'; } });

  document.querySelectorAll('[data-drum-lane], #show-ai-suggestions, #show-manual, #show-deleted').forEach(input => input.addEventListener('change', updatePanels));
  const reviewedOnly = document.querySelector('#reviewed-only');
  const unreviewedOnly = document.querySelector('#unreviewed-only');
  reviewedOnly?.addEventListener('change', () => { if (reviewedOnly.checked && unreviewedOnly) unreviewedOnly.checked = false; });
  unreviewedOnly?.addEventListener('change', () => { if (unreviewedOnly.checked && reviewedOnly) reviewedOnly.checked = false; });

  const auditionBefore = document.querySelector('#audition-before');
  const auditionAfter = document.querySelector('#audition-after');
  if (auditionBefore) auditionBefore.value = config.review?.auditionBeforeMs ?? 150;
  if (auditionAfter) auditionAfter.value = config.review?.auditionAfterMs ?? 350;

  function rebuildIndices(targetStage) {
    channels.forEach(channel => { indices[targetStage][channel] = collectionFor(data[targetStage][channel]).slice().sort((a, b) => eventTime(a) - eventTime(b)); });
  }

  function setSaveStatus(message, state = 'saved') {
    if (!saveStatus) return;
    saveStatus.textContent = message;
    saveStatus.className = `save-${state}`;
  }

  function calculateDrumStats() {
    const active = indices.reviewed.drums || [];
    const countsByPiece = Object.fromEntries(drumPieces.map(piece => [piece, 0]));
    active.forEach(item => { countsByPiece[drumLane(item)] += 1; });
    const assigned = active.filter(item => drumReviewState(item) !== 'UNREVIEWED').length;
    const reviewed = assigned + deletedDrums.length;
    const reviewable = active.length + deletedDrums.length;
    return {
      totalDetected: (indices.processed.drums || []).length,
      active: active.length,
      reviewed,
      assigned,
      unassigned: countsByPiece.unassigned,
      deleted: deletedDrums.length,
      manualAdded: active.filter(item => item.source === 'human').length,
      progress: reviewable ? reviewed / reviewable : 1,
      counts: countsByPiece,
    };
  }

  function updatePanels() {
    const rows = channels.map(channel => `<tr><th>${channel}</th><td>${indices.raw[channel]?.length || 0}</td><td>${indices.processed[channel]?.length || 0}</td><td>${indices.reviewed[channel]?.length || 0}</td></tr>`).join('');
    counts.innerHTML = `<table class="count-table"><thead><tr><th>Channel</th><th>Raw</th><th>Processed</th><th>Reviewed</th></tr></thead><tbody>${rows}</tbody></table>`;
    if (drumStatsPanel) {
      const summary = calculateDrumStats();
      const pieceRows = drumPieces.filter(piece => piece !== 'unassigned').map(piece => `<span><strong>${drumLabels[piece]}</strong> ${summary.counts[piece]}</span>`).join('');
      const transcription = data.processed.drums?.transcription || data.raw.drums?.transcription;
      const automaticSummary = transcription ? `<div class="transcription-summary"><strong>Automatic Drum Transcription</strong><span>Backend: ${escapeHtml(String(transcription.backend || 'none').toUpperCase())}</span><span>Device: ${escapeHtml(String(transcription.device || '—').toUpperCase())}</span><span>Duration: ${Number(transcription.processingTime || 0).toFixed(2)} s</span><span>Events: ${Number(transcription.automaticEventCount ?? transcription.eventCount ?? 0)}</span><span>Fallback: ${transcription.fallbackUsed ? 'yes' : 'no'}</span>${Object.entries(transcription.classCounts || {}).map(([piece, count]) => `<span>${escapeHtml(drumLabels[piece] || piece)}: ${Number(count)}</span>`).join('')}${(transcription.warnings || []).map(warning => `<small class="review-warning">${escapeHtml(warning)}</small>`).join('')}</div>` : '';
      drumStatsPanel.innerHTML = `${automaticSummary}<div class="drum-progress"><strong>Drums review progress: ${(summary.progress * 100).toFixed(1)}%</strong><div class="progress"><i style="width:${summary.progress * 100}%"></i></div>${summary.unassigned ? `<p class="review-warning">${summary.unassigned} drum events remain unassigned.</p>` : ''}</div><div class="drum-counter-grid"><span>Detected hits: <strong>${summary.totalDetected}</strong></span><span>Reviewed: <strong>${summary.reviewed}</strong></span><span>Unassigned: <strong>${summary.unassigned}</strong></span><span>Deleted: <strong>${summary.deleted}</strong></span><span>Manual added: <strong>${summary.manualAdded}</strong></span>${pieceRows}</div>`;
    }
    const selectionCount = document.querySelector('#selection-count');
    if (selectionCount) selectionCount.textContent = `${selectedIds.size} selected`;
    if (!qualityPanel) return;
    qualityPanel.innerHTML = channels.map(channel => {
      const quality = data.reviewed[channel]?.quality || data.processed[channel]?.quality;
      if (!quality) return `<div class="quality-row"><strong>${channel}</strong><span>pending</span></div>`;
      return `<div class="quality-row quality-${quality.status}"><strong>${channel}</strong><span>${quality.status} · ${quality.score}</span><small>${(quality.warnings || []).join(' ') || 'No warnings'}</small><details><summary>Metrics</summary><pre>${JSON.stringify(quality.metrics, null, 2)}</pre></details></div>`;
    }).join('');
  }

  function updateEditorMode() {
    if (drumToolbar) drumToolbar.hidden = !isDrumLaneMode();
    const filters = document.querySelector('#drum-filter-panel');
    if (filters) filters.hidden = !isDrumLaneMode();
  }

  async function loadReviewed() {
    if (!editor) return;
    const response = await fetch(config.review.dataUrl);
    if (!response.ok) throw new Error((await response.json()).error || 'Could not load review data.');
    const payload = await response.json();
    data.reviewed = payload.channels;
    deletedDrums = (payload.deletedDrums || []).sort((a, b) => eventTime(a) - eventTime(b));
    reviewVersion = payload.session.version;
    rebuildIndices('reviewed');
    updatePanels();
    renderInspector();
  }

  function resizeCanvas(height) {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    canvas.style.height = `${height}px`;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    }
    return {width, height};
  }

  function timeWindow() {
    const nowMs = (audio.currentTime || 0) * 1000;
    const beforeMs = zoomMs / 3;
    const afterMs = zoomMs - beforeMs;
    return {nowMs, beforeMs, afterMs, startMs: nowMs - beforeMs, endMs: nowMs + afterMs};
  }

  function confidenceVisible(item) {
    const value = aiConfidence(item);
    if (value == null) return !lowOnlyInput?.checked;
    return lowOnlyInput?.checked ? value < minimumConfidence : value >= minimumConfidence;
  }

  function visibleItems(channel, startMs, endMs) {
    const items = indices[stage][channel] || [];
    const start = Math.max(0, lowerBound(items, startMs) - 1);
    const visible = [];
    for (let index = start; index < items.length && eventTime(items[index]) <= endMs; index += 1) {
      if (confidenceVisible(items[index]) && (items[index].endMs ?? eventTime(items[index])) >= startMs) visible.push(items[index]);
    }
    return visible;
  }

  function filteredDrums(startMs, endMs) {
    const active = visibleItems('drums', startMs, endMs).filter(item => {
      if (!document.querySelector(`[data-drum-lane="${drumLane(item)}"]`)?.checked) return false;
      if (!checked('#show-manual') && item.source === 'human') return false;
      if (checked('#reviewed-only') && drumReviewState(item) === 'UNREVIEWED') return false;
      if (checked('#unreviewed-only') && drumReviewState(item) !== 'UNREVIEWED') return false;
      return true;
    }).map(item => ({item, deleted: false}));
    if (!checked('#show-deleted') || stage !== 'reviewed') return active;
    const deleted = [];
    for (let index = lowerBound(deletedDrums, startMs); index < deletedDrums.length && eventTime(deletedDrums[index]) <= endMs; index += 1) {
      const item = deletedDrums[index];
      if (confidenceVisible(item) && document.querySelector(`[data-drum-lane="${drumLane(item)}"]`)?.checked) deleted.push({item, deleted: true});
    }
    return active.concat(deleted);
  }

  function drawDrumShape(item, piece, x, y, deleted = false) {
    const colors = {unassigned: '#99a3b5', kick: '#79f5ce', snare: '#ff9d9d', hi_hat: '#ffe28a', tom: '#6fa8ff', crash: '#ffb86c', splash: '#ff8fc8', ride: '#d38cff', cymbal: '#aeb7c8', unknown: '#eff2f8'};
    const radius = 4 + Math.max(0, Math.min(1, Number(item.intensity ?? 0.5))) * 7;
    context.save();
    context.strokeStyle = context.fillStyle = colors[piece];
    context.lineWidth = item.reviewedType || item.source === 'human' ? 2 : 1;
    if (!item.reviewedType && item.source !== 'human') context.setLineDash([3, 3]);
    context.beginPath();
    if (piece === 'kick') context.arc(x, y, radius, 0, Math.PI * 2);
    else if (piece === 'snare') { context.moveTo(x, y - radius); context.lineTo(x, y + radius); context.moveTo(x - 3, y - radius); context.lineTo(x - 3, y + radius); }
    else if (piece === 'hi_hat') { context.moveTo(x - radius, y - radius); context.lineTo(x + radius, y + radius); context.moveTo(x + radius, y - radius); context.lineTo(x - radius, y + radius); }
    else if (piece === 'tom') context.rect(x - radius, y - radius, radius * 2, radius * 2);
    else if (piece === 'crash') { context.arc(x, y, radius, 0, Math.PI * 2); context.moveTo(x - radius * 1.5, y); context.lineTo(x + radius * 1.5, y); }
    else if (piece === 'splash') { context.moveTo(x, y - radius); context.lineTo(x + radius, y + radius); context.lineTo(x - radius, y + radius); context.closePath(); }
    else if (piece === 'ride') { context.moveTo(x, y - radius); context.lineTo(x + radius, y); context.lineTo(x, y + radius); context.lineTo(x - radius, y); context.closePath(); }
    else if (piece === 'cymbal') context.arc(x, y + radius / 2, radius, Math.PI, 0);
    else if (piece === 'unknown') { context.stroke(); context.setLineDash([]); context.fillText('?', x - 3, y + 4); context.beginPath(); }
    else context.arc(x, y, radius, 0, Math.PI * 2);
    if (item.reviewedType || item.source === 'human') context.fill();
    context.stroke();
    context.setLineDash([]);
    if (item.source === 'human') { context.fillStyle = '#fff'; context.fillText('+', x + radius + 2, y - radius); }
    if (deleted) { context.strokeStyle = '#ff7777'; context.lineWidth = 2; context.beginPath(); context.moveTo(x - radius, y - radius); context.lineTo(x + radius, y + radius); context.moveTo(x + radius, y - radius); context.lineTo(x - radius, y + radius); context.stroke(); }
    context.restore();
    return radius;
  }

  function renderDrumLanes(windowData) {
    const visibleLanes = drumPieces.filter(piece => document.querySelector(`[data-drum-lane="${piece}"]`)?.checked !== false);
    const laneHeight = 56;
    const canvasHeight = Math.max(570, visibleLanes.length * laneHeight + 18);
    const {width, height} = resizeCanvas(canvasHeight);
    const plotLeft = 118;
    const plotWidth = width - plotLeft - 16;
    const xFor = time => plotLeft + ((time - windowData.startMs) / (windowData.endMs - windowData.startMs)) * plotWidth;
    renderWindow = {...windowData, plotLeft, plotWidth, laneHeight, visibleLanes, xFor};
    context.clearRect(0, 0, width, height);
    context.font = '12px system-ui';
    drawCache = [];
    visibleLanes.forEach((piece, row) => {
      const top = 9 + row * laneHeight;
      const center = top + laneHeight / 2;
      context.fillStyle = piece === 'unassigned' ? '#ffe28a' : '#99a3b5';
      context.fillText(drumLabels[piece], 8, center + 4);
      context.strokeStyle = '#2a3140';
      context.beginPath(); context.moveTo(plotLeft, top + laneHeight); context.lineTo(width, top + laneHeight); context.stroke();
    });
    const visibleDrums = filteredDrums(windowData.startMs, windowData.endMs);
    const showSuggestionText = plotWidth / Math.max(1, visibleDrums.length) > 55;
    visibleDrums.forEach(({item, deleted}) => {
      const piece = drumLane(item);
      const row = visibleLanes.indexOf(piece);
      if (row < 0) return;
      const x = xFor(item.timeMs);
      const y = 9 + row * laneHeight + laneHeight / 2;
      const radius = drawDrumShape(item, piece, x, y, deleted);
      const proposedType = automaticDrumType(item);
      const confidence = aiConfidence(item);
      if (checked('#show-ai-suggestions') && proposedType !== 'unassigned' && (selectedIds.has(item.id) || showSuggestionText)) {
        const confidenceText = confidence == null ? '' : ` ${Math.round(confidence * 100)}%`;
        context.fillStyle = '#99a3b5'; context.font = '10px system-ui'; context.fillText(`AI · ${proposedType}${confidenceText}`, x + radius + 3, y - 5); context.font = '12px system-ui';
      }
      const status = drumReviewState(item, deleted);
      const badge = status === 'UNREVIEWED' && proposedType !== 'unassigned' ? 'AI · UNREVIEWED' : ({CONFIRMED: '✓ CONFIRMED', OVERRIDDEN: 'H OVERRIDE', MANUAL: 'M MANUAL', DELETED: 'DELETED'}[status] || status);
      if (selectedIds.has(item.id) || showSuggestionText) { context.fillStyle = '#eff2f8'; context.font = '9px system-ui'; context.fillText(badge, x + radius + 3, y + 8); context.font = '12px system-ui'; }
      drawCache.push({item, channel: 'drums', row, lane: piece, deleted, x0: x - radius - 4, x1: x + radius + 4, y0: y - radius - 5, y1: y + radius + 5, x, y});
      if (selectedIds.has(item.id)) { context.strokeStyle = '#fff'; context.lineWidth = 2; context.strokeRect(x - radius - 5, y - radius - 5, radius * 2 + 10, radius * 2 + 10); context.lineWidth = 1; }
    });
    const playhead = xFor(windowData.nowMs);
    context.strokeStyle = '#fff'; context.lineWidth = 2; context.beginPath(); context.moveTo(playhead, 0); context.lineTo(playhead, height); context.stroke(); context.lineWidth = 1;
    if (marquee) { context.fillStyle = 'rgba(121,245,206,.12)'; context.strokeStyle = '#79f5ce'; const x = Math.min(marquee.x0, marquee.x1); const y = Math.min(marquee.y0, marquee.y1); const w = Math.abs(marquee.x1 - marquee.x0); const h = Math.abs(marquee.y1 - marquee.y0); context.fillRect(x, y, w, h); context.strokeRect(x, y, w, h); }
  }

  function drawLegacyDrum(item, x, y) {
    drawDrumShape(item, effectiveDrumType(item), x, y);
  }

  function renderClassic(windowData) {
    const {width, height} = resizeCanvas(570);
    const plotLeft = 90;
    const plotWidth = width - plotLeft - 16;
    const rowHeight = 86;
    const xFor = time => plotLeft + ((time - windowData.startMs) / (windowData.endMs - windowData.startMs)) * plotWidth;
    renderWindow = {...windowData, plotLeft, plotWidth, rowHeight, xFor};
    context.clearRect(0, 0, width, height); context.font = '12px system-ui'; drawCache = [];
    channels.forEach((channel, row) => {
      const top = 15 + row * rowHeight; const center = top + rowHeight / 2;
      context.fillStyle = channel === selectedChannel ? '#eff2f8' : '#99a3b5'; context.fillText(channel.toUpperCase(), 8, center + 4);
      context.strokeStyle = '#2a3140'; context.beginPath(); context.moveTo(plotLeft, top + rowHeight); context.lineTo(width, top + rowHeight); context.stroke();
      const items = visibleItems(channel, windowData.startMs, windowData.endMs);
      if (channel === 'drums') items.forEach(item => { const x = xFor(item.timeMs); drawLegacyDrum(item, x, center); drawCache.push({item, channel, row, x0: x - 8, x1: x + 8, y0: top, y1: top + rowHeight}); });
      else if (noteChannels.includes(channel)) items.forEach(item => { const x0 = xFor(item.startMs); const x1 = xFor(item.endMs); const midi = item.midi; const y = midi == null ? center : top + rowHeight - 10 - Math.max(0, Math.min(1, (midi - 24) / 84)) * (rowHeight - 20); context.fillStyle = midi == null ? '#697386' : channel === 'bass' ? '#6fa8ff' : channel === 'guitar' ? '#d38cff' : '#79f5ce'; context.fillRect(x0, y - 5, Math.max(2, x1 - x0), 10); drawCache.push({item, channel, row, x0, x1: Math.max(x0 + 3, x1), y0: y - 9, y1: y + 9}); });
      else if (channel === 'vocals') { [['presence', '#ff8fc8'], ['intensity', '#f5b879'], ['pitchNormalized', '#6fa8ff']].forEach(([key, color]) => { context.strokeStyle = color; context.beginPath(); items.forEach((item, index) => { const x = xFor(item.timeMs); const y = top + rowHeight - 8 - (item[key] || 0) * (rowHeight - 16); if (!index) context.moveTo(x, y); else context.lineTo(x, y); }); context.stroke(); }); items.forEach(item => { const x = xFor(item.timeMs); drawCache.push({item, channel, row, x0: x - 4, x1: x + 4, y0: top, y1: top + rowHeight}); }); }
      else { context.strokeStyle = '#f5b879'; context.beginPath(); items.forEach((item, index) => { const x = xFor(item.timeMs); const y = top + rowHeight - 8 - (item.overallEnergy || 0) * (rowHeight - 16); if (!index) context.moveTo(x, y); else context.lineTo(x, y); drawCache.push({item, channel, row, x0: x - 4, x1: x + 4, y0: top, y1: top + rowHeight}); }); context.stroke(); }
    });
    const playhead = xFor(windowData.nowMs); context.strokeStyle = '#fff'; context.lineWidth = 2; context.beginPath(); context.moveTo(playhead, 0); context.lineTo(playhead, height); context.stroke(); context.lineWidth = 1;
    drawCache.filter(entry => entry.item.id === selectedId).forEach(entry => { context.strokeStyle = '#fff'; context.strokeRect(entry.x0 - 3, entry.y0, Math.max(8, entry.x1 - entry.x0 + 6), entry.y1 - entry.y0); });
  }

  function render() {
    const windowData = timeWindow();
    const beforeLabel = document.querySelector('#window-before'); const afterLabel = document.querySelector('#window-after');
    if (beforeLabel) beforeLabel.textContent = `−${(windowData.beforeMs / 1000).toFixed(1)} s`;
    if (afterLabel) afterLabel.textContent = `+${(windowData.afterMs / 1000).toFixed(1)} s`;
    if (isDrumLaneMode()) renderDrumLanes(windowData); else renderClassic(windowData);
    const current = document.querySelector('#current-time'); const total = document.querySelector('#total-time');
    if (current) current.textContent = formatTime(audio.currentTime);
    if (total) total.textContent = formatTime(audio.duration || (config.durationMs || 0) / 1000);
    if (trackScrubber) {
      configureScrubber();
      if (!scrubbing && scrubberPreview) scrubberPreview.textContent = formatTime(audio.currentTime);
    }
    requestAnimationFrame(render);
  }

  function clearSelection() { selectedId = null; selectedObject = null; selectedIds.clear(); selectionAnchorTime = null; mergePartnerId = null; updatePanels(); }
  function selectedEvent() {
    if (selectedChannel === 'drums') {
      const id = selectedId || selectedIds.values().next().value;
      return (indices[stage].drums || []).find(item => item.id === id) || (indices.reviewed.drums || []).find(item => item.id === id) || deletedDrums.find(item => item.id === id);
    }
    return (indices[stage][selectedChannel] || []).find(item => selectedId && item.id === selectedId) || (indices.reviewed[selectedChannel] || []).find(item => selectedId && item.id === selectedId) || selectedObject;
  }
  function button(label, handler, className = 'secondary') { const element = document.createElement('button'); element.type = 'button'; element.className = `button ${className}`; element.textContent = label; element.addEventListener('click', handler); return element; }

  function renderInspector() {
    if (!inspector) return;
    const item = selectedEvent(); inspector.replaceChildren();
    if (!item) { inspector.textContent = editor ? 'Select an event or double-click a track to add one.' : 'Click an event to inspect it.'; return; }
    const view = selectedChannel === 'drums' ? {channel: 'drums', artifactStage: stage, reviewState: drumReviewState(item), lane: drumLane(item), effectiveType: effectiveDrumType(item), ...item} : {channel: selectedChannel, source: stage, quality: data.processed[selectedChannel]?.quality || null, ...item};
    const pre = document.createElement('pre'); pre.textContent = JSON.stringify(view, null, 2); inspector.append(pre);
    if (!editor || stage !== 'reviewed' || item.deleted) return;
    const actions = document.createElement('div'); actions.className = 'inspector-actions';
    if (selectedChannel === 'drums') {
      actions.append(button('▶ Audition Hit', auditionSelected));
      if (automaticDrumType(item) !== 'unassigned' && item.source !== 'human') actions.append(button('Confirm AI suggestion', confirmAutomaticSelected));
      const select = document.createElement('select'); drumPieces.forEach(type => select.add(new Option(drumLabels[type], type))); select.value = drumLane(item); select.addEventListener('change', () => assignSelected(select.value)); actions.append(select);
      actions.append(button('Delete selected', deleteSelected));
      [-50, -10, 10, 50].forEach(delta => actions.append(button(`${delta > 0 ? '+' : ''}${delta} ms`, () => saveAction('MOVE', {eventId: item.id, toMs: Math.round(item.timeMs + delta)}, 'drums'))));
    } else {
      actions.append(button('Delete', () => saveAction('DELETE', {eventId: item.id})), button('Confirm correct', () => saveAction('CONFIRM', {eventId: item.id})));
    }
    if (noteChannels.includes(selectedChannel)) {
      [-50, -10, 10, 50].forEach(delta => actions.append(button(`${delta > 0 ? '+' : ''}${delta} ms`, () => saveAction('MOVE', {eventId: item.id, toStartMs: Math.round(item.startMs + delta)}))));
      const midi = document.createElement('input'); midi.type = 'number'; midi.min = '0'; midi.max = '127'; midi.placeholder = 'MIDI or blank'; midi.value = item.midi ?? ''; actions.append(midi, button('Change pitch', () => saveAction('CHANGE_PITCH', {eventId: item.id, midi: midi.value === '' ? null : Number(midi.value)})));
      const end = document.createElement('input'); end.type = 'number'; end.value = item.endMs; actions.append(end, button('Resize end', () => saveAction('RESIZE', {eventId: item.id, toEndMs: Number(end.value)})), button('Split at playhead', () => saveAction('SPLIT', {eventId: item.id, splitMs: Math.round(audio.currentTime * 1000)})));
      if (mergePartnerId) actions.append(button('Merge selected pair', () => saveAction('MERGE', {eventId: item.id, eventIds: [item.id, mergePartnerId]})));
    }
    const intensity = document.createElement('input'); intensity.type = 'range'; intensity.min = '0'; intensity.max = '1'; intensity.step = '0.01'; intensity.value = item.intensity ?? 0.5; intensity.addEventListener('change', () => saveAction('CHANGE_INTENSITY', {eventId: item.id, to: Number(intensity.value)})); actions.append(intensity);
    inspector.append(actions);
  }

  async function jsonPost(url, body) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken()}, body: JSON.stringify(body)});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Save failed.');
    return payload;
  }

  function applyDrumAction(action) {
    const events = data.reviewed.drums.events;
    const index = events.findIndex(item => item.id === action.eventId);
    if (action.actionType === 'ADD') events.push(action.payload.event);
    else if (action.actionType === 'DELETE' && index >= 0) { const [removed] = events.splice(index, 1); deletedDrums.push({...removed, deleted: true, deletedByActionId: action.id}); deletedDrums.sort((a, b) => eventTime(a) - eventTime(b)); }
    else if ((action.actionType === 'ASSIGN_DRUM_PIECE' || action.actionType === 'RELABEL' || action.actionType === 'CONFIRM_DRUM_PIECE') && index >= 0) {
      events[index].reviewedType = action.payload.to === 'unassigned' ? null : action.payload.to;
      events[index].effectiveType = events[index].reviewedType || automaticDrumType(events[index]) || 'unknown';
      if (action.actionType === 'CONFIRM_DRUM_PIECE') {
        events[index].reviewMetadata = {...events[index].reviewMetadata, confirmedAutomaticByHuman: true};
        events[index].reviewStatus = 'CONFIRMED';
      } else if (events[index].reviewedType) {
        events[index].reviewStatus = events[index].reviewedType === automaticDrumType(events[index]) ? 'CONFIRMED' : 'OVERRIDDEN';
      } else {
        events[index].reviewStatus = 'UNREVIEWED';
      }
    }
    else if (action.actionType === 'MOVE' && index >= 0) events[index].timeMs = action.payload.toMs;
    else if (action.actionType === 'CHANGE_INTENSITY' && index >= 0) events[index].intensity = action.payload.to;
    else if (action.actionType === 'CONFIRM' && index >= 0) events[index].reviewMetadata = {...events[index].reviewMetadata, confirmedByHuman: true};
    events.sort((a, b) => a.timeMs - b.timeMs);
  }

  async function saveAction(actionType, payload, channel = selectedChannel) {
    if (!editor) return false;
    setSaveStatus('Saving…', 'saving');
    try {
      const response = await jsonPost(config.review.actionsUrl, {jobId: config.jobId, version: reviewVersion, channel, actionType, payload});
      reviewVersion = response.sessionVersion;
      if (channel === 'drums' && ['ADD', 'DELETE', 'ASSIGN_DRUM_PIECE', 'CONFIRM_DRUM_PIECE', 'RELABEL', 'MOVE', 'CHANGE_INTENSITY', 'CONFIRM'].includes(actionType)) {
        applyDrumAction(response.action); rebuildIndices('reviewed'); updatePanels(); renderInspector();
        if (actionType === 'ADD') { selectedIds = new Set([response.action.eventId]); selectedId = response.action.eventId; }
        if (actionType === 'DELETE') { selectedIds.delete(response.action.eventId); selectedId = selectedIds.values().next().value || null; }
      } else await loadReviewed();
      setSaveStatus('Saved', 'saved');
      return true;
    } catch (error) { setSaveStatus(`Error saving: ${error.message}`, 'error'); return false; }
  }

  async function saveBatch(actionSpecs) {
    if (!editor || !actionSpecs.length) return false;
    setSaveStatus(`Saving ${actionSpecs.length} actions…`, 'saving');
    try {
      const response = await jsonPost(config.review.batchUrl, {jobId: config.jobId, version: reviewVersion, actions: actionSpecs});
      reviewVersion = response.sessionVersion;
      response.actions.forEach(applyDrumAction);
      rebuildIndices('reviewed'); updatePanels(); renderInspector(); setSaveStatus('Saved', 'saved');
      return true;
    } catch (error) { setSaveStatus(`Error saving: ${error.message}`, 'error'); return false; }
  }

  async function cursor(direction) {
    try { const response = await jsonPost(config.review[`${direction}Url`], {version: reviewVersion}); reviewVersion = response.sessionVersion; clearSelection(); await loadReviewed(); setSaveStatus('Saved', 'saved'); }
    catch (error) { setSaveStatus(error.message, 'error'); }
  }

  function activeSelectedDrumIds() {
    const active = new Set((indices.reviewed.drums || []).map(item => item.id));
    return [...selectedIds].filter(id => active.has(id));
  }

  async function assignSelected(piece) {
    const ids = activeSelectedDrumIds();
    if (!ids.length || stage !== 'reviewed') return false;
    const lastTime = Math.max(...ids.map(id => (indices.reviewed.drums || []).find(item => item.id === id)?.timeMs || 0));
    const actions = ids.map(id => ({channel: 'drums', actionType: 'ASSIGN_DRUM_PIECE', payload: {eventId: id, to: piece}}));
    const saved = actions.length === 1 ? await saveAction('ASSIGN_DRUM_PIECE', actions[0].payload, 'drums') : await saveBatch(actions);
    if (saved && checked('#rapid-drum-review')) navigateUnreviewed(1, lastTime, true);
    return saved;
  }

  async function confirmAutomaticSelected() {
    const ids = activeSelectedDrumIds().filter(id => {
      const item = (indices.reviewed.drums || []).find(event => event.id === id);
      return item && item.source !== 'human' && automaticDrumType(item) !== 'unassigned';
    });
    if (!ids.length || stage !== 'reviewed') return false;
    const lastTime = Math.max(...ids.map(id => (indices.reviewed.drums || []).find(item => item.id === id)?.timeMs || 0));
    const actions = ids.map(id => ({channel: 'drums', actionType: 'CONFIRM_DRUM_PIECE', payload: {eventId: id}}));
    const saved = actions.length === 1 ? await saveAction('CONFIRM_DRUM_PIECE', actions[0].payload, 'drums') : await saveBatch(actions);
    if (saved && checked('#rapid-drum-review')) navigateUnreviewed(1, lastTime, true);
    return saved;
  }

  async function deleteSelected() {
    const ids = activeSelectedDrumIds();
    if (!ids.length || stage !== 'reviewed') return;
    if (ids.length > 50 && !window.confirm(`Delete ${ids.length} selected drum events from REVIEWED?`)) return;
    const actions = ids.map(id => ({channel: 'drums', actionType: 'DELETE', payload: {eventId: id}}));
    const saved = actions.length === 1 ? await saveAction('DELETE', actions[0].payload, 'drums') : await saveBatch(actions);
    if (saved) { clearSelection(); if (checked('#rapid-drum-review')) navigateUnreviewed(1, audio.currentTime * 1000, true); }
  }

  function addDrum(piece, timeMs = Math.round(audio.currentTime * 1000)) {
    selectedChannel = 'drums'; if (channelSelect) channelSelect.value = 'drums';
    stage = 'reviewed'; document.querySelector('input[value="reviewed"]')?.click(); updateEditorMode();
    saveAction('ADD', {event: {timeMs: Math.round(timeMs), reviewedType: piece, durationMs: 80, intensity: 0.5}}, 'drums');
  }
  function addNote(channel, timeMs) { const value = window.prompt(`MIDI note for ${channel} (blank for unknown pitch)`, channel === 'bass' ? '40' : '60'); if (value === null) return; saveAction('ADD', {event: {startMs: Math.round(timeMs), endMs: Math.round(timeMs + 250), midi: value === '' ? null : Number(value), intensity: 0.5}}, channel); }

  async function restoreAfterAudition(state) {
    if (!state || auditionState !== state) return;
    auditionState = null;
    audio.pause();
    audio.removeEventListener('timeupdate', state.stopListener);
    if (sourceSelect.value !== state.sourceUrl) await switchAudioSource(state.sourceUrl, state);
    else { audio.currentTime = Math.min(state.time, audio.duration || state.time); audio.playbackRate = state.rate; if (!state.paused) { try { await audio.play(); } catch (_) {} } }
  }

  async function auditionSelected() {
    const item = selectedEvent();
    if (!item || selectedChannel !== 'drums' || item.deleted) return;
    if (auditionState) await restoreAfterAudition(auditionState);
    const drumsSource = config.audioSources.find(source => source.key === 'drums');
    if (!drumsSource) { setSaveStatus('The drums.wav source is unavailable.', 'error'); return; }
    const state = {sourceUrl: sourceSelect.value, time: audio.currentTime, paused: audio.paused, rate: audio.playbackRate};
    auditionState = state;
    if (sourceSelect.value !== drumsSource.url) { audio.pause(); sourceSelect.value = drumsSource.url; audio.src = drumsSource.url; try { await waitForMetadata(); } catch (_) { auditionState = null; return; } }
    const before = Math.max(0, Number(auditionBefore?.value || 150));
    const after = Math.max(10, Number(auditionAfter?.value || 350));
    const start = Math.max(0, item.timeMs - before) / 1000;
    const end = Math.min(config.durationMs || Infinity, item.timeMs + after) / 1000;
    audio.playbackRate = 1;
    audio.currentTime = start;
    state.stopListener = () => { if (audio.currentTime >= end) restoreAfterAudition(state); };
    audio.addEventListener('timeupdate', state.stopListener);
    try { await audio.play(); } catch (error) { await restoreAfterAudition(state); setSaveStatus(`Audition failed: ${error.message}`, 'error'); }
  }

  function selectDrum(item, options = {}) {
    if (!item?.id) return;
    if (options.toggle) {
      if (selectedIds.has(item.id)) selectedIds.delete(item.id); else selectedIds.add(item.id);
    } else if (options.range && selectionAnchorTime != null) {
      const low = Math.min(selectionAnchorTime, item.timeMs); const high = Math.max(selectionAnchorTime, item.timeMs);
      (indices.reviewed.drums || []).filter(event => event.timeMs >= low && event.timeMs <= high).forEach(event => selectedIds.add(event.id));
    } else selectedIds = new Set([item.id]);
    selectedId = item.id;
    selectionAnchorTime = item.timeMs;
    updatePanels(); renderInspector();
    if (checked('#auto-audition')) auditionSelected();
  }

  function navigateUnreviewed(direction, fromMs = audio.currentTime * 1000, audition = false) {
    const items = (indices.reviewed.drums || []).filter(item => drumReviewState(item) === 'UNREVIEWED');
    if (!items.length) return;
    let target;
    if (direction > 0) target = items.find(item => item.timeMs > fromMs + 1) || items[0];
    else target = [...items].reverse().find(item => item.timeMs < fromMs - 1) || items[items.length - 1];
    audio.currentTime = target.timeMs / 1000;
    selectDrum(target);
    if (audition && !checked('#auto-audition')) auditionSelected();
  }

  function findPointerHit(x, y) {
    const hits = drawCache.filter(entry => x >= entry.x0 - 4 && x <= entry.x1 + 4 && y >= entry.y0 - 4 && y <= entry.y1 + 4);
    return hits.sort((a, b) => Math.hypot(a.x - x, a.y - y) - Math.hypot(b.x - x, b.y - y))[0] || null;
  }

  canvas.addEventListener('pointerdown', event => {
    if (!editor || stage !== 'reviewed' || event.button !== 0) return;
    const rect = canvas.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const hit = findPointerHit(x, y);
    if (!hit && !event.altKey) {
      pointerState = {mode: 'pan', x0: x, startTime: audio.currentTime};
      canvas.classList.add('timeline-panning');
      canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
      return;
    }
    if (!isDrumLaneMode()) return;
    pointerState = {x0: x, y0: y, x, y, hit, shift: event.shiftKey, toggle: event.ctrlKey || event.metaKey, moved: false};
    if (!hit && !pointerState.toggle) { selectedIds.clear(); selectedId = null; }
    if (!hit) marquee = {x0: x, y0: y, x1: x, y1: y};
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (!pointerState) return;
    if (pointerState.mode === 'pan') {
      const rect = canvas.getBoundingClientRect(); const x = event.clientX - rect.left;
      const millisecondsPerPixel = (renderWindow.endMs - renderWindow.startMs) / renderWindow.plotWidth;
      const duration = audio.duration || Number(config.durationMs || 0) / 1000;
      audio.currentTime = Math.max(0, Math.min(duration || Infinity, pointerState.startTime - ((x - pointerState.x0) * millisecondsPerPixel) / 1000));
      event.preventDefault();
      return;
    }
    const rect = canvas.getBoundingClientRect(); pointerState.x = event.clientX - rect.left; pointerState.y = event.clientY - rect.top;
    pointerState.moved = pointerState.moved || Math.hypot(pointerState.x - pointerState.x0, pointerState.y - pointerState.y0) > 5;
    if (marquee) { marquee.x1 = pointerState.x; marquee.y1 = pointerState.y; }
  });
  canvas.addEventListener('pointerup', event => {
    if (!pointerState) return;
    const state = pointerState; pointerState = null;
    if (state.mode === 'pan') {
      canvas.classList.remove('timeline-panning');
      try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
      return;
    }
    if (state.hit) {
      if (state.moved && !state.hit.deleted) {
        if (!selectedIds.has(state.hit.item.id)) selectDrum(state.hit.item);
        if (state.shift) {
          const deltaMs = ((state.x - state.x0) / renderWindow.plotWidth) * (renderWindow.endMs - renderWindow.startMs);
          saveAction('MOVE', {eventId: state.hit.item.id, toMs: Math.round(state.hit.item.timeMs + deltaMs)}, 'drums');
        } else {
          const row = Math.floor((state.y - 9) / renderWindow.laneHeight);
          const target = renderWindow.visibleLanes[row];
          if (target && target !== drumLane(state.hit.item)) assignSelected(target);
        }
      } else selectDrum(state.hit.item, {toggle: state.toggle, range: state.shift});
    } else if (marquee) {
      const x0 = Math.min(marquee.x0, marquee.x1); const x1 = Math.max(marquee.x0, marquee.x1); const y0 = Math.min(marquee.y0, marquee.y1); const y1 = Math.max(marquee.y0, marquee.y1);
      drawCache.filter(entry => !entry.deleted && entry.x >= x0 && entry.x <= x1 && entry.y >= y0 && entry.y <= y1).forEach(entry => selectedIds.add(entry.item.id));
      selectedId = selectedIds.values().next().value || null; updatePanels(); renderInspector(); marquee = null;
    }
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
  });
  canvas.addEventListener('pointercancel', () => { pointerState = null; marquee = null; canvas.classList.remove('timeline-panning'); });

  canvas.addEventListener('click', event => {
    if (isDrumLaneMode()) return;
    const rect = canvas.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const row = Math.floor((y - 15) / 86);
    if (row < 0 || row >= channels.length) return;
    const previousChannel = selectedChannel; const previousId = selectedId; selectedChannel = channels[row]; if (channelSelect) channelSelect.value = selectedChannel;
    const candidates = drawCache.filter(item => item.row === row && x >= item.x0 - 5 && x <= item.x1 + 5);
    if (candidates.length) { const selected = candidates.reduce((best, item) => Math.abs((item.x0 + item.x1) / 2 - x) < Math.abs((best.x0 + best.x1) / 2 - x) ? item : best); if (event.shiftKey && previousId && previousChannel === selected.channel) mergePartnerId = previousId; else mergePartnerId = null; selectedId = selected.item.id || null; selectedObject = selected.item; renderInspector(); }
    updateEditorMode();
  });
  canvas.addEventListener('dblclick', event => {
    if (!editor) return;
    const rect = canvas.getBoundingClientRect(); const x = event.clientX - rect.left;
    const time = renderWindow.startMs + ((x - renderWindow.plotLeft) / renderWindow.plotWidth) * (renderWindow.endMs - renderWindow.startMs);
    if (isDrumLaneMode()) addDrum('unknown', time);
    else { const y = event.clientY - rect.top; const row = Math.floor((y - 15) / 86); if (row >= 0 && row < channels.length && noteChannels.includes(channels[row])) addNote(channels[row], time); }
  });

  document.querySelectorAll('[data-assign-drum]').forEach(element => element.addEventListener('click', () => assignSelected(element.dataset.assignDrum)));
  document.querySelector('#delete-selected')?.addEventListener('click', deleteSelected);
  document.querySelector('#confirm-ai-suggestion')?.addEventListener('click', confirmAutomaticSelected);
  document.querySelector('#audition-hit')?.addEventListener('click', auditionSelected);
  document.querySelector('#add-drum-event')?.addEventListener('click', () => addDrum(document.querySelector('#add-drum-type').value));
  document.querySelector('#review-undo')?.addEventListener('click', () => cursor('undo'));
  document.querySelector('#review-redo')?.addEventListener('click', () => cursor('redo'));
  document.querySelector('#previous-unreviewed')?.addEventListener('click', () => navigateUnreviewed(-1));
  document.querySelector('#next-unreviewed')?.addEventListener('click', () => navigateUnreviewed(1));
  document.querySelector('#rapid-drum-review')?.addEventListener('change', event => { if (event.target.checked) navigateUnreviewed(1, audio.currentTime * 1000 - 2, true); });

  function navigate(direction) {
    const items = indices[stage][selectedChannel] || []; const now = audio.currentTime * 1000; const index = lowerBound(items, now);
    const target = direction < 0 ? items[Math.max(0, index - 1)] : items[Math.min(items.length - 1, index + (eventTime(items[index]) <= now ? 1 : 0))];
    if (target) { audio.currentTime = eventTime(target) / 1000; if (selectedChannel === 'drums') selectDrum(target); else { selectedId = target.id || null; selectedObject = target; renderInspector(); } }
  }
  document.querySelector('#previous-event')?.addEventListener('click', () => navigate(-1));
  document.querySelector('#next-event')?.addEventListener('click', () => navigate(1));
  document.querySelectorAll('[data-range]').forEach(element => element.addEventListener('click', () => { const mode = element.dataset.range; const channel = ['voice_active', 'silence', 'suspicious'].includes(mode) ? 'vocals' : 'other'; const start = Math.round(audio.currentTime * 1000); saveAction('MARK_RANGE', {startMs: start, endMs: Math.min(config.durationMs, start + 1000), mode}, channel); }));

  document.querySelector('#finish-review')?.addEventListener('click', async () => {
    try {
      const response = await fetch(config.review.summaryUrl); const summary = await response.json();
      document.querySelector('#finish-summary').textContent = JSON.stringify(summary, null, 2);
      const warning = summary.drumReview?.unassigned ? `\nWARNING: ${summary.drumReview.unassigned} drum events remain unassigned.\n` : '';
      if (!window.confirm(`Generate human-reviewed Teleo Experience?${warning}\n${JSON.stringify(summary.summary, null, 2)}`)) return;
      const result = await jsonPost(config.review.finishUrl, {version: reviewVersion, confirm: true}); reviewVersion = result.sessionVersion; document.querySelector('#review-status').textContent = 'Completed'; window.alert(`Review completed.\n${result.reviewedExperienceUrl}`);
    } catch (error) { window.alert(error.message); }
  });

  async function togglePlayback() {
    if (audio.paused) {
      try { await audio.play(); } catch (error) { setSaveStatus(`Playback failed: ${error.message}`, 'error'); }
    } else {
      audio.pause();
    }
  }

  document.addEventListener('keydown', event => {
    if (!editor) return;
    if (event.code === 'Space' || event.key === ' ') {
      event.preventDefault();
      if (!event.repeat) togglePlayback();
      return;
    }
    const tag = event.target.tagName;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || event.target.isContentEditable) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); cursor('undo'); return; }
    if ((event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'y' || (event.shiftKey && event.key.toLowerCase() === 'z'))) { event.preventDefault(); cursor('redo'); return; }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); const delta = (event.shiftKey ? 1 : 0.1) * (event.key === 'ArrowLeft' ? -1 : 1); audio.currentTime = Math.max(0, Math.min(audio.duration || Infinity, audio.currentTime + delta)); return; }
    if (event.key === '[') { event.preventDefault(); navigateUnreviewed(-1); return; }
    if (event.key === ']') { event.preventDefault(); navigateUnreviewed(1); return; }
    if (event.key.toLowerCase() === 'a' && selectedIds.size) { event.preventDefault(); auditionSelected(); return; }
    if (event.key === 'Delete' && selectedIds.size) { event.preventDefault(); deleteSelected(); return; }
    if (!selectedIds.size || selectedChannel !== 'drums') return;
    if (event.key === 'Enter') { event.preventDefault(); confirmAutomaticSelected(); return; }
    const assignments = {k: 'kick', s: 'snare', h: 'hi_hat', t: 'tom', c: 'crash', p: 'splash', r: 'ride', y: 'cymbal', u: 'unassigned', n: 'unknown'};
    const piece = assignments[event.key.toLowerCase()];
    if (piece) { event.preventDefault(); assignSelected(piece); }
  }, {capture: true});

  const artifactLoads = Object.entries(config.artifacts).flatMap(([artifactStage, urls]) => Object.entries(urls).map(async ([channel, url]) => { const response = await fetch(url); if (!response.ok) throw new Error(`Could not load ${artifactStage}/${channel}`); data[artifactStage][channel] = await response.json(); }));
  Promise.all(artifactLoads).then(async () => { rebuildIndices('raw'); rebuildIndices('processed'); if (editor) await loadReviewed(); else updatePanels(); updateEditorMode(); }).catch(error => { counts.textContent = error.message; });
  window.addEventListener('resize', () => resizeCanvas(isDrumLaneMode() ? Math.max(570, drumPieces.length * 56 + 18) : 570));
  requestAnimationFrame(render);
})();

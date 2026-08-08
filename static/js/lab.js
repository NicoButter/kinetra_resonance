(() => {
  const config = JSON.parse(document.querySelector('#lab-config').textContent);
  const audio = document.querySelector('#lab-audio');
  const sourceSelect = document.querySelector('#audio-source');
  const canvas = document.querySelector('#analysis-canvas');
  const context = canvas.getContext('2d');
  const confidenceInput = document.querySelector('#confidence-filter');
  const confidenceValue = document.querySelector('#confidence-value');
  const counts = document.querySelector('#lab-counts');
  const inspector = document.querySelector('#event-inspector');
  const qualityPanel = document.querySelector('#quality-panel');
  const channels = ['drums', 'bass', 'guitar', 'piano', 'vocals', 'other'];
  const data = {raw: {}, processed: {}};
  const indices = {raw: {}, processed: {}};
  let stage = 'processed';
  let minimumConfidence = 0;
  let drawCache = [];

  const collectionFor = payload => payload?.events || payload?.notes || payload?.frames || [];
  const eventTime = item => item.timeMs ?? item.startMs ?? 0;
  const confidence = item => item.confidence ?? item.pitchConfidence ?? 1;
  const lowerBound = (array, value) => { let low = 0, high = array.length; while (low < high) { const mid = (low + high) >> 1; if (eventTime(array[mid]) < value) low = mid + 1; else high = mid; } return low; };

  config.audioSources.forEach(source => sourceSelect.add(new Option(source.label, source.url)));
  audio.src = config.audioSources[0]?.url || '';
  sourceSelect.addEventListener('change', () => {
    const position = audio.currentTime;
    const wasPlaying = !audio.paused;
    audio.src = sourceSelect.value;
    audio.addEventListener('loadedmetadata', () => { audio.currentTime = Math.min(position, audio.duration || position); if (wasPlaying) audio.play(); }, {once: true});
  });

  document.querySelectorAll('input[name="artifact-stage"]').forEach(input => input.addEventListener('change', event => { stage = event.target.value; updatePanels(); }));
  confidenceInput.addEventListener('input', () => { minimumConfidence = Number(confidenceInput.value); confidenceValue.value = minimumConfidence.toFixed(2); });

  function updatePanels() {
    const rawCount = channels.reduce((sum, channel) => sum + (indices.raw[channel]?.length || 0), 0);
    const processedCount = channels.reduce((sum, channel) => sum + (indices.processed[channel]?.length || 0), 0);
    counts.textContent = `Raw events: ${rawCount} · Processed events: ${processedCount}`;
    qualityPanel.innerHTML = channels.map(channel => {
      const quality = data.processed[channel]?.quality;
      if (!quality) return `<div class="quality-row"><strong>${channel}</strong><span>pending</span></div>`;
      return `<div class="quality-row quality-${quality.status}"><strong>${channel}</strong><span>${quality.status} · ${quality.score}</span><small>${(quality.warnings || []).join(' ') || 'No warnings'}</small><details><summary>Metrics</summary><pre>${JSON.stringify(quality.metrics, null, 2)}</pre></details></div>`;
    }).join('');
  }

  function resizeCanvas() {
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = 570;
    if (canvas.width !== width * ratio || canvas.height !== height * ratio) { canvas.width = width * ratio; canvas.height = height * ratio; context.setTransform(ratio, 0, 0, ratio, 0, 0); }
    return {width, height};
  }

  function visibleItems(channel, startMs, endMs) {
    const items = indices[stage][channel] || [];
    const start = Math.max(0, lowerBound(items, startMs) - 1);
    const visible = [];
    for (let index = start; index < items.length && eventTime(items[index]) <= endMs; index++) if (confidence(items[index]) >= minimumConfidence && (items[index].endMs ?? eventTime(items[index])) >= startMs) visible.push(items[index]);
    return visible;
  }

  function drawDrum(item, x, y) {
    context.strokeStyle = context.fillStyle = item.type === 'kick' ? '#79f5ce' : item.type === 'snare' ? '#ff9d9d' : item.type === 'hi_hat' ? '#ffe28a' : '#aeb7c8';
    if (item.type === 'kick') { context.beginPath(); context.arc(x, y, 6, 0, Math.PI * 2); context.fill(); }
    else if (item.type === 'snare') { context.beginPath(); context.moveTo(x, y - 12); context.lineTo(x, y + 12); context.stroke(); }
    else if (item.type === 'hi_hat') context.fillRect(x - 2, y - 2, 4, 4);
    else context.fillText('?', x - 3, y + 4);
  }

  function render() {
    const {width, height} = resizeCanvas();
    const nowMs = (audio.currentTime || 0) * 1000;
    const startMs = nowMs - config.windowBeforeMs;
    const endMs = nowMs + config.windowAfterMs;
    const plotLeft = 90, plotWidth = width - plotLeft - 16, rowHeight = 86;
    const xFor = time => plotLeft + ((time - startMs) / (endMs - startMs)) * plotWidth;
    context.clearRect(0, 0, width, height);
    context.font = '12px system-ui';
    drawCache = [];
    channels.forEach((channel, row) => {
      const top = 15 + row * rowHeight, center = top + rowHeight / 2;
      context.fillStyle = '#99a3b5'; context.fillText(channel.toUpperCase(), 8, center + 4);
      context.strokeStyle = '#2a3140'; context.beginPath(); context.moveTo(plotLeft, top + rowHeight); context.lineTo(width, top + rowHeight); context.stroke();
      const items = visibleItems(channel, startMs, endMs);
      if (channel === 'drums') items.forEach(item => { const x = xFor(item.timeMs); drawDrum(item, x, center); drawCache.push({item, channel, row, x0: x - 7, x1: x + 7}); });
      else if (['bass','guitar','piano'].includes(channel)) items.forEach(item => { const x0 = xFor(item.startMs), x1 = xFor(item.endMs); const midi = item.midi; const y = midi == null ? center : top + rowHeight - 10 - Math.max(0, Math.min(1, (midi - 24) / 84)) * (rowHeight - 20); context.fillStyle = midi == null ? '#697386' : channel === 'bass' ? '#6fa8ff' : channel === 'guitar' ? '#d38cff' : '#79f5ce'; context.fillRect(x0, y - 5, Math.max(2, x1 - x0), 10); drawCache.push({item, channel, row, x0, x1: Math.max(x0 + 3, x1)}); });
      else if (channel === 'vocals') { [['presence','#ff8fc8'],['intensity','#f5b879'],['pitchNormalized','#6fa8ff']].forEach(([key,color]) => { context.strokeStyle=color; context.beginPath(); items.forEach((item,index) => { const x=xFor(item.timeMs), y=top+rowHeight-8-(item[key]||0)*(rowHeight-16); if(!index) context.moveTo(x,y); else context.lineTo(x,y); }); context.stroke(); }); items.forEach(item => { const x=xFor(item.timeMs); drawCache.push({item,channel,row,x0:x-4,x1:x+4}); }); }
      else { context.strokeStyle = '#f5b879'; context.beginPath(); items.forEach((item, index) => { const x = xFor(item.timeMs), y = top + rowHeight - 8 - (item.overallEnergy || 0) * (rowHeight - 16); if (!index) context.moveTo(x, y); else context.lineTo(x, y); drawCache.push({item, channel, row, x0: x - 4, x1: x + 4}); }); context.stroke(); }
    });
    const playhead = xFor(nowMs); context.strokeStyle = '#fff'; context.lineWidth = 2; context.beginPath(); context.moveTo(playhead, 0); context.lineTo(playhead, height); context.stroke(); context.lineWidth = 1;
    requestAnimationFrame(render);
  }

  canvas.addEventListener('click', event => {
    const rect = canvas.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const row = Math.floor((y - 15) / 86);
    const candidates = drawCache.filter(item => item.row === row && x >= item.x0 - 5 && x <= item.x1 + 5);
    if (candidates.length) { const selected = candidates.reduce((best, item) => Math.abs((item.x0 + item.x1) / 2 - x) < Math.abs((best.x0 + best.x1) / 2 - x) ? item : best); inspector.textContent = JSON.stringify({channel: selected.channel, quality: data.processed[selected.channel]?.quality || null, ...selected.item}, null, 2); }
  });

  Promise.all(Object.entries(config.artifacts).flatMap(([artifactStage, urls]) => Object.entries(urls).map(async ([channel, url]) => { const response = await fetch(url); if (!response.ok) throw new Error(`Could not load ${artifactStage}/${channel}`); data[artifactStage][channel] = await response.json(); indices[artifactStage][channel] = collectionFor(data[artifactStage][channel]).sort((a,b) => eventTime(a) - eventTime(b)); }))).then(updatePanels).catch(error => { counts.textContent = error.message; });
  window.addEventListener('resize', resizeCanvas);
  requestAnimationFrame(render);
})();

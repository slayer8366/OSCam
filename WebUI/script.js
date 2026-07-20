// SECTION: State & utilities
const state = {
  isLive: false,
  lastFrameTime: 0,
  frameIntervalMs: 1000 / 20,
  automation: {
    mode: 'off',
    timerId: null,
    framesCaptured: 0,
    targetFrames: 0,
    seqSteps: [],
    seqIndex: 0,
    running: false,
  },
  metrics: {
    mean: 0,
    max: 0,
    min: 0,
    profileSummary: '-',
  },
  charts: {
    histogram: null,
    intensity: null,
    profile: null,
  },
  intensityHistory: [],
};

const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

// SECTION: DOM references
const liveCanvas = document.getElementById('liveCanvas');
const liveCtx = liveCanvas.getContext('2d');

const btnLive = document.getElementById('btnLive');
const btnCapture = document.getElementById('btnCapture');
const btnRecord = document.getElementById('btnRecord');
const btnReset = document.getElementById('btnReset');
const btnSimulateSignal = document.getElementById('btnSimulateSignal');

const captureModeEl = document.getElementById('captureMode');
const exposureMsEl = document.getElementById('exposureMs');
const gainEl = document.getElementById('gain');
const frameRateEl = document.getElementById('frameRate');
const frameRateLabelEl = document.getElementById('frameRateLabel');

const metricMeanEl = document.getElementById('metricMean');
const metricMaxEl = document.getElementById('metricMax');
const metricMinEl = document.getElementById('metricMin');
const metricProfileEl = document.getElementById('metricProfile');
const liveInfoEl = document.getElementById('liveInfo');

const automationModeEl = document.getElementById('automationMode');
const timelapseConfigEl = document.getElementById('timelapseConfig');
const sequenceConfigEl = document.getElementById('sequenceConfig');
const tlIntervalEl = document.getElementById('tlInterval');
const tlFramesEl = document.getElementById('tlFrames');
const seqStartExpEl = document.getElementById('seqStartExp');
const seqEndExpEl = document.getElementById('seqEndExp');
const seqStepsEl = document.getElementById('seqSteps');
const seqRepeatEl = document.getElementById('seqRepeat');
const btnAutoStart = document.getElementById('btnAutoStart');
const btnAutoStop = document.getElementById('btnAutoStop');
const automationStatusEl = document.getElementById('automationStatus');

const logEl = document.getElementById('log');
const btnClearLog = document.getElementById('btnClearLog');

// SECTION: Logging
function log(message, type = 'user') {
  if (!logEl) return;
  const entry = document.createElement('div');
  entry.className = 'log__entry' + (type === 'system' ? ' log__entry--system' : '');
  const now = new Date();
  const t = now.toLocaleTimeString('en-US', { hour12: false });
  entry.textContent = `[${t}] ${message}`;
  logEl.appendChild(entry);
  logEl.scrollTop = logEl.scrollHeight;
}

// SECTION: Live view simulation
function generateSyntheticFrame(timeMs) {
  const { width, height } = liveCanvas;
  const imageData = liveCtx.createImageData(width, height);
  const data = imageData.data;

  const exposure = parseFloat(exposureMsEl.value) || 10;
  const gain = parseFloat(gainEl.value) || 0;

  const exposureNorm = clamp(Math.log10(exposure + 1) / 4, 0, 1);
  const gainNorm = clamp(gain / 24, 0, 1);

  let sum = 0;
  let min = 255;
  let max = 0;

  const t = timeMs / 1000;
  const freq = 0.2;
  const breathing = 0.5 + 0.5 * Math.sin(2 * Math.PI * freq * t);

  const basePhase = Math.sin(t * 0.7) * 3;

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;

      const nx = (x - width / 2) / (width / 2);
      const ny = (y - height / 2) / (height / 2);
      const r2 = nx * nx + ny * ny;

      const ring = Math.exp(-r2 * 2) * (0.5 + 0.5 * Math.sin(10 * Math.sqrt(r2) - basePhase));

      const stripe = 0.5 + 0.5 * Math.sin((x / width) * 50 + t * 3);

      const hotspotX = 0.3 + 0.2 * Math.sin(t * 0.8);
      const hotspotY = -0.1 + 0.15 * Math.cos(t * 0.6);
      const dx = nx - hotspotX;
      const dy = ny - hotspotY;
      const hotspot = Math.exp(-(dx * dx + dy * dy) * 18);

      const breathingMod = 0.4 + 0.6 * breathing;
      let val = 0.25 * ring + 0.35 * stripe + 0.5 * hotspot * breathingMod;

      val *= 0.6 + 0.8 * exposureNorm + 0.5 * gainNorm;

      const noiseAmp = 0.08 + 0.25 * exposureNorm + 0.3 * gainNorm;
      const noise = (Math.random() - 0.5) * 2 * noiseAmp;
      val += noise;

      val = clamp(val, 0, 1);
      let gray = Math.round(val * 255);

      gray = clamp(gray, 0, 255);
      sum += gray;
      if (gray < min) min = gray;
      if (gray > max) max = gray;

      data[idx] = gray;
      data[idx + 1] = gray;
      data[idx + 2] = gray;
      data[idx + 3] = 255;
    }
  }

  state.metrics.mean = sum / (width * height);
  state.metrics.min = min;
  state.metrics.max = max;

  const centerY = Math.floor(height / 2);
  let rising = 0;
  let falling = 0;
  let last = data[centerY * width * 4];
  for (let x = 1; x < width; x++) {
    const g = data[(centerY * width + x) * 4];
    if (g > last) rising++;
    if (g < last) falling++;
    last = g;
  }
  state.metrics.profileSummary = `${rising}↑ / ${falling}↓`;

  liveCtx.putImageData(imageData, 0, 0);

  updateChartsFromFrame(imageData);
}

function updateMetricDisplay() {
  metricMeanEl.textContent = state.metrics.mean.toFixed(1);
  metricMaxEl.textContent = state.metrics.max.toFixed(0);
  metricMinEl.textContent = state.metrics.min.toFixed(0);
  metricProfileEl.textContent = state.metrics.profileSummary;
}

// SECTION: Charts
function initCharts() {
  // Guard if Chart.js is not available in this environment
  if (typeof Chart === 'undefined') {
    log('Chart.js not loaded; analysis charts are disabled in this preview.', 'system');
    return;
  }
  const histogramCtx = document.getElementById('histogramChart');
  const intensityCtx = document.getElementById('intensityChart');
  const profileCtx = document.getElementById('profileChart');

  if (!histogramCtx || !intensityCtx || !profileCtx) return;

  const commonOptions = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: {
      legend: { display: false },
      tooltip: { enabled: false },
    },
    scales: {
      x: {
        grid: { color: 'rgba(148, 163, 184, 0.15)' },
        ticks: { color: '#9ca3af', maxTicksLimit: 6, font: { size: 10 } },
      },
      y: {
        grid: { color: 'rgba(148, 163, 184, 0.15)' },
        ticks: { color: '#9ca3af', maxTicksLimit: 4, font: { size: 10 } },
      },
    },
  };

  // Alias for Chart to satisfy static analyzers without assuming global
  // Use a loose global lookup so static analyzers do not require Chart
  // eslint-disable-next-line no-undef
  const ChartRef = typeof window !== 'undefined' && window.Chart ? window.Chart : null;

  const labels256 = new Array(256).fill(0).map((_, i) => i);

  if (!ChartRef) return;

  state.charts.histogram = new ChartRef(histogramCtx, {
    type: 'bar',
    data: {
      labels: labels256,
      datasets: [
        {
          data: new Array(256).fill(0),
          backgroundColor: 'rgba(56, 189, 248, 0.45)',
          borderWidth: 0,
        },
      ],
    },
    options: {
      ...commonOptions,
      scales: {
        ...commonOptions.scales,
        y: {
          ...commonOptions.scales.y,
          beginAtZero: true,
        },
      },
    },
  });

  state.charts.intensity = new ChartRef(intensityCtx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          data: [],
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.1)',
          borderWidth: 1.5,
          tension: 0.25,
          fill: true,
          pointRadius: 0,
        },
      ],
    },
    options: {
      ...commonOptions,
      scales: {
        ...commonOptions.scales,
        y: {
          ...commonOptions.scales.y,
          beginAtZero: true,
          max: 255,
        },
      },
    },
  });

  const profileLabels = new Array(liveCanvas.width).fill(0).map((_, i) => i);
  state.charts.profile = new ChartRef(profileCtx, {
    type: 'line',
    data: {
      labels: profileLabels,
      datasets: [
        {
          data: new Array(profileLabels.length).fill(0),
          borderColor: '#f97316',
          backgroundColor: 'rgba(249, 115, 22, 0.08)',
          borderWidth: 1.5,
          tension: 0,
          fill: true,
          pointRadius: 0,
        },
      ],
    },
    options: {
      ...commonOptions,
      scales: {
        ...commonOptions.scales,
        y: {
          ...commonOptions.scales.y,
          beginAtZero: true,
          max: 255,
        },
      },
    },
  });
}

function updateChartsFromFrame(imageData) {
  const { width, height, data } = imageData;

  const hist = new Array(256).fill(0);
  let sum = 0;
  const numPixels = width * height;

  const centerY = Math.floor(height / 2);
  const profile = new Array(width);

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const g = data[idx];
      hist[g]++;
      if (y === centerY) {
        profile[x] = g;
      }
      sum += g;
    }
  }

  const mean = sum / numPixels;
  state.intensityHistory.push(mean);
  if (state.intensityHistory.length > 60) {
    state.intensityHistory.shift();
  }

  const { histogram, intensity, profile: profileChart } = state.charts;
  if (histogram) {
    histogram.data.datasets[0].data = hist;
    histogram.update('none');
  }

  if (intensity) {
    intensity.data.labels = state.intensityHistory.map((_, i) => i + 1);
    intensity.data.datasets[0].data = [...state.intensityHistory];
    intensity.update('none');
  }

  if (profileChart) {
    profileChart.data.datasets[0].data = profile;
    profileChart.update('none');
  }

  updateMetricDisplay();
}

// SECTION: Live loop
function scheduleNextFrame() {
  if (!state.isLive) return;
  const now = performance.now();
  const dt = now - state.lastFrameTime;
  if (dt >= state.frameIntervalMs) {
    state.lastFrameTime = now;
    generateSyntheticFrame(now);
  }
  requestAnimationFrame(scheduleNextFrame);
}

function startLive() {
  if (state.isLive) return;
  state.isLive = true;
  state.lastFrameTime = performance.now();
  liveInfoEl.textContent = `Live · ${frameRateEl.value} fps`;
  btnLive.textContent = 'Stop Live View';
  log(`Live view started @ ${frameRateEl.value} fps`);
  scheduleNextFrame();
}

function stopLive() {
  if (!state.isLive) return;
  state.isLive = false;
  liveInfoEl.textContent = 'Stopped';
  btnLive.textContent = 'Start Live View';
  log('Live view stopped');
}

// SECTION: Automation
function updateAutomationVisibility() {
  const mode = automationModeEl.value;
  timelapseConfigEl.hidden = mode !== 'timelapse';
  sequenceConfigEl.hidden = mode !== 'sequence';
}

function stopAutomation() {
  if (state.automation.timerId) {
    clearInterval(state.automation.timerId);
    state.automation.timerId = null;
  }
  state.automation.running = false;
  automationStatusEl.textContent = 'Automation idle.';
  btnAutoStart.disabled = false;
  btnAutoStop.disabled = true;
  log('Automation stopped', 'system');
}

function startAutomation() {
  const mode = automationModeEl.value;
  if (mode === 'off') {
    log('Select an automation mode to start.', 'system');
    return;
  }

  stopAutomation();

  if (mode === 'timelapse') {
    const intervalSec = clamp(parseFloat(tlIntervalEl.value) || 1, 0.1, 3600);
    const frames = clamp(parseInt(tlFramesEl.value, 10) || 1, 1, 10000);

    state.automation.mode = 'timelapse';
    state.automation.framesCaptured = 0;
    state.automation.targetFrames = frames;
    state.automation.running = true;

    const intervalMs = intervalSec * 1000;
    automationStatusEl.textContent = `Time-lapse · every ${intervalSec.toFixed(
      2
    )} s · ${frames} frames`;
    log(
      `Time-lapse started: ${frames} frames every ${intervalSec.toFixed(
        2
      )} s (simulated captures)`,
      'system'
    );

    state.automation.timerId = setInterval(() => {
      if (!state.automation.running) return;
      state.automation.framesCaptured++;
      log(`Time-lapse frame ${state.automation.framesCaptured}/${frames}`);
      if (!state.isLive) {
        generateSyntheticFrame(performance.now());
      }

      if (state.automation.framesCaptured >= frames) {
        stopAutomation();
      }
    }, intervalMs);
  } else if (mode === 'sequence') {
    const startExp = clamp(parseFloat(seqStartExpEl.value) || 1, 0.05, 10000);
    const endExp = clamp(parseFloat(seqEndExpEl.value) || 10, 0.05, 10000);
    const steps = clamp(parseInt(seqStepsEl.value, 10) || 2, 2, 200);
    const repeat = clamp(parseInt(seqRepeatEl.value, 10) || 1, 1, 100);

    const seq = [];
    for (let r = 0; r < repeat; r++) {
      for (let i = 0; i < steps; i++) {
        const t = steps === 1 ? 0 : i / (steps - 1);
        const e = startExp + (endExp - startExp) * t;
        seq.push(e);
      }
    }

    state.automation.mode = 'sequence';
    state.automation.seqSteps = seq;
    state.automation.seqIndex = 0;
    state.automation.running = true;

    automationStatusEl.textContent = `Exposure sequence · ${seq.length} steps`;
    log(`Exposure sequence started with ${seq.length} steps`, 'system');

    const stepIntervalMs = 500;
    state.automation.timerId = setInterval(() => {
      if (!state.automation.running) return;
      if (state.automation.seqIndex >= state.automation.seqSteps.length) {
        stopAutomation();
        return;
      }
      const exp = state.automation.seqSteps[state.automation.seqIndex++];
      exposureMsEl.value = exp.toFixed(2);
      log(`Sequence step ${state.automation.seqIndex}: exposure ${exp.toFixed(2)} ms`);
      if (!state.isLive) {
        generateSyntheticFrame(performance.now());
      }
    }, stepIntervalMs);
  }

  btnAutoStart.disabled = true;
  btnAutoStop.disabled = false;
}

// SECTION: Tabs handling
function initTabs() {
  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('[data-tab-panel]');

  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const target = tab.getAttribute('data-tab');
      tabs.forEach((t) => t.classList.remove('tab--active'));
      tab.classList.add('tab--active');

      panels.forEach((panel) => {
        const isTarget = panel.getAttribute('data-tab-panel') === target;
        panel.hidden = !isTarget;
      });
    });
  });
}

// SECTION: Event handlers
btnLive?.addEventListener('click', () => {
  if (state.isLive) stopLive();
  else startLive();
});

btnCapture?.addEventListener('click', () => {
  generateSyntheticFrame(performance.now());
  log(`Captured still (${captureModeEl.value})`, 'user');
});

btnRecord?.addEventListener('click', () => {
  log('Video recording is simulated in this UI-only prototype.', 'system');
});

btnReset?.addEventListener('click', () => {
  exposureMsEl.value = 10;
  gainEl.value = 0;
  frameRateEl.value = 20;
  frameRateLabelEl.textContent = '20 fps';
  log('Controls reset to defaults', 'system');
});

frameRateEl?.addEventListener('input', () => {
  const fps = parseInt(frameRateEl.value, 10) || 1;
  state.frameIntervalMs = 1000 / fps;
  frameRateLabelEl.textContent = `${fps} fps`;
  if (state.isLive) {
    liveInfoEl.textContent = `Live · ${fps} fps`;
  }
});

btnSimulateSignal?.addEventListener('click', () => {
  for (let i = 0; i < 30; i++) {
    generateSyntheticFrame(performance.now() + i * 30);
  }
  log('Simulated signal burst applied to analysis charts.', 'system');
});

btnClearLog?.addEventListener('click', () => {
  if (logEl) logEl.innerHTML = '';
});

automationModeEl?.addEventListener('change', updateAutomationVisibility);
btnAutoStart?.addEventListener('click', startAutomation);
btnAutoStop?.addEventListener('click', stopAutomation);
btnAutoStop.disabled = true;

// SECTION: Segmented control (binning)
const binningButtons = document.querySelectorAll('[data-binning]');
binningButtons.forEach((btn) => {
  btn.addEventListener('click', () => {
    binningButtons.forEach((b) => b.classList.remove('segmented__item--active'));
    btn.classList.add('segmented__item--active');
    const val = btn.getAttribute('data-binning');
    log(`Binning set to ${val}×${val}`);
  });
});

// SECTION: Init
window.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initCharts();
  updateAutomationVisibility();
  log('UI ready. This prototype simulates frames on the client only.', 'system');
});

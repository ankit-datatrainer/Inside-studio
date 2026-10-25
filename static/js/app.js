/* Insight Studio front-end helpers (vanilla JS, offline-friendly) */

/* ------------------------------------------------------------------ utils */
function toast(message, type = 'info', ms = 4200) {
  const wrap = document.getElementById('toast-wrap');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = 'toast ' + (type === 'info' ? '' : type);
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, ms);
}

function busy(on, text = 'Working…') {
  const box = document.getElementById('busy');
  if (!box) return;
  document.getElementById('busy-text').textContent = text;
  box.classList.toggle('hidden', !on);
}

async function api(url, payload) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
    const data = await res.json().catch(() => ({ok: false, error: 'Bad server response'}));
    return data;
  } catch (err) {
    return {ok: false, error: 'Network error: ' + err.message};
  }
}

function fmt(value, decimals = 2) {
  const n = Number(value);
  if (!isFinite(n)) return value;
  return n.toLocaleString(undefined, {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
}

/* ----------------------------------------------- universal progress engine */
window.downloadWithProgress = async function(url, defaultFilename = 'download.pdf', options = {}) {
  const modal = document.getElementById('export-progress-modal');
  if (!modal) {
    window.location.href = url;
    return;
  }

  const titleEl = document.getElementById('export-modal-title');
  const subEl = document.getElementById('export-modal-sub');
  const labelEl = document.getElementById('export-stage-label');
  const pctEl = document.getElementById('export-pct-badge');
  const fillEl = document.getElementById('export-bar-fill');
  const stepsList = document.getElementById('export-steps-list');
  const closeBtn = document.getElementById('export-modal-close');

  const title = options.title || 'Preparing Your Export';
  const sub = options.sub || 'Compiling publication-ready document';
  const steps = options.steps && options.steps.length > 0 ? options.steps : [
    'Auditing dataset health metrics...',
    'Evaluating quality scorecard & matrix...',
    'Compiling vector document layout...',
    'Finalizing download...'
  ];

  titleEl.textContent = title;
  subEl.textContent = sub;
  pctEl.textContent = '0%';
  fillEl.style.width = '0%';
  fillEl.style.background = 'linear-gradient(90deg, #0066FF 0%, #23C0FF 100%)';
  labelEl.textContent = steps[0];

  stepsList.innerHTML = steps.map((s, idx) => `
    <div class="progress-step-item ${idx === 0 ? 'active' : ''}" id="p-step-${idx}">
      <span class="progress-step-icon">
        <span class="progress-step-dot"></span>
      </span>
      <span>${s}</span>
    </div>
  `).join('');

  modal.classList.remove('hidden');

  let currentStepIdx = 0;
  let currentPct = 12;
  pctEl.textContent = `${currentPct}%`;
  fillEl.style.width = `${currentPct}%`;

  let isAborted = false;
  const abortCtrl = new AbortController();

  const handleClose = () => {
    isAborted = true;
    abortCtrl.abort();
    modal.classList.add('hidden');
    closeBtn.removeEventListener('click', handleClose);
  };
  closeBtn.addEventListener('click', handleClose);

  const targetPctSteps = [28, 58, 82, 94];
  let stageTimer = setInterval(() => {
    if (isAborted) {
      clearInterval(stageTimer);
      return;
    }
    if (currentStepIdx < steps.length - 1) {
      const maxTarget = targetPctSteps[currentStepIdx] || 90;
      if (currentPct < maxTarget) {
        currentPct += Math.floor(Math.random() * 5) + 3;
        if (currentPct > maxTarget) currentPct = maxTarget;
        pctEl.textContent = `${currentPct}%`;
        fillEl.style.width = `${currentPct}%`;
      } else {
        const prevItem = document.getElementById(`p-step-${currentStepIdx}`);
        if (prevItem) {
          prevItem.classList.remove('active');
          prevItem.classList.add('done');
          prevItem.querySelector('.progress-step-icon').innerHTML = `<svg class="icon sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`;
        }
        currentStepIdx++;
        const nextItem = document.getElementById(`p-step-${currentStepIdx}`);
        if (nextItem) {
          nextItem.classList.add('active');
          labelEl.textContent = steps[currentStepIdx];
        }
      }
    }
  }, 180);

  try {
    const fetchOptions = {
      method: options.method || 'GET',
      signal: abortCtrl.signal,
    };
    if (options.headers) {
      fetchOptions.headers = options.headers;
    }
    if (options.body) {
      fetchOptions.body = typeof options.body === 'string' ? options.body : JSON.stringify(options.body);
      if (!fetchOptions.headers) fetchOptions.headers = {'Content-Type': 'application/json'};
    }

    const response = await fetch(url, fetchOptions);
    clearInterval(stageTimer);

    if (!response.ok) {
      const errText = await response.text().catch(() => '');
      throw new Error(`Export request failed (${response.status}): ${errText.slice(0, 80) || response.statusText}`);
    }

    let filename = defaultFilename;
    const disposition = response.headers.get('Content-Disposition');
    if (disposition && disposition.indexOf('filename=') !== -1) {
      const matches = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/.exec(disposition);
      if (matches != null && matches[1]) {
        filename = matches[1].replace(/['"]/g, '');
      }
    }

    const contentType = response.headers.get('Content-Type') || '';
    if (contentType.includes('application/json') && options.expectJson) {
      const jsonData = await response.json();
      if (!jsonData.ok) throw new Error(jsonData.error || 'Export generation failed');
      if (jsonData.url) {
        return window.downloadWithProgress(jsonData.url, jsonData.file || defaultFilename, {
          title, sub: 'Saving to your machine...',
          steps: ['Generating layout...', 'Packaging file...', 'Finalizing download...']
        });
      }
    }

    const blob = await response.blob();

    pctEl.textContent = '100%';
    fillEl.style.width = '100%';
    labelEl.textContent = 'Download Ready!';

    for (let i = 0; i < steps.length; i++) {
      const item = document.getElementById(`p-step-${i}`);
      if (item) {
        item.classList.remove('active');
        item.classList.add('done');
        item.querySelector('.progress-step-icon').innerHTML = `<svg class="icon sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`;
      }
    }

    const blobUrl = window.URL.createObjectURL(blob);
    const downloadAnchor = document.createElement('a');
    downloadAnchor.style.display = 'none';
    downloadAnchor.href = blobUrl;
    downloadAnchor.download = filename;
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();

    setTimeout(() => {
      window.URL.revokeObjectURL(blobUrl);
      downloadAnchor.remove();
      modal.classList.add('hidden');
      closeBtn.removeEventListener('click', handleClose);
      toast(`Exported: ${filename}`, 'success');
    }, 550);

  } catch (err) {
    clearInterval(stageTimer);
    if (err.name === 'AbortError') return;
    labelEl.textContent = 'Export error';
    pctEl.textContent = 'Failed';
    fillEl.style.background = '#DC2626';
    toast(err.message || 'Export failed', 'error');
    setTimeout(() => {
      modal.classList.add('hidden');
      closeBtn.removeEventListener('click', handleClose);
    }, 2800);
  }
};

/* ------------------------------------------------------------------ tabs */
function initTabs(name) {
  const nav = document.querySelector(`.tabs[data-tabs="${name}"]`);
  if (!nav) return;
  const buttons = nav.querySelectorAll('.tab');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      buttons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('hidden'));
      const target = document.getElementById(btn.dataset.tab);
      if (target) target.classList.remove('hidden');
    });
  });
}

/* ---------------------------------------------------------- chart builder */
const CHART_NEEDS = {
  bar: 'xy', hbar: 'xy', line: 'xy', area: 'xy', stacked_bar: 'xyg',
  pie: 'xy', donut: 'xy', scatter: 'xy', hist: 'x', box: 'xy',
  count: 'x', heatmap: 'none', heatmap_counts: 'xy',
};

function initChartBuilder(columns) {
  const form = document.getElementById('chart-form');
  if (!form) return;

  const kindByCol = {};
  (columns || []).forEach(c => { kindByCol[c.name] = c.kind; });

  const setValue = (field, value) => {
    if (value === undefined || value === null) return;
    const el = form.elements[field];
    if (!el) return;
    el.value = value;
  };

  function collect() {
    const fd = new FormData(form);
    const spec = {};
    for (const [key, value] of fd.entries()) spec[key] = value;
    spec.show_values = form.elements.show_values.checked;
    spec.on_dashboard = form.elements.on_dashboard.checked;
    if (kindByCol[spec.x] === 'datetime') spec.x_is_date = true;
    return spec;
  }

  async function render() {
    const spec = collect();
    busy(true, 'Rendering chart…');
    const res = await api('/api/chart/render', spec);
    busy(false);
    if (!res.ok) { toast(res.error || 'Could not render the chart', 'error'); return; }

    document.getElementById('preview-empty').classList.add('hidden');
    document.getElementById('preview-box').classList.remove('hidden');
    const img = document.getElementById('preview-img');
    img.src = res.chart.image_url + '?t=' + Date.now();
    document.getElementById('preview-title').textContent = res.chart.title;
    document.getElementById('preview-note').textContent =
      'saved to your charts' + (res.chart.on_dashboard ? ' · added to dashboard' : '');

    const links = document.getElementById('download-links');
    links.innerHTML = '';
    (res.downloads || []).forEach(d => {
      const a = document.createElement('a');
      a.className = 'dl';
      a.href = d.url;
      a.textContent = d.label;
      links.appendChild(a);
    });
    toast('Chart rendered: ' + res.chart.title, 'success');
    addToGallery(res.chart, res.downloads);
  }

  form.addEventListener('submit', e => { e.preventDefault(); render(); });

  // suggestion cards prefill the form
  document.querySelectorAll('.suggestion[data-spec]').forEach(card => {
    const btn = card.querySelector('.make-chart');
    if (!btn) return;
    btn.addEventListener('click', () => {
      let spec;
      try { spec = JSON.parse(card.dataset.spec); } catch (e) { return; }
      setValue('chart_type', spec.chart_type);
      setValue('x', spec.x || '');
      setValue('y', spec.y || '');
      setValue('group', spec.group || '');
      setValue('agg', spec.agg || 'sum');
      setValue('freq', spec.freq || 'M');
      setValue('top_n', spec.top_n || 0);
      setValue('title', spec.title || '');
      document.getElementById('builder').scrollIntoView({behavior: 'smooth', block: 'start'});
      render();
    });
  });
}

function addToGallery(chart, downloads) {
  const section = [...document.querySelectorAll('section.card')]
    .find(s => (s.querySelector('h2')?.textContent || '').startsWith('Your charts'));
  if (!section) return;

  let gallery = section.querySelector('.gallery');
  if (!gallery) {
    section.querySelector('p.muted')?.remove();
    gallery = document.createElement('div');
    gallery.className = 'gallery';
    section.appendChild(gallery);
  }

  const item = document.createElement('div');
  item.className = 'gallery-item';
  item.dataset.id = chart.id;

  const img = document.createElement('img');
  img.src = chart.image_url;
  img.alt = chart.title;

  const body = document.createElement('div');
  body.className = 'gi-body';

  const title = document.createElement('b');
  title.textContent = chart.title;

  const meta = document.createElement('span');
  meta.className = 'muted small';
  const spec = chart.spec || {};
  meta.textContent = (spec.chart_type || 'chart').replace('_', ' ') + ' · ' +
    (spec.x || 'all columns') + (spec.y ? ' × ' + spec.y : '');

  const actions = document.createElement('div');
  actions.className = 'gi-actions';

  const toggle = document.createElement('button');
  toggle.className = 'btn ghost small toggle-dash';
  toggle.dataset.id = chart.id;
  toggle.dataset.on = chart.on_dashboard ? '0' : '1';
  toggle.textContent = chart.on_dashboard ? 'Remove from dashboard' : 'Add to dashboard';

  const badge = document.createElement('span');
  badge.className = 'badge' + (chart.on_dashboard ? ' ok' : '');
  badge.textContent = chart.on_dashboard ? 'in dashboard' : 'saved';

  const del = document.createElement('button');
  del.className = 'btn ghost small danger delete-chart';
  del.dataset.id = chart.id;
  del.textContent = 'Delete';

  actions.append(toggle, badge, del);

  const row = document.createElement('div');
  row.className = 'download-row small';
  (downloads || []).forEach(d => {
    const a = document.createElement('a');
    a.className = 'dl';
    a.href = d.url;
    a.textContent = d.label;
    row.appendChild(a);
  });

  body.append(title, meta, actions, row);
  item.append(img, body);
  gallery.prepend(item);

  bindGalleryItem(item);
  const heading = section.querySelector('h2');
  if (heading) {
    const count = gallery.querySelectorAll('.gallery-item').length;
    heading.textContent = `Your charts (${count})`;
  }
}

/* ------------------------------------------------------------------ gallery */
function bindGalleryItem(scope) {
  scope.querySelectorAll('.toggle-dash').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
      const on = btn.dataset.on === '1';
      const res = await api('/api/chart/dashboard', {id: btn.dataset.id, on});
      if (!res.ok) { toast(res.error || 'Update failed', 'error'); return; }
      btn.dataset.on = on ? '0' : '1';
      btn.textContent = on ? 'Remove from dashboard' : 'Add to dashboard';
      const badge = btn.parentElement.querySelector('.badge');
      if (badge) {
        badge.textContent = on ? 'in dashboard' : 'saved';
        badge.classList.toggle('ok', on);
      }
      toast(on ? 'Added to dashboard' : 'Removed from dashboard', 'success');
    });
  });

  scope.querySelectorAll('.delete-chart').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
      if (!confirm('Delete this chart?')) return;
      const res = await api('/api/chart/delete', {id: btn.dataset.id});
      if (res.ok) {
        const item = btn.closest('.gallery-item');
        if (item) item.remove();
        toast('Chart deleted', 'success');
      }
    });
  });
}

function initGallery() {
  bindGalleryItem(document);
}

/* ------------------------------------------------------------ table builder */
function initTableBuilder() {
  const buildBtn = document.getElementById('tb-build');
  if (!buildBtn) return;

  const metrics = [];
  const metricsBox = document.getElementById('tb-metrics');
  const colSel = document.getElementById('tb-metric-col');
  const aggSel = document.getElementById('tb-metric-agg');
  let lastTableId = null;

  function chipLabel(metric) {
    if (metric.column === '__rows__' || metric.agg === 'count') return 'Rows';
    return metric.agg + ' · ' + metric.column;
  }

  function renderChips() {
    metricsBox.innerHTML = '';
    metrics.forEach((m, i) => {
      const chip = document.createElement('span');
      chip.className = 'metric-chip';
      chip.innerHTML = `<span>${chipLabel(m)}</span><button type="button" title="remove">&times;</button>`;
      chip.querySelector('button').addEventListener('click', () => {
        metrics.splice(i, 1);
        renderChips();
      });
      metricsBox.appendChild(chip);
    });
  }

  document.getElementById('tb-add-metric').addEventListener('click', () => {
    const column = colSel.value;
    const agg = aggSel.value;
    if (!column) return;
    const metric = column === '__rows__'
      ? {column: null, agg: 'count'}
      : {column, agg};
    const key = chipLabel(metric);
    if (!metrics.some(m => chipLabel(m) === key)) metrics.push(metric);
    renderChips();
  });
  renderChips();

  async function build(onDashboard) {
    const rows = Array.from(document.getElementById('tb-rows').selectedOptions).map(o => o.value);
    if (!rows.length) { toast('Pick at least one column to group by', 'error'); return; }
    const title = document.getElementById('tb-title').value || 'Custom analysis';
    busy(true, 'Building table…');
    const res = await api('/api/table/build', {
      rows, metrics, title, on_dashboard: !!onDashboard,
    });
    busy(false);
    if (!res.ok) { toast(res.error || 'Could not build table', 'error'); return; }

    lastTableId = res.table.id;
    document.getElementById('tb-empty').classList.add('hidden');
    const box = document.getElementById('tb-result');
    box.classList.remove('hidden');
    document.getElementById('tb-result-title').textContent =
      res.table.title + `  (${res.table.total_rows} groups)`;

    let html = '<table class="data compact"><thead><tr>';
    res.table.columns.forEach(c => { html += `<th>${c}</th>`; });
    html += '</tr></thead><tbody>';
    res.table.rows.slice(0, 60).forEach(row => {
      html += '<tr>' + row.map(cell => `<td>${cell}</td>`).join('') + '</tr>';
    });
    html += '</tbody></table>';
    document.getElementById('tb-result-table').innerHTML = html;
    document.getElementById('tb-csv').href = res.csv_url;
    toast(onDashboard ? 'Table built and added to dashboard' : 'Table built', 'success');
  }

  buildBtn.addEventListener('click', () => build(document.getElementById('tb-dash').checked));
  document.getElementById('tb-save').addEventListener('click', async () => {
    if (!lastTableId) { toast('Build the table first', 'error'); return; }
    const res = await api('/api/table/dashboard', {id: lastTableId, on: true});
    if (res.ok) toast('Saved to dashboard', 'success');
    else toast(res.error || 'Could not save', 'error');
  });

  // ready-made tables prefill the builder
  document.querySelectorAll('.auto-table').forEach(card => {
    card.querySelector('.load-table')?.addEventListener('click', () => {
      let groups = [], mets = [];
      try { groups = JSON.parse(card.dataset.group); } catch (e) {}
      try { mets = JSON.parse(card.dataset.metrics); } catch (e) {}
      const sel = document.getElementById('tb-rows');
      Array.from(sel.options).forEach(o => { o.selected = groups.includes(o.value); });
      metrics.length = 0;
      mets.forEach(m => metrics.push({column: m.column, agg: m.agg}));
      renderChips();
      document.getElementById('tb-title').value = card.dataset.title || '';
      build(true);
    });
  });
}

/* ------------------------------------------------------------- AI Chat Panel */
function initChatWidget() {
  const toggle = document.getElementById('chat-toggle');
  const panel = document.getElementById('chat-panel');
  const close = document.getElementById('chat-close');
  const sendBtn = document.getElementById('chat-send');
  const input = document.getElementById('chat-input');
  const msgContainer = document.getElementById('chat-messages');

  if (!toggle || !panel || !input || !sendBtn || !msgContainer) return;

  const chatHistory = [];

  const toggleChat = (show) => {
    const isHidden = panel.classList.contains('hidden');
    const target = show !== undefined ? !show : !isHidden;
    panel.classList.toggle('hidden', target);
    if (!target) {
      input.focus();
      scrollBottom();
    }
  };

  toggle.addEventListener('click', () => toggleChat());
  close.addEventListener('click', () => toggleChat(false));

  function scrollBottom() {
    msgContainer.scrollTop = msgContainer.scrollHeight;
  }

  function appendMsg(role, htmlContent) {
    const msg = document.createElement('div');
    msg.className = `chat-msg ${role}`;
    msg.innerHTML = `<div class="chat-bubble">${htmlContent}</div>`;
    msgContainer.appendChild(msg);
    scrollBottom();
    return msg;
  }

  function formatMarkdown(text) {
    let escaped = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    // Headings
    escaped = escaped.replace(/^### (.*$)/gm, '<h4 style="margin:12px 0 6px; font-size:13.5px; font-weight:700; color:var(--ink);">$1</h4>');
    escaped = escaped.replace(/^## (.*$)/gm, '<h3 style="margin:14px 0 8px; font-size:14.5px; font-weight:700; color:var(--ink);">$1</h3>');
    escaped = escaped.replace(/^# (.*$)/gm, '<h2 style="margin:16px 0 10px; font-size:15.5px; font-weight:700; color:var(--ink);">$1</h2>');
    // Horizontal rule
    escaped = escaped.replace(/^\s*---\s*$/gm, '<hr style="border:none; border-top:1px solid var(--line); margin:10px 0;"/>');
    // Bold & italic
    escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    escaped = escaped.replace(/\*(.*?)\*/g, '<em>$1</em>');
    // Inline code
    escaped = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bullet lists
    escaped = escaped.replace(/^\s*[-*]\s+(.*)$/gm, '<li>$1</li>');
    escaped = escaped.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
    // Paragraphs
    escaped = escaped.replace(/\n\n+/g, '</p><p>');
    return `<p>${escaped}</p>`;
  }

  async function sendQuestion() {
    const q = input.value.trim();
    if (!q) return;

    input.value = '';
    appendMsg('user', escapeHtml(q));
    chatHistory.push({ role: 'user', content: q });

    const thinkingMsg = appendMsg('bot', '<em>Analyzing with AI…</em>');
    sendBtn.disabled = true;

    try {
      const res = await api('/api/chat', { question: q, history: chatHistory });
      thinkingMsg.remove();
      sendBtn.disabled = false;

      if (res.ok) {
        appendMsg('bot', formatMarkdown(res.answer));
        chatHistory.push({ role: 'assistant', content: res.answer });
      } else {
        appendMsg('bot', `<span style="color:var(--err); display:inline-flex; align-items:center; gap:6px;"><svg class="icon sm" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> ${res.error || 'Could not reach AI'}</span>`);
      }
    } catch (err) {
      thinkingMsg.remove();
      sendBtn.disabled = false;
      appendMsg('bot', `<span style="color:var(--err); display:inline-flex; align-items:center; gap:6px;"><svg class="icon sm" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> Network error: ${err.message}</span>`);
    }
  }

  sendBtn.addEventListener('click', sendQuestion);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendQuestion();
    }
  });

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
}

// Auto-initialize global widgets on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  initChatWidget();
});


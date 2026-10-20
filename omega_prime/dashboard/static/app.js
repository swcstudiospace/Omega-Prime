'use strict';

(() => {
  const byId = (id) => document.getElementById(id);
  const connectionEl = byId('connection');
  const lastRefreshEl = byId('last-refresh');
  let snapshotAt = 0;
  let eventSource = null;
  let pollingTimer = null;
  let reconnectTimer = null;
  let staleTimer = null;

  const text = (value, fallback = '—') => value === null || value === undefined || value === '' ? fallback : String(value);
  const node = (tag, value, className) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined) el.textContent = text(value);
    return el;
  };
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); };
  const safeArray = (value) => Array.isArray(value) ? value : [];
  const formatAedtTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return text(value);
    return new Intl.DateTimeFormat('en-AU', {timeZone: 'Australia/Sydney', dateStyle: 'medium', timeStyle: 'short', timeZoneName: 'short'}).format(date);
  };
  const relativeTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return text(value);
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const abs = Math.abs(seconds);
    let amount, unit;
    if (abs < 60) { amount = abs; unit = 'second'; }
    else if (abs < 3600) { amount = Math.round(abs / 60); unit = 'minute'; }
    else if (abs < 86400) { amount = Math.round(abs / 3600); unit = 'hour'; }
    else { amount = Math.round(abs / 86400); unit = 'day'; }
    const phrase = `${amount} ${unit}${amount === 1 ? '' : 's'}`;
    return seconds < 0 ? `in ${phrase}` : `${phrase} ago`;
  };
  const timeLabel = (value) => {
    const wrap = node('span', undefined, 'time-label');
    wrap.append(node('span', relativeTime(value), 'relative-time'));
    wrap.append(node('time', formatAedtTime(value), 'absolute-time'));
    return wrap;
  };
  const badge = (value, prefix = '') => {
    const raw = text(value, 'unknown');
    return node('span', `${prefix}${raw}`, `badge badge-${raw.toLowerCase().replace(/[^a-z0-9_-]/g, '-')}`);
  };
  const empty = (container, message) => { clear(container); container.append(node('p', message, 'empty-state')); };

  function renderHeader(snapshot) {
    const clock = byId('clock');
    const updateClock = () => {
      const now = new Date();
      clock.textContent = new Intl.DateTimeFormat('en-AU', {timeZone: 'Australia/Sydney', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZoneName: 'short'}).format(now);
      clock.dateTime = now.toISOString();
    };
    if (!clock.dataset.clockStarted) { updateClock(); clock.dataset.clockStarted = 'true'; window.setInterval(updateClock, 1000); }
    const generated = snapshot && snapshot.generated_at;
    lastRefreshEl.textContent = generated ? `${relativeTime(generated)} (${formatAedtTime(generated)})` : 'Snapshot received';
    const strip = byId('health-strip');
    clear(strip);
    const sources = snapshot && snapshot.sources && typeof snapshot.sources === 'object' ? snapshot.sources : {};
    Object.keys(sources).sort().forEach((id) => {
      try {
        const source = sources[id] && typeof sources[id] === 'object' ? sources[id] : {};
        const status = text(source.status, 'unknown');
        const chip = node('li', undefined, `health-chip health-${status.toLowerCase().replace(/[^a-z0-9_-]/g, '-')}`);
        chip.append(node('strong', id), node('span', status));
        if (['disabled', 'unavailable', 'error'].includes(status)) chip.append(node('span', source.error || source.detail || 'No further information', 'health-reason'));
        strip.append(chip);
      } catch (error) { console.error('Could not render source health', error); }
    });
  }

  function renderActive(snapshot) {
    const container = byId('active-content');
    const rows = safeArray(snapshot && snapshot.active).slice();
    const order = ['blocked', 'working', 'quiet', 'stopped', 'waiting_on_ove', 'idle', 'unknown', 'done', 'failed'];
    rows.sort((a, b) => {
      const sa = text(a && a.state, 'unknown'), sb = text(b && b.state, 'unknown');
      const rank = (state) => { const index = order.indexOf(state); return index < 0 ? order.length : index; };
      return rank(sa) - rank(sb) || text(a && a.id).localeCompare(text(b && b.id));
    });
    if (!rows.length) return empty(container, 'Nothing running right now');
    clear(container);
    const seen = new Set();
    rows.forEach((task) => {
      try {
        if (!task || typeof task !== 'object') return;
        const id = text(task.id, '');
        if (id && seen.has(id)) return;
        if (id) seen.add(id);
        const card = node('article', undefined, 'task-card');
        const title = node('h3', task.title, 'task-title');
        card.append(title);
        const chips = node('div', undefined, 'card-chips');
        const owner = task.owner && typeof task.owner === 'object' ? task.owner.label : null;
        if (owner) chips.append(node('span', owner, 'owner-chip'));
        chips.append(badge(task.state, 'State: '));
        card.append(chips);
        const times = node('dl', undefined, 'metadata');
        if (task.started_at) { times.append(node('dt', 'Started'), node('dd', undefined)); times.lastChild.append(timeLabel(task.started_at)); }
        if (task.last_activity_at) { times.append(node('dt', 'Last activity'), node('dd', undefined)); times.lastChild.append(timeLabel(task.last_activity_at)); }
        if (times.childNodes.length) card.append(times);
        const progress = task.progress;
        if (progress && typeof progress === 'object') {
          const done = Number(progress.done), total = Number(progress.total);
          const label = node('p', undefined, 'progress-label');
          label.textContent = `Progress: ${text(progress.done, '0')} / ${text(progress.total, '?')}`;
          card.append(label);
          if (Number.isFinite(done) && Number.isFinite(total) && total > 0) {
            const bar = node('progress'); bar.max = total; bar.value = Math.max(0, Math.min(done, total)); bar.setAttribute('aria-label', `${done} of ${total} complete`); card.append(bar);
          }
        }
        if (task.model) card.append(node('p', `Model: ${task.model}`, 'model-label'));
        const disclosure = node('details', undefined, 'task-details');
        disclosure.append(node('summary', 'Details'));
        if (task.detail) disclosure.append(node('p', task.detail, 'detail-text'));
        const links = safeArray(task.links);
        if (links.length) {
          const list = node('ul', undefined, 'detail-links');
          links.forEach((link) => {
            if (!link || typeof link !== 'object') return;
            const item = node('li');
            item.append(node('span', link.label, 'link-label'), document.createTextNode(' '), node('code', link.path_or_url));
            list.append(item);
          });
          disclosure.append(list);
        }
        if (task.detail || links.length) card.append(disclosure);
        container.append(card);
      } catch (error) { console.error('Could not render active task', error); }
    });
  }

  function renderPrGate(snapshot) {
    const container = byId('pr-content');
    const rows = safeArray(snapshot && snapshot.pr_gate);
    if (!rows.length) return empty(container, 'No pull requests at the gate');
    clear(container);
    const table = node('table', undefined, 'data-table');
    table.append(node('caption', 'Pull request review and merge gate status'));
    const head = node('thead'), headerRow = node('tr');
    ['Pull request', 'Title', 'Branch', 'Status', 'CI', 'Greptile', 'Mergeability', 'Merge rule'].forEach((label) => headerRow.append(node('th', label, '')));
    head.append(headerRow); table.append(head);
    const body = node('tbody');
    rows.forEach((pr) => {
      try {
        if (!pr || typeof pr !== 'object') return;
        const row = node('tr');
        row.append(node('td', `${text(pr.repo, 'unknown')}#${text(pr.number, '?')}`), node('td', pr.title), node('td', pr.branch));
        row.append(node('td', pr.draft === true ? 'Draft' : pr.draft === false ? 'Ready' : 'Unknown'));
        const ci = node('td'); ci.append(badge(pr.ci, 'CI: ')); row.append(ci);
        row.append(node('td', pr.greptile === null || pr.greptile === undefined || pr.greptile === '' ? 'not reviewed' : pr.greptile));
        row.append(node('td', pr.mergeable === true ? 'Mergeable' : pr.mergeable === false ? 'Conflicting' : 'Unknown'));
        row.append(node('td', pr.merge_rule));
        body.append(row);
      } catch (error) { console.error('Could not render pull request', error); }
    });
    table.append(body); container.append(table);
  }

  function renderWaiting(snapshot) {
    const container = byId('waiting-content');
    const rows = safeArray(snapshot && snapshot.waiting_on_ove);
    if (!rows.length) return empty(container, 'Nothing is waiting on Ove');
    clear(container);
    rows.forEach((item) => {
      try {
        if (!item || typeof item !== 'object') return;
        const card = node('article', undefined, 'waiting-card');
        card.append(node('h3', item.ask, 'task-title'));
        if (item.since) { const p = node('p', undefined, 'waiting-since'); p.append(document.createTextNode('Since '), timeLabel(item.since)); card.append(p); }
        if (item.blocks) card.append(node('p', `Blocks: ${item.blocks}`, 'blocks-text'));
        container.append(card);
      } catch (error) { console.error('Could not render waiting item', error); }
    });
  }

  function renderFinished(snapshot) {
    const container = byId('finished-content');
    const rows = safeArray(snapshot && snapshot.finished).slice(0, 50);
    if (!rows.length) return empty(container, 'No recently finished work');
    clear(container);
    const list = node('ul', undefined, 'finished-list');
    rows.forEach((item) => {
      try {
        if (!item || typeof item !== 'object') return;
        const row = node('li', undefined, 'finished-row');
        row.append(node('span', item.title, 'finished-title'), timeLabel(item.finished_at), badge(item.result, 'Result: '));
        if (item.receipt) row.append(node('code', item.receipt, 'receipt'));
        list.append(row);
      } catch (error) { console.error('Could not render finished item', error); }
    });
    container.append(list);
  }

  function renderSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') throw new Error('Invalid snapshot');
    snapshotAt = Date.now();
    renderHeader(snapshot);
    [['active', renderActive], ['PR gate', renderPrGate], ['waiting', renderWaiting], ['finished', renderFinished]].forEach(([name, render]) => {
      try { render(snapshot); } catch (error) { console.error(`Could not render ${name} section`, error); }
    });
    if (connectionEl.textContent !== 'reconnecting') setConnection('live');
  }
  function setConnection(state) {
    connectionEl.textContent = state;
    connectionEl.className = `connection-state connection-${state}`;
  }
  async function fetchSnapshot() {
    try {
      const response = await fetch('/api/snapshot', {cache: 'no-store'});
      if (!response.ok) throw new Error(`Snapshot request failed: ${response.status}`);
      renderSnapshot(await response.json());
      return true;
    } catch (error) {
      console.error('Snapshot request failed', error);
      if (snapshotAt && Date.now() - snapshotAt > 30000) setConnection('stale');
      return false;
    }
  }
  function startPolling() {
    if (pollingTimer) return;
    const poll = async () => {
      await fetchSnapshot();
      if (eventSource) return;
      pollingTimer = window.setTimeout(poll, 5000);
    };
    pollingTimer = window.setTimeout(poll, 5000);
    if (staleTimer) window.clearInterval(staleTimer);
    staleTimer = window.setInterval(() => {
      if (snapshotAt && Date.now() - snapshotAt > 30000) setConnection('stale');
    }, 1000);
  }
  function stopPolling() {
    if (pollingTimer) window.clearTimeout(pollingTimer);
    pollingTimer = null;
    if (staleTimer) window.clearInterval(staleTimer);
    staleTimer = null;
  }
  function connectStream() {
    if (eventSource) eventSource.close();
    const source = new EventSource('/api/stream');
    eventSource = source;
    source.addEventListener('snapshot', (event) => {
      try { renderSnapshot(JSON.parse(event.data)); } catch (error) { console.error('Invalid stream snapshot', error); }
    });
    source.onopen = () => {
      if (eventSource !== source) return;
      stopPolling(); setConnection('live');
    };
    source.onerror = () => {
      if (eventSource !== source) return;
      source.close(); eventSource = null;
      setConnection('reconnecting'); startPolling();
      if (reconnectTimer) window.clearInterval(reconnectTimer);
      reconnectTimer = window.setInterval(() => {
        if (!eventSource) connectStream();
      }, 30000);
    };
  }

  fetchSnapshot().then(() => connectStream());
})();

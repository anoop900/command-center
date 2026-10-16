// Local stand-in for the claude.ai runtime the dashboard page expects: window.claude.use('db' | 'downloads').
// Talks to the REST API in cc/web.py. Snapshots are polled; writes carry the version the page last saw.
(() => {
  const POLL_MS = 3000;
  const versions = new Map();  // "col/id" -> version seen in the latest read

  const api = async (method, path, body, ifMatch) => {
    const h = {};
    if (body !== undefined) h['Content-Type'] = 'application/json';
    if (ifMatch !== undefined) h['If-Match'] = ifMatch;
    const [pp, qs] = path.split('?');  // the query string must not be percent-encoded with the path
    const r = await fetch('/api/' + pp.split('/').map(encodeURIComponent).join('/') + (qs ? '?' + qs : ''),
      {method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store'})
      .catch(e => { e.network = true; throw e; });  // only these count as lost contact; a server answer never does
    if (r.status === 404 && method === 'GET') return null;
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(j.error || r.statusText); e.code = r.status === 409 ? 'conflict' : String(r.status); throw e; }
    return j;
  };

  const docSnap = (col, row) => {
    if (row) versions.set(col + '/' + row.id, row.version);
    return {exists: !!row, id: row ? row.id : undefined, data: () => (row ? row.data : undefined)};
  };
  const querySnap = (col, rows) => {
    rows.forEach(r => versions.set(col + '/' + r.id, r.version));
    const docs = rows.map(r => ({id: r.id, exists: true, data: () => r.data}));
    return {docs, size: docs.length, empty: !docs.length, forEach: f => docs.forEach(f)};
  };

  // A fixed strip that appears only when the server stops answering (restart after an update, or the window that
  // served the page was closed) and clears itself when it answers again. Text only; nothing from the server is shown.
  let strip = null, misses = 0;
  const conn = ok => {
    misses = ok ? 0 : misses + 1;
    if (!strip && misses >= 2) {
      strip = document.createElement('div');
      strip.setAttribute('role', 'status');
      strip.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:99999;padding:8px 14px;background:#7a1f1f;color:#fff;font:14px sans-serif;text-align:center';
      strip.textContent = 'Lost contact with Command Center. It may be restarting after an update; this clears by itself. '
        + 'If it stays, open Command Center from the Start menu, or run: cc open. Your data is safe.';
      document.body.appendChild(strip);
    }
    if (ok && strip) { strip.remove(); strip = null; }
  };

  const ticks = new Set();   // every live poller, so the page can ask for an immediate re-read (window.ccRefresh)
  window.ccRefresh = () => Promise.all([...ticks].map(t => t(true)));
  const poll = (fetchSnap, cb, err) => {
    let stop = false, key = null, down = false;
    const tick = async force => {
      if (stop || (document.hidden && key !== null && force !== true)) return;
      try {
        const s = await fetchSnap(key);
        down = false; conn(true);
        if (s && s.key !== key) { key = s.key; cb(s.snap); }
      } catch (e) { if (!down && err) err(e); down = true; conn(!e.network); }
    };
    tick();
    const t = setInterval(() => tick(), POLL_MS);
    ticks.add(tick);
    return () => { stop = true; clearInterval(t); ticks.delete(tick); };
  };

  const writeVersion = (col, id) => { const v = versions.get(col + '/' + id); return v === undefined ? '*' : String(v); };

  const docRef = (col, id) => ({
    id,
    get: async () => docSnap(col, await api('GET', col + '/' + id)),
    set: async data => { const r = await api('PUT', col + '/' + id, data, writeVersion(col, id)); versions.set(col + '/' + id, r.version); },
    update: async data => { const r = await api('PATCH', col + '/' + id, data, writeVersion(col, id)); versions.set(col + '/' + id, r.version); },
    delete: async () => { await api('DELETE', col + '/' + id, undefined, writeVersion(col, id)); versions.delete(col + '/' + id); },
    onSnapshot: (cb, err) => poll(async () => {
      const row = await api('GET', col + '/' + id);
      return {key: JSON.stringify(row), snap: docSnap(col, row)};
    }, cb, err),
  });

  const db = {
    doc: path => { const i = path.indexOf('/'); return docRef(path.slice(0, i), path.slice(i + 1)); },
    collection: col => ({
      doc: id => docRef(col, id),
      onSnapshot: (cb, err) => poll(async since => {
        const j = await api('GET', col + (since ? '?since=' + encodeURIComponent(since.split('\u0000')[0]) : ''));
        if (j.unchanged) return null;
        return {key: j.stamp + '\u0000', snap: querySnap(col, j.docs)};
      }, cb, err),
    }),
  };

  const downloads = {
    save: async ({filename, data}) => {
      const blob = data instanceof Blob ? data : new Blob([data]);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename || 'download';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    },
  };

  // Ask panel: the server runs the configured AI runner; nothing is stored.
  const ask = {
    ask: async (question, context) => {
      const r = await fetch('/ask', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                     body: JSON.stringify({question, context})});
      return r.json();
    },
  };

  const services = {db, downloads, ask};
  window.claude = {use: name => (services[name] ? Promise.resolve(services[name]) : Promise.reject(new Error('not available locally')))};
})();

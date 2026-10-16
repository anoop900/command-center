// Keeps the Job Hunting folder handle across sessions (handles cannot go in chrome.storage).
const IDB = {
  db: () => new Promise((ok, no) => {
    const r = indexedDB.open("jaf", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("kv");
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  }),
  async get(k) { const d = await IDB.db(); return new Promise((ok) => { const q = d.transaction("kv").objectStore("kv").get(k); q.onsuccess = () => ok(q.result); q.onerror = () => ok(undefined); }); },
  async set(k, v) { const d = await IDB.db(); return new Promise((ok, no) => { const t = d.transaction("kv", "readwrite"); t.objectStore("kv").put(v, k); t.oncomplete = ok; t.onerror = () => no(t.error); }); },
};

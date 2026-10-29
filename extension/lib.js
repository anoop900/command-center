// Shared by the side panel and the background worker: role kits, resume, profile, fill.
const JAF_SCRIPTS = ["common.js", "adapters/workday.js", "adapters/successfactors.js", "adapters/lever.js", "adapters/ashby.js", "adapters/greenhouse.js"];
const JAF_PORTS = [8792, 8791]; // tried when no dashboard address is set in Options

// The running dashboard: the address from Options, else the usual local ports. null when none answers.
async function jafBase() {
  const { dash } = await chrome.storage.local.get("dash");
  let tries = JAF_PORTS.map((p) => `http://127.0.0.1:${p}`);
  if (dash) { try { tries = [new URL(dash).origin]; } catch { return null; } }
  for (const b of tries) {
    try {
      const r = await fetch(b + "/extension/health", { cache: "no-store", signal: AbortSignal.timeout(2500) });
      if (r.ok && (await r.json()).ok) return b;
    } catch {}
  }
  return null;
}
async function jafGet(base, path) {
  const r = await fetch(base + path, { cache: "no-store" });
  if (!r.ok) { let m = ""; try { m = (await r.json()).error; } catch {} throw new Error(m || `dashboard answered ${r.status}`); }
  return r;
}

// Open roles that have a kit, from the dashboard: same shape as the folder roles, plus key and resume name.
async function jafDashRoles(base) {
  const { roles } = await (await jafGet(base, "/extension/roles")).json();
  return roles.map((x) => {
    const parts = x.folder.split(/[\/]/);
    return { date: parts[0], folder: parts[parts.length - 1], rel: x.folder, key: x.key, handle: null, resumeName: x.resume, applied: x.applied,
      kit: { company: x.company, aliases: x.aliases, posting_url: x.link, apply_url: "", job_id: x.job_id } };
  });
}

// Where the roles come from: the dashboard first, the chosen folder only if the dashboard is not reachable.
// -> {source: "dashboard"|"folder"|null, base, roles, problem}
async function jafLoad(ask) {
  const base = await jafBase();
  if (base) {
    try { return { source: "dashboard", base, roles: await jafDashRoles(base), problem: "" }; }
    catch (e) { return { source: null, base, roles: [], problem: `The dashboard is running but did not give the roles: ${e.message}` }; }
  }
  const root = await jafRoot(ask);
  if (root && root !== "needs-permission") {
    try { return { source: "folder", base: null, roles: await jafRoles(root), problem: "" }; }
    catch (e) { return { source: null, base: null, roles: [], problem: `Dashboard not running, and the folder could not be read: ${e.message}` }; }
  }
  return { source: null, base: null, roles: [], needsPermission: root === "needs-permission",
    problem: "Dashboard not reachable. Start it (cc start), or set its address in Options." };
}

// One line per thing that can be wrong, for the side-panel health check.
async function jafHealth() {
  const base = await jafBase();
  if (!base) return [{ ok: false, text: "Dashboard not reachable. Start it with `cc start`, or set its address in Options." }];
  const out = [{ ok: true, text: `Dashboard found at ${base}.` }];
  try {
    const h = await (await jafGet(base, "/extension/health")).json();
    out.push(h.profile ? { ok: true, text: "Profile is filled in." } : { ok: false, text: "Profile has no confirmed first name: open Profile on the dashboard and confirm your answers." });
    out.push(h.roles ? { ok: true, text: `${h.roles} open role${h.roles === 1 ? "" : "s"} with a kit.` } : { ok: false, text: "No open role has a kit yet: build one on the dashboard." });
  } catch (e) { out.push({ ok: false, text: e.message }); }
  return out;
}

async function jafRoot(ask) {
  const h = await IDB.get("root");
  if (!h) return null;
  let p = await h.queryPermission({ mode: "read" });
  if (p !== "granted" && ask) p = await h.requestPermission({ mode: "read" });
  return p === "granted" ? h : "needs-permission";
}

// Role kit folders from the last 21 days, newest first: [{date, folder, handle, kit}]
// kit = the folder's kit.json written by the kit builder: {company, aliases, posting_url, apply_url, job_id}.
async function jafRoles(root) {
  const { kitsFolder } = await chrome.storage.local.get("kitsFolder");
  const daily = await root.getDirectoryHandle(kitsFolder || "Daily");
  const cutoff = new Date(Date.now() - 21 * 864e5).toISOString().slice(0, 10);
  const dates = [];
  for await (const [name, h] of daily.entries()) if (h.kind === "directory" && /^\d{4}-\d{2}-\d{2}$/.test(name) && name >= cutoff) dates.push([name, h]);
  dates.sort((a, b) => b[0].localeCompare(a[0]));
  const roles = [];
  for (const [date, dh] of dates) for await (const [folder, h] of dh.entries()) if (h.kind === "directory") roles.push({ date, folder, handle: h, kit: await jafKitJson(h) });
  return roles;
}

async function jafKitJson(dir) {
  try { return JSON.parse(await (await (await dir.getFileHandle("kit.json")).getFile()).text()); } catch { return null; }
}

const jafCompany = (role) => (role && ((role.kit && role.kit.company) || role.folder.split("_")[0].trim())) || "";

// Same posting: same host and one path is a prefix of the other (the apply page extends the posting path).
function jafSamePosting(a, b) {
  try {
    const x = new URL(a), y = new URL(b);
    if (x.hostname.replace(/^www\./, "") !== y.hostname.replace(/^www\./, "")) return false;
    const px = x.pathname.replace(/\/+$/, "").toLowerCase(), py = y.pathname.replace(/\/+$/, "").toLowerCase();
    return px.length > 1 && py.length > 1 && (py.startsWith(px) || px.startsWith(py));
  } catch { return false; }
}

// Best role for a page, -1 if none. Order: same posting URL (kit.json), job id in the URL, then the longest company
// name or alias found in the URL or title. Ties go to the newest kit (roles are newest first).
function jafMatch(roles, url, title) {
  const u = String(url || "").toLowerCase();
  const hay = (u + " " + String(title || "").toLowerCase()).replace(/[^a-z0-9]/g, "");
  const flat = (x) => String(x || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  let best = -1, score = 0;
  roles.forEach((r, i) => {
    const k = r.kit || {};
    let s = 0;
    if ([k.posting_url, k.apply_url].some((x) => x && jafSamePosting(x, url))) s = 1000;
    else if (k.job_id && flat(k.job_id).length >= 4 && flat(u).includes(flat(k.job_id))) s = 900;
    else {
      for (const n of [r.folder.split("_")[0], k.company].map(flat)) if (n.length >= 3 && hay.includes(n)) s = Math.max(s, n.length);
      for (const n of (k.aliases || []).map(flat)) if (n.length >= 2 && hay.includes(n)) s = Math.max(s, n.length);
    }
    if (s > score) { score = s; best = i; }
  });
  return best;
}

// Why a role matched this page, in words (shown next to the kit name).
function jafWhy(r, url, title) {
  const k = r.kit || {};
  const flat = (x) => String(x || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  if ([k.posting_url, k.apply_url].some((x) => x && jafSamePosting(x, url))) return "same job link as the dashboard";
  if (k.job_id && flat(k.job_id).length >= 4 && flat(url).includes(flat(k.job_id))) return "job number in the link";
  return "company name in the page";
}

// Fill log (E3): what happened on each page, labels only, never values. Last 200 entries, local to this Chrome.
async function jafLog(entry) {
  const { log = [] } = await chrome.storage.local.get("log");
  log.push({ at: new Date().toISOString(), ...entry });
  await chrome.storage.local.set({ log: log.slice(-200) });
}
function jafLogEntry(url, kit, results) {
  let path = "";
  try { const x = new URL(url); path = x.hostname + x.pathname; } catch {}
  return { page: path, kit: kit || "", systems: results.map((x) => x.system),
    filled: results.reduce((a, x) => a + x.filled.length, 0),
    review: results.flatMap((x) => (x.review || []).map((f) => f.field.slice(0, 120))),
    flagged: results.flatMap((x) => x.flagged.map((f) => `${f.field.slice(0, 120)} :: ${f.why.slice(0, 120)}`)),
    unknown: results.flatMap((x) => x.unknown || []), errors: results.flatMap((x) => x.errors), resume: results.map((x) => x.resume).find((s) => s.startsWith("attached")) || "" };
}

async function jafResume(role) {
  if (role.key) {
    const base = await jafBase();
    if (!base) return null;
    let r;
    try { r = await jafGet(base, "/extension/resume/" + encodeURIComponent(role.key)); } catch { return null; }
    const buf = new Uint8Array(await r.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    return { name: r.headers.get("X-Resume-Name") || role.resumeName || "Resume.pdf", folder: role.rel, b64: btoa(bin) };
  }
  let fh = null;
  for await (const [n, h] of role.handle.entries()) if (h.kind === "file" && /resume.*\.pdf$/i.test(n)) { fh = h; break; }
  if (!fh) return null;
  const f = await fh.getFile();
  const buf = new Uint8Array(await f.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { name: f.name, folder: `${role.date}\\${role.folder}`, b64: btoa(bin) };
}

// Profile answers: from the dashboard (always current), else the profile.json bundled with the extension.
// -> the answers; `missing` (on the result) lists the profile fields still blank.
async function jafProfile() {
  const base = await jafBase();
  if (base) {
    try {
      const j = await (await jafGet(base, "/extension/profile")).json();
      if (j.profile && j.profile.firstName) return Object.assign(j.profile, { _missing: j.missing });
    } catch {}
  }
  const r = await fetch(chrome.runtime.getURL("profile.json")).catch(() => null);
  if (!r || !r.ok) throw new Error("Dashboard not reachable, so there are no profile answers. Start it (cc start), or set its address in Options.");
  return r.json();
}

// Tell the dashboard the role is applied. Only called from a click on "Mark applied".
async function jafMarkApplied(key) {
  const base = await jafBase();
  if (!base) throw new Error("Dashboard not reachable: mark it applied there.");
  const r = await fetch(base + "/extension/applied", { method: "POST", headers: { "Content-Type": "application/json", "X-CC-Extension": "1" }, body: JSON.stringify({ key }) });
  if (!r.ok) { let m = ""; try { m = (await r.json()).error; } catch {} throw new Error(m || `dashboard answered ${r.status}`); }
}

// Inject the engine into every frame of the tab and fill. Returns the per-frame results that did something.
// Falls back to the top frame only when Chrome refuses some frames (sites opened with a one-time grant).
async function jafFill(tabId, args) {
  let target = { tabId, allFrames: true };
  try { await chrome.scripting.executeScript({ target, files: JAF_SCRIPTS }); }
  catch (e) { target = { tabId }; await chrome.scripting.executeScript({ target, files: JAF_SCRIPTS }); }
  const out = await chrome.scripting.executeScript({ target, func: (a) => window.JAF.run(a), args: [args] });
  return out.map((x) => x.result).filter((x) => x && (x.filled.length || x.flagged.length || x.resume !== "not attached"));
}

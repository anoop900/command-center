// Side panel: pick the role kit, fill the active tab, show kit answers to copy.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const KNOWN = /(myworkday(jobs|site)?\.com|greenhouse\.io|successfactors\.(com|eu)|sapsf\.(com|eu)|jobs\.lever\.co|jobs\.ashbyhq\.com)$/i;
let roles = [];
let tab = null; // kept current so Fill can ask for site access before any await (Chrome needs the click)

async function refreshTab() { [tab] = await chrome.tabs.query({ active: true, currentWindow: true }); }

let base = null;
async function loadRoles(ask = false) {
  const box = $("folderBox");
  try {
    const got = await jafLoad(ask);
    base = got.base; roles = got.roles;
    if (!got.source) {
      box.innerHTML = `<span class="warn">${esc(got.problem)}</span> ` + (got.needsPermission ? `<button id="grant">Allow reading the Job Hunting folder</button> ` : "") + `<a href="#" id="setup">Open setup</a>`;
      if (got.needsPermission) $("grant").onclick = () => loadRoles(true);
      $("setup").onclick = () => chrome.runtime.openOptionsPage();
      await renderRoles();
      return;
    }
    box.innerHTML = got.source === "dashboard"
      ? `<span class="ok">Dashboard connected.</span> ${roles.length} open role${roles.length === 1 ? "" : "s"} with a kit.`
      : `<span class="ok">Folder OK</span> (dashboard not running). ${roles.length} role kits from the last 21 days.`;
    await renderRoles();
  } catch (e) { box.innerHTML = `<span class="warn">Problem: ${esc(e.message)}</span>`; }
}

async function renderRoles() {
  await refreshTab();
  const best = tab ? jafMatch(roles, tab.url, tab.title) : -1;
  $("role").innerHTML = `<option value="">(no kit: profile answers only)</option>` +
    roles.map((r, i) => `<option value="${i}" ${i === best ? "selected" : ""}>${esc(r.date)} · ${esc(r.folder.replace(/_/g, " "))}</option>`).join("");
  await renderKit();
}

async function readText(dir, name) { try { return await (await (await dir.getFileHandle(name)).getFile()).text(); } catch { return null; } }

async function renderKit() {
  const r = roles[$("role").value];
  const kit = $("kit");
  $("mark").hidden = !(r && r.key && !r.applied);
  if (!r) { kit.textContent = "No kit selected."; return; }
  const md = r.handle ? await readText(r.handle, "Application_Kit.md") : await fetch(`${base}/kits/${r.rel.split(/[\/]/).map(encodeURIComponent).join("/")}/Application_Kit.md`).then((x) => (x.ok ? x.text() : null), () => null);
  if (!md) { kit.textContent = "This folder has no Application_Kit.md."; return; }
  const parts = md.split(/^##\s+/m).slice(1).map((s) => { const i = s.indexOf("\n"); return { h: s.slice(0, i).trim(), body: s.slice(i + 1).trim() }; });
  kit.innerHTML = parts.map((p, i) => `<details ${/why|screen|question/i.test(p.h) ? "open" : ""}><summary><span>${esc(p.h)}</span><button data-i="${i}">Copy</button></summary><pre>${esc(p.body)}</pre></details>`).join("");
  kit.querySelectorAll("button[data-i]").forEach((b) => (b.onclick = (e) => { e.preventDefault(); navigator.clipboard.writeText(parts[b.dataset.i].body); b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 1200); }));
}

function fill() {
  const out = $("result");
  if (!tab || !/^https:/.test(tab.url || "")) { out.innerHTML = `<p class="warn">Open the application page in this window first.</p>`; refreshTab(); return; }
  const host = new URL(tab.url).hostname;
  // Supported systems are pre-approved in the manifest. Anything else: ask now, synchronously inside the click.
  const access = KNOWN.test(host) ? Promise.resolve(true) : chrome.permissions.request({ origins: [`https://${host}/*`] });
  out.textContent = "Filling…";
  access.then(async (ok) => {
    if (!ok) { out.innerHTML = `<p class="warn">Site access refused for ${esc(host)}.</p>`; return; }
    const profile = await jafProfile();
    let resume = null;
    const r = roles[$("role").value];
    if ($("attach").checked && r) {
      resume = await jafResume(r);
      if (!resume) { out.innerHTML = `<p class="warn">No resume PDF in ${esc(r.folder)}.</p>`; return; }
    }
    const gaps = (profile._missing || []).filter((m) => ["firstName", "lastName", "email", "phone", "address1", "city", "province", "postalCode", "country", "citizenship", "education", "school"].includes(m.key)).map((m) => m.label);
    const results = await jafFill(tab.id, { profile, resume, opts: { heardRe: $("heard").value, heard: $("heard").selectedOptions[0].text, company: jafCompany(r) } });
    jafLog(jafLogEntry(tab.url, r ? r.folder : "", results));
    show(results);
    const un = results.flatMap((x) => x.unknown || []).slice(0, 15);
    out.insertAdjacentHTML("beforeend", (r ? `<p class="note">Kit: ${esc(r.folder)} (${esc(jafWhy(r, tab.url, tab.title))}).</p>` : "") +
      (gaps.length ? `<p class="warn">Your profile has no answer for: ${esc(gaps.join(", "))}. Add them on the dashboard Profile page.</p>` : "") +
      (un.length ? `<p class="warn">Not recognised (answer yourself): ${esc(un.map((x) => x.slice(0, 50)).join("; "))}</p>` : ""));
  }).catch((e) => { out.innerHTML = `<p class="warn">Could not fill: ${esc(e.message || e)}</p>`; });
}

function show(rs) {
  const out = $("result");
  if (!rs.length) { out.innerHTML = `<p class="warn">Found nothing to fill on this page. Open the application form step (not the job posting).</p>`; return; }
  out.innerHTML = rs.map((x) => `<div class="box">
    <b>${esc(x.system)}</b>: <span class="ok">${x.filled.length} filled</span>${(x.review || []).length ? `, <span class="rev">${x.review.length} to check</span>` : ""}, <span class="warn">${x.flagged.length} for you</span><br>
    Resume: ${esc(x.resume)}
    ${(x.review || []).length ? `<ul>${x.review.map((f) => `<li class="rev">${esc(f.field.slice(0, 80))}: ${esc(f.why)}</li>`).join("")}</ul>` : ""}
    ${x.flagged.length ? `<ul>${x.flagged.map((f) => `<li class="warn">${esc(f.field.slice(0, 80))}: ${esc(f.why)}</li>`).join("")}</ul>` : ""}
    ${x.errors.length ? `<p class="note">Errors: ${esc(x.errors.join("; "))}</p>` : ""}
    <p class="note">Green = filled, yellow = filled, check it, orange = yours. Check the page, click Next yourself, then Fill again.</p></div>`).join("");
}

$("fill").onclick = fill;
$("check").onclick = async () => {
  $("result").textContent = "Checking…";
  $("result").innerHTML = (await jafHealth()).map((x) => `<p class="${x.ok ? "ok" : "warn"}">${x.ok ? "OK" : "Fix"}: ${esc(x.text)}</p>`).join("");
};
$("mark").onclick = async () => {
  const r = roles[$("role").value];
  try { await jafMarkApplied(r.key); r.applied = true; $("mark").hidden = true; $("result").innerHTML = `<p class="ok">Marked ${esc(jafCompany(r))} applied on the dashboard.</p>`; }
  catch (e) { $("result").innerHTML = `<p class="warn">${esc(e.message)}</p>`; }
};
$("role").onchange = renderKit;
$("heard").onchange = () => chrome.storage.local.set({ heard: { re: $("heard").value, text: $("heard").selectedOptions[0].text } });
$("log").onclick = async () => {
  const { log = [] } = await chrome.storage.local.get("log");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(log, null, 1)], { type: "application/json" }));
  a.download = `fill_log_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
};
$("dash").onclick = async () => { const { dash } = await chrome.storage.local.get("dash"); dash ? chrome.tabs.create({ url: dash }) : chrome.runtime.openOptionsPage(); };
chrome.tabs.onActivated.addListener(() => roles.length ? renderRoles() : refreshTab());
chrome.tabs.onUpdated.addListener((id, info) => { if (info.status === "complete" && tab && id === tab.id) roles.length ? renderRoles() : refreshTab(); });
refreshTab().then(() => loadRoles());

// On application pages: a small "Fill" button that names the matched kit. One click fills this page.
// It never clicks Next or Submit; the engine refuses those clicks. Also injected on any site by the right-click
// menu or Alt+Shift+F. After a "thank you for applying" page it offers to open the dashboard.
(() => {
  if (window.top !== window || document.getElementById("jaf-host")) return;
  const host = document.createElement("div");
  host.id = "jaf-host";
  host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
    button{font:600 13px system-ui,sans-serif;background:#1f6feb;color:#fff;border:0;border-radius:20px;padding:9px 14px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25)}
    button:disabled{opacity:.6;cursor:wait}
    button.sec{background:#fff;color:#1f6feb;border:1px solid #1f6feb;box-shadow:none;padding:5px 10px;margin-top:6px}
    .box{display:none;max-width:340px;max-height:300px;overflow:auto;margin-bottom:8px;background:#fff;color:#1d1d1f;border:1px solid #d9d9de;border-radius:8px;padding:8px 10px;font:12px/1.4 system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.2)}
    .ok{color:#2e7d4f}.warn{color:#b35f00}.rev{color:#8a6a00}ul{margin:4px 0;padding-left:16px}.x{float:right;cursor:pointer;color:#888}
  </style><div class="box" id="box"></div><button id="b" title="Job Apply Filler: fills this page, never submits">Fill this page</button>`;
  const btn = root.getElementById("b"), box = root.getElementById("box");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const show = (html) => { box.innerHTML = `<span class="x" id="x">✕</span>` + html; box.style.display = "block"; root.getElementById("x").onclick = () => (box.style.display = "none"); };
  let label = "Fill this page", kit = "", key = "";

  const ask = (msg) => new Promise((res) => { try { chrome.runtime.sendMessage(msg, (r) => res(chrome.runtime.lastError ? null : r)); } catch { res(null); } });
  ask({ type: "jaf-match" }).then((r) => {
    if (r && r.company) { kit = r.kit; key = r.key || ""; label = `Fill · ${r.company} kit`; btn.textContent = label; btn.title = `Kit: ${r.kit}. Fills this page, never submits.`; }
  });

  const run = () => {
    btn.disabled = true; btn.textContent = "Filling…";
    chrome.runtime.sendMessage({ type: "jaf-fill" }, (r) => {
      btn.disabled = false; btn.textContent = label;
      if (chrome.runtime.lastError || !r) return show(`<span class="warn">Extension did not answer. Reload the page and try again.</span>`);
      if (r.error) return show(`<span class="warn">${esc(r.error)}</span>`);
      const f = r.results.reduce((a, x) => a + x.filled.length, 0);
      const flagged = r.results.flatMap((x) => x.flagged);
      const review = r.results.flatMap((x) => x.review || []);
      const res = r.results.map((x) => x.resume).find((s) => s.startsWith("attached")) || r.results[0]?.resume || "not attached";
      show(`<b>${f ? `<span class="ok">${f} filled</span>` : "Nothing to fill here"}</b>${review.length ? `, <span class="rev">${review.length} to check</span>` : ""}, <span class="warn">${flagged.length} for you</span><br>
        Kit: ${esc(r.kit || "none")} · Resume: ${esc(res)}
        ${r.unknown && r.unknown.length ? `<div class="warn">Not recognised (answer yourself): ${r.unknown.map((x) => esc(x.slice(0, 50))).join("; ")}</div>` : ""}
        ${r.notes.map((n) => `<div class="warn">${esc(n)}</div>`).join("")}
        ${review.length ? `<ul>${review.map((x) => `<li class="rev">${esc(x.field.slice(0, 60))}: ${esc(x.why)}</li>`).join("")}</ul>` : ""}
        ${flagged.length ? `<ul>${flagged.map((x) => `<li>${esc(x.field.slice(0, 60))}: ${esc(x.why)}</li>`).join("")}</ul>` : ""}
        <div>Green = filled, yellow = filled, check it, orange = yours. Click Next yourself, then Fill again.</div>`);
    });
  };
  btn.onclick = run;
  chrome.runtime.onMessage.addListener((m) => { if (m && m.type === "jaf-run") run(); });

  // Submitted? Offer the dashboard once. Checks the visible text at most every 2 seconds after page changes.
  const DONE = /thank you for (applying|your application)|application (has been |was )?(submitted|received)|we('ve| have) received your application/i;
  let seen = false, t = 0;
  const check = () => {
    if (seen || !document.body || document.body.innerText.length > 200000 || !DONE.test(document.body.innerText)) return;
    seen = true;
    ask({ type: "jaf-submitted", page: location.hostname + location.pathname, kit });
    show(`<b class="ok">Looks submitted.</b><br>Mark it applied here, or on the dashboard (the confirmation email also does it on the next run).<br>${key ? `<button class="sec" id="mark">Mark applied</button> ` : ""}<button class="sec" id="dash">Open dashboard</button>`);
    root.getElementById("dash").onclick = () => ask({ type: "jaf-dash" });
    const mk = root.getElementById("mark");
    if (mk) mk.onclick = () => { mk.disabled = true; ask({ type: "jaf-applied", key }).then((r) => { mk.textContent = r && r.ok ? "Marked applied" : "Could not: " + ((r && r.error) || "no answer"); }); };
  };
  new MutationObserver(() => { if (!seen && !t) t = setTimeout(() => { t = 0; check(); }, 2000); }).observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(check, 1500);
  document.documentElement.appendChild(host);
})();

// Job Apply Filler: shared fill engine. Injected into the application page on demand.
// Fills what the profile answers, flags what it must not answer, and NEVER clicks Next or Submit.
(() => {
  if (window.JAF && window.JAF.version) return;
  const JAF = (window.JAF = window.JAF || {});
  JAF.version = "0.3.0";
  JAF.adapters = JAF.adapters || {};

  const norm = (s) => (s || "").replace(/\s+/g, " ").replace(/[*:✱]+\s*$/, "").trim();
  const camel = (s) => (s || "").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[-_.]+/g, " ");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const visible = (el) => !!(el && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden");
  const US = /united states|\bu\.?s\.?a?\b|america/i;

  // ---- Rules: first match wins. Label text is matched first, then the element's id/name hints.
  // kind: flag = never fill, tell the user; skip = leave silently; text = type a value; choice = pick an option.
  // Every value traces to Candidate_Profile.md section 9 via profile.json.
  const DECLINE = /decline|prefer not|do not (wish|want)|don.?t (wish|want)|choose not|not to (say|answer|disclose|identify|self)|not wish|no answer/i;
  const rx = (s) => String(s || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Home country, province and phone code come from the profile (generic; defaults keep older profiles working).
  JAF.rules = (p, o) => [
    { re: /salary|compensation|pay (range|expectation)|desired pay|expected (pay|rate|salary)|hourly rate|rate expectation/i, kind: "flag", why: "Salary: left blank by rule. If required, decide yourself." },
    // v0.3 (the user's answers, Oct 9): previously applied = No; previously worked = from the former-employer list.
    { re: /previously (applied|submitted)|applied (to|with|for|at) .*(before|previously|in the past)|have you (ever )?applied/i, kind: "choice", opt: /^no\b/i, val: p.previouslyApplied },
    { re: /re-?hire|eligible for rehire/i, kind: "rehire" },
    { re: /previously (worked|been employed|employed)|former (employee|employer)|(worked|been employed|employed) (for|at|with|by) .*(before|previously|in the past)|ever (worked|been employed)|current or former|are you a current/i, kind: "worked" },
    { re: /password|signature|date of birth|birth ?date|\bage\b|18 years|criminal|convict|background check|non.?compete|relative|family member|referr(ed|al) by|employee referral|reference/i, kind: "flag", why: "Answer yourself (not in profile section 9 or needs the tracker)." },
    { re: /gender|\bsex\b|\brace\b|ethnic|indigenous|aboriginal|first nations|disab|veteran|military status|lgbt|sexual orientation|pronoun|visible minorit|hispanic|latin[oa]/i, kind: "choice", opt: DECLINE, why: "Self-identification: decline" },
    { re: /preferred (first |last )?name|middle name|address line 2|apartment|suite|\bunit\b|extension|\bext\b/i, kind: "skip" },
    { re: /highest (level of )?education|education level|level of education|highest degree/i, kind: "choice", opt: /diploma|college|associate|post.?secondary/i, val: p.education },
    { re: /bachelor|university degree|undergraduate degree|post.?secondary degree/i, kind: "choice", opt: /^yes\b/i, val: p.bachelorYesNo, review: "Bachelor's question answered by your rule (degree or equivalent). Check the wording." },
    { re: /master|doctor|ph\.?d/i, kind: "flag", why: "Graduate degree question: answer yourself." },
    { re: /degree type|type of degree|^degree$|degree (obtained|earned|level)/i, kind: "choice", opt: /diploma|associate|college|^other/i, val: "Diploma" },
    { re: /field of study|area of study|program of study|^(your )?major$|^discipline$/i, kind: "choice", opt: /computer eng|engineering|computer/i, val: p.fieldOfStudy },
    { re: /^(school|university|institution|college)( name)?$|school name|name of (the )?(school|university|institution|college)|school or university|university ?\/ ?college/i, kind: "school" },
    { re: /first name|given name|legal first|firstname/i, kind: "text", val: p.firstName },
    { re: /last name|family name|surname|lastname/i, kind: "text", val: p.lastName },
    { re: /^(your |full |legal )?name$|full name|legal name/i, kind: "text", val: `${p.firstName} ${p.lastName}` },
    { re: /e-?mail/i, kind: "text", val: p.email },
    { re: /(phone|device) type/i, kind: "flag", why: "Phone device type: pick one yourself." },
    { re: /(country )?phone code|phone country|country code/i, kind: "choice", opt: new RegExp(`${rx(p.country)}.*\\+ ?${p.phoneCountryCode || 1}\\b|\\+ ?${p.phoneCountryCode || 1}\\b.*${rx(p.country)}|^${rx(p.country)}`, "i"), val: p.country },
    { re: /phone|mobile|telephone|cell/i, kind: "text", val: p.phone },
    { re: /address( line)? ?1|street|^address$|home address/i, kind: "text", val: p.address1 },
    { re: /\bcity\b|town|municipality/i, kind: "text", val: p.city },
    { re: /^(current )?location$|where are you (currently )?(located|based)/i, kind: "choice", val: p.city, opt: new RegExp(String(p.city).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ".*" + String(p.province || ""), "i") },
    { re: /postal|zip/i, kind: "text", val: p.postalCode },
    { re: /province|\bstate\b|region|territory/i, kind: "choice", opt: new RegExp(`^${rx(p.province)}$|^${rx(p.provinceCode)}$|^${rx(p.provinceCode)} - ${rx(p.province)}|${rx(p.province)}`, "i"), val: p.province },
    { re: /country/i, kind: "choice", opt: new RegExp(`^${rx(p.country)}$|^${rx(p.country)} `, "i"), val: p.country, notIf: /citizen/i },
    { re: /linked ?in/i, kind: "text", val: p.linkedin },
    { re: /website|portfolio|personal (site|url)|blog|other url/i, kind: "text", val: p.website },
    { re: /(authori[sz]ed|entitled|eligible|permitted|legally able|right) to work|work authori[sz]ation|legally work/i, kind: "choice", opt: /^yes\b/i, val: p.authorizedHome || p.authorizedCanada, usFlag: true, id: "auth" },
    { re: /sponsor|work permit|visa/i, kind: "choice", opt: /^no\b/i, val: p.sponsorshipHome || p.sponsorshipCanada, usFlag: true, id: "sponsor" },
    { re: /citizen|nationality/i, kind: "citizen" },
    { re: /relocat/i, kind: "choice", opt: /^yes\b/i, val: p.relocate },
    { re: /start date|notice period|available to start|earliest|when can you start|availability to start/i, kind: "text", val: p.startDate, dateFlag: true },
    { re: /clearance/i, kind: "choice", opt: /^yes\b/i, val: p.clearance },
    { re: /french/i, kind: "choice", opt: /^no\b/i, val: p.frenchFluent },
    { re: /english/i, kind: "choice", opt: /^yes\b/i, val: p.englishFluent },
    { re: /hear about|how did you (find|learn|hear)|source of (application|referral)|where did you (find|see)|^source$/i, kind: "choice", opt: o.heardRe, val: o.heard },
    { re: /cover letter|why (do you want|are you interested|this (company|role))|tell us|additional information/i, kind: "flag", why: "Free text: copy from the kit in the side panel." },
  ];

  // ---- Label discovery
  JAF.labelOf = (el) => {
    const parts = [];
    const lb = el.getAttribute("aria-labelledby");
    if (lb) parts.push(lb.split(/\s+/).map((id) => document.getElementById(id)?.innerText || "").join(" "));
    if (el.getAttribute("aria-label")) parts.push(el.getAttribute("aria-label"));
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) parts.push(l.innerText);
    }
    const wrap = el.closest("label");
    if (wrap) parts.push(wrap.innerText);
    for (const a of JAF.labelHooks || []) parts.push(a(el) || "");
    if (!parts.join("").trim()) {
      const fs = el.closest("fieldset");
      if (fs && fs.querySelector("legend")) parts.push(fs.querySelector("legend").innerText);
    }
    if (!parts.join("").trim()) {
      let n = el.parentElement;
      for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
        const t = n.querySelector("label, legend, [class*='label' i], [class*='question' i]");
        if (t && t !== el && t.innerText.trim()) { parts.push(t.innerText); break; }
      }
    }
    const uniq = [];
    for (const t of parts.map(norm).filter(Boolean)) if (!uniq.some((u) => u.includes(t) || t.includes(u))) uniq.push(t);
    return uniq.join(" | ").slice(0, 300);
  };
  JAF.hintOf = (el) => norm(camel([el.getAttribute("data-automation-id"), el.name, el.id, el.getAttribute("autocomplete"), el.placeholder].filter(Boolean).join(" ")));

  // Turns the special kinds into a concrete rule for this field, or a flag.
  const words = (s) => " " + String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim() + " ";
  const GENERIC = /\b(bank|financial|group|management|canada|technologies|technology|inc|ltd|limited|corp|corporation|company|wealth)\b/g;
  JAF.workedHere = (p, company) => {
    if (!company) return null;
    const co = words(company);
    return (p.formerEmployers || []).some((e) => {
      const core = words(e).replace(GENERIC, " ").trim();
      return core && co.includes(" " + core + " ");
    });
  };
  // The other country the user may work in through a visa route (default: the US, for a TN-style setup).
  const routeRe = (p) => (p.routeCountryRe ? new RegExp(p.routeCountryRe, "i") : US);
  JAF.resolve = (r, label, f, p, o) => {
    const yn = (v, why) => ({ kind: "choice", opt: new RegExp("^" + v.toLowerCase() + "\\b", "i"), val: v, review: why });
    if (r.kind === "worked") {
      const w = JAF.workedHere(p, o.company);
      if (w === null) return { kind: "flag", why: "Previously worked here: no kit matched, so the company is unknown. Pick the kit in the side panel." };
      return yn(w ? "Yes" : "No");
    }
    if (r.kind === "rehire") {
      const w = JAF.workedHere(p, o.company);
      const k = w && Object.keys(p.rehire || {}).find((c) => words(o.company).includes(words(c)));
      return k ? { kind: "choice", opt: new RegExp(p.rehire[k], "i"), val: p.rehire[k] } : { kind: "flag", why: "Rehire question: answer yourself." };
    }
    if (r.kind === "citizen") {
      if (routeRe(p).test(label)) return yn("No");
      if (p.citizenship && new RegExp(rx(String(p.citizenship).slice(0, 5)), "i").test(label)) return yn("Yes");
      if (/(country|nationality|which|what)/i.test(label) || f.type !== "radio") return { kind: "choice", opt: new RegExp("^" + p.citizenship, "i"), val: p.citizenship };
      return { kind: "flag", why: "Citizenship question: check the wording and answer yourself." };
    }
    if (r.kind === "school") {
      const alt = [p.school, p.schoolAlt].filter(Boolean).map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
      if (f.type === "text") return { kind: "text", val: p.schoolOtherText };
      return { kind: "choice", opt: new RegExp(alt || "^other", "i"), fallback: /^other\b/i, val: p.school, review: "School: if 'Other' was picked, type the school name in the next box." };
    }
    if (r.usFlag && routeRe(p).test(label)) {
      if (r.id === "auth") return f.type === "text" ? { kind: "text", val: p.usAuthText, review: "US work authorization: check the wording." } : yn(p.usAuthorizedNow || "No", "US: answered 'not currently authorized'. Check the wording.");
      return { kind: "flag", why: `US sponsorship: answer yourself (check with lawyer). Suggested wording: "${p.usAuthText}".` };
    }
    return r;
  };

  JAF.match = (label, hint, rules) => {
    for (const r of rules) {
      if (r.notIf && r.notIf.test(label)) continue;
      if (r.re.test(label)) return r;
    }
    if (!label) for (const r of rules) if (r.re.test(hint)) return r;
    // A label exists but matched nothing: try the hint only for plain identity fields.
    if (!/\?/.test(label)) for (const r of rules) if (r.kind === "text" && r.re.test(hint)) return r;
    return null;
  };

  // ---- Value setters that React/Angular forms register
  JAF.setValue = (el, v) => {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    el.focus();
    setter.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  };

  // Clicks only things that open or pick an option. Refuses anything that could advance or submit.
  const DANGER = /submit|apply|next|continue|save|review|sign ?in|log ?in|create account|finish|send|done/i;
  JAF.safeClick = (el) => {
    const role = el.getAttribute("role") || "";
    const opener = el.getAttribute("aria-haspopup") || role === "option" || role === "combobox" || el.type === "radio" || el.type === "checkbox";
    if (!opener && DANGER.test(el.innerText || el.value || el.getAttribute("aria-label") || "")) throw new Error("refused click: " + (el.innerText || el.value));
    if (el.type === "submit" && !el.getAttribute("aria-haspopup")) throw new Error("refused submit-type click");
    el.click();
  };

  const mark = (el, color) => { try { el.style.outline = `3px solid ${color}`; el.style.outlineOffset = "1px"; } catch (_) {} };
  JAF.markOk = (el) => mark(el, "#2e9d5b");
  JAF.markReview = (el) => mark(el, "#d4a106");
  JAF.markFlag = (el) => mark(el, "#e8890c");

  // ---- Field collection
  JAF.collect = () => {
    const out = [];
    const seenRadio = new Set();
    const els = document.querySelectorAll("input, textarea, select, button[aria-haspopup='listbox'], [role='combobox']:not(input)");
    for (const el of els) {
      if (!visible(el) && el.type !== "radio" && el.type !== "file") continue;
      if (el.disabled || el.readOnly) continue;
      if (el.tagName === "BUTTON" || (el.getAttribute("role") === "combobox" && el.tagName !== "INPUT")) { out.push({ el, type: "dropdown" }); continue; }
      const t = (el.type || "").toLowerCase();
      if (["hidden", "submit", "button", "image", "reset", "checkbox", "password"].includes(t)) continue;
      if (t === "file") { out.push({ el, type: "file" }); continue; }
      if (t === "radio") {
        const group = el.closest("[role='radiogroup'], fieldset") || el.form || document;
        const key = el.name ? "n:" + el.name : group;
        if (seenRadio.has(key)) continue;
        seenRadio.add(key);
        const radios = [...(el.name ? document.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`) : group.querySelectorAll("input[type=radio]"))];
        out.push({ el, type: "radio", radios, group });
        continue;
      }
      if (el.tagName === "SELECT") { out.push({ el, type: "select" }); continue; }
      if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") || el.getAttribute("data-automation-id") === "searchBox") { out.push({ el, type: "combo" }); continue; }
      out.push({ el, type: t === "date" ? "date" : "text" });
    }
    return out;
  };

  const radioText = (r) => norm((r.id && document.querySelector(`label[for="${CSS.escape(r.id)}"]`)?.innerText) || r.closest("label")?.innerText || r.getAttribute("aria-label") || r.value);
  const groupLabel = (f) => {
    const g = f.group && f.group !== document ? f.group : null;
    if (g) {
      const lb = g.getAttribute && g.getAttribute("aria-labelledby");
      if (lb) return norm(document.getElementById(lb)?.innerText);
      const lg = g.querySelector && g.querySelector("legend");
      if (lg) return norm(lg.innerText);
    }
    for (const h of JAF.labelHooks || []) { const t = h(f.el); if (t) return norm(t); }
    let n = f.radios[0].parentElement;
    for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
      const t = [...n.childNodes].map((c) => (c.nodeType === 3 ? c.textContent : c.matches && !c.querySelector("input") && !c.matches("input,label") ? c.innerText : "")).join(" ");
      if (norm(t)) return norm(t);
    }
    return "";
  };

  // ---- Main run
  JAF.run = async (args) => {
    const { profile, opts = {}, resume } = args;
    opts.heardRe = new RegExp(opts.heardRe || "job board", "i");
    const rules = JAF.rules(profile, opts);
    const adapter = Object.values(JAF.adapters).find((a) => a.detect()) || null;
    const res = { system: adapter ? adapter.name : "generic", filled: [], flagged: [], review: [], unknown: [], resume: "not attached", errors: [] };
    const fields = (adapter && adapter.collect ? adapter.collect() : null) || JAF.collect();
    for (const f of fields) {
      try {
        if (f.type === "file") { await handleFile(f, resume, adapter, res); continue; }
        const label = f.type === "radio" ? groupLabel(f) : JAF.labelOf(f.el);
        const hint = JAF.hintOf(f.el);
        const m = JAF.match(label, hint, rules);
        const name = label || hint || "(unlabelled)";
        if (!m) { if (label && res.unknown.length < 40) res.unknown.push(label.slice(0, 120)); continue; }
        if (m.kind === "skip") continue;
        const r = JAF.resolve(m, label, f, profile, opts);
        if (r.kind === "flag") { JAF.markFlag(f.el); res.flagged.push({ field: name, why: r.why }); continue; }
        if (r.dateFlag && f.type === "date") { JAF.markFlag(f.el); res.flagged.push({ field: name, why: "Start date as a date: pick a date yourself." }); continue; }
        let ok = await fillOne(f, r, adapter);
        if (!ok && r.fallback) ok = await fillOne(f, { ...r, opt: r.fallback }, adapter);
        if (ok === "kept") continue;
        if (ok === "nostick") { JAF.markFlag(f.el); res.flagged.push({ field: name, why: "The page did not accept the value. Type it yourself." }); continue; }
        if (ok && r.review) { JAF.markReview(f.el); res.filled.push(name); res.review.push({ field: name, why: r.review }); }
        else if (ok) { JAF.markOk(f.el); res.filled.push(name); }
        else { JAF.markFlag(f.el); res.flagged.push({ field: name, why: r.why ? r.why + " (no matching option)" : "No matching option. Answer yourself." }); }
      } catch (e) {
        res.errors.push(String(e.message || e));
      }
    }
    if (adapter && adapter.extra) { try { await adapter.extra(res); } catch (e) { res.errors.push(String(e.message || e)); } }
    // Required fields still empty
    document.querySelectorAll("[aria-required='true'], [required]").forEach((el) => {
      if (visible(el) && !el.value && el.tagName !== "DIV" && el.type !== "radio" && el.type !== "file" && !el.style.outline) {
        JAF.markFlag(el);
        res.flagged.push({ field: JAF.labelOf(el) || JAF.hintOf(el), why: "Required and still empty." });
      }
    });
    return res;
  };

  async function fillOne(f, r, adapter) {
    const el = f.el;
    if (f.type === "text" || f.type === "date") {
      if (r.kind === "choice" && !r.val) return false;
      if (el.value && el.value.trim()) return "kept"; // never overwrite what is there
      if (!r.val) return false;
      JAF.setValue(el, r.val);
      await sleep(60);
      return el.value && el.value.trim() ? true : "nostick";
    }
    if (f.type === "select") {
      const cur = el.options[el.selectedIndex];
      if (cur && el.selectedIndex > 0 && cur.value) return "kept";
      const opt = [...el.options].find((o) => o.value && (r.opt ? r.opt.test(norm(o.text)) : norm(o.text) === r.val));
      if (!opt) return false;
      JAF.setValue(el, opt.value);
      await sleep(30);
      return el.value === opt.value ? true : "nostick";
    }
    if (f.type === "radio") {
      if (f.radios.some((x) => x.checked)) return "kept";
      const want = r.opt || new RegExp("^" + (r.val || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
      const pick = f.radios.find((x) => want.test(radioText(x)));
      if (!pick) return false;
      JAF.safeClick(pick);
      return true;
    }
    if ((f.type === "dropdown" || f.type === "combo" || f.type === "yesno") && adapter && adapter.choose) return adapter.choose(f, r);
    if (f.type === "combo") { if (el.value) return "kept"; JAF.setValue(el, r.val); return true; }
    return false;
  }

  async function handleFile(f, resume, adapter, res) {
    if (!resume) return;
    const label = JAF.labelOf(f.el) + " " + JAF.hintOf(f.el);
    const isResume = (adapter && adapter.isResumeInput && adapter.isResumeInput(f.el)) || /resume|r[ée]sum[ée]|\bcv\b|curriculum/i.test(label);
    if (!isResume || /cover/i.test(label)) return;
    if (res.resume.startsWith("attached")) return;
    if (adapter && adapter.hasFile && adapter.hasFile(f.el)) { res.resume = "already attached (left as is)"; return; }
    const bytes = Uint8Array.from(atob(resume.b64), (c) => c.charCodeAt(0));
    const file = new File([bytes], resume.name, { type: "application/pdf" });
    const dt = new DataTransfer();
    dt.items.add(file);
    f.el.files = dt.files;
    f.el.dispatchEvent(new Event("input", { bubbles: true }));
    f.el.dispatchEvent(new Event("change", { bubbles: true }));
    res.resume = `attached ${resume.name} from ${resume.folder}`;
  }

  JAF.util = { norm, sleep, visible, DECLINE };
})();

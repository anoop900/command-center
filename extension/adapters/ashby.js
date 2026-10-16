// Ashby adapter: jobs.ashbyhq.com/<company>/<id>/application. Labels use for= ids; yes/no questions are two
// buttons ("Yes" / "No") instead of inputs; location is a type-ahead combobox.
(() => {
  const JAF = window.JAF;
  if (!JAF || JAF.adapters.ashby) return;
  const { norm, sleep, visible } = JAF.util;
  const isAshby = () => /ashbyhq\.com$/i.test(location.hostname) || !!document.querySelector("[class*='ashby-application-form']");
  const entry = (el) => el.closest("[class*='application-form-field-entry'], [class*='fieldEntry'], fieldset") || el.parentElement;

  JAF.labelHooks = JAF.labelHooks || [];
  JAF.labelHooks.push((el) => {
    if (!isAshby()) return "";
    const e = entry(el);
    const l = e && e.querySelector("label, legend, [class*='question-title']");
    return l && !l.contains(el) ? l.innerText : "";
  });

  const yesNoGroups = () => [...document.querySelectorAll("div")].filter((d) => {
    const b = [...d.children].filter((c) => c.tagName === "BUTTON");
    return b.length === 2 && /^yes$/i.test(norm(b[0].innerText)) && /^no$/i.test(norm(b[1].innerText)) && visible(d);
  });
  const pressed = (b) => b.getAttribute("aria-pressed") === "true" || /active|selected|checked/i.test(b.className);

  JAF.adapters.ashby = {
    name: "Ashby",
    detect: isAshby,
    collect: () => JAF.collect().concat(yesNoGroups().map((d) => ({ el: d, type: "yesno", buttons: [...d.children].filter((c) => c.tagName === "BUTTON") }))),
    isResumeInput: (el) => /resume/i.test(el.id || "") || /resume|cv/i.test(norm(entry(el)?.innerText || "")),
    hasFile: (el) => /\.(pdf|docx?)\b/i.test(norm(entry(el)?.innerText || "")),
    async choose(f, r) {
      const want = r.opt || new RegExp("^" + String(r.val || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
      if (f.type === "yesno") {
        if (f.buttons.some(pressed)) return "kept";
        const b = f.buttons.find((x) => want.test(norm(x.innerText)));
        if (!b) return false;
        JAF.safeClick(b);
        return true;
      }
      // Type-ahead combobox (location): type, then pick the matching option.
      const el = f.el;
      if (el.value) return "kept";
      if (!r.val) return false;
      JAF.setValue(el, String(r.val));
      let opts = [];
      for (let t = 0; t < 2000 && !opts.length; t += 100) { await sleep(100); opts = [...document.querySelectorAll("[role='option']")].filter(visible); }
      const hit = opts.find((o) => want.test(norm(o.innerText))) || opts.find((o) => norm(o.innerText).toLowerCase().startsWith(String(r.val).toLowerCase()));
      if (!hit) return opts.length ? false : true; // no menu: plain text box, the typed value stands
      JAF.safeClick(hit);
      await sleep(100);
      return true;
    },
  };
})();

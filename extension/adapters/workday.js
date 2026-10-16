// Workday adapter: *.myworkdayjobs.com / *.myworkdaysite.com / wd*.myworkday.com
(() => {
  const JAF = window.JAF;
  if (!JAF || JAF.adapters.workday) return;
  const { norm, sleep, visible, DECLINE } = JAF.util;
  const isWD = () => /myworkday(jobs|site)?\.com$/i.test(location.hostname) || !!document.querySelector("[data-automation-id^='formField-'], [data-automation-id='applyFlowPage']");

  // Workday puts the question text in the formField wrapper's label or legend.
  JAF.labelHooks = JAF.labelHooks || [];
  JAF.labelHooks.push((el) => {
    if (!isWD()) return "";
    const ff = el.closest("[data-automation-id^='formField-']");
    const l = ff && ff.querySelector("label, legend, [data-automation-id='richText']");
    return l ? l.innerText : "";
  });

  const options = () =>
    [...document.querySelectorAll("[role='listbox'] [role='option'], [data-automation-id='promptOption'], [data-automation-id='menuItem']")].filter(visible);

  async function waitOptions(ms = 2000) {
    for (let t = 0; t < ms; t += 100) {
      const o = options();
      if (o.length) return o;
      await sleep(100);
    }
    return [];
  }
  const esc = () => document.activeElement && document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", keyCode: 27, bubbles: true }));
  const pickFrom = (opts, r) => {
    const want = r.opt || new RegExp("^" + String(r.val || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
    return opts.find((o) => want.test(norm(o.innerText)));
  };

  JAF.adapters.workday = {
    name: "Workday",
    detect: isWD,
    isResumeInput: (el) => el.getAttribute("data-automation-id") === "file-upload-input-ref",
    hasFile: (el) => {
      const ff = el.closest("[data-automation-id^='formField-'], section, form") || document;
      return !!ff.querySelector("[data-automation-id='file-upload-item'], [data-automation-id='fileUploadItem']");
    },
    async choose(f, r) {
      const el = f.el;
      if (f.type === "dropdown") {
        const cur = norm(el.innerText);
        if (cur && !/^select one$|^select$/i.test(cur)) return "kept";
        JAF.safeClick(el);
        const hit = pickFrom(await waitOptions(), r);
        if (!hit) { esc(); return false; }
        JAF.safeClick(hit.closest("[role='option']") || hit);
        await sleep(150);
        return true;
      }
      // Search prompt (multiselect): How did you hear, country phone code, etc.
      const box = el.closest("[data-automation-id^='formField-']") || el.parentElement;
      if (box && box.querySelector("[data-automation-id='selectedItem']")) return "kept";
      if (!r.val && !r.opt) return false;
      JAF.setValue(el, String(r.val || ""));
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true }));
      const hit = pickFrom(await waitOptions(), r);
      if (!hit) { esc(); return false; }
      JAF.safeClick(hit.closest("[role='option']") || hit);
      await sleep(150);
      return true;
    },
    // Disability page: tick only the "do not want to answer" box.
    async extra(res) {
      const boxes = [...document.querySelectorAll("input[type=checkbox]")].filter((c) => visible(c) || visible(c.parentElement));
      for (const c of boxes) {
        const t = norm(document.querySelector(`label[for="${CSS.escape(c.id || "-")}"]`)?.innerText || c.closest("label")?.innerText || "");
        const sect = norm(c.closest("section, fieldset, [data-automation-id^='formField-']")?.innerText || "");
        if (/disab/i.test(sect) && DECLINE.test(t) && !c.checked) { JAF.safeClick(c); JAF.markOk(c); res.filled.push("Disability: " + t); }
      }
    },
  };
})();

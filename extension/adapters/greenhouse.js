// Greenhouse adapter: boards.greenhouse.io, job-boards.greenhouse.io, and Greenhouse forms embedded on employer sites.
(() => {
  const JAF = window.JAF;
  if (!JAF || JAF.adapters.greenhouse) return;
  const { norm, sleep, visible } = JAF.util;
  const isGH = () => /greenhouse\.io$/i.test(location.hostname) || (!/lever\.co$|ashbyhq\.com$/i.test(location.hostname) && !!document.querySelector("#application_form, form#application-form, [data-provides='greenhouse']"));

  JAF.adapters.greenhouse = {
    name: "Greenhouse",
    detect: isGH,
    isResumeInput: (el) => el.id === "resume" || !!el.closest("#resume_fieldset, [data-field='resume']"),
    hasFile: (el) => {
      const box = el.closest("#resume_fieldset, .file-upload, [class*='upload' i]") || el.parentElement;
      return !!(box && box.querySelector(".chosen, [class*='filename' i], [class*='file-name' i]") && norm(box.innerText).match(/\.pdf|\.docx?/i));
    },
    // New Greenhouse boards use react-select comboboxes: open the menu, then click the option.
    async choose(f, r) {
      const el = f.el;
      const ctl = el.closest("[class*='control' i]") || el;
      const cur = ctl.querySelector("[class*='singleValue' i], [class*='single-value' i]");
      if (cur && norm(cur.innerText)) return "kept";
      el.focus();
      ctl.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      let opts = [];
      for (let t = 0; t < 1500 && !opts.length; t += 100) {
        await sleep(100);
        opts = [...document.querySelectorAll("[role='option'], [class*='option' i][id*='option']")].filter(visible);
      }
      const want = r.opt || new RegExp("^" + String(r.val || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
      const hit = opts.find((o) => want.test(norm(o.innerText)));
      if (!hit) { el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); return false; }
      JAF.safeClick(hit);
      await sleep(100);
      return true;
    },
  };
})();

// Lever adapter: jobs.lever.co/<company>/<id>/apply. Plain inputs inside .application-question blocks;
// the question text sits in .application-label. Resume input name="resume".
(() => {
  const JAF = window.JAF;
  if (!JAF || JAF.adapters.lever) return;
  const { norm } = JAF.util;
  const isLever = () => /(^|\.)lever\.co$/i.test(location.hostname) || !!document.querySelector(".application-question .application-label");

  JAF.labelHooks = JAF.labelHooks || [];
  JAF.labelHooks.push((el) => {
    if (!isLever()) return "";
    const q = el.closest(".application-question") || el.closest("li");
    const l = q && q.querySelector(".application-label, .text");
    return l ? l.innerText : "";
  });

  JAF.adapters.lever = {
    name: "Lever",
    detect: isLever,
    isResumeInput: (el) => el.name === "resume" || el.id === "resume-upload-input",
    hasFile: (el) => {
      const box = el.closest(".application-question, li, .resume-upload") || el.parentElement;
      const n = box && box.querySelector(".resume-upload-filename, .filename, [class*='filename' i]");
      return !!(n && /\.(pdf|docx?)/i.test(norm(n.innerText)));
    },
  };
})();

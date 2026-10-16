// SAP SuccessFactors adapter: career*.successfactors.com / .eu / sapsf, and employer career sites built on it
// (employer career sites with /job/<title>/<id>/ URLs). Forms are plain inputs and selects,
// so the shared engine does most of the work; this adds SuccessFactors label lookup.
(() => {
  const JAF = window.JAF;
  if (!JAF || JAF.adapters.successfactors) return;
  const isSF = () => /successfactors|sapsf/i.test(location.hostname) || !!document.querySelector(".rcmFormField, [id*='rcmCandidate'], [class*='sfcareer' i]");

  JAF.labelHooks = JAF.labelHooks || [];
  JAF.labelHooks.push((el) => {
    if (!isSF()) return "";
    const ff = el.closest(".rcmFormField, .formField, tr, .fieldRow");
    const l = ff && ff.querySelector("label, .rcmFormLabel, .fieldLabel, th");
    return l && !l.contains(el) ? l.innerText : "";
  });

  JAF.adapters.successfactors = {
    name: "SuccessFactors",
    detect: isSF,
    isResumeInput: (el) => /resume|cv/i.test((el.closest(".rcmFormField, .formField, tr, section") || el.parentElement)?.innerText || ""),
  };
})();

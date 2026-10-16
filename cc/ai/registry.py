"""Task registry (DESIGN section 6). One entry per AI task: prompt file, input builder, schema, semantic validator,
deterministic fallback. Prompt files arrive in M2 item 3; until then prompt_file only names them."""
import dataclasses
import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from . import prompts, trust

_FLAG = {"injection_suspect": {"type": "boolean"}}
_STR = {"type": "string", "maxLength": 4000}


@dataclass(frozen=True)
class Task:
    name: str
    feature: Optional[str]          # row in cc/modes.py that gates this task; None = always allowed
    prompt_file: str
    schema: dict
    build_input: Callable           # inputs -> prompt text (untrusted parts fenced); raises ValueError on bad inputs
    validator: Callable = lambda obj, inputs: []     # semantic checks the schema cannot express
    fallback: Optional[Callable] = None              # inputs -> data, or None when there is nothing honest to return


def _need(inputs, *keys):
    if not isinstance(inputs, dict):
        raise ValueError("inputs must be an object")
    for k in keys:
        if k not in inputs:
            raise ValueError(f"missing input {k!r}")


def _text(v, limit=20_000):
    if not isinstance(v, str):
        raise ValueError("input text must be a string")
    return v[:limit]


def _posting_input(inputs):
    _need(inputs, "posting")
    return trust.wrap_untrusted(_text(inputs["posting"]), "POSTING")


def _score_input(inputs):
    _need(inputs, "posting", "dims")
    dims = inputs["dims"]
    if not (isinstance(dims, list) and dims and all(isinstance(d, str) for d in dims)):
        raise ValueError("dims must be a non-empty list of names")
    return f"Dimensions: {', '.join(dims)}\n" + _posting_input(inputs) + _opt(inputs, "profile", "PROFILE")


def _score_validate(obj, inputs):
    got, want = set(obj["scores"]), set(inputs["dims"])
    return [] if got == want else [f"scores must cover exactly {sorted(want)}"]


def _words(s):
    return set(re.findall(r"[a-z0-9+#.]{2,}", str(s).lower()))


def _score_fallback(inputs):
    """Keyword overlap between the posting and the profile keywords, labelled as such. Same value for every dimension."""
    kws = inputs.get("keywords", [])
    kw = {w for k in (kws if isinstance(kws, list) else []) if isinstance(k, str) for w in _words(k)}
    pct = round(100 * len(kw & _words(inputs["posting"])) / len(kw)) if kw else 0
    return {"scores": {d: pct for d in inputs["dims"]}, "basis": "keyword score only"}


def _pair_input(inputs):
    _need(inputs, "posting", "profile")
    return _posting_input(inputs) + "\n" + trust.wrap_untrusted(_text(inputs["profile"]), "PROFILE")


def _opt(inputs, key, label):
    v = inputs.get(key)
    return "\n" + trust.wrap_untrusted(_text(v), label) if v else ""


def _kit_input(inputs):
    _need(inputs, "posting", "profile", "kit")
    return _pair_input(inputs) + "\n" + trust.wrap_untrusted(_text(inputs["kit"]), "KIT")


def _review_input(inputs):
    return _kit_input(inputs) + _opt(inputs, "resume", "RESUME") + _opt(inputs, "rules", "RULES")


def _review_validate(obj, inputs):
    """Every edit must quote text that occurs exactly once in the file it names (see the AI task schema)."""
    errs = []
    for i, e in enumerate(obj["part_a"]):
        src = inputs.get(e["file"] if e["file"] == "kit" else "resume")
        if not isinstance(src, str):
            errs.append(f"part_a[{i}]: no {e['file']} text was given to edit")
        elif not e["old_string"] or src.count(e["old_string"]) != 1:
            errs.append(f"part_a[{i}]: old_string must occur exactly once in the {e['file']}")
    return errs


def _prep_input(inputs):
    return _pair_input(inputs) + _opt(inputs, "stories", "STORIES")


_FILLER = ("i am writing to", "excited", "thrilled", "passionate", "eager to", "leverage", "delve", "seamless",
           "proven track record", "synergy", "results-driven", "highly motivated", "dynamic")


def _cover_input(inputs):
    return _pair_input(inputs) + _opt(inputs, "rules", "RULES")


def _cover_validate(obj, inputs):
    """Reject, never repair: length, placeholders, filler, banned wording, and any figure not found in the profile."""
    text = " ".join(obj["paragraphs"])
    low, errs = _norm(text), []
    n = len(text.split())
    if not 120 <= n <= 300:
        errs.append(f"the letter body must be 120 to 300 words, got {n}")
    if re.search(r"[\[\]{}<>]", text + obj["greeting"] + obj["closing"]):
        errs.append("the letter must not contain brackets or placeholders")
    if "—" in text or "–" in text:
        errs.append("do not use dashes as punctuation")
    errs += [f"filler phrase: {w!r}" for w in _FILLER if w in low]
    try:
        rules = json.loads(inputs.get("rules") or "{}")
    except ValueError:
        rules = {}
    errs += [f"banned phrase: {w!r}" for w in (rules.get("banned_phrases") or []) if _norm(str(w)) and _norm(str(w)) in low]
    words = _words(text)
    errs += [f"banned word: {w!r}" for w in (rules.get("banned_words") or []) if str(w).lower() in words]
    if rules.get("no_contractions") and re.search(r"\b(\w+n't|\w+'(re|ve|ll|d|m)|(it|that|there|he|she|what|let)'s)\b", text, re.I):
        errs.append("no contractions allowed")
    known = _norm(str(inputs.get("profile", "")))
    errs += [f"figure {x!r} is not in the profile" for x in dict.fromkeys(re.findall(r"\d[\d,.]*\d|\d", text))
             if x not in known]
    return errs


def _ask_input(inputs):
    _need(inputs, "question")
    return trust.wrap_untrusted(_text(inputs["question"], 4000), "QUESTION") + _opt(inputs, "context", "CONTEXT")


def _profile_input(inputs):
    _need(inputs, "profile")
    return trust.wrap_untrusted(_text(inputs["profile"]), "PROFILE")


def _resume_input(inputs):
    _need(inputs, "resume")
    return trust.wrap_untrusted(_text(inputs["resume"]), "RESUME")


def _norm(s):
    return " ".join(s.lower().split())


def _resume_validate(obj, inputs):
    """Every employer, title, fact and skill must be quoted from the resume text: reject, never repair."""
    text, errs = _norm(_text(inputs["resume"])), []
    for i, j in enumerate(obj["jobs"]):
        for k in ("employer", "title"):
            if _norm(j[k]) not in text:
                errs.append(f"jobs[{i}].{k} does not appear in the resume")
        for f in j.get("facts", []):
            if _norm(f) not in text:
                errs.append(f"jobs[{i}] has a fact that is not quoted from the resume")
                break
    for i, k in enumerate(obj["skills"]):
        if _norm(k) not in text:
            errs.append(f"skills[{i}] does not appear in the resume")
    return errs


def _week_input(inputs):
    _need(inputs, "week")
    return trust.wrap_untrusted(_text(inputs["week"], 8000), "WEEK")


def _brand_input(inputs):
    _need(inputs, "profile", "links")
    return _profile_input(inputs) + "\n" + trust.wrap_untrusted(_text(inputs["links"], 500), "LINKS")


def _mail_items(inputs):
    _need(inputs, "mail")
    m = inputs["mail"]
    if not (isinstance(m, list) and m and all(isinstance(x, dict) for x in m)):
        raise ValueError("mail must be a non-empty list of messages")
    return m[:40]


def _learning_input(inputs):
    _need(inputs, "signals")
    sig = inputs["signals"]
    if not (isinstance(sig, list) and sig and all(isinstance(x, str) for x in sig)):
        raise ValueError("signals must be a non-empty list of lines")
    return trust.wrap_untrusted(chr(10).join(x[:160] for x in sig[:80]), "SIGNALS")


def _triage_input(inputs):
    lines = [f"{i}. From: {str(x.get('from', ''))[:80]} | Subject: {str(x.get('subject', ''))[:120]} | "
             f"{str(x.get('snippet', ''))[:200]}" for i, x in enumerate(_mail_items(inputs), 1)]
    return trust.wrap_untrusted("\n".join(lines), "MAIL")


def _triage_validate(obj, inputs):
    n, seen = len(_mail_items(inputs)), set()
    errs = []
    for i, it in enumerate(obj["items"]):
        if not 1 <= it["n"] <= n or it["n"] in seen:
            errs.append(f"items[{i}]: n must be a distinct message number from 1 to {n}")
        seen.add(it["n"])
    return errs


def _triage_fallback(inputs):
    """Mail from people already in the contacts list, newest first; the reason says only that."""
    known = [i for i, x in enumerate(_mail_items(inputs), 1) if inputs["mail"][i - 1].get("known")]
    return {"items": [{"n": i, "why": "From someone in your contacts."} for i in known[:8]]}


def _linkedin_input(inputs):
    _need(inputs, "search")
    return trust.wrap_untrusted(_text(inputs["search"], 1000), "SEARCH")


def _linkedin_validate(obj, inputs):
    bad = [p["url"] for p in obj["postings"] if not re.match(r"(https://)?([a-z]+\.)?linkedin\.com/jobs/", p["url"])]
    return [f"{len(bad)} posting(s) without a linkedin.com job link"] if bad else []


def _with_prompt(name, build):
    """Instructions first, then the fenced inputs."""
    def build_input(inputs):
        fenced = build(inputs)
        return prompts.load(name) + "\n\n" + fenced
    return build_input


_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
_COACH_OK = {"title": {"{names}", "{n}"}, "steps": {"{names}", "{n}"}, "praise": {"{streak}", "{week}"}}


def _coach_input(inputs):
    _need(inputs, "tasks")
    rows = [{"id": str(t["id"]), "title": str(t["title"]), "steps": [str(x) for x in t["steps"]]} for t in inputs["tasks"]]
    if not rows or len(rows) > 5:
        raise ValueError("tasks: 1 to 5 expected")
    return trust.wrap_untrusted(json.dumps(rows, ensure_ascii=True), "TASKS")


def _coach_validate(obj, inputs):
    """Reword only: same ids, no digits or links, and a placeholder only where the input had it (or a praise one)."""
    have = {str(t["id"]): t for t in inputs["tasks"]}
    errs, seen = [], set()
    for i, t in enumerate(obj["tasks"]):
        if t["id"] not in have or t["id"] in seen:
            errs.append(f"tasks[{i}].id is not one of the given tasks")
            continue
        seen.add(t["id"])
        src = have[t["id"]]
        texts = {"title": [t["title"]], "steps": t["steps"], "praise": [t["praise"]]}
        for key, vals in texts.items():
            for v in vals:
                if re.search(r"\d|https?:|www\.", v):
                    errs.append(f"tasks[{i}].{key} has a digit or link")
                allowed = _COACH_OK[key] if key == "praise" else _COACH_OK[key] & set(_PLACEHOLDER.findall(" ".join([src["title"]] + list(src["steps"]))))
                if set(_PLACEHOLDER.findall(v)) - allowed:
                    errs.append(f"tasks[{i}].{key} has a placeholder that was not offered")
        if "{names}" in " ".join([src["title"]] + list(src["steps"])) and "{names}" not in t["title"] + " ".join(t["steps"]):
            errs.append(f"tasks[{i}] dropped the {{names}} placeholder")
    return errs


_S = {"type": "string", "maxLength": 100}
_STRS = {"type": "array", "items": _STR, "maxItems": 50}
_ONE = {"type": "string", "maxLength": 1000}

TASKS = {t.name: t for t in (
    Task("score", "fit_scoring", "score.md",
         {"type": "object", "required": ["scores"],
          "properties": {**_FLAG, "basis": _STR, "people_management": {"type": "boolean"},
                         "scores": {"type": "object", "values": {"type": "number", "minimum": 0, "maximum": 100}},
                         "pay": {"type": "object", "properties": {
                             "min": {"type": "number", "minimum": 0}, "max": {"type": "number", "minimum": 0},
                             "currency": {"type": "string", "maxLength": 8},
                             "basis": {"type": "string", "enum": ["year", "hour"]}}},
                         "location": {"type": "object", "properties": {
                             "type": {"type": "string", "enum": ["remote", "hybrid", "onsite"]},
                             "country": {"type": "string", "maxLength": 8}, "region": _S}},
                         "work_auth": {"type": "object", "properties": {
                             "accepts": {"type": "string", "enum": ["yes", "silent", "excludes"]}}}}},
         _score_input, _score_validate, _score_fallback),
    Task("tailor", "kits", "tailor.md",
         {"type": "object", "required": ["headline", "profile", "skills"],
          "properties": {**_FLAG, "headline": _S, "subhead": _STR, "profile": _STR, "why_company": _STR,
                         "skills": {"type": "array", "maxItems": 20, "items": {
                             "type": "object", "required": ["label", "text"],
                             "properties": {"label": _S, "text": _STR}}},
                         "bullet_changes": {"type": "array", "maxItems": 2, "items": {
                             "type": "object", "required": ["old", "new"],
                             "properties": {"old": _STR, "new": _STR}}},
                         "answers": {"type": "array", "maxItems": 20, "items": {
                             "type": "object", "required": ["question", "answer"],
                             "properties": {"question": _STR, "answer": _STR}}}}},
         lambda inputs: _pair_input(inputs) + _opt(inputs, "rules", "RULES"),
         fallback=lambda inputs: {"use_base_resume": True}),
    Task("trace", "claim_review", "trace.md",
         {"type": "object", "required": ["unsupported"],
          "properties": {**_FLAG, "unsupported": {"type": "array", "maxItems": 100, "items": {
              "type": "object", "required": ["claim", "reason"], "properties": {"claim": _STR, "reason": _STR}}}}},
         _kit_input),
    Task("review", "claim_review", "review.md",
         {"type": "object", "required": ["verdict", "part_a", "part_b", "notes"],
          "properties": {**_FLAG,
                         "verdict": {"type": "string", "enum": ["Ready to send", "Send after fixes",
                                                                "Weak fit: reconsider"]},
                         "part_a": {"type": "array", "maxItems": 40, "items": {
                             "type": "object", "required": ["file", "old_string", "new_string", "reason"],
                             "properties": {"file": {"type": "string", "enum": ["resume", "kit"]},
                                            "old_string": _STR, "new_string": _STR,
                                            "reason": {"type": "string", "enum": [
                                                "grounding", "keyword", "company-angle", "reframing", "voice"]}}}},
                         "part_b": {"type": "object",
                                    "required": ["missed_requirements", "company_angle", "reframing", "voice"],
                                    "properties": {k: _STR for k in
                                                   ("missed_requirements", "company_angle", "reframing", "voice")}},
                         "notes": {"type": "array", "items": _ONE, "maxItems": 6}}},
         _review_input, _review_validate),
    Task("prep", "interview_prep", "prep.md",
         {"type": "object", "required": ["sections"],
          "properties": {**_FLAG, "sections": {"type": "array", "maxItems": 12, "items": {
              "type": "object", "required": ["h", "items"], "properties": {"h": _S, "items": _STRS}}}}},
         _prep_input),
    Task("cover", "cover_letter", "cover.md",
         {"type": "object", "required": ["greeting", "paragraphs", "closing"],
          "properties": {**_FLAG, "greeting": _S, "closing": _S,
                         "paragraphs": {"type": "array", "minItems": 2, "maxItems": 5, "items": _STR}}},
         _cover_input, _cover_validate),
    Task("ask", None, "ask.md",
         {"type": "object", "required": ["answer"],
          "properties": {**_FLAG, "answer": {"type": "string", "maxLength": 8000}}},
         _ask_input),
    Task("week_review", None, "week_review.md",
         {"type": "object", "required": ["worked", "stop", "priorities"],
          "properties": {**_FLAG, "worked": {"type": "array", "items": _ONE, "maxItems": 5},
                         "stop": {"type": "array", "items": _ONE, "maxItems": 5},
                         "priorities": {"type": "array", "items": _ONE, "maxItems": 3}}},
         _week_input),
    Task("brand_check", "linkedin_reading", "brand_check.md",
         {"type": "object", "required": ["brand", "post_idea"],
          "properties": {**_FLAG, "brand": {"type": "array", "items": _ONE, "maxItems": 6},
                         "post_idea": {"type": "array", "items": _ONE, "maxItems": 3}}},
         _brand_input),
    Task("inbox_triage", None, "inbox_triage.md",
         {"type": "object", "required": ["items"],
          "properties": {**_FLAG, "items": {"type": "array", "maxItems": 8, "items": {
              "type": "object", "required": ["n", "why"],
              "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 40}, "why": _ONE}}}}},
         _triage_input, _triage_validate, _triage_fallback),
    Task("linkedin_jobs", "linkedin_reading", "linkedin_jobs.md",
         {"type": "object", "required": ["postings"],
          "properties": {**_FLAG, "postings": {"type": "array", "maxItems": 50, "items": {
              "type": "object", "required": ["title", "company", "url"],
              "properties": {"title": _S, "company": _S, "location": _S, "url": {"type": "string", "maxLength": 300}}}}}},
         _linkedin_input, _linkedin_validate),
    Task("learning_suggest", "learning_ai", "learning_suggest.md",
         {"type": "object", "required": ["avoid"],
          "properties": {**_FLAG, "avoid": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 30}}}},
         _learning_input),
    Task("profile_draft", None, "profile_draft.md",
         {"type": "object", "required": ["headline", "summary", "skills"],
          "properties": {**_FLAG, "headline": _STR, "summary": _STR,
                         "skills": {"type": "array", "items": _S, "maxItems": 20}}},
         _profile_input),
    Task("coach_wording", "coach", "coach_wording.md",
         {"type": "object", "required": ["tasks"],
          "properties": {**_FLAG, "tasks": {"type": "array", "maxItems": 5, "items": {
              "type": "object", "required": ["id", "title", "steps", "praise"],
              "properties": {"id": _S, "title": {"type": "string", "maxLength": 120},
                             "steps": {"type": "array", "items": {"type": "string", "maxLength": 160}, "minItems": 1, "maxItems": 3},
                             "praise": {"type": "string", "maxLength": 200}}}}}},
         _coach_input, _coach_validate),
    Task("resume_parse", None, "resume_parse.md",
         {"type": "object", "required": ["jobs", "skills"],
          "properties": {**_FLAG,
                         "jobs": {"type": "array", "maxItems": 20, "items": {
                             "type": "object", "required": ["employer", "title"],
                             "properties": {"employer": _S, "title": _S, "dates": _S,
                                            "facts": {"type": "array", "maxItems": 30, "items": {
                                                "type": "string", "maxLength": 400}}}}},
                         "skills": {"type": "array", "items": _S, "maxItems": 30}}},
         _resume_input, _resume_validate),
)}
TASKS = {n: dataclasses.replace(t, build_input=_with_prompt(n, t.build_input)) for n, t in TASKS.items()}

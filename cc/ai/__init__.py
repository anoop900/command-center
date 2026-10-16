"""AI layer entry point: run_task(name, inputs) -> Result. Only the none runner exists so far (M2 item 1); a runner
is any callable (task, prompt_text) -> raw output text, QUEUED, or None for 'no AI available'."""
from dataclasses import dataclass, field

from .. import modes
from . import schema, trust
from .registry import TASKS

QUEUED = object()


class RunnerError(Exception):
    """A runner failure with a fixed, safe message (never output, stderr, URLs with keys, or prompt text)."""


@dataclass
class Result:
    status: str                     # ok | queued | unavailable
    data: dict = None
    source: str = ""                # ai | fallback
    reason: str = ""
    injection_suspect: bool = False
    errors: list = field(default_factory=list)


def none_runner(task, prompt):
    return None


def _fallback(task, inputs, reason, **kw):
    try:
        data = task.fallback(inputs) if task.fallback else None
    except Exception:       # a broken fallback must not take down a run
        data = None
    if data is None:
        return Result("unavailable", reason=reason, **kw)
    return Result("ok", data, "fallback", reason, **kw)


def run_task(name, inputs, mode=modes.DEFAULT, runner=none_runner):
    task = TASKS.get(name)
    if task is None:
        return Result("unavailable", reason=f"unknown task {name!r}")
    try:
        prompt = task.build_input(inputs)
    except (ValueError, TypeError, KeyError) as e:
        return Result("unavailable", reason=f"bad input: {e}")
    try:
        modes.check(mode)
    except modes.ModeError as e:
        return Result("unavailable", reason=str(e))
    if mode == "no-ai" or (task.feature and modes.is_off(task.feature, mode)):
        reason = modes.REASONS.get((task.feature, mode), "No AI configured.")
        return _fallback(task, inputs, reason)
    try:
        raw = runner(task, prompt)
    except RunnerError as e:
        return _fallback(task, inputs, f"runner failed: {e}")
    except Exception as e:  # a runner must never crash a run
        return _fallback(task, inputs, f"runner failed: {type(e).__name__}")
    if raw is QUEUED:
        return Result("queued", reason="waiting for a pasted answer")
    if raw is None:
        return _fallback(task, inputs, "No AI available.")
    obj, err = trust.parse_output(raw)
    errs = [err] if err else schema.validate(task.schema, obj)
    if not errs:
        try:
            errs = list(task.validator(obj, inputs))
        except Exception:
            errs = ["validator could not check the output"]
    if errs:
        return _fallback(task, inputs, "AI output rejected", errors=errs[:5])
    if obj.get("injection_suspect") or trust.looks_injected(obj):
        return _fallback(task, inputs, "AI output flagged as possible prompt injection", injection_suspect=True)
    return Result("ok", obj, "ai")

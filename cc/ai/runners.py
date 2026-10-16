"""Runners (DESIGN section 6): cli, api, paste. A runner is (task, prompt) -> raw text | QUEUED | None. Every failure is a
RunnerError with a fixed message, so keys, stderr and prompt text never reach logs or the board."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from .. import modes, secrets, store
from . import QUEUED, RunnerError, none_runner, schema, trust

KEY_NAME = "ai_api_key"
MAX_RESPONSE = 256 * 1024
CARDS = "prompt_cards"
# A background service has no console, so Windows would open a visible one for each CLI call.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
_ENV_KEEP = ("PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "TEMP", "TMP")


def cli_runner(name, timeout, run=subprocess.run):
    spec = modes.CLI_RUNNERS.get(name)
    if spec is None:
        raise RunnerError(f"unknown CLI {name!r}")

    def runner(task, prompt):
        exe = shutil.which(spec["argv"][0])
        if not exe:
            raise RunnerError("No AI configured: the AI tool chosen in Setup was not found on this computer")
        env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}  # no CC_SECRET_* or API keys for the child
        # Windows can hold the folder briefly after the child exits; a failed cleanup must not fail the call.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as cwd:  # an injected posting cannot make an agent read repo files
            try:
                p = run([exe, *spec["argv"][1:]], input=prompt.encode("utf-8"), capture_output=True,
                        timeout=timeout, cwd=cwd, env=env, shell=False, **_NO_WINDOW)
            except subprocess.TimeoutExpired:
                raise RunnerError(f"timed out after {timeout}s") from None
            except OSError:
                raise RunnerError("could not start the CLI") from None
        if p.returncode != 0:
            raise RunnerError(f"CLI exited with code {p.returncode}")
        if len(p.stdout) > trust.MAX_OUTPUT:
            raise RunnerError("CLI output too large")
        try:
            return p.stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise RunnerError("CLI output is not UTF-8") from None

    return runner


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # a redirect would carry the key to another host
        return None


def _send(req, timeout):
    return urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)


def _endpoint(kind, base, model):
    if not model:
        raise RunnerError("api_model is not set")
    base = base or {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1"}[kind]
    u = urllib.parse.urlsplit(base)
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise RunnerError("api_base must be https (http only for localhost)")
    if not u.hostname or u.username or u.password or u.query or u.fragment:
        raise RunnerError("api_base is not a plain URL")
    path = u.path.rstrip("/") + ("/v1/messages" if kind == "anthropic" else "/chat/completions")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, path, "", ""))


def api_runner(kind, base, model, timeout, get_secret=secrets.get, send=_send):
    url = _endpoint(kind, base, model)
    local = urllib.parse.urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1")   # a local server such as Ollama needs no key

    def runner(task, prompt):
        key = get_secret(KEY_NAME)
        if not key and not (local and kind == "openai"):   # only an OpenAI-style local server (Ollama) runs keyless
            raise RunnerError("no API key set (cc ai key)")
        if kind == "anthropic":
            body = {"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]}
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        else:
            body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            headers = {"Authorization": f"Bearer {key}"} if key else {}
        req = urllib.request.Request(url, json.dumps(body).encode(), {**headers, "Content-Type": "application/json"})
        try:
            with send(req, timeout) as r:
                raw = r.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as e:
            raise RunnerError(f"HTTP {e.code}") from None
        except (urllib.error.URLError, OSError, ValueError):
            raise RunnerError("network error") from None
        if len(raw) > MAX_RESPONSE:
            raise RunnerError("response too large")
        try:
            j = json.loads(raw)
            text = j["content"][0]["text"] if kind == "anthropic" else j["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise RunnerError("unexpected response shape") from None
        if not isinstance(text, str):
            raise RunnerError("unexpected response shape")
        return text

    return runner


def card_id(task, prompt):
    return hashlib.sha256(f"{task.name}\n{prompt}".encode("utf-8")).hexdigest()[:16]


def paste_runner(conn):
    """First call queues a prompt card and returns QUEUED; once the user's answer is recorded it returns that text."""
    def runner(task, prompt):
        cid = card_id(task, prompt)
        doc = store.get(conn, CARDS, cid)
        if doc and doc["data"].get("status") == "answered":
            return doc["data"]["answer"]
        if not doc:
            store.set_doc(conn, CARDS, cid, {"task": task.name, "prompt": prompt, "status": "pending",
                                             "created": store.now()}, by="ai")
        return QUEUED

    return runner


def pending_cards(conn):
    return [{"id": d["id"], "task": d["data"]["task"]} for d in store.list_docs(conn, CARDS)
            if d["data"].get("status") == "pending"]


def answer_card(conn, cid, text):
    """Record a pasted answer. Returns a list of problems; empty means stored. Same checks as any model output."""
    from .registry import TASKS
    doc = store.get(conn, CARDS, cid)
    if not doc:
        return ["no such card"]
    obj, err = trust.parse_output(text)
    errs = [err] if err else schema.validate(TASKS[doc["data"]["task"]].schema, obj)
    if errs:
        return errs[:5]
    store.update_doc(conn, CARDS, cid, {"status": "answered", "answer": text}, if_version=doc["version"], by="user")
    return []


def make_runner(cfg, conn):
    """The runner the config and mode select. Chat mode is always paste; no-ai is always none."""
    mode = cfg.mode.name
    if mode == "no-ai":
        return none_runner
    if mode == "chat":
        return paste_runner(conn)
    a = cfg.ai
    from .budget import Budget  # paste and none runners make no model call, so only cli and api are metered
    if a.runner == "cli":
        return Budget(conn, cfg.budget).wrap(cli_runner(a.cli, a.timeout_s))
    if a.runner == "api":
        return Budget(conn, cfg.budget).wrap(api_runner(a.api_kind, a.api_base, a.api_model, a.timeout_s))
    if a.runner == "paste":
        return paste_runner(conn)
    return none_runner

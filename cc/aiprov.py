"""Wizard step: choose the AI provider that Autopilot and Assisted use. Writes only the [ai] section of config.toml
and, when given, the API key through the secrets store (never into config.toml, never echoed back)."""
import shutil

from . import config, modes, secrets
from .ai import RunnerError, runners

# key -> (label, [ai] values, needs a key, model hint). Models change often, so the user types the model name.
PRESETS = {
    "claude-cli": ("Claude (installed command-line app)", {"runner": "cli", "cli": "claude"}, False, ""),
    "codex-cli": ("Codex (installed command-line app)", {"runner": "cli", "cli": "codex"}, False, ""),
    "anthropic": ("Anthropic API", {"runner": "api", "api_kind": "anthropic", "api_base": ""}, True, "a Claude model name"),
    "openai": ("OpenAI API", {"runner": "api", "api_kind": "openai", "api_base": ""}, True, "an OpenAI model name"),
    "gemini": ("Google Gemini API", {"runner": "api", "api_kind": "openai",
                                     "api_base": "https://generativelanguage.googleapis.com/v1beta/openai"}, True, "a Gemini model name"),
    "openrouter": ("OpenRouter", {"runner": "api", "api_kind": "openai", "api_base": "https://openrouter.ai/api/v1"},
                   True, "a model id such as vendor/model"),
    "ollama": ("Ollama (runs on this computer; nothing leaves it)",
               {"runner": "api", "api_kind": "openai", "api_base": "http://localhost:11434/v1"}, False, "a model you pulled"),
    "other": ("Other OpenAI-compatible service", {"runner": "api", "api_kind": "openai"}, True, "the service's model name"),
    "paste": ("Paste into any chat app yourself", {"runner": "paste"}, False, ""),
}


def _current_preset(ai):
    if ai.runner == "cli":
        return ai.cli + "-cli"
    if ai.runner == "paste":
        return "paste"
    if ai.runner == "api":
        for k, (_, v, _, _) in PRESETS.items():
            if v.get("runner") == "api" and k != "other" and v["api_kind"] == ai.api_kind and v.get("api_base", "") == ai.api_base:
                return k
        return "other"
    return ""


def options(cfg_file):
    cfg = config.load(cfg_file)
    return {"current": _current_preset(cfg.ai), "model": cfg.ai.api_model, "base": cfg.ai.api_base,
            "key_set": bool(secrets.get(runners.KEY_NAME)), "mode": cfg.mode.name, "uses_ai": modes.uses_provider(cfg.mode.name),
            "intro": " and ".join(modes.LABELS[m] for m in modes.UPGRADE_ORDER if modes.uses_provider(m)),
            "presets": [{"id": k, "label": lab, "needs_key": nk, "model_hint": hint,
                         "installed": shutil.which(v["cli"]) is not None if v.get("runner") == "cli" else None}
                        for k, (lab, v, nk, hint) in PRESETS.items()]}


def save(cfg_file, data):
    """Validate, then write [ai]; the key (if any) goes to the secrets store. Returns the same shape as options()."""
    if not isinstance(data, dict) or data.get("preset") not in PRESETS:
        raise ValueError("choose one of the listed providers")
    _, values, needs_key, _ = PRESETS[data["preset"]]
    values = dict(values)
    if values["runner"] == "api":
        for k in ("api_model", "api_base"):
            v = data.get(k, "")
            if not isinstance(v, str):
                raise ValueError(f"{k}: text only")
            values[k] = v.strip() if k == "api_model" or data["preset"] == "other" else values.get(k, "")
        if not values["api_model"]:
            raise ValueError("type the model name your provider gave you")
        if data["preset"] == "other" and not values.get("api_base"):
            raise ValueError("type the service's web address (https://...)")
    key = data.get("key", "")
    if not isinstance(key, str) or len(key) > 500 or any(c.isspace() or ord(c) < 32 for c in key):
        raise ValueError("key: no spaces or line breaks")
    if values["runner"] == "api":
        try:
            runners._endpoint(values["api_kind"], values.get("api_base", ""), values["api_model"])   # same URL rules as a real call
        except RunnerError as e:
            raise ValueError(str(e)) from None
    config.set_values(cfg_file, "ai", values)
    out = {}
    if key:
        out["key_stored_in"] = secrets.set(runners.KEY_NAME, key)
    elif needs_key and not secrets.get(runners.KEY_NAME):
        out["warning"] = "No API key is stored yet. Type it here and save again, or run cc ai key."
    return {**out, **options(cfg_file)}


def test(cfg_file, conn, runner=None):
    """One real call to the chosen provider. Returns {ok, message}; never raises for a provider problem."""
    from . import ai
    cfg = config.load(cfg_file)
    if not modes.uses_provider(cfg.mode.name):
        return {"ok": False, "message": "Your mode does not call an AI provider. Choose Autopilot or Assisted first."}
    try:
        r = ai.run_task("ask", {"question": 'Reply with exactly {"answer": "ok"}'}, cfg.mode.name,
                        runner or runners.make_runner(cfg, conn))
    except ai.RunnerError as e:
        return {"ok": False, "message": str(e)}
    ok = r.status == "ok" and r.source == "ai"
    return {"ok": ok, "message": "The provider answered." if ok else (r.reason or "No answer.")}

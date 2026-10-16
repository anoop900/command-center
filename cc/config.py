"""Data folder resolution and config.toml loading. Secrets never live here (keyring, next item)."""
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from cc import modes

REPO = Path(__file__).resolve().parent.parent
SUBDIRS = ("kits", "logs", "exports")
SECRET_WORDS = ("password", "passwd", "secret", "token", "api_key", "apikey", "ics_url")

STARTER = """# Command Center config. Placeholders only; secrets (mail password, calendar URL) go in the OS keyring.
[mail]
host = "imap.example.com"
user = "you@example.com"
folder = "INBOX"

[calendar]
ics_configured = false

[run]
morning = "07:30"
midday = "12:30"

[budget]
max_calls_per_run = 20
max_calls_per_day = 100

[mode]
name = "no-ai"

[ai]
# runner: none | cli | paste | api. The API key is never stored here; set it with `cc ai key`.
runner = "none"
cli = "claude"
api_kind = "anthropic"
api_base = ""
api_model = ""
timeout_s = 120

[paths]
# kits = "D:/Documents/Resumes"   # where resumes and kits are saved; blank = inside the data folder
kits = ""

[backup]
# One backup a day is kept in the data folder (keep = how many days; keep_manual = how many you made yourself). extra_dir (optional) also receives a copy, e.g. a OneDrive or USB folder.
keep = 7
keep_manual = 20
extra_dir = ""
"""


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Mail:
    host: str = ""
    user: str = ""
    folder: str = "INBOX"


@dataclass(frozen=True)
class Calendar:
    ics_configured: bool = False


@dataclass(frozen=True)
class Run:
    morning: str = "07:30"
    midday: str = "12:30"


@dataclass(frozen=True)
class Budget:
    max_calls_per_run: int = 20
    max_calls_per_day: int = 100


@dataclass(frozen=True)
class Mode:
    name: str = "no-ai"


@dataclass(frozen=True)
class Backup:
    keep: int = 7
    keep_manual: int = 20
    extra_dir: str = ""


@dataclass(frozen=True)
class Paths:
    kits: str = ""      # empty = <data folder>/kits; otherwise an absolute folder of the user's choice


@dataclass(frozen=True)
class Ai:
    runner: str = "none"
    cli: str = "claude"
    api_kind: str = "anthropic"
    api_base: str = ""
    api_model: str = ""
    timeout_s: int = 120


@dataclass(frozen=True)
class Config:
    mail: Mail = field(default_factory=Mail)
    calendar: Calendar = field(default_factory=Calendar)
    run: Run = field(default_factory=Run)
    budget: Budget = field(default_factory=Budget)
    mode: Mode = field(default_factory=Mode)
    ai: Ai = field(default_factory=Ai)
    backup: Backup = field(default_factory=Backup)
    paths: Paths = field(default_factory=Paths)


def data_dir():
    """CC_HOME, else ~/.commandcenter. Lives outside the repo."""
    return Path(os.environ.get("CC_HOME") or Path.home() / ".commandcenter")


def config_path():
    return data_dir() / "config.toml"


def kits_dir(home=None):
    """Where kits are saved: [paths] kits in config.toml, else <data folder>/kits. A damaged config gives the default."""
    home = Path(home) if home else data_dir()
    try:
        custom = load(home / "config.toml").paths.kits
    except ConfigError:
        custom = ""
    return Path(custom).expanduser() if custom else home / "kits"


def _hhmm(v):
    h, _, m = v.partition(":")
    return len(v) == 5 and h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60


AI_RUNNERS = ("none", "cli", "paste", "api")
API_KINDS = ("anthropic", "openai")


def _check_ai(k, v):
    allowed = {"runner": AI_RUNNERS, "cli": tuple(modes.CLI_RUNNERS), "api_kind": API_KINDS}
    if k in allowed and v not in allowed[k]:
        raise ConfigError(f"[ai] {k}: expected one of {', '.join(allowed[k])}")
    if k == "timeout_s" and v > 600:
        raise ConfigError("[ai] timeout_s: must be 600 or less")
    if k in ("api_base", "api_model") and (len(v) > 300 or any(c.isspace() or ord(c) < 32 for c in v)):
        raise ConfigError(f"[ai] {k}: no spaces or control characters, 300 characters at most")


def validate(raw):
    sections = {f.name: f.type for f in fields(Config)}
    out = {}
    for name, body in raw.items():
        if name not in sections:
            raise ConfigError(f"unknown section [{name}]")
        if not isinstance(body, dict):
            raise ConfigError(f"[{name}] must be a table")
        cls = sections[name]
        known = {f.name: f for f in fields(cls)}
        for k, v in body.items():
            if any(w in k.lower() for w in SECRET_WORDS) and k != "ics_configured":
                raise ConfigError(f"[{name}] {k}: secrets belong in the keyring, not config.toml")
            if k not in known:
                raise ConfigError(f"[{name}] unknown key {k}")
            want = type(getattr(cls(), k))
            if type(v) is not want:
                raise ConfigError(f"[{name}] {k}: expected {want.__name__}, got {type(v).__name__}")
            if want is int and v < 1:
                raise ConfigError(f"[{name}] {k}: must be 1 or more")
            if name == "run" and not _hhmm(v):
                raise ConfigError(f"[run] {k}: expected HH:MM")
            if name == "mode" and v not in modes.MODES:
                raise ConfigError(f"[mode] name: expected one of {', '.join(modes.MODES)}")
            if name == "ai":
                _check_ai(k, v)
            if name == "backup" and k == "extra_dir" and (len(v) > 500 or any(ord(c) < 32 for c in v)):
                raise ConfigError("[backup] extra_dir: a folder path, 500 characters at most, no control characters")
            if name == "paths" and v and (len(v) > 500 or any(ord(c) < 32 for c in v) or not Path(v).expanduser().is_absolute()):
                raise ConfigError("[paths] kits: a full folder path (for example D:/Documents/Resumes), 500 characters at most")
            if name == "backup" and k == "keep" and v > 60:
                raise ConfigError("[backup] keep: 60 or less")
            if name == "backup" and k == "keep_manual" and v > 200:
                raise ConfigError("[backup] keep_manual: 200 or less")
        out[name] = cls(**body)
    return Config(**out)


def load(path=None):
    p = Path(path) if path else config_path()
    if not p.exists():
        return Config()
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise ConfigError(f"{p.name}: not valid UTF-8 text")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p.name}: {e}") from None
    return validate(raw)


def init_data_dir():
    """Create the data folder, subfolders and a starter config. Never overwrites an existing config."""
    d = data_dir()
    for s in SUBDIRS:
        (d / s).mkdir(parents=True, exist_ok=True)
    p = config_path()
    if not p.exists():
        p.write_text(STARTER, encoding="utf-8")
    return d


def set_mode(path, name):
    modes.check(name)
    set_values(path, "mode", {"name": name})


def _toml(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str) and v.isprintable() and '"' not in v and "\\" not in v:
        return f'"{v}"'
    raise ConfigError("values may not contain quotes, backslashes or control characters")


def set_values(path, section, values):
    """Rewrite only the named keys in one section (keeping comments and other sections); the file is
    validated before it is replaced atomically, so a bad value or a corrupt file leaves it untouched."""
    p = Path(path)
    out = (p.read_text(encoding="utf-8") if p.exists() else STARTER).splitlines()
    head = f"[{section}]"
    if head not in (x.strip() for x in out):
        out += ["", head]
    for key, val in values.items():
        line = f"{key} = {_toml(val)}"
        start = [x.strip() for x in out].index(head) + 1
        end = next((k for k in range(start, len(out)) if out[k].strip().startswith("[")), len(out))
        at = next((k for k in range(start, end) if out[k].split("=")[0].strip() == key and "=" in out[k]), None)
        if at is None:
            out.insert(start, line)
        else:
            out[at] = line
    new = "\n".join(out) + "\n"
    try:
        validate(tomllib.loads(new))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p.name}: {e}") from None
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, p)

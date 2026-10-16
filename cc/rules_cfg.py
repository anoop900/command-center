"""Wizard step 4: pay, location and work-authorization rules. Writes only the [pay], [location] and
[[work_authorization]] sections of scoring.toml in the data folder; every other section and comment is kept as is.
No floor is ever defaulted: pay floors must be typed by the user."""
import math
import os
import re
import tomllib
from pathlib import Path

FILE = "scoring.toml"
OWNED = ("pay", "location", "work_authorization")
MAX_LIST = 20
MAX_ROUTES = 10
MAX_TEXT = 60
MAX_NOTE = 300
MAX_FLOOR = 10_000_000
HEADER = re.compile(r"^\s*\[\[?\s*([A-Za-z0-9_-]+)")
COUNTRY = re.compile(r"^[A-Za-z]{2}$")
CURRENCY = re.compile(r"^[A-Za-z]{3}$")
STARTER = """[scoring]
weights = { technical = 0.30, experience = 0.25, seniority = 0.15, career = 0.30 }
kit_threshold = 80
top_n = 3
top_n_min = 60
bands = [[75, "Strong fit"], [60, "Good fit"], [45, "Moderate fit"], [30, "Weak fit"], [0, "Poor fit"]]
"""


def defaults():
    return {"currency": "", "floor_salary": None, "floor_hourly": None, "flag_within": 0,
            "home_country": "", "remote_countries": [], "onsite_regions": [], "relocation_channels": [],
            "onsite_via_route": True, "routes": []}


def _text(v, what, limit=MAX_TEXT):
    if not isinstance(v, str) or len(v) > limit or not v.isprintable():
        raise ValueError(f"{what}: text only, up to {limit} characters, on one line")
    return v.strip()


def _num(v, what, lo, hi, required=True, pct=False, money=False):
    """pct=True also accepts '10%', and a plain number above 1 as a percentage (stored as 0.1). money=True accepts
    '$85k', 'CAD 85,000' and '40/hr' (a leading currency, a trailing k, a trailing per-unit note)."""
    pct_sign, mult = False, 1
    if isinstance(v, str):
        v = v.strip().replace(",", "")
        if pct and v.endswith("%"):
            v, pct_sign = v[:-1].strip(), True
        if money:
            v = re.sub(r"\s*(/|per\s|an?\s+(hour|year)).*$", "", re.sub(r"^(?:[A-Za-z]{1,3}\$|[A-Za-z]{3}\b|[$€£])\s*", "", v), flags=re.I).strip()
            if v[-1:] in ("k", "K"):
                v, mult = v[:-1].strip(), 1000
    if v in (None, ""):
        if required:
            raise ValueError(f"{what}: required, type a number")
        return 0
    if isinstance(v, bool):
        raise ValueError(f"{what}: must be a number")
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: must be a number" + (", such as 10% or 0.10" if pct else "")) from None
    n *= mult
    if pct and not pct_sign and 1 < n:
        pct_sign = True  # '10' means 10%
    if pct_sign:
        n = round(n / 100, 6)
    elif pct and 0.5 < n <= 1:
        raise ValueError(f"{what}: {n:g} is unclear. Type a percentage such as 10% (the most allowed is 50%)")
    if not math.isfinite(n) or not lo <= n <= hi:
        if pct:
            raise ValueError(f"{what}: type a percentage from 0% to {hi * 100:g}%, such as 10% (or 0.10)")
        raise ValueError(f"{what}: must be between {lo:g} and {hi:g}")
    return int(n) if n == int(n) else n


# Country names people type, mapped to the 2-letter codes postings use. A region is not a country: it gets a clear error.
NAMES = {
    "united states": "US", "usa": "US", "united states of america": "US", "america": "US", "canada": "CA", "mexico": "MX",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "germany": "DE",
    "france": "FR", "netherlands": "NL", "holland": "NL", "ireland": "IE", "spain": "ES", "italy": "IT", "portugal": "PT",
    "belgium": "BE", "luxembourg": "LU", "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI",
    "switzerland": "CH", "austria": "AT", "poland": "PL", "czechia": "CZ", "czech republic": "CZ", "australia": "AU",
    "new zealand": "NZ", "india": "IN", "singapore": "SG", "japan": "JP", "brazil": "BR", "israel": "IL",
    "united arab emirates": "AE", "uae": "AE", "south africa": "ZA", "argentina": "AR", "chile": "CL", "colombia": "CO",
}
CURRENCY_NAMES = {"canadian dollar": "CAD", "canadian dollars": "CAD", "cdn": "CAD", "us dollar": "USD", "us dollars": "USD",
                  "american dollar": "USD", "euro": "EUR", "euros": "EUR", "pound": "GBP", "pounds": "GBP",
                  "british pound": "GBP", "australian dollar": "AUD", "australian dollars": "AUD"}
WORK_STYLES = {"remote", "hybrid", "on-site", "onsite", "on site", "in office", "in-office", "wfh", "work from home"}
REGIONS = {"europe", "eu", "european union", "asia", "africa", "latam", "latin america", "apac", "emea", "north america",
           "south america", "worldwide", "global", "anywhere", "world", "americas"}


def _country(v, what):
    s = _text(v, what, 40)
    key = re.sub(r"[^a-z ]", "", s.lower()).strip()
    if key in NAMES:  # first, so 'UK' becomes GB
        return NAMES[key]
    if COUNTRY.match(s):
        return s.upper()
    if key in REGIONS:
        raise ValueError(f"{what}: {s!r} is a region, not a country. List the countries one per line, by name or "
                         "2-letter code, for example GB, DE, FR, NL, IE")
    raise ValueError(f"{what}: {s[:20]!r} is not a country name or 2-letter code (for example US, CA, GB)")


def _bool(v, what):
    if not isinstance(v, bool):
        raise ValueError(f"{what}: must be yes or no")
    return v


def _list(v, what, limit=MAX_TEXT, code=None):
    if not isinstance(v, list) or len(v) > MAX_LIST:
        raise ValueError(f"{what}: a list of up to {MAX_LIST} items")
    out = []
    for x in v:
        s = _text(x, what, limit if code is None else 40)
        if not s:
            continue
        if code is not None:
            s = _country(s, what)
        if s not in out:
            out.append(s)
    return out


def _places(items):
    for x in items:
        if re.sub(r"[^a-z\- ]", "", x.lower()).strip() in WORK_STYLES:
            raise ValueError(f"on-site places: {x!r} is a way of working, not a place. Type the provinces, states or cities "
                             "where you would work on-site or hybrid, for example Texas or Austin. Remote jobs are "
                             "covered by the remote countries box")
    return items


def validate(raw):
    """-> a clean rules state, or ValueError naming the first bad field."""
    if not isinstance(raw, dict):
        raise ValueError("rules must be an object")
    d = {**defaults(), **raw}
    problems = []

    def check(fn, *a, **k):  # every field is checked so the user sees all the problems at once, not one per click
        try:
            return fn(*a, **k)
        except ValueError as e:
            problems.append(str(e))

    def currency():
        cur = _text(d["currency"], "currency", 30)
        cur = CURRENCY_NAMES.get(re.sub(r"[^a-z ]", "", cur.lower()).strip(), cur)
        if not CURRENCY.match(cur):
            raise ValueError("currency: a 3-letter code such as CAD, USD, EUR or GBP (or a name such as Canadian dollars)")
        return cur.upper()

    def home_country():
        if not _text(d["home_country"], "home country", 40):
            raise ValueError("home country required: type your country name or 2-letter code")
        return _country(d["home_country"], "home country")

    def routes():
        routes = d["routes"]
        if not isinstance(routes, list) or len(routes) > MAX_ROUTES:
            raise ValueError(f"routes: a list of up to {MAX_ROUTES}")
        clean = []
        for r in routes:
            if not isinstance(r, dict):
                raise ValueError("routes: each route must be an object")
            c = _country(r.get("country", ""), "route country")
            silent, excl = r.get("on_silent", "flag"), r.get("on_excludes", "fail")
            if silent not in ("flag", "fail") or excl not in ("flag", "fail"):
                raise ValueError("route: on_silent and on_excludes must be flag or fail")
            clean.append({
                "country": c.upper(), "route": _text(r.get("route", ""), "route name") or "visa",
                "on_silent": silent, "on_excludes": excl,
                "flag_people_management": _bool(r.get("flag_people_management", False), "flag people management"),
                "flag_title_patterns": [p.lower() for p in _list(r.get("flag_title_patterns", []), "title patterns")],
                "note": _text(r.get("note", ""), "route note", MAX_NOTE)})
        return clean

    out = {
        "currency": check(currency),
        "floor_salary": check(_num, d["floor_salary"], "lowest yearly salary", 1, MAX_FLOOR, money=True),
        "floor_hourly": check(_num, d["floor_hourly"], "lowest hourly rate", 1, MAX_FLOOR, money=True),
        "flag_within": check(_num, d["flag_within"], "warn within", 0, 0.5, required=False, pct=True),
        "home_country": check(home_country),
        "remote_countries": check(_list, d["remote_countries"], "remote countries", 2, COUNTRY),
        "onsite_regions": check(lambda: _places(_list(d["onsite_regions"], "on-site places"))),
        "relocation_channels": check(_list, d["relocation_channels"], "relocation channels"),
        "onsite_via_route": check(_bool, d["onsite_via_route"], "on-site via route"),
        "routes": check(routes)}
    if problems:
        raise ValueError(" | ".join(problems))
    return out


def _s(v):
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _arr(xs):
    return "[" + ", ".join(_s(x) for x in xs) + "]"


def _fx(old):
    fx = old.get("pay", {}).get("fx", {})
    if not isinstance(fx, dict):
        raise ValueError("existing fx in scoring.toml is not a table; fix it by hand first")
    for k, v in fx.items():
        if not CURRENCY.match(k) or isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v < 1e6:
            raise ValueError("existing fx in scoring.toml has a bad entry; fix it by hand first")
    return fx


def render(state, old=None):
    """The owned sections as TOML text. Keeps the old [pay] hours_per_year and fx, and [location] region names."""
    old = old or {}
    op, ol = old.get("pay", {}), old.get("location", {})
    hours = _num(op.get("hours_per_year", 2080), "hours_per_year", 1, 8760)
    fx = _fx(old)
    lines = ["[pay]", f"currency = {_s(state['currency'])}", f"floor_salary = {state['floor_salary']}",
             f"floor_hourly = {state['floor_hourly']}", f"hours_per_year = {hours}",
             f"flag_within = {state['flag_within']}"]
    if fx:
        lines.append("fx = { " + ", ".join(f"{k} = {v}" for k, v in fx.items()) + " }")
    lines += ["", "[location]", f"home_country = {_s(state['home_country'])}",
              f"onsite_regions = {_arr(state['onsite_regions'])}",
              f"remote_countries = {_arr(state['remote_countries'])}",
              f"relocation_channels = {_arr(state['relocation_channels'])}",
              f"onsite_via_route = {'true' if state['onsite_via_route'] else 'false'}"]
    for k in ("region", "region_name"):
        if isinstance(ol.get(k), str) and ol[k].isprintable():
            lines.append(f"{k} = {_s(ol[k])}")
    for r in state["routes"]:
        lines += ["", "[[work_authorization]]", f"country = {_s(r['country'])}", f"route = {_s(r['route'])}",
                  f"on_silent = {_s(r['on_silent'])}", f"on_excludes = {_s(r['on_excludes'])}",
                  f"flag_people_management = {'true' if r['flag_people_management'] else 'false'}",
                  f"flag_title_patterns = {_arr(r['flag_title_patterns'])}"]
        if r["note"]:
            lines.append(f"note = {_s(r['note'])}")
    return "\n".join(lines) + "\n"


def _keep(text):
    """The old file without the sections this step owns. Sections are cut at header lines, so comments stay."""
    out, skipping = [], False
    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            skipping = m.group(1) in OWNED
        if not skipping:
            out.append(line)
    return "\n".join(out).strip("\n")


def _read(path):
    try:
        text = path.read_text(encoding="utf-8")
        return text, tomllib.loads(text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise ValueError(f"{FILE} exists but cannot be read as TOML, so it was left alone. "
                         "Fix or move it first.") from None


def load(data_dir):
    """The saved rules for the form, or blank defaults when nothing usable is saved."""
    p = Path(data_dir) / FILE
    if not p.exists():
        return defaults()
    try:
        cfg = _read(p)[1]
        pay, loc = cfg.get("pay", {}), cfg.get("location", {})
        return validate({"currency": pay.get("currency", "USD"), "floor_salary": pay.get("floor_salary"),
                         "floor_hourly": pay.get("floor_hourly"), "flag_within": pay.get("flag_within", 0),
                         "home_country": loc.get("home_country", ""),
                         "remote_countries": loc.get("remote_countries", []),
                         "onsite_regions": loc.get("onsite_regions", []),
                         "relocation_channels": loc.get("relocation_channels", []),
                         "onsite_via_route": loc.get("onsite_via_route", True),
                         "routes": cfg.get("work_authorization", [])})
    except (ValueError, TypeError, AttributeError):
        return defaults()  # a damaged or hand-edited file shows a blank form; it is only replaced on Save


def save(data_dir, raw):
    state = validate(raw)
    p = Path(data_dir) / FILE
    old_text, old = (_read(p) if p.exists() else ("", {}))
    body = render(state, old)
    kept = _keep(old_text) if old_text else ""
    if "scoring" not in old:
        kept = (kept + "\n\n" if kept else "") + STARTER.strip("\n")
    text = (kept + "\n\n" if kept else "") + body
    new = tomllib.loads(text)  # the result must parse and must not have lost any section it did not own
    for k, v in old.items():
        if k not in OWNED and new.get(k) != v:
            raise ValueError(f"could not keep [{k}] in {FILE} unchanged, so nothing was written")
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)
    return {"written": [FILE]}

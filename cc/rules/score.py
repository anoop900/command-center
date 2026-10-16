#!/usr/bin/env python3
"""Turn a model's per-dimension scores into a decision, using config only.

The model judges (dimension scores, what the posting states). This script decides:
weighted overall score, band, three gates (pay, location, work authorization),
eligibility, and whether to build a kit. Same input, same answer, every run.

Input: a JSON list of scored postings (see SCHEMA below), one per role.
Usage:
  python3 score.py CONFIG.toml scored.json            # prints decisions as JSON
  python3 score.py CONFIG.toml scored.json --out decisions.json

SCHEMA (per posting; unknown values may be null):
{
  "key": "acme|senior cloud architect",
  "title": "Senior Cloud Architect", "channel": "LinkedIn",
  "scores": {"technical": 0-100, "experience": 0-100, "seniority": 0-100, "career": 0-100},
  "pay": {"min": 150000, "max": 180000, "currency": "USD", "basis": "year"|"hour"} | null,
  "location": {"type": "remote"|"hybrid"|"onsite", "country": "US", "region": "..."} | null,
  "work_auth": {"accepts": "yes"|"silent"|"excludes"},
  "people_management": true|false
}
Verdicts: PASS, FLAG (show a warning, keep the role), FAIL (exclude, whatever the score).
"""
import json
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None


def load_config(path):
    if str(path).endswith(".json"):
        return json.load(open(path, encoding="utf-8"))
    if tomllib is None:
        sys.exit("TOML config needs Python 3.11+; use a .json config instead")
    with open(path, "rb") as f:
        return tomllib.load(f)


def overall(scores, weights):
    missing = [k for k in weights if k not in scores or scores[k] is None]
    if missing:
        raise ValueError(f"missing dimension scores: {missing}")
    total = sum(weights.values())
    return round(sum(float(scores[k]) * w for k, w in weights.items()) / total)


def band(score, bands):
    for floor, name in sorted(bands, key=lambda b: -b[0]):
        if score >= floor:
            return name
    return bands[-1][1]


def _v(verdict, reason):
    return {"verdict": verdict, "reason": reason}


def gate_pay(pay, cfg):
    if not pay or pay.get("max") in (None, "") and pay.get("min") in (None, ""):
        return _v("PASS", "pay not stated")
    top = pay.get("max") if pay.get("max") not in (None, "") else pay.get("min")
    cur = (pay.get("currency") or cfg["currency"]).upper()
    if cur == cfg["currency"].upper():
        rate = 1.0
    elif cur in cfg.get("fx", {}):
        rate = float(cfg["fx"][cur])
    else:
        return _v("FLAG", f"no exchange rate configured for {cur}")
    home = float(top) * rate
    hourly = (pay.get("basis") or "year") == "hour"
    floor = cfg["floor_hourly"] if hourly else cfg["floor_salary"]
    unit = "/h" if hourly else "/yr"
    shown = f"{home:,.0f} {cfg['currency']}{unit}"
    if home < floor:
        band_ = float(cfg.get("flag_within", 0) or 0)
        if band_ and home >= floor * (1 - band_):
            return _v("FLAG", f"top of range {shown} is slightly below floor {floor:,} {cfg['currency']}{unit}")
        return _v("FAIL", f"top of range {shown} is below floor {floor:,} {cfg['currency']}{unit}")
    return _v("PASS", f"top of range {shown}")


def gate_location(loc, channel, cfg, routes=()):
    if not loc or not loc.get("type"):
        return _v("FLAG", "location not stated")
    country = (loc.get("country") or "").upper()
    if channel and channel in cfg.get("relocation_channels", []):
        return _v("PASS", f"relocation channel ({channel})")
    if loc["type"] == "remote":
        if country in [c.upper() for c in cfg.get("remote_countries", [])] or not country:
            return _v("PASS", f"remote ({country or 'country not stated'})")
        return _v("FAIL", f"remote but employed from {country}")
    if cfg.get("onsite_via_route") and country in [r["country"].upper() for r in routes]:
        return _v("PASS", f"{loc['type']} in {loc.get('region') or country} (work authorization gate decides)")
    regions = [r.lower() for r in cfg.get("onsite_regions", [])]
    if (loc.get("region") or "").lower() in regions and country in ("", cfg["home_country"].upper()):
        return _v("PASS", f"{loc['type']} in {loc['region']}")
    return _v("FAIL", f"{loc['type']} in {loc.get('region') or country or 'unknown place'}")


def gate_work_auth(post, cfg_loc, routes):
    loc = post.get("location") or {}
    channel = post.get("channel")
    country = (loc.get("country") or cfg_loc["home_country"]).upper()
    if country == cfg_loc["home_country"].upper():
        return _v("PASS", "home country")
    route = next((r for r in routes if r["country"].upper() == country), None)
    if route is None:
        if channel in cfg_loc.get("relocation_channels", []):
            return _v("FLAG", f"relocation to {country}: confirm the visa route")
        return _v("FAIL", f"no work authorization route configured for {country}")
    accepts = ((post.get("work_auth") or {}).get("accepts") or "silent").lower()
    name = route.get("route", "visa")
    if accepts == "excludes":
        return _v(route.get("on_excludes", "fail").upper(), f"posting excludes {name} candidates")
    flags = []
    if accepts == "silent":
        if route.get("on_silent", "flag") == "fail":
            return _v("FAIL", f"posting does not say it accepts {name}")
        flags.append(f"posting does not mention {name}")
    title = (post.get("title") or "").lower()
    if route.get("flag_people_management") and post.get("people_management"):
        flags.append(f"mostly people management: {name} category may not fit")
    hits = [p for p in route.get("flag_title_patterns", []) if p in title]
    if hits:
        flags.append(f"title '{hits[0]}' may not map to a {name} category")
    if flags:
        note = route.get("note")
        return _v("FLAG", "; ".join(flags) + (f". {note}" if note else ""))
    return _v("PASS", f"{name} accepted")


def decide(post, config):
    sc, pay, loc = config["scoring"], config["pay"], config["location"]
    o = overall(post["scores"], sc["weights"])
    gates = {
        "pay": gate_pay(post.get("pay"), pay),
        "location": gate_location(post.get("location"), post.get("channel"), loc, config.get("work_authorization", [])),
        "work_auth": gate_work_auth(post, loc, config.get("work_authorization", [])),
    }
    eligible = all(g["verdict"] != "FAIL" for g in gates.values())
    return {"key": post.get("key"), "overall": o, "band": band(o, sc["bands"]),
            "gates": gates, "eligible": eligible,
            "flags": [f"{n}: {g['reason']}" for n, g in gates.items() if g["verdict"] == "FLAG"],
            "build_kit": eligible and o >= sc["kit_threshold"]}


def decide_batch(posts, config):
    out = [decide(p, config) for p in posts]
    ranked = sorted((d for d in out if d["eligible"]), key=lambda d: -d["overall"])
    for i, d in enumerate(ranked):
        d["rank"] = i + 1
        if i < config["scoring"].get("top_n", 0) and d["overall"] >= config["scoring"].get("top_n_min", 0):
            d["build_kit"] = True
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    result = decide_batch(json.load(open(sys.argv[2], encoding="utf-8")), load_config(sys.argv[1]))
    text = json.dumps(result, indent=1)
    if "--out" in sys.argv:
        open(sys.argv[sys.argv.index("--out") + 1], "w", encoding="utf-8").write(text)
    else:
        print(text)

"""Minimal read-only ICS reader: VEVENT with DTSTART/SUMMARY/LOCATION/DESCRIPTION/UID/EXDATE and simple DAILY/WEEKLY
RRULEs, expanded over a window. Other recurrence rules are skipped and counted. Never raises on bad input."""
import datetime as dt
import re
from zoneinfo import ZoneInfo

DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
INTERVIEW = re.compile(r"\binterview|\bscreen(?:ing)?\b|hiring manager|recruiter|technical round|onsite|"
                       r"final round|panel\b|phone call with", re.I)
MAX_EVENTS = 2000


def _unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _unescape(s):
    return s.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _props(lines):
    """Yield (name, params dict, value) for each content line."""
    for ln in lines:
        head, sep, val = ln.partition(":")
        if not sep:
            continue
        name, *ps = head.split(";")
        yield name.upper(), dict(p.split("=", 1) for p in ps if "=" in p), val


def _when(val, params):
    """datetime or date, or None. Naive values are floating local time."""
    try:
        if len(val) == 8:
            return dt.datetime.strptime(val, "%Y%m%d").date()
        if val.endswith("Z"):
            return dt.datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        d = dt.datetime.strptime(val, "%Y%m%dT%H%M%S")
        if params.get("TZID"):
            try:
                d = d.replace(tzinfo=ZoneInfo(params["TZID"]))
            except Exception:
                pass  # unknown zone: keep floating
        return d
    except ValueError:
        return None


def _day(v):
    return v.date() if isinstance(v, dt.datetime) else v


def _occurrences(start, rule, exdates, win_start, win_end):
    """Starts within [win_start, win_end]; None when the rule is unsupported."""
    sd = _day(start)
    if not rule:
        return [start] if win_start <= sd <= win_end else []
    if rule.get("FREQ") not in ("DAILY", "WEEKLY"):
        return None
    daily = rule["FREQ"] == "DAILY"
    interval = int(rule["INTERVAL"]) if rule.get("INTERVAL", "").isdigit() and int(rule["INTERVAL"]) > 0 else 1
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = _day(_when(rule["UNTIL"], {})) if "UNTIL" in rule and _when(rule["UNTIL"], {}) else None
    byday = [DAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",") if d[-2:] in DAYS] or [sd.weekday()]
    out, n, d = [], 0, sd
    while d <= win_end and (until is None or d <= until) and (d - sd).days < 3650:
        offset = (d - sd).days
        if offset % interval == 0 if daily else (offset // 7 % interval == 0 and d.weekday() in byday):
            n += 1
            if count is not None and n > count:
                break
            if d >= win_start and d not in exdates:
                out.append(dt.datetime.combine(d, start.timetz()) if isinstance(start, dt.datetime) else d)
        d += dt.timedelta(days=1)
    return out


def parse_ics(text, today, days=14):
    """-> (events, skipped). Event: {uid, summary, start (ISO), all_day, location, description, interview}."""
    win_end = today + dt.timedelta(days=days)
    events, skipped, cur = [], 0, None
    for name, params, val in _props(_unfold(text or "")[:500_000]):
        if name == "BEGIN" and val == "VEVENT":
            cur = {"ex": set()}
        elif name == "END" and val == "VEVENT" and cur is not None:
            ev, cur = cur, None
            occ = None if ev.get("start") is None else _occurrences(ev["start"], ev.get("rule"), ev["ex"], today, win_end)
            if occ is None:
                skipped += 1
                continue
            for o in occ:
                text_ = ev.get("summary", "") + " " + ev.get("description", "")
                events.append({"uid": ev.get("uid", ""), "summary": ev.get("summary", ""),
                               "location": ev.get("location", ""), "description": ev.get("description", ""),
                               "all_day": not isinstance(o, dt.datetime), "start": o.isoformat(),
                               "interview": bool(INTERVIEW.search(text_))})
            if len(events) > MAX_EVENTS:
                break
        elif cur is not None:
            if name == "DTSTART":
                cur["start"] = _when(val, params)
            elif name == "RRULE":
                cur["rule"] = dict(p.split("=", 1) for p in val.split(";") if "=" in p)
            elif name == "EXDATE":
                cur["ex"].update(_day(d) for d in (_when(v, params) for v in val.split(",")) if d is not None)
            elif name in ("SUMMARY", "LOCATION", "DESCRIPTION", "UID"):
                cur[name.lower()] = _unescape(val)[:500]
    events.sort(key=lambda e: e["start"])
    return events, skipped


def interviews(events):
    return [e for e in events if e["interview"]]

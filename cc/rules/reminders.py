"""Follow-up reminders from role rows (ported from make_tracker --reminders)."""
import datetime as dt


def _d(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def reminders(rows, today):
    monday = today.weekday() == 0
    out = []
    for r in rows:
        st = r.get("status", "").lower()
        who = f'{r.get("company", "")}, {r.get("title", "")}'
        if not st or "not " in st or "rejected" in st or "closed" in st:
            continue
        fu, ap, found = _d(r.get("follow_up")), _d(r.get("date_applied")), _d(r.get("date_found"))
        if "interview" in st:
            if (fu and fu == today) or (fu is None and monday):
                out.append({"p": 1, "type": "interview check-in", "role": who, "note": r.get("status")})
        elif "applied" in st:
            due = fu or ((ap + dt.timedelta(days=7)) if ap else None)
            if due and due <= today and (today - due).days % 7 == 0:
                days = (today - ap).days if ap else None
                kind = "no reply 3+ weeks: ask for status or close" if days and days >= 21 else "follow up"
                out.append({"p": 2, "type": kind, "role": who, "note": f"applied {days} days ago" if days is not None else "applied"})
        elif st == "ready to apply" and found and (today - found).days in (3, 7):
            out.append({"p": 3, "type": "packet ready, not applied", "role": who, "note": f"built {(today - found).days} days ago"})
    out.sort(key=lambda x: x["p"])
    return out

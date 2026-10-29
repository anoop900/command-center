"""Reasons for not applying: the one place that says what each reason teaches. The dashboard offers these labels
(web/index.html SKIP, checked against this list by tests/test_reasons.py); free text is classified by the same words."""

# group -> lowercase fragments. "title" reasons teach title words (learned.train); "none" teaches nothing.
GROUPS = {
    "title": ("not a match", "level wrong", "too technical", "not interested", "irrelevant", "wrong industry"),
    "level": ("too junior", "too senior"),
    "pay": ("pay too low", "salary not shown", "pay"),
    "place": ("location", "remote / hybrid", "hybrid", "commute"),
    "contract": ("contract vs permanent", "contract"),
    "company": ("company / culture", "bad reviews", "culture"),
    "agency": ("recruiter", "agency"),
    "auth": ("work authorization", "visa"),
    "none": ("already applied elsewhere", "expired", "duplicate"),
}
LABELS = ("Not a match", "Pay too low", "Salary not shown", "Location", "Remote / hybrid mismatch", "Too technical / coding",
          "Level wrong", "Too junior", "Too senior", "Wrong industry", "Contract vs permanent", "Company / culture",
          "Bad reviews", "Recruiter / agency posting", "Work authorization needed", "Posting expired or duplicate",
          "Already applied elsewhere", "Not interested")
PREF = ("level", "pay", "place", "contract", "company", "agency", "auth")      # taught as preferences, not title words


def group_of(reason):
    r = str(reason or "").lower()
    for g, words in GROUPS.items():
        if any(w in r for w in words):
            return g
    return "other"

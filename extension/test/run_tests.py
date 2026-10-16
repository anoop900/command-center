"""Mock-page tests: python3 test/run_tests.py PROFILE_JSON. Exits 1 on any failure."""
import base64, json, os, pathlib, sys
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
profile = json.load(open(sys.argv[1]))
pdf = base64.b64encode(b"%PDF-1.4 test").decode()
SCRIPTS = ["common.js", "adapters/workday.js", "adapters/successfactors.js", "adapters/lever.js", "adapters/ashby.js", "adapters/greenhouse.js"]
fails = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

def run(page, mock, heard="job board", heard_text="Job board", company="Teladoc Health"):
    page.goto((ROOT / "test" / mock).as_uri())
    for s in SCRIPTS: page.add_script_tag(path=str(ROOT / s))
    args = {"profile": profile, "resume": {"name": "Resume.pdf", "folder": "2026-10-07\\Teladoc_VP", "b64": pdf}, "opts": {"heardRe": heard, "heard": heard_text, "company": company}}
    return page.evaluate("a => window.JAF.run(a)", args)

with sync_playwright() as p:
    b = p.chromium.launch(channel=os.environ.get("PW_CHANNEL") or None)  # PW_CHANNEL=chrome|msedge when the bundled Chromium cannot be downloaded
    page = b.new_page()
    r = run(page, "mock_workday.html")
    print(json.dumps(r, indent=1))
    v = lambda sel: page.eval_on_selector(sel, "e => e.value")
    t = lambda sel: page.eval_on_selector(sel, "e => e.innerText.trim()")
    check("wd system", r["system"] == "Workday")
    check("wd first name", v("#name--legalName--firstName") == profile["firstName"])
    check("wd last name", v("#name--legalName--lastName") == profile["lastName"])
    check("wd preferred untouched", v("#pref") == "")
    check("wd city kept (no overwrite)", v("#address--city") == "Toronto")
    check("wd postal", v("#address--postalCode") == profile["postalCode"])
    check("wd province", t("[aria-labelledby=l-prov]") == "Ontario")
    check("wd phone type flagged", t("[aria-labelledby=l-pt]") == "Select One" and any("Device" in f["field"] for f in r["flagged"]))
    check("wd phone code", "Canada (+1)" in page.inner_text("[data-automation-id='formField-countryPhoneCode']"))
    check("wd heard", "Job Board" in page.inner_text("[data-automation-id='formField-source']"))
    check("wd eligible Canada yes", t("[aria-labelledby=q1]") == "Yes")
    check("wd sponsorship no", t("[aria-labelledby=q2]") == "No")
    check("wd US authorized now = No (to check)", t("[aria-labelledby=q3]") == "No" and any("US" in f["why"] for f in r["review"]))
    check("wd US sponsorship flagged with wording", t("[aria-labelledby=q12]") == "Select One" and any("Suggested wording" in f["why"] for f in r["flagged"]))
    check("wd previously applied = No", page.is_checked("#pa2") and not page.is_checked("#pa1"))
    check("wd US citizen = No", t("[aria-labelledby=q9]") == "No")
    check("wd country of citizenship = Canada", t("[aria-labelledby=q13]") == "Canada")
    check("wd bachelor's = Yes (to check)", t("[aria-labelledby=q10]") == "Yes" and any("Bachelor" in f["why"] for f in r["review"]))
    check("wd school not listed -> Other", t("[aria-labelledby=q11]") == "Other")
    check("wd salary blank+flagged", v("#sal") == "" and any("Salary" in f["why"] for f in r["flagged"]))
    check("wd previously worked = No (not a former employer)", page.is_checked("#p2") and not page.is_checked("#p1"))
    check("wd relocate yes", page.is_checked("#r1"))
    check("wd education diploma", t("[aria-labelledby=q7]") == "College Diploma")
    check("wd resume attached", page.eval_on_selector("[data-automation-id=file-upload-input-ref]", "e => e.files.length && e.files[0].name") == "Resume.pdf")
    check("wd gender decline", t("[aria-labelledby=g]") == "I do not wish to answer")
    check("wd disability decline", page.is_checked("#d2") and not page.is_checked("#d1"))
    check("wd terms untouched", not page.is_checked("#terms"))
    check("wd never clicked next/submit", page.evaluate("window.clicked") == [])
    check("wd no errors", r["errors"] == [])

    page = b.new_page()
    r = run(page, "mock_greenhouse.html")
    print(json.dumps(r, indent=1))
    v = lambda sel: page.eval_on_selector(sel, "e => e.value")
    check("gh system", r["system"] == "Greenhouse")
    check("gh names", v("#first_name") == profile["firstName"] and v("#last_name") == profile["lastName"])
    check("gh email/phone", v("#email") == profile["email"] and v("#phone") == profile["phone"])
    check("gh linkedin/web", v("#q_li") == profile["linkedin"] and v("#q_web") == profile["website"])
    check("gh resume only", page.eval_on_selector("#resume", "e => e.files.length") == 1 and page.eval_on_selector("#cover", "e => e.files.length") == 0)
    check("gh auth yes", v("#q1") == "1")
    check("gh sponsor no", v("#q2") == "0")
    check("gh salary blank", v("#q3") == "")
    check("gh why blank+flagged", v("#q4") == "" and any("Free text" in f["why"] for f in r["flagged"]))
    check("gh race decline", v("#q5") == "d")
    check("gh veteran decline", v("#q6") == "d")
    check("gh never submitted", page.evaluate("window.clicked") == [])
    # previously worked: former employer -> Yes; unknown company -> flagged
    page = b.new_page()
    r = run(page, "mock_workday.html", company="Example Bank Financial Group")
    check("wd former employer -> worked Yes", page.is_checked("#p1"))
    page = b.new_page()
    r = run(page, "mock_workday.html", company="")
    check("wd no kit -> worked flagged", not page.is_checked("#p1") and not page.is_checked("#p2") and any("Previously worked" in f["why"] for f in r["flagged"]))
    wh = page.evaluate("p => [JAF.workedHere(p, 'TD Securities'), JAF.workedHere(p, 'Coastal Ltd'), JAF.workedHere(p, 'Sample Corp')]", profile)
    check("workedHere whole words (TD yes, Ltd no, Sample yes)", wh == [True, False, True])

    page = b.new_page()
    r = run(page, "mock_lever.html", company="Acme Media")
    print(json.dumps(r, indent=1))
    v = lambda sel: page.eval_on_selector(sel, "e => e.value")
    check("lv system", r["system"] == "Lever")
    check("lv full name", v("input[name=name]") == f"{profile['firstName']} {profile['lastName']}")
    check("lv email/phone", v("input[name=email]") == profile["email"] and v("input[name=phone]") == profile["phone"])
    check("lv linkedin", v("input[name='urls[LinkedIn]']") == profile["linkedin"])
    check("lv current company untouched", v("input[name=org]") == "")
    check("lv auth yes", page.is_checked("#lv-a1"))
    check("lv worked before = No", v("#lv-w") == "No")
    check("lv why flagged", v("#lv-why") == "" and any("Free text" in f["why"] for f in r["flagged"]))
    check("lv gender decline", v("#lv-g") == "Decline to self-identify")
    check("lv resume attached", page.eval_on_selector("#resume-upload-input", "e => e.files.length") == 1)
    check("lv never submitted", page.evaluate("window.clicked") == [])

    page = b.new_page()
    r = run(page, "mock_ashby.html", company="Acme Robotics")
    print(json.dumps(r, indent=1))
    v = lambda sel: page.eval_on_selector(sel, "e => e.value")
    pr = lambda sel: page.eval_on_selector(sel, "e => e.getAttribute('aria-pressed')") == "true"
    check("ab system", r["system"] == "Ashby")
    check("ab name/email", v("#_systemfield_name") == f"{profile['firstName']} {profile['lastName']}" and v("#_systemfield_email") == profile["email"])
    check("ab location picked from menu", v("#loc").startswith(profile["city"]) and profile["province"] in v("#loc"))
    check("ab auth Yes button", pr("#ay") and not pr("#an"))
    check("ab sponsorship No button", pr("#sn"))
    check("ab bachelor Yes (to check)", pr("#by") and len(r["review"]) >= 1)
    check("ab salary blank", v("#sal") == "")
    check("ab resume attached", page.eval_on_selector("#_systemfield_resume", "e => e.files.length") == 1)
    check("ab never submitted", page.evaluate("window.clicked") == [])

    # Kit matching (lib.js): posting URL beats names; title name match; alias; no match.
    page = b.new_page()
    page.goto((ROOT / "test" / "mock_greenhouse.html").as_uri())
    page.add_script_tag(path=str(ROOT / "lib.js"))
    roles = [
        {"folder": "Wingtip Media Group_EM_FDE", "kit": None},
        {"folder": "Acme Media_Dir", "kit": {"company": "Acme Media", "aliases": ["wmg"], "posting_url": "https://jobs.lever.co/acmemedia/abc123"}},
        {"folder": "Older Co_Role", "kit": {"company": "Older Co", "job_id": "R-55555"}},
    ]
    m = lambda url, title: page.evaluate("([r,u,t]) => jafMatch(r,u,t)", [roles, url, title])
    check("match: posting URL", m("https://jobs.lever.co/acmemedia/abc123/apply", "Wingtip Media Group - Manager") == 1)
    check("match: company in title", m("https://jobs.lever.co/wmgx/zzz/apply", "Wingtip Media Group - Engineering Manager") == 0)
    check("match: job id in URL", m("https://x.wd1.myworkdayjobs.com/en-US/careers/job/R-55555/apply", "Apply") == 2)
    check("match: none", m("https://boards.greenhouse.io/other/jobs/1", "Other Corp") == -1)
    check("match: alias", m("https://careers.example.com/wmg/1", "Careers") == 1)
    b.close()

print(f"\n{len(fails)} failed" if fails else "\nALL PASS")
sys.exit(1 if fails else 0)

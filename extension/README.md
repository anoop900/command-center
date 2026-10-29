# Job Apply Filler (private, unpacked Chrome extension)

Fills job applications on **Workday**, **SAP SuccessFactors**, **Greenhouse**, **Lever** and **Ashby** (and any other site on request) from a local `profile.json`,
attaches the role's resume PDF from the kit folder, and shows the kit's `Application_Kit.md` answers to copy.
**It never clicks Next or Submit.** The person reviews and submits.

## Setup (once)
1. Chrome → `chrome://extensions` → Developer mode → **Load unpacked** → this `extension` folder.
2. Keep the dashboard running (`cc start`). The extension finds it on port 8792 or 8791; if yours is elsewhere, set the address in Options.
3. Pin the extension. Alt+J opens the side panel; **Check connection** says what is wrong, if anything.
Role kits, the resume PDF and your answers come from the dashboard (`/extension/*`, read-only; the one write is Mark applied, sent only when you click it). The old folder method (Options, "Choose folder") and a bundled `profile.json` (`tools/make_profile.py`) remain only as a backup when the dashboard is not running.

## Use
1. Open the application page and sign in yourself.
2. Side panel: check the role kit (auto-picked by company name) and "How did you hear".
3. **Fill this page.** Green = filled, orange = yours. Check, click Next yourself, press Fill again.
4. After submitting: **Mark applied** (on the thank-you page or in the side panel) updates the dashboard; the confirmation email also does it.

## Built-in rules
- Never overwrites a filled field. Refuses to click Next, Submit, Save, Apply, Sign in.
- Salary: blank and flagged. US sponsorship: flagged with the profile's suggested wording.
  Referrals, references, age, date of birth, criminal record, terms checkboxes: left for the person.
- Answered from the profile (v0.3): citizenship, "currently authorized to work in the US", bachelor's yes/no,
  school pickers (falls back to "Other"), field of study, previously applied, previously worked (checked against the
  profile's former employers; flagged when no kit matched, because the company is unknown).
- Colours: green = filled, yellow = filled from a rule worth a second look, orange = yours.
- Self-identification: "decline / prefer not to say".

## Tests
`python test/run_tests.py profile.example.json` (mock pages), `PW_CHANNEL=msedge python test/e2e_dashboard.py` (real extension against a real local dashboard; Chrome 137+ ignores `--load-extension`, so use Edge or Chromium).
SuccessFactors uses the shared engine; not yet tested on a real form.

## v0.2 (Oct 7)
- On Workday, Greenhouse and SuccessFactors pages a blue **Fill this page** button appears bottom-right. One click fills.
  It matches the role kit by company name and attaches that kit's resume (open the side panel once so Chrome
  grants folder access). Use the side panel to pick a kit by hand or copy kit answers.
- Fix: the side panel Fill button lost the click before asking for site access, so nothing happened.

## v0.3 (Oct 9)
- Kit matching reads `kit.json` in each kit folder (written by the kit builder): `{company, aliases, posting_url,
  apply_url, job_id}`. Order: same posting URL, job id in the URL, then the longest company name or alias in the URL
  or title. The on-page button names the kit it found ("Fill · Acme kit").
- Lever and Ashby adapters (mock-tested; first live run still needed).
- Any site: right-click "Fill this page (Job Apply Filler)" or Alt+Shift+F. Chrome grants that tab for the click.
- Values are checked after filling; a value the page rejected is flagged "did not accept".
- After a "thank you for applying" page: a prompt to open the dashboard and mark the role applied.
- Fill log: side panel → Download fill log. Question labels and outcomes only, never answers. Save it to the
  Job Hunting folder so Claude can tune the rules from real forms.
- Regenerate profile.json after pulling: `python tools/make_profile.py <Candidate_Profile.md> profile.json`
  (new fields: citizenship, US answers, bachelor's rule, school, previously applied, former employers).

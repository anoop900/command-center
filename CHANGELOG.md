# Changelog

## 1.1.1

- Glassdoor job alerts are read again (their links moved to country sites such as glassdoor.ca, so no jobs came through).
- Indeed alert emails now give every job they list, not only the first one, and job links wrapped in Indeed's click tracking are opened.
- ZipRecruiter alerts are read again after a change to their links.
- Buttons in alert emails such as "View job" or "Apply now" are no longer added as job titles.
- LinkedIn alerts no longer put the city in the company name. Roles you applied to stay off the board when the same job arrives from another alert or another city, including roles saved before this fix.

## 1.1.0

- Fixed the morning run stopping with "unexpected Conflict" when a role you applied to appeared twice in one fetch; Run now also no longer waits on a run that crashed.
- The demo shifts its dates to today when you load it, so it always looks current (it used to show an empty Today once its fixed dates aged out).
- Roles you applied to stay off the board until a cooldown passes (90 days, `reapply_after_days`) or the posting is reposted; a repost comes back flagged.
- More reasons for Not applying (18 now). Pay, place, level, contract, company and agency reasons turn into suggestions on the Learning page once they repeat.
- Roles you add by hand ask where you found them, and the Learning page shows why the search missed them. Each job alert and feed shows what it sent and which rule failed it.
- A Run now button on Today runs the morning update when the scheduled run failed or did not happen, and says what to do if it stops.
- Draft a message for any role (email, LinkedIn message or connection note), including roles you add by hand and replies to a message you received, with your own instructions. Nothing is sent.
- The statements in a kit that could not be traced to your profile are listed in the role's Details, with the reason.
- The browser extension gets your role kits, resume and answers from the running dashboard, so there is no folder to pick and nothing to regenerate. It says which role it matched and why, lists profile answers you are missing and questions it did not recognise, has a Check connection button, and Mark applied on the thank-you page updates the dashboard (only when you click). The folder method stays as a backup.
- A private and a public install on one computer no longer replace each other's scheduled tasks (each data folder gets its own).

## 1.0.1

- The resume check now fails any date range that is not in your profile, for example a job shown as "Present" after you set an end date.
- `cc service install` keeps the port the dashboard already uses instead of switching back to the default.
- Dates are read in any common order and format (Aug 2024, 15 Aug 2024, 2024/08/15, 08.2024), and only real month names count as months. Dates that cannot be read are reported when you save your profile and in each kit's notes, with the format to use.
- Your resume rules are applied when a kit is built: if you ban the em dash or the word "leverage", your own profile text is tidied so the kit does not fail its own check. Your content file is never rewritten by this.
- Rebuild kit keeps resume content you edited by hand and tells you so.
- Every error now shows a short message and what to do next, in the page and on the command line.
- The database keeps the newest 25 copies of each record and trims the rest when the dashboard starts, so it no longer grows without limit.

## 1.0.0

First release.

- One job board for every role, grouped by stage, with search.
- Screens each role against your own pay, location and work-permission rules, and shows why a role was set aside.
- Drafts a tailored resume for roles you pick, and a one-page cover letter when you press the button, using only facts from your own profile.
- A daily Today page with a short to-do list, follow-ups and tasks. Your choices teach it which roles to show first.
- Setup wizard with made-up demo data you can try first and remove in one click.
- Rebuild a kit at any time from your current profile; the kit it replaces is kept in a "previous" folder.
- Roles you add by hand are listed first, with or without a kit, and never age off the board.
- The Backup page shows where backups, the optional copy folder and kits are stored, lets you restore from either, and has housekeeping settings for how many backups to keep.
- Windows installer that checks your PC first (including free disk space), skips what is already installed, and runs Command Center in the background; matching uninstaller that asks before removing anything.
- Works with no AI, with a chatbot you paste into, or with your own AI account. It never applies, sends email or logs in for you.

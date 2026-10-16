# Changelog

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

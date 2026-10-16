# Setup guide

Install first (see the README: `bootstrap/install.ps1` on Windows, `bootstrap/install.sh` on macOS and Linux). Then
`cc setup` opens the wizard at `http://127.0.0.1:8765/`. It never needs AI to run and you can stop and resume at any
step; **Setup** in the dashboard reopens it.

## The wizard, step by step
1. **Welcome.** Try the demo (a fictional candidate, "Jordan Example") or set up with your own data. The demo is
   removable with one click.
2. **Choose AI.** See [MODES.md](MODES.md). Default is No AI. For Assisted or Autopilot, pick a provider and press
   **Test it**.
3. **Email and calendar.** Email is read with IMAP and an app password, read-only (the app never changes, flags or
   sends mail). Calendar is read from your private ICS feed address. Both have live tests and on-screen steps.
   - Gmail: turn on 2-step verification, then create an app password. Outlook, Yahoo and iCloud have the same idea.
   - Some accounts disable app passwords. If yours does, forward job alerts to a mailbox that supports them.
   - Passwords go to your computer's keyring when one exists, otherwise to a plain user-only file in the data
     folder (not encrypted; `cc doctor` warns). They are never written to `config.toml`.
4. **Your profile.** Upload a resume (.docx, .pdf or .txt) to prefill blanks, or type it. Nothing is used until you
   tick **correct** next to it. In an AI mode, jobs and skills can be drafted from the resume as quoted text, still
   unconfirmed. Resume checks (banned claims, voice rules) are yours to define and live in `qc_rules.json`.
5. **Rules.** Pay, location and work-authorization rules. Roles that fail them are shown under "Failed your rules",
   never silently dropped.
6. **Schedule.** Two daily runs (morning and midday) start on their own and catch up if the computer was off. A
   checkbox also keeps the dashboard running from login at `http://127.0.0.1:8791/`.
7. **Browser extension.** The form-filler reads kits straight from your data folder. It fills forms; you press
   submit. It never submits for you.
8. **First run** on your data.

## Where things live
Your data folder is `~/.commandcenter` (or `CC_HOME`): `config.toml`, `profile.json`, `qc_rules.json`,
`commandcenter.db`, `kits/`, `logs/`, `exports/`. It is private and never part of this repository. Check it is safe
with `cc doctor`; back it up with `cc backup DIR`.

## Everyday commands
`cc run morning|midday` (a run now), `cc web --open` (the dashboard), `cc tracker` (Excel copy), `cc status`,
`cc doctor`, `cc service install|status|remove`. Run `cc --help` for the full list.

## If something fails
See "If setup fails" in the README, then run `cc doctor`.

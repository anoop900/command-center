# Backup, restore and import

- **Half-finished profile:** the profile form saves a draft in your data folder a moment after each edit. Close the tab,
  come back later, and it reopens with what you typed ("We restored the draft"). A draft is not used by any run until you
  press **Save profile**. (Pay and location rules do not autosave yet.)
- **Backups:** Dashboard > **Backup & restore**, or `cc backup create [FILE]`. One `.ccbackup` file holds your database,
  profile, resumes, kits and settings. A backup is made automatically after each morning/midday run (one per day, last 7
  kept in `<data>/backups/`; set `[backup] extra_dir` in `config.toml` to also copy it to a USB or cloud folder).
  `cc backup verify FILE` checks a file; `cc backup list` shows what is kept.
- **Not in any backup:** your email password, calendar link and AI key. They live in your computer's secure store, so after
  a restore on a new computer you enter them again in Setup (the restore result lists which ones). The backup file is not
  encrypted and holds personal data: keep it somewhere private.
- **Restore** (after reinstalling, or on a new computer): Backup & restore > choose the file, or
  `cc backup restore FILE`. If the data folder already has data, restore replaces it, and **first saves your current data to
  `<data>/backups/before-restore-<time>.ccbackup`**; the screen and the command both print that path. To go back, restore
  that file. A damaged, tampered or too-new backup is refused before anything changes.
- A backup in the data folder protects against mistakes, not against losing the computer: download a copy and keep it elsewhere.
- **Bring in data from another tool:** Backup & restore > *Bring in data from another tool*, or
  `cc import-other FILE [--preview] [--yes]`. It reads a spreadsheet of applications (`.csv` or `.xlsx`; it looks for
  Company, Job title/Position, Status, URL, Applied date, Notes and Location columns by name) or a JSON Resume
  (`.json`, jsonresume.org). It shows what it found and what it skips, and saves nothing until you confirm. Roles are
  stored as `imported` (not scored or shown as new until you review them); salary is not imported; running it twice adds
  nothing. A JSON Resume fills your profile **draft** only, with every fact unticked, and never replaces what you typed.
  Limits: 5 MB, 5,000 rows. These readers were tested on made-up files only: no real export from Huntr, Teal, Simplify or
  similar was available, so there are no named presets; if your tool's columns are not recognised, rename them to the
  names above.

# If setup fails

Running the installer again is always safe. If it stops, it says in plain words what happened and saves a short log.

| What you see | What to do |
|---|---|
| "running scripts is disabled on this system" or "is not digitally signed" | Run `powershell -ExecutionPolicy Bypass -File .\bootstrap\install.ps1`. If you downloaded a zip, first run `Get-ChildItem -Recurse . \| Unblock-File` in the unzipped folder. |
| The same error even with `-ExecutionPolicy Bypass` | Your organisation enforces the policy. Use the manual path below; it needs no script. |
| "winget is not available" | Install Python 3.11 or later from python.org (tick "Add python.exe to PATH"), then re-run the script. |
| "Python was installed but this window cannot see it yet" | Close the window and double-click the installer again; the second run finishes the job. |
| The installer stopped and mentions a log | The log is `install-log.txt` in the data folder (`~/.commandcenter`). It lists folder names on your computer, so read it before sharing it. |
| pip stops with `PermissionError ... Access is denied` naming a folder you do not own | A leftover `PYTHONPATH` points at a folder you cannot read. The installer ignores it while it runs. For other pip commands run `$env:PYTHONPATH = $null` first, or remove it under Environment Variables. |
| pip or winget cannot download (offline, proxy) | Connect to the internet or set your proxy (`HTTPS_PROXY`), then re-run. Re-running is safe. |
| `cc` is not recognised in a new terminal | Run `pipx ensurepath`, then open a new terminal. |
| "cannot use the data folder ..." | The data folder (`~/.commandcenter`, or `CC_HOME`) is a file or is not writable. Point `CC_HOME` at a folder you can write to. |
| "cannot listen on 127.0.0.1:PORT" | Something else holds that port. For the dashboard service use `cc service install --port N`. |
| "config error: ..." | `config.toml` in the data folder has a typo; the message says what is wrong. Fix it, or move the file aside and run `cc setup` again. |
| Antivirus blocks or quarantines the background service | Checked on Windows 11 with Microsoft Defender (real-time protection on): no detection, and the hidden login task runs `pythonw` listening on 127.0.0.1 only. Other antivirus products were not tested. If one blocks it, allow the Python in the `pipx` or `venv` folder, or skip the always-on service and run `cc web --open` when you want the dashboard.

**Manual path (no script, any OS):** install Python 3.11 or later, then in this folder run
`python -m pip install --user pipx`, `python -m pipx ensurepath`, open a new terminal, then
`pipx install .` and `cc setup`.

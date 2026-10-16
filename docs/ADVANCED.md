# Advanced install and options

Most people only need the three-step Windows install in the README. This page is for the command line, macOS and Linux, updating, removing, and running in the background.

## Update and remove

To update, run the installer again: your data is untouched and the running copy restarts on the new version by itself
(also after `pipx upgrade`, within a couple of minutes). To remove everything on Windows, double-click `Uninstall Command Center.cmd`. It stops the background dashboard and scheduled runs, removes the program and the Start menu entry, and asks before removing Python, pipx and LibreOffice, but only if the installer added them. Your data folder is kept unless you choose to delete it (a zip copy goes to your Desktop first). Running `bootstrap/uninstall.ps1 -DryRun` shows what it would do. Manual route: `cc uninstall` (add `--delete-data` to delete your data too), then `pipx uninstall cc`.

## Install from PowerShell, macOS or Linux

Prerequisites: Python 3.11 or later (3.12 recommended). The installer adds it for you through winget if it is
missing, and carries on in the same window. If an older Python is already there, it installs a newer one beside it.
In the commands below, `<path-to-this-folder>` is the folder you unzipped or cloned (`git clone <repository-address>`).

```powershell
cd <path-to-this-folder>
.\bootstrap\install.ps1 -DryRun    # optional: prints each step and changes nothing
.\bootstrap\install.ps1
```

Expected output ends with `ALL DONE in <time>. Command Center is running.` and your browser opens Command Center,
the same end state as the easy way above (it runs in the background at the address the window shows, normally
`http://127.0.0.1:8791/`). The macOS / Linux script below instead starts the setup wizard in the window.

### macOS and Linux

```sh
cd <path-to-this-folder>
sh bootstrap/install.sh --dry-run    # optional: prints each step and changes nothing
sh bootstrap/install.sh
```

The script needs Python 3.11 or later (it tells you how to get it if missing; on Debian or Ubuntu also
`python3-venv`). It installs into a private virtual environment under `~/.local/share/command-center`, links `cc`
into `~/.local/bin`, and starts the wizard. It installs nothing system-wide.

By hand, with pipx: `python3 -m pip install --user pipx && python3 -m pipx ensurepath`, open a new terminal, then
`pipx install <path-to-this-folder>` and `cc setup`.

`cc setup` creates the data folder (`~/.commandcenter`, or `CC_HOME`) if needed and serves the wizard on
127.0.0.1. If port 8765 is busy it takes the next free one and prints the address. Running it again resumes the
wizard where you left off. To stop, press Ctrl+C in that window; the wizard's schedule step installs the
background runs.

Check an install with `cc doctor` (an `info` line about the optional `mcp` library is normal). Run it first whenever something looks wrong: it reports the Python version, the
data folder and whether the folder is safe to keep in git.

The ports differ on purpose: the setup wizard starts at 8765 (it takes the next free one if that is busy), while the
always-on dashboard in the next section uses 8791 so its bookmark never moves.

## Choose an AI provider

Only the Autopilot and Assisted modes call an AI provider; No AI is the default and sends nothing anywhere. After
you pick one of those modes, the wizard asks which provider to use: the Claude or Codex command-line app you have
installed, the Anthropic, OpenAI or Google Gemini API, OpenRouter, any other OpenAI-compatible service, Ollama
(runs on your own computer, no key needed), or paste mode (you copy prompts into any chat app yourself). API keys go
to your computer's keyring when one exists, otherwise to a plain file in the data folder (not encrypted); they are
never written to `config.toml`. The wizard has a "Test it" button. Terminal equivalents: `cc ai key`, `cc ai test`.

## Keep the dashboard running

`cc service install` creates one per-user task (no admin rights) that starts the dashboard at login, hidden, restarts
it if it stops, and serves it at a fixed address, `http://127.0.0.1:8791/` by default (`--port N` to change). It never
moves to another port: if something else holds it, the reason is in `<data>/logs/web.log` and your bookmark stays
honest. `cc service status` shows whether it is installed and answering; `cc service remove` stops and deletes it.
Windows and macOS use the OS task manager; on Linux it uses a `@reboot` cron line (no restart on crash). Only Windows
has been tried on a real machine. The installer sets this up for you. After an upgrade the running copy notices the new
code and restarts itself (Windows and macOS by the OS task, Linux by itself); `cc start --reinstall` forces it.

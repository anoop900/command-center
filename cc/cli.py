"""cc: command line for the engine.

  cc init                      create the database (in CC_HOME or ~/.commandcenter)
  cc import FOLDER             load a folder of JSON records (one file per record); safe to repeat
  cc diff FOLDER               compare the database with a folder; exit 1 when they differ
  cc export FOLDER [COLL...]   write the database's records as a folder of JSON files
  cc markdown FILE             write the read-only Markdown copy of open roles
  cc backup DIR [--keep N]     consistent copy of the database only (default keep 7)
  cc backup create [FILE]      full backup of profile, resumes, kits, settings and data (no passwords or API keys)
  cc backup verify FILE        check a backup is complete and undamaged
  cc backup restore FILE [--replace]  restore it; your current data is saved first and its path is printed
  cc backup list               backups kept in the data folder
  cc import-other FILE [--preview] [--yes]  bring in a CSV/XLSX tracker or a JSON Resume (shows what it found first)
  cc status                    record counts per collection and the last finished run
  cc doctor                    check Python, SQLite, MCP library, data folder, git safety
  cc ingest sample [DAYS]      parse recent mail and print what the parsers found (no bodies, nothing stored)
  cc run morning|midday --no-ai  build board/today and board/roles from the data folder (no AI)
  cc setup [--port N] [--no-open]  create the data folder if needed and open the setup wizard
  cc web [--port N] [--open]   serve the dashboard on 127.0.0.1 (default port 8765)
  cc start [--reinstall] [--no-open] [--no-foreground]  run the dashboard in the background (starts at login), wait until it answers, open the browser
  cc open                      same, without reinstalling when it is already running
  cc uninstall [--delete-data [--yes]]  remove the background dashboard and scheduled runs (your data is kept unless asked)
  cc service install [--port N]|remove|status   keep the dashboard running from login (default port 8791)
  cc tracker [PATH]            write the Excel tracker into the data folder
  cc ai test|cards|key         smoke-test the AI runner, list pending paste cards, store the API key (hidden prompt)
  cc ai answer CARD FILE|-     record the answer pasted for a card (validated like any model output)
  cc kit ROLE_ID --posting FILE  tailor, build, check and trace the kit for one role
  cc review|prep|cover ROLE_ID --posting FILE  review of the kit / interview prep / one-page cover letter (needs an AI runner)
  cc ask "QUESTION"            one answer from the configured AI (nothing is stored)
  cc mcp                       run the MCP server on stdio
"""
import json
import os
import threading
import subprocess
import sqlite3
import sys
from pathlib import Path

from . import api, config, importer, problems, secrets, store


def _doctor():
    ok = True

    def line(good, msg):
        nonlocal ok
        ok &= good
        print(("ok    " if good else "FAIL  ") + msg)

    line(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]} (need 3.11+)")
    import sqlite3
    line(sqlite3.sqlite_version_info >= (3, 35), f"SQLite {sqlite3.sqlite_version} (need 3.35+)")
    try:
        import importlib.metadata as md
        v = md.version("mcp")
        line(v.split(".")[0] == "2", f"mcp library {v} (need 2.x)")
    except Exception:
        # Optional extra, only `cc mcp` needs it; a missing library must not fail a fresh install.
        print("info  mcp library not installed (optional, only for `cc mcp`: pip install \"mcp>=2,<3\")")
    for mod, pkg in (("docx", "python-docx"), ("pdfplumber", "pdfplumber"), ("openpyxl", "openpyxl")):
        try:
            __import__(mod)
            line(True, f"{pkg} installed")
        except ImportError:
            line(False, f"{pkg} is missing, so kits cannot be built: run the installer again, or pip install {pkg}")
    from .kits import build_resume
    try:
        build_resume.soffice_path()
        line(True, "LibreOffice found (makes the PDF resume)")
    except RuntimeError as e:
        line(False, str(e))
    p = api.db_path()
    line(True, f"database: {p}")
    try:
        inside = subprocess.run(["git", "-C", str(config.REPO), "check-ignore", "-q", str(p)], stderr=subprocess.DEVNULL).returncode == 0
        line(inside or config.REPO not in p.parents, "database path is ignored by git" if inside else "database outside repo")
    except FileNotFoundError:
        line(True, "git not installed (skipped ignore check)")
    try:
        config.load()
        line(True, f"config: {config.config_path()}" + ("" if config.config_path().exists() else " (absent, defaults)"))
    except config.ConfigError as e:
        line(False, f"config: {e}")
    b = secrets.backend()
    line(b == "keyring", "secrets: OS keyring" if b == "keyring" else
         "secrets: no OS keyring found; the fallback file is NOT encrypted (user-only permissions). Install a keyring backend")
    if "OneDrive" in str(p) or "Dropbox" in str(p):
        line(False, "database is inside a sync folder: move it (set CC_HOME); back up to the sync folder instead")
    return 0 if ok else 1


def _ingest_sample(days):
    """Shows what the parsers make of real mail so misses are visible. Prints sender domain and subject only."""
    import datetime as dt
    from .ingest import alerts, imap, status
    try:
        msgs = imap.fetch(config.load().mail, secrets.get("mail_password"),
                          dt.date.today() - dt.timedelta(days=days))
    except imap.IngestError as e:
        print(f"ingest error: {e}", file=sys.stderr)
        return 1
    for m in msgs:
        jobs, st = alerts.parse_alert(m), status.parse_status(m)
        found = f"{len(jobs)} job(s)" if jobs else (f"status {st['kind']}" if st else "nothing")
        print(f"{str(m['From']).rpartition('@')[2].strip('>')[:30]:30} {str(m['Subject'])[:50]:50} -> {found}")
    print(f"{len(msgs)} message(s) from known senders")
    return 0


def _setup(rest):
    """First-run entry: idempotent. Takes the next free port when the wanted one is busy; prints the URL if no browser opens."""
    from . import web
    port = int(rest[rest.index("--port") + 1]) if "--port" in rest else 8765
    config.init_data_dir()
    config.load()
    srv = None
    for p in range(port, port + 20) if port else (0,):
        try:
            srv = web.make_server(port=p)
            break
        except OSError:
            continue
    if srv is None:
        print(f"no free port from {port} to {port + 19} on 127.0.0.1", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"setup wizard at {url} (Ctrl+C to stop)")
    print("KEEP THIS WINDOW OPEN while you use Command Center. Closing it stops the page. To start it again later, run: cc setup")
    if "--no-open" not in rest:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    print("Setup stopped. To open it again, run: cc setup")
    return 0

def _usage(conn):
    from .ai import budget
    try:
        return budget.status(conn, config.load().budget)
    except config.ConfigError as e:
        return {"error": str(e)}


def _ask(conn, rest):
    from . import ai
    from .ai import features, runners
    from .kits.pipeline import KitError
    if len(rest) != 1:
        print(__doc__)
        return 2
    cfg = config.load()
    try:
        out = features.ask_question(rest[0], None, cfg.mode.name, runners.make_runner(cfg, conn))
    except (KitError, ai.RunnerError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(out.get("answer") or out["reason"])
    return 0 if out["status"] == "ok" else 1


def _role_ai(conn, cmd, rest):
    """Shared by kit, review and prep: ROLE_ID --posting FILE, then the feature under the configured runner."""
    from . import ai
    from .ai import features, runners
    from .kits import pipeline
    if len(rest) != 3 or rest[1] != "--posting":
        print(__doc__)
        return 2
    try:
        posting = Path(rest[2]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"error: cannot read {rest[2]}: {getattr(e, 'strerror', None) or 'not UTF-8 text'}", file=sys.stderr)
        return 2
    cfg = config.load()
    run = {"kit": pipeline.build_kit, "review": features.review_role, "prep": features.prep_role,
           "cover": features.cover_role}[cmd]
    try:
        out = run(conn, rest[0], posting[:pipeline.MAX_INPUT], cfg.mode.name, runners.make_runner(cfg, conn))
    except (pipeline.KitError, ai.RunnerError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if out["status"] in ("ok", "review") else 1


def _web(rest):
    from . import web
    port = int(rest[rest.index("--port") + 1]) if "--port" in rest else 8765
    config.init_data_dir()
    config.load()
    try:
        srv = web.make_server(port=port)
    except OSError as e:
        print(f"cannot listen on 127.0.0.1:{port}: {e}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"dashboard at {url} (Ctrl+C to stop)")
    if "--open" in rest:
        import webbrowser
        webbrowser.open(url)
    changed = []
    if "--watch" in rest:  # the always-on service: restart on new code after an upgrade
        from . import selfwatch
        selfwatch.watch(lambda: (changed.append(1), threading.Thread(target=srv.shutdown, daemon=True).start()))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    if changed:
        from . import scheduler, selfwatch
        print("code changed on disk; restarting on the new version")
        return selfwatch.respawn_if_needed(scheduler.service_command(config.data_dir(), srv.server_address[1], watch=True))
    return 0


def _backup_cmd(rest):
    """Whole-folder backup and restore (cc/backup.py); the older `cc backup DIR` stays the database-only copy."""
    from . import backup
    sub, args = rest[0], [a for a in rest[1:] if not a.startswith("--")]
    d = config.data_dir()
    try:
        if sub == "list":
            for b in backup.listing(d):
                print(f"{b['modified']}  {b['kind']:14} {b['bytes']:>12}  {d / backup.BACKUPS / b['name']}")
            return 0
        if sub == "create":
            if not d.exists():
                print(f"cc backup: no data folder at {d}", file=sys.stderr)
                return 1
            dest = Path(args[0]) if args else d / backup.BACKUPS / f"manual-{backup._stamp()}{backup.EXT}"
            out = backup.create(d, dest)
            print(f"backup written: {out['path']} ({out['files']} files, {out['bytes']} bytes)")
            print("NOTE:", backup.SECRETS_NOTICE)
            print("NOTE:", backup.FILE_NOTICE)
            return 0
        if not args:
            print(f"cc backup {sub}: a backup FILE is required", file=sys.stderr)
            return 2
        if sub == "verify":
            m = backup.verify(args[0])
            print(f"ok: complete backup made {m.get('created', '?')} by version {m.get('app_version', '?')}, {len(m['files'])} files")
            return 0
        replace = "--replace" in rest
        if backup.has_data(d):
            print(f"Your CURRENT data will be saved first to: {backup.planned_safety_path(d).parent}{os.sep}before-restore-<time>.ccbackup")
            print("If the restore is not what you wanted, restore that file to go back.")
        out = backup.restore(args[0], d, replace=replace)
        if out["safety_backup"]:
            print(f"Your previous data was saved to: {out['safety_backup']}")
            print("To go back to it:  cc backup restore", out["safety_backup"], "--replace")
        print(f"restored {out['restored']} files (backup made {out['created']})")
        hints = backup.reentry_hints(config.load())
        print("NOTE:", out["notice"])
        if hints:
            print("Enter again in Setup:", ", ".join(hints))
        return 0
    except (backup.BackupError, OSError, config.ConfigError) as e:
        print(f"cc backup {sub}: {e}", file=sys.stderr)
        return 1


def _service(rest):
    from . import scheduler
    sub = rest[0] if rest else ""
    try:
        if sub == "install":
            port = scheduler._saved_port(config.data_dir())  # a reinstall keeps the port the service already has
            if "--port" in rest:
                i = rest.index("--port") + 1
                port = int(rest[i]) if i < len(rest) and rest[i].isdigit() else -1
            out = scheduler.service_install(port)
        elif sub == "remove":
            out = scheduler.service_remove()
        elif sub == "status":
            out = scheduler.service_status()
        else:
            print("cc service: use install [--port N], remove or status", file=sys.stderr)
            return 2
    except scheduler.SchedulerError as e:
        print(f"cc service: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    if sub == "install" and not out["running"]:
        print(f"not answering yet: wait a few seconds, then check {config.data_dir() / 'logs' / 'web.log'}", file=sys.stderr)
    return 0


def _due(conn, run, now=None):
    """For --catch-up: True when today's scheduled time has passed and today's run has not finished."""
    import datetime as dt
    hh, mm = getattr(config.load().run, run).split(":")
    now = now or dt.datetime.now()
    if now.time() < dt.time(int(hh), int(mm)):
        return False
    doc = store.get(conn, "runs", f"{now.date().isoformat()}-{run}")
    return not (doc and doc["data"].get("finished"))

def _ai(conn, rest):
    from . import ai
    from .ai import runners
    sub = rest[0] if rest else ""
    cfg = config.load()
    if sub == "key":
        import getpass
        print(f"stored in: {secrets.set(runners.KEY_NAME, getpass.getpass('API key (hidden): ').strip())}")
    elif sub == "cards":
        for c in runners.pending_cards(conn):
            print(f"{c['id']}  {c['task']}")
    elif sub == "answer" and len(rest) == 3:
        try:
            text = sys.stdin.read() if rest[2] == "-" else Path(rest[2]).read_text(encoding="utf-8")
        except OSError as e:
            print(f"error: cannot read {rest[2]}: {e.strerror}", file=sys.stderr)
            return 2
        errs = runners.answer_card(conn, rest[1], text)
        for e in errs:
            print(f"rejected: {e}", file=sys.stderr)
        return 1 if errs else 0
    elif sub == "test":
        try:
            runner = runners.make_runner(cfg, conn)
        except ai.RunnerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        r = ai.run_task("ask", {"question": 'Reply with exactly {"answer": "ok"}'}, cfg.mode.name, runner)
        print(f"{r.status} ({r.source or '-'}) {r.reason}".strip())
        return 0 if r.status == "ok" and r.source == "ai" else 1
    else:
        print(__doc__)
        return 2
    return 0


def _fail(e, label="error"):
    """Print a failure as a plain message plus what to do next (details go to the log, not the screen)."""
    p = problems.describe(e)
    print(f"{label}: {p['message']}\n  What to do: {p['fix']}", file=sys.stderr)
    if p["detail"] and p["detail"].rstrip(".").lower() not in p["message"].lower():
        print(f"  Details: {p['detail']}", file=sys.stderr)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = a[0], a[1:]
    from . import ai
    from .kits.pipeline import KitError
    try:
        return _dispatch(cmd, rest)
    except config.ConfigError as e:
        _fail(e, "config error")
        return 2
    except (ValueError, json.JSONDecodeError) as e:
        _fail(e)
        return 2
    except (PermissionError, FileExistsError, FileNotFoundError, NotADirectoryError, IsADirectoryError) as e:   # not network errors
        print(f"cannot use the data folder {config.data_dir()}: {e.strerror or e}. "
              "Set CC_HOME to a folder you can write to, then run the command again.", file=sys.stderr)
        return 2
    except sqlite3.DatabaseError as e:
        if "unable to open" in str(e):
            print(f"cannot open the database in {config.data_dir()}: is the folder writable? Set CC_HOME to one that is.",
                  file=sys.stderr)
            return 2
        print(f"database error: {e}. Restore from a backup (cc backup) or move the database file aside.",
              file=sys.stderr)
        return 3
    except (KitError, ai.RunnerError) as e:
        _fail(e)
        return 2
    except KeyboardInterrupt:
        raise
    except Exception as e:     # last resort: a short message, never a traceback
        print(f"cc: unexpected {type(e).__name__}", file=sys.stderr)
        _fail(e)
        return 3


def _dispatch(cmd, rest):
    if cmd == "doctor":
        return _doctor()
    if cmd == "ingest" and rest[:1] == ["sample"]:
        return _ingest_sample(int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 3)
    if cmd == "web":
        return _web(rest)
    if cmd == "setup":
        return _setup(rest)
    if cmd == "backup" and rest[:1] in (["create"], ["verify"], ["restore"], ["list"]):
        return _backup_cmd(rest)
    if cmd == "service":
        return _service(rest)
    if cmd in ("start", "open"):
        from . import lifecycle
        return lifecycle.start(rest, foreground=_setup)
    if cmd == "uninstall":
        from . import lifecycle
        return lifecycle.uninstall(rest)
    if cmd == "mcp":
        from .mcp_server import main as run
        run()
        return 0
    if cmd == "init":
        config.init_data_dir()
    config.load()
    conn = store.connect(api.db_path())
    try:
        return _db_cmd(conn, cmd, rest)
    finally:
        conn.close()  # Windows cannot delete a database that is still open


def _import_other(conn, rest):
    from . import thirdparty
    files = [a for a in rest if not a.startswith("--")]
    if len(files) != 1:
        print("cc import-other: give one FILE (.csv, .xlsx or JSON Resume .json)", file=sys.stderr)
        return 2
    d = api.db_path().parent
    try:
        pv = thirdparty.preview(conn, d, files[0])
        if pv["kind"] == "resume":
            print("JSON Resume: would add to your profile DRAFT:", ", ".join(pv["draft_adds"]) or "nothing new")
        else:
            print(f"found {pv['roles']} new roles, {pv['duplicates']} already known, {pv['skipped_count']} skipped")
            print("columns used:", ", ".join(f"{k}={v}" for k, v in pv["mapped"].items()))
            for r in pv["sample"][:10]:
                print(f"  {r['company']} | {r['title']} | {r['status']}")
            for s in pv["skipped"][:10]:
                print(f"  skipped row {s['row']}: {s['reason']}")
        for n in pv["notes"]:
            print("NOTE:", n)
        if "--preview" in rest:
            print("preview only: nothing was written")
            return 0
        if "--yes" not in rest and input("Write this? [y/N] ").strip().lower() not in ("y", "yes"):
            print("nothing written")
            return 0
        out = thirdparty.apply(conn, d, files[0])
        print("imported", out["roles"], "roles" if out["kind"] == "tracker" else "(profile draft updated)")
        return 0
    except (thirdparty.ImportError_, ValueError, OSError, EOFError) as e:
        print(f"cc import-other: {e}", file=sys.stderr)
        return 1


def _db_cmd(conn, cmd, rest):
    if cmd == "import-other":
        return _import_other(conn, rest)
    if cmd in ("import", "diff", "export", "markdown", "backup") and not rest:
        print(f"cc {cmd}: a path argument is required", file=sys.stderr)
        return 2
    is_zip = False
    if cmd in ("import", "diff") and not is_zip and not Path(rest[0]).is_dir():
        print(f"cc {cmd}: {rest[0]} is not a folder", file=sys.stderr)
        return 2
    if cmd == "init":
        print(f"data folder ready: {config.data_dir()}")
        print(f"database ready: {api.db_path()}")
    elif cmd == "import":
        print(json.dumps(importer.import_folder(conn, rest[0]), indent=1))
    elif cmd == "diff":
        d = importer.diff(conn, rest[0])
        print(json.dumps(d[:200], indent=1, ensure_ascii=False))
        print(f"{len(d)} difference(s)")
        return 1 if d else 0
    elif cmd == "export":
        print(f"{importer.export_folder(conn, rest[0], rest[1:] or None)} records written")
    elif cmd == "markdown":
        print(f"{api.export_markdown(conn, rest[0])} open roles written to {rest[0]}")
    elif cmd == "backup":
        keep = int(rest[rest.index("--keep") + 1]) if "--keep" in rest else 7
        print(f"backup: {store.backup(conn, rest[0], keep)}")
    elif cmd == "tracker":
        from . import tracker
        try:
            n, out = tracker.export(conn, rest[0] if rest else None)
        except tracker.TrackerError as e:
            print(f"tracker: {e}", file=sys.stderr)
            return 1
        print(f"{n} role(s) written to {out}")
    elif cmd == "run" and rest[:1] in (["morning"], ["midday"]):
        from . import ai, modes, tracker
        from .ai import runners
        from .runs import morning, sources
        cfg = config.load()
        if "--catch-up" in rest and not _due(conn, rest[0]):
            print(json.dumps({"skipped": "already ran today or not due yet"}))
            return 0
        api.run_heartbeat(conn, rest[0], "start")
        ai_arg = None
        if modes.schedules_ai(cfg.mode.name) and "--no-ai" not in rest:  # other modes would queue paste cards every run
            try:
                ai_arg = (cfg.mode.name, runners.make_runner(cfg, conn))
            except ai.RunnerError as e:
                print(f"note: AI steps skipped: {e}", file=sys.stderr)
        result = morning.run(conn, rest[0], tracker_path=tracker.default_path(), ai=ai_arg, coach_dir=config.data_dir(), files=(api.db_path(), config.config_path()), **sources.gather(config.load(), rest[0], contact_emails=[d["data"].get("email", "") for d in store.list_docs(conn, "contacts")]))
        try:
            from . import backup
            for note in backup.auto(config.data_dir(), cfg.backup.keep, cfg.backup.extra_dir, keep_manual=cfg.backup.keep_manual):
                print(f"note: {note}", file=sys.stderr)
        except (OSError, ValueError) as e:  # a failed backup must never fail the run
            print(f"note: automatic backup skipped: {e}", file=sys.stderr)
        api.run_heartbeat(conn, rest[0], "finish", info={k: result[k] for k in ("roles", "todos", "notes") if k in result})
        print(json.dumps(result, indent=1))
    elif cmd == "ai":
        return _ai(conn, rest)
    elif cmd == "kit":
        return _role_ai(conn, "kit", rest)
    elif cmd == "ask":
        return _ask(conn, rest)
    elif cmd in ("review", "prep", "cover"):
        return _role_ai(conn, cmd, rest)
    elif cmd == "status":
        print(json.dumps({"collections": store.collections(conn), "last_run": api.last_finished_run(conn),
                          "ai_usage": _usage(conn)}, indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Local dashboard server: 127.0.0.1 only. Serves web/index.html, the db shim, a small REST API over the store and
kit files from the data folder. No auth: anything that can reach loopback can already read the data folder, so the
guards below only stop other websites (DNS rebinding, cross-site writes)."""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import threading
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .rules import job_key
from . import aiprov, api, assets, jobs, backup, coach, config, drafts, learned, learning, modes, problems, profile, qcrules, resume, rules_cfg, runnow, scheduler, store, thirdparty, wizard
from .ai import RunnerError, budget, features, runners
from .kits.pipeline import KitError
from .runs import actions, board
from . import extension_api, kitfolder, kitlink

WEB = assets.find("web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/shim.js": ("shim.js", "text/javascript; charset=utf-8"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml"), "/favicon.ico": ("favicon.svg", "image/svg+xml")}
SETTLE_POSTS = ("/wizard/demo/clear", "/wizard/profile", "/wizard/rules", "/wizard/qcrules", "/wizard/resume")
BACKUP_POSTS = ("/backup/create", "/backup/restore", "/backup/restore-local", "/backup/settings", "/backup/open")
MAX_BACKUP_UPLOAD = 512 << 20
LEARNED_POSTS = ("/learned",)
COACH_POSTS = ("/coach/plan", "/coach/reword", "/coach/done", "/coach/skip", "/coach/restore")
IMPORT_OTHER_POSTS = ("/import-other/preview", "/import-other/apply")
IMPORT_EXTS = (".csv", ".tsv", ".txt", ".xlsx", ".json")
MAX_BODY = 1 << 20
WIZARD_POSTS = ("/wizard/demo", "/wizard/demo/clear", "/wizard/step", "/wizard/mode", "/wizard/mail/test",
                "/wizard/mail", "/wizard/calendar/test", "/wizard/calendar", "/wizard/skip", "/wizard/profile", "/wizard/resume", "/wizard/qcrules", "/wizard/rules",
                "/wizard/draft", "/wizard/schedule", "/wizard/ai", "/wizard/ai/test", "/wizard/extension", "/wizard/firstrun", "/wizard/finish")

MAX_BODY_ASK = 256 << 10
MAX_BODY_KITS = 16 << 20  # kit files travel as base64 inside a doc
BAD_ID = re.compile(r"[\x00-\x1f/\\]")


def _raise_explorer(title):
    """Windows keeps a background service's new window behind the browser; a tapped Alt key lets us bring it forward. Best effort."""
    import ctypes
    import time
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(h, _):
        cls, txt = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(512)
        u.GetClassNameW(h, cls, 64)
        u.GetWindowTextW(h, txt, 512)
        if cls.value == "CabinetWClass" and txt.value.startswith(title + " - "):
            found.append(h)
        return True

    for _ in range(20):
        time.sleep(0.15)
        u.EnumWindows(visit, 0)
        if found:
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x12, 0, 2, 0)
            u.ShowWindow(found[0], 9)
            u.SetForegroundWindow(found[0])
            return


_SETTLE = threading.Lock()


def _settle(conn):
    """Make a button press take effect now. The press is already saved, so a failure here only means the next run applies it."""
    with _SETTLE:
        try:
            actions.settle(conn, dt.date.today())
        except (store.Conflict, sqlite3.Error, ValueError, KeyError) as e:
            print(f"could not apply the button press yet ({type(e).__name__}); the next run will", flush=True)


def _open_folder(p):
    if sys.platform == "win32":
        os.startfile(p)
        _raise_explorer(Path(p).name)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])


class _Handler(BaseHTTPRequestHandler):
    folder_opener = None  # tests inject a fake; None means the real file manager
    db_file = None
    gather = None  # tests inject a fake source fetch; None means the real mail and calendar
    @property
    def kits_dir(self):
        return config.kits_dir(self.cfg_file.parent)

    ext_src = None  # tests point this at a fixture; None means the extension next to the package
    mail_opener = None  # tests inject a fake IMAP connection; None means the real one
    cal_fetcher = None
    ai_runner = None  # tests inject a fake model runner; None means the configured one
    sched_runner = None  # tests inject a fake OS scheduler; None means the real one
    sched_platform = None
    run_popen = None     # tests inject a fake process launcher for Run now
    server_version = "cc-web"

    def log_message(self, *_):
        pass

    def handle(self):
        try:
            super().handle()
        except ConnectionError:
            pass  # the browser went away mid-request; nothing to answer

    def _send(self, code, body=b"", ctype="application/json", extra=()):
        if not isinstance(body, bytes):
            body = json.dumps(problems.for_body(body), ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _drain(self):
        """Read an unwanted request body before answering, so the client sees the reply and not a reset."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if 0 < n <= MAX_BODY:
            self.rfile.read(n)

    def _guard(self, write):
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._drain()
            self._send(403, {"error": "bad host"})
            return False
        origin = self.headers.get("Origin")
        # The browser extension is the one outside caller: its Origin is chrome-extension://<id> (a web page cannot
        # send that), and it must say so in a header, for the one extension write only.
        ext = (origin or "").startswith("chrome-extension://") and self.headers.get("X-CC-Extension") == "1"             and urlsplit(self.path).path == "/extension/applied"
        if write and origin is not None and not ext and urlsplit(origin).netloc not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._drain()
            self._send(403, {"error": "cross-site write refused"})
            return False
        return True

    def _route(self):
        u = urlsplit(self.path)
        return unquote(u.path), parse_qs(u.query)

    def _target(self, path):
        """('api', collection, doc_id|None) or None. Unknown collections and odd ids are rejected."""
        parts = path.split("/")[2:]
        if not 1 <= len(parts) <= 2 or parts[0] not in store.COLLECTIONS:
            return None
        doc_id = parts[1] if len(parts) == 2 else None
        if doc_id is not None and (not doc_id or doc_id in (".", "..") or BAD_ID.search(doc_id) or len(doc_id) > 200):
            return None
        return parts[0], doc_id

    def _body(self, collection):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            self._send(400, {"error": "bad Content-Length"})
            return None
        if n > (MAX_BODY_KITS if collection == "kitfiles" else MAX_BODY):
            self._send(413, {"error": "body too large"})
            return None
        try:
            data = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._send(400, {"error": "body must be a JSON object"})
            return None
        return data

    def _safely(self, fn):
        """Any unexpected crash becomes a short answer with a next step, never a dropped connection or a traceback."""
        try:
            fn()
        except ConnectionError:
            raise
        except Exception as e:
            try:
                print(f"cc web: {self.command} {self.path.split('?')[0]} failed: {type(e).__name__}: {e}", file=sys.stderr)
                self._send(500, {**problems.describe(e), "error": problems.describe(e)["message"]})
            except Exception:
                pass

    def do_GET(self):
        self._safely(self._get)

    def do_POST(self):
        self._safely(self._post)

    def _get(self):
        if not self._guard(False):
            return
        path, q = self._route()
        if path in STATIC:
            name, ctype = STATIC[path]
            f = WEB / name
            if not f.exists():
                return self._send(404, {"error": "missing"})
            # The page fills in its own title from config/settings later; until then show a real name, not the template.
            return self._send(200, f.read_bytes().replace(b"{{TITLE}}", b"Command Center"), ctype)
        if path == "/mode":
            try:
                cfg = config.load(self.cfg_file)
                panel = modes.panel(cfg.mode.name)
                panel["ai_missing"] = modes.uses_provider(cfg.mode.name) and cfg.ai.runner == "none"
                conn = store.connect(self.db_file)
                try:
                    if cfg.mode.name != "no-ai":
                        panel["usage"] = budget.status(conn, cfg.budget)
                finally:
                    conn.close()
                return self._send(200, panel)
            except config.ConfigError as e:
                return self._send(200, {"error": str(e)})
        if path == "/backup/list":
            b, d = self._backup_cfg(), self.cfg_file.parent
            return self._send(200, {"backups": backup.listing(d, b.extra_dir), "notice": backup.SECRETS_NOTICE,
                                    "file_notice": backup.FILE_NOTICE,
                                    "folders": backup.folders(d, b.extra_dir, self.kits_dir),
                                    "settings": {"keep": b.keep, "keep_manual": b.keep_manual, "extra_dir": b.extra_dir},
                                    "next_safety": str(backup.planned_safety_path(d))})
        if path.startswith("/backup/download/"):
            try:
                where = parse_qs(urlsplit(self.path).query).get("where", ["data"])[0]
                f = backup.local_path(self.cfg_file.parent, path[len("/backup/download/"):], self._backup_cfg().extra_dir, where)
            except backup.BackupError as e:
                return self._send(404, {"error": str(e)})
            return self._send(200, f.read_bytes(), "application/octet-stream",
                              [("Content-Disposition", f'attachment; filename="{f.name}"')])
        if path.startswith("/extension/"):
            return self._extension_get(path)
        if path == "/wizard/profile":
            draft = drafts.read(self.cfg_file.parent, "profile")
            return self._send(200, {"state": draft or profile.load(self.cfg_file.parent), "draft": bool(draft),
                                    "fields": [{"key": k, "label": lb, "group": g, "kind": kd} for k, lb, g, kd in profile.FIELDS]})
        if path == "/wizard/rules":
            return self._send(200, {"state": rules_cfg.load(self.cfg_file.parent)})
        if path == "/wizard/qcrules":
            try:
                return self._send(200, qcrules.load(self.cfg_file.parent))
            except ValueError as e:
                return self._send(200, {"error": str(e)})
        if path == "/wizard/schedule":
            try:
                cfg = config.load(self.cfg_file)
            except config.ConfigError as e:
                return self._send(200, {"error": str(e)})
            return self._send(200, {"times": scheduler.current_times(cfg), "mode": cfg.mode.name,
                                    "status": scheduler.status(self.sched_runner or scheduler.system_runner, self.sched_platform,
                                                                self.cfg_file.parent),
                                    "service": scheduler.service_status(self.sched_runner or scheduler.system_runner,
                                                                        self.sched_platform, self.cfg_file.parent),
                                    "runs_ai": modes.schedules_ai(cfg.mode.name)})
        if path == "/wizard/ai":
            try:
                return self._send(200, aiprov.options(self.cfg_file))
            except config.ConfigError as e:
                return self._send(200, {"error": str(e)})
        if path == "/wizard/extension":
            return self._send(200, wizard.extension_status(self.cfg_file.parent, self.ext_src))
        if path == "/wizard/providers":
            return self._send(200, wizard.PROVIDERS)
        if path == "/wizard/modes":
            try:
                return self._send(200, {**modes.table(), "current": config.load(self.cfg_file).mode.name})
            except config.ConfigError as e:
                return self._send(200, {**modes.table(), "current": None, "error": str(e)})
        if path == "/kits-folder":
            return self._send(200, kitfolder.current(self.cfg_file.parent))
        if path == "/profile/status":
            return self._send(200, dict(profile.completeness(self.cfg_file.parent), kits_dir=str(self.kits_dir)))
        if path == "/coach":
            conn = store.connect(self.db_file)
            try:
                return self._send(200, self._coach_snap(conn))
            finally:
                conn.close()
        if path == "/run-now":
            conn = store.connect(self.db_file)
            try:
                return self._send(200, runnow.status(conn, self.cfg_file.parent))
            finally:
                conn.close()
        if path == "/learned":
            conn = store.connect(self.db_file)
            try:
                return self._send(200, learned.view(conn, self._jobs()))
            finally:
                conn.close()
        if path == "/learning":
            conn = store.connect(self.db_file)
            try:
                return self._send(200, learning.analyze(conn, self.cfg_file.parent))
            finally:
                conn.close()
        if path == "/wizard":
            conn = store.connect(self.db_file)
            try:
                st = wizard.state(conn, self.cfg_file.parent)
                try:
                    mode = config.load(self.cfg_file).mode.name
                except config.ConfigError:
                    mode = modes.DEFAULT
                st["ask_note"] = modes.ASK_NOTES.get(mode, "")
                return self._send(200, st)
            finally:
                conn.close()
        if path.startswith("/kits/"):
            return self._kit(path[len("/kits/"):])
        if not path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        t = self._target(path)
        if t is None:
            return self._send(404, {"error": "unknown collection or id"})
        col, doc_id = t
        conn = store.connect(self.db_file)
        try:
            if doc_id is not None:
                row = store.get(conn, col, doc_id)
                return self._send(200, row) if row else self._send(404, {"error": "no such document"})
            rows = store.list_docs(conn, col)
        finally:
            conn.close()
        stamp = f"{max((r['updated_at'] for r in rows), default='')}|{len(rows)}|{sum(r['version'] for r in rows)}"
        if q.get("since") == [stamp]:
            return self._send(200, {"stamp": stamp, "unchanged": True})
        self._send(200, {"stamp": stamp, "docs": rows})

    def _extension_get(self, path):
        """Read-only answers for the browser extension: health, open roles with a kit, profile answers, a resume PDF."""
        conn = store.connect(self.db_file)
        try:
            if path == "/extension/health":
                ready = bool(profile.profile_json(profile.load(self.cfg_file.parent)).get("firstName"))
                return self._send(200, {"ok": True, "roles": len(extension_api.roles(conn, self.kits_dir)), "profile": ready})
            if path == "/extension/roles":
                return self._send(200, {"roles": extension_api.roles(conn, self.kits_dir)})
            if path == "/extension/profile":
                state = profile.load(self.cfg_file.parent)
                have = profile.profile_json(state)
                return self._send(200, {"profile": have, "missing": [{"key": k, "label": lb} for k, lb, _, _ in profile.FIELDS
                                                                      if k not in have]})
            if path.startswith("/extension/resume/"):
                f = extension_api.resume_file(conn, self.kits_dir, path[len("/extension/resume/"):])
                if f is None:
                    return self._send(404, {"error": "no resume for that role", "fix": "Build the kit for this role first."})
                return self._send(200, f.read_bytes(), "application/pdf", [("X-Resume-Name", f.name)])
            return self._send(404, {"error": "not found"})
        finally:
            conn.close()

    def _extension_applied(self):
        """Body: {key}. Sent only when the person clicks Mark applied on a thank-you page."""
        data = self._json_body(MAX_BODY)
        if data is None:
            return
        conn = store.connect(self.db_file)
        try:
            out = extension_api.mark_applied(conn, data.get("key"))
        except (store.Conflict, sqlite3.Error):
            return self._send(503, {"error": "database busy", "fix": "Try again in a moment."})
        finally:
            conn.close()
        if out is None:
            return self._send(404, {"error": "no such role", "fix": "Open the dashboard and mark it applied there."})
        self._send(200, out)

    def _kit(self, name):
        root = self.kits_dir.resolve()
        try:
            f = (root / name).resolve()
            ok = root in f.parents and f.is_file()
        except (ValueError, OSError):      # a null byte or an unreadable name raises on Python 3.11/3.12
            ok = False
        if not ok:
            return self._send(404, {"error": "no such kit file"})
        self._send(200, f.read_bytes(), "application/octet-stream",
                   [("Content-Disposition", f'attachment; filename="{f.name}"')])

    def _wizard(self, path):
        data = self._body("wizard")
        if data is None:
            return
        conn = store.connect(self.db_file)
        try:
            if path == "/wizard/demo":
                out = wizard.load_demo(conn, data_dir=self.cfg_file.parent)
            elif path == "/wizard/demo/clear":
                out = wizard.clear_demo(conn)
            elif path in ("/wizard/mail/test", "/wizard/mail"):
                args = (data.get("host"), data.get("user"), data.get("folder"), data.get("password"))
                out = (wizard.check_mail(*args, opener=self.mail_opener) if path.endswith("/test")
                       else wizard.save_mail(self.cfg_file, *args, opener=self.mail_opener))
            elif path in ("/wizard/calendar/test", "/wizard/calendar"):
                out = (wizard.check_calendar(data.get("url"), self.cal_fetcher) if path.endswith("/test")
                       else wizard.save_calendar(self.cfg_file, data.get("url"), self.cal_fetcher))
            elif path == "/wizard/draft":
                if not isinstance(data.get("form"), str) or data["form"] not in drafts.FORMS or not isinstance(data.get("data"), dict):
                    raise ValueError("form: unknown form, or data missing")
                out = drafts.write(self.cfg_file.parent, data["form"], data["data"])
            elif path == "/wizard/profile":
                out = profile.save(self.cfg_file.parent, data)
            elif path == "/wizard/rules":
                out = rules_cfg.save(self.cfg_file.parent, data)
            elif path == "/wizard/qcrules":
                out = qcrules.save(self.cfg_file.parent, data)
            elif path == "/wizard/resume":
                raw = resume.decode_upload(data.get("data"))
                text = resume.extract_text(data.get("name"), raw)
                resume.store(self.cfg_file.parent, data.get("name"), raw)
                cfg = config.load(self.cfg_file)
                out = {"fields": resume.draft(text),
                       "ai": resume.draft_ai(text, cfg.mode.name, self.ai_runner or runners.make_runner(cfg, conn))}
            elif path == "/wizard/schedule":
                runner = self.sched_runner or scheduler.system_runner
                if data.get("action") == "remove":
                    out = {"status": scheduler.remove(runner, self.sched_platform, self.cfg_file.parent)}
                elif data.get("action") == "install":
                    out = {"status": scheduler.install(config.load(self.cfg_file), data, runner, self.sched_platform,
                                                       self.cfg_file.parent)}
                elif data.get("action") == "service_install":
                    out = {"service": scheduler.service_install(scheduler._saved_port(self.cfg_file.parent), runner,
                                                                self.sched_platform, self.cfg_file.parent)}
                elif data.get("action") == "service_remove":
                    out = {"service": scheduler.service_remove(runner, self.sched_platform, self.cfg_file.parent)}
                else:
                    raise ValueError("action must be install, remove, service_install or service_remove")
            elif path == "/wizard/ai":
                out = aiprov.save(self.cfg_file, data)
            elif path == "/wizard/ai/test":
                out = aiprov.test(self.cfg_file, conn, self.ai_runner)
            elif path == "/wizard/extension":
                out = wizard.prepare_extension(self.cfg_file.parent, self.ext_src)
            elif path == "/wizard/firstrun":
                out = wizard.first_run(conn, self.cfg_file, self.gather)
            elif path == "/wizard/finish":
                out = wizard.finish(conn)
            elif path == "/wizard/skip":
                out = wizard.skip_step(conn, data.get("step"))
            elif path == "/wizard/mode":
                out = wizard.change_mode(conn, self.cfg_file, data.get("name"), self.sched_runner, self.sched_platform)
            else:
                out = wizard.go(conn, data.get("step"), data.get("complete") is True)
            if path in SETTLE_POSTS:
                wizard.settle(conn, self.cfg_file.parent)
            out = {**out, **wizard.state(conn, self.cfg_file.parent)}
        except (ValueError, OSError, config.ConfigError) as e:
            return self._send(400, {"error": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": "database busy"})
        finally:
            conn.close()
        self._send(200, out)

    def _post(self):
        if not self._guard(True):
            return
        path, _ = self._route()
        if path in WIZARD_POSTS:
            return self._wizard(path)
        if path == "/review/apply":
            return self._review_apply()
        if path in BACKUP_POSTS:
            return self._backup(path)
        if path in LEARNED_POSTS:
            return self._learned()
        if path in COACH_POSTS:
            return self._coach(path)
        if path in IMPORT_OTHER_POSTS:
            return self._import_other(path)
        if path == "/job":
            return self._job()
        if path == "/add-role":
            return self._add_role()
        if path == "/run-now":
            return self._run_now()
        if path == "/extension/applied":
            return self._extension_applied()
        if path == "/outreach":
            return self._outreach()
        if path == "/kits-folder":
            return self._kits_folder()
        if path == "/kits-folder/open":
            return self._kits_open()
        if path != "/ask":
            self._drain()
            return self._send(404, {"error": "not found"})
        self._ask()

    def _jobs(self):
        return [j for j in profile.load(self.cfg_file.parent)["jobs"] if j.get("ok")]

    def _learned(self):
        """Body: {action: forget|restore|ai|exclude|include|targets|levels, term|on|items}. Changes what is learned, never a hard rule."""
        data = self._body("wizard")
        if data is None:
            return
        act, conn = data.get("action"), store.connect(self.db_file)
        try:
            if act in ("forget", "restore"):
                if not isinstance(data.get("term"), str):
                    return self._send(400, {"error": "term: text expected"})
                learned.forget(conn, data["term"], restore=act == "restore")
            elif act == "ai":
                if not isinstance(data.get("on"), bool):
                    return self._send(400, {"error": "on: true or false expected"})
                learned.set_ai(conn, data["on"])
            elif act in ("exclude", "include", "targets"):
                learned.set_list(conn, act, data.get("items"))
            elif act == "levels":
                learned.set_levels(conn, data.get("items"))
            else:
                return self._send(400, {"error": "unknown action"})
            return self._send(200, learned.view(conn, self._jobs()))
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": "database busy"})
        finally:
            conn.close()

    def _kits_folder(self):
        """Body: {path}. Blank path = back to the default. Creates the folder, moves the existing kits, saves the setting."""
        data = self._body("wizard")
        if data is None:
            return
        if not isinstance(data.get("path"), str):
            return self._send(400, {"error": "path: text expected"})
        try:
            return self._send(200, kitfolder.change(self.cfg_file.parent, self.cfg_file, data.get("path", "")))
        except (kitfolder.KitFolderError, config.ConfigError) as e:
            return self._send(400, {"error": str(e)})
        except (OSError, sqlite3.Error) as e:
            return self._send(500, {"error": f"could not change the kit folder ({type(e).__name__}); nothing was lost"})

    def _kits_open(self):
        """Body: {sub}. Opens the kit folder (or one role's folder inside it) in the file manager. Never leaves the kit folder."""
        data = self._body("wizard")
        if data is None:
            return
        sub = data.get("sub", "")
        if not isinstance(sub, str) or len(sub) > 300:
            return self._send(400, {"error": "sub: text expected"})
        root = self.kits_dir.resolve()
        target = (root / sub).resolve() if sub else root
        if target != root and root not in target.parents:
            return self._send(400, {"error": "that folder is not inside the kit folder"})
        if not target.is_dir():
            return self._send(404, {"error": "that folder is not on this computer yet"})
        try:
            (self.folder_opener or _open_folder)(target)
        except OSError as e:
            return self._send(500, {"error": f"could not open the folder ({type(e).__name__})"})
        return self._send(200, {"ok": True})

    def _import_other(self, path):
        """Body = the file's bytes, ?name= its file name (only the extension is used). Preview writes nothing."""
        ext = Path(parse_qs(urlsplit(self.path).query).get("name", [""])[0]).suffix.lower()
        try:
            n = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            n = -1
        if ext not in IMPORT_EXTS or not 0 < n <= thirdparty.MAX_BYTES:
            self._drain()
            return self._send(400 if n <= thirdparty.MAX_BYTES else 413,
                              {"error": "send a .csv, .xlsx or .json file (5 MB at most) as the request body, with ?name=FILE"})
        d = self.cfg_file.parent
        conn = store.connect(self.db_file)
        try:
            with tempfile.TemporaryDirectory(dir=d) as work:
                f = Path(work) / ("upload" + ext)
                body = self.rfile.read(n)
                if len(body) != n:
                    return self._send(400, {"error": "the upload stopped early"})
                f.write_bytes(body)
                fn = thirdparty.preview if path.endswith("preview") else thirdparty.apply
                return self._send(200, fn(conn, d, f))
        except (thirdparty.ImportError_, ValueError, OSError) as e:
            return self._send(400, {"error": str(e)})
        finally:
            conn.close()

    def _backup_cfg(self):
        """The [backup] settings; a corrupt config.toml falls back to the defaults so the page still opens."""
        try:
            return config.load(self.cfg_file).backup
        except config.ConfigError:
            return config.Backup()

    def _backup_settings(self, d):
        """Body: {keep, keep_manual, extra_dir}. Saves the housekeeping settings, then applies them to the backups already there."""
        data = self._body("wizard")
        if data is None:
            return
        keep, km, ex = data.get("keep"), data.get("keep_manual"), data.get("extra_dir", "")
        if any(type(v) is not int for v in (keep, km)) or not isinstance(ex, str):
            return self._send(400, {"error": "keep and keep_manual must be whole numbers and extra_dir text"})
        if not 1 <= keep <= 60 or not 1 <= km <= 200:
            return self._send(400, {"error": "keep must be 1 to 60 and keep_manual 1 to 200"})
        ex = ex.strip().replace("\\", "/")
        if ex:
            if len(ex) > 500 or any(ord(c) < 32 for c in ex) or '"' in ex or not Path(ex).expanduser().is_absolute():
                return self._send(400, {"error": "the copy folder must be a full folder path (for example D:/Backups)"})
            try:
                Path(ex).expanduser().mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return self._send(400, {"error": f"cannot use that folder ({e.strerror or type(e).__name__})"})
        try:
            config.set_values(self.cfg_file, "backup", {"keep": keep, "keep_manual": km, "extra_dir": ex})
        except config.ConfigError as e:
            return self._send(400, {"error": str(e)})
        backup.prune(d, keep, km)
        return self._send(200, {"ok": True})

    def _backup_open(self, d):
        """Body: {which: backups | extra | kits}. Opens that folder in the file manager."""
        data = self._body("wizard")
        if data is None:
            return
        f = backup.folders(d, self._backup_cfg().extra_dir, self.kits_dir).get(data.get("which") if isinstance(data.get("which"), str) else "")
        if not f or not Path(f).is_dir():
            return self._send(404, {"error": "that folder is not on this computer yet"})
        try:
            (self.folder_opener or _open_folder)(Path(f))
        except OSError as e:
            return self._send(500, {"error": f"could not open the folder ({type(e).__name__})"})
        return self._send(200, {"ok": True})

    def _backup(self, path):
        d = self.cfg_file.parent
        try:
            if path == "/backup/settings":
                return self._backup_settings(d)
            if path == "/backup/open":
                return self._backup_open(d)
            if path == "/backup/create":
                self._drain()
                out = backup.create(d, d / backup.BACKUPS / f"manual-{backup._stamp()}{backup.EXT}")
                out.update(name=Path(out["path"]).name, notice=backup.SECRETS_NOTICE, file_notice=backup.FILE_NOTICE)
                return self._send(200, out)
            replace = "1" in parse_qs(urlsplit(self.path).query).get("replace", [])
            if path == "/backup/restore-local":
                data = self._body("wizard")
                if data is None:
                    return
                src = backup.local_path(d, data.get("name"), self._backup_cfg().extra_dir, data.get("where", "data"))
                replace = data.get("replace") is True
                return self._send(200, self._restored(backup.restore(src, d, replace=replace)))
            try:
                n = int(self.headers.get("Content-Length") or -1)
            except ValueError:
                n = -1
            if not 0 < n <= MAX_BACKUP_UPLOAD:
                self._drain()
                return self._send(413 if n > 0 else 400, {"error": "send the backup file as the request body (512 MB at most)"})
            with tempfile.TemporaryDirectory(dir=d) as work:
                f = Path(work) / ("upload" + backup.EXT)
                left = n
                with open(f, "wb") as o:
                    while left:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            return self._send(400, {"error": "the upload stopped early"})
                        o.write(chunk)
                        left -= len(chunk)
                return self._send(200, self._restored(backup.restore(f, d, replace=replace)))
        except (backup.BackupError, ValueError, OSError) as e:
            return self._send(400, {"error": str(e)})

    def _restored(self, out):
        try:
            out["reenter"] = backup.reentry_hints(config.load(self.cfg_file))
        except config.ConfigError:
            out["reenter"] = []
        return out

    def _coach_snap(self, conn):
        try:
            mode = config.load(self.cfg_file).mode.name
        except config.ConfigError:
            mode = None
        return {**coach.snapshot(conn, self.cfg_file.parent), "can_reword": mode not in (None, modes.DEFAULT)}

    def _coach(self, path):
        data = self._body("wizard")
        if data is None:
            return
        conn = store.connect(self.db_file)
        try:
            if path == "/coach/plan":
                coach.plan(conn, self.cfg_file.parent)
            elif path == "/coach/reword":
                cfg = config.load(self.cfg_file)
                coach.reword(conn, dt.date.today(), cfg.mode.name, self.ai_runner or runners.make_runner(cfg, conn))
            else:
                i = data.get("id")
                if not isinstance(i, str):
                    return self._send(400, {"error": "id: text expected"})
                if path == "/coach/done":
                    done = coach.complete(conn, i)
                elif path == "/coach/restore":
                    done = coach.restore(conn, i)
                else:
                    r, n = data.get("reason"), data.get("note", "")
                    if not isinstance(r, str) or not isinstance(n, str):
                        return self._send(400, {"error": "reason and note: text expected"})
                    done = coach.skip(conn, i, r, n)
                return self._send(200, {**done, **self._coach_snap(conn)})
            return self._send(200, self._coach_snap(conn))
        except (ValueError, config.ConfigError, RunnerError) as e:
            return self._send(400, {"error": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": "database busy"})
        finally:
            conn.close()

    def _review_apply(self):
        data = self._body("wizard")
        if data is None:
            return
        role = data.get("role")
        if not isinstance(role, str):
            return self._send(400, {"error": "role: text expected"})
        conn = store.connect(self.db_file)
        try:
            out = features.apply_review_edit(conn, role, data.get("index"), self.cfg_file.parent)
        except (KitError, OSError) as e:
            return self._send(400, {"error": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": "database busy"})
        finally:
            conn.close()
        self._send(200, out)

    def _write(self, method):
        if not self._guard(True):
            return
        path, _ = self._route()
        t = self._target(path) if path.startswith("/api/") else None
        if t is None or t[1] is None:
            return self._send(404, {"error": "unknown collection or id"})
        col, doc_id = t
        match = self.headers.get("If-Match", "")
        data = self._body(col) if method != "DELETE" else {}
        if data is None:
            return
        force = match == "*"
        if not force and not match.isdigit():
            return self._send(428, {"error": "If-Match: <version> (or *) required"})
        conn = store.connect(self.db_file)
        try:
            ver = None if force else int(match)
            if force and method != "PUT":
                cur = store.get(conn, col, doc_id)
                ver = cur["version"] if cur else None
            if method == "PUT":
                v = store.set_doc(conn, col, doc_id, data, ver, by="dashboard", force=force)
            elif method == "PATCH":
                v = store.update_doc(conn, col, doc_id, data, ver, by="dashboard")
            else:
                v = store.delete_doc(conn, col, doc_id, ver, by="dashboard")
        except store.Conflict as e:
            return self._send(409, {"error": "version conflict", "current": e.current})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": "database busy"})
        else:
            if col == "actions" and method != "DELETE":
                _settle(conn)
        finally:
            conn.close()
        self._send(200, {"version": v} if method != "DELETE" else {"deleted": bool(v)})

    def _job(self):
        """Start a kit build or review inside the portal. Body: {kind, key, posting?}."""
        data = self._json_body(MAX_BODY_ASK)
        if data is None:
            return
        kind, key = data.get("kind"), data.get("key")
        if not isinstance(key, str) or not key or len(key) > 200 or BAD_ID.search(key) or not isinstance(data.get("posting", ""), str):
            return self._send(400, {"error": "key must be a role id and posting must be text"})
        try:
            out = jobs.start(self.db_file, self.cfg_file, kind, key, data.get("posting", ""), self.ai_runner)
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": "database busy"})
        self._send(200, out)

    def _outreach(self):
        """Draft a message for a role. Body: {key, channel: email|linkedin|note, request?, theirs?, to?, name?}. Nothing is sent or saved."""
        data = self._json_body(MAX_BODY_ASK)
        if data is None:
            return
        from . import ai
        from .ai import features, runners
        from .kits.pipeline import KitError
        key = data.get("key")
        if not isinstance(key, str) or not key.strip() or len(key) > 400:
            return self._send(400, {"error": "Choose a role first."})
        conn = store.connect(self.db_file)
        try:
            cfg = config.load(self.cfg_file)
            out = features.outreach_role(conn, job_key.doc_id(key), data.get("channel"), data.get("request"), data.get("theirs"),
                                         cfg.mode.name, runners.make_runner(cfg, conn), self.cfg_file.parent,
                                         to=data.get("to") or "", name=data.get("name") or "")
        except KitError as e:
            return self._send(400, {"error": str(e)})
        except (config.ConfigError, ai.RunnerError) as e:
            return self._send(200, {"status": "unavailable", "reason": str(e)})
        except sqlite3.Error:
            return self._send(503, {"error": "database busy"})
        finally:
            conn.close()
        self._send(200, out)

    def _run_now(self):
        """Start the morning or midday run in the background. Body: {run?: "morning" | "midday"}."""
        data = self._json_body(MAX_BODY_ASK)
        if data is None:
            return
        try:
            conn = store.connect(self.db_file)
            try:
                out = runnow.start(conn, self.cfg_file.parent, data.get("run", "morning"), *([self.run_popen] if self.run_popen else []))
            finally:
                conn.close()
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except OSError as e:
            return self._send(500, dict(problems.describe(e), error="The run could not be started."))
        except sqlite3.Error:
            return self._send(503, {"error": "database busy"})
        self._send(200, out)

    def _add_role(self):
        """Add a role by hand and, unless build is false, start its kit. Body: {company, title, link?, posting?, build?}."""
        data = self._json_body(MAX_BODY_ASK)
        if data is None:
            return
        try:
            conn = store.connect(self.db_file)
            try:
                out = api.add_manual_role(conn, data.get("company"), data.get("title"), data.get("link", ""), data.get("posting", ""),
                                          found_via=data.get("found_via", ""))
                rid = out.get("id") or out.get("duplicate_of")
                if out.get("created"):
                    board.ensure(conn, dt.date.today())
                    if data.get("build", True) is not False:
                        out["job"] = jobs.start(self.db_file, self.cfg_file, "kit", rid, "", self.ai_runner)
            finally:
                conn.close()
        except (ValueError, store.Conflict) as e:
            return self._send(400, {"error": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": "database busy"})
        self._send(200, out)

    def _json_body(self, limit):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            self._send(413 if n > 0 else 400, {"error": "bad or oversized body"})
            return None
        try:
            data = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._send(400, {"error": "body must be a JSON object"})
            return None
        return data

    def _ask(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY_ASK:
            return self._send(413 if n > 0 else 400, {"error": "bad or oversized body"})
        try:
            data = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return self._send(400, {"error": "body must be a JSON object"})
        from . import ai
        from .ai import features, runners
        from .kits.pipeline import KitError
        conn = store.connect(self.db_file)
        try:
            cfg = config.load()
            out = features.ask_question(data.get("question"), data.get("context"), cfg.mode.name,
                                        runners.make_runner(cfg, conn))
        except KitError as e:
            return self._send(400, {"error": str(e)})
        except (config.ConfigError, ai.RunnerError) as e:
            return self._send(200, {"status": "unavailable", "reason": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": "database busy"})
        finally:
            conn.close()
        self._send(200, out)

    do_PUT = lambda self: self._write("PUT")
    do_PATCH = lambda self: self._write("PATCH")
    do_DELETE = lambda self: self._write("DELETE")


def make_server(data_dir=None, port=8765):
    """Bind loopback only; there is deliberately no host argument."""
    d = Path(data_dir) if data_dir else config.data_dir()
    handler = type("Handler", (_Handler,), {"db_file": d / "commandcenter.db", "cfg_file": d / "config.toml"})
    conn = store.connect(handler.db_file)
    jobs.recover(conn)
    try:
        store.housekeeping(conn)
    except Exception:
        pass       # housekeeping must never stop the dashboard from starting
    try:
        kitlink.link_all(conn, d)
    except (store.Conflict, OSError):
        pass        # the folders are only a convenience; the page works without the link
    conn.close()
    return ThreadingHTTPServer(("127.0.0.1", port), handler)

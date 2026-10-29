"""End-to-end against a real local dashboard: the extension takes the role, the resume and the answers from it,
fills the mock Workday page, and Mark applied changes the role. No profile.json in the extension is used."""
import json, os, pathlib, sys, tempfile, threading  # run from the project folder (cc must be importable)
from playwright.sync_api import sync_playwright
from cc import profile, store, web

ext = pathlib.Path(__file__).resolve().parent.parent
html = (ext / "test" / "mock_workday.html").read_text()
data = pathlib.Path(tempfile.mkdtemp())
ex = json.load(open(ext / "profile.example.json"))
profile.save(data, {"fields": {k: {"value": v, "ok": True} for k, v in ex.items() if isinstance(v, str) and k in profile.KINDS}})
kit = data / "kits" / "2026-10-29" / "Teladoc_VP"
kit.mkdir(parents=True)
(kit / "Jordan_Example_Resume.pdf").write_bytes(b"%PDF-1.4 test")
c = store.connect(data / "commandcenter.db")
store.set_doc(c, "roles", "teladoc-vp", {"key": "teladoc-vp", "company": "Teladoc Health", "title": "VP", "status": "ready",
              "kit": "2026-10-29/Teladoc_VP", "link": "https://teladoc.wd503.myworkdayjobs.com/en-US/apply"})
c.close()
srv = web.make_server(data, 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
bad = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: bad.append(name)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), headless=True, channel=os.environ.get("PW_CHANNEL") or "chromium",
        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
    ctx.route("https://teladoc.wd503.myworkdayjobs.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body=html))
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
    sw.evaluate(f"chrome.storage.local.set({{dash: '{base}'}})")
    page = ctx.new_page()
    page.goto("https://teladoc.wd503.myworkdayjobs.com/en-US/apply/myInformation")
    page.wait_for_selector("#jaf-host", state="attached", timeout=8000)
    page.wait_for_timeout(1500)
    host = page.locator("#jaf-host")
    check("button names the kit", "Teladoc" in host.locator("button#b").inner_text())
    host.locator("button#b").click()
    host.locator("#box").wait_for(state="visible", timeout=15000)
    box = host.locator("#box").inner_text()
    print(box[:700].encode("ascii", "replace").decode())
    check("first name from the dashboard profile", page.eval_on_selector("#name--legalName--firstName", "e => e.value") == ex["firstName"])
    check("resume attached from the dashboard", "attached" in box and "Jordan_Example_Resume.pdf" in box)
    check("says why it matched", "same job link" in box)
    check("no Next/Submit clicks", page.evaluate("window.clicked") == [])
    # thank-you page -> Mark applied
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<p>Thank you for applying to Teladoc Health. We have received your application.</p>')")
    host.locator("button#mark").wait_for(timeout=8000)
    host.locator("button#mark").click()
    host.locator("button#mark", has_text="Marked applied").wait_for(timeout=8000)
    c = store.connect(data / "commandcenter.db")
    check("role is applied in the dashboard", store.get(c, "roles", "teladoc-vp")["data"]["status"].startswith("applied"))
    c.close()
    # dashboard stopped -> a plain message, no crash
    srv.shutdown(); srv.server_close()
    sw.evaluate("1")
    page.evaluate("document.querySelector('#name--legalName--firstName').value = ''")
    host.locator("button#b").click()
    page.wait_for_timeout(4000)
    print(host.locator("#box").inner_text().encode("ascii", "replace").decode()[:400])
    check("dashboard down gives a plain note", "Dashboard not reachable" in host.locator("#box").inner_text() and "Traceback" not in host.locator("#box").inner_text())
    ctx.close()
sys.exit(1 if bad else 0)

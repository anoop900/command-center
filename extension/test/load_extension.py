"""Checks Chromium accepts the unpacked extension and the side panel and options pages load without errors."""
import pathlib, sys, tempfile
from playwright.sync_api import sync_playwright
ext = str(pathlib.Path(__file__).resolve().parent.parent)
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), headless=True, channel="chromium", args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=10000)
    eid = sw.url.split("/")[2]
    errs = []
    for pg in ["sidepanel.html", "options.html"]:
        page = ctx.new_page()
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errs.append(m.text))
        page.goto(f"chrome-extension://{eid}/{pg}")
        page.wait_for_timeout(800)
        print(pg, "->", page.inner_text("body")[:120].replace("\n", " | "))
    print("errors:", errs)
    ctx.close()
    sys.exit(1 if errs else 0)

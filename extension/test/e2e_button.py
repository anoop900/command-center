"""End-to-end: real extension + mock Workday page at a myworkdayjobs.com URL; click the on-page Fill button.
Needs profile.json in the extension folder (copy profile.example.json for the test)."""
import pathlib, sys, tempfile
from playwright.sync_api import sync_playwright
ext = pathlib.Path(__file__).resolve().parent.parent
html = (ext / "test" / "mock_workday.html").read_text()
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), headless=True, channel="chromium",
        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
    ctx.route("https://teladoc.wd503.myworkdayjobs.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body=html))
    if not ctx.service_workers: ctx.wait_for_event("serviceworker")
    page = ctx.new_page()
    page.goto("https://teladoc.wd503.myworkdayjobs.com/en-US/apply/myInformation")
    page.wait_for_selector("#jaf-host", state="attached", timeout=8000)
    page.locator("#jaf-host").locator("button#b").click()
    page.locator("#jaf-host").locator("#box").wait_for(state="visible", timeout=15000)
    box = page.locator("#jaf-host").locator("#box").inner_text()
    print(box[:600])
    ok = page.eval_on_selector("#name--legalName--firstName", "e => e.value") != "" and page.evaluate("window.clicked") == []
    print("first name filled & no Next/Submit clicks:", ok)
    ctx.close(); sys.exit(0 if ok else 1)

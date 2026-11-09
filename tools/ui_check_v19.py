"""Browser test for v0.19: the right-click menu (thumbnails, folders, tags, the empty grid) with its
Open with submenu, the Programs dialog, and the page's own yes/no question (ask) instead of
window.confirm. The desktop app's native menu, drops and drags are checked by its --selftest.

    python tools/ui_check_v19.py PORT OUT_DIR LIBRARY_FOLDER
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, LIB = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1400, "height": 900})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{PORT}")
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell", timeout=30000)
    page.wait_for_timeout(800)

    def api(method, url, body=None):
        return page.evaluate("""async ([m, u, b]) => { const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' },
            body: b == null ? undefined : JSON.stringify(b) }); return r.json(); }""", [method, url, body])

    def menu_rows(level=0):
        page.wait_for_selector(".ctxmenu")
        return [r.strip() for r in page.locator(".ctxmenu").nth(level).locator(".pm-row .grow").all_inner_texts()]

    def pick(label, level=0):
        page.locator(".ctxmenu").nth(level).locator(".pm-row", has_text=label).first.click()
        page.wait_for_timeout(300)

    # --- a thumbnail: the right-clicked one becomes the selection
    page.locator(".cell[data-i='1']").click(button="right")
    rows = menu_rows()
    for want in ("Open", "Open with", "Copy path", "Copy to…", "Move to…", "Rename…", "Resample…", "Delete…"):
        assert want in rows, rows
    assert page.evaluate(f"[...{K}.selected]") == [page.evaluate(f"{K}.items.ids[1]")]
    page.screenshot(path=os.path.join(OUT, "v19_menu_file.png"))
    page.locator(".ctxmenu .pm-row", has_text="Open with").hover()
    page.wait_for_timeout(200)
    assert menu_rows(1) == ["Programs…"], menu_rows(1)
    page.keyboard.press("Escape")
    assert page.locator(".ctxmenu").count() == 0

    # --- Copy path, then Rename… opens its dialog
    page.locator(".cell[data-i='1']").click(button="right")
    pick("Copy path")
    clip = page.evaluate("navigator.clipboard.readText()")
    assert clip.startswith(LIB) and clip.endswith(".png"), clip
    page.locator(".cell[data-i='1']").click(button="right")
    pick("Rename…")
    page.wait_for_selector(".dlg")
    assert "Rename 1 file" in page.locator(".dlg-title").inner_text()
    page.keyboard.press("Escape")

    # --- Programs…: add one, then it is offered for pictures
    page.locator(".cell[data-i='1']").click(button="right")
    page.locator(".ctxmenu .pm-row", has_text="Open with").hover()
    pick("Programs…", 1)
    page.wait_for_selector(".dlg .apps-own", state="attached")
    page.locator(".dlg button", has_text="+ Add a program").click()
    row = page.locator(".dlg .apps-row").first
    row.locator("input").nth(0).fill("Paint")
    row.locator("input").nth(1).fill(sys.executable)
    row.locator("select").select_option("image")
    page.screenshot(path=os.path.join(OUT, "v19_programs.png"))
    page.locator(".dlg button", has_text="Save").click()
    page.wait_for_timeout(500)
    assert page.locator(".dlg").count() == 0
    saved = api("GET", "/api/apps")["apps"]
    assert [(a["name"], a["kinds"]) for a in saved if a["custom"]] == [("Paint", ["image"])], saved
    page.locator(".cell[data-i='1']").click(button="right")
    page.locator(".ctxmenu .pm-row", has_text="Open with").hover()
    page.wait_for_timeout(200)
    assert menu_rows(1) == ["Paint", "Programs…"], menu_rows(1)
    page.keyboard.press("Escape")

    # --- the empty grid: Select all
    box = page.locator("#grid").bounding_box()
    page.mouse.click(box["x"] + box["width"] - 40, box["y"] + box["height"] - 30, button="right")
    assert menu_rows()[:2] == ["Select all", "Invert selection"], menu_rows()
    pick("Select all")
    assert len(page.evaluate(f"[...{K}.selected]")) == page.evaluate(f"{K}.items.ids.length")

    # --- a library folder: Pin, and Remove from library… asks first (Cancel keeps it)
    folder = page.locator(f".tree [data-folder='{LIB}']").first
    folder.click(button="right")
    rows = menu_rows()
    assert rows[:4] == ["Open", rows[1], "Copy path", "Pin"] and "Remove from library…" in rows, rows
    pick("Pin")
    page.wait_for_timeout(300)
    assert page.evaluate(f"{K}.sidebar.isPinned({LIB!r})")
    folder.click(button="right")
    pick("Remove from library…")
    page.wait_for_selector(".dlg .ask-head")
    assert "from the library?" in page.locator(".dlg .ask-head").inner_text()
    page.screenshot(path=os.path.join(OUT, "v19_ask.png"))
    page.locator(".dlg button", has_text="Cancel").click()
    page.wait_for_timeout(300)
    assert any(r["path"] == LIB for r in api("GET", "/api/state")["roots"])

    # --- a tag: add it to the selection from the menu
    t = api("POST", "/api/tags", {"name": "Props"})
    page.evaluate(f"{K}.tags.refresh()")
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.locator(".cell[data-i='0']").click()
    page.locator(f".tagnode[data-tag-id='{t['id']}']").click(button="right")
    rows = menu_rows()
    assert rows[0] == "Show everything with this tag" and "Add to 1 item" in rows and "Edit tag…" in rows, rows
    pick("Add to 1 item")
    page.wait_for_timeout(400)
    first = page.evaluate(f"{K}.items.ids[0]")
    assert t["id"] in api("GET", f"/api/items/{first}")["tags"]

    # --- text fields keep the browser's own menu
    page.locator("input[type='search'], input[type='text']").first.click(button="right")
    page.wait_for_timeout(200)
    assert page.locator(".ctxmenu").count() == 0

    assert not errors, errors
    print("v0.19 UI check passed")
    b.close()

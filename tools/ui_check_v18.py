"""Browser test for v0.18: Copy to… (K) with undo, Resample… (I), the last tag on 0 / Q (also on a
Czech keyboard), and a project's own hotkeys.

Any library with a few pictures in an "inbox" folder and an empty "sorted" folder next to it, e.g.:

    python tools/ui_check_v18.py PORT OUT_DIR LIBRARY_FOLDER
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

PORT, OUT, LIB = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = b.new_context(viewport={"width": 1400, "height": 900}).new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell", timeout=30000)
    page.wait_for_timeout(1000)

    def api(method, url, body=None):
        return page.evaluate("""async ([m, u, b]) => { const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' },
            body: b == null ? undefined : JSON.stringify(b) }); return r.json(); }""", [method, url, body])

    def names():
        return page.evaluate(f"{K}.items.n")

    def select(*ns):
        page.keyboard.press("Escape")
        for i, n in enumerate(ns):
            loc = page.locator(f".cell[data-i='{names().index(n)}']:visible")
            loc.click(modifiers=["Control"] if i else [])
        page.wait_for_timeout(300)

    def tags_of(n):
        it = page.evaluate(f"{K}.allItems")
        i = it["n"].index(n)
        return sorted(page.evaluate(f"(i) => ({K}.allItems.t[i] || []).map((t) => {K}.tags.byId.get(t).name)", i))

    hero = api("POST", "/api/tags", {"name": "Hero"})
    tree = api("POST", "/api/tags", {"name": "Tree"})
    page.evaluate(f"{K}.tags.refresh()")
    page.wait_for_timeout(600)

    # --- 0 / Q: the last tag again
    print("no last tag yet:", page.evaluate(f"{K}.tags.last"))
    select("pic0.png")
    page.keyboard.press(str(hero["hotkey"]))
    page.wait_for_timeout(500)
    print("last tag after the hotkey:", page.evaluate(f"{K}.tags.last?.name"),
          "| chip:", page.locator("#tagbar .chip.last").inner_text().replace("\n", " "))
    select("pic1.png")
    page.keyboard.press("0")
    page.wait_for_timeout(500)
    select("pic2.png")
    page.keyboard.press("q")
    page.wait_for_timeout(500)
    select("pic3.png")
    page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'é', code: 'Digit0', bubbles: true }))")   # Czech layout
    page.wait_for_timeout(500)
    print("tagged with 0, Q, Czech 0:", tags_of("pic1.png"), tags_of("pic2.png"), tags_of("pic3.png"))
    page.screenshot(path=f"{OUT}/last_tag_chip.png")
    page.keyboard.press("q")                  # toggles off again, like a hotkey
    page.wait_for_timeout(500)
    print("Q again removes:", tags_of("pic3.png"))

    # --- the viewer: Q tags the shown picture, 0 still fits
    select("pic4.png")
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    page.keyboard.press("q")
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    print("viewer Q:", tags_of("pic4.png"))

    # --- a project's own hotkeys
    proj = api("POST", "/api/tags", {"name": "Game A", "facet": "project", "auto_hotkey": False})
    print("project keys:", api("PUT", f"/api/projects/{proj['id']}/hotkeys", {"hotkeys": {str(hero['hotkey']): tree["id"]}}))
    page.evaluate(f"{K}.projects.refresh().then(() => {K}.projects.activate({proj['id']}, {{ mode: 'add' }}))")
    page.wait_for_timeout(1200)
    print(f"in project: key {hero['hotkey']} ->", page.evaluate(f"{K}.tags.byHotkey.get({hero['hotkey']})?.name"),
          "| sidebar project badge:", page.locator(".node .hk.proj").count())
    select("pic5.png")
    page.keyboard.press(str(hero["hotkey"]))
    page.wait_for_timeout(500)
    print("pic5 with the project's key:", tags_of("pic5.png"))
    page.locator('#pathbar button:has-text("Edit…")').first.click()
    page.wait_for_timeout(600)
    page.locator(".hkgrid").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/project_hotkeys.png")
    page.keyboard.press("Escape")
    page.evaluate(f"{K}.projects.leave()")
    page.wait_for_timeout(1000)
    print(f"left project: key {hero['hotkey']} ->", page.evaluate(f"{K}.tags.byHotkey.get({hero['hotkey']})?.name"))

    # --- Copy to… (K), then Ctrl+Z
    select("pic0.png", "pic1.png")
    page.keyboard.press("k")
    page.wait_for_selector(".dlg")
    page.fill(".dlg input[type=text]", os.path.join(LIB, "sorted"))
    page.click('.dlg button:has-text("Preview")')
    page.wait_for_timeout(800)
    print("copy preview:", page.locator(".dlg .move-sum").inner_text().replace("\n", " | "))
    page.screenshot(path=f"{OUT}/copy_dialog.png")
    page.click(".dlg button.primary")
    page.wait_for_timeout(1500)
    print("toast:", page.locator(".toast").inner_text(), "| on disk:", sorted(os.listdir(os.path.join(LIB, "sorted"))))
    page.evaluate(f"{K}.openPath({os.path.join(LIB, 'sorted')!r})")
    page.wait_for_timeout(1200)
    print("copies in the grid:", names(), "| their tags:", [tags_of(n) for n in names()])
    page.keyboard.press("Control+z")
    page.wait_for_timeout(1500)
    print("after Ctrl+Z:", page.locator(".toast").inner_text(), "| on disk:", sorted(os.listdir(os.path.join(LIB, "sorted"))))

    # --- Resample… (I)
    page.evaluate(f"{K}.openPath({os.path.join(LIB, 'inbox')!r})")
    page.wait_for_timeout(1200)
    select("pic0.png", "pic1.png", "sprite.png")
    page.keyboard.press("i")
    page.wait_for_selector(".dlg")
    page.click('.dlg .chip:has-text("512 px")')
    page.click('.dlg button:has-text("Preview")')
    page.wait_for_timeout(800)
    print("resample preview:", page.locator(".dlg .move-sum").inner_text().replace("\n", " | "))
    page.screenshot(path=f"{OUT}/resample_dialog.png")
    page.click(".dlg button.primary")
    t = time.time()
    while "Done" not in page.locator(".dlg .move-preview").inner_text() and time.time() - t < 30:
        page.wait_for_timeout(200)
    print("resample:", page.locator(".dlg .move-preview .ok").inner_text())
    page.screenshot(path=f"{OUT}/resample_done.png")
    page.keyboard.press("Escape")
    page.wait_for_timeout(2500)
    page.evaluate(f"{K}.reload()")
    page.wait_for_timeout(1000)
    print("grid now:", [n for n in names() if "_512x" in n], "| tags of a copy:", tags_of("pic0_512x384.png"))
    page.screenshot(path=f"{OUT}/grid_after.png")

    # --- status bar and help
    print("status bar buttons:", [x for x in page.locator("#statusbar .btn").all_inner_texts() if x in ("Resample…", "Copy to…", "Move to…")])
    page.keyboard.press("?")
    page.wait_for_timeout(300)
    print("help rows:", [r for r in page.locator(".help td kbd").all_inner_texts() if r in ("0  or  Q", "K", "I")])
    print("console errors:", errors)
    b.close()

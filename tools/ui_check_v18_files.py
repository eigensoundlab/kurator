"""Browser test for v0.18 file tools: the folder picker (Browse…, new folder, recent folders), copies
for delivery (zip), batch rename (F2), delete (Del) with undo in both trash modes, quick destinations
(D, then 1-9) and Clean up duplicates.

Library: a folder with an "inbox" of a few pictures (two of them identical) and an empty "sorted"
folder next to it.

    python tools/ui_check_v18_files.py PORT OUT_DIR LIBRARY_FOLDER
"""

import os
import sys
import zipfile

from playwright.sync_api import sync_playwright

PORT, OUT, LIB = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"
INBOX, SORTED = os.path.join(LIB, "inbox"), os.path.join(LIB, "sorted")

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = b.new_context(viewport={"width": 1400, "height": 900}).new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell", timeout=30000)
    page.wait_for_timeout(800)

    def api(method, url, body=None):
        return page.evaluate("""async ([m, u, b]) => { const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' },
            body: b == null ? undefined : JSON.stringify(b) }); return r.json(); }""", [method, url, body])

    def names():
        return page.evaluate(f"{K}.items.n")

    def select(*ns):
        page.keyboard.press("Escape")
        for i, n in enumerate(ns):
            page.locator(f".cell[data-i='{names().index(n)}']:visible").click(modifiers=["Control"] if i else [])
        page.wait_for_timeout(300)

    def open_folder(path):
        page.evaluate(f"{K}.openPath({path!r})")
        page.wait_for_timeout(1000)

    def toast():
        return page.locator(".toast").inner_text()

    open_folder(INBOX)

    # --- Copy to… with Browse…: the folder picker, a new folder, then copies for delivery as a zip
    select("pic0.png", "pic1.png")
    page.keyboard.press("k")
    page.wait_for_selector(".dlg")
    page.click('.dlg .destfield button:has-text("Browse…")')
    page.wait_for_selector(".fp-wrap")
    path_in = page.locator(".dlg-wrap:last-child .fp-path input[type=text]")
    path_in.fill(LIB)
    path_in.press("Enter")
    page.wait_for_timeout(600)
    print("picker lists:", page.locator(".dlg-wrap:last-child .fp-list .node .nm").all_inner_texts(),
          "| info:", page.locator(".dlg-wrap:last-child .fp-wrap .muted.small").inner_text())
    page.fill(".dlg-wrap:last-child input[placeholder='New folder name']", "client A")
    page.click('.dlg-wrap:last-child button:has-text("+ New folder")')
    page.wait_for_timeout(600)
    page.screenshot(path=f"{OUT}/folder_picker.png")
    page.click('.dlg-wrap:last-child button:has-text("Use this folder")')
    page.wait_for_timeout(400)
    print("picked:", page.locator(".dlg .destfield input").input_value(), "| dialogs open:", page.locator(".dlg-wrap").count())
    page.click(".dlg details.delivery summary")
    page.locator(".dlg details.delivery input[type=checkbox]").nth(0).check()
    page.locator(".dlg details.delivery input[type=checkbox]").nth(2).check()
    page.fill(".dlg details.delivery input[type=text]", "round 1")
    page.click('.dlg button:has-text("Preview")')
    page.wait_for_timeout(700)
    print("delivery preview:", page.locator(".dlg .move-sum").inner_text().replace("\n", " | "))
    page.screenshot(path=f"{OUT}/copy_delivery.png")
    page.click(".dlg button.primary")
    page.wait_for_timeout(1200)
    zpath = os.path.join(LIB, "client A", "round 1.zip")
    print("toast:", toast(), "| zip:", os.path.exists(zpath) and sorted(zipfile.ZipFile(zpath).namelist()))
    page.keyboard.press("Control+z")
    page.wait_for_timeout(1000)
    print("after Ctrl+Z:", toast(), "| zip still there:", os.path.exists(zpath))
    print("recent folders:", [r["path"] for r in api("GET", "/api/folders/recent")["recent"]])

    # --- F2: rename with a pattern, then undo
    select("pic2.png", "pic3.png", "pic4.png")
    page.keyboard.press("F2")
    page.wait_for_selector(".dlg")
    page.fill(".dlg .form > label input[type=text]", "art_{n}")
    page.wait_for_timeout(600)
    print("rename preview:", page.locator(".dlg .move-list").inner_text().replace("\n", " "))
    page.screenshot(path=f"{OUT}/rename_dialog.png")
    page.click(".dlg button.primary")
    page.wait_for_timeout(1200)
    print("toast:", toast(), "| grid:", sorted(n for n in names() if n.startswith("art_")))
    page.keyboard.press("Control+z")
    page.wait_for_timeout(1200)
    print("after Ctrl+Z:", toast(), "| on disk:", sorted(f for f in os.listdir(INBOX) if f.startswith(("art_", "pic2", "pic3", "pic4"))))

    # --- Del: Kurator's trash folder, then the system trash; both undone
    for mode in ("kurator", "system"):
        api("PUT", "/api/settings", {"delete_mode": mode})
        select("pic5.png")
        page.keyboard.press("Delete")
        page.wait_for_selector(".dlg")
        q = page.locator(".dlg").inner_text().replace("\n", " | ")
        if mode == "kurator":
            page.screenshot(path=f"{OUT}/delete_confirm.png")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1200)
        gone = not os.path.exists(os.path.join(INBOX, "pic5.png"))
        print(f"delete ({mode}):", q[:90], "| toast:", toast(), "| gone:", gone, "| in grid:", "pic5.png" in names())
        page.keyboard.press("Control+z")
        page.wait_for_timeout(1500)
        print(f"  Ctrl+Z ({mode}):", toast(), "| back:", os.path.exists(os.path.join(INBOX, "pic5.png")), "| in grid:", "pic5.png" in names())

    # --- quick destinations: set slot 1 in the editor, then D 1
    page.keyboard.press("Escape")
    page.keyboard.press("d")
    page.wait_for_selector(".dlg")
    page.keyboard.press("e")
    page.wait_for_selector(".slot-grid")
    page.locator(".slot-row").nth(0).locator("input[type=text]").first.fill(SORTED)
    page.locator(".slot-row").nth(0).locator("input[type=text]").nth(1).fill("Sorted")
    page.screenshot(path=f"{OUT}/slot_editor.png")
    page.click('.dlg button:has-text("Save")')
    page.wait_for_timeout(500)
    select("pic0.png")
    page.keyboard.press("d")
    page.wait_for_selector(".send-row")
    page.screenshot(path=f"{OUT}/send_menu.png")
    page.keyboard.press("1")
    page.wait_for_timeout(1200)
    print("D 1:", toast(), "| in sorted:", os.listdir(SORTED))

    # --- duplicates: open the view, Clean up…
    page.evaluate(f"{K}.tags.refresh()")
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.evaluate(f"{K}.clearSelection()")
    page.evaluate(f"{K}.setDupes(true)")
    page.wait_for_timeout(1200)
    print("duplicates view:", names())
    page.click('#pathbar button:has-text("Clean up…")')
    page.wait_for_selector(".dgroup")
    page.wait_for_timeout(500)
    print("clean-up preview:", page.locator(".dlg .move-sum").inner_text().replace("\n", " | "))
    page.screenshot(path=f"{OUT}/dupes_dialog.png")
    page.click(".dlg button.primary")
    page.wait_for_timeout(1500)
    print("toast:", toast())
    page.keyboard.press("Control+z")
    page.wait_for_timeout(1500)
    print("after Ctrl+Z:", toast())

    # --- Files ▾ menu and Settings → Delete
    page.click('#statusbar button:has-text("Files ▾")')
    page.wait_for_selector(".popmenu")
    print("files menu:", page.locator(".popmenu .pm-row .grow").all_inner_texts())
    page.screenshot(path=f"{OUT}/files_menu.png")
    page.keyboard.press("Escape")
    page.click('button[title^="Settings"]')
    page.wait_for_selector(".dlg")
    page.locator('.dlg h3:has-text("Delete")').scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    page.screenshot(path=f"{OUT}/settings_delete.png")
    print("console errors:", errors)
    b.close()

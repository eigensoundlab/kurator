"""Browser test for v0.9: viewer back button + 3D progress, music waveforms, duplicates, saved views,
texture sets in the panel, project export.

    python tools/ui_check_v9.py PORT OUT_DIR EXPORT_DIR
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, EXP = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=30000)
    page.wait_for_timeout(1500)
    n = lambda: page.evaluate(f"{K}.items.ids.length")  # noqa: E731
    idx = lambda name: page.evaluate(f"{K}.items.n.indexOf('{name}')")  # noqa: E731
    print("items:", n())

    # --- 3D viewer: progress overlay, back button
    page.locator(".node", has_text="models").first.click(); page.wait_for_timeout(900)
    page.locator(f".cell[data-i='{idx('scene.glb')}']").dblclick()
    page.wait_for_timeout(60)
    print("loading overlay seen:", page.evaluate("document.querySelector('.v-loading').className"),
          "|", page.inner_text(".v-loading"))
    page.wait_for_timeout(1500)
    print("back button:", page.inner_text(".v-back").replace("\n", " "), "| viewer:", page.evaluate("document.querySelector('.viewer').className"))
    page.screenshot(path=f"{OUT}/1_viewer.png")
    page.click(".v-back"); page.wait_for_timeout(400)
    print("closed by back:", page.evaluate("!document.querySelector('.viewer').classList.contains('open')"))

    # --- music vs effects
    page.locator(".node", has_text="formats").first.click(); page.wait_for_timeout(1200)
    page.screenshot(path=f"{OUT}/2_sounds.png")

    # --- duplicates
    page.locator(".node", has_text="All library items").first.click(); page.wait_for_timeout(800)
    dn = page.locator(".node", has_text="Duplicates")
    print("duplicates node:", dn.inner_text().replace("\n", " ") if dn.count() else "none")
    dn.click(); page.wait_for_timeout(900)
    print("dupes view:", n(), page.evaluate(f"{K}.items.n.slice(0, 6)"), "|", page.inner_text("#pathbar").replace("\n", " ")[:80])
    page.locator(".cell:visible").nth(0).click(); page.wait_for_timeout(800)
    print("panel also in:", page.locator("#panel .twin").all_inner_texts()[:3])
    page.screenshot(path=f"{OUT}/3_dupes.png")

    # --- saved view
    page.click("#pathbar button:text-is('☆ Save view')"); page.wait_for_timeout(300)
    page.fill(".dlg input", "Copies to clean up"); page.click(".dlg .btn.primary"); page.wait_for_timeout(600)
    page.click("#pathbar button:text-is('Clear filters')"); page.wait_for_timeout(800)
    total = n()
    page.locator(".node", has_text="Copies to clean up").click(); page.wait_for_timeout(900)
    print("saved view applied:", total, "->", n(), "| active:", page.locator(".node.active", has_text="Copies to clean up").count())
    page.click("#pathbar button:text-is('Clear filters')"); page.wait_for_timeout(600)
    print("view unhighlighted after change:", page.locator(".node.active", has_text="Copies to clean up").count() == 0)

    # --- texture set in the panel
    root = page.evaluate(f"{K}.state.roots[0].path")
    page.evaluate(f"{K}.openPath('{root}/textures/rock')"); page.wait_for_timeout(900)
    page.locator(f".cell[data-i='{idx('rock_normal_4k.png')}']").click(); page.wait_for_timeout(900)
    print("texture set:", page.locator("#panel .rel").first.inner_text().replace("\n", " "))
    page.locator("#panel .rel button", has_text="roughness").click(); page.wait_for_timeout(500)
    print("jumped to:", page.evaluate(f"{K}.items.n[{K}.focusIndex]"))
    page.screenshot(path=f"{OUT}/4_texset.png")
    page.evaluate(f"{K}.openPath('{root}/props')"); page.wait_for_timeout(900)
    page.locator(f".cell[data-i='{idx('cube.obj')}']").click(); page.wait_for_timeout(900)
    print("model uses:", page.locator("#panel .rel").first.inner_text().replace("\n", " "))

    # --- project export
    page.click(".projbtn"); page.click(".pm-row:has-text('New project')"); page.wait_for_timeout(300)
    page.fill(".projdlg input[type=text]", "Game A")
    page.fill(".projdlg .row input[placeholder^='Folder']", root + "/props")
    page.click(".projdlg button:text-is('+ Folder')"); page.click(".dlg .btn.primary"); page.wait_for_timeout(1200)
    page.click("#pathbar button:text-is('Export…')"); page.wait_for_timeout(400)
    page.fill(".dlg label:has-text('Destination') input", EXP)
    page.select_option(".dlg select", "unity")
    page.click(".dlg button:text-is('Preview')"); page.wait_for_timeout(1000)
    print("export preview:", page.inner_text(".dlg .move-sum").replace("\n", " | "))
    page.screenshot(path=f"{OUT}/5_export.png")
    page.click(".dlg button:text-is('Export')"); page.wait_for_timeout(2500)
    print("export result:", page.inner_text(".dlg .move-preview").replace("\n", " | ")[:200])
    print("files:", sorted(os.path.relpath(os.path.join(r, f), EXP) for r, _, fs in os.walk(EXP) for f in fs)[:12])
    print("console errors:", errors[:10])
    b.close()

"""Browser test for v0.13: turntable on hover, waveform selection and "save as new file",
Godot resources in the sprites dialog. Library from tools/make_v13_samples.py.

    python tools/ui_check_v13.py PORT OUT_DIR SAVE_DIR
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, SAVE = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
os.makedirs(SAVE, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--autoplay-policy=no-user-gesture-required"])
    page = b.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=30000)
    root = page.evaluate(f"{K}.state.roots[0].path")

    # --- turntable
    page.evaluate(f"{K}.openPath('{root}/models')"); page.wait_for_timeout(1500)
    names = page.evaluate(f"{K}.items.n")
    print("models:", names)
    cell = page.locator(f".cell[data-i='{names.index('bricks_ball.glb')}']:visible")
    bx = cell.bounding_box()
    page.mouse.move(bx["x"] + 20, bx["y"] + bx["height"] / 2); page.wait_for_timeout(900)
    page.mouse.move(bx["x"] + bx["width"] * 0.6, bx["y"] + bx["height"] / 2, steps=3); page.wait_for_timeout(400)
    print("turntable:", page.evaluate("document.querySelector('.cell.scrubbing .strip')?.dataset.k"))
    page.screenshot(path=f"{OUT}/1_turntable.png", clip={"x": bx["x"] - 10, "y": bx["y"] - 10, "width": bx["width"] + 20, "height": bx["height"] + 20})
    for n in names:
        if n.startswith("quad"):
            print(n, page.evaluate(f"fetch('/api/items/' + {K}.items.ids[{names.index(n)}]).then(r => r.json()).then(d => [d.status, d.error, d.meta.model?.textured])"))
    page.mouse.move(700, 850)

    # --- waveform selection
    page.evaluate(f"{K}.openPath('{root}/sounds')"); page.wait_for_timeout(1200)
    page.locator(".cell:visible").first.dblclick(); page.wait_for_timeout(1500)
    wv = page.locator(".viewer canvas.wavecanvas").bounding_box()
    y = wv["y"] + wv["height"] / 2
    page.mouse.move(wv["x"] + wv["width"] * 0.25, y); page.mouse.down()
    page.mouse.move(wv["x"] + wv["width"] * 0.5, y, steps=6); page.mouse.up(); page.wait_for_timeout(300)
    sel = page.evaluate(f"{K}.viewer.wave.sel")
    print("selection:", {k: round(v, 2) for k, v in sel.items()}, "| bar:", page.inner_text(".wave-selbar").replace("\n", " "))
    page.keyboard.press(" "); page.wait_for_timeout(700)
    print("plays selection:", page.evaluate(f"{K}.viewer.wave.playSel"), round(page.evaluate(f"{K}.audio.currentTime"), 2))
    page.screenshot(path=f"{OUT}/2_selection.png")
    page.keyboard.press("e"); page.wait_for_timeout(800)
    page.fill(".dlg .sf:has-text('Save into') input", SAVE)
    page.select_option(".dlg .sf:has-text('Format') select", "flac")
    page.screenshot(path=f"{OUT}/3_export.png")
    page.click(".dlg button:text-is('Save')"); page.wait_for_timeout(1500)
    print("saved:", page.inner_text(".dlg .ok") if page.locator(".dlg .ok").count() else page.inner_text(".dlg .err"))
    page.click(".dlg button:text-is('Close')"); page.keyboard.press("Escape"); page.wait_for_timeout(300)

    # --- sprites with Godot resources
    page.evaluate(f"{K}.openPath('{root}/sprites')"); page.wait_for_timeout(1200)
    page.keyboard.press("Control+a"); page.keyboard.press("j"); page.wait_for_timeout(1000)
    page.click(".segs button:text-is('Sprites / atlas')"); page.wait_for_timeout(800)
    page.select_option(".sheetopts select >> nth=0", "frames"); page.wait_for_timeout(500)
    page.check(".sheetopts label:has-text('Godot 4') input")
    page.fill(".sheetcol > .sf:has-text('Save into') input", SAVE)
    page.fill(".sheetcol > .sf:has-text('Name') input", "run")
    page.screenshot(path=f"{OUT}/4_godot.png")
    page.click(".dlg button:text-is('Save')"); page.wait_for_timeout(1500)
    print("sprites saved:", page.inner_text(".sheetcol .ok") if page.locator(".sheetcol .ok").count() else page.inner_text(".sheetcol .err"))
    print("files:", sorted(os.listdir(SAVE)))
    print("errors:", errors)
    b.close()

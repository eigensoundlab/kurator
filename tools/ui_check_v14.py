"""Browser test for v0.14: print rows, inks view and PDF boxes, proofing profile, Unity import
dialog, KTX2 textures. Library from tools/make_v14_samples.py.

    python tools/ui_check_v14.py PORT OUT_DIR PROFILE_PATH
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, PROFILE = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "415" not in m.text else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=30000)
    root = page.evaluate(f"{K}.state.roots[0].path")

    def cell(name):
        i = page.evaluate(f"{K}.items.n").index(name)
        return page.locator(f".cell[data-i='{i}']:visible")

    page.evaluate(f"{K}.openPath('{root}/print')"); page.wait_for_timeout(1200)
    cell("poster_cmyk.jpg").click(); page.wait_for_timeout(1000)
    rows = page.locator("#panel .facts tr").all_inner_texts()
    print("panel:", [r.replace("\t", ": ") for r in rows if r.split("\t")[0] in ("Colour", "Print size")])
    box1 = page.locator("#panel .p-preview").bounding_box(); page.wait_for_timeout(800)
    box2 = page.locator("#panel .p-preview").bounding_box()
    print("preview steady:", box1 == box2, box2["width"], box2["height"])
    page.screenshot(path=f"{OUT}/1_panel.png")
    cell("poster_cmyk.jpg").dblclick(); page.wait_for_timeout(1000)
    print("inks button:", page.locator(".v-inkbtn").is_visible())
    page.keyboard.press("i"); page.wait_for_timeout(1500)
    for tab in ("C", "Total ink"):
        page.locator(".ink-controls .lit-tab", has_text=tab).first.click(); page.wait_for_timeout(400)
        page.screenshot(path=f"{OUT}/2_inks_{tab.replace(' ', '_')}.png")
    cb = page.locator("canvas.inkview").bounding_box()
    page.mouse.move(cb["x"] + cb["width"] * 0.5, cb["y"] + cb["height"] * 0.5); page.wait_for_timeout(200)
    print("readout:", page.inner_text(".ink-readout"))
    page.keyboard.press("Escape"); page.wait_for_timeout(300)

    cell("flyer.pdf").click(); page.wait_for_timeout(1000)
    print("pdf rows:", [r.replace("\t", ": ") for r in page.locator("#panel .facts tr").all_inner_texts() if r.split("\t")[0] in ("Page", "PDF", "Spot colours", "Fonts")])
    cell("flyer.pdf").dblclick(); page.wait_for_timeout(1500)
    print("boxes:", page.locator(".pdfboxes.on .pbox").count())
    page.screenshot(path=f"{OUT}/3_pdf_boxes.png")
    page.keyboard.press("Escape"); page.wait_for_timeout(300)

    r = page.evaluate(f"fetch('/api/settings', {{method: 'PUT', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{proof_profile: {PROFILE!r}}})}}).then(r => r.status)")
    page.evaluate(f"{K}.state.proof_profile = true")
    print("proof profile set:", r)
    cell("screen_rgb.png").dblclick(); page.wait_for_timeout(1000)
    page.keyboard.press("i"); page.wait_for_timeout(2000)
    print("proof tabs:", page.locator(".ink-controls .lit-tab").all_inner_texts())
    page.locator(".ink-controls .lit-tab", has_text="Gamut").click(); page.wait_for_timeout(400)
    page.screenshot(path=f"{OUT}/4_gamut.png")
    page.keyboard.press("i"); page.keyboard.press("Escape"); page.wait_for_timeout(300)

    page.evaluate(f"{K}.openPath('{root}/Game/Assets/Sprites')"); page.wait_for_timeout(1200)
    cell("hero.png").click(); page.wait_for_timeout(1000)
    page.click("#panel button:text-is('Unity import…')"); page.wait_for_timeout(500)
    page.click(".dlg button:text-is('Pixel art preset')")
    page.screenshot(path=f"{OUT}/5_unity.png")
    page.click(".dlg button:text-is('Apply')"); page.wait_for_timeout(1000)
    print("unity:", page.inner_text(".dlg .ok"))
    page.click(".dlg button:text-is('Close')")

    page.evaluate(f"{K}.openPath('{root}/textures')"); page.wait_for_timeout(1200)
    page.screenshot(path=f"{OUT}/6_ktx2.png")
    print("ktx2:", page.evaluate(f"Promise.all({K}.items.ids.map(i => fetch('/api/items/' + i).then(r => r.json()).then(d => [d.name, d.meta.texture, d.meta.preview_note || ''])))"))
    print("errors:", errors)
    b.close()

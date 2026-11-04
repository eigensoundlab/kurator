"""Browser test for v0.11: waveform view, Look filter (colours, shape, sounds), timeline scrubber,
sprite sheets and texture atlases.

    python tools/ui_check_v11.py PORT OUT_DIR SAVE_DIR
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, SAVE = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
os.makedirs(SAVE, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=30000)
    page.wait_for_timeout(1500)
    n = lambda: page.evaluate(f"{K}.items.ids.length")  # noqa: E731
    idx = lambda name: page.evaluate(f"{K}.items.n.indexOf('{name}')")  # noqa: E731
    root = page.evaluate(f"{K}.state.roots[0].path")
    print("items:", n())

    # --- timeline
    print("timeline shown:", page.evaluate("document.querySelector('#gridwrap').classList.contains('has-timeline')"))
    tl = page.locator("canvas.timeline").bounding_box()
    page.mouse.move(tl["x"] + 30, tl["y"] + tl["height"] * 0.6); page.wait_for_timeout(200)
    print("timeline tip:", page.inner_text(".timeline-tip"))
    page.mouse.down(); page.mouse.move(tl["x"] + 30, tl["y"] + tl["height"] * 0.8, steps=5); page.mouse.up(); page.wait_for_timeout(400)
    print("scrolled to:", page.evaluate("document.querySelector('#grid').scrollTop"))
    page.screenshot(path=f"{OUT}/1_timeline.png")

    # --- look filter
    page.click(".typefilter button:has-text('Look')"); page.wait_for_timeout(300)
    page.locator(".swatch[title='red']").click(); page.wait_for_timeout(900)
    print("red:", n(), "| button:", page.inner_text(".typefilter button:has-text('Look')"))
    page.screenshot(path=f"{OUT}/2_look.png")
    page.select_option(".lookpanel select >> nth=0", "panorama"); page.wait_for_timeout(800)
    print("red panoramas:", n(), "| chip:", page.inner_text("#pathbar").replace("\n", " ")[:120])
    page.click(".lookpanel button:text-is('Clear')"); page.wait_for_timeout(800)
    print("cleared:", n())
    page.fill(".lookpanel input[placeholder='from'] >> nth=0", "100"); page.keyboard.press("Tab"); page.wait_for_timeout(800)
    print("bpm>=100:", page.evaluate(f"{K}.items.n"))
    page.click(".lookpanel button:text-is('Clear')"); page.wait_for_timeout(600)
    page.mouse.click(800, 500); page.wait_for_timeout(300)

    # --- sound: panel waveform, viewer waveform with playhead, modes
    page.evaluate(f"{K}.openPath('{root}/sounds')"); page.wait_for_timeout(900)
    i = idx("groove_120.wav")
    page.locator(f".cell[data-i='{i}']").click(); page.wait_for_timeout(1500)
    print("panel rows:", [t.replace("\n", " ") for t in page.locator("#panel .facts tr").all_inner_texts() if t.split("\t")[0] in ("Loudness", "Music", "Length")])
    page.screenshot(path=f"{OUT}/3_sound_panel.png")
    page.locator(f".cell[data-i='{i}']").dblclick(); page.wait_for_timeout(1500)
    print("viewer mode-sound:", page.evaluate("document.querySelector('.viewer').classList.contains('mode-sound')"))
    wv = page.locator(".viewer canvas.wavecanvas").first.bounding_box() if page.locator(".viewer canvas.wavecanvas").count() else None
    if wv:
        page.mouse.click(wv["x"] + wv["width"] * 0.5, wv["y"] + wv["height"] * 0.5); page.wait_for_timeout(700)
        print("seek → time:", round(page.evaluate(f"{K}.audio ? {K}.audio.currentTime : -1"), 2))
    page.screenshot(path=f"{OUT}/4_wave_peaks.png")
    for m in ("minmax", "db", "stereo", "spectrogram"):
        sel = page.locator(".viewer select.wavemode")
        if sel.count():
            sel.select_option(m); page.wait_for_timeout(900)
            page.screenshot(path=f"{OUT}/5_wave_{m}.png")
    page.keyboard.press("Escape"); page.wait_for_timeout(400)

    # --- sprite sheet and atlas
    page.evaluate(f"{K}.openPath('{root}/sprites')"); page.wait_for_timeout(900)
    page.keyboard.press("Control+a"); page.keyboard.press("j"); page.wait_for_timeout(1200)
    page.click(".segs button:text-is('Sprites / atlas')"); page.wait_for_timeout(1800)
    print("atlas info:", page.inner_text(".sheetdlg .prevcol .muted.small"))
    page.fill(".sheetcol > .sf:has-text('Save into') input", SAVE)
    page.fill(".sheetcol > .sf:has-text('Name') input", "items")
    page.screenshot(path=f"{OUT}/6_atlas.png")
    page.click(".dlg button:text-is('Save')"); page.wait_for_timeout(1500)
    print("saved:", page.inner_text(".sheetcol .ok") if page.locator(".sheetcol .ok").count() else page.inner_text(".sheetcol .err"))
    page.select_option(".sheetopts select >> nth=0", "frames"); page.wait_for_timeout(1800)
    print("frames info:", page.inner_text(".sheetdlg .prevcol .muted.small"))
    page.screenshot(path=f"{OUT}/7_frames.png")
    page.keyboard.press("Escape")
    print("saved files:", sorted(os.listdir(SAVE)))
    print("errors:", errors)
    b.close()

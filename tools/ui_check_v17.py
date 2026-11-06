"""Browser test for v0.17: rows for long sounds, the resizable sound preview, and Playhead across the
library (Look filter, sorts, the "Analyse sounds" dialog, grid strips, markers).

Library from tools/make_v17_samples.py (plus tools/make_v16_samples.py). Start Kurator with
KURATOR_PLAYHEAD pointing at a Playhead command line to check the command-line job too.

    python tools/ui_check_v17.py PORT OUT_DIR
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

PORT, OUT = int(sys.argv[1]), sys.argv[2]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"
W = f"{K}.viewer.wave"
ARGS = ["--autoplay-policy=no-user-gesture-required"]

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    b = p.chromium.launch(executable_path=exe, args=ARGS) if exe else p.chromium.launch(args=ARGS)
    ctx = b.new_context(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    errors, requests = [], []
    expected = lambda m: "Failed to load resource" in m.text and "/playhead?v=" in (m.location or {}).get("url", "")  # noqa: E731
    page.on("console", lambda m: errors.append(f"{m.text} {(m.location or {}).get('url', '')}") if m.type == "error" and not expected(m) else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell", timeout=30000)
    page.wait_for_timeout(1500)

    def names():
        return page.evaluate(f"{K}.items.n")

    def cell(name):
        return page.locator(f".cell[data-i='{names().index(name)}']:visible")

    def open_viewer(name):
        if page.evaluate(f"{K}.viewer.isOpen"):
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        cell(name).click()
        page.wait_for_timeout(600)
        page.keyboard.press("Enter")
        page.wait_for_timeout(2000)

    # --- Playhead off: no Playhead requests, no sound-character section, no Playhead sorts
    page.click('.typefilter .btn:has-text("Look")')
    page.wait_for_timeout(400)
    print("off: sound character section:", page.locator(".ph-look-box").count(),
          "| Playhead sorts:", page.eval_on_selector('select[title="Sort by"]', "s => s.querySelectorAll('optgroup').length"),
          "| requests:", [u for u in requests if "/api/playhead" in u or "playhead-core" in u])
    page.mouse.click(700, 600)

    # --- the panel: rows and a taller preview, remembered
    cell("field_12min.flac").click()
    page.wait_for_timeout(1500)
    page.select_option(".p-preview-box .wave-rows", "64")
    box = page.locator(".p-grip").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 4)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 200, steps=6)
    page.mouse.up()
    page.wait_for_timeout(1200)
    h = page.evaluate("document.querySelector('.p-preview-box').getBoundingClientRect().height")
    print("panel: height", round(h), "| saved", page.evaluate("localStorage.getItem('kurator.panel.waveH')"),
          "| rows", page.evaluate(f"{K}.panel.wave.rows.length"), "| per row", page.evaluate(f"{K}.panel.wave.rows[0].per"), "s")
    page.screenshot(path=f"{OUT}/panel_rows.png")
    page.dblclick(".p-grip")
    page.wait_for_timeout(400)
    print("panel: double-click resets to", round(page.evaluate("document.querySelector('.p-preview-box').getBoundingClientRect().height")))
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 4)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 120, steps=4)
    page.mouse.up()

    # --- the viewer: rows of 120 px, every row the same time scale, finer data where needed
    open_viewer("field_12min.flac")
    page.select_option(".viewer .wave-rows", "120")
    page.wait_for_timeout(2500)
    rows = page.evaluate(f"{W}.rows.map(r => [r.a, r.b, r.per])")
    print("viewer rows:", len(rows), "| seconds per row:", rows[0][2], "| same scale:", len({round(r[1] - r[0], 9) for r in rows}) == 1,
          "| detail:", page.evaluate(f"{W}.det && [{W}.det.start, Math.round({W}.det.end), {W}.det.bins]"))
    print("rows menu title:", page.get_attribute(".viewer .wave-rows", "title"))
    page.screenshot(path=f"{OUT}/viewer_rows.png")
    c = page.locator(".viewer canvas.wavecanvas").bounding_box()
    page.mouse.move(c["x"] + c["width"] * 0.7, c["y"] + c["height"] * 0.15)
    page.mouse.down()
    page.mouse.move(c["x"] + c["width"] * 0.3, c["y"] + c["height"] * 0.45, steps=8)
    page.mouse.up()
    page.wait_for_timeout(400)
    s = page.evaluate(f"{W}.sel")
    print("selection across rows:", [round(s["a"], 1), round(s["b"], 1)], "|", page.inner_text(".wave-selbar .sel-time"))
    page.screenshot(path=f"{OUT}/viewer_rows_selection.png")
    page.click(".wave-selbar button:has-text('✕')")
    # click on the third row plays from there
    page.mouse.click(c["x"] + c["width"] * 0.5, c["y"] + c["height"] * 0.62)
    page.wait_for_timeout(500)
    t = page.evaluate(f"{K}.audio.currentTime")
    print("click on row 3 plays from", round(t, 1), "s (row 3 starts at", rows[2][0] * 720, ")")
    fps = page.evaluate(f"""async () => {{ const w = {W}; let n = 0, slow = 0; const orig = w.draw.bind(w);
      w.draw = () => {{ const t = performance.now(); orig(); if (performance.now() - t > 8) slow++; n++; }};
      await new Promise(r => setTimeout(r, 1500)); w.draw = orig; w.app.audio.pause(); return [n, slow]; }}""")
    print("playback frames in 1.5 s, slower than 8 ms:", fps)
    # zoom in on a row, then move along with Shift+wheel
    page.mouse.move(c["x"] + c["width"] * 0.5, c["y"] + c["height"] * 0.4)
    for _ in range(8):
        page.mouse.wheel(0, -250)
        page.wait_for_timeout(50)
    page.wait_for_timeout(1500)
    z = page.evaluate(f"[{W}.t0 * {W}.duration, {W}.t1 * {W}.duration, {W}.rows.length]")
    page.keyboard.down("Shift")
    page.mouse.wheel(0, 400)
    page.keyboard.up("Shift")
    page.wait_for_timeout(300)
    print("zoomed:", [round(x, 1) for x in z[:2]], "rows", z[2], "| Shift+wheel moved:", page.evaluate(f"{W}.t0 * {W}.duration") > z[0],
          "| detail:", page.evaluate(f"{W}.det && [Math.round({W}.det.start), Math.round({W}.det.end), {W}.det.bins]"))
    page.dblclick(".viewer canvas.wavecanvas")
    # spectrogram on rows; an AAC file (only ffmpeg reads it) past 10 minutes
    page.select_option(".viewer .wavemode", "spectrogram")
    page.wait_for_timeout(3000)
    print("spectrogram 12 min:", page.evaluate(f"{W}.spec && [{W}.spec.start, Math.round({W}.spec.end)]"),
          "| finer:", page.evaluate(f"{W}.specDet && [{W}.specDet.start, Math.round({W}.specDet.end), {W}.specDet.cols]"))
    page.screenshot(path=f"{OUT}/viewer_rows_spectrogram.png")
    if "talk_14min.m4a" in names():
        open_viewer("talk_14min.m4a")
        t0 = time.time()
        while time.time() - t0 < 30 and not page.evaluate(f"!!{W}.spec"):
            page.wait_for_timeout(300)
        page.wait_for_timeout(500)
        print("AAC 14 min spectrogram covers:", page.evaluate(f"{W}.spec && [{W}.spec.start, Math.round({W}.spec.end)]"),
              f"(after {time.time() - t0:.1f} s)")
        page.screenshot(path=f"{OUT}/viewer_aac_spectrogram.png")
    page.select_option(".viewer .wavemode", "peaks")
    page.select_option(".viewer .wave-rows", "0")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    # --- Playhead on: analyse the library, then filter, sort, strips, markers
    page.evaluate("localStorage.setItem('kurator.playhead.enabled', 'true')")
    page.reload()
    page.wait_for_selector(".cell", timeout=30000)
    page.wait_for_timeout(1500)
    page.click('button[title*="Settings"], button:has-text("⚙")')
    page.wait_for_timeout(800)
    page.check('.dlg label:has-text("Playhead strips") input')
    page.click('.dlg button:has-text("Analyse sounds")')
    page.wait_for_timeout(1000)
    st = page.inner_text(".phlibdlg")
    cli = page.is_enabled("input[name=phhow][value=cli]")
    print("dialog:", st.splitlines()[0][:80], "| command line:", cli)
    page.screenshot(path=f"{OUT}/analyse_dialog.png")
    if not cli:
        page.check("input[name=phhow][value=browser]")
    page.click(".dlg-buttons button:has-text('Start')")
    t0 = time.time()
    while time.time() - t0 < 300:
        txt = page.inner_text(".ph-run")
        if txt.startswith(("Done", "Stopped")):
            break
        page.wait_for_timeout(500)
    print("analysis:", page.inner_text(".ph-run").splitlines()[0], f"in {time.time() - t0:.1f} s")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    page.keyboard.press("Escape")
    page.wait_for_timeout(2000)
    print("grid strips:", page.evaluate("document.querySelectorAll('.cell.ph-strip').length"), "of", page.evaluate("document.querySelectorAll('.cell').length"))
    page.screenshot(path=f"{OUT}/grid_strips.png")
    page.click('.typefilter .btn:has-text("Look")')
    page.wait_for_timeout(800)
    page.screenshot(path=f"{OUT}/look_sound_character.png")
    for chip in ("Swell-like", "Long ring", "Bursty", "Sections"):
        page.click(f'.ph-look-box button:has-text("{chip}")')
        page.wait_for_timeout(900)
        print(f"filter {chip}:", names())
        page.click(f'.ph-look-box button:has-text("{chip}")')
        page.wait_for_timeout(700)
    page.click('.ph-look-box button:has-text("Hit-like")')
    page.click('.ph-look-box button:has-text("Swell-like")')
    page.wait_for_timeout(900)
    print("hit or swell:", len(names()), "| path bar:", page.locator(".chip:has-text('Hit-like')").count())
    page.click('.lookpanel button:has-text("Clear")')
    page.mouse.click(700, 600)
    page.wait_for_timeout(600)
    for sort in ("harshness", "ring", "burstiness"):
        page.select_option('select[title="Sort by"]', sort)
        page.wait_for_timeout(900)
        print(f"sort {sort}:", names()[:4])
    page.select_option('select[title="Sort by"]', "date")
    page.wait_for_timeout(600)
    cell("sections_120bpm.wav").click()
    page.wait_for_timeout(2500)
    page.click('.p-ph button:has-text("Markers")')
    page.wait_for_timeout(600)
    page.check('.phmarkdlg label:has-text("Bar lines") input')
    page.screenshot(path=f"{OUT}/markers.png")
    with page.expect_download() as dl:
        page.click('.dlg-buttons button:has-text("Save")')
    with open(dl.value.path()) as fh:
        lines = fh.read().splitlines()
    print("markers:", dl.value.suggested_filename, "|", lines[0], "|", lines[1], "|", len(lines) - 1, "rows")
    # off again: filters and sorts rest, strips go
    page.evaluate("localStorage.setItem('kurator.playhead.enabled', 'false')")
    page.reload()
    page.wait_for_selector(".cell", timeout=30000)
    page.wait_for_timeout(1500)
    print("off again: strips", page.evaluate("document.querySelectorAll('.cell.ph-strip').length"),
          "| Playhead sorts:", page.eval_on_selector('select[title="Sort by"]', "s => s.querySelectorAll('optgroup').length"))
    print("errors:", errors)
    b.close()

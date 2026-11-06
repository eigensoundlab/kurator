"""Browser test for v0.16: the optional Playhead analysis view of sounds.

Library from tools/make_v16_samples.py. Checks: no Playhead entry while it is off; switching it on in
Settings; a short sound and a long one (sections) in the viewer in "Playhead analysis"; the canvas is
not blank; the wheel zoom redraws the strip; playback moves the cursor over it; looks (Automatic,
Structure, a pasted code) change the legend and the feedback link; the side panel section; switching
it off falls back to Peak + RMS; no console errors.

    python tools/ui_check_v16.py PORT OUT_DIR
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

PORT, OUT = int(sys.argv[1]), sys.argv[2]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"
ARGS = ["--autoplay-policy=no-user-gesture-required"]

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    b = p.chromium.launch(executable_path=exe, args=ARGS) if exe else p.chromium.launch(args=ARGS)
    page = b.new_page(viewport={"width": 1400, "height": 900})
    errors, requests = [], []
    # a 404 from /api/audio/<id>/playhead is the documented "no stored analysis yet" answer, not an error
    expected = lambda m: "Failed to load resource" in m.text and "/playhead?v=" in (m.location or {}).get("url", "")  # noqa: E731
    page.on("console", lambda m: errors.append(f"{m.text} {(m.location or {}).get('url', '')}") if m.type == "error" and not expected(m) else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell", timeout=30000)
    page.wait_for_timeout(1500)
    root = page.evaluate(f"{K}.state.roots[0].path")

    def cell(name):
        i = page.evaluate(f"{K}.items.n").index(name)
        return page.locator(f".cell[data-i='{i}']:visible")

    def modes(sel):
        return page.eval_on_selector(sel, "s => [...s.options].map(o => o.value)")

    def strip_stats():
        """Mean and spread of the viewer canvas below the ruler: a blank strip has no spread."""
        return page.evaluate("""() => { const c = document.querySelector('.viewer canvas.wavecanvas'); const g = c.getContext('2d');
          const d = g.getImageData(0, Math.round(c.height * 0.2), c.width, Math.round(c.height * 0.6)).data; let s = 0, s2 = 0, n = 0;
          for (let i = 0; i < d.length; i += 16) { const v = d[i] + d[i + 1] + d[i + 2]; s += v; s2 += v * v; n++; }
          const m = s / n; return [Math.round(m), Math.round(Math.sqrt(s2 / n - m * m))]; }""")

    def wait_doc(timeout=90):
        t = time.time()
        while time.time() - t < timeout:
            if page.evaluate(f"!!{K}.viewer.wave.phd || !!{K}.viewer.wave.phErr"):
                return round(time.time() - t, 1)
            page.wait_for_timeout(200)
        return None

    # --- Playhead off: nothing about it, no requests for it
    page.evaluate(f"{K}.openPath('{root}/sfx')"); page.wait_for_timeout(1200)
    cell("metal_hit.wav").click(); page.wait_for_timeout(1200)
    print("off: panel modes", modes("#panel .wavemode"), "| Playhead section:", page.locator("#panel summary:text-is('Playhead')").count())
    print("off: requests for Playhead:", [u for u in requests if "playhead" in u.lower()])

    # --- switch it on in Settings
    page.click("button[title^='Settings']")
    page.wait_for_timeout(800)
    page.locator(".dlg label:has-text('Playhead analysis (experimental)') input").check()
    page.wait_for_timeout(400)
    page.keyboard.press("Escape"); page.wait_for_timeout(500)
    page.wait_for_timeout(1500)
    print("on: panel modes", modes("#panel .wavemode")[-1], "| section:", page.locator("#panel summary:text-is('Playhead')").count(),
          "| button:", page.locator("#panel button:text-is('Analyse with Playhead')").count())

    # --- short sound in the viewer
    cell("metal_hit.wav").dblclick(); page.wait_for_timeout(1500)
    print("viewer modes:", modes(".viewer .wavemode"))
    before = strip_stats()
    page.select_option(".viewer .wavemode", "playhead")
    page.wait_for_timeout(300)
    print("note while analysing:", page.evaluate(f"{K}.viewer.wave.phd ? 'ready already' : 'analysing'"))
    print("short sound analysed in", wait_doc(), "s; error:", page.evaluate(f"{K}.viewer.wave.phErr"))
    page.wait_for_timeout(500)
    st = strip_stats()
    print("canvas (mean, spread):", before, "->", st, "| legend:", page.inner_text(".viewer .ph-legend"))
    print("profile:", page.evaluate(f"{K}.viewer.wave.phd.profile"), "| events:", page.evaluate(f"{K}.viewer.wave.phd.events.length"))
    page.screenshot(path=f"{OUT}/1_viewer_sfx_auto.png")

    # looks: Sound character, Hits and surprise, Structure (music only: greyed out for an effect)
    print("looks:", page.eval_on_selector(".viewer .ph-look", "s => [...s.options].map(o => o.value + (o.disabled ? '(off)' : ''))"))
    for look in ("character", "surprise"):
        page.select_option(".viewer .ph-look", look); page.wait_for_timeout(500)
        print(f"look {look}:", page.inner_text(".viewer .ph-legend"), "| feedback:", page.get_attribute(".viewer .ph-fb", "href").split("?")[1])
        page.screenshot(path=f"{OUT}/2_viewer_sfx_{look}.png")

    # zoom with the wheel: the strip follows
    box = page.locator(".viewer canvas.wavecanvas").bounding_box()
    k0 = page.evaluate(f"{K}.viewer.wave.baseKey")
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.5)
    for _ in range(5):
        page.mouse.wheel(0, -300); page.wait_for_timeout(100)
    page.wait_for_timeout(400)
    print("zoom:", [round(v, 3) for v in page.evaluate(f"[{K}.viewer.wave.t0, {K}.viewer.wave.t1]")],
          "| strip redrawn:", page.evaluate(f"{K}.viewer.wave.baseKey") != k0, "| canvas:", strip_stats())
    page.screenshot(path=f"{OUT}/3_viewer_zoomed.png")
    page.dblclick(".viewer canvas.wavecanvas"); page.wait_for_timeout(300)

    # click to play: the cursor moves over the strip
    page.mouse.click(box["x"] + box["width"] * 0.1, box["y"] + box["height"] * 0.6)
    page.wait_for_timeout(700)
    t1 = page.evaluate(f"{K}.audio.currentTime")
    page.wait_for_timeout(500)
    t2 = page.evaluate(f"{K}.audio.currentTime")
    print("playing:", round(t1, 2), "->", round(t2, 2), "| playing id is this sound:", page.evaluate(f"{K}.playingId === {K}.viewer.wave.id"))
    page.screenshot(path=f"{OUT}/4_viewer_playing.png")
    page.evaluate(f"{K}.audio.pause()")

    # a custom look code; a bad one is refused
    page.select_option(".viewer .ph-look", "custom"); page.wait_for_timeout(200)
    page.fill(".viewer .ph-code", "nonsense,,,"); page.press(".viewer .ph-code", "Enter"); page.wait_for_timeout(200)
    print("bad code refused:", page.evaluate("document.querySelector('.viewer .ph-code').classList.contains('bad')"))
    code = "wave:harshness,lane:onsetStrength,lane:n1Kurtosis~signed,ticks:events"
    page.fill(".viewer .ph-code", code); page.press(".viewer .ph-code", "Enter"); page.wait_for_timeout(500)
    print("custom:", page.inner_text(".viewer .ph-legend"), "| feedback has the code:",
          "look=wave%3Aharshness" in page.get_attribute(".viewer .ph-fb", "href"), "| saved:", page.evaluate("localStorage.getItem('kurator.playhead.look')"))
    page.screenshot(path=f"{OUT}/5_viewer_custom.png")
    page.locator(".viewer .ph-code").blur()
    page.keyboard.press("Escape"); page.wait_for_timeout(400)

    # AIFF (the browser can't decode it; Kurator decodes it for Playhead)
    cell("swell.aiff").dblclick(); page.wait_for_timeout(800)
    page.select_option(".viewer .ph-look", "auto"); page.wait_for_timeout(200)
    print("AIFF analysed in", wait_doc(), "s; error:", page.evaluate(f"{K}.viewer.wave.phErr"),
          "| time arrow:", page.evaluate(f"{K}.viewer.wave.phd && {K}.viewer.wave.phd.descriptors && {K}.viewer.wave.phd.descriptors.arrow.T20"))
    page.keyboard.press("Escape"); page.wait_for_timeout(400)

    # the side panel: the analysed hit has its Playhead section
    cell("metal_hit.wav").click(); page.wait_for_timeout(1500)
    print("panel rows:", page.locator("#panel .p-ph tr").all_inner_texts())
    page.screenshot(path=f"{OUT}/6_panel_sfx.png")
    cell("crackle.wav").click(); page.wait_for_timeout(1200)
    print("crackle, before:", page.locator("#panel .p-ph button").all_inner_texts())
    page.click("#panel button:text-is('Analyse with Playhead')")
    t = time.time()
    while page.locator("#panel .p-ph tr").count() == 0 and time.time() - t < 60:
        page.wait_for_timeout(200)
    print("crackle rows:", page.locator("#panel .p-ph tr").all_inner_texts())

    # --- long music: 22.05 kHz mono, music profile, sections
    page.evaluate(f"{K}.openPath('{root}/music')"); page.wait_for_timeout(1200)
    cell("sections_120bpm.wav").dblclick(); page.wait_for_timeout(2500)
    print("long sound analysed in", wait_doc(180), "s; error:", page.evaluate(f"{K}.viewer.wave.phErr"))
    d = page.evaluate(f"(() => {{ const d = {K}.viewer.wave.phd; return d && [d.profile, d.source.sampleRate, d.source.channels, d.rhythm && d.rhythm.tempo, d.sections && d.sections.map(s => s.label).join('')]; }})()")
    print("music doc (profile, rate, channels, tempo, sections):", d)
    page.wait_for_timeout(500)
    print("auto legend:", page.inner_text(".viewer .ph-legend"))
    page.screenshot(path=f"{OUT}/7_viewer_music_auto.png")
    page.select_option(".viewer .ph-look", "structure"); page.wait_for_timeout(600)
    print("structure legend:", page.inner_text(".viewer .ph-legend"))
    page.screenshot(path=f"{OUT}/8_viewer_music_structure.png")
    page.keyboard.press("Escape"); page.wait_for_timeout(400)
    cell("sections_120bpm.wav").click(); page.wait_for_timeout(1500)
    print("music panel rows:", page.locator("#panel .p-ph tr").all_inner_texts())
    page.screenshot(path=f"{OUT}/9_panel_music.png")

    # stored: a new page gets the document from Kurator's store, without analysing again
    stored = page.evaluate(f"fetch('/api/audio/' + {K}.items.ids[0] + '/playhead?v=0.2.0').then(r => r.status)")
    print("stored on the server:", stored)

    # --- switch it off: the style falls back to Peak + RMS
    page.click("button[title^='Settings']")
    page.wait_for_timeout(800)
    page.locator(".dlg label:has-text('Playhead analysis (experimental)') input").uncheck()
    page.wait_for_timeout(400)
    page.keyboard.press("Escape"); page.wait_for_timeout(500)
    print("off again: panel mode", page.input_value("#panel .wavemode"), "| modes", modes("#panel .wavemode"),
          "| saved mode", page.evaluate("localStorage.getItem('kurator.wave.mode')"),
          "| section:", page.locator("#panel summary:text-is('Playhead')").count())
    print("errors:", errors)
    b.close()

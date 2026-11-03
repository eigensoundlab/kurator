"""Browser test for v0.8: projects as the top filter (switcher, modes, sources, + / −), video scrub
and playback.

    python tools/ui_check_v8.py PORT OUT_DIR
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT = int(sys.argv[1]), sys.argv[2]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    page = b.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=30000)
    page.wait_for_timeout(1200)
    total = page.evaluate(f"{K}.items.ids.length")
    print("items:", total)
    n = lambda: page.evaluate(f"{K}.items.ids.length")  # noqa: E731

    # --- subject tag to use as a source
    page.locator(".facet-head", has_text="Subjects").locator("button").click()
    page.fill(".dlg label:has-text('Name') input", "Creatures")
    page.click(".dlg .btn.primary"); page.wait_for_timeout(400)
    page.locator(".node", has_text="models").first.click(); page.wait_for_timeout(700)
    page.keyboard.press("Control+a"); page.wait_for_timeout(200)
    hk = page.evaluate(f"{K}.tags.list.find(t => t.name === 'Creatures').hotkey")
    page.keyboard.press(str(hk)); page.wait_for_timeout(600)
    creatures = page.evaluate(f"{K}.tags.list.find(t => t.name === 'Creatures').count")
    print("tagged creatures:", creatures)
    page.keyboard.press("Escape")
    page.locator(".node", has_text="All library items").first.click(); page.wait_for_timeout(600)

    # --- new project from the switcher, including the Creatures tag and the video folder
    page.click(".projbtn"); page.wait_for_timeout(200)
    page.screenshot(path=f"{OUT}/1_switcher_empty.png")
    page.click(".pm-row:has-text('New project')"); page.wait_for_timeout(400)
    page.fill(".projdlg input[type=text]", "Game A")
    page.select_option(".projdlg select >> nth=0", label=page.locator(".projdlg select >> nth=0 >> option", has_text="Creatures").inner_text())
    vid_folder = page.evaluate(f"{K}.state.roots[0].path") + "/videos"
    page.fill(".projdlg .row input[placeholder^='Folder']", vid_folder)
    page.click(".projdlg button:text-is('+ Folder')"); page.wait_for_timeout(200)
    page.screenshot(path=f"{OUT}/2_project_dialog.png")
    page.click(".dlg .btn.primary"); page.wait_for_timeout(1500)
    print("project active:", page.inner_text(".projbtn"), "| items in project:", n())
    print("pathbar:", page.inner_text("#pathbar").replace("\n", " ")[:120])
    print("sidebar library:", page.inner_text("#sidebar").split("TAGS")[0].replace("\n", " ")[-160:])
    page.screenshot(path=f"{OUT}/3_in_project.png")

    # narrow inside the project: type filter Video
    page.click(".typefilter > button"); page.wait_for_timeout(200)
    page.click(".tp-presets button:text-is('Video')"); page.wait_for_timeout(800)
    page.mouse.click(1100, 900); page.wait_for_timeout(200)
    print("videos in project:", n())
    page.click(".typefilter > button"); page.wait_for_timeout(200)
    page.click(".tp-presets button:text-is('All files')"); page.wait_for_timeout(800)
    page.mouse.click(1100, 900); page.wait_for_timeout(200)

    # --- add from library: members marked, + adds, − removes
    page.click(".segs .seg:text-is('Add from library')"); page.wait_for_timeout(1200)
    print("add mode items:", n(), "| members marked:", page.locator(".cell.member").count())
    nonmember = page.evaluate(f"{K}.items.m.indexOf(0)")
    page.locator(f".cell[data-i='{nonmember}']").click(); page.wait_for_timeout(300)
    print("add button:", page.locator("#pathbar button.primary").inner_text())
    page.keyboard.press("+"); page.wait_for_timeout(800)
    print("after +:", page.evaluate(f"{K}.items.m[{nonmember}]"), "| toast:", page.locator(".toast").inner_text())
    page.screenshot(path=f"{OUT}/4_add_mode.png")
    page.click(".segs .seg:text-is('In project')"); page.wait_for_timeout(1000)
    after_add = n()
    print("in project now:", after_add)
    # remove a file that's in only because of a source -> stays, toast explains
    page.locator(".cell:visible").nth(0).click(); page.wait_for_timeout(300)
    page.keyboard.press("-"); page.wait_for_timeout(800)
    print("− on a source member:", page.locator(".toast").inner_text())

    # --- sidebar: clicking the project toggles it; switcher lists it
    page.locator(".node.tagnode", has_text="Game A").click(); page.wait_for_timeout(800)
    print("left project:", page.inner_text(".projbtn"), n())
    page.locator(".node.tagnode", has_text="Game A").click(); page.wait_for_timeout(800)
    print("back in project:", page.inner_text(".projbtn"), n())
    page.click(".projbtn"); page.wait_for_timeout(200)
    print("switcher:", page.inner_text(".projmenu").replace("\n", " | "))
    page.screenshot(path=f"{OUT}/5_switcher.png")
    page.keyboard.press("Escape"); page.mouse.click(1100, 900)

    # --- video: hover scrub, viewer playback
    page.click(".projbtn"); page.wait_for_timeout(200)
    page.click(".pm-row:has-text('Whole library')"); page.wait_for_timeout(900)
    page.click(".typefilter > button"); page.wait_for_timeout(200)
    page.click(".tp-presets button:text-is('Video')"); page.wait_for_timeout(900)
    page.mouse.click(1100, 900); page.wait_for_timeout(300)
    i = page.evaluate(f"{K}.items.n.indexOf('phone.mp4')")
    cell = page.locator(f".cell[data-i='{i}']")
    bb = cell.bounding_box()
    page.mouse.move(bb["x"] + 10, bb["y"] + bb["height"] / 2); page.wait_for_timeout(700)
    page.mouse.move(bb["x"] + bb["width"] * 0.8, bb["y"] + bb["height"] / 2, steps=5); page.wait_for_timeout(300)
    print("scrubbing:", cell.evaluate("c => c.classList.contains('scrubbing')"), cell.locator(".strip").evaluate("s => s.dataset.k"))
    page.screenshot(path=f"{OUT}/6_scrub.png")
    cell.click(); page.wait_for_timeout(500)
    print("panel:", page.inner_text("#panel .kv.facts").replace("\n", " | ")[:220])
    # open-source Chromium has no H.264: play the WebM clip in the viewer
    page.locator(f".cell[data-i='{page.evaluate(f'{K}.items.n.indexOf(\'loop.webm\')')}']").click(); page.wait_for_timeout(300)
    page.keyboard.press("Enter"); page.wait_for_timeout(2500)
    st = page.evaluate("(() => { const v = document.querySelector('.v-video'); return {cls: document.querySelector('.viewer').className, t: v.currentTime, paused: v.paused, w: v.videoWidth}; })()")
    print("viewer video:", st)
    page.screenshot(path=f"{OUT}/7_video.png")
    page.keyboard.press(" "); page.wait_for_timeout(300)
    print("space pauses:", page.evaluate("document.querySelector('.v-video').paused"))
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    print("console errors:", errors[:10])
    b.close()

"""Browser test for v0.7: 3D thumbnails and viewer, sound auto-play and ▶, tag clicks / exclusion,
drag selection onto a project, remove from project.

    python tools/ui_check_v7.py PORT OUT_DIR
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT = int(sys.argv[1]), sys.argv[2]
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
    print("items:", page.evaluate(f"{K}.items.ids.length"))

    # --- 3D group
    page.click(".typefilter > button"); page.wait_for_timeout(200)
    page.click(".tp-presets button:text-is('All files')"); page.wait_for_timeout(300)
    page.locator(".tp-head", has_text="3D models").locator("input").check(); page.wait_for_timeout(900)
    page.mouse.click(1100, 900); page.wait_for_timeout(300)
    print("3d items:", page.evaluate(f"{K}.items.ids.length"))
    page.wait_for_timeout(2500)
    page.screenshot(path=f"{OUT}/1_models.png")
    idx = page.evaluate(f"{K}.items.n.indexOf('scene.glb')")
    page.locator(f".cell[data-i='{idx}']").click(); page.wait_for_timeout(800)
    print("panel:", page.inner_text("#panel .kv.facts").replace("\n", " | ")[:200])
    page.keyboard.press("Enter"); page.wait_for_timeout(2500)
    print("viewer 3d:", page.evaluate("document.querySelector('.viewer').className"), "|", page.inner_text(".v-zoom"))
    page.screenshot(path=f"{OUT}/2_viewer3d.png")
    box = page.locator("canvas.meshview").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy); page.mouse.down(); page.mouse.move(cx + 220, cy + 60, steps=8); page.mouse.up()
    page.mouse.wheel(0, -400); page.wait_for_timeout(500)
    page.screenshot(path=f"{OUT}/3_viewer3d_turned.png")
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(2000)
    print("next:", page.inner_text(".v-title"), "|", page.evaluate("document.querySelector('.viewer').className"))
    page.keyboard.press("Escape"); page.wait_for_timeout(400)

    # --- sounds
    page.click(".typefilter > button"); page.wait_for_timeout(200)
    page.click(".tp-presets button:text-is('Audio')"); page.wait_for_timeout(900)
    page.mouse.click(1100, 900); page.wait_for_timeout(300)
    print("auto-play button:", page.inner_text("#statusbar").split("\n")[:3])
    page.locator(".cell:visible").nth(1).click(); page.wait_for_timeout(900)
    print("click plays:", page.evaluate(f"{K}.playingId != null"), "| playing cells:", page.locator(".cell.playing").count())
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(900)
    print("arrow plays next:", page.evaluate(f"{K}.playingId === {K}.focusId"))
    page.locator(".cell.playing .play").click(); page.wait_for_timeout(300)
    print("▶ stops:", page.evaluate(f"{K}.playingId == null"))
    page.locator(".cell:visible").nth(3).locator(".play").click(); page.wait_for_timeout(700)
    print("▶ on another plays without selecting it:", page.evaluate(f"{K}.playingId === {K}.items.ids[3]"),
          page.evaluate(f"{K}.selected.has({K}.items.ids[3])"))
    page.screenshot(path=f"{OUT}/4_audio.png")
    page.keyboard.press("p"); page.wait_for_timeout(300)
    print("P toggles:", page.inner_text("#statusbar button.on") if page.locator("#statusbar button.on").count() else "off")
    page.click(".typefilter > button"); page.wait_for_timeout(200)
    page.click(".tp-presets button:text-is('All files')"); page.wait_for_timeout(900)
    page.mouse.click(1100, 900); page.wait_for_timeout(300)

    # --- projects: create, drag selection onto it, click semantics, exclude, remove
    page.locator(".facet-head", has_text="Projects").locator("button").click()
    page.fill(".dlg label:has-text('Name') input", "Game A")
    page.click(".dlg .btn.primary"); page.wait_for_timeout(500)
    page.locator(".node", has_text="formats").first.click(); page.wait_for_timeout(900)
    folder_n = page.evaluate(f"{K}.items.ids.length")
    cells = page.locator(".cell:visible")
    cells.nth(0).click(); cells.nth(2).click(modifiers=["Shift"]); page.wait_for_timeout(300)
    src = cells.nth(1).bounding_box()
    dst = page.locator(".node.tagnode", has_text="Game A").bounding_box()
    page.mouse.move(src["x"] + 40, src["y"] + 40); page.mouse.down()
    page.mouse.move(src["x"] + 80, src["y"] + 60, steps=4)
    page.mouse.move(dst["x"] + 30, dst["y"] + 8, steps=12)
    page.screenshot(path=f"{OUT}/5_drag.png")
    print("ghost:", page.inner_text(".dragghost"))
    page.mouse.up(); page.wait_for_timeout(800)
    print("toast:", page.locator(".toast").inner_text())
    print("Game A count:", page.locator(".node.tagnode", has_text="Game A").locator(".ct").inner_text())
    print("folder view before:", folder_n, "| pathbar:", page.inner_text("#pathbar").replace("\n", " ")[:80])
    page.locator(".node.tagnode", has_text="Game A").click(); page.wait_for_timeout(900)
    print("click project -> whole library:", page.evaluate(f"{K}.query.folder"), page.evaluate(f"{K}.items.ids.length"),
          "|", page.inner_text("#pathbar").replace("\n", " ")[:80])
    page.locator(".cell:visible").nth(0).click(); page.wait_for_timeout(400)
    rm = page.locator("#pathbar button", has_text="Remove")
    print("remove button:", rm.inner_text())
    rm.click(); page.wait_for_timeout(700)
    page.keyboard.press("r"); page.wait_for_timeout(800)
    print("after remove + R:", page.evaluate(f"{K}.items.ids.length"))
    page.screenshot(path=f"{OUT}/6_project.png")
    page.click("#pathbar button:text-is('Clear filters')"); page.wait_for_timeout(700)
    total = page.evaluate(f"{K}.items.ids.length")
    page.locator(".node.tagnode", has_text="Game A").click(modifiers=["Alt"]); page.wait_for_timeout(900)
    print("alt-click excludes:", total, "->", page.evaluate(f"{K}.items.ids.length"), "|", page.inner_text("#pathbar").replace("\n", " ")[:80])
    page.screenshot(path=f"{OUT}/7_exclude.png")
    print("console errors:", errors[:10])
    b.close()

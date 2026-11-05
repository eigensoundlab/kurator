"""Browser test for v0.12: textured 3D viewer (maps, view modes, lights), 2D lit preview with
companion maps, SVG setting. Needs a library made by tools/make_v12_samples.py.

    python tools/ui_check_v12.py PORT OUT_DIR
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT = int(sys.argv[1]), sys.argv[2]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=30000)
    root = page.evaluate(f"{K}.state.roots[0].path")

    # --- 3D
    page.evaluate(f"{K}.openPath('{root}/models')"); page.wait_for_timeout(1500)
    names = page.evaluate(f"{K}.items.n")
    i = names.index("bricks_ball.glb")
    page.locator(f".cell[data-i='{i}']:visible").click(); page.wait_for_timeout(800)
    print("panel textures:", page.locator("#panel td.maps").inner_text().replace("\n", " | "))
    page.screenshot(path=f"{OUT}/1_models.png")
    page.locator(f".cell[data-i='{i}']:visible").dblclick(); page.wait_for_selector(".viewer.mesh-ready", timeout=30000); page.wait_for_timeout(2500)
    print("maps in viewer:", page.locator(".mv-map").all_inner_texts())
    page.screenshot(path=f"{OUT}/2_lit.png")
    for mode in ("normals", "uv", "clay"):
        page.select_option(".mv-controls select >> nth=0", mode); page.wait_for_timeout(500)
        page.screenshot(path=f"{OUT}/3_{mode}.png")
    page.select_option(".mv-controls select >> nth=0", "lit")
    page.keyboard.press("l"); page.wait_for_timeout(400)
    print("light after L:", page.evaluate(f"{K}.viewer.mv.lights"))
    c = page.locator("canvas.meshview").bounding_box()
    page.keyboard.down("Alt")
    page.mouse.move(c["x"] + 600, c["y"] + 400); page.mouse.down(); page.mouse.move(c["x"] + 800, c["y"] + 400, steps=4); page.mouse.up()
    page.keyboard.up("Alt")
    print("light turned:", round(page.evaluate(f"{K}.viewer.mv.lightYaw"), 2))
    page.screenshot(path=f"{OUT}/4_lights.png")
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    page.click(".typefilter button:has-text('Look')"); page.wait_for_timeout(300)
    page.select_option(".lookpanel select >> nth=2", "missing"); page.wait_for_timeout(900)
    print("missing textures:", page.evaluate(f"{K}.items.n"))
    page.click(".lookpanel button:text-is('Clear')"); page.wait_for_timeout(500)
    page.mouse.click(700, 600)

    # --- 2D maps
    page.evaluate(f"{K}.openPath('{root}/sprites')"); page.wait_for_timeout(1500)
    names = page.evaluate(f"{K}.items.n")
    i = names.index("hero.png")
    page.locator(f".cell[data-i='{i}']:visible").click(); page.wait_for_timeout(1000)
    print("panel 2D maps:", page.locator("#panel .rel").last.inner_text().replace("\n", " "))
    page.locator(f".cell[data-i='{i}']:visible").dblclick(); page.wait_for_timeout(1000)
    print("lit button:", page.locator(".v-litbtn").is_visible())
    page.keyboard.press("l"); page.wait_for_timeout(1500)
    box = page.locator("canvas.lit2d").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.3); page.wait_for_timeout(400)
    page.screenshot(path=f"{OUT}/5_lit2d.png")
    page.locator(".lit-tab", has_text="Depth").click(); page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/6_depth.png")
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(800)
    print("next image keeps lit:", page.evaluate(f"{K}.viewer.litOn"), page.evaluate(f"{K}.viewer.items.n[{K}.viewer.i]"))
    page.keyboard.press("Escape")
    print("errors:", errors)
    b.close()

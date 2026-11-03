"""Browser smoke test of the UI (needs a running Kurator on PORT).

    python tools/ui_check.py [PORT] [OUT_DIR]
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/ui"
os.makedirs(OUT, exist_ok=True)
URL = f"http://127.0.0.1:{PORT}/"

def sel_count(page):
    return page.evaluate("window.kurator.selected.size")

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 950})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.wait_for_selector(".cell img.loaded", timeout=20000)
    page.wait_for_timeout(1200)
    page.screenshot(path=f"{OUT}/1_grid.png")
    n = page.evaluate("window.kurator.items.ids.length")
    dom = page.evaluate("document.querySelectorAll('.cell').length")
    print("items:", n, "cells in DOM:", dom)

    cells = page.locator(".cell:visible")
    cells.nth(0).click()
    print("click ->", sel_count(page))
    cells.nth(3).click(modifiers=["Control"])
    cells.nth(5).click(modifiers=["Control"])
    print("ctrl-clicks ->", sel_count(page))
    cells.nth(12).click(modifiers=["Shift"])
    print("shift range ->", sel_count(page))

    # Marquee on the next rows (additive with Ctrl)
    box = page.locator("#grid").bounding_box()
    page.keyboard.down("Control")
    page.mouse.move(box["x"] + 30, box["y"] + 600)
    page.mouse.down()
    page.mouse.move(box["x"] + 500, box["y"] + 760, steps=8)
    page.mouse.up()
    page.keyboard.up("Control")
    after_marquee = sel_count(page)
    print("ctrl+marquee ->", after_marquee)
    page.screenshot(path=f"{OUT}/2_selection.png")

    # Browse with arrow keys + viewer: selection must stay intact
    page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowDown")
    print("after arrows (multi-select kept) ->", sel_count(page))
    page.keyboard.press("Enter")
    page.wait_for_selector(".v-img.ready", timeout=10000)
    for _ in range(4):
        page.keyboard.press("ArrowRight")
    page.wait_for_timeout(500)
    page.keyboard.press("x")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/3_viewer.png")
    page.keyboard.press("Escape")
    print("after viewer + X ->", sel_count(page), "(expected", after_marquee + 1, ")")

    # Filter: selection survives, hidden count shown
    page.fill("#search", "icon")
    page.wait_for_timeout(800)
    print("status with filter:", page.inner_text(".st-sel"), "| visible items:", page.evaluate("window.kurator.items.ids.length"))
    page.screenshot(path=f"{OUT}/4_filtered.png")
    page.fill("#search", "")
    page.wait_for_timeout(800)
    page.keyboard.press("Escape")  # leave the search box

    # Undo
    page.keyboard.press("Control+z")
    print("after undo ->", sel_count(page))
    page.keyboard.press("Escape")
    print("after Esc (clear) ->", sel_count(page))
    page.keyboard.press("Control+z")
    print("after undo of clear ->", sel_count(page))

    # Scroll performance: jump through the whole list, measure frame times
    stats = page.evaluate("""async () => {
        const g = document.querySelector('#grid');
        const times = [];
        let last = performance.now();
        const step = g.clientHeight * 0.9;
        while (g.scrollTop + g.clientHeight < g.scrollHeight - 2) {
            g.scrollTop += step;
            await new Promise(r => requestAnimationFrame(r));
            const now = performance.now(); times.push(now - last); last = now;
        }
        times.sort((a,b)=>a-b);
        return {frames: times.length, median: times[times.length>>1].toFixed(1), p95: times[Math.floor(times.length*0.95)].toFixed(1),
                cells: document.querySelectorAll('.cell').length};
    }""")
    print("scroll frames:", stats)
    page.wait_for_timeout(1500)
    page.screenshot(path=f"{OUT}/5_bottom.png")

    # Panel for an AI-generated PNG
    page.fill("#search", "-")
    page.wait_for_timeout(600)
    page.evaluate("""() => { const k = window.kurator; const i = k.items.n.findIndex(n => /^\\d{5}-\\d+\\.png$/.test(n)); k.setFocus(i); }""")
    page.wait_for_timeout(800)
    page.screenshot(path=f"{OUT}/6_ai_panel.png")
    page.keyboard.press("?")
    page.wait_for_timeout(200)
    page.screenshot(path=f"{OUT}/7_help.png")
    print("console errors:", errors or "none")
    b.close()

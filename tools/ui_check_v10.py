"""Browser test for v0.10: similar images, version stacks, compare, join and contact sheet.

    python tools/ui_check_v10.py PORT OUT_DIR SAVE_DIR
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
    page.wait_for_timeout(1200)
    n = lambda: page.evaluate(f"{K}.items.ids.length")  # noqa: E731
    idx = lambda name: page.evaluate(f"{K}.items.n.indexOf('{name}')")  # noqa: E731
    root = page.evaluate(f"{K}.state.roots[0].path")
    print("items:", n())

    # --- similar images
    page.locator(".node", has_text="Similar images").click(); page.wait_for_timeout(1500)
    print("similar:", n(), page.evaluate(f"{K}.items.n"), "| groups:", page.evaluate(f"{K}.items.groups"))
    print("pathbar:", page.inner_text("#pathbar").replace("\n", " ")[:120])
    page.screenshot(path=f"{OUT}/1_similar.png")
    page.select_option("#pathbar select", "12"); page.wait_for_timeout(800)
    print("looser:", n())
    page.click("#pathbar button:text-is('Clear filters')"); page.wait_for_timeout(700)

    # --- stacks by name
    page.evaluate(f"{K}.openPath('{root}/versions')"); page.wait_for_timeout(900)
    before = n()
    page.keyboard.press("Control+a"); page.wait_for_timeout(200)
    page.click("#panel button:text-is('Stack by name')"); page.wait_for_timeout(1200)
    print("stacked by name:", before, "->", n(), "| badges:", page.locator(".cell.stacked .stackbadge").all_inner_texts())
    page.screenshot(path=f"{OUT}/2_stacks.png")
    page.keyboard.press("Escape")
    hero = idx("hero_color.png")
    page.locator(f".cell[data-i='{hero}']").click(); page.wait_for_timeout(800)
    print("panel versions:", page.locator("#panel .rel").first.inner_text().replace("\n", " "))
    page.locator(f".cell[data-i='{hero}'] .stackbadge").click(); page.wait_for_timeout(900)
    print("stack view:", page.evaluate(f"{K}.items.n"), "|", page.inner_text("#pathbar").replace("\n", " ")[:80])
    page.keyboard.press("Control+a"); page.keyboard.press("Control+Shift+g"); page.wait_for_timeout(900)
    page.click("#pathbar button:text-is('Clear filters')"); page.wait_for_timeout(600)
    page.evaluate(f"{K}.openPath('{root}/versions')"); page.wait_for_timeout(900)
    print("after unstack:", n())

    # --- compare
    page.locator(".cell:visible").nth(0).click(); page.locator(".cell:visible").nth(1).click(modifiers=["Control"])
    page.keyboard.press("c"); page.wait_for_timeout(1200)
    print("compare panes:", page.locator(".cmp-pane").count())
    pane = page.locator(".cmp-pane").first.bounding_box()
    page.mouse.move(pane["x"] + pane["width"] / 2, pane["y"] + pane["height"] / 2)
    page.mouse.wheel(0, -500); page.wait_for_timeout(300)
    t = page.evaluate("[...document.querySelectorAll('.cmp-img')].map(i => i.style.transform)")
    print("zoomed together:", len(set(x.split('scale(')[1] for x in t)) <= 2, t[:1])
    page.screenshot(path=f"{OUT}/3_compare.png")
    page.keyboard.press("s"); page.wait_for_timeout(500)
    page.mouse.move(pane["x"] + pane["width"] * 0.7, pane["y"] + 200); page.wait_for_timeout(200)
    print("swipe:", page.locator(".cmp-pane").count(), page.locator(".cmp-divider").count())
    page.screenshot(path=f"{OUT}/4_swipe.png")
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    print("closed:", page.evaluate("!document.querySelector('.compare').classList.contains('open')"))

    # --- join and contact sheet
    page.keyboard.press("Control+a"); page.keyboard.press("j"); page.wait_for_timeout(1800)
    print("join info:", page.inner_text(".sheetdlg .muted.small"))
    page.select_option(".sheetopts select >> nth=0", "grid"); page.wait_for_timeout(1200)
    page.fill(".sheetcol > .sf:has-text('Save into') input", SAVE)
    page.fill(".sheetcol > .sf:has-text('Name') input", "joined")
    page.screenshot(path=f"{OUT}/5_join.png")
    page.click(".dlg .btn.primary"); page.wait_for_timeout(1500)
    print("join saved:", page.inner_text(".sheetcol .ok"))
    page.click(".segs .seg:text-is('Contact sheet')"); page.wait_for_timeout(1800)
    print("sheet info:", page.inner_text(".sheetdlg .muted.small"))
    page.screenshot(path=f"{OUT}/6_sheet.png")
    page.click(".dlg .btn.primary"); page.wait_for_timeout(1500)
    print("sheet saved:", page.inner_text(".sheetcol .ok"))
    print("files:", sorted(os.listdir(SAVE)))
    print("console errors:", errors[:10])
    b.close()

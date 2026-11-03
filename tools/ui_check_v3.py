"""Browser test: live folder updates, move rules, sidecars, settings (needs a running Kurator).

    python tools/ui_check_v3.py PORT OUT_DIR LIBRARY
"""

import os
import sys
import time

from PIL import Image
from playwright.sync_api import sync_playwright

PORT, OUT, LIB = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 950})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=20000)
    page.wait_for_timeout(800)

    # live update: a new file appears without Rescan
    n0 = page.evaluate("window.kurator.items.ids.length")
    newfile = os.path.join(LIB, "wip", f"live_{int(time.time())}.png")
    Image.new("RGB", (64, 64), "orange").save(newfile)
    t0 = time.time()
    while page.evaluate("window.kurator.items.ids.length") == n0 and time.time() - t0 < 15:
        page.wait_for_timeout(250)
    print(f"live update: {n0} -> {page.evaluate('window.kurator.items.ids.length')} after {time.time() - t0:.1f}s")
    os.remove(newfile)

    # tag 4 items: 2 Creatures, 2 Icons
    page.evaluate("""async () => {
      const k = window.kurator;
      for (const n of ['Creatures', 'Icons']) await fetch('/api/tags', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:n})});
      await k.tags.refresh();
    }""")
    page.wait_for_timeout(300)
    cells = page.locator(".cell:visible")
    cells.nth(1).click(); cells.nth(2).click(modifiers=["Control"])
    cre = page.evaluate("window.kurator.tags.list.find(t => t.name === 'Creatures').hotkey")
    ico = page.evaluate("window.kurator.tags.list.find(t => t.name === 'Icons').hotkey")
    page.keyboard.press(str(cre)); page.wait_for_timeout(400)
    cells.nth(3).click(); cells.nth(4).click(modifiers=["Control"])
    page.keyboard.press(str(ico)); page.wait_for_timeout(400)
    cells.nth(1).click(); cells.nth(4).click(modifiers=["Shift"])
    print("selected for rules:", page.evaluate("window.kurator.selected.size"))

    # rules dialog
    page.keyboard.press("Shift+M"); page.wait_for_timeout(400)
    for name, dest in (("Creatures", os.path.join(LIB, "sorted", "{tag}", "{year}")), ("Icons", os.path.join(LIB, "sorted", "{tag}"))):
        page.click(".dlg button:text-is('+ Add rule')")
        row = page.locator(".rule").last
        tid = page.evaluate(f"String(window.kurator.tags.list.find(t => t.name === '{name}').id)")
        row.locator("select").select_option(tid)
        row.locator("input[type=text]").fill(dest)
    page.click(".dlg button:text-is('Save & preview')"); page.wait_for_timeout(800)
    print("rules preview:", page.inner_text(".move-sum").replace("\n", " | "))
    page.screenshot(path=f"{OUT}/1_rules.png")
    page.click(".dlg .btn.primary"); page.wait_for_timeout(1200)
    moved = sum(len(fs) for _, _, fs in os.walk(os.path.join(LIB, "sorted")))
    print("files under sorted/:", moved)
    page.keyboard.press("Control+z"); page.wait_for_timeout(1500)
    print("after undo, files under sorted/:", sum(len(fs) for _, _, fs in os.walk(os.path.join(LIB, "sorted"))))

    # settings: write sidecars for the selection, import keywords back
    page.click("#toolbar button:text-is('⚙')"); page.wait_for_timeout(400)
    page.click(".dlg button:text-is('Write sidecars now')"); page.wait_for_timeout(800)
    print("sidecars:", page.inner_text(".dlg .move-sum").replace("\n", " | "))
    page.screenshot(path=f"{OUT}/2_settings.png")
    page.click(".dlg button:text-is('Import keywords')"); page.wait_for_timeout(1000)
    print("import:", page.inner_text(".dlg .move-sum").replace("\n", " | "))
    page.click(".dlg button:text-is('Close')")
    page.keyboard.press("?"); page.wait_for_timeout(300)
    print("about:", page.inner_text(".about"), "|", page.inner_text(".about-links"))
    page.screenshot(path=f"{OUT}/3_help.png")
    print("console errors:", errors or "none")
    b.close()

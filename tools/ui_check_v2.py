"""Browser test of tagging, moving, browsing, pins and animation (needs a running Kurator).

    python tools/ui_check_v2.py PORT OUT_DIR LIBRARY OUTSIDE_FOLDER
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, LIB, OUTSIDE = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
os.makedirs(OUT, exist_ok=True)
URL = f"http://127.0.0.1:{PORT}/"


def ev(page, js):
    return page.evaluate(js)


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 950})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.wait_for_selector(".cell img.loaded", timeout=20000)
    page.wait_for_timeout(800)

    # 1. create a tag via the UI
    page.click(".side-sec:nth-child(3) .mini")
    page.fill(".dlg input[type=text]", "Creatures")
    page.click(".dlg .btn.primary")
    page.wait_for_timeout(600)
    for name in ["Items", "Icons", "Characters"]:
        page.keyboard.press("t")  # needs a target -> focus first
        page.keyboard.press("Escape")
    ev(page, "Promise.all(['Items','Icons','Characters'].map(n => fetch('/api/tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})})))")
    ev(page, "window.kurator.tags.refresh()")
    page.wait_for_timeout(500)
    print("tags:", ev(page, "window.kurator.tags.list.map(t => `${t.hotkey}:${t.name}`)"))

    # 2. select 3, press 1
    cells = page.locator(".cell:visible")
    cells.nth(0).click(); cells.nth(1).click(modifiers=["Control"]); cells.nth(2).click(modifiers=["Control"])
    page.keyboard.press("1")
    page.wait_for_timeout(700)
    print("tagged dots on first cell:", page.locator(".cell:visible").nth(0).locator(".dots span").count())
    print("tag bar:", page.inner_text("#tagbar").replace("\n", " | "))
    page.screenshot(path=f"{OUT}/1_tagged.png")

    # 3. undo / redo the tag
    page.keyboard.press("Control+z"); page.wait_for_timeout(500)
    print("after undo dots:", page.locator(".cell:visible").nth(0).locator(".dots span").count())
    page.keyboard.press("Control+y"); page.wait_for_timeout(500)
    print("after redo dots:", page.locator(".cell:visible").nth(0).locator(".dots span").count())

    # 4. quick tag with a new hierarchical tag
    page.keyboard.press("t")
    page.fill(".dlg input[type=text]", "Items/Weapons")
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    print("tags now:", ev(page, "window.kurator.tags.list.map(t => `${t.name}(${t.count})`)"))

    # 5. Untagged view, tag items there -> dimmed
    page.click("text=Untagged")
    page.wait_for_timeout(800)
    n_untagged = ev(page, "window.kurator.items.ids.length")
    page.locator(".cell:visible").nth(0).click()
    page.keyboard.press("3")
    page.wait_for_timeout(600)
    print("untagged view:", n_untagged, "| dimmed after tagging:", page.locator(".cell.dim").count())
    page.screenshot(path=f"{OUT}/2_untagged.png")
    page.keyboard.press("r"); page.wait_for_timeout(800)
    print("after refresh:", ev(page, "window.kurator.items.ids.length"))
    page.click("#pathbar >> text=Clear filters"); page.wait_for_timeout(800)

    # 6. tag filter by parent includes child
    ev(page, "(() => { const k = window.kurator; k.setTagFilter(k.tags.list.find(t => t.name === 'Items').id); })()")
    page.wait_for_timeout(800)
    print("filter Items (incl. Items/Weapons):", ev(page, "window.kurator.items.ids.length"))
    page.click("#pathbar >> text=Clear filters"); page.wait_for_timeout(800)

    # 7. move two files with preview, then undo
    page.keyboard.press("Escape")
    cells = page.locator(".cell:visible")
    cells.nth(4).click(); cells.nth(5).click(modifiers=["Control"])
    names = ev(page, "[...window.kurator.selected].map(id => window.kurator.items.n[window.kurator.indexOf(id)])")
    page.keyboard.press("m")
    dest = os.path.join(LIB, "sorted", "creatures")
    page.fill(".dlg input[type=text]", dest)
    page.click(".dlg button:text-is(\"Preview\")"); page.wait_for_timeout(800)
    print("move preview:", page.inner_text(".move-sum").replace("\n", " | "))
    page.screenshot(path=f"{OUT}/3_move_preview.png")
    page.click(".dlg .btn.primary"); page.wait_for_timeout(1200)
    moved_ok = all(os.path.exists(os.path.join(dest, n)) for n in names)
    print("moved on disk:", moved_ok, names)
    page.keyboard.press("Control+z"); page.wait_for_timeout(1500)
    print("undo -> files back in dest?", any(os.path.exists(os.path.join(dest, n)) for n in names))

    # 8. browse outside folder + pin
    page.fill(".side-sec:nth-child(4) input", OUTSIDE)
    page.keyboard.press("Enter"); page.wait_for_timeout(1500)
    print("browse outside:", ev(page, "window.kurator.items.ids.length"), "| pathbar:", page.inner_text("#pathbar").replace("\n", " "))
    if page.locator("#pathbar button:text-is('☆ Pin')").count():
        page.click("#pathbar button:text-is('☆ Pin')"); page.wait_for_timeout(500)
    print("pinned:", page.inner_text(".side-sec:nth-child(1) .sec-body").replace("\n", " "))
    page.wait_for_selector(".cell img.loaded", timeout=10000)
    page.screenshot(path=f"{OUT}/4_browse.png")

    # 9. animation: back to library, find animated item, hover + viewer
    page.click("text=All library items"); page.wait_for_timeout(1000)
    page.fill("#search", "bouncing"); page.wait_for_timeout(1000)
    page.locator(".cell:visible").nth(0).hover(); page.wait_for_timeout(700)
    print("hover src:", ev(page, "document.querySelector('.cell:not([style*=\"display: none\"]) img').getAttribute('src')"))
    page.locator(".cell:visible").nth(1).dblclick(); page.wait_for_timeout(800)
    print("viewer src:", ev(page, "document.querySelector('.v-img').getAttribute('src')"))
    page.keyboard.press("Escape")

    page.keyboard.press("?"); page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/5_help.png")
    page.keyboard.press("Escape")
    print("console errors:", errors or "none")
    b.close()

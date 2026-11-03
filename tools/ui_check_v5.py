"""Browser test: tag groups (projects, status) and a cloud backup target (needs a running Kurator
with rclone available; the 'gdrive' remote may be simulated with an rclone 'local' remote).

    python tools/ui_check_v5.py PORT OUT_DIR CLOUD_FOLDER
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, CLOUD = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=20000)
    page.wait_for_timeout(800)

    # create a project and two status tags through the UI (+ in the group header)
    def new_tag(group, name):
        page.locator(".facet-head", has_text=group).locator("button").click()
        page.fill(".dlg label:has-text('Name') input", name)
        page.click(".dlg .btn.primary"); page.wait_for_timeout(500)
    new_tag("Projects", "Game A")
    new_tag("Status", "WIP")
    new_tag("Status", "final")
    print("groups:", page.locator(".facet-head").all_inner_texts())

    cells = page.locator(".cell:visible")
    cells.nth(0).click(); cells.nth(1).click(modifiers=["Control"]); cells.nth(2).click(modifiers=["Control"])
    hk = lambda n: page.evaluate(f"window.kurator.tags.list.find(t => t.name === '{n}').hotkey")
    page.keyboard.press(str(hk("Game A"))); page.wait_for_timeout(400)
    page.keyboard.press(str(hk("WIP"))); page.wait_for_timeout(400)
    cells.nth(0).click()
    page.keyboard.press(str(hk("final"))); page.wait_for_timeout(600)
    print("status of first image (WIP replaced by final):",
          page.evaluate("(() => { const k = window.kurator; return (k.items.t[k.focusIndex] || []).map(t => k.tags.byId.get(t).name); })()"))
    print("tag bar:", page.inner_text("#tagbar").replace("\n", " "))
    page.keyboard.press("Control+z"); page.wait_for_timeout(600)
    print("after undo:", page.evaluate("(() => { const k = window.kurator; return (k.items.t[k.focusIndex] || []).map(t => k.tags.byId.get(t).name); })()"))
    page.screenshot(path=f"{OUT}/1_groups.png")

    # filter: project Game A, then "No project"
    page.locator(".node.tagnode", has_text="Game A").click(); page.wait_for_timeout(700)
    print("Game A items:", page.evaluate("window.kurator.items.ids.length"))
    page.locator(".node", has_text="No project").first.click(); page.wait_for_timeout(700)
    print("No project items:", page.evaluate("window.kurator.items.ids.length"), "| pathbar:", page.inner_text("#pathbar").replace("\n", " "))
    page.click("#pathbar button:text-is('Clear filters')"); page.wait_for_timeout(600)

    # cloud backup set
    page.keyboard.press("b"); page.wait_for_timeout(800)
    page.click(".dlg button:text-is('+ New backup set')"); page.wait_for_timeout(1200)
    print("cloud box:", page.inner_text(".dlg .cloudbox").split("\n")[0])
    page.fill(".dlg label:has-text('Name') input", "Google Drive")
    row = page.locator(".dlg .trow").first
    row.locator("select").first.select_option("rclone"); page.wait_for_timeout(200)
    row = page.locator(".dlg .trow").first
    row.locator(".cloudloc select").select_option("gdrive")
    row.locator(".cloudloc input").fill(CLOUD)
    row.locator("input[type=number]").fill("0")
    page.locator(".dlg input[name=layout]").nth(1).check()
    page.fill(".dlg label.chk:has-text('Tag folders') input[type=text]", "{project}/{tagpath}")
    page.screenshot(path=f"{OUT}/2_cloud_editor.png")
    page.click(".dlg button:text-is('Create backup set')"); page.wait_for_timeout(1500)
    if page.locator(".dlg .err").count():
        print("SAVE ERROR:", page.inner_text(".dlg .err"))
    page.locator(".bset", has_text="Google Drive").locator("button:text-is('Back up now')").click()
    page.wait_for_selector(".dlg .move-sum", timeout=60000)
    print("cloud plan:", page.inner_text(".dlg .move-sum").replace("\n", " | "))
    page.click(".dlg button:has-text('Start backup')")
    page.wait_for_selector(".dlg h3:text-is('Finished')", timeout=300000)
    print("cloud result:", page.inner_text(".dlg .cur"))
    print("cloud folders:", sorted(os.listdir(CLOUD))[:10])
    page.screenshot(path=f"{OUT}/3_cloud_done.png")
    print("console errors:", errors or "none")
    b.close()

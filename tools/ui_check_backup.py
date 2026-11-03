"""Browser test: create a backup set with two targets (tag layout), plan, run, restore check.

    python tools/ui_check_backup.py PORT OUT_DIR TARGET1 TARGET2
"""

import os
import shutil
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, T1, T2 = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
os.makedirs(OUT, exist_ok=True)
free_gb = shutil.disk_usage(os.path.dirname(T1)).free / 1024 ** 3
reserve1 = round(free_gb - 0.03, 3)  # leave ~30 MB budget on target 1 -> the rest spills to target 2

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=20000)
    page.click("#toolbar button:text-is('Backup')"); page.wait_for_timeout(600)
    print("coverage:", page.inner_text(".dlg .cov") if page.locator(".dlg .cov").count() else "-")
    page.click(".dlg button:text-is('+ New backup set')"); page.wait_for_timeout(300)
    page.fill(".dlg label:has-text('Name') input", "Two disks by tag")
    page.fill(".dlg .trow input[type=text]", T1)
    page.fill(".dlg .trow input[type=number]", str(reserve1))
    page.click(".dlg button:text-is('+ Add target')")
    page.locator(".dlg .trow").nth(1).locator("input[type=text]").fill(T2)
    page.locator(".dlg .trow").nth(1).locator("input[type=number]").fill("0")
    page.locator(".dlg input[name=layout]").nth(1).check()
    page.screenshot(path=f"{OUT}/1_editor.png")
    page.click(".dlg button:text-is('Create backup set')"); page.wait_for_timeout(800)
    page.click(".dlg button:text-is('Back up now')"); page.wait_for_timeout(2500)
    print("plan:", page.inner_text(".dlg .move-sum").replace("\n", " | "))
    print("space table:", page.inner_text(".dlg table.space").replace("\n", " | ").replace("\t", " "))
    page.screenshot(path=f"{OUT}/2_plan.png")
    page.click(".dlg button:has-text('Start backup')")
    page.wait_for_selector(".dlg h3:text-is('Finished')", timeout=180000)
    print("result:", page.inner_text(".dlg .cur"))
    page.screenshot(path=f"{OUT}/3_done.png")
    page.click(".dlg button:text-is('Back to backups')"); page.wait_for_timeout(1500)
    print("list:", page.inner_text(".dlg .bset").replace("\n", " | "))
    print("coverage:", page.inner_text(".dlg .cov"))
    page.screenshot(path=f"{OUT}/4_list.png")
    page.click(".dlg button:text-is('Restore…')"); page.wait_for_timeout(300)
    page.click(".dlg button:text-is('Check')"); page.wait_for_timeout(1000)
    print("restore check:", page.inner_text(".dlg .move-sum").replace("\n", " | "))
    print("target folders:", sorted(os.listdir(T1))[:8], sorted(os.listdir(T2))[:8])
    print("console errors:", errors or "none")
    b.close()

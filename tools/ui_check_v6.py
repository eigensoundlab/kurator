"""Browser test for v0.6: type filter, new formats, audio player, AI dialog / pools / review, AI search and similar.

Needs a running Kurator with the AI extra; for an offline run set the model to custom ViT-B-32 (random weights).

    python tools/ui_check_v6.py PORT OUT_DIR
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT = int(sys.argv[1]), sys.argv[2]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=30000)
    page.wait_for_timeout(1500)
    total = page.evaluate(f"{K}.items.ids.length")
    print("items:", total)

    # --- type filter
    page.click(".typefilter > button"); page.wait_for_timeout(300)
    print("groups:", page.locator(".tp-head").all_inner_texts())
    page.screenshot(path=f"{OUT}/1_typefilter.png")
    page.click(".tp-presets button:text-is('Vector')"); page.wait_for_timeout(900)
    print("vector:", page.evaluate(f"{K}.items.ids.length"), "button:", page.inner_text(".typefilter > button"))
    page.screenshot(path=f"{OUT}/2_vector.png")
    page.click(".tp-presets button:text-is('Audio')"); page.wait_for_timeout(900)
    print("audio:", page.evaluate(f"{K}.items.ids.length"))
    page.mouse.click(1100, 900); page.wait_for_timeout(200)  # close popover
    page.locator(".cell:visible").nth(0).click(); page.wait_for_timeout(700)
    page.screenshot(path=f"{OUT}/3_audio.png")
    page.keyboard.press("Space"); page.wait_for_timeout(1200)
    print("playing:", page.evaluate(f"{K}.playingId != null"), "t=", round(page.evaluate(f"{K}.audio.currentTime"), 2))
    page.keyboard.press("Space"); page.wait_for_timeout(300)
    print("stopped:", page.evaluate(f"{K}.playingId == null"))
    page.click(".typefilter > button"); page.wait_for_timeout(200)
    page.click(".tp-presets button:text-is('Work files')"); page.wait_for_timeout(900)
    print("work files:", page.evaluate(f"{K}.items.ids.length"))
    page.screenshot(path=f"{OUT}/4_work.png")
    page.click(".tp-presets button:text-is('All files')"); page.wait_for_timeout(900)
    page.mouse.click(1100, 900); page.wait_for_timeout(200)
    print("all again:", page.evaluate(f"{K}.items.ids.length"))

    # --- tags for a pool
    def new_tag(name):
        page.locator(".facet-head", has_text="Subjects").locator("button").click()
        page.fill(".dlg label:has-text('Name') input", name)
        page.click(".dlg .btn.primary"); page.wait_for_timeout(400)
    for n in ("creature", "icon", "character"):
        new_tag(n)

    # --- AI dialog: prepare images
    page.click("button:text-is('AI')"); page.wait_for_timeout(1200)
    page.screenshot(path=f"{OUT}/5_ai.png")
    print("ai head:", page.inner_text(".dlg .ai-head").replace("\n", " | ")[:200])
    page.click(".dlg button:text-is('Prepare images')")
    page.wait_for_selector(".dlg .ok", timeout=240000)
    print("prepare:", page.inner_text(".dlg .ok"))
    page.click(".dlg button:text-is('Back')"); page.wait_for_timeout(800)

    # --- create pool
    page.click(".dlg button:text-is('+ New tag pool')"); page.wait_for_timeout(600)
    page.fill(".dlg label:has-text('Name') input", "Art type")
    for n in ("creature", "icon", "character"):
        page.click(f".dlg .chip:text-is('{n}')")
    page.fill(".dlg label:has-text('Suggest when score') input", "0")  # random test weights give flat scores
    page.screenshot(path=f"{OUT}/6_pool.png")
    page.click(".dlg button:text-is('Create pool')"); page.wait_for_timeout(900)
    page.locator(".dlg .bset", has_text="Art type").locator("select").select_option("view")
    page.locator(".dlg .bset", has_text="Art type").locator("button:text-is('Suggest tags')").click()
    page.wait_for_selector(".dlg .ok", timeout=120000)
    print("run:", page.inner_text(".dlg .ok"))
    page.click(".dlg button:text-is('Back')"); page.wait_for_timeout(800)
    page.screenshot(path=f"{OUT}/7_ai_after.png")
    page.click(".dlg button:text-is('Review them')"); page.wait_for_timeout(1200)
    sugg = page.evaluate(f"{K}.items.ids.length")
    print("review view:", sugg, "| pathbar:", page.inner_text("#pathbar").replace("\n", " "))
    print("hollow dots:", page.locator(".cell .sug").count())
    page.locator(".cell:visible").nth(0).click(); page.wait_for_timeout(700)
    page.screenshot(path=f"{OUT}/8_review.png")
    print("panel suggestions:", page.locator(".p-tags .chip.sugg").count(), "| panel text has null:", "null" in page.inner_text("#panel"))
    page.keyboard.press("a"); page.wait_for_timeout(1000)
    print("after accept:", page.evaluate(f"{K}.items.ids.length"), "toast:", page.locator(".toast").all_inner_texts()[-1:])
    page.locator(".cell:visible").nth(0).click(); page.wait_for_timeout(400)
    page.keyboard.press("Shift+A"); page.wait_for_timeout(1000)
    print("after reject:", page.evaluate(f"{K}.items.ids.length"))
    page.click("#pathbar button:text-is('Clear filters')"); page.wait_for_timeout(800)

    # --- AI search and similar
    page.fill("#search", "?a drawing of a dragon"); page.keyboard.press("Enter"); page.wait_for_timeout(2500)
    print("ai search:", page.evaluate(f"{K}.items.ids.length"), "|", page.inner_text("#pathbar").replace("\n", " ")[:120])
    page.screenshot(path=f"{OUT}/9_search.png")
    page.click("#pathbar button:text-is('Clear filters')"); page.wait_for_timeout(800)
    page.fill("#search", ""); page.keyboard.press("Enter"); page.wait_for_timeout(600)
    page.locator(".cell:visible").nth(12).click(); page.wait_for_timeout(300)
    first = page.evaluate(f"{K}.items.ids[{K}.focusIndex]")
    page.keyboard.press("s"); page.wait_for_timeout(2000)
    print("similar first is query:", page.evaluate(f"{K}.items.ids[0]") == first, "|", page.inner_text("#pathbar").replace("\n", " ")[:120])
    page.screenshot(path=f"{OUT}/10_similar.png")

    print("console errors:", errors[:10])
    b.close()

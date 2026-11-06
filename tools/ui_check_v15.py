"""Browser test for v0.15: Basis/HDR thumbnails, PDF pages in the viewer, source & licence in the
panel, the AI dialog (vision model server, dataset box), the dataset export dialog, the backup
schedule and reminder. Library from tools/make_v15_samples.py.

    python tools/ui_check_v15.py PORT OUT_DIR DATASET_DIR
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT, DS = int(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
    page = b.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell img.loaded", timeout=30000)
    root = page.evaluate(f"{K}.state.roots[0].path")

    def cell(name):
        i = page.evaluate(f"{K}.items.n").index(name)
        return page.locator(f".cell[data-i='{i}']:visible")

    # textures: Basis Universal KTX2 and HDR pictures
    page.evaluate(f"{K}.openPath('{root}/textures')"); page.wait_for_timeout(1500)
    page.screenshot(path=f"{OUT}/1_basis_grid.png")
    print("basis:", page.evaluate(f"Promise.all({K}.items.ids.map(i => fetch('/api/items/' + i).then(r => r.json()).then(d => [d.name, d.meta.texture, d.meta.preview_note || 'ok'])))"))
    cell("uastc_photo_l4.ktx2").dblclick(); page.wait_for_timeout(1200)
    page.screenshot(path=f"{OUT}/2_basis_viewer.png")
    page.keyboard.press("Escape"); page.wait_for_timeout(300)
    page.evaluate(f"{K}.openPath('{root}/hdr')"); page.wait_for_timeout(1500)
    cell("sky.exr").click(); page.wait_for_timeout(1000)
    page.screenshot(path=f"{OUT}/3_hdr.png")
    print("hdr:", page.evaluate(f"Promise.all({K}.items.ids.map(i => fetch('/api/items/' + i).then(r => r.json()).then(d => [d.name, d.meta.format, d.meta.compression || '', d.meta.brightest])))"))

    # PDF pages
    page.evaluate(f"{K}.openPath('{root}/docs')"); page.wait_for_timeout(1500)
    cell("brochure.pdf").dblclick(); page.wait_for_timeout(1500)
    print("pages bar:", page.locator(".v-pages").is_visible(), page.input_value(".v-page-in"), page.inner_text(".v-page-total"))
    page.keyboard.press("]"); page.wait_for_timeout(1000)
    page.keyboard.press("]"); page.wait_for_timeout(1200)
    print("page 3:", page.input_value(".v-page-in"), page.evaluate("document.querySelector('.v-img').getAttribute('src')"),
          page.evaluate("[document.querySelector('.v-img').style.width, document.querySelector('.v-img').style.height]"))
    page.screenshot(path=f"{OUT}/4_pdf_page3.png")
    for _ in range(6):
        page.keyboard.press("+")
    page.wait_for_timeout(1500)
    print("sharpened:", page.evaluate("document.querySelector('.v-img').getAttribute('src')"))
    page.fill(".v-page-in", "4"); page.press(".v-page-in", "Enter"); page.wait_for_timeout(1000)
    print("typed page:", page.input_value(".v-page-in"))
    page.keyboard.press("Escape"); page.wait_for_timeout(300)

    # source & licence in the panel; search
    page.evaluate(f"{K}.openPath('{root}/refs')"); page.wait_for_timeout(1500)
    cell("castle.png").click(); page.wait_for_timeout(1000)
    page.click("#panel summary:text-is('Source & licence')"); page.wait_for_timeout(300)
    inputs = page.locator("#panel .p-info input")
    inputs.nth(0).fill("https://example.com/castle"); inputs.nth(0).press("Enter"); page.wait_for_timeout(400)
    inputs.nth(1).fill("Jane Doe"); inputs.nth(1).press("Enter"); page.wait_for_timeout(400)
    inputs.nth(2).fill("CC BY 4.0"); inputs.nth(2).press("Enter"); page.wait_for_timeout(800)
    print("licence link:", page.locator("#panel .p-info a").get_attribute("href"))
    page.screenshot(path=f"{OUT}/5_panel_licence.png")
    print("search author:", page.evaluate("fetch('/api/items?q=author:jane').then(r => r.json()).then(d => d.n)"))
    print("search licence:none:", page.evaluate("fetch('/api/items?q=licence:none').then(r => r.json()).then(d => d.n.length)"))

    # AI dialog: vision model server and dataset box (the AI add-on may be missing here)
    page.click("button:text-is('AI')"); page.wait_for_timeout(1200)
    page.screenshot(path=f"{OUT}/6_ai_dialog.png")
    page.click(".dlg button:has-text('as a dataset')"); page.wait_for_timeout(800)
    page.fill(".dsdlg input[placeholder='e.g. D:\\\\Training']", DS)
    page.locator(".dsdlg input[placeholder='optional, e.g. mystyle']").fill("refstyle")
    page.click(".dlg button:text-is('Preview')"); page.wait_for_timeout(1200)
    print("dataset preview:", page.inner_text(".dsdlg .move-sum"))
    page.screenshot(path=f"{OUT}/7_dataset.png")
    page.click(".dlg button:text-is('Export')"); page.wait_for_timeout(2500)
    print("dataset done:", page.inner_text(".dsdlg .move-preview"))
    print("files:", sorted(os.listdir(os.path.join(DS, "dataset")))[:14])
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)

    # backup schedule: a set with a reminder shows in the status bar
    r = page.evaluate("fetch('/api/backups', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify("
                      f"{{name: 'Weekly', targets: [{{location: {os.path.join(DS, 'backup')!r}}}], every_days: 7}})}}).then(r => r.status)")
    page.wait_for_timeout(4500)
    print("reminder:", r, page.locator(".st-due").is_visible(), page.inner_text(".st-due"))
    page.click(".st-due"); page.wait_for_timeout(1000)
    page.click(".dlg button:text-is('Edit')"); page.wait_for_timeout(800)
    page.locator(".dlg h3:text-is('When')").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/8_backup_when.png")
    print("errors:", errors)
    b.close()

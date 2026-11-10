"""Browser test of .blend files in the viewer: Materials (what the preview keeps), Render in Blender (EEVEE,
Cycles), back to 3D, saving the render. Needs Blender.

    python tools/ui_check_blend.py PORT OUT_DIR
Kurator runs on PORT with a library holding scene.blend (image texture, procedural, missing texture).
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, OUT = int(sys.argv[1]), sys.argv[2]
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    b = p.chromium.launch(executable_path=exe, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"]) if exe else p.chromium.launch()
    page = b.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell", timeout=30000)

    def wait(cond, ms=120000):
        for _ in range(ms // 250):
            if page.evaluate(cond):
                return
            page.wait_for_timeout(250)
        raise TimeoutError(cond)

    i = page.evaluate(f"{K}.items.n.indexOf('scene.blend')")
    assert i >= 0
    page.locator(f".cell[data-i='{i}']").click()
    page.keyboard.press("Enter")
    wait(f"document.querySelector('.viewer').classList.contains('mesh-ready')")
    assert page.locator(".v-blendbtn", has_text="Materials").is_visible() and page.locator(".v-render").is_visible()
    assert not page.locator(".v-back3d").is_visible()
    page.screenshot(path=f"{OUT}/1-3d.png")

    # Materials
    page.locator(".v-blendbtn", has_text="Materials").click()
    page.wait_for_selector(".bl-report", timeout=60000)
    txt = page.inner_text(".bl-report")
    assert "Rock" in txt and "not in the preview - bake it to a texture" in txt, txt
    assert "the preview shows the plain texture" in txt and "missing file" in txt and "floor_albedo" in txt, txt
    assert "Bevel" in txt and "UVMap" in txt
    page.locator(".dlg").last.screenshot(path=f"{OUT}/2-materials.png")
    page.keyboard.press("Escape")

    # Render (EEVEE): the picture in place of the 3D view; Esc back to 3D
    page.locator(".v-render").click()
    wait(f"document.querySelector('.viewer').classList.contains('blend-render') && document.querySelector('.v-img').naturalWidth > 0")
    assert page.locator(".v-back3d").is_visible() and not page.locator(".meshview").is_visible()
    assert "rendered by Blender (EEVEE)" in page.inner_text(".v-info"), page.inner_text(".v-info")
    print("render:", page.inner_text(".v-info"), page.evaluate("document.querySelector('.v-img').naturalWidth"))
    page.screenshot(path=f"{OUT}/3-render.png")
    # right-click: back, save, engines
    page.mouse.click(700, 450, button="right")
    page.wait_for_selector(".ctxmenu")
    rows = [r.strip() for r in page.locator(".ctxmenu .pm-row .grow").all_inner_texts()]
    assert rows[:4] == ["Back to the 3D view", "Save the render as a picture", "Render in Blender", "Materials…"], rows
    page.locator(".ctxmenu .pm-row", has_text="Save the render as a picture").click()
    wait(f"{K}.toastEl.textContent.startsWith('Saved scene render')", 20000)
    print("saved:", page.evaluate(f"{K}.toastEl.textContent"))
    page.keyboard.press("Escape")
    wait("document.querySelector('.viewer').classList.contains('mesh-ready')", 10000)
    assert page.evaluate(f"{K}.viewer.isOpen") and not page.locator(".v-back3d").is_visible()
    # Cycles from the 3D view's menu (the 3D view asks for its own right-click)
    page.evaluate(f"{K}.viewerMenu(700, 450)")
    page.wait_for_selector(".ctxmenu")
    rows = [r.strip() for r in page.locator(".ctxmenu .pm-row .grow").all_inner_texts()]
    assert "Materials…" in rows and "Render in Blender" in rows, rows
    page.locator(".ctxmenu .pm-row", has_text="Render in Blender").hover()
    page.wait_for_timeout(300)
    page.locator(".ctxmenu").nth(1).locator(".pm-row", has_text="Cycles").click()
    wait(f"document.querySelector('.viewer').classList.contains('blend-render') && document.querySelector('.v-img').naturalWidth > 0", 300000)
    assert "rendered by Blender (Cycles)" in page.inner_text(".v-info")
    print("cycles:", page.inner_text(".v-info"))
    page.screenshot(path=f"{OUT}/4-cycles.png")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    assert not page.evaluate(f"{K}.viewer.isOpen")
    real = [e for e in errors if "Failed to load resource" not in e]
    assert not real, real
    print("OK")
    b.close()

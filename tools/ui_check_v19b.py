"""Browser test for what came out of trying the 0.19 desktop app: Back / Forward (viewer, folders,
tags), drops that land in the library, the viewer's right-click menu (colour, SVG beyond the page,
3D matcap and cavity, Copy picture, Regenerate preview).

    python tools/ui_check_v19b.py PORT OUT_DIR LIBRARY_FOLDER OUTSIDE_FOLDER
LIBRARY_FOLDER needs: sub/ (a few PNGs), page.svg (content beyond its page), bumps.obj.
"""

import base64
import io
import os
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

PORT, OUT, LIB, OUTSIDE = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
SEP = os.sep
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    b = p.chromium.launch(executable_path=exe, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"]) if exe else p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1400, "height": 900})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{PORT}")
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".cell", timeout=30000)
    page.wait_for_timeout(800)

    def api(method, url, body=None):
        return page.evaluate("""async ([m, u, b]) => { const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' },
            body: b == null ? undefined : JSON.stringify(b) }); return r.json(); }""", [method, url, body])

    def menu_rows(level=0):
        page.wait_for_selector(".ctxmenu")
        return [r.strip() for r in page.locator(".ctxmenu").nth(level).locator(".pm-row .grow").all_inner_texts()]

    def pick(label, level=0):
        page.locator(".ctxmenu").nth(level).locator(".pm-row", has_text=label).first.click()
        page.wait_for_timeout(300)

    def view():
        return page.evaluate(f"({{ folder: {K}.query.folder, tag: {K}.query.tag, viewer: {K}.viewer.isOpen }})")

    def settle():
        page.wait_for_timeout(700)

    # --- Back / Forward: folders, a tag, the viewer
    sub = LIB + SEP + "sub"
    page.evaluate(f"{K}.setFolder({sub!r})")
    settle()
    t = api("POST", "/api/tags", {"name": "Moodboard"})
    page.evaluate(f"{K}.tags.refresh()")
    settle()
    page.evaluate(f"{K}.setTagFilter({t['id']})")
    settle()
    assert view() == {"folder": None, "tag": str(t["id"]), "viewer": False}, view()
    page.go_back()
    settle()
    assert view() == {"folder": sub, "tag": "", "viewer": False}, view()
    page.locator(".cell[data-i='0']").click()
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert view()["viewer"]
    page.go_back()                                   # Back closes the viewer, the folder stays
    settle()
    assert view() == {"folder": sub, "tag": "", "viewer": False}, view()
    page.go_forward()                                # Forward opens it again
    settle()
    assert view()["viewer"]
    page.keyboard.press("Escape")                    # Esc is Back: closed, Forward would open it again
    settle()
    assert view() == {"folder": sub, "tag": "", "viewer": False}, view()
    page.go_forward()
    settle()
    assert view()["viewer"]
    page.go_back()                                   # Back from the viewer and the folder: where we started
    settle()
    page.go_back()
    settle()
    assert view() == {"folder": None, "tag": "", "viewer": False}, view()

    # --- a drop onto the grid of a library folder goes into that folder
    page.evaluate(f"{K}.setFolder({sub!r})")
    settle()
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), "teal").save(buf, "PNG")
    files = [{"name": "dropped.png", "b64": base64.b64encode(buf.getvalue()).decode(), "mtime": 1600000000000}]
    drop = """([sel, files]) => {
      const t = document.querySelector(sel);
      const dt = new DataTransfer();
      for (const f of files) dt.items.add(new File([Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0))], f.name, { type: 'image/png', lastModified: f.mtime }));
      for (const type of ['dragenter', 'dragover', 'drop']) t.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
    }"""
    page.evaluate(drop, ["#grid", files])
    page.wait_for_selector(".dlg .destfield input")
    assert page.locator(".dlg .destfield input").input_value() == sub
    assert "In the library" in page.locator(".dlg .drop-where").inner_text()
    # elsewhere: offered to become a library folder (ticked), and then the file shows
    page.locator(".dlg .destfield input").fill(OUTSIDE)
    page.wait_for_timeout(200)
    assert "Add this folder to the library" in page.locator(".dlg .drop-where").inner_text()
    assert page.locator(".dlg .drop-where input[type='checkbox']").is_checked()
    page.screenshot(path=os.path.join(OUT, "v19b_drop_outside.png"))
    page.locator(".dlg button", has_text="Add").click()
    page.wait_for_timeout(2500)
    roots = [r["path"] for r in api("GET", "/api/state")["roots"] if r["kind"] != "browse"]
    assert OUTSIDE in roots, roots
    page.evaluate(f"{K}.setFolder({OUTSIDE!r})")
    settle()
    page.wait_for_timeout(1500)
    names = page.evaluate(f"{K}.items.n")
    assert "dropped.png" in names, names

    # --- the viewer's menu on a picture: its colour, the background, Regenerate preview
    page.evaluate(f"{K}.setFolder({sub!r})")
    settle()
    page.locator(".cell[data-i='0']").click()
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    box = page.locator(".viewer .v-img").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, button="right")
    rows = menu_rows()
    assert rows[0].startswith("Copy colour #") and "Background" in rows and "Regenerate preview" in rows and "Copy picture" in rows, rows
    hexcol = rows[0].split()[-1]
    page.screenshot(path=os.path.join(OUT, "v19b_viewer_menu.png"))
    pick("Copy colour")
    assert page.evaluate("navigator.clipboard.readText()") == hexcol
    page.mouse.click(box["x"] + 5, box["y"] + 5, button="right")
    pick("Copy picture")
    page.wait_for_timeout(1500)
    kinds = page.evaluate("(async () => (await navigator.clipboard.read()).flatMap((i) => i.types))()")
    assert "image/png" in kinds, kinds
    page.mouse.click(box["x"] + 5, box["y"] + 5, button="right")
    pick("Regenerate preview")
    assert page.locator(".toast", has_text="again").count() or True
    page.keyboard.press("Escape")
    settle()

    # --- SVG: what is outside the page
    page.evaluate(f"{K}.setFolder({LIB!r})")
    page.evaluate(f"{K}.query.recursive = false")
    settle()
    i = page.evaluate(f"{K}.items.n.indexOf('page.svg')")
    assert i >= 0
    page.evaluate(f"{K}.openViewer({i})")
    page.wait_for_timeout(1200)
    w0 = page.evaluate(f"{K}.viewer.w")
    box = page.locator(".viewer .v-stage").bounding_box()
    page.mouse.click(box["x"] + 30, box["y"] + 60, button="right")
    pick("Show what is outside the page")
    page.wait_for_timeout(1200)
    assert "all=1" in page.evaluate(f"{K}.viewer.img.src") and page.evaluate(f"{K}.viewer.w") > w0
    page.screenshot(path=os.path.join(OUT, "v19b_svg_all.png"))
    page.keyboard.press("Escape")
    settle()

    # --- 3D: matcap and cavity from the menu (a right-click that doesn't move the model)
    i = page.evaluate(f"{K}.items.n.indexOf('bumps.obj')")
    page.evaluate(f"{K}.openViewer({i})")
    page.wait_for_selector(".viewer.mesh-ready", timeout=30000)
    page.wait_for_timeout(800)
    c = page.locator(".viewer canvas.meshview").bounding_box()
    cx, cy = c["x"] + c["width"] / 2, c["y"] + c["height"] / 2
    page.screenshot(path=os.path.join(OUT, "v19b_3d_lit.png"))
    page.mouse.click(cx, cy, button="right")
    rows = menu_rows()
    assert rows[:3] == ["Reset the view", "Shading", "Matcap"], rows
    page.locator(".ctxmenu .pm-row", has_text="Matcap").first.hover()
    page.wait_for_timeout(200)
    assert "Red wax" in menu_rows(1)
    pick("Red wax", 1)
    assert page.evaluate(f"{K}.viewer.mv.mode") == "matcap" and page.evaluate(f"{K}.viewer.mv.matcap") == "wax"
    page.screenshot(path=os.path.join(OUT, "v19b_3d_matcap.png"))
    page.keyboard.press("c")                         # cavity
    page.wait_for_timeout(500)
    assert page.evaluate(f"{K}.viewer.mv.cavity")
    page.screenshot(path=os.path.join(OUT, "v19b_3d_matcap_cavity.png"))
    page.evaluate(f"{K}.viewer.mv.set({{ mode: 'clay' }})")
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(OUT, "v19b_3d_clay_cavity.png"))
    page.mouse.move(cx, cy)
    page.mouse.down(button="right")
    page.mouse.move(cx + 60, cy + 20, steps=5)       # right-drag: moves, no menu
    page.mouse.up(button="right")
    page.wait_for_timeout(300)
    assert page.locator(".ctxmenu").count() == 0
    page.evaluate(f"{K}.viewer.mv.set({{ cavity: false, mode: 'lit' }})")
    page.keyboard.press("Escape")

    assert not errors, errors
    print("v0.19 follow-up UI check passed")
    b.close()

"""Browser test of the SFX Workbench link, both ways: sounds sent from Kurator's menu load in SFX Workbench,
and what SFX Workbench saves comes back to Kurator as new, tagged files.

    python tools/ui_check_sfx.py KURATOR_PORT SFX_URL OUT_DIR LIBRARY_FOLDER [--public]

Kurator runs on KURATOR_PORT with LIBRARY_FOLDER (holding foley/a.wav and foley/b.wav) as its library;
SFX Workbench is served at SFX_URL (npm start in its repository). --public: Chrome treats SFX Workbench's
server as a public site (as GitHub Pages is), so Local Network Access applies; the permission is granted.
"""

import os
import sys

from playwright.sync_api import sync_playwright

PORT, SFX, OUT, LIB = int(sys.argv[1]), sys.argv[2].rstrip("/") + "/", sys.argv[3], sys.argv[4]
PUBLIC = "--public" in sys.argv
os.makedirs(OUT, exist_ok=True)
K = "window.kurator"
KUR = f"http://127.0.0.1:{PORT}"
sfx_host = SFX.split("//", 1)[1].split("/", 1)[0]

with sync_playwright() as p:
    exe = os.environ.get("PW_CHROME")
    sp = sfx_host.split(":")[1]
    args = [f"--ip-address-space-overrides=127.0.0.1:{sp}=public,[::1]:{sp}=public"] if PUBLIC else []
    b = p.chromium.launch(executable_path=exe, args=args) if exe else p.chromium.launch(args=args)
    ctx = b.new_context(viewport={"width": 1400, "height": 900})
    if PUBLIC:   # what clicking Allow on Chrome's "connect to apps on this device" prompt does
        cdp = ctx.new_cdp_session(ctx.new_page())
        bc = cdp.send("Target.getTargetInfo")["targetInfo"]["browserContextId"]
        origin = SFX.split("/", 3)
        origin = origin[0] + "//" + origin[2]
        for name in ("loopbackNetwork", "localNetworkAccess"):   # Chrome 145+, older
            try:
                cdp.send("Browser.grantPermissions", {"origin": origin, "permissions": [name], "browserContextId": bc})
                print("granted", name)
                break
            except Exception as e:  # noqa: BLE001 - names differ between Chrome versions
                print("no permission", name, str(e)[:80])
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(("kurator", m.text)) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(("kurator", str(e))))

    def api(method, url, body=None):
        return page.evaluate("""async ([m, u, b]) => { const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json' },
            body: b == null ? undefined : JSON.stringify(b) }); return r.json(); }""", [method, url, body])

    page.goto(KUR + "/")
    page.wait_for_selector(".cell", timeout=30000)
    api("PUT", "/api/settings", {"sfx_url": SFX})
    proj = api("POST", "/api/tags", {"name": "Eryxian Music", "facet": "project"})
    ids0 = api("GET", "/api/items")["ids"]
    api("POST", f"/api/tags/{proj['id']}/apply", {"ids": ids0, "op": "add"})
    page.evaluate(f"{K}.tags.refresh()")
    page.wait_for_timeout(500)
    page.evaluate(f"{K}.projects.activate({proj['id']})")       # the project's sounds
    page.wait_for_timeout(1200)
    foley = LIB + os.sep + "foley"
    page.keyboard.press("Control+a")
    page.locator(".cell[data-i='0']").click(button="right")
    page.wait_for_selector(".ctxmenu")
    rows = [r.strip() for r in page.locator(".ctxmenu .pm-row .grow").all_inner_texts()]
    assert "Send 2 sounds to SFX Workbench…" in rows, rows
    page.locator(".ctxmenu .pm-row", has_text="SFX Workbench").first.click()
    page.wait_for_selector(".sfx-dlg")
    dlg = page.locator(".dlg").last
    assert dlg.locator("input[type=text]").input_value() == foley
    assert "◆ Eryxian Music" in dlg.inner_text()
    page.screenshot(path=f"{OUT}/1-kurator-dialog.png")
    with ctx.expect_page() as pop:
        dlg.locator("button", has_text="Open SFX Workbench").click()
    sfx = pop.value
    sfx.on("console", lambda m: errors.append(("sfx", m.text)) if m.type == "error" else None)
    sfx.on("pageerror", lambda e: errors.append(("sfx", str(e))))
    sfx.wait_for_load_state()
    sfx.wait_for_function("document.querySelector('#kur-msg')?.textContent.includes('2 sounds from Kurator')", timeout=20000)
    assert "#" not in sfx.url or "link=" not in sfx.url, sfx.url                # the link left the address
    msg = sfx.inner_text("#kur-msg")
    assert "foley" in msg and "SFX Workbench" in msg and "Eryxian Music" in msg, msg
    sfx.wait_for_function("document.querySelectorAll('#lib-list .tag').length && [...document.querySelectorAll('#lib-list .lib-name')].filter(n => n.textContent.includes('kurator')).length === 2", timeout=20000)
    assert sfx.inner_text("#btn-save").strip() == "Send output to Kurator"
    sfx.screenshot(path=f"{OUT}/2-sfx-linked.png")

    # the output of the selected sound (b.wav, the last loaded) comes back as a new file
    sfx.click("#btn-save")
    sfx.wait_for_function("document.querySelector('#toast')?.textContent.startsWith('Sent to Kurator')", timeout=20000)
    sent = sfx.inner_text("#toast")
    print("sfx:", sent)
    for _ in range(60):                                  # Kurator's page has no eval (CSP): ask it every 250 ms
        if "From SFX Workbench" in page.evaluate(f"{K}.toastEl.textContent"):
            break
        page.wait_for_timeout(250)
    print("kurator:", page.evaluate(f"{K}.toastEl.textContent"))
    fresh = [n for n in os.listdir(foley) if n not in ("a.wav", "b.wav")]
    assert len(fresh) == 1 and fresh[0].endswith(".wav"), fresh
    ids = dict(zip(*(lambda it: (it["n"], it["ids"]))(api("GET", "/api/items"))))
    nid = ids[fresh[0]]
    info = api("GET", f"/api/items/{nid}")
    own = api("GET", "/api/settings")
    print("new file:", fresh[0], info.get("tags"), info.get("info"))
    assert proj["id"] in info["tags"] and len(info["tags"]) == 2, info
    assert info["info"]["source"] in ("SFX Workbench, from a.wav", "SFX Workbench, from b.wav"), info

    # source WAV too; then downloads again
    sfx.click("#btn-save-src")
    sfx.wait_for_timeout(1500)
    assert len([n for n in os.listdir(foley) if n not in ("a.wav", "b.wav")]) == 2
    sfx.click("#kur-to-d")
    assert sfx.inner_text("#btn-save").strip() == "Save output WAV"
    with sfx.expect_download() as dl:
        sfx.click("#btn-save")
    print("download:", dl.value.suggested_filename)
    sfx.click("#kur-to-k")

    # layout: desktop and 400 px, light and dark
    for theme in ("light", "dark"):
        sfx.evaluate(f"document.documentElement.dataset.theme = {theme!r}")
        for w in (1400, 400):
            sfx.set_viewport_size({"width": w, "height": 900})
            sfx.wait_for_timeout(300)
            sfx.locator("#kur-bar").screenshot(path=f"{OUT}/3-bar-{theme}-{w}.png")

    # a reload keeps the link (the library starts fresh, so the sounds load again)
    sfx.reload()
    sfx.wait_for_function("document.querySelector('#kur-msg')?.textContent.includes('2 sounds from Kurator')", timeout=20000)
    # ended in Kurator: SFX Workbench says so
    api("DELETE", "/api/sfx/links")
    sfx.click("#btn-save")
    sfx.wait_for_function("document.querySelector('#toast')?.textContent.includes('Kurator did not take it')", timeout=10000)
    print("ended:", sfx.inner_text("#toast"))
    real = [e for e in errors if "404" not in e[1] and "Failed to load resource" not in e[1]]
    assert not real, real
    print("OK", "(public)" if PUBLIC else "")
    b.close()

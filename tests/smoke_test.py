"""Smoke tests for the builder. Run:  pip install -r tests/requirements.txt && playwright install chromium && python tests/smoke_test.py

Checks, in a real headless Chromium:
  * the builder loads and every control works without console errors
  * every cursor effect and every theme renders a non-blank wallpaper
  * a downloaded wallpaper renders, with and without an image and with photo reveal
  * the Lively export is a valid zip with the two expected files
  * settings round-trip, and hostile settings files are clamped/ignored
  * nothing (builder, preview or generated wallpaper) makes a network request
"""
import io, json, sys, zipfile, base64
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BUILDER = (ROOT / "ascii-wallpaper-builder.html").as_uri()
SAMPLE = ROOT / "tests" / "fixtures" / "sample.png"
failures, requests = [], []

def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond: failures.append(msg)

def watch(page, label, errors):
    page.on("pageerror", lambda e: errors.append(f"{label}: {e}"))
    page.on("console", lambda m: errors.append(f"{label}: {m.text}") if m.type == "error" else None)
    page.on("request", lambda r: requests.append(f"{label}: {r.url[:80]}") if not r.url.startswith(("data:", "blob:", "about:", "file:")) else None)

def nonblank(png_bytes):
    from PIL import Image  # pillow ships with playwright's deps in most setups; fall back to size heuristic
    im = Image.open(io.BytesIO(png_bytes)).convert("L")
    return im.getextrema()[1] > 40

def render(browser, html, errors, label, move=True):
    pg = browser.new_page(viewport={"width": 960, "height": 540})
    watch(pg, label, errors)
    pg.set_content(html); pg.wait_for_timeout(500)
    if move:
        for k in range(10):
            pg.mouse.move(300 + k * 30, 250); pg.wait_for_timeout(35)
        pg.mouse.down(); pg.mouse.up(); pg.wait_for_timeout(200)
    shot = pg.screenshot(); perf = pg.evaluate("window.__perf"); pg.close()
    return shot, perf

def main():
    errors = []
    img = "data:image/png;base64," + base64.b64encode(SAMPLE.read_bytes()).decode()
    with sync_playwright() as pw:
        br = pw.chromium.launch(); ctx = br.new_context(accept_downloads=True)
        host = ctx.new_page(); watch(host, "builder", errors)
        host.goto(BUILDER); host.wait_for_timeout(400)
        check(host.evaluate("typeof buildHtml") == "function", "builder loads")
        check(host.evaluate("$('edges').checked") is False and host.evaluate("$('word').value") == "", "defaults: edge glyphs off, no fallback word")
        build = lambda o: host.evaluate("o=>buildHtml(Object.assign(cfgFromUI(),o))", o)

        for r in ["push", "pull", "lens", "wake", "flashlight"]:
            shot, perf = render(br, build({"image": img, "reaction": r}), errors, f"reaction={r}")
            check(nonblank(shot) and perf["cells"] > 0, f"cursor effect '{r}' renders")
        for t in ["rgb", "photo", "spectrum", "sunset", "lava", "synthwave", "amber", "matrix", "ice", "magenta", "mono"]:
            shot, perf = render(br, build({"image": img, "palette": t}), errors, f"theme={t}")
            check(nonblank(shot) and perf["cells"] > 0, f"theme '{t}' renders")
        shot, perf = render(br, build({"image": img, "photoReveal": True}), errors, "photo-reveal")
        check(nonblank(shot), "photo reveal renders")
        shot, perf = render(br, build({"image": "", "word": "HELLO"}), errors, "word-only")
        check(nonblank(shot), "word-only wallpaper renders")
        shot, perf = render(br, build({"image": ""}), errors, "empty")
        check(nonblank(shot), "wallpaper with no image and no word still animates")

        # preview + file downloads from the real UI
        host.set_input_files("#file", str(SAMPLE)); host.wait_for_timeout(800)
        with host.expect_download() as d: host.click("#download")
        html = Path(d.value.path()).read_text()
        check("__startWallpaper" in html and "http" not in html.replace("http-equiv", ""), "downloaded wallpaper is self-contained (no URLs)")
        with host.expect_download() as d: host.click("#downloadLively")
        z = zipfile.ZipFile(d.value.path())
        check(z.testzip() is None and sorted(z.namelist()) == ["LivelyProperties.json", "index.html"], "Lively export is a valid zip with index.html + LivelyProperties.json")
        props = json.loads(z.read("LivelyProperties.json"))
        check(all("type" in v and ("text" in v or v["type"] == "label") for v in props.values()), "LivelyProperties.json entries are well-formed")
        shot, perf = render(br, z.read("index.html").decode(), errors, "lively-index")
        check(nonblank(shot), "Lively index.html renders")
        pg = br.new_page(viewport={"width": 960, "height": 540}); watch(pg, "lively-sim", errors)
        pg.set_content(z.read("index.html").decode()); pg.wait_for_timeout(500)
        for n, v in [("palette", 4), ("reaction", 2), ("cell", 20), ("palette", 1)]:
            pg.evaluate("([n,v])=>livelyPropertyListener(n,v)", [n, v]); pg.wait_for_timeout(100)
        pg.wait_for_timeout(900)
        check(pg.evaluate("document.querySelectorAll('canvas').length") == 1 and pg.evaluate("window.__perf.fps") > 5, "Lively setting changes restart the engine cleanly")
        pg.close()

        # settings round trip and hostile input
        st = host.evaluate("getSettings()")
        host.evaluate("()=>applySettings({palette:'lava',reaction:'lens'})")
        host.evaluate("s=>applySettings(s)", st)
        check(host.evaluate("getSettings()") == st, "settings round-trip")
        host.evaluate("()=>applySettings({palette:'<script>',radius:99999,cell:-5,edges:'yes',word:123})")
        after = host.evaluate("getSettings()")
        check(after["palette"] == st["palette"] and float(after["radius"]) == 360 and float(after["cell"]) == 10, "hostile settings are rejected or clamped")
        br.close()

    check(not requests, "no network requests at all" + (": " + "; ".join(requests[:3]) if requests else ""))
    check(not errors, "no console or page errors" + (": " + "; ".join(errors[:3]) if errors else ""))
    print(f"\n{'FAILED' if failures else 'ALL PASSED'} ({len(failures)} failure(s))")
    sys.exit(1 if failures else 0)

if __name__ == "__main__":
    main()

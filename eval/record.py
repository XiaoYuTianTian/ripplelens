"""Record the README screenshots and demo GIF with the locally installed Edge or Chrome.

  pip install playwright pillow        # no `playwright install`: the system browser is used
  python eval/record.py [--lang en,zh] [--only shots|gif] [--out docs/assets] [--browser msedge|chrome|PATH]

It builds the `shop` demo repo (eval/demo.py), starts the review UI and the local model stub
(eval/fake_model.py) with a throwaway AI config, then drives the browser through hover, focus,
AI review and group expansion. Output, per language (`.zh` suffix for Chinese):

    board-light.png  board-dark.png  focus-light.png  ai-dark.png  groups-light.png
    map-light.png  demo.gif
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = {"width": 1440, "height": 860}
GIF_WIDTH = 1200

CURSOR_JS = """
(() => {
  const install = () => {
    if (document.getElementById('rec-cursor')) return;
    const c = document.createElement('div');
    c.id = 'rec-cursor';
    c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2l14 8.2-6.1 1.3L8 17.6z" fill="#141a2a" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: '-40px', top: '-40px', zIndex: 2147483647, pointerEvents: 'none', transition: 'none'});
    document.body.appendChild(c);
    const ring = document.createElement('div');
    ring.id = 'rec-ring';
    Object.assign(ring.style, {position: 'fixed', width: '30px', height: '30px', marginLeft: '-15px', marginTop: '-15px', borderRadius: '50%',
      border: '2px solid rgba(91,108,255,.7)', zIndex: 2147483646, pointerEvents: 'none', opacity: 0, transition: 'opacity .35s, transform .35s'});
    document.body.appendChild(ring);
    addEventListener('mousemove', (e) => { c.style.left = (e.clientX - 3) + 'px'; c.style.top = (e.clientY - 2) + 'px'; }, true);
    addEventListener('mousedown', (e) => {
      ring.style.transition = 'none'; ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px';
      ring.style.opacity = 1; ring.style.transform = 'scale(.5)';
      requestAnimationFrame(() => { ring.style.transition = 'opacity .35s, transform .35s'; ring.style.opacity = 0; ring.style.transform = 'scale(1.4)'; });
    }, true);
  };
  if (document.body) install(); else addEventListener('DOMContentLoaded', install);
})();
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 180) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {url}")


@contextmanager
def servers():
    cfg_dir = Path(tempfile.mkdtemp(prefix="rl-record-cfg-"))
    model_port, ui_port = free_port(), free_port()
    env = {**os.environ, "RIPPLELENS_CONFIG_DIR": str(cfg_dir)}
    for var in ("RIPPLELENS_AI_PROVIDER", "RIPPLELENS_AI_BASE_URL", "RIPPLELENS_AI_MODEL", "RIPPLELENS_AI_API_KEY"):
        env.pop(var, None)
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "ai": {
                    "provider": "openai",
                    "base_url": f"http://127.0.0.1:{model_port}/v1",
                    "model": "demo-reviewer",
                    "enabled": True,
                    "auto": False,
                    "acknowledged": True,
                }
            }
        ),
        encoding="utf-8",
    )
    py = sys.executable
    procs = [
        subprocess.Popen([py, str(ROOT / "eval" / "fake_model.py"), "--port", str(model_port)], env=env),
        subprocess.Popen(
            [py, str(ROOT / "eval" / "demo.py"), "shop", "--no-open", "--port", str(ui_port), "--dest", str(cfg_dir / "shop")],
            env=env,
        ),
    ]
    try:
        url = f"http://127.0.0.1:{ui_port}"
        wait_http(f"{url}/api/impact")
        yield url
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


def launch(pw, browser: str):
    kinds = [browser] if browser else ["msedge", "chrome"]
    errors = []
    for b in kinds:
        try:
            if os.path.sep in b or b.endswith(".exe"):
                return pw.chromium.launch(executable_path=b)
            return pw.chromium.launch(channel=b)
        except Exception as e:
            errors.append(f"{b}: {str(e).splitlines()[0]}")
    raise SystemExit("no usable browser (install Edge or Chrome, or pass --browser PATH)\n" + "\n".join(errors))


class Session:
    def __init__(self, browser, url: str, lang: str, theme: str, scale: float):
        self.ctx = browser.new_context(viewport=VIEW, device_scale_factor=scale, locale="zh-CN" if lang == "zh" else "en-US")
        prefs = json.dumps({"theme": theme, "lang": lang, "detail": "auto"})
        self.ctx.add_init_script(f"if (!sessionStorage.rlRec) {{ localStorage.clear(); localStorage.setItem('rl:prefs', {json.dumps(prefs)}); sessionStorage.rlRec = 1; }}")
        self.ctx.add_init_script(CURSOR_JS)
        self.page = self.ctx.new_page()
        self.page.goto(url)
        self.page.wait_for_selector(".react-flow__node-card")
        self.page.wait_for_timeout(900)
        self.x, self.y = VIEW["width"] * 0.55, VIEW["height"] * 0.7
        self.page.mouse.move(self.x, self.y)
        self.frames: list[tuple[bytes, int]] = []

    # -------------------------------------------------------------- locators
    def row(self, name: str):
        sym = self.page.locator(".sym", has_text=re.compile(rf"^{re.escape(name)}$"))
        return self.page.locator(".react-flow .row").filter(has=sym).first

    def more_detail(self, index: int) -> None:
        self.page.locator('.topbar button[title="More"], .topbar button[title="更多"]').click()
        self.page.locator(".more-pop .seg-row").nth(2).locator("button").nth(index).click()
        # click the empty board so the popover closes without Esc clearing focus
        self.page.mouse.click(VIEW["width"] - 40, VIEW["height"] - 120)
        self.page.wait_for_timeout(350)

    # ---------------------------------------------------------------- frames
    def snap(self, hold: int = 90) -> None:
        self.frames.append((self.page.screenshot(), hold))

    def shot(self, path: Path) -> None:
        self.page.screenshot(path=str(path))
        print("wrote", path.relative_to(ROOT))

    def move(self, target, record: bool, steps: int = 9, dx: float = 0.32) -> None:
        box = target.bounding_box()
        tx, ty = box["x"] + box["width"] * dx, box["y"] + box["height"] / 2
        for i in range(1, steps + 1):
            k = i / steps
            k = k * k * (3 - 2 * k)
            self.page.mouse.move(self.x + (tx - self.x) * k, self.y + (ty - self.y) * k)
            if record:
                self.snap(40)
        self.x, self.y = tx, ty
        self.page.wait_for_timeout(260)

    def click(self, target, record: bool) -> None:
        self.move(target, record)
        self.page.mouse.down()
        self.page.mouse.up()
        self.page.wait_for_timeout(120)
        if record:
            self.snap(90)
            self.snap(90)
        self.page.wait_for_timeout(450)

    def wait_ai(self, record: bool, timeout: float = 15) -> None:
        end = time.time() + timeout
        self.page.wait_for_selector(".drawer .ai-live, .drawer .finding", timeout=timeout * 1000)
        while time.time() < end:
            if record:
                self.snap(110)
            if self.page.locator(".drawer .ai-live").count() == 0 and self.page.locator(".drawer .finding").count() > 0:
                break
            self.page.wait_for_timeout(60)
        self.page.wait_for_timeout(300)

    def scroll_drawer(self, px: int, record: bool) -> None:
        for _ in range(6):
            self.page.evaluate(f"document.querySelector('.drawer-body').scrollBy(0, {px // 6})")
            if record:
                self.snap(45)
        self.page.wait_for_timeout(200)

    def close(self) -> None:
        self.ctx.close()


def suffix(lang: str) -> str:
    return "" if lang == "en" else f".{lang}"


def record_gif(browser, url: str, lang: str, out: Path) -> None:
    s = Session(browser, url, lang, "light", 1)
    s.snap(1300)
    s.move(s.row("applyDiscount"), True)
    s.snap(1700)
    s.move(s.row("OrderService.quote"), True, dx=0.25)
    s.snap(1500)
    s.click(s.row("applyDiscount"), True)
    s.snap(1700)
    s.scroll_drawer(300, True)
    s.snap(1100)
    s.click(s.page.locator(".drawer-tabs button").nth(1), True)
    s.click(s.page.locator(".drawer .btn-ai"), True)
    s.wait_ai(True)
    s.snap(2600)
    s.page.keyboard.press("Escape")
    s.page.wait_for_timeout(500)
    s.snap(1300)
    s.more_detail(3)
    s.page.wait_for_timeout(500)
    s.snap(1300)
    s.click(s.page.locator(".grouprow-changed .card-head", has_text="src/pricing").first, True)
    s.snap(1900)
    s.more_detail(0)
    s.page.wait_for_timeout(500)
    s.snap(1400)
    s.click(s.page.locator(".topbar .seg button", has_text=re.compile(r"^(Impact|影响图)$")), True)
    s.page.wait_for_timeout(500)
    s.snap(1800)
    frames = s.frames
    s.close()
    write_gif(frames, out / f"demo{suffix(lang)}.gif")


def write_gif(frames: list[tuple[bytes, int]], path: Path) -> None:
    from PIL import Image

    imgs = []
    for png, _ in frames:
        im = Image.open(io.BytesIO(png)).convert("RGB")
        imgs.append(im.resize((GIF_WIDTH, round(im.height * GIF_WIDTH / im.width)), Image.LANCZOS))
    sample = imgs[:: max(1, len(imgs) // 6)][:6]
    sheet = Image.new("RGB", (GIF_WIDTH, sum(i.height for i in sample)))
    y = 0
    for im in sample:
        sheet.paste(im, (0, y))
        y += im.height
    palette = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    pal = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in imgs]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=[h for _, h in frames], loop=0, optimize=True, disposal=1)
    print("wrote", path.relative_to(ROOT), f"({len(frames)} frames, {path.stat().st_size // 1024} KB)")


def record_shots(browser, url: str, lang: str, out: Path) -> None:
    sx = suffix(lang)
    for theme in ("light", "dark"):
        s = Session(browser, url, lang, theme, 2)
        s.move(s.row("applyDiscount"), False)
        s.page.wait_for_timeout(400)
        s.shot(out / f"board-{theme}{sx}.png")
        s.click(s.row("applyDiscount"), False)
        if theme == "light":
            s.shot(out / f"focus-light{sx}.png")
        else:
            s.click(s.page.locator(".drawer-tabs button").nth(1), False)
            s.click(s.page.locator(".drawer .btn-ai"), False)
            s.wait_ai(False)
            s.page.mouse.move(VIEW["width"] * 0.4, VIEW["height"] - 20)
            s.page.wait_for_timeout(300)
            s.shot(out / f"ai-dark{sx}.png")
        if theme == "light":
            s.page.keyboard.press("Escape")
            s.more_detail(3)
            s.click(s.page.locator(".grouprow-changed .card-head", has_text="src/pricing").first, False)
            s.page.mouse.move(VIEW["width"] * 0.4, VIEW["height"] - 20)
            s.page.wait_for_timeout(400)
            s.shot(out / f"groups-light{sx}.png")
            s.more_detail(0)
            s.page.locator(".topbar .seg button", has_text=re.compile(r"^(Impact|影响图)$")).click()
            s.page.wait_for_timeout(700)
            s.page.mouse.move(VIEW["width"] * 0.55, VIEW["height"] - 30)
            s.page.wait_for_timeout(250)
            s.shot(out / f"map-light{sx}.png")
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lang", default="en,zh")
    ap.add_argument("--only", choices=["shots", "gif"])
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "assets")
    ap.add_argument("--browser", default="", help="msedge, chrome, or a path to a Chromium-based browser")
    args = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("pip install playwright pillow  (no browser download needed)") from None
    args.out.mkdir(parents=True, exist_ok=True)
    langs = [x.strip() for x in args.lang.split(",") if x.strip()]
    with servers() as url, sync_playwright() as pw:
        browser = launch(pw, args.browser)
        for lang in langs:
            if args.only != "shots":
                record_gif(browser, url, lang, args.out)
            if args.only != "gif":
                record_shots(browser, url, lang, args.out)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

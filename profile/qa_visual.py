"""Capture browser-based visual QA for generated Makaren Signal assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _serve() -> tuple[ThreadingHTTPServer, str]:
    handler = partial(QuietHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _readme_html(base_url: str, theme: str) -> str:
    background = "#0d1117" if theme == "dark" else "#ffffff"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pictures = re.findall(r"<picture>.*?</picture>", readme, flags=re.DOTALL)
    if len(pictures) != 4:
        raise RuntimeError("README must contain exactly four picture blocks")
    raw_base = "https://raw.githubusercontent.com/MakarenD/MakarenD/output/"
    local_pictures = [
        picture.replace(raw_base, f"{base_url}/dist/").replace("?v=1", "")
        for picture in pictures
    ]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;background:{background}}}
      main{{width:100%;max-width:1000px;margin:0 auto;padding:12px;box-sizing:border-box}}
      picture{{display:block;margin:0 0 18px}}
      img{{display:block;width:100%;height:auto}}
    </style></head><body><main>{"".join(local_pictures)}</main></body></html>"""


def _asset_html(
    base_url: str, section: str, theme: str, width: int, reduced: bool = False
) -> str:
    mobile = "-mobile" if width <= 480 else ""
    reduced_suffix = "-reduced" if reduced else ""
    background = "#0d1117" if theme == "dark" else "#ffffff"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;background:{background}}} img{{display:block;width:100%;height:auto}}
    </style></head><body><img id="asset" src="{base_url}/dist/signal-{section}-{theme}{mobile}{reduced_suffix}.svg"></body></html>"""


def _capture(page: Page, html: str, path: Path, wait_ms: int) -> None:
    page.set_content(html, wait_until="networkidle")
    if wait_ms:
        page.wait_for_timeout(wait_ms)
    page.screenshot(path=str(path), full_page=True, animations="allow")


def _new_page(browser: Browser, width: int, theme: str, reduced: bool) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        color_scheme=theme,
        reduced_motion="reduce" if reduced else "no-preference",
        device_scale_factor=1,
    )
    return context.new_page()


def run(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    server, base_url = _serve()
    captures: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for theme in ("dark", "light"):
                for width in (1000, 600, 360):
                    for reduced in (False, True):
                        page = _new_page(browser, width, theme, reduced)
                        motion = "reduced" if reduced else "normal"
                        path = output_dir / f"{theme}-{width}-readme-{motion}.png"
                        _capture(
                            page,
                            _readme_html(base_url, theme),
                            path,
                            100 if reduced else 2200,
                        )
                        selected = page.locator("img").evaluate_all(
                            "images => images.map(image => image.currentSrc)"
                        )
                        expected_mobile = width <= 480
                        if any(("-reduced.svg" in url) != reduced for url in selected):
                            raise RuntimeError(
                                "picture selected an incorrect motion asset"
                            )
                        if any(
                            ("-mobile" in url) != expected_mobile for url in selected
                        ):
                            raise RuntimeError(
                                "picture selected an incorrect viewport asset"
                            )
                        if any(f"-{theme}" not in url for url in selected):
                            raise RuntimeError(
                                "picture selected an incorrect theme asset"
                            )
                        captures.append(path.name)
                        page.context.close()

            timeline = {
                "hero": (
                    ("initial", 30),
                    ("assembly", 850),
                    ("final", 1900),
                    ("pulse", 7550),
                ),
                "history": (
                    ("initial", 30),
                    ("assembly", 800),
                    ("final", 1850),
                    ("pulse", 7550),
                ),
            }
            for theme in ("dark", "light"):
                for section, moments in timeline.items():
                    for label, wait_ms in moments:
                        page = _new_page(browser, 1000, theme, False)
                        path = output_dir / f"{theme}-1000-{section}-{label}.png"
                        _capture(
                            page,
                            _asset_html(base_url, section, theme, 1000),
                            path,
                            wait_ms,
                        )
                        captures.append(path.name)
                        page.context.close()

            for section in ("hero", "history"):
                hashes = []
                for label, wait_ms in (("t0", 100), ("t6", 6000)):
                    page = _new_page(browser, 1000, "dark", True)
                    path = output_dir / f"dark-1000-{section}-reduced-{label}.png"
                    _capture(
                        page,
                        _asset_html(base_url, section, "dark", 1000, True),
                        path,
                        wait_ms,
                    )
                    captures.append(path.name)
                    hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
                    page.context.close()
                if hashes[0] != hashes[1]:
                    raise RuntimeError(
                        f"Reduced-motion {section} changed between t0 and t6"
                    )
            browser.close()
    finally:
        server.shutdown()

    summary = {
        "screenshots": len(captures),
        "files": captures,
        "reduced_motion_stable": True,
        "picture_selection_verified": True,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Makaren Signal browser QA")
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "qa-artifacts" / "makaren-signal"
    )
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

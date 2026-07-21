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

from .render import THEMES, render_system_node, render_systems_header

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
    if len(pictures) < 5 or "CONNECTED_SYSTEMS:START" not in readme:
        raise RuntimeError(
            "README must contain signal pictures and a connected-systems block"
        )
    raw_base = "https://raw.githubusercontent.com/MakarenD/MakarenD/output/"
    local_readme = re.sub(r"\?v=\d+", "", readme.replace(raw_base, f"{base_url}/dist/"))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;background:{background};color:{"#f0f6fc" if theme == "dark" else "#1f2328"}}}
      main{{width:100%;max-width:1000px;margin:0 auto;padding:12px;box-sizing:border-box}}
      picture{{display:block}}
      img{{display:block;width:100%;height:auto}}
    </style></head><body><main>{local_readme}</main></body></html>"""


def _asset_html(
    base_url: str, section: str, theme: str, width: int, reduced: bool = False
) -> str:
    mobile = "-mobile" if width <= 480 else ""
    reduced_suffix = "-reduced" if reduced else ""
    background = "#0d1117" if theme == "dark" else "#ffffff"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;background:{background}}} img{{display:block;width:100%;height:auto}}
    </style></head><body><img id="asset" src="{base_url}/dist/signal-{section}-{theme}{mobile}{reduced_suffix}.svg"></body></html>"""


def _systems_fixture_html(base_url: str, theme: str, width: int) -> str:
    mobile = "-mobile" if width <= 480 else ""
    background = "#0d1117" if theme == "dark" else "#ffffff"
    assets = [
        "signal-systems-header",
        "signal-system-qa-community",
        "signal-system-qa-product",
        "signal-system-qa-website",
    ]
    images = "".join(
        f'<img src="{base_url}/qa-artifacts/makaren-signal/systems-fixture/{asset}-{theme}{mobile}.svg" alt="{asset}" />'
        for asset in assets
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{{margin:0;background:{background}}} img{{display:block;width:100%;height:auto}}
    </style></head><body>{images}</body></html>"""


def _write_systems_fixture(output_dir: Path) -> None:
    fixture_dir = output_dir / "systems-fixture"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    systems = [
        {
            "id": "qa-community",
            "kind": "community",
            "name": "Example Community",
            "url": "https://example.com/community",
            "description": "Temporary QA fixture",
        },
        {
            "id": "qa-product",
            "kind": "product",
            "name": "Example Product",
            "url": "https://example.com/product",
            "description": "Temporary QA fixture",
            "status": "live",
        },
        {
            "id": "qa-website",
            "kind": "website",
            "name": "Example Website",
            "url": "https://example.com",
            "description": "Temporary QA fixture",
        },
    ]
    for theme_name, theme in THEMES.items():
        for mobile in (False, True):
            suffix = "-mobile" if mobile else ""
            (
                fixture_dir / f"signal-systems-header-{theme_name}{suffix}.svg"
            ).write_text(render_systems_header(theme, mobile), encoding="utf-8")
            for index, system in enumerate(systems):
                (
                    fixture_dir
                    / f"signal-system-{system['id']}-{theme_name}{suffix}.svg"
                ).write_text(
                    render_system_node(system, theme, index, len(systems), mobile),
                    encoding="utf-8",
                )


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
    _write_systems_fixture(output_dir)
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
                        motion_assets = [
                            url for url in selected if "signal-system" not in url
                        ]
                        expected_mobile = width <= 480
                        if any(
                            ("-reduced.svg" in url) != reduced for url in motion_assets
                        ):
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

            page = _new_page(browser, 1000, "dark", False)
            page.set_content(_readme_html(base_url, "dark"), wait_until="networkidle")
            hrefs = page.locator("a").evaluate_all(
                "links => links.map(link => link.href)"
            )
            expected = ["https://github.com/UNIVER-Project", "https://makaren.pro/"]
            if hrefs != expected:
                raise RuntimeError(f"Unexpected README links: {hrefs}")
            clicked = page.locator("a").evaluate_all(
                "links => links.map(link => { let href = ''; link.addEventListener('click', event => { event.preventDefault(); href = link.href; }, { once: true }); link.click(); return href; })"
            )
            if clicked != expected:
                raise RuntimeError(
                    f"README links did not receive expected clicks: {clicked}"
                )
            page.context.close()

            for theme in ("dark", "light"):
                for width in (1000, 360):
                    page = _new_page(browser, width, theme, False)
                    path = output_dir / f"{theme}-{width}-systems-fixture.png"
                    _capture(
                        page, _systems_fixture_html(base_url, theme, width), path, 100
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
                    ("draw-25", 650),
                    ("draw-50", 1250),
                    ("final", 2500),
                    ("pulse-start", 3550),
                    ("pulse-middle", 4700),
                    ("pulse-end", 5850),
                    ("pulse-repeat", 7050),
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
        "clicks_verified": True,
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

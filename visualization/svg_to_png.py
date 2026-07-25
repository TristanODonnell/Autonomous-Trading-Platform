"""
Convert the hand-authored portfolio_graphics SVGs to PNG.

These SVGs use CSS classes and hover-state styling (see
portfolio_graphics/safety/four_gates_hero.svg for an example), so they need
a real browser engine to rasterize faithfully — cairosvg's CSS support is
too weak for the <style> blocks these files use, and it also requires a
native Cairo library that isn't installed by default on most machines. This
instead shells out to a headless Chromium-based browser (Edge or Chrome,
whichever is found first) already present on the system, which renders the
SVG exactly as a browser would (fonts, classes, gradients, filters) and
just skips any :hover-only state, which is correct for a static export.

Usage:
    python -m visualization.svg_to_png
    python -m visualization.svg_to_png --input visualization/portfolio_graphics/safety/four_gates_hero.svg
    python -m visualization.svg_to_png --scale 3 --out-dir visualization/portfolio_graphics_png
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_DEFAULT_INPUT_DIR = Path("visualization/portfolio_graphics")

# Common install locations for a Chromium-based browser, checked in order.
# Overridable via --browser.
_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

_VIEWBOX_RE = re.compile(r'viewBox="[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"')


def find_browser(explicit: str | None = None) -> str:
    if explicit:
        if not Path(explicit).exists():
            raise FileNotFoundError(f"--browser path does not exist: {explicit}")
        return explicit
    for candidate in _BROWSER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "No Chromium-based browser found (checked Edge/Chrome/Chromium in common "
        "install locations). Pass --browser <path-to-executable> explicitly."
    )


def parse_viewbox_size(svg_path: Path) -> tuple[int, int]:
    text = svg_path.read_text(encoding="utf-8")
    match = _VIEWBOX_RE.search(text)
    if not match:
        raise ValueError(f"No viewBox found in {svg_path} — cannot determine render size.")
    width, height = float(match.group(1)), float(match.group(2))
    return round(width), round(height)


def render_svg_to_png(
    svg_path: Path,
    png_path: Path,
    *,
    browser: str,
    scale: float = 2.0,
) -> None:
    """Render one SVG to PNG at `scale`x its viewBox resolution."""
    base_w, base_h = parse_viewbox_size(svg_path)
    out_w, out_h = round(base_w * scale), round(base_h * scale)

    svg_text = svg_path.read_text(encoding="utf-8")
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:#070B0F;}"
        f"svg{{display:block;width:{out_w}px;height:{out_h}px;}}</style>"
        f"</head><body>{svg_text}</body></html>"
    )

    png_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html)
        tmp_path = tmp.name

    try:
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--default-background-color=00000000",
                f"--window-size={out_w},{out_h}",
                f"--screenshot={png_path}",
                Path(tmp_path).as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Browser screenshot failed for {svg_path}: {exc.stderr.decode(errors='replace')}"
        ) from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def convert(
    input_path: Path,
    *,
    out_dir: Path | None,
    scale: float,
    browser: str,
) -> list[Path]:
    """Convert a single SVG file or every .svg under a directory (recursive)."""
    if input_path.is_file():
        svg_files = [input_path]
        base_dir = input_path.parent
    else:
        svg_files = sorted(input_path.rglob("*.svg"))
        base_dir = input_path

    written: list[Path] = []
    for svg_file in svg_files:
        if out_dir is None:
            png_file = svg_file.with_suffix(".png")
        else:
            rel = svg_file.relative_to(base_dir)
            png_file = out_dir / rel.with_suffix(".png")
        render_svg_to_png(svg_file, png_file, browser=browser, scale=scale)
        written.append(png_file)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert portfolio_graphics SVGs to PNG via headless browser rendering."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_INPUT_DIR,
        help=f"SVG file or directory to convert (default: {_DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (mirrors input structure). Default: alongside each SVG.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=2.0,
        help="Render scale relative to the SVG's viewBox (default: 2.0, i.e. retina).",
    )
    parser.add_argument(
        "--browser",
        type=str,
        default=None,
        help="Explicit path to a Chromium-based browser executable.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"[error] Input not found: {args.input}", file=sys.stderr)
        return 1

    try:
        browser = find_browser(args.browser)
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(f"Using browser: {browser}")
    written = convert(args.input, out_dir=args.out_dir, scale=args.scale, browser=browser)

    print(f"\nConverted {len(written)} SVG(s) to PNG:")
    for p in written:
        size_kb = p.stat().st_size / 1024 if p.exists() else 0
        print(f"  {p}  ({size_kb:.0f} KB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

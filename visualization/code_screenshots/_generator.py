"""
Render a Python file/line-range as a VS-Code-style screenshot PNG.

One-off portfolio content tool — not part of the trading platform package.
Reuses the headless-browser rendering approach from visualization/svg_to_png.py.

Usage:
    python code_screenshot.py <source_file> <start_line> <end_line> <out.png> [display_path]
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import PythonLexer, get_lexer_for_filename
from pygments.style import Style
from pygments.token import (
    Comment,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Token,
)
from pygments.util import ClassNotFound

_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_browser() -> str:
    for c in _BROWSER_CANDIDATES:
        if Path(c).exists():
            return c
    raise FileNotFoundError("No Chromium-based browser found in common install locations.")


def _autocrop(png_path: Path, sentinel_rgb: tuple[int, int, int], margin: int = 0) -> None:
    """Trim the oversized canvas down to the actual rendered window content."""
    img = Image.open(png_path).convert("RGB")
    bg = Image.new("RGB", img.size, sentinel_rgb)
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()
    if bbox is None:
        return
    left, top, right, bottom = bbox
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(img.width, right + margin)
    bottom = min(img.height, bottom + margin)
    img.crop((left, top, right, bottom)).save(png_path)


class VSCodeDarkPlus(Style):
    background_color = "#1E1E1E"
    styles = {
        Token: "#D4D4D4",
        Comment: "italic #6A9955",
        Keyword: "#569CD6",
        Keyword.Constant: "#569CD6",
        Keyword.Namespace: "#569CD6",
        Name: "#9CDCFE",
        Name.Function: "#DCDCAA",
        Name.Class: "#4EC9B0",
        Name.Decorator: "#DCDCAA",
        Name.Builtin: "#4EC9B0",
        Name.Builtin.Pseudo: "#569CD6",
        Name.Exception: "#4EC9B0",
        Name.Namespace: "#D4D4D4",
        String: "#CE9178",
        String.Doc: "#CE9178",
        Number: "#B5CEA8",
        Operator: "#D4D4D4",
        Punctuation: "#D4D4D4",
    }


def render(
    source_file: Path,
    start_line: int,
    end_line: int,
    out_png: Path,
    display_path: str | None = None,
    scale: float = 2.0,
) -> None:
    lines = source_file.read_text(encoding="utf-8").splitlines()
    snippet_lines = lines[start_line - 1 : end_line]
    snippet = "\n".join(snippet_lines)
    n_lines = len(snippet_lines)

    # linenos='table' drifts around multi-line docstrings in some pygments
    # versions — build the gutter manually instead, one number per real
    # source line, so it can never desync from the highlighted code.
    formatter = HtmlFormatter(
        style=VSCodeDarkPlus,
        noclasses=True,
        nowrap=True,
    )
    try:
        lexer = get_lexer_for_filename(display_path or source_file.name)
    except ClassNotFound:
        lexer = PythonLexer()
    highlighted = highlight(snippet, lexer, formatter)
    lineno_html = "\n".join(str(i) for i in range(start_line, start_line + n_lines))
    code_html = f"""<table><tr>
      <td class="linenos"><pre>{lineno_html}</pre></td>
      <td class="code"><pre>{highlighted}</pre></td>
    </tr></table>"""

    filename = display_path or source_file.name
    # Canvas is deliberately oversized — the real crop happens post-render by
    # trimming to the actual rendered content (see _autocrop), so a rough
    # under-estimate here can no longer clip text or leave a ragged edge.
    max_line_len = max((len(line) for line in snippet.splitlines()), default=40)
    width_px = min(1600, max(640, 120 + max_line_len * 9))
    height_px = 44 + n_lines * 20 + 200

    # Sentinel background color the autocrop trims away — chosen to never
    # plausibly appear in syntax-highlighted code or UI chrome.
    sentinel = "#ff00ff"

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body {{
    margin: 0; padding: 0; background: {sentinel};
    font-family: 'Consolas', 'SF Mono', 'Fira Code', monospace;
  }}
  .window {{
    width: {width_px}px;
    overflow: hidden;
    border: 1px solid #2D2D30;
    display: inline-block;
  }}
  .tabbar {{
    background: #252526;
    height: 36px;
    display: flex;
    align-items: stretch;
    border-bottom: 1px solid #1E1E1E;
  }}
  .tab {{
    background: #1E1E1E;
    color: #FFFFFF;
    font-size: 13px;
    display: flex;
    align-items: center;
    padding: 0 16px;
    border-right: 1px solid #2D2D30;
    border-top: 2px solid #569CD6;
  }}
  .tab .icon {{ margin-right: 8px; font-size: 12px; }}
  .codearea {{
    background: #1E1E1E;
    padding: 12px 0;
    overflow: hidden;
  }}
  .codearea table {{
    border-spacing: 0;
    width: 100%;
  }}
  .codearea pre {{
    margin: 0;
    font-size: 13px;
    line-height: 20px;
  }}
  .codearea td.linenos {{
    color: #6E7681;
    text-align: right;
    padding-right: 16px;
    padding-left: 16px;
    user-select: none;
    width: 1%;
    white-space: nowrap;
  }}
  .codearea td.linenos pre {{ color: #6E7681 !important; }}
  .codearea td.code {{ padding-right: 20px; width: 100%; }}
  {formatter.get_style_defs(".codearea")}
</style></head>
<body>
  <div class="window">
    <div class="tabbar">
      <div class="tab">{filename}</div>
    </div>
    <div class="codearea">
      {code_html}
    </div>
  </div>
</body></html>"""

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html)
        tmp_path = tmp.name

    browser = find_browser()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_w = round(width_px * scale)
    out_h = round(height_px * scale)
    subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={out_w},{out_h}",
            f"--force-device-scale-factor={scale}",
            f"--screenshot={out_png}",
            Path(tmp_path).as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    Path(tmp_path).unlink(missing_ok=True)

    sentinel_rgb = (
        int(sentinel[1:3], 16),
        int(sentinel[3:5], 16),
        int(sentinel[5:7], 16),
    )
    _autocrop(out_png, sentinel_rgb)


if __name__ == "__main__":
    src = Path(sys.argv[1])
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    out = Path(sys.argv[4])
    disp = sys.argv[5] if len(sys.argv) > 5 else None
    render(src, start, end, out, display_path=disp)
    print(f"Wrote {out}")

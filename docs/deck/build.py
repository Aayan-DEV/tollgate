"""Build the deck: inline the dashboard's own icons into deck.src.html, then print it to Tollgate.pdf.

  uv run python docs/deck/build.py
"""

import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

icons = json.loads(subprocess.run(
    ["node", "-e", "import('./dashboard/static/icons.js').then(m => console.log(JSON.stringify(m.ICONS)))"],
    cwd=ROOT, capture_output=True, text=True, check=True).stdout)


def svg(name: str) -> str:
    body = re.sub(r'stroke-width="[^"]*"', 'stroke-width="1.8"', icons[name])
    return (f'<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{body}</svg>')


src = (HERE / "deck.src.html").read_text()
out = re.sub(r"\{\{i:([a-z]+)\}\}", lambda m: svg(m.group(1)), src)
(HERE / "deck.html").write_text(out)
subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=8000",
                "--run-all-compositor-stages-before-draw", f"--print-to-pdf={HERE / 'Tollgate.pdf'}",
                f"file://{HERE / 'deck.html'}"], check=True, capture_output=True)
print("wrote", HERE / "deck.html", "and", HERE / "Tollgate.pdf")

"""Render docs/index.html (self-contained dashboard) from the processed JSON."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "data/processed/dashboard_data.json").read_text())
html = (ROOT / "src/dashboard_template.html").read_text()
out = html.replace("__DATA__", json.dumps(data, separators=(",", ":")))
(ROOT / "docs/index.html").write_text(out)
print(f"Wrote docs/index.html ({len(out)/1024:.0f} KB)")

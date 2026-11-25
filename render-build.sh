#!/usr/bin/env bash
set -euo pipefail

# Render's build working directory is the Git repository root.
python -m pip install -r requirements.txt
cd backend

# Keep the ESCO catalog separate from runtime accounts/sessions.
# This database is packaged into Render's build artifact.
python setup_esco.py --database data/esco_catalog.sqlite3

python - <<'PY'
from pathlib import Path
import setup_esco
path = Path("data/esco_catalog.sqlite3").resolve()
status = setup_esco.catalog_status(path)
print("Bundled ESCO catalog:", status)
if not status["ready"]:
    raise SystemExit("ESCO catalog is not ready; refusing incomplete build.")
PY

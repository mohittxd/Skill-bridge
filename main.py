"""Render entrypoint for the SkillBridge FastAPI backend."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
from backend.main import app  # noqa: E402,F401

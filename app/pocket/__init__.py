"""Pocket v0.1 — mobile companion sub-app.

Mounted at /m/* on the existing FastAPI app. Shares auth + DB.
Thin proxy in front of Ollama; never exposes Ollama to clients.
"""

from app.pocket.router import router  # noqa: F401  (re-export)

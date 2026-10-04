"""Frontend route for serving the web interface."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["frontend"])
INDEX_HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_index() -> HTMLResponse:
    return HTMLResponse(content=INDEX_HTML.read_text(encoding="utf-8"))

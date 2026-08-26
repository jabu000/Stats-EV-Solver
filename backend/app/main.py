"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import boards, entries, settings as settings_api, track_record
from app.config import REPO_ROOT, get_settings
from app.db import init_db

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


# `create_all` is idempotent, so initialising at import as well as in the lifespan
# hook costs nothing and means the app is usable under any runner -- including test
# clients that never trigger lifespan events.
init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Stats EV Solver",
    description=(
        "Prices Underdog Pick'em props for MLB, NFL and CFB, and ranks them by "
        "expected value or by probability of hitting."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# The dev frontend runs on its own Vite port; in production it is served from here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(boards.router)
app.include_router(entries.router)
app.include_router(settings_api.router)
app.include_router(track_record.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "data_mode": get_settings().data_mode.value}


def _mount_frontend() -> None:
    """Serve the built SPA, if it has been built."""
    if not FRONTEND_DIST.exists():
        return

    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        # Client-side routing: any non-API path returns the shell.
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


_mount_frontend()

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    # Playwright needs Windows subprocess support when launched from the app event loop.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gov_aggregator.services import crawl_site_keys, site_catalog_payload


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Government Website Crawler")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class CrawlRequest(BaseModel):
    site_keys: list[str] = Field(default_factory=list)
    use_cache: bool = False


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def healthcheck() -> dict:
    catalog = site_catalog_payload()
    return {
        "status": "ok",
        "supported_sites": catalog["meta"]["supported_sites"],
        "total_sites": catalog["meta"]["total_sites"],
    }


@app.get("/api/sites")
async def get_sites() -> dict:
    return site_catalog_payload()


@app.post("/api/crawl")
async def crawl(request: CrawlRequest) -> dict:
    return await crawl_site_keys(request.site_keys, use_cache=request.use_cache)

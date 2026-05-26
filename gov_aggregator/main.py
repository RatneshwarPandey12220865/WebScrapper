from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gov_aggregator.services import (
    ACTIVE_JOBS,
    JOBS_LOCK,
    crawl_site_keys,
    run_bulk_crawl,
    site_catalog_payload,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
EXPORTS_DIR = BASE_DIR.parent / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Government Website Crawler")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Request models ─────────────────────────────────────────────────────────
class CrawlRequest(BaseModel):
    site_keys: list[str] = Field(default_factory=list)
    use_cache: bool = False
    date_from: str | None = None
    date_to: str | None = None


class CrawlAllRequest(BaseModel):
    use_cache: bool = False
    date_from: str | None = None
    date_to: str | None = None


class ExportSummaryRequest(BaseModel):
    job_id: str
    date_from: str | None = None
    date_to: str | None = None


# ── Routes ─────────────────────────────────────────────────────────────────
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
    return await crawl_site_keys(
        request.site_keys,
        use_cache=request.use_cache,
        date_from=request.date_from,
        date_to=request.date_to,
    )


@app.post("/api/crawl/all")
async def crawl_all(request: CrawlAllRequest, background_tasks: BackgroundTasks) -> dict:
    job_id = uuid.uuid4().hex[:10]
    with JOBS_LOCK:
        ACTIVE_JOBS[job_id] = {
            "job_id": job_id,
            "status": "starting",
            "sites_total": 0,
            "sites_done": 0,
            "started_at": _now_iso(),
            "finished_at": None,
            "date_from": request.date_from,
            "date_to": request.date_to,
            "result": None,
            "error": None,
        }
    background_tasks.add_task(
        run_bulk_crawl,
        job_id,
        use_cache=request.use_cache,
        date_from=request.date_from,
        date_to=request.date_to,
    )
    return {
        "job_id": job_id,
        "status": "started",
        "message": "Bulk crawl started. Poll /api/crawl/status/{job_id} for progress.",
    }


@app.get("/api/crawl/status/{job_id}")
async def crawl_status(job_id: str) -> dict:
    with JOBS_LOCK:
        job = ACTIVE_JOBS.get(job_id)
    if not job:
        return {"error": "Job not found", "job_id": job_id}

    total = job["sites_total"] or 1
    done = job["sites_done"]
    percent = round((done / total) * 100, 1)

    started_at = job.get("started_at")
    elapsed = 0
    if started_at:
        try:
            elapsed = round(
                (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds()
            )
        except Exception:
            elapsed = 0

    result_meta = None
    if job.get("result"):
        result_meta = job["result"].get("meta")

    return {
        "job_id": job_id,
        "status": job["status"],
        "sites_total": job["sites_total"],
        "sites_done": done,
        "percent_complete": percent,
        "started_at": started_at,
        "finished_at": job.get("finished_at"),
        "elapsed_seconds": elapsed,
        "date_from": job.get("date_from"),
        "date_to": job.get("date_to"),
        "result_meta": result_meta,
    }


@app.post("/api/crawl/cancel/{job_id}")
async def cancel_crawl(job_id: str) -> dict:
    with JOBS_LOCK:
        job = ACTIVE_JOBS.get(job_id)
        if not job:
            return {"error": "Job not found", "job_id": job_id}
        if job["status"] in {"done", "cancelled", "failed"}:
            return {"job_id": job_id, "status": job["status"], "message": "Job already finished."}
        ACTIVE_JOBS[job_id]["status"] = "cancelled"
        ACTIVE_JOBS[job_id]["finished_at"] = _now_iso()
    return {"job_id": job_id, "status": "cancelled", "message": "Crawl cancelled."}


@app.get("/api/crawl/result/{job_id}")
async def crawl_result(job_id: str) -> dict:
    with JOBS_LOCK:
        job = ACTIVE_JOBS.get(job_id)
    if not job:
        return {"error": "Job not found", "job_id": job_id}
    if job["status"] != "done":
        return {"error": f"Job is not done yet (status: {job['status']})", "job_id": job_id}
    return job["result"]


# ── Phase 3: Excel export ──────────────────────────────────────────────────

@app.post("/api/export/summary")
async def export_summary(request: ExportSummaryRequest) -> FileResponse:
    """Generate summary Excel from a completed bulk-crawl job and stream it."""
    from gov_aggregator.exporters.excel_summary import generate_summary_excel

    with JOBS_LOCK:
        job = ACTIVE_JOBS.get(request.job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{request.job_id}' not found.")
    if job["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not finished yet (status: {job['status']}). Wait for it to complete.",
        )

    crawl_result = job["result"]
    date_from = request.date_from or job.get("date_from")
    date_to   = request.date_to   or job.get("date_to")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    df_label = (date_from or "open").replace("-", "")
    dt_label = (date_to   or "open").replace("-", "")
    filename = f"KSyder_Summary_{df_label}_to_{dt_label}_{ts}.xlsx"
    output_path = EXPORTS_DIR / filename

    await generate_summary_excel(
        crawl_result=crawl_result,
        date_from=date_from,
        date_to=date_to,
        output_path=str(output_path),
    )

    return FileResponse(
        path=str(output_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@app.get("/api/export/site/{site_key}")
async def export_site(
    site_key: str,
    job_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> FileResponse:
    """Generate and stream a single-site detail Excel for a completed job."""
    from gov_aggregator.exporters.excel_site_detail import (
        _safe_filename,
        generate_site_detail_excel,
    )

    with JOBS_LOCK:
        job = ACTIVE_JOBS.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Job not finished (status: {job['status']}).")

    crawl_result = job["result"]
    date_from = date_from or job.get("date_from")
    date_to   = date_to   or job.get("date_to")

    items_for_site = [i for i in crawl_result.get("items", []) if i.get("site_key") == site_key]
    if not items_for_site:
        raise HTTPException(status_code=404, detail=f"No items found for site '{site_key}'.")

    status = next(
        (s for s in crawl_result.get("site_statuses", []) if s["site_key"] == site_key),
        {"site_key": site_key, "site_name": site_key, "ministry": ""},
    )

    ministry = status.get("ministry") or "Unknown"
    filename = _safe_filename(ministry, site_key, date_from)
    output_path = EXPORTS_DIR / filename

    await generate_site_detail_excel(
        site_key=site_key,
        items=items_for_site,
        status=status,
        date_from=date_from,
        date_to=date_to,
        output_path=str(output_path),
    )

    return FileResponse(
        path=str(output_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@app.get("/api/export/all")
async def export_all(
    job_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> FileResponse:
    """Generate ZIP of all per-site detail Excels + summary for a completed job."""
    from gov_aggregator.exporters.zip_builder import generate_all_site_files

    with JOBS_LOCK:
        job = ACTIVE_JOBS.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Job not finished (status: {job['status']}).")

    date_from = date_from or job.get("date_from")
    date_to   = date_to   or job.get("date_to")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    df_label = (date_from or "open").replace("-", "")
    dt_label = (date_to   or "open").replace("-", "")
    zip_filename = f"KSyder_Export_{df_label}_to_{dt_label}_{ts}.zip"

    zip_path = await generate_all_site_files(
        crawl_result=job["result"],
        date_from=date_from,
        date_to=date_to,
        output_dir=str(EXPORTS_DIR),
    )

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=zip_filename,
    )

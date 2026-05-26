"""Phase 4 — ZIP bundler.

Generates per-site detail Excel files for every site with items in range,
bundles them together with the summary Excel into one ZIP archive.
"""
from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("gov_aggregator.exporters.zip_builder")


async def generate_all_site_files(
    crawl_result: dict[str, Any],
    date_from: str | None,
    date_to: str | None,
    output_dir: str,
) -> str:
    """Generate per-site detail Excels + summary Excel, bundle into a ZIP.

    Returns the path to the ZIP file.
    """
    from gov_aggregator.exporters.excel_site_detail import (
        _safe_filename,
        generate_site_detail_excel,
    )
    from gov_aggregator.exporters.excel_summary import generate_summary_excel

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = crawl_result.get("items", [])
    statuses: list[dict[str, Any]] = crawl_result.get("site_statuses", [])

    # Group items by site_key
    items_by_site: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        sk = item.get("site_key", "")
        items_by_site.setdefault(sk, []).append(item)

    status_by_site: dict[str, dict[str, Any]] = {s["site_key"]: s for s in statuses}

    # Only generate files for sites that have items
    site_keys_with_items = [sk for sk, site_items in items_by_site.items() if site_items]

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    df_label = (date_from or "open").replace("-", "")
    dt_label = (date_to   or "open").replace("-", "")
    zip_filename = f"KSyder_Export_{df_label}_to_{dt_label}_{ts}.zip"
    zip_path = out_dir / zip_filename

    # Generate summary Excel first
    summary_filename = f"KSyder_Summary_{df_label}_to_{dt_label}_{ts}.xlsx"
    summary_path = out_dir / summary_filename
    await generate_summary_excel(
        crawl_result=crawl_result,
        date_from=date_from,
        date_to=date_to,
        output_path=str(summary_path),
    )

    # Generate per-site detail files concurrently (max 8 at once)
    sem = asyncio.Semaphore(8)

    async def _generate_one(sk: str) -> tuple[str, str] | None:
        async with sem:
            site_items = items_by_site.get(sk, [])
            st = status_by_site.get(sk, {})
            ministry = st.get("ministry") or "Unknown"
            fname = _safe_filename(ministry, sk, date_from)
            fpath = out_dir / fname
            try:
                await generate_site_detail_excel(
                    site_key=sk,
                    items=site_items,
                    status=st,
                    date_from=date_from,
                    date_to=date_to,
                    output_path=str(fpath),
                )
                return (str(fpath), fname)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to generate detail Excel for %s: %s", sk, exc)
                return None

    results = await asyncio.gather(*[_generate_one(sk) for sk in site_keys_with_items], return_exceptions=True)

    # Collect valid (path, filename) pairs
    generated: list[tuple[str, str]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Unexpected error generating site Excel: %s", r)
        elif r is not None:
            generated.append(r)

    # Build ZIP in a thread to avoid blocking the event loop
    def _build_zip() -> None:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Summary goes in root of ZIP
            zf.write(summary_path, summary_filename)
            # Per-site files go in a "sites/" subfolder
            for fpath, fname in generated:
                zf.write(fpath, f"sites/{fname}")

    await asyncio.to_thread(_build_zip)

    # Clean up individual xlsx files (they're now inside the ZIP)
    def _cleanup() -> None:
        try:
            summary_path.unlink(missing_ok=True)
        except Exception:
            pass
        for fpath, _ in generated:
            try:
                Path(fpath).unlink(missing_ok=True)
            except Exception:
                pass

    await asyncio.to_thread(_cleanup)

    logger.info(
        "ZIP export built: %s (%d site files + summary)",
        zip_path, len(generated),
    )
    return str(zip_path)

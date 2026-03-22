from __future__ import annotations

import asyncio
import json
import sys

from gov_aggregator.services import crawl_all_supported_sites, crawl_site_keys


async def main() -> None:
    site_key = sys.argv[1] if len(sys.argv) > 1 else None

    if site_key:
        print(f"--- Crawling single site: {site_key} ---")
        results = await crawl_site_keys([site_key], use_cache=False)
    else:
        print("--- Crawling all supported sites ---")
        results = await crawl_all_supported_sites(use_cache=False)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

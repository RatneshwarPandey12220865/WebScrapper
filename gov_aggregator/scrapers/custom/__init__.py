from __future__ import annotations

from collections.abc import Awaitable, Callable

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

from .asi import crawl_asi
from .cbic import crawl_cbic_customs
from .chemexcil import crawl_chemexcil
from .chandigarh import crawl_chandigarh
from .commerce import crawl_commerce
from .dgft import crawl_dgft
from .dbt import crawl_dbt
from .dolr import crawl_dolr
from .dot import crawl_dot
from .fssai import crawl_fssai_recent
from .gst import crawl_gst
from .income_tax import scrape_income_tax
from .irdai import crawl_irdai
from .labour import crawl_labour
from .meity import crawl_meity
from .nccd import crawl_nccd
from .nmc import crawl_nmc
from .pci import crawl_pci
from .rbi import crawl_rbi
from .sebi import crawl_sebi

CustomCrawler = Callable[[SiteConfig], Awaitable[list[ScrapedItem]]]

CUSTOM_CRAWLERS: dict[str, CustomCrawler] = {
    "archaeological-survey-of-india": crawl_asi,
    "cbic-customs": crawl_cbic_customs,
    "chemexcil": crawl_chemexcil,
    "chandigarh": crawl_chandigarh,
    "department-of-land-resources": crawl_dolr,
    "minisry-of-commerce": crawl_commerce,
    "department-of-bio-technology": crawl_dbt,
    "directorate-general-of-foreign-trade": crawl_dgft,
    "dot": crawl_dot,
    "fssai": crawl_fssai_recent,
    "gst": crawl_gst,
    "income-tax": scrape_income_tax,
    "irdai": crawl_irdai,
    "ministry-of-labour": crawl_labour,
    "meity": crawl_meity,
    "national-centre-for-cold-chain-development": crawl_nccd,
    "national-medical-commission": crawl_nmc,
    "pharmacy-council": crawl_pci,
    "rbi": crawl_rbi,
    "sebi": crawl_sebi,
}

__all__ = [
    "CUSTOM_CRAWLERS",
    "CustomCrawler",
    "crawl_asi",
    "crawl_cbic_customs",
    "crawl_chemexcil",
    "crawl_chandigarh",
    "crawl_commerce",
    "crawl_dgft",
    "crawl_dbt",
    "crawl_dolr",
    "crawl_dot",
    "crawl_fssai_recent",
    "crawl_gst",
    "crawl_irdai",
    "crawl_labour",
    "crawl_meity",
    "crawl_nccd",
    "crawl_nmc",
    "crawl_pci",
    "crawl_rbi",
    "crawl_sebi",
    "scrape_income_tax",
]

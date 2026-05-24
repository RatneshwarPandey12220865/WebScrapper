from __future__ import annotations

from collections.abc import Awaitable, Callable

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

from .agriculture import crawl_agriculture
from .andhra_pradesh import crawl_andhra_pradesh
from .civil_aviation import crawl_civil_aviation
from .dasd_kerala import crawl_dasd_kerala
from .dopt import crawl_dopt
from .asi import crawl_asi
from .csez import crawl_csez
from .ayush import crawl_ayush
from .cbic import crawl_cbic_customs
from .cci import crawl_cci
from .coffee_board import crawl_coffee_board
from .dahd import crawl_dahd
from .chandigarh import crawl_chandigarh
from .chemexcil import crawl_chemexcil
from .commerce import crawl_commerce
from .dgft import crawl_dgft
from .dbt import crawl_dbt
from .dhr import crawl_dhr
from .dpiit import crawl_dpiit
from .dolr import crawl_dolr
from .epfo import crawl_epfo
from .dot import crawl_dot
from .fertilizers import crawl_fertilizers
from .fisheries import crawl_fisheries
from .fssai import crawl_fssai_recent
from .gst import crawl_gst
from .icmr import crawl_icmr
from .income_tax import scrape_income_tax
from .irdai import crawl_irdai
from .labour import crawl_labour
from .meity import crawl_meity
from .morth import crawl_morth
from .nccd import crawl_nccd
from .nmc import crawl_nmc
from .nse import crawl_nse
from .pci import crawl_pci
from .pharma_dept import crawl_pharma
from .power import crawl_power
from .power_pib import crawl_power_pib
from .rajasthan import crawl_rajasthan
from .rbi import crawl_rbi
from .sebi import crawl_sebi

CustomCrawler = Callable[[SiteConfig], Awaitable[list[ScrapedItem]]]

CUSTOM_CRAWLERS: dict[str, CustomCrawler] = {
    "andhra-pradesh-official-portal": crawl_andhra_pradesh,
    "civil-aviation": crawl_civil_aviation,
    "dasd-kerala": crawl_dasd_kerala,
    "dopt": crawl_dopt,
    "archaeological-survey-of-india": crawl_asi,
    "cochin-sez": crawl_csez,
    "department-of-agriculture-and-farmers-welfare-whatsnew": crawl_agriculture,
    "cbic-customs": crawl_cbic_customs,
    "cci": crawl_cci,
    "competition-commission-of-india": crawl_cci,
    "chemexcil": crawl_chemexcil,
    "chandigarh": crawl_chandigarh,
    "industry-and-internal-trade": crawl_dpiit,
    "department-of-land-resources": crawl_dolr,
    "epfo": crawl_epfo,
    "minisry-of-commerce": crawl_commerce,
    "department-of-bio-technology": crawl_dbt,
    "department-of-health-research": crawl_dhr,
    "directorate-general-of-foreign-trade": crawl_dgft,
    "dot": crawl_dot,
    "fssai": crawl_fssai_recent,
    "gst": crawl_gst,
    "income-tax": scrape_income_tax,
    "irdai": crawl_irdai,
    "ministry-of-labour": crawl_labour,
    "ministry-of-road-transport-and-highways": crawl_morth,
    "meity": crawl_meity,
    "ministry-of-ayush": crawl_ayush,
    "national-centre-for-cold-chain-development": crawl_nccd,
    "national-medical-commission": crawl_nmc,
    "nse": crawl_nse,
    "pharmacy-council": crawl_pci,
    "department-of-pharmaceuticals": crawl_pharma,
    "power-ministry": crawl_power,
    "power-ministry-pib": crawl_power_pib,
    "rajasthan": crawl_rajasthan,
    "rbi": crawl_rbi,
    "coffee-board": crawl_coffee_board,
    "department-of-animal-husbandry-and-dairying": crawl_dahd,
    "department-of-fertilizers": crawl_fertilizers,
    "department-of-fisheries": crawl_fisheries,
    "icmr": crawl_icmr,
    "sebi": crawl_sebi,
}

__all__ = [
    "CUSTOM_CRAWLERS",
    "CustomCrawler",
    "crawl_agriculture",
    "crawl_andhra_pradesh",
    "crawl_civil_aviation",
    "crawl_dasd_kerala",
    "crawl_dopt",
    "crawl_asi",
    "crawl_csez",
    "crawl_ayush",
    "crawl_cbic_customs",
    "crawl_cci",
    "crawl_chemexcil",
    "crawl_chandigarh",
    "crawl_commerce",
    "crawl_dgft",
    "crawl_dbt",
    "crawl_dhr",
    "crawl_dpiit",
    "crawl_dolr",
    "crawl_dot",
    "crawl_fssai_recent",
    "crawl_gst",
    "crawl_irdai",
    "crawl_labour",
    "crawl_meity",
    "crawl_morth",
    "crawl_nccd",
    "crawl_nmc",
    "crawl_nse",
    "crawl_pci",
    "crawl_power",
    "crawl_power_pib",
    "crawl_rajasthan",
    "crawl_rbi",
    "crawl_coffee_board",
    "crawl_dahd",
    "crawl_fertilizers",
    "crawl_fisheries",
    "crawl_sebi",
    "scrape_income_tax",
]

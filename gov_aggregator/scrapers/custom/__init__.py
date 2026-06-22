from __future__ import annotations

from collections.abc import Awaitable, Callable

from gov_aggregator.scrapers.schemas import ScrapedItem, SiteConfig

from .agriculture import crawl_agriculture
from .andhra_pradesh import crawl_andhra_pradesh
from .arunachal_pradesh import crawl_arunachal_pradesh
from .chhattisgarh import crawl_chhattisgarh
from .civil_aviation import crawl_civil_aviation
from .dasd_kerala import crawl_dasd_kerala
from .darpg import crawl_darpg
from .dopt import crawl_dopt
from .doppw import crawl_doppw
from .asi import crawl_asi
from .bis import crawl_bis
from .cbic_gst import crawl_cbic_gst
from .csez import crawl_csez
from .ayush import crawl_ayush
from .cbic import crawl_cbic_customs
from .cci import crawl_cci
from .cerc import crawl_cerc
from .coal import crawl_coal
from .coffee_board import crawl_coffee_board
from .dahd import crawl_dahd
from .chandigarh import crawl_chandigarh
from .chemexcil import crawl_chemexcil
from .commerce import crawl_commerce
from .dgft import crawl_dgft
from .dor import crawl_dor
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
from .icar import crawl_icar
from .icmr import crawl_icmr
from .ip_india import crawl_ip_india
from .income_tax import scrape_income_tax
from .irdai import crawl_irdai
from .labour import crawl_labour
from .dot_eservices import crawl_dot_eservices
from .enam import crawl_enam
from .jalshakti import crawl_jalshakti
from .jerc_mizoram import crawl_jerc_mizoram
from .jercuts import crawl_jercuts
from .mea import crawl_mea
from .mha import crawl_mha
from .mohfw_dohfw import crawl_mohfw_dohfw
from .tea_board import crawl_tea_board
from .sfac import crawl_sfac
from .npci import crawl_npci
from .meity import crawl_meity
from .morth import crawl_morth
from .nccd import crawl_nccd
from .nbb import crawl_nbb
from .nmc import crawl_nmc
from .nse import crawl_nse
from .pci import crawl_pci
from .pharma_dept import crawl_pharma
from .pharmexcil import crawl_pharmexcil
from .project_exports import crawl_project_exports
from .power import crawl_power
from .power_pib import crawl_power_pib
from .rajasthan import crawl_rajasthan
from .rbi import crawl_rbi
from .sebi import crawl_sebi
from .cooperation import crawl_cooperation
from .dfs import crawl_dfs
from .midh import crawl_midh
from .nfsm import crawl_nfsm
from .ppqs import crawl_ppqs

CustomCrawler = Callable[[SiteConfig], Awaitable[list[ScrapedItem]]]

CUSTOM_CRAWLERS: dict[str, CustomCrawler] = {
    "andhra-pradesh-official-portal": crawl_andhra_pradesh,
    "arunachal-pradesh": crawl_arunachal_pradesh,
    "chhattisgarh": crawl_chhattisgarh,
    "civil-aviation": crawl_civil_aviation,
    "dasd-kerala": crawl_dasd_kerala,
    "darpg": crawl_darpg,
    "dopt": crawl_dopt,
    "doppw": crawl_doppw,
    "archaeological-survey-of-india": crawl_asi,
    "bis": crawl_bis,
    "cbic-gst-portal": crawl_cbic_gst,
    "cochin-sez": crawl_csez,
    "department-of-agriculture-and-farmers-welfare-whatsnew": crawl_agriculture,
    "cbic-customs": crawl_cbic_customs,
    "cci": crawl_cci,
    "competition-commission-of-india": crawl_cci,
    "central-electricity-regulatory-commission": crawl_cerc,
    "chemexcil": crawl_chemexcil,
    "chandigarh": crawl_chandigarh,
    "industry-and-internal-trade": crawl_dpiit,
    "department-of-land-resources": crawl_dolr,
    "epfo": crawl_epfo,
    "minisry-of-commerce": crawl_commerce,
    "department-of-bio-technology": crawl_dbt,
    "department-of-health-research": crawl_dhr,
    "directorate-general-of-foreign-trade": crawl_dgft,
    "department-of-revenue": crawl_dor,
    "dot": crawl_dot,
    "dot-eservices": crawl_dot_eservices,
    "fssai": crawl_fssai_recent,
    "gst": crawl_gst,
    "income-tax": scrape_income_tax,
    "irdai": crawl_irdai,
    "ministry-of-labour": crawl_labour,
    "ministry-of-road-transport-and-highways": crawl_morth,
    "enam": crawl_enam,
    "jalshakti-dowr": crawl_jalshakti,
    "jerc-mizoram": crawl_jerc_mizoram,
    "jercuts": crawl_jercuts,
    "mea": crawl_mea,
    "ministry-of-home-affairs": crawl_mha,
    "mohfw-dohfw": crawl_mohfw_dohfw,
    "npci": crawl_npci,
    "sfac": crawl_sfac,
    "tea-board": crawl_tea_board,
    "meity": crawl_meity,
    "ministry-of-ayush": crawl_ayush,
    "national-centre-for-cold-chain-development": crawl_nccd,
    "national-bee-board": crawl_nbb,
    "national-medical-commission": crawl_nmc,
    "nse": crawl_nse,
    "pharmacy-council": crawl_pci,
    "department-of-pharmaceuticals": crawl_pharma,
    "pharmexcil": crawl_pharmexcil,
    "project-exports-promotion-council": crawl_project_exports,
    "power-ministry": crawl_power,
    "power-ministry-pib": crawl_power_pib,
    "rajasthan": crawl_rajasthan,
    "rbi": crawl_rbi,
    "ministry-of-coal-for-coal-mines": crawl_coal,
    "coffee-board": crawl_coffee_board,
    "department-of-animal-husbandry-and-dairying": crawl_dahd,
    "department-of-fertilizers": crawl_fertilizers,
    "department-of-fisheries": crawl_fisheries,
    "department-of-agricultural-research-and-education": crawl_icar,
    "icmr": crawl_icmr,
    "industry-and-internal-trade-ip": crawl_ip_india,
    "sebi": crawl_sebi,
    "national-food-security-mission-for-oilseeds": crawl_nfsm,
    "ministry-of-cooperation": crawl_cooperation,
    "department-of-financial-services": crawl_dfs,
    "directorate-of-plant-protection-quarantine-storage-for-plant-quarantine-rules-insectides-rules": crawl_ppqs,
    "mission-for-integrated-development-of-horticulture": crawl_midh,
}

__all__ = [
    "CUSTOM_CRAWLERS",
    "CustomCrawler",
    "crawl_agriculture",
    "crawl_andhra_pradesh",
    "crawl_arunachal_pradesh",
    "crawl_civil_aviation",
    "crawl_dasd_kerala",
    "crawl_darpg",
    "crawl_dopt",
    "crawl_doppw",
    "crawl_asi",
    "crawl_bis",
    "crawl_cbic_gst",
    "crawl_csez",
    "crawl_ayush",
    "crawl_cbic_customs",
    "crawl_cci",
    "crawl_cerc",
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
    "crawl_jerc_mizoram",
    "crawl_sebi",
    "crawl_nfsm",
    "crawl_cooperation",
    "crawl_dfs",
    "crawl_midh",
    "crawl_ppqs",
    "scrape_income_tax",
]

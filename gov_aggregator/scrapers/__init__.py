__all__ = ["ScraperEngine"]


def __getattr__(name: str):
    if name == "ScraperEngine":
        from gov_aggregator.scrapers.engine import ScraperEngine

        return ScraperEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

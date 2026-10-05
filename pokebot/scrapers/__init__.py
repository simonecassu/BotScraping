from .base import BaseScraper, Listing, ScraperError
from .ebay import EbayScraper
from .subito import SubitoScraper
from .vinted import VintedScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    SubitoScraper.name: SubitoScraper,
    VintedScraper.name: VintedScraper,
    EbayScraper.name: EbayScraper,
}

__all__ = ["BaseScraper", "Listing", "ScraperError", "SCRAPERS", "SubitoScraper", "VintedScraper", "EbayScraper"]

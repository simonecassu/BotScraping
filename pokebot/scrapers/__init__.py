from .base import BaseScraper, Listing, ScraperError
from .ebay import EbayScraper
from .vinted import VintedScraper
from .wallapop import WallapopScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    WallapopScraper.name: WallapopScraper,
    VintedScraper.name: VintedScraper,
    EbayScraper.name: EbayScraper,
}

__all__ = ["BaseScraper", "Listing", "ScraperError", "SCRAPERS", "WallapopScraper", "VintedScraper", "EbayScraper"]

"""Live interest rate feed with provenance and graceful degradation."""

from .catalog import CATALOG, CATEGORIES, FEATURED_KEYS, RateDefinition
from .cache import TTLCache
from .fred import FredProvider
from .mock import MockProvider
from .models import (
    Freshness, RateFeedError, RateObservation, RateQuote, RateSeries,
)
from .service import RateService

__version__ = "0.1.0"

__all__ = [
    "CATALOG", "CATEGORIES", "FEATURED_KEYS", "RateDefinition", "TTLCache",
    "FredProvider", "MockProvider", "Freshness", "RateFeedError",
    "RateObservation", "RateQuote", "RateSeries", "RateService",
]

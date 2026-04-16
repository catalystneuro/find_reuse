"""Archive adapters for the multi-archive reuse analysis framework."""

from .base import ArchiveAdapter
from .dandi import DANDIAdapter
from .crcns import CRCNSAdapter

ADAPTERS = {
    "dandi": DANDIAdapter,
    "crcns": CRCNSAdapter,
}


def get_adapter(name: str, **kwargs) -> ArchiveAdapter:
    """Get an archive adapter by name."""
    if name not in ADAPTERS:
        raise ValueError(f"Unknown archive: {name}. Available: {list(ADAPTERS.keys())}")
    return ADAPTERS[name](**kwargs)

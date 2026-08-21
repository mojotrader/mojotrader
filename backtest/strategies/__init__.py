"""Strategy implementations ported from the Pine sources in ``pinescript/``.

Each module names the Pine file it mirrors. When you change one side, change
the other: a silent divergence between the Pine and the Python is worse than
having no Python port at all, because the backtest keeps producing numbers.
"""

from .orb_fade import ORBFade
from .pdh_pdl import PDHPDLBreakout
from .vwap_reversion import VWAPReversion

REGISTRY = {
    "orb_fade": ORBFade,
    "pdh_pdl": PDHPDLBreakout,
    "vwap_reversion": VWAPReversion,
}

__all__ = ["ORBFade", "PDHPDLBreakout", "VWAPReversion", "REGISTRY"]

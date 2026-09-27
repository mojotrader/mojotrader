"""Strategy registry. Add a new strategy class here and it appears in the dashboard."""
from .orbib import ORBIB

STRATEGIES = {cls.name: cls for cls in (ORBIB,)}

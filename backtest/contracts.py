"""Contract specifications.

``point_value`` is the currency change per 1.00 move in the index, per contract.
Getting it wrong scales every dollar figure in a report while leaving the point
figures right, so it is worth checking against a known trade before trusting a
P&L number.
"""

from __future__ import annotations

CONTRACTS = {
    "NQ":  {"point_value": 20.0, "tick_size": 0.25, "desc": "E-mini Nasdaq-100"},
    "MNQ": {"point_value": 2.0,  "tick_size": 0.25, "desc": "Micro E-mini Nasdaq-100"},
    "ES":  {"point_value": 50.0, "tick_size": 0.25, "desc": "E-mini S&P 500"},
    "MES": {"point_value": 5.0,  "tick_size": 0.25, "desc": "Micro E-mini S&P 500"},
}

DEFAULT = {"point_value": 1.0, "tick_size": 0.01, "desc": "generic (1 pt = $1)"}


def spec(symbol: str) -> dict:
    """Look up a contract by symbol, tolerating suffixes like ``MNQ1!``."""
    key = symbol.upper().rstrip("!0123456789").split(":")[-1]
    return CONTRACTS.get(key, DEFAULT)

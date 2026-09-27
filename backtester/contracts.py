"""Futures contract specs used for P&L, sizing and IBKR downloads."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSpec:
    symbol: str          # IBKR symbol
    name: str
    point_value: float   # $ per 1.00 point per contract
    tick_size: float
    commission: float    # default $ per contract per side (IBKR tiered, approx.)
    exchange: str = "CME"


CONTRACTS = {
    "MNQ": ContractSpec("MNQ", "Micro E-mini Nasdaq-100", 2.0, 0.25, 0.62),
    "NQ": ContractSpec("NQ", "E-mini Nasdaq-100", 20.0, 0.25, 2.25),
    "MES": ContractSpec("MES", "Micro E-mini S&P 500", 5.0, 0.25, 0.62),
    "ES": ContractSpec("ES", "E-mini S&P 500", 50.0, 0.25, 2.25),
}

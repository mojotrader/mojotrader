"""Strategy parameter definitions. The dashboard builds its input widgets from these."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Param:
    name: str
    label: str
    default: Any
    group: str = "Settings"
    options: list = field(default_factory=list)   # non-empty -> dropdown
    min: float | None = None
    max: float | None = None
    step: float | None = None
    help: str = ""

    @property
    def kind(self) -> str:
        if self.options:
            return "choice"
        if isinstance(self.default, bool):
            return "bool"
        if isinstance(self.default, int):
            return "int"
        if isinstance(self.default, float):
            return "float"
        return "text"


def defaults(params: list[Param]) -> dict:
    return {p.name: p.default for p in params}

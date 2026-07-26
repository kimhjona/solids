"""Records that move between the sheet, the engine, and the email."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .catalog import Food

# How much of it she actually ate.
ATE_VALUES = ("all", "some", "none", "unknown")

# Reactions we recognize. Free text still round-trips, it just lands in "other".
REACTION_NONE = "none"
REACTIONS = (
    "none",
    "hives",
    "splotches",
    "rash",
    "eczema",
    "vomit",
    "diarrhea",
    "swelling",
    "other",
)

# Reactions that mean stop and call someone, not "log it and move on".
SEVERE_REACTIONS = ("swelling", "vomit")

# How confident we are that the food caused it. The whole point of this column
# is that she may just have eczema.
ATTRIBUTION = ("sure", "unsure", "not_food")

_ATE_ALIASES = {
    "all": "all",
    "all of it": "all",
    "yes": "all",
    "y": "all",
    "most": "all",
    "some": "some",
    "a bit": "some",
    "partly": "some",
    "partial": "some",
    "none": "none",
    "no": "none",
    "n": "none",
    "refused": "none",
    "": "unknown",
}

_REACTION_ALIASES = {
    "": "none",
    "no": "none",
    "n": "none",
    "none": "none",
    "yes": "other",
    "y": "other",
    "hive": "hives",
    "hives": "hives",
    "splotch": "splotches",
    "splotches": "splotches",
    "face splotches": "splotches",
    "redness": "rash",
    "red": "rash",
    "rash": "rash",
    "eczema": "eczema",
    "flare": "eczema",
    "eczema flare": "eczema",
    "vomit": "vomit",
    "throw up": "vomit",
    "threw up": "vomit",
    "spit up": "vomit",
    "diarrhea": "diarrhea",
    "loose stool": "diarrhea",
    "swelling": "swelling",
    "swollen": "swelling",
}


def parse_ate(value: str) -> str:
    v = (value or "").strip().lower()
    return _ATE_ALIASES.get(v, "some" if v else "unknown")


def parse_reaction(value: str) -> str:
    v = (value or "").strip().lower()
    if v in _REACTION_ALIASES:
        return _REACTION_ALIASES[v]
    for token, canonical in _REACTION_ALIASES.items():
        if token and token in v:
            return canonical
    return "other" if v else "none"


@dataclass
class LogEntry:
    date: dt.date
    food_name: str
    food: Food | None = None
    ate: str = "unknown"
    reaction: str = REACTION_NONE
    attribution: str = ""
    notes: str = ""
    source: str = "manual"  # manual | form | assumed | intro
    row: int | None = None  # 1-indexed sheet row, for rewriting

    @property
    def had_reaction(self) -> bool:
        return self.reaction not in ("", REACTION_NONE)

    @property
    def counts_as_reaction(self) -> bool:
        """A reaction we should actually act on.

        If whoever logged it said it probably was not the food, we believe them.
        """
        return self.had_reaction and self.attribution != "not_food"

    @property
    def refused(self) -> bool:
        return self.ate == "none"

    @property
    def confirmed(self) -> bool:
        """Did a human actually tell us this happened?"""
        return self.source != "assumed"


def label_with_allergen(food: Food) -> str:
    """"Peanut butter (peanut)", so it is obvious what each food is covering."""
    if not food.allergen:
        return food.name
    from .config import ALLERGEN_LABELS

    return f"{food.name} ({ALLERGEN_LABELS[food.allergen].lower()})"


@dataclass
class PlannedFood:
    food: Food
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0
    is_new: bool = False
    is_rechallenge: bool = False
    # "main" is the food the day is built around. "keeper" is an allergen we are
    # keeping in her diet, usually a spread or a spoonful alongside the main.
    role: str = "main"

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass
class DayPlan:
    date: dt.date
    anchors: list[PlannedFood] = field(default_factory=list)
    secondaries: list[Food] = field(default_factory=list)
    flavor: Food | None = None
    vitamin_c: Food | None = None
    headline: str = ""
    cautions: list[str] = field(default_factory=list)

    @property
    def new_foods(self) -> list[PlannedFood]:
        return [a for a in self.anchors if a.is_new]

    @property
    def new_names(self) -> str:
        return ", ".join(label_with_allergen(a.food) for a in self.new_foods)

    @property
    def mains(self) -> list[PlannedFood]:
        return [a for a in self.anchors if a.role != "keeper"]

    @property
    def keepers(self) -> list[PlannedFood]:
        return [a for a in self.anchors if a.role == "keeper"]

    @property
    def anchor_names(self) -> str:
        return " + ".join(a.food.name for a in self.anchors)

    @property
    def main_names(self) -> str:
        return " + ".join(label_with_allergen(a.food) for a in self.mains)

    @property
    def keeper_names(self) -> str:
        return ", ".join(label_with_allergen(a.food) for a in self.keepers)

    @property
    def secondary_names(self) -> str:
        return ", ".join(f.name for f in self.secondaries)

    def all_foods(self) -> list[Food]:
        extra = [f for f in (self.vitamin_c,) if f is not None]
        return [a.food for a in self.anchors] + extra + list(self.secondaries)

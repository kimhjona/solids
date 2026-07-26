"""The food catalog: what we know about each food, and how to recognize it
when it shows up misspelled in a spreadsheet."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "foods.json"

SOLID_STARTS = "https://solidstarts.com/foods/{slug}/"


@dataclass(frozen=True)
class Food:
    name: str
    slug: str
    category: str
    allergen: str | None = None
    iron: int = 0
    bitter: bool = False
    sweet: bool = False
    min_age: int = 6
    prep: dict = field(default_factory=dict)
    note: str | None = None
    aliases: tuple = ()
    vitamin_c: int = 0    # 0 none, 1 moderate, 2 high
    common: bool = True          # easy to buy at an ordinary grocery store
    anchor: bool = True          # substantial enough to build a meal around
    carrier_needed: bool = False  # has to be mixed into or spread on something
    good_carrier: bool = False    # soft and scoopable enough to carry something

    @property
    def key(self) -> str:
        return normalize(self.name)

    @property
    def url(self) -> str:
        return SOLID_STARTS.format(slug=self.slug)

    @property
    def is_sweet_fruit(self) -> bool:
        return self.category == "fruit" and self.sweet

    @property
    def heme_iron(self) -> bool:
        """Iron from meat, fish and shellfish, absorbed well on its own.

        Egg is animal but its iron is non-heme and poorly absorbed, so it does
        not count.
        """
        return self.category == "protein" and self.allergen != "egg"

    @property
    def plant_iron(self) -> bool:
        """Iron that needs vitamin C alongside it to be absorbed properly."""
        return self.iron >= 1 and not self.heme_iron

    def prep_for(self, band: str) -> str:
        """Prep guidance for an age band, falling back to the nearest younger one."""
        for b in (band, "9", "6", "12"):
            if b in self.prep:
                return self.prep[b]
        return ""


_PARENS = re.compile(r"\([^)]*\)")
_NONWORD = re.compile(r"[^a-z0-9 ]+")


def normalize(name: str) -> str:
    """Fold a free-text food name into something matchable.

    A hand-kept sheet accumulates entries like "Edamame (Soy)", "Cantelope",
    "Broccoli " and "Mango (the pressed snack)", so this has to be forgiving.
    """
    s = name.lower().strip()
    s = _PARENS.sub(" ", s)
    s = _NONWORD.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Singularize the easy cases so "almonds" finds "almond".
    if s.endswith("ies") and len(s) > 4:
        s = s[:-3] + "y"
    elif s.endswith("es") and s[:-2].endswith(("sh", "ch", "s", "x")):
        s = s[:-2]
    elif s.endswith("s") and not s.endswith("ss"):
        s = s[:-1]
    return s


class Catalog:
    def __init__(self, foods: list[Food]):
        self.foods = foods
        self._by_key: dict[str, Food] = {}
        for f in foods:
            self._by_key.setdefault(f.key, f)
            for alias in f.aliases:
                self._by_key.setdefault(normalize(alias), f)
        # Cache misses too, so repeated fuzzy lookups stay cheap.
        self._resolved: dict[str, Food | None] = {}

    def __iter__(self):
        return iter(self.foods)

    def __len__(self) -> int:
        return len(self.foods)

    def get(self, name: str) -> Food | None:
        """Resolve a free-text name to a Food, tolerating spelling drift."""
        if not name or not name.strip():
            return None
        key = normalize(name)
        if key in self._resolved:
            return self._resolved[key]

        food = self._by_key.get(key)
        if food is None:
            # Try the head word: "wheat bread" -> "wheat", "chicken thigh" -> "chicken".
            for candidate in (key.split(" ")[0], " ".join(key.split(" ")[:2])):
                if candidate in self._by_key:
                    food = self._by_key[candidate]
                    break
        if food is None:
            close = difflib.get_close_matches(key, self._by_key.keys(), n=1, cutoff=0.82)
            if close:
                food = self._by_key[close[0]]
        self._resolved[key] = food
        return food

    def by_allergen(self, allergen: str) -> list[Food]:
        return [f for f in self.foods if f.allergen == allergen]

    def eligible(self, age_months: int) -> list[Food]:
        return [f for f in self.foods if f.min_age <= age_months]


@lru_cache(maxsize=1)
def load_catalog(path: Path | None = None) -> Catalog:
    raw = json.loads((path or DATA_PATH).read_text())
    foods = []
    for entry in raw["foods"]:
        foods.append(
            Food(
                name=entry["name"],
                slug=entry["slug"],
                category=entry["category"],
                allergen=entry.get("allergen"),
                iron=entry.get("iron", 0),
                bitter=entry.get("bitter", False),
                sweet=entry.get("sweet", False),
                min_age=entry.get("min_age", 6),
                prep=entry.get("prep", {}),
                note=entry.get("note"),
                aliases=tuple(entry.get("aliases", ())),
                vitamin_c=entry.get("vitamin_c", 0),
                common=entry.get("common", True),
                anchor=entry.get("anchor", True),
                carrier_needed=entry.get("carrier_needed", False),
                good_carrier=entry.get("good_carrier", False),
            )
        )
    return Catalog(foods)

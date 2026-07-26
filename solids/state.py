"""Everything the engine needs to know, derived from the log in one pass."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from .catalog import Catalog, Food
from .config import ALLERGENS, Config
from .model import LogEntry

BIG = 10_000  # stand-in for "never happened"


@dataclass
class FoodHistory:
    food: Food
    offered: int = 0
    first: dt.date | None = None
    last: dt.date | None = None
    refusals: int = 0
    accepted: int = 0
    reactions: list[LogEntry] = field(default_factory=list)

    @property
    def ever_reacted(self) -> bool:
        return bool(self.reactions)

    @property
    def last_reaction(self) -> LogEntry | None:
        return max(self.reactions, key=lambda e: e.date) if self.reactions else None

    @property
    def is_liked(self) -> bool:
        """Eaten willingly at least once, and not mostly refused."""
        return self.accepted > 0 and self.refusals <= self.accepted


@dataclass
class Snapshot:
    today: dt.date
    config: Config
    catalog: Catalog
    entries: list[LogEntry]
    history: dict[str, FoodHistory]
    allergen_last: dict[str, dt.date | None]
    allergen_count_14d: dict[str, int]
    iron_days_7d: int
    bitter_7d: int
    veg_14d: int
    sweet_fruit_14d: int
    distinct_foods: int

    # ---- lookups -------------------------------------------------------

    def hist(self, food: Food) -> FoodHistory | None:
        return self.history.get(food.key)

    def days_since(self, food: Food) -> int:
        h = self.hist(food)
        if not h or not h.last:
            return BIG
        return (self.today - h.last).days

    def times_offered(self, food: Food) -> int:
        h = self.hist(food)
        return h.offered if h else 0

    def is_new(self, food: Food) -> bool:
        return self.times_offered(food) == 0

    def days_since_allergen(self, allergen: str) -> int:
        last = self.allergen_last.get(allergen)
        return BIG if last is None else (self.today - last).days

    def allergen_introduced(self, allergen: str) -> bool:
        return self.allergen_last.get(allergen) is not None

    def allergen_overdue_by(self, allergen: str) -> float:
        """Days past the target interval. Negative means still in the window."""
        gap = self.days_since_allergen(allergen)
        if gap == BIG:
            return BIG
        return gap - self.config.allergen_interval_days

    # ---- goal tracking -------------------------------------------------

    @property
    def veg_ratio(self) -> float:
        if self.sweet_fruit_14d == 0:
            return float("inf") if self.veg_14d else 0.0
        return self.veg_14d / self.sweet_fruit_14d

    @property
    def iron_short(self) -> bool:
        return self.iron_days_7d < self.config.iron_days_per_week

    @property
    def bitter_short(self) -> bool:
        return self.bitter_7d < self.config.bitter_per_week

    @property
    def fruit_heavy(self) -> bool:
        return self.veg_ratio < self.config.veg_to_fruit_ratio

    def overdue_allergens(self) -> list[tuple[str, float]]:
        """Introduced allergens past their interval, worst first."""
        out = []
        for a in ALLERGENS:
            if not self.allergen_introduced(a):
                continue
            over = self.allergen_overdue_by(a)
            if over > 0:
                out.append((a, over))
        return sorted(out, key=lambda t: -t[1])

    def missing_allergens(self) -> list[str]:
        return [a for a in ALLERGENS if not self.allergen_introduced(a)]

    def rechallenge_candidates(self) -> list[FoodHistory]:
        """Foods that reacted once, went quiet, and deserve another careful try."""
        out = []
        for h in self.history.values():
            if not h.ever_reacted:
                continue
            last = h.last_reaction
            if last is None:
                continue
            # Only re-challenge if nothing since the reaction, and enough time passed.
            if (self.today - last.date).days < self.config.rechallenge_gap_days:
                continue
            if h.last and h.last > last.date:
                continue  # already retried successfully
            out.append(h)
        return sorted(out, key=lambda h: h.last_reaction.date)

    def needs_reoffer(self, food: Food) -> bool:
        """Rejected before, not yet given a fair number of tries.

        Acceptance of a new flavor usually takes somewhere between 8 and 15
        exposures, so a refusal is a reason to try again, not to give up.
        """
        h = self.hist(food)
        if not h or h.offered == 0:
            return False
        if h.offered >= self.config.acceptance_target_exposures:
            return False
        if h.refusals == 0:
            return False
        return self.days_since(food) >= self.config.reoffer_gap_days


def build_snapshot(
    entries: list[LogEntry],
    catalog: Catalog,
    config: Config,
    today: dt.date | None = None,
) -> Snapshot:
    today = today or dt.date.today()
    entries = sorted((e for e in entries if e.date <= today), key=lambda e: e.date)

    history: dict[str, FoodHistory] = {}
    allergen_last: dict[str, dt.date | None] = {a: None for a in ALLERGENS}
    allergen_count_14d: dict[str, int] = defaultdict(int)

    iron_days: set[dt.date] = set()
    bitter_7d = 0
    veg_14d = 0
    sweet_fruit_14d = 0

    # One offering per food per day, however many rows it spans.
    seen_food_days: set[tuple[str, dt.date]] = set()

    for e in entries:
        food = e.food or catalog.get(e.food_name)
        if food is None:
            continue
        e.food = food

        day_key = (food.key, e.date)
        first_today = day_key not in seen_food_days
        seen_food_days.add(day_key)

        h = history.get(food.key)
        if h is None:
            h = history[food.key] = FoodHistory(food=food, first=e.date)
        if first_today:
            h.offered += 1
        h.last = e.date if h.last is None or e.date > h.last else h.last
        if h.first is None or e.date < h.first:
            h.first = e.date

        if e.refused:
            h.refusals += 1
        elif e.ate in ("all", "some"):
            h.accepted += 1
        if e.counts_as_reaction:
            h.reactions.append(e)

        # She has to have actually eaten some of it for exposure to count.
        ate_it = e.ate != "none"
        age_days = (today - e.date).days

        if ate_it and food.allergen:
            prev = allergen_last.get(food.allergen)
            if prev is None or e.date > prev:
                allergen_last[food.allergen] = e.date
            if age_days < 14 and first_today:
                allergen_count_14d[food.allergen] += 1

        if ate_it and food.iron >= 1 and age_days < 7:
            iron_days.add(e.date)
        if ate_it and food.bitter and age_days < 7 and first_today:
            bitter_7d += 1
        if ate_it and age_days < 14 and first_today:
            if food.category == "vegetable":
                veg_14d += 1
            elif food.is_sweet_fruit:
                sweet_fruit_14d += 1

    return Snapshot(
        today=today,
        config=config,
        catalog=catalog,
        entries=entries,
        history=history,
        allergen_last=allergen_last,
        allergen_count_14d=dict(allergen_count_14d),
        iron_days_7d=len(iron_days),
        bitter_7d=bitter_7d,
        veg_14d=veg_14d,
        sweet_fruit_14d=sweet_fruit_14d,
        distinct_foods=len(history),
    )

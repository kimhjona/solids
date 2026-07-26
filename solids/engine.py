"""Deciding what to feed her, and being able to say why.

Priorities, highest first:
  1. Iron, because at this age it is the one that actually matters medically.
  2. Allergen rotation, so the nine stay in her diet rather than being ticked off once.
  3. Bitter vegetables, because that window closes and sweet acceptance is free.
  4. Re-offering rejected foods, because acceptance takes 8 to 15 tries.
  5. New foods, toward 100 by her first birthday.

Every recommendation carries the reason that produced it. If the reasons look
wrong to a parent reading the email, the parent is probably right.
"""

from __future__ import annotations

import datetime as dt
import zlib

from .catalog import Catalog, Food
from .config import ALLERGEN_LABELS, ALLERGENS, Config
from .model import DayPlan, LogEntry, PlannedFood
from .state import Snapshot, build_snapshot

# Weights. These are the opinions.
# Above the overdue cap, so introducing the last missing allergen beats
# re-serving one she has already had.
W_MISSING_ALLERGEN = 330.0
W_ALLERGEN_TURN = 150.0
W_IRON = 20.0
W_VITAMIN_C = 18.0
W_BITTER = 26.0
W_VEG_WHEN_FRUIT_HEAVY = 16.0
W_SWEET_FRUIT_PENALTY = 9.0
W_SWEET_FRUIT_EXTRA_PENALTY = 30.0
W_REOFFER = 32.0
W_NEW = 22.0
W_RECHALLENGE = 95.0
W_LIKED = 4.0
W_UNCOMMON = 26.0  # nudge toward things an ordinary grocery store stocks

# Two re-challenges on consecutive days makes both of them uninterpretable.
RECHALLENGE_SPACING_DAYS = 3

BIG_GAP = 1000  # "she has never had this", from state.BIG

# Serving the same thing two days running is fine, three is a rut.
RECENCY_PENALTY = {0: -500.0, 1: -26.0, 2: -9.0}

# What counts as "the main". Dairy, nuts and fruit are things you put on a plate,
# not the reason you made it.
MAIN_CATEGORIES = ("vegetable", "protein", "legume", "grain")


def _allergens_for_day(day: dt.date, cfg: Config) -> list[str]:
    """Which allergens are up today, on an even weekly cycle.

    Nine allergens over seven days at two slots a day gives fourteen turns, so
    each one comes round once or twice a week. The starting point shifts by
    week, so the same allergen is not always on a Monday. No dates involved,
    which is the point: the log only reliably records first exposures.
    """
    year, week, weekday = day.isocalendar()
    slots = cfg.allergen_keepers_per_day
    offset = (week * 7 * slots) % len(ALLERGENS)
    start = (offset + (weekday - 1) * slots) % len(ALLERGENS)
    return [ALLERGENS[(start + i) % len(ALLERGENS)] for i in range(slots)]


def _jitter(food: Food, day: dt.date) -> float:
    """Stable pseudo-random tiebreak so plans rotate instead of locking on."""
    seed = f"{food.key}:{day.isoformat()}".encode()
    return (zlib.crc32(seed) % 1000) / 1000.0 * 6.0


def score_food(
    food: Food,
    snap: Snapshot,
    day: dt.date,
    allow_new: bool,
    include_allergen: bool = True,
) -> tuple[float, list[str]]:
    """Score a single food for a single day. Returns (score, reasons).

    include_allergen is False when scoring for the main slot. Nine allergens on a
    twice-weekly rotation would otherwise fill every slot forever and the
    vegetables would never get a look in.
    """
    cfg = snap.config
    reasons: list[str] = []
    score = 0.0

    is_new = snap.is_new(food)
    if is_new and not allow_new:
        return (-1e6, [])

    # --- allergens ---
    #
    # Repeat feedings mostly do not get written down, only first exposures, so
    # "days since last" is not a number worth trusting. We rotate all nine
    # evenly instead and never call anything late.
    if food.allergen and include_allergen:
        label = ALLERGEN_LABELS[food.allergen]
        if not snap.allergen_introduced(food.allergen):
            bonus = W_MISSING_ALLERGEN
            if cfg.prefer_weekend_for_new_allergen and day.weekday() < 5:
                # Save first introductions for a day when someone is home and
                # unhurried. Damped hard, so it genuinely waits for the weekend.
                bonus *= 0.05
            score += bonus
            reasons.append(f"{label} is the last common allergen not tried yet")
        elif food.allergen in _allergens_for_day(day, cfg):
            score += W_ALLERGEN_TURN
            reasons.append(f"{label}'s turn in this week's rotation")

    # --- iron ---
    if food.iron >= 1 and snap.iron_short:
        score += W_IRON * food.iron
        reasons.append(
            f"iron-rich, and she has had iron on {snap.iron_days_7d} of the last 7 days"
        )

    # --- bitter flavors ---
    if food.bitter and snap.bitter_short:
        score += W_BITTER
        reasons.append(
            f"bitter, and she has had {snap.bitter_7d} bitter foods this week "
            f"against a target of {cfg.bitter_per_week}"
        )

    # --- vegetables over fruit ---
    if food.category == "vegetable" and snap.fruit_heavy:
        score += W_VEG_WHEN_FRUIT_HEAVY
    if food.is_sweet_fruit:
        score -= W_SWEET_FRUIT_PENALTY
        if snap.fruit_heavy:
            score -= W_SWEET_FRUIT_EXTRA_PENALTY

    # --- re-offering things she turned down ---
    if snap.needs_reoffer(food):
        h = snap.hist(food)
        score += W_REOFFER
        reasons.append(
            f"she turned this down before, this would be try {h.offered + 1} "
            f"of about {cfg.acceptance_target_exposures}"
        )

    # --- novelty ---
    if is_new:
        score += W_NEW
        reasons.append(f"new food, she is at {snap.distinct_foods} of {cfg.new_foods_by_first_birthday}")
    else:
        h = snap.hist(food)
        if h and h.is_liked:
            score += W_LIKED

    if not food.common:
        score -= W_UNCOMMON

    # --- do not repeat yesterday's plate ---
    since = snap.days_since(food)
    score += RECENCY_PENALTY.get(since, 0.0)

    return score + _jitter(food, day), reasons


def _reaction_unresolved(snap: Snapshot, food: Food) -> bool:
    """Reacted once, never successfully retried since."""
    h = snap.hist(food)
    if not h or not h.ever_reacted:
        return False
    last = h.last_reaction
    return not (h.last and h.last > last.date)


def _candidates(snap: Snapshot, day: dt.date) -> list[Food]:
    cfg = snap.config
    age = cfg.age_months(day)
    excluded = {name.lower() for name in cfg.excluded}
    out = []
    for food in snap.catalog:
        if food.min_age > age:
            continue
        if food.category in ("flavor", "other"):
            continue  # herbs, spices and cooking fats are additions, not the plan
        if food.name.lower() in excluded:
            continue
        # Foods with an unresolved reaction stay out until the re-challenge is due.
        if _reaction_unresolved(snap, food):
            h = snap.hist(food)
            if (day - h.last_reaction.date).days < cfg.rechallenge_gap_days:
                continue
        out.append(food)
    return out


def _pick_secondaries(
    snap: Snapshot,
    day: dt.date,
    taken: set[str],
    n: int,
    avoid: set[str] | None = None,
) -> list[Food]:
    """Familiar, low-effort options so nobody has to shop to follow the plan.

    Rotated by how long since she last had them, and spread across categories.
    """
    avoid = avoid or set()
    pool = []
    for food in _candidates(snap, day):
        if food.key in taken:
            continue
        h = snap.hist(food)
        if not h or not h.is_liked:
            continue
        if snap.days_since(food) < 1:
            continue
        # Anything awaiting a careful re-challenge is not a casual option.
        if _reaction_unresolved(snap, food):
            continue
        pool.append(food)

    # Longest gap first, so the familiar rotation keeps moving. Anything we
    # suggested in the last couple of days goes to the back.
    pool.sort(key=lambda f: (f.key in avoid, -snap.days_since(f), f.name))

    chosen: list[Food] = []
    used_categories: dict[str, int] = {}
    for food in pool:
        cap = 2 if food.category == "vegetable" else 1
        if used_categories.get(food.category, 0) >= cap:
            continue
        if food.is_sweet_fruit and sum(1 for c in chosen if c.is_sweet_fruit) >= 1:
            continue
        chosen.append(food)
        used_categories[food.category] = used_categories.get(food.category, 0) + 1
        if len(chosen) >= n:
            break
    return chosen


def _pick_vitamin_c(snap: Snapshot, day: dt.date, plan_foods: list[Food]) -> Food | None:
    """Something with vitamin C to go with plant iron.

    Iron from beans, lentils, grains, greens and seeds is non-heme, and eaten
    on its own very little of it is absorbed. Vitamin C alongside it makes a
    large difference. Iron from meat and fish does not need the help.
    """
    if not any(f.plant_iron for f in plan_foods):
        return None
    if any(f.vitamin_c >= 1 for f in plan_foods):
        return None  # already covered by something on the plate

    candidates = [
        f for f in _candidates(snap, day)
        if f.vitamin_c >= 2 and f.key not in {p.key for p in plan_foods}
    ]
    if not candidates:
        return None
    # Prefer vegetables over fruit, then rotate by how long since she had it.
    candidates.sort(
        key=lambda f: (f.is_sweet_fruit, -snap.days_since(f), _jitter(f, day))
    )
    return candidates[0]


def _pick_flavor(snap: Snapshot, day: dt.date) -> Food | None:
    """One herb or spice a day. Cheap variety, and it makes food taste like food."""
    flavors = [f for f in snap.catalog if f.category == "flavor" and f.min_age <= snap.config.age_months(day)]
    if not flavors:
        return None
    flavors.sort(key=lambda f: (-snap.days_since(f), _jitter(f, day)))
    return flavors[0]


def plan_day(
    snap: Snapshot,
    day: dt.date,
    allow_rechallenge: bool = True,
    avoid_secondaries: set[str] | None = None,
) -> DayPlan:
    cfg = snap.config
    plan = DayPlan(date=day)

    # A re-challenge is the only thing happening that day, so a reaction means something.
    rechallenges = []
    if allow_rechallenge:
        for h in snap.rechallenge_candidates():
            if h.food.min_age > cfg.age_months(day):
                continue
            # An allergen re-challenge belongs on a day when someone is home for it.
            if h.food.allergen and cfg.prefer_weekend_for_new_allergen and day.weekday() < 5:
                continue
            rechallenges.append(h)
    doing_rechallenge = bool(rechallenges)

    allow_new = cfg.max_new_foods_per_day > 0 and not doing_rechallenge
    pool = [f for f in _candidates(snap, day) if f.anchor]

    def rank(include_allergen: bool, categories: tuple | None = None) -> list[PlannedFood]:
        out = []
        for food in pool:
            if categories and food.category not in categories:
                continue
            s, reasons = score_food(
                food, snap, day, allow_new=allow_new, include_allergen=include_allergen
            )
            if s <= -1e5:
                continue
            out.append(
                PlannedFood(food=food, reasons=reasons, score=s, is_new=snap.is_new(food))
            )
        return sorted(out, key=lambda p: -p.score)

    anchors: list[PlannedFood] = []
    used_categories: set[str] = set()
    used_allergens: set[str] = set()
    new_count = 0
    solo = False  # a first-time allergen or a re-challenge owns the whole day

    def take(cand: PlannedFood, role: str) -> None:
        nonlocal new_count, solo
        cand.role = role
        anchors.append(cand)
        used_categories.add(cand.food.category)
        if cand.food.allergen:
            used_allergens.add(cand.food.allergen)
        if cand.is_new:
            new_count += 1
        if cand.is_rechallenge or (
            cand.food.allergen and not snap.allergen_introduced(cand.food.allergen)
        ):
            solo = True

    def acceptable(cand: PlannedFood, same_category_ok: bool = False) -> bool:
        if solo:
            return False
        food = cand.food
        if not same_category_ok and food.category in used_categories:
            return False
        if food.allergen and food.allergen in used_allergens:
            return False
        if cand.is_new and new_count >= cfg.max_new_foods_per_day:
            return False
        # A first-time allergen goes out on its own, or a reaction tells us nothing.
        if food.allergen and not snap.allergen_introduced(food.allergen):
            return not anchors and not new_count
        return True

    # --- keepers: the allergens that need to stay in her diet ---
    if doing_rechallenge:
        h = rechallenges[0]
        last = h.last_reaction
        gap = (day - last.date).days
        take(
            PlannedFood(
                food=h.food,
                reasons=[
                    f"re-challenge, {gap} days since the {last.reaction} on "
                    f"{last.date.strftime('%-m/%-d')}"
                ],
                score=W_RECHALLENGE,
                is_rechallenge=True,
            ),
            role="rechallenge",
        )
    else:
        needed = set(_allergens_for_day(day, cfg)) | set(snap.missing_allergens())
        ranked = rank(include_allergen=True)
        for cand in ranked:
            if len(anchors) >= cfg.allergen_keepers_per_day:
                break
            # Already-familiar allergens can share a plate, they are not tests.
            if cand.food.allergen in needed and acceptable(cand, same_category_ok=True):
                first_time = not snap.allergen_introduced(cand.food.allergen)
                take(cand, role="main" if first_time else "keeper")

    # --- the main: the food the day is actually built around ---
    if not solo:
        for cand in rank(include_allergen=False, categories=MAIN_CATEGORIES):
            if acceptable(cand):
                if not cand.reasons:
                    # Nothing was urgent about it, which is itself worth saying
                    # rather than presenting a food with no explanation.
                    gap = snap.days_since(cand.food)
                    cand.reasons.append(
                        "nothing urgent today, and she has not had it in "
                        f"{gap} days" if gap < BIG_GAP else "keeping the variety up"
                    )
                take(cand, role="main")
                break

    # Lead with the main, or with the thing we are re-testing.
    order = {"rechallenge": 0, "main": 1, "keeper": 2}
    anchors.sort(key=lambda a: order.get(a.role, 3))
    plan.anchors = anchors
    taken = {a.food.key for a in anchors}
    plan.secondaries = _pick_secondaries(
        snap, day, taken, cfg.secondaries_per_day, avoid_secondaries
    )
    plan.vitamin_c = _pick_vitamin_c(snap, day, [a.food for a in anchors])
    plan.flavor = _pick_flavor(snap, day)
    plan.headline = _headline(snap, plan)
    plan.cautions = _cautions(snap, plan, day)
    return plan


def _headline(snap: Snapshot, plan: DayPlan) -> str:
    bits = []
    for a in plan.anchors:
        if a.reasons:
            bits.append(a.reasons[0])
    return bits[0] if bits else "Keeping the rotation moving."


def _cautions(snap: Snapshot, plan: DayPlan, day: dt.date) -> list[str]:
    cfg = snap.config
    band = cfg.age_band(day)
    out: list[str] = []

    for a in plan.anchors:
        food = a.food
        if a.is_rechallenge:
            out.append(
                f"{food.name} is a re-challenge. Offer a small amount early in the day, "
                f"at home, and watch for two hours. Nothing else new today."
            )
        if food.allergen and not snap.allergen_introduced(food.allergen):
            out.append(
                f"{food.name} is her first {ALLERGEN_LABELS[food.allergen].lower()}. "
                f"Offer it in the morning, start with a taste, wait 10 minutes, "
                f"then the rest. Nothing else new today."
            )
        if food.note:
            out.append(f"{food.name}: {food.note}")

    if band == "6":
        out.append(
            "She is still in the palmar-grasp stage, so finger-length strips she can "
            "hold with her fist, not small pieces."
        )
    return out


def plan_ahead(
    entries: list[LogEntry],
    catalog: Catalog,
    config: Config,
    start: dt.date,
    days: int,
) -> list[DayPlan]:
    """Plan several days by assuming each day's anchors get eaten.

    The lookahead exists so the Saturday grocery list is real. It will drift from
    what actually happens, which is fine, it is regenerated every morning.
    """
    working = list(entries)
    plans: list[DayPlan] = []
    last_rechallenge: dt.date | None = None
    recent_secondaries: list[set[str]] = []

    for i in range(days):
        day = start + dt.timedelta(days=i)
        snap = build_snapshot(working, catalog, config, today=day)

        allow_rc = (
            last_rechallenge is None
            or (day - last_rechallenge).days >= RECHALLENGE_SPACING_DAYS
        )
        avoid = set().union(*recent_secondaries[-2:]) if recent_secondaries else set()

        plan = plan_day(snap, day, allow_rechallenge=allow_rc, avoid_secondaries=avoid)
        plans.append(plan)
        if any(a.is_rechallenge for a in plan.anchors):
            last_rechallenge = day
        recent_secondaries.append({f.key for f in plan.secondaries})
        # Only the anchors are assumed eaten. The secondaries are options, so
        # counting them would inflate the allergen and iron tallies.
        for anchor in plan.anchors:
            working.append(
                LogEntry(
                    date=day,
                    food_name=anchor.food.name,
                    food=anchor.food,
                    ate="all",
                    source="assumed",
                )
            )
    return plans

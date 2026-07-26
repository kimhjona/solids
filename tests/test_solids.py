from __future__ import annotations

import datetime as dt

import pytest

from solids.catalog import load_catalog, normalize
from solids.config import Config
from solids.engine import plan_ahead, plan_day
from solids.model import LogEntry, parse_ate, parse_reaction
from solids.sheet import parse_sheet_date
from solids.state import build_snapshot

CATALOG = load_catalog()
CFG = Config()
TODAY = dt.date(2026, 7, 25)


def entry(day: str, food: str, ate="all", reaction="none", attribution="", source="intro"):
    return LogEntry(
        date=dt.date.fromisoformat(day),
        food_name=food,
        food=CATALOG.get(food),
        ate=parse_ate(ate),
        reaction=parse_reaction(reaction),
        attribution=attribution,
        source=source,
    )


def snap(entries, today=TODAY, cfg=CFG):
    return build_snapshot(entries, CATALOG, cfg, today=today)


# ------------------------------------------------------------- catalog ----

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Edamame (Soy)", "Edamame"),
        ("Dairy (yogurt)", "Yogurt"),
        ("Wheat (bread)", "Wheat bread"),
        ("Tahini (sesame)", "Tahini"),
        ("Mango (pressed meta snack)", "Mango"),
        ("Cantelope", "Cantaloupe"),
        ("Aspargus", "Asparagus"),
        ("Broccoli ", "Broccoli"),
        ("pistachio", "Pistachio"),
        ("Almonds", "Almond butter"),
        ("Steak", "Beef"),
        ("Green beans", "Green beans"),
        ("Chia seeds", "Chia seeds"),
    ],
)
def test_catalog_resolves_the_names_actually_in_the_sheet(raw, expected):
    """The tracker sheet is hand-typed, so matching has to be forgiving."""
    food = CATALOG.get(raw)
    assert food is not None, f"could not resolve {raw!r}"
    assert food.name == expected


def test_unknown_food_returns_none():
    assert CATALOG.get("plutonium") is None
    assert CATALOG.get("") is None


def test_every_food_has_prep_for_her_current_age():
    for food in CATALOG:
        if food.min_age <= 7:
            assert food.prep_for("6"), f"{food.name} has no 6-month prep"


def test_normalize_strips_parentheticals_and_plurals():
    assert normalize("Edamame (Soy)") == "edamame"
    assert normalize("Almonds") == "almond"


# ---------------------------------------------------------------- dates ----

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5/17", dt.date(2026, 5, 17)),
        ("2026-05-17", dt.date(2026, 5, 17)),
        ("5/17/26", dt.date(2026, 5, 17)),
        ("12/28", dt.date(2025, 12, 28)),  # rolls back rather than landing in the future
        ("", None),
        ("not a date", None),
    ],
)
def test_parse_sheet_date(raw, expected):
    assert parse_sheet_date(raw, today=TODAY) == expected


# ---------------------------------------------------------------- state ----

def test_allergen_gap_uses_the_most_recent_exposure():
    s = snap([entry("2026-07-01", "Egg"), entry("2026-07-20", "Egg")])
    assert s.days_since_allergen("egg") == 5
    assert s.allergen_introduced("egg")
    assert not s.allergen_introduced("shellfish")


def test_refused_food_does_not_count_as_an_exposure():
    s = snap([entry("2026-07-24", "Egg", ate="none")])
    assert not s.allergen_introduced("egg")


def test_reaction_marked_not_food_is_believed():
    """She may just have eczema, so the parent's judgement wins."""
    reacted = snap([entry("2026-07-20", "Strawberry", reaction="hives")])
    assert reacted.hist(CATALOG.get("Strawberry")).ever_reacted

    dismissed = snap(
        [entry("2026-07-20", "Strawberry", reaction="hives", attribution="not_food")]
    )
    assert not dismissed.hist(CATALOG.get("Strawberry")).ever_reacted


def test_same_food_twice_in_one_day_counts_once():
    s = snap([entry("2026-07-20", "Broccoli"), entry("2026-07-20", "Broccoli")])
    assert s.times_offered(CATALOG.get("Broccoli")) == 1


def test_veg_ratio_ignores_avocado_but_counts_banana():
    s = snap([entry("2026-07-20", "Broccoli"), entry("2026-07-21", "Avocado")])
    assert s.sweet_fruit_14d == 0
    s2 = snap([entry("2026-07-20", "Broccoli"), entry("2026-07-21", "Banana")])
    assert s2.sweet_fruit_14d == 1


def test_rechallenge_waits_for_the_gap_then_becomes_due():
    early = snap([entry("2026-07-20", "Raspberry", reaction="hives")])
    assert early.rechallenge_candidates() == []

    due = snap([entry("2026-06-01", "Raspberry", reaction="hives")])
    assert [h.food.name for h in due.rechallenge_candidates()] == ["Raspberry"]


def test_successful_retry_clears_the_rechallenge():
    s = snap(
        [
            entry("2026-06-01", "Raspberry", reaction="hives"),
            entry("2026-07-01", "Raspberry"),
        ]
    )
    assert s.rechallenge_candidates() == []


def test_reoffer_after_a_refusal_but_not_forever():
    refused = snap([entry("2026-07-01", "Kale", ate="none")])
    assert refused.needs_reoffer(CATALOG.get("Kale"))

    # Once she has had a fair number of tries, stop pushing it.
    many = snap(
        [entry(f"2026-0{m}-0{d}", "Kale", ate="none") for m in (5, 6, 7) for d in (1, 3, 5, 7)]
    )
    assert not many.needs_reoffer(CATALOG.get("Kale"))


def test_reoffer_waits_a_few_days():
    s = snap([entry("2026-07-24", "Kale", ate="none")])
    assert not s.needs_reoffer(CATALOG.get("Kale"))


# --------------------------------------------------------------- engine ----

BASELINE = [
    entry("2026-07-20", "Egg"),
    entry("2026-07-20", "Yogurt"),
    entry("2026-07-21", "Peanut butter"),
    entry("2026-07-21", "Broccoli"),
    entry("2026-07-22", "Wheat bread"),
    entry("2026-07-22", "Tofu"),
    entry("2026-07-23", "Tahini"),
    entry("2026-07-23", "Salmon"),
    entry("2026-07-24", "Cashew"),
    entry("2026-07-24", "Carrot"),
]


def test_at_most_one_new_food_a_day():
    """So that if she reacts, you know to what."""
    for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 21):
        assert sum(1 for a in plan.anchors if a.is_new) <= CFG.max_new_foods_per_day


def test_a_first_time_allergen_goes_out_alone():
    """Shellfish is untried, so the day it arrives it is the only thing on trial."""
    for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 21):
        if any(a.food.allergen == "shellfish" for a in plan.anchors):
            assert len(plan.anchors) == 1, plan.anchor_names
            return  # only the first appearance is a test
    pytest.fail("shellfish was never introduced")


def test_first_allergen_introduction_lands_on_a_weekend():
    for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 21):
        if any(a.food.allergen == "shellfish" for a in plan.anchors):
            assert plan.date.weekday() >= 5
            break
    else:
        pytest.fail("shellfish was never introduced")


def test_rechallenge_day_introduces_nothing_new():
    entries = BASELINE + [entry("2026-06-01", "Blueberry", reaction="hives")]
    for plan in plan_ahead(entries, CATALOG, CFG, TODAY, 14):
        if any(a.is_rechallenge for a in plan.anchors):
            assert not any(a.is_new for a in plan.anchors)
            assert len(plan.anchors) == 1


def test_rechallenges_are_spaced_apart():
    entries = BASELINE + [
        entry("2026-06-01", "Blueberry", reaction="hives"),
        entry("2026-06-02", "Raspberry", reaction="hives"),
        entry("2026-06-03", "Strawberry", reaction="hives"),
    ]
    days = [
        p.date
        for p in plan_ahead(entries, CATALOG, CFG, TODAY, 21)
        if any(a.is_rechallenge for a in p.anchors)
    ]
    gaps = [(days[i + 1] - days[i]).days for i in range(len(days) - 1)]
    assert all(g >= 3 for g in gaps), days


def test_food_awaiting_rechallenge_is_never_a_casual_suggestion():
    entries = BASELINE + [entry("2026-07-24", "Blueberry", reaction="hives")]
    plan = plan_day(snap(entries), TODAY)
    assert "Blueberry" not in plan.secondary_names
    assert "Blueberry" not in plan.anchor_names


def test_the_main_is_a_real_food_not_a_condiment():
    from solids.engine import MAIN_CATEGORIES

    for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 21):
        for a in plan.mains:
            if a.is_rechallenge:
                continue
            assert a.food.category in MAIN_CATEGORIES, a.food.name
            assert a.food.anchor


def test_vegetables_dominate_the_mains_over_a_month():
    """The whole point is that she is not a picky eater, so vegetables lead."""
    mains = [
        a.food for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 28)
        for a in plan.mains if not a.is_rechallenge
    ]
    veg = sum(1 for f in mains if f.category == "vegetable")
    assert veg / len(mains) > 0.4, f"only {veg} of {len(mains)} mains were vegetables"


def test_every_allergen_stays_in_rotation_over_a_month():
    from solids.config import ALLERGENS

    seen: dict[str, list[dt.date]] = {a: [] for a in ALLERGENS}
    for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 35):
        for a in plan.anchors:
            if a.food.allergen:
                seen[a.food.allergen].append(plan.date)
    for allergen, days in seen.items():
        assert len(days) >= 4, f"{allergen} only appeared {len(days)} times in 5 weeks"
        gaps = [(days[i + 1] - days[i]).days for i in range(len(days) - 1)]
        assert max(gaps) <= 10, f"{allergen} had a {max(gaps)} day gap"


def test_same_food_is_not_planned_two_days_running():
    """Secondaries may repeat, they are only options. The plan itself should not."""
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 21)
    for a, b in zip(plans, plans[1:]):
        overlap = {x.food.key for x in a.anchors} & {x.food.key for x in b.anchors}
        assert not overlap, f"{a.date} and {b.date} share {overlap}"


def test_reasons_are_always_given_for_anchors():
    """If it cannot say why, it should not be recommending it."""
    for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 14):
        for a in plan.anchors:
            assert a.reasons, f"{a.food.name} on {plan.date} had no reason"


def test_plan_survives_an_empty_history():
    plans = plan_ahead([], CATALOG, CFG, TODAY, 7)
    assert len(plans) == 7
    assert all(p.anchors for p in plans)


def test_nothing_below_her_age_is_recommended():
    for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 21):
        for food in plan.all_foods():
            assert food.min_age <= CFG.age_months(plan.date), food.name


def test_honey_is_never_recommended_before_one():
    names = {
        f.name
        for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 60)
        for f in plan.all_foods()
    }
    assert "Honey" not in names


def test_excluded_foods_are_respected():
    cfg = Config(excluded=["Broccoli"])
    names = {
        f.name
        for plan in plan_ahead(BASELINE, CATALOG, cfg, TODAY, 21)
        for f in plan.all_foods()
    }
    assert "Broccoli" not in names


# ---------------------------------------------------------------- render ----

# ------------------------------------------------------- weekly rotation ----

def test_every_allergen_comes_up_in_a_single_week():
    """The rotation is even and ignores dates, so a week covers all nine."""
    from solids.config import ALLERGENS
    from solids.engine import _allergens_for_day

    for start_offset in range(14):
        start = TODAY + dt.timedelta(days=start_offset)
        week = {
            a
            for i in range(7)
            for a in _allergens_for_day(start + dt.timedelta(days=i), CFG)
        }
        assert set(ALLERGENS) == week, f"week from {start} missed {set(ALLERGENS) - week}"


def test_the_rotation_does_not_depend_on_what_was_logged():
    from solids.engine import _allergens_for_day

    day = dt.date(2026, 8, 3)
    assert _allergens_for_day(day, CFG) == _allergens_for_day(day, Config())


def test_nothing_is_ever_described_as_overdue():
    """Repeat feedings are not written down, so lateness cannot be claimed."""
    from solids.render import render_status_text, render_week_text

    s = snap(BASELINE)
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 7)
    text = render_status_text(s) + render_week_text(plans, s)
    for plan in plans:
        for a in plan.anchors:
            text += a.reason
    # "days ago" is fine when it refers to a reaction date, which is recorded
    # reliably. What must never appear is an allergen being called late.
    for word in ("overdue", "target is every", "last landed", "days out"):
        assert word not in text.lower(), f"found {word!r} in the output"


# ------------------------------------------------------------- vitamin C ----

def test_plant_iron_gets_a_vitamin_c_partner():
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 21)
    checked = 0
    for plan in plans:
        foods = [a.food for a in plan.anchors]
        if not any(f.plant_iron for f in foods):
            continue
        checked += 1
        has_c = any(f.vitamin_c >= 1 for f in foods) or plan.vitamin_c is not None
        assert has_c, f"{plan.date} planned {plan.anchor_names} with no vitamin C"
    assert checked, "no plant-iron days in the sample"


def test_heme_iron_needs_no_partner():
    """Iron from meat and fish is absorbed fine on its own."""
    assert CATALOG.get("Beef").heme_iron
    assert CATALOG.get("Sardine").heme_iron
    assert not CATALOG.get("Egg").heme_iron   # animal, but non-heme iron
    assert CATALOG.get("Lentils").plant_iron
    assert not CATALOG.get("Beef").plant_iron


# ------------------------------------------------------------- labelling ----

def test_foods_are_labelled_with_the_allergen_they_cover():
    from solids.model import label_with_allergen

    assert label_with_allergen(CATALOG.get("Peanut butter")) == "Peanut butter (peanut)"
    assert label_with_allergen(CATALOG.get("Tahini")) == "Tahini (sesame)"
    assert label_with_allergen(CATALOG.get("Broccoli")) == "Broccoli"


def test_new_foods_are_called_out_by_name():
    for plan in plan_ahead(BASELINE, CATALOG, CFG, TODAY, 14):
        if plan.new_foods:
            assert plan.new_names
            assert plan.new_foods[0].food.name in plan.new_names
        else:
            assert plan.new_names == ""


def test_week_email_renders_a_row_per_day():
    from solids.render import render_week_html

    s = snap(BASELINE)
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 7)
    html = render_week_html(plans, s, "https://example.com")
    assert html.count("<tr") >= 8  # header plus seven days
    assert "<script" not in html


def test_week_table_has_borders_inline():
    """Some mail clients drop <style>, and the grid is the point of the table."""
    from solids.render import render_week_html

    s = snap(BASELINE)
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 7)
    html = render_week_html(plans, s)
    assert html.count("border:1px solid") >= 8


def test_every_food_in_the_table_links_to_solid_starts():
    from solids.render import render_week_html

    s = snap(BASELINE)
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 7)
    html = render_week_html(plans, s)
    for plan in plans:
        for food in [a.food for a in plan.anchors] + (
            [plan.vitamin_c] if plan.vitamin_c else []
        ):
            assert food.url in html, f"{food.name} was not linked"


def test_table_carries_the_preparation_for_her_age_band():
    from solids.render import render_week_html

    s = snap(BASELINE)
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 7)
    html = render_week_html(plans, s)
    band = CFG.age_band(plans[0].date)
    for plan in plans:
        for a in plan.anchors:
            prep = a.food.prep_for(band)
            if prep:
                # Rendered escaped, so compare against the escaped form.
                import html as _h

                assert _h.escape(prep, quote=True) in html, a.food.name


def test_the_email_no_longer_carries_a_shopping_list():
    from solids.render import render_week_html

    s = snap(BASELINE)
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 7)
    html = render_week_html(plans, s)
    assert "Shopping list" not in html
    assert "Groceries" not in html


def test_email_escapes_food_notes():
    from solids.render import render_week_html

    s = snap(BASELINE)
    plans = plan_ahead(BASELINE, CATALOG, CFG, TODAY, 3)
    plans[0].cautions.append("<img src=x onerror=alert(1)>")
    html = render_week_html(plans, s)
    assert "<img src=x" not in html
    assert "&lt;img" in html

"""Turning plans into something a tired parent can read at 6am."""

from __future__ import annotations

import datetime as dt
import html

from .config import ALLERGEN_LABELS, Config
from .model import DayPlan
from .state import Snapshot

SHEET_URL = "https://docs.google.com/spreadsheets/d/{sid}/edit#gid={gid}"


def sheet_link(spreadsheet_id: str, gid: int | None = None, cell: str | None = None) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    if gid is not None:
        url += f"#gid={gid}"
        if cell:
            url += f"&range={cell}"
    return url


def pretty_date(d: dt.date) -> str:
    return d.strftime("%A, %B ") + str(d.day)


def short_date(d: dt.date) -> str:
    return d.strftime("%a ") + f"{d.month}/{d.day}"


def age_string(cfg: Config, on: dt.date) -> str:
    months = cfg.age_months(on)
    anniversary = cfg.birth_date.replace(
        year=cfg.birth_date.year + (cfg.birth_date.month + months - 1) // 12,
        month=(cfg.birth_date.month + months - 1) % 12 + 1,
    )
    days = (on - anniversary).days
    if days == 0:
        return f"{months} months today"
    return f"{months} months, {days} day{'s' if days != 1 else ''}"


# ---------------------------------------------------------------- status ----

def status_lines(snap: Snapshot) -> list[tuple[str, str, bool]]:
    """(label, value, is_a_problem) for the dashboard."""
    cfg = snap.config
    out: list[tuple[str, str, bool]] = []

    out.append((
        "Iron",
        f"{snap.iron_days_7d} of the last 7 days",
        snap.iron_short,
    ))

    # Nothing here counts days since an allergen was last eaten. Repeat feedings
    # do not get written down, so that number would be fiction. First exposures
    # do get written down, so "not yet tried" is worth showing.
    missing = snap.missing_allergens()
    if missing:
        names = ", ".join(ALLERGEN_LABELS[a] for a in missing)
        out.append(("Allergens not yet tried", names, True))
    else:
        out.append(("Allergens", "all nine tried, and on the weekly rotation", False))

    ratio = snap.veg_ratio
    ratio_s = "no fruit" if ratio == float("inf") else f"{ratio:.1f} to 1"
    out.append((
        "Vegetables vs fruit",
        f"{ratio_s} over 14 days ({snap.veg_14d} veg, {snap.sweet_fruit_14d} fruit)",
        snap.fruit_heavy,
    ))

    out.append((
        "Bitter foods",
        f"{snap.bitter_7d} this week, target {cfg.bitter_per_week}",
        snap.bitter_short,
    ))

    out.append((
        "Foods tried",
        f"{snap.distinct_foods} of {cfg.new_foods_by_first_birthday} before her birthday",
        False,
    ))
    return out


# ------------------------------------------------------------------ text ----

def render_day_text(plan: DayPlan, snap: Snapshot) -> str:
    cfg = snap.config
    band = cfg.age_band(plan.date)
    lines = [f"{pretty_date(plan.date)}  ({age_string(cfg, plan.date)})", ""]

    if plan.main_names:
        label = "RE-TRY: " if any(a.is_rechallenge for a in plan.mains) else "MAIN:   "
        lines.append(f"  {label} {plan.main_names}")
    if plan.keeper_names:
        lines.append(f"  KEEP IN: {plan.keeper_names}")

    for a in plan.anchors:
        lines.append("")
        tag = "new  " if a.is_new else ("retry" if a.is_rechallenge else "     ")
        lines.append(f"  {tag} {a.food.name}")
        prep = a.food.prep_for(band)
        if prep:
            lines.append(f"        how: {prep}")
        if a.reasons:
            lines.append(f"        why: {a.reason}")
        lines.append(f"        {a.food.url}")

    if plan.new_names:
        lines.append("")
        lines.append(f"  NEW TODAY: {plan.new_names}")
    if plan.vitamin_c:
        lines.append(
            f"  SERVE WITH: {plan.vitamin_c.name}, for the vitamin C. "
            f"It markedly increases how much of the iron she absorbs."
        )
    if plan.secondaries:
        lines.append("")
        lines.append(f"  ALSO GOOD (any of these): {plan.secondary_names}")
    if plan.flavor:
        lines.append(f"  FLAVOR: {plan.flavor.name}, {plan.flavor.prep_for(band).lower()}")

    if plan.cautions:
        lines.append("")
        lines.append("  WATCH:")
        for c in plan.cautions:
            lines.append(f"    - {c}")
    return "\n".join(lines)


def render_status_text(snap: Snapshot) -> str:
    lines = [f"Where {snap.config.baby_name} stands, {pretty_date(snap.today)}", ""]
    width = max(len(label) for label, _, _ in status_lines(snap))
    for label, value, problem in status_lines(snap):
        mark = "!" if problem else " "
        lines.append(f"  {mark} {label.ljust(width)}   {value}")

    rc = snap.rechallenge_candidates()
    if rc:
        lines.append("")
        lines.append("  Due for a careful re-try:")
        for h in rc:
            last = h.last_reaction
            gap = (snap.today - last.date).days
            lines.append(
                f"    - {h.food.name}: {last.reaction} on "
                f"{last.date.month}/{last.date.day}, {gap} days ago"
            )
    return "\n".join(lines)


def render_grocery_text(plans: list[DayPlan], snap: Snapshot) -> str:
    by_category: dict[str, list[str]] = {}
    seen: set[str] = set()
    for plan in plans:
        for food in plan.all_foods():
            if food.key in seen:
                continue
            seen.add(food.key)
            by_category.setdefault(food.category, []).append(food.name)

    start, end = plans[0].date, plans[-1].date
    lines = [f"Groceries for {short_date(start)} through {short_date(end)}", ""]
    for category in ("protein", "vegetable", "fruit", "legume", "grain", "dairy", "nut_seed", "other"):
        if category not in by_category:
            continue
        label = category.replace("_", " ").title()
        lines.append(f"  {label}")
        for name in sorted(by_category[category]):
            lines.append(f"    [ ] {name}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ------------------------------------------------------------------ html ----

_CSS = """
body { margin:0; padding:0; background:#f4f2ee; }
.wrap { max-width:600px; margin:0 auto; padding:20px 16px 40px;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  color:#20201d; line-height:1.5; }
.card { background:#ffffff; border-radius:14px; padding:20px; margin-bottom:14px;
  border:1px solid #e5e1da; }
h1 { font-size:20px; margin:0 0 2px; letter-spacing:-0.01em; }
.sub { color:#6b6660; font-size:13px; margin:0 0 18px; }
h2 { font-size:12px; text-transform:uppercase; letter-spacing:0.07em;
  color:#8a8379; margin:0 0 12px; font-weight:600; }
.main-food { font-size:22px; font-weight:600; margin:0 0 2px; letter-spacing:-0.01em; }
.keeper-food { font-size:16px; font-weight:600; margin:0 0 2px; }
.badge { display:inline-block; font-size:10px; font-weight:700; letter-spacing:0.06em;
  text-transform:uppercase; padding:2px 7px; border-radius:20px; vertical-align:middle;
  margin-left:6px; }
.badge-new { background:#e8f0e4; color:#40632f; }
.badge-retry { background:#f3e9d8; color:#7a5a1e; }
.how { font-size:14px; margin:6px 0 0; }
.why { font-size:13px; color:#6b6660; margin:6px 0 0; }
.item { padding:14px 0; border-bottom:1px solid #efece6; }
.item:last-child { border-bottom:none; padding-bottom:0; }
.item:first-child { padding-top:0; }
a { color:#2f5d8c; }
.link { font-size:12px; }
.chips { font-size:15px; margin:0; }
.watch { background:#fdf6ec; border:1px solid #f0e0c4; }
.watch li { font-size:13px; margin-bottom:7px; }
.watch ul { margin:0; padding-left:18px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
td { padding:7px 0; border-bottom:1px solid #efece6; vertical-align:top; }
tr:last-child td { border-bottom:none; }
td.label { color:#6b6660; padding-right:12px; white-space:nowrap; }
td.flag { color:#a3421f; font-weight:600; }
.cta { display:block; background:#20201d; color:#ffffff !important; text-decoration:none;
  text-align:center; padding:13px; border-radius:10px; font-size:15px; font-weight:600; }
.foot { font-size:12px; color:#8a8379; text-align:center; margin-top:6px; }
.day { font-size:13px; padding:7px 0; border-bottom:1px solid #efece6; }
.day:last-child { border-bottom:none; }
.day b { font-weight:600; }
"""


def _e(s: str) -> str:
    return html.escape(s, quote=True)


# Borders are set inline on every cell as well as in the stylesheet, because
# some mail clients drop <style> blocks and the grid is the point of the table.
_B = "1px solid #d9d4cb"
_TH = (f"border:{_B};padding:8px 10px;text-align:left;background:#f0ece5;"
       "font-size:10px;text-transform:uppercase;letter-spacing:0.06em;"
       "color:#6b6660;font-weight:700;")
_TD = f"border:{_B};padding:10px;vertical-align:top;"

_WEEK_CSS = """
.week { width:100%; border-collapse:collapse; font-size:13px;
  border:1px solid #d9d4cb; }
.week th, .week td { border:1px solid #d9d4cb; padding:10px; vertical-align:top; }
.week .d { white-space:nowrap; font-weight:700; width:1%; }
.food { font-weight:600; }
.food a { color:#20201d; text-decoration:none; border-bottom:1px solid #c9c3b8; }
.prep { color:#6b6660; font-size:12px; margin:3px 0 0; }
.stack { margin:0 0 12px; }
.stack:last-child { margin-bottom:0; }
.scroll { overflow-x:auto; -webkit-overflow-scrolling:touch; }
"""


def _food_cell(food, band: str, badge: str = "") -> str:
    """A linked food name with its preparation underneath."""
    bits = [
        f"<p class='stack'><span class='food'>"
        f"<a href='{_e(food.url)}'>{_e(food.name)}</a></span>{badge}"
    ]
    prep = food.prep_for(band)
    if prep:
        bits.append(f"<span class='prep' style='display:block'>{_e(prep)}</span>")
    bits.append("</p>")
    return "".join(bits)


def render_week_html(
    plans: list[DayPlan],
    snap: Snapshot,
    log_url: str | None = None,
) -> str:
    """The Saturday email: the coming week as one table you can cook from."""
    cfg = snap.config
    p: list[str] = [f"<style>{_CSS}{_WEEK_CSS}</style><div class='wrap'>"]
    start, end = plans[0].date, plans[-1].date

    p.append(
        f"<h1>The week ahead</h1>"
        f"<p class='sub'>{_e(short_date(start))} through {_e(short_date(end))} &middot; "
        f"{_e(age_string(cfg, start))}</p>"
    )

    p.append("<div class='card'><h2>The plan</h2><div class='scroll'>")
    p.append(
        f"<table class='week'><tr>"
        f"<th style='{_TH}'>Day</th>"
        f"<th style='{_TH}'>Main</th>"
        f"<th style='{_TH}'>Also serve</th>"
        f"</tr>"
    )

    for plan in plans:
        band = cfg.age_band(plan.date)
        shade = "background:#fdf6ec;" if (
            plan.new_foods or any(a.is_rechallenge for a in plan.anchors)
        ) else ""

        mains = []
        for a in plan.mains:
            badge = ""
            if a.is_new:
                badge = "<span class='badge badge-new'>new</span>"
            elif a.is_rechallenge:
                badge = "<span class='badge badge-retry'>re-try</span>"
            mains.append(_food_cell(a.food, band, badge))

        also = []
        for a in plan.keepers:
            badge = "<span class='badge badge-new'>new</span>" if a.is_new else ""
            label = ALLERGEN_LABELS[a.food.allergen].lower() if a.food.allergen else ""
            tag = f"<span class='prep'> &middot; {_e(label)}</span>" if label else ""
            also.append(_food_cell(a.food, band, badge + tag))
        if plan.vitamin_c:
            also.append(
                _food_cell(
                    plan.vitamin_c, band,
                    "<span class='prep'> &middot; vitamin C, helps her absorb the iron</span>",
                )
            )

        p.append(
            f"<tr>"
            f"<td class='d' style='{_TD}{shade}'>{_e(plan.date.strftime('%a'))}</td>"
            f"<td style='{_TD}{shade}'>{''.join(mains) or '-'}</td>"
            f"<td style='{_TD}{shade}'>{''.join(also) or '-'}</td>"
            f"</tr>"
        )
    p.append("</table></div>")
    p.append(
        "<p class='why'>Shaded rows have something new or a re-try on them. "
        "Those are the days to keep the rest of the plate boring. "
        "Every food links to its Solid Starts page.</p>"
    )
    p.append("</div>")

    # --- cautions worth carrying for the week ---
    cautions: list[str] = []
    for plan in plans:
        for c in plan.cautions:
            if c not in cautions:
                cautions.append(c)
    if cautions:
        p.append("<div class='card watch'><h2>Worth knowing</h2><ul>")
        for c in cautions:
            p.append(f"<li>{_e(c)}</li>")
        p.append("</ul></div>")

    # --- status ---
    p.append("<div class='card'><h2>Where she stands</h2><table>")
    for label, value, problem in status_lines(snap):
        cls = " class='flag'" if problem else ""
        p.append(f"<tr><td class='label'>{_e(label)}</td><td{cls}>{_e(value)}</td></tr>")
    p.append("</table></div>")

    if log_url:
        p.append(f"<a class='cta' href='{_e(log_url)}'>Log a reaction</a>")
    p.append(
        "<p class='foot'>Nothing here is recorded as eaten unless someone says so. "
        "If a day goes sideways, skip it and the next week reshuffles around it.</p>"
    )
    p.append("</div>")
    return "".join(p)


def render_week_text(plans: list[DayPlan], snap: Snapshot) -> str:
    start, end = plans[0].date, plans[-1].date
    lines = [f"The week ahead: {short_date(start)} through {short_date(end)}", ""]
    width = max(len(p.main_names or "-") for p in plans)
    for plan in plans:
        new = f"   NEW: {plan.new_names}" if plan.new_names else ""
        also = plan.keeper_names
        if plan.vitamin_c:
            extra = f"{plan.vitamin_c.name} (vitamin C)"
            also = f"{also}, {extra}" if also else extra
        lines.append(
            f"  {plan.date.strftime('%a')}  {(plan.main_names or '-').ljust(width)}"
            f"   {also}{new}"
        )
    lines.append("")
    lines.append(render_status_text(snap))
    return "\n".join(lines)


def render_week_subject(plans: list[DayPlan], cfg: Config) -> str:
    new = [a.food.name for plan in plans for a in plan.new_foods]
    if new:
        return f"This week: {', '.join(new[:3])}" + (" and more" if len(new) > 3 else "")
    return f"This week's plan, from {short_date(plans[0].date)}"

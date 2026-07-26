"""Turning plans into something a tired parent can read at 6am."""

from __future__ import annotations

import datetime as dt
import html

from .config import ALLERGEN_LABELS, Config
from .model import DayPlan, food_label, food_tags
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


def numeric_date(d: dt.date) -> str:
    """Just the numbers, for when the weekday is already spelled out."""
    return f"{d.month}/{d.day}"


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
    vit_c = plan.provides_vitamin_c()
    vit_c_key = vit_c.key if vit_c else None

    lines = [f"{pretty_date(plan.date)}  ({age_string(cfg, plan.date)})", ""]

    for a in plan.anchors:
        label = food_label(
            a.food, a.is_new, a.is_rechallenge, vitamin_c=a.food.key == vit_c_key
        )
        lines.append(f"  - {label}")
        prep = a.food.prep_for(band)
        if prep:
            lines.append(f"      {prep}")
        if a.carrier is not None:
            lines.append(f"      Needs mixing into something. Use the {a.carrier.name.lower()}.")
        if a.reasons:
            lines.append(f"      why: {a.reason}")
        lines.append(f"      {a.food.url}")

    if plan.vitamin_c is not None:
        lines.append(f"  - {food_label(plan.vitamin_c, vitamin_c=True)}")
        prep = plan.vitamin_c.prep_for(band)
        if prep:
            lines.append(f"      {prep}")
        lines.append(f"      {plan.vitamin_c.url}")

    if plan.secondaries:
        lines.append("")
        lines.append(f"  Plus any of these: {plan.secondary_names}")
    if plan.flavor:
        lines.append(f"  Season it with: {plan.flavor.name}, {plan.flavor.prep_for(band).lower()}")

    if plan.cautions:
        lines.append("")
        lines.append("  Worth knowing:")
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
a { color:#2f5d8c; }
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
"""


def _e(s: str) -> str:
    return html.escape(s, quote=True)


# Borders are set inline as well as in the stylesheet, because some mail clients
# drop <style> blocks entirely and the boxes are what makes this readable.
_B = "1px solid #d9d4cb"
_DAYHEAD = (f"border:{_B};padding:9px 12px;background:#efeae2;"
            "font-size:13px;font-weight:700;color:#20201d;text-align:left;")
_CELL = f"border:{_B};padding:11px 12px;vertical-align:top;"

_WEEK_CSS = """
.day-table { width:100%; border-collapse:collapse; margin:0 0 16px;
  border:1px solid #d9d4cb; }
.day-table th, .day-table td { border:1px solid #d9d4cb; }
.food { font-size:15px; font-weight:600; margin:0; }
.food a { color:#20201d; text-decoration:none; border-bottom:1px solid #c9c3b8; }
.tags { font-weight:400; color:#6b6660; font-size:13px; }
.prep { color:#57534d; font-size:13px; margin:4px 0 0; }
.mix { color:#7a5a1e; font-size:13px; margin:4px 0 0; }
.opts { color:#57534d; font-size:13px; margin:0; }
.rowlabel { font-size:10px; text-transform:uppercase; letter-spacing:0.06em;
  color:#8a8379; font-weight:700; margin:0 0 5px; }
"""


def _food_html(food, band: str, tags: list, carrier=None) -> str:
    tag_s = f" <span class='tags'>({_e(', '.join(tags))})</span>" if tags else ""
    out = [
        f"<p class='food'><a href='{_e(food.url)}'>{_e(food.name)}</a>{tag_s}</p>"
    ]
    prep = food.prep_for(band)
    if prep:
        out.append(f"<p class='prep'>{_e(prep)}</p>")
    if carrier is not None:
        out.append(
            f"<p class='mix'>Needs mixing into something. Use the "
            f"<a href='{_e(carrier.url)}'>{_e(carrier.name.lower())}</a>.</p>"
        )
    return "".join(out)


def render_week_html(
    plans: list[DayPlan],
    snap: Snapshot,
    log_url: str | None = None,
) -> str:
    """The Saturday email: one box per day, stacked, readable on a phone."""
    cfg = snap.config
    p: list[str] = [f"<style>{_CSS}{_WEEK_CSS}</style><div class='wrap'>"]
    start, end = plans[0].date, plans[-1].date

    p.append(
        f"<h1>The week ahead</h1>"
        f"<p class='sub'>{_e(short_date(start))} through {_e(short_date(end))} &middot; "
        f"{_e(age_string(cfg, start))}</p>"
    )

    for plan in plans:
        band = cfg.age_band(plan.date)
        vit_c = plan.provides_vitamin_c()
        vit_c_key = vit_c.key if vit_c else None
        flagged = plan.new_foods or any(a.is_rechallenge for a in plan.anchors)
        shade = "background:#fdf6ec;" if flagged else ""

        # Build the cells first, then emit one row each. Assembling rows inline
        # is how you end up with stray <tr> tags and a table that renders as a
        # single blob in Outlook.
        cells: list[str] = []
        for a in plan.anchors:
            tags = food_tags(
                a.food, a.is_new, a.is_rechallenge, vitamin_c=a.food.key == vit_c_key
            )
            cells.append((_food_html(a.food, band, tags, a.carrier), shade))

        if plan.vitamin_c is not None:
            cells.append(
                (
                    _food_html(
                        plan.vitamin_c, band, food_tags(plan.vitamin_c, vitamin_c=True)
                    ),
                    "",
                )
            )

        if plan.secondaries:
            opts = ", ".join(
                f"<a href='{_e(f.url)}'>{_e(f.name)}</a>" for f in plan.secondaries
            )
            cells.append(
                (
                    "<p class='rowlabel'>Plus any of these</p>"
                    f"<p class='opts'>{opts}</p>",
                    "",
                )
            )

        if plan.flavor is not None:
            cells.append(
                (
                    "<p class='rowlabel'>Season it with</p>"
                    f"<p class='opts'>{_e(plan.flavor.name)}, "
                    f"{_e(plan.flavor.prep_for(band).lower())}</p>",
                    "",
                )
            )

        p.append("<table class='day-table'>")
        p.append(
            f"<tr><th style='{_DAYHEAD}'>"
            f"{_e(plan.date.strftime('%A'))} "
            f"<span style='font-weight:400;color:#8a8379'>{_e(numeric_date(plan.date))}</span>"
            f"</th></tr>"
        )
        for body, cell_shade in cells:
            p.append(f"<tr><td style='{_CELL}{cell_shade}'>{body}</td></tr>")
        p.append("</table>")

    p.append(
        "<p class='foot' style='text-align:left;margin:0 0 18px'>"
        "Shaded days have something new or a re-try on them. Keep the rest of "
        "those plates boring. Every food links to its Solid Starts page.</p>"
    )

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
    cfg = snap.config
    start, end = plans[0].date, plans[-1].date
    lines = [f"The week ahead: {short_date(start)} through {short_date(end)}"]

    for plan in plans:
        band = cfg.age_band(plan.date)
        vit_c = plan.provides_vitamin_c()
        vit_c_key = vit_c.key if vit_c else None
        lines.append("")
        lines.append(f"{plan.date.strftime('%A')} {numeric_date(plan.date)}")
        for a in plan.anchors:
            label = food_label(
                a.food, a.is_new, a.is_rechallenge, vitamin_c=a.food.key == vit_c_key
            )
            lines.append(f"  - {label}")
            prep = a.food.prep_for(band)
            if prep:
                lines.append(f"      {prep}")
            if a.carrier is not None:
                lines.append(f"      Mix it into the {a.carrier.name.lower()}.")
        if plan.vitamin_c is not None:
            lines.append(f"  - {food_label(plan.vitamin_c, vitamin_c=True)}")
            prep = plan.vitamin_c.prep_for(band)
            if prep:
                lines.append(f"      {prep}")
        if plan.secondaries:
            lines.append(f"  Plus any of these: {plan.secondary_names}")

    lines.append("")
    lines.append(render_status_text(snap))
    return "\n".join(lines)


def render_week_subject(plans: list[DayPlan], cfg: Config) -> str:
    new = [a.food.name for plan in plans for a in plan.new_foods]
    if new:
        return f"This week: {', '.join(new[:3])}" + (" and more" if len(new) > 3 else "")
    return f"This week's plan, from {short_date(plans[0].date)}"

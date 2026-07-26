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

    overdue = snap.overdue_allergens()
    missing = snap.missing_allergens()
    if missing:
        names = ", ".join(ALLERGEN_LABELS[a] for a in missing)
        out.append(("Allergens not yet tried", names, True))
    if overdue:
        names = ", ".join(
            f"{ALLERGEN_LABELS[a]} ({snap.days_since_allergen(a)}d)" for a, _ in overdue
        )
        out.append(("Allergens overdue", names, True))
    if not missing and not overdue:
        out.append(("Allergens", "all nine in rotation", False))

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


def render_email_html(
    plans: list[DayPlan],
    snap: Snapshot,
    yesterday: DayPlan | None = None,
    confirm_url: str | None = None,
    log_url: str | None = None,
) -> str:
    cfg = snap.config
    today_plan = plans[0]
    band = cfg.age_band(today_plan.date)
    p: list[str] = []

    p.append(f"<style>{_CSS}</style><div class='wrap'>")
    p.append(
        f"<h1>{_e(cfg.baby_name)}, {_e(pretty_date(today_plan.date))}</h1>"
        f"<p class='sub'>{_e(age_string(cfg, today_plan.date))}</p>"
    )

    # --- yesterday ---
    if yesterday and yesterday.anchors:
        p.append("<div class='card'>")
        p.append("<h2>Yesterday</h2>")
        p.append(
            f"<p class='chips'>The plan was <b>{_e(yesterday.anchor_names)}</b>. "
            f"Did that happen?</p>"
        )
        if confirm_url:
            p.append(
                f"<p class='why' style='margin-top:10px'>"
                f"<a href='{_e(confirm_url)}'>Tap here and type yes, some, or no</a>. "
                f"If you skip it, we assume it happened.</p>"
            )
        p.append("</div>")

    # --- today ---
    def food_block(a, small: bool = False) -> None:
        badge = ""
        if a.is_new:
            badge = "<span class='badge badge-new'>new</span>"
        elif a.is_rechallenge:
            badge = "<span class='badge badge-retry'>re-try</span>"
        cls = "keeper-food" if small else "main-food"
        p.append("<div class='item'>")
        p.append(f"<p class='{cls}'>{_e(a.food.name)}{badge}</p>")
        prep = a.food.prep_for(band)
        if prep:
            p.append(f"<p class='how'>{_e(prep)}</p>")
        if a.reasons:
            p.append(f"<p class='why'>Why: {_e(a.reason)}</p>")
        p.append(
            f"<p class='why link'><a href='{_e(a.food.url)}'>"
            f"Solid Starts on {_e(a.food.name.lower())}</a></p>"
        )
        p.append("</div>")

    if today_plan.mains:
        heading = (
            "Re-try today"
            if any(a.is_rechallenge for a in today_plan.mains)
            else "Today"
        )
        p.append(f"<div class='card'><h2>{heading}</h2>")
        for a in today_plan.mains:
            food_block(a)
        p.append("</div>")

    if today_plan.keepers:
        p.append("<div class='card'><h2>Keep these in too</h2>")
        p.append(
            "<p class='why' style='margin:0 0 4px'>Allergens that are due again. "
            "A spoonful or a thin spread alongside the main is plenty.</p>"
        )
        for a in today_plan.keepers:
            food_block(a, small=True)
        p.append("</div>")

    # --- secondaries ---
    if today_plan.secondaries:
        p.append("<div class='card'>")
        p.append("<h2>Round it out with any of these</h2>")
        p.append(f"<p class='chips'>{_e(today_plan.secondary_names)}</p>")
        p.append(
            "<p class='why'>Pick whatever is in the fridge. These are all things "
            "she has taken before, so none of it is a test.</p>"
        )
        if today_plan.flavor:
            p.append(
                f"<p class='why'>Flavor of the day: <b>{_e(today_plan.flavor.name)}</b>, "
                f"{_e(today_plan.flavor.prep_for(band).lower())}</p>"
            )
        p.append("</div>")

    # --- cautions ---
    if today_plan.cautions:
        p.append("<div class='card watch'>")
        p.append("<h2>Worth knowing</h2><ul>")
        for c in today_plan.cautions:
            p.append(f"<li>{_e(c)}</li>")
        p.append("</ul></div>")

    # --- lookahead ---
    if len(plans) > 1:
        p.append("<div class='card'>")
        p.append("<h2>Coming up</h2>")
        for plan in plans[1:]:
            p.append(
                f"<div class='day'><b>{_e(short_date(plan.date))}</b> &nbsp; "
                f"{_e(plan.main_names or plan.keeper_names)}"
                + (f" <span style='color:#8a8379'>+ {_e(plan.keeper_names)}</span>"
                   if plan.main_names and plan.keeper_names else "")
                + "</div>"
            )
        p.append("</div>")

    # --- status ---
    p.append("<div class='card'>")
    p.append(f"<h2>Where {_e(cfg.baby_name)} stands</h2><table>")
    for label, value, problem in status_lines(snap):
        cls = " class='flag'" if problem else ""
        p.append(
            f"<tr><td class='label'>{_e(label)}</td><td{cls}>{_e(value)}</td></tr>"
        )
    p.append("</table></div>")

    if log_url:
        p.append(f"<a class='cta' href='{_e(log_url)}'>Log a reaction</a>")
    p.append(
        "<p class='foot'>Reactions only ever come from you, never assumed. "
        "If something looks wrong here, it probably is, and you should trust yourself "
        "over this email.</p>"
    )
    p.append("</div>")
    return "".join(p)


def render_email_subject(plan: DayPlan, cfg: Config) -> str:
    names = plan.anchor_names or "Solids"
    return f"{cfg.baby_name} today: {names}"


def render_grocery_html(plans: list[DayPlan], snap: Snapshot) -> str:
    by_category: dict[str, list[str]] = {}
    seen: set[str] = set()
    for plan in plans:
        for food in plan.all_foods():
            if food.key in seen:
                continue
            seen.add(food.key)
            by_category.setdefault(food.category, []).append(food.name)

    start, end = plans[0].date, plans[-1].date
    p = [f"<style>{_CSS}</style><div class='wrap'>"]
    p.append(f"<h1>Groceries</h1><p class='sub'>{_e(short_date(start))} through {_e(short_date(end))}</p>")
    p.append("<div class='card'>")
    for category in ("protein", "vegetable", "fruit", "legume", "grain", "dairy", "nut_seed", "other"):
        if category not in by_category:
            continue
        p.append(f"<h2>{_e(category.replace('_', ' ').title())}</h2>")
        p.append(f"<p class='chips' style='margin-bottom:16px'>{_e(', '.join(sorted(by_category[category])))}</p>")
    p.append("</div>")
    p.append("<div class='card'>")
    p.append("<h2>The week</h2>")
    for plan in plans:
        p.append(
            f"<div class='day'><b>{_e(short_date(plan.date))}</b> &nbsp; {_e(plan.anchor_names)}</div>"
        )
    p.append("</div>")
    p.append(
        "<p class='foot'>This is a plan, not a contract. Buy what looks good and "
        "the tracker will adjust.</p></div>"
    )
    return "".join(p)

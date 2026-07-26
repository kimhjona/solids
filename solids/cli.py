"""Command line interface.

    solids today                  what to feed her now, and why
    solids plan --days 7          the week, so you can shop for it
    solids status                 where she stands against the goals
    solids log broccoli --ate all --reaction none
    solids grocery                the shopping list
    solids daily                  update the sheet for today, no email
    solids weekly                 plan the week and email the table
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import render
from .catalog import load_catalog
from .config import CONFIG_PATH, Config, load_config
from .engine import plan_ahead, plan_day
from .model import LogEntry, parse_ate, parse_reaction
from .state import build_snapshot

# Column offsets in the Log tab.
COL_ATE = 2
COL_SOURCE = 6


def _today(args) -> dt.date:
    return dt.date.fromisoformat(args.date) if getattr(args, "date", None) else dt.date.today()


def _open_store(cfg: Config):
    from .sheet import SheetStore

    return SheetStore(cfg.spreadsheet_id)


def _load_entries(cfg: Config, catalog, today: dt.date, store=None):
    """All history, from the original tab plus our own log.

    SOLIDS_FIXTURE lets the engine run against a local file, which is how the
    tests work and how you can try changes without touching the real sheet.
    """
    fixture = os.environ.get("SOLIDS_FIXTURE")
    if fixture:
        raw = json.loads(Path(fixture).read_text())
        entries = []
        for r in raw:
            entries.append(
                LogEntry(
                    date=dt.date.fromisoformat(r["date"]),
                    food_name=r["food"],
                    food=catalog.get(r["food"]),
                    ate=parse_ate(r.get("ate", "all")),
                    reaction=parse_reaction(r.get("reaction", "none")),
                    attribution=r.get("attribution", ""),
                    notes=r.get("notes", ""),
                    source=r.get("source", "intro"),
                )
            )
        return entries, None

    store = store or _open_store(cfg)
    entries = store.read_intro_tab(catalog, today)
    entries += store.read_log(catalog, cfg.log_tab, today)
    return entries, store


def _snapshot(cfg: Config, today: dt.date, store=None):
    catalog = load_catalog()
    entries, store = _load_entries(cfg, catalog, today, store)
    return build_snapshot(entries, catalog, cfg, today), entries, catalog, store


# ------------------------------------------------------------- commands ----

def cmd_init(args, cfg: Config) -> int:
    from .mailer import OUTBOX_HEADER
    from .sheet import LOG_HEADER, PLAN_HEADER

    store = _open_store(cfg)
    print(f"Spreadsheet: {store.meta()['properties']['title']}")
    print(f"Leaving '{store.first_tab_name()}' untouched.\n")
    tabs = [
        (cfg.log_tab, LOG_HEADER),
        (cfg.plan_tab, PLAN_HEADER),
        (cfg.status_tab, ["Measure", "Where she is", "Needs attention"]),
    ]
    if cfg.mail_transport == "outbox":
        tabs.append((cfg.outbox_tab, OUTBOX_HEADER))
    for tab, header in tabs:
        created = store.ensure_tab(tab, header)
        print(f"  {'created' if created else 'already there'}: {tab}")
    if not CONFIG_PATH.exists():
        path = cfg.save()
        print(f"\nWrote config to {path}")
    return 0


def cmd_today(args, cfg: Config) -> int:
    today = _today(args)
    snap, _, _, _ = _snapshot(cfg, today)
    plan = plan_day(snap, today)
    print(render.render_day_text(plan, snap))
    return 0


def cmd_plan(args, cfg: Config) -> int:
    today = _today(args)
    snap, entries, catalog, _ = _snapshot(cfg, today)
    plans = plan_ahead(entries, catalog, cfg, today, args.days)
    for i, plan in enumerate(plans):
        if i:
            print()
        print(render.render_day_text(plan, snap))
    return 0


def cmd_status(args, cfg: Config) -> int:
    snap, _, _, _ = _snapshot(cfg, _today(args))
    print(render.render_status_text(snap))
    return 0


def cmd_grocery(args, cfg: Config) -> int:
    today = _today(args)
    snap, entries, catalog, _ = _snapshot(cfg, today)
    plans = plan_ahead(entries, catalog, cfg, today, args.days)
    print(render.render_grocery_text(plans, snap))
    return 0


def cmd_foods(args, cfg: Config) -> int:
    catalog = load_catalog()
    needle = (args.search or "").lower()
    rows = []
    for food in catalog:
        if needle and needle not in food.name.lower() and needle not in food.category:
            continue
        tags = [food.category]
        if food.allergen:
            tags.append(food.allergen)
        if food.iron == 2:
            tags.append("iron+")
        elif food.iron == 1:
            tags.append("iron")
        if food.bitter:
            tags.append("bitter")
        rows.append((food.name, ", ".join(tags)))
    width = max((len(n) for n, _ in rows), default=0)
    for name, tags in sorted(rows):
        print(f"  {name.ljust(width)}   {tags}")
    print(f"\n  {len(rows)} foods")
    return 0


def cmd_log(args, cfg: Config) -> int:
    from .sheet import LOG_HEADER

    today = _today(args)
    catalog = load_catalog()
    food = catalog.get(args.food)
    if food is None:
        print(f"Don't know '{args.food}'. Try `solids foods --search {args.food}`.")
        return 1

    store = _open_store(cfg)
    store.ensure_tab(cfg.log_tab, LOG_HEADER)
    row = [
        today.isoformat(),
        food.name,
        parse_ate(args.ate),
        parse_reaction(args.reaction),
        args.attribution or "",
        args.notes or "",
        "manual",
    ]
    store.append(cfg.log_tab, [row])
    print(f"Logged {food.name} on {today.isoformat()}: ate {row[2]}, reaction {row[3]}")

    if row[3] != "none" and not args.attribution:
        print(
            "\nNo attribution given, so this counts as a real reaction and "
            f"{food.name} drops out of rotation for {cfg.rechallenge_gap_days} days.\n"
            "If you think it was her skin rather than the food, re-run with "
            "--attribution not_food."
        )
    return 0


def cmd_weekly(args, cfg: Config) -> int:
    """The Saturday job: plan the coming week and email it as a table.

    Runs on Saturday afternoon so there is time to shop before it starts.
    """
    today = _today(args)
    days = args.days or cfg.lookahead_days
    start = today + dt.timedelta(days=1) if cfg.week_starts_tomorrow else today

    catalog = load_catalog()
    store = None if os.environ.get("SOLIDS_FIXTURE") else _open_store(cfg)
    snap, entries, catalog, store = _snapshot(cfg, today, store)
    plans = plan_ahead(entries, catalog, cfg, start, days)

    log_url = None
    if store:
        from .sheet import PLAN_HEADER

        store.ensure_tab(cfg.plan_tab, PLAN_HEADER)
        for plan in plans:
            _write_plan_row(store, cfg, plan)
        _write_status_tab(store, cfg, snap)
        gid = store.tab_gid(cfg.log_tab)
        if gid is not None:
            log_url = render.sheet_link(cfg.spreadsheet_id, gid)

    html_body = render.render_week_html(plans, snap, log_url)
    text_body = render.render_week_text(plans, snap)
    subject = render.render_week_subject(plans, cfg)

    if args.dry_run:
        out = Path(args.out or "solids-week.html")
        out.write_text(html_body)
        print(text_body)
        print(f"\n[dry run] Subject: {subject}")
        print(f"[dry run] Wrote {out}")
        return 0

    from .mailer import send_email

    send_email(
        subject, html_body, text_body, cfg.mail_to, cfg.mail_from,
        transport=cfg.mail_transport, store=store, outbox_tab=cfg.outbox_tab,
    )
    print(f"{'Queued' if cfg.mail_transport == 'outbox' else 'Sent'}: {subject}")
    return 0


def cmd_daily(args, cfg: Config) -> int:
    """The daily job. Reconcile yesterday, plan today, write it to the sheet.

    It does not email. The inbox only gets the Saturday summary; this exists so
    the sheet and `solids today` stay current in between.
    """
    from .sheet import LOG_HEADER, PLAN_HEADER

    today = _today(args)
    yesterday = today - dt.timedelta(days=1)
    catalog = load_catalog()
    store = None if os.environ.get("SOLIDS_FIXTURE") else _open_store(cfg)

    if store:
        store.ensure_tab(cfg.log_tab, LOG_HEADER)
        store.ensure_tab(cfg.plan_tab, PLAN_HEADER)
        entries, _ = _load_entries(cfg, catalog, today, store)
        _reconcile(store, cfg, catalog, entries, yesterday)

    snap, entries, catalog, store = _snapshot(cfg, today, store)
    plans = plan_ahead(entries, catalog, cfg, today, cfg.lookahead_days)
    today_plan = plans[0]

    if store and not args.dry_run:
        _write_plan_row(store, cfg, today_plan)
        _write_status_tab(store, cfg, snap)

    print(render.render_day_text(today_plan, snap))
    print()
    print(render.render_status_text(snap))
    if store and not args.dry_run:
        print("\nWrote the plan and status to the sheet. No email, that is Saturday's job.")
    return 0


def cmd_doctor(args, cfg: Config) -> int:
    """Check every external thing this depends on, and say what to click."""
    import json as _json

    ok = True

    def report(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else 'XX'}] {label}")
        if detail:
            for line in detail.strip().splitlines():
                print(f"       {line}")

    # --- credentials on disk ---
    sa_path = Path(
        os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS",
            str(Path.home() / ".config" / "solids" / "service-account.json"),
        )
    )
    inline = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_email = None
    if inline:
        sa_email = _json.loads(inline).get("client_email")
        report("service account credentials", True, "from GOOGLE_SERVICE_ACCOUNT_JSON")
    elif sa_path.exists():
        sa_email = _json.loads(sa_path.read_text()).get("client_email")
        report("service account credentials", True, f"{sa_path}\n{sa_email}")
    else:
        report("service account credentials", False, f"nothing at {sa_path}")
        return 1

    # --- can we reach the spreadsheet ---
    from .sheet import SheetStore

    store = None
    try:
        store = SheetStore(cfg.spreadsheet_id)
        title = store.meta()["properties"]["title"]
        report("spreadsheet readable", True, f"{title}\ntabs: {', '.join(store.tab_names())}")
    except Exception as e:
        text = str(e)
        if "SERVICE_DISABLED" in text or "has not been used in project" in text:
            report(
                "Sheets API enabled",
                False,
                "Enable it, then wait a minute:\n"
                "https://console.cloud.google.com/apis/library/sheets.googleapis.com"
                f"?project={_json.loads(sa_path.read_text()).get('project_id', '')}",
            )
        elif "403" in text or "does not have permission" in text:
            report(
                "spreadsheet shared with the service account",
                False,
                f"Share the sheet with {sa_email} as Editor:\n"
                f"https://docs.google.com/spreadsheets/d/{cfg.spreadsheet_id}/edit",
            )
        else:
            report("spreadsheet readable", False, text[:300])
        return 1

    # --- can we write ---
    try:
        probe = "_solids_probe"
        store.ensure_tab(probe, ["delete me"])
        store._service.spreadsheets().batchUpdate(
            spreadsheetId=cfg.spreadsheet_id,
            body={"requests": [{"deleteSheet": {"sheetId": store.tab_gid(probe)}}]},
        ).execute()
        store.meta(refresh=True)
        report("write access", True, "created and removed a scratch tab")
    except Exception as e:
        report(
            "write access",
            False,
            f"Share the sheet with {sa_email} as Editor, not Viewer.\n{str(e)[:200]}",
        )

    # --- the original tab parses ---
    try:
        entries = store.read_intro_tab(load_catalog())
        unresolved = sorted({e.food_name for e in entries if e.food is None})
        report(
            "original tab parses",
            not unresolved,
            f"{len(entries)} exposures read from '{store.first_tab_name()}'"
            + (f"\nnot in the catalog: {', '.join(unresolved)}" if unresolved else ""),
        )
    except Exception as e:
        report("original tab parses", False, str(e)[:300])

    # --- mail ---
    if cfg.mail_transport == "resend":
        report("RESEND_API_KEY set", bool(os.environ.get("RESEND_API_KEY")))
    elif cfg.mail_transport == "smtp":
        report("GMAIL_APP_PASSWORD set", bool(os.environ.get("GMAIL_APP_PASSWORD")))
    else:
        report("outbox transport", True, "no sending credential needed")

    print("\n" + ("Ready. Run `solids init`." if ok else "Fix the above, then re-run."))
    return 0 if ok else 1


def cmd_setup(args, cfg: Config) -> int:
    sa_path = Path.home() / ".config" / "solids" / "service-account.json"
    print(f"""
Setup, once.

1. Google Cloud, to let this read and write the sheet.
   a. https://console.cloud.google.com/projectcreate, name it anything.
   b. Enable the Sheets API:
      https://console.cloud.google.com/apis/library/sheets.googleapis.com
   c. Create a service account under IAM > Service Accounts, no roles needed.
   d. On that service account, Keys > Add key > JSON. Save it to
      {sa_path}
   e. Copy the service account email, it looks like
      something@your-project.iam.gserviceaccount.com

2. Share the sheet with that service account email, as Editor.
   https://docs.google.com/spreadsheets/d/{cfg.spreadsheet_id}/edit

3. Sending, currently set to "{cfg.mail_transport}".

   outbox   No credential needed. The tracker writes the message to the Outbox
            tab and a small script on the sheet mails it. Open the sheet,
            Extensions > Apps Script, paste in appsscript/Code.gs, run
            sendOutbox once to approve it, then run createTrigger once.

   resend   A send-only API key from https://resend.com/api-keys.
            export RESEND_API_KEY='...'

   smtp     A Gmail app password. This also grants full read access to the
            whole mailbox, so prefer one of the other two.
            export GMAIL_APP_PASSWORD='...'

4. solids init
5. solids daily --dry-run     to see the email without sending it

Config lives at {CONFIG_PATH}.
""".strip())
    return 0


# --------------------------------------------------------------- helpers ----

def _reconcile(store, cfg: Config, catalog, entries, day: dt.date) -> None:
    """Turn yesterday's plan into log rows.

    If nobody confirmed it, we assume it happened and mark it assumed, so the
    counters keep moving. Reactions are never assumed, only ever entered by hand.
    """
    plans = store.read_plans(cfg.plan_tab, day)
    row = plans.get(day)
    if not row or not row["main"]:
        return

    confirmed = parse_ate(row["confirmed"]) if row["confirmed"].strip() else None
    existing = [e for e in entries if e.date == day and e.source in ("assumed", "confirmed")]

    if not existing:
        ate = confirmed or "all"
        source = "confirmed" if confirmed else "assumed"
        rows = []
        for name in (n.strip() for n in row["main"].split("+")):
            food = catalog.get(name)
            if food:
                rows.append([day.isoformat(), food.name, ate, "", "", "", source])
        store.append(cfg.log_tab, rows)
        return

    # A late confirmation should correct what we assumed.
    if confirmed:
        for e in existing:
            if e.ate != confirmed or e.source != "confirmed":
                store.update_cell(cfg.log_tab, e.row, COL_ATE, confirmed)
                store.update_cell(cfg.log_tab, e.row, COL_SOURCE, "confirmed")


def _write_plan_row(store, cfg: Config, plan) -> None:
    reasons = " | ".join(a.reason for a in plan.anchors if a.reasons)
    row = [
        plan.date.isoformat(),
        plan.anchor_names,
        plan.secondary_names,
        reasons,
        "",
        dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    ]
    existing = store.read_plans(cfg.plan_tab, plan.date).get(plan.date)
    if existing:
        for col, value in enumerate(row):
            if col == 4:
                continue  # never clobber a confirmation someone typed
            store.update_cell(cfg.plan_tab, existing["row"], col, value)
    else:
        store.append(cfg.plan_tab, [row])


def _write_status_tab(store, cfg: Config, snap) -> None:
    rows = [["Measure", "Where she is", "Needs attention"]]
    for label, value, problem in render.status_lines(snap):
        rows.append([label, value, "yes" if problem else ""])
    rows.append([])
    rows.append(["Updated", dt.datetime.now().strftime("%Y-%m-%d %H:%M"), ""])
    store.overwrite(cfg.status_tab, rows)


# ------------------------------------------------------------------ main ----

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="solids", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", help="pretend today is this date, YYYY-MM-DD")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="one-time setup walkthrough").set_defaults(fn=cmd_setup)
    sub.add_parser("doctor", help="check credentials, sheet access and mail").set_defaults(fn=cmd_doctor)
    sub.add_parser("init", help="create the Log, Plan and Status tabs").set_defaults(fn=cmd_init)
    sub.add_parser("today", help="what to feed her today").set_defaults(fn=cmd_today)
    sub.add_parser("status", help="how she is tracking against the goals").set_defaults(fn=cmd_status)

    sp = sub.add_parser("plan", help="the next few days")
    sp.add_argument("--days", type=int, default=7)
    sp.set_defaults(fn=cmd_plan)

    sg = sub.add_parser("grocery", help="shopping list for the coming days")
    sg.add_argument("--days", type=int, default=7)
    sg.set_defaults(fn=cmd_grocery)

    sf = sub.add_parser("foods", help="what the catalog knows")
    sf.add_argument("--search", help="filter by name or category")
    sf.set_defaults(fn=cmd_foods)

    sl = sub.add_parser("log", help="record that she ate something")
    sl.add_argument("food")
    sl.add_argument("--ate", default="all", help="all, some, or none")
    sl.add_argument("--reaction", default="none",
                    help="none, hives, splotches, rash, eczema, vomit, diarrhea, swelling")
    sl.add_argument("--attribution", default="",
                    help="sure, unsure, or not_food if you think it was her skin")
    sl.add_argument("--notes", default="")
    sl.set_defaults(fn=cmd_log)

    sd = sub.add_parser("daily", help="update the sheet for today, no email")
    sd.add_argument("--dry-run", action="store_true", help="render without writing or sending")
    sd.set_defaults(fn=cmd_daily)

    sw = sub.add_parser("weekly", help="plan the coming week and email the table")
    sw.add_argument("--days", type=int, default=0, help="defaults to lookahead_days")
    sw.add_argument("--dry-run", action="store_true", help="write the email to a file instead")
    sw.add_argument("--out", help="where to write the dry-run html")
    sw.set_defaults(fn=cmd_weekly)

    return p


def _load_env_file() -> None:
    """Read secrets from ~/.config/solids/env so they stay out of the repo.

    One KEY=value per line. Anything already in the environment wins, which is
    how the GitHub Actions run overrides it.
    """
    path = Path(os.environ.get("SOLIDS_ENV", Path.home() / ".config" / "solids" / "env"))
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_env_file()
    cfg = load_config()
    try:
        return args.fn(args, cfg)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

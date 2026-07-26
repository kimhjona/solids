"""Google Sheets access.

Rules of engagement: the tab Lisha already uses is read-only as far as this
program is concerned. We read it once to seed history, and everything we write
goes into tabs we created ourselves.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .catalog import Catalog
from .model import LogEntry, parse_ate, parse_reaction

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

LOG_HEADER = [
    "Date",
    "Food",
    "Ate",
    "Reaction",
    "Sure it was the food?",
    "Notes",
    "Source",
]

PLAN_HEADER = ["Date", "Main", "Also good", "Why", "Confirmed", "Generated"]


def _credentials():
    """Service account, from a file path or an inline JSON blob."""
    inline = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if inline:
        info = json.loads(inline)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        str(Path.home() / ".config" / "solids" / "service-account.json"),
    )
    if not Path(path).exists():
        raise SystemExit(
            f"No Google credentials found.\n"
            f"Expected a service account key at {path}, or the "
            f"GOOGLE_SERVICE_ACCOUNT_JSON environment variable.\n"
            f"Run `solids setup` for the walkthrough."
        )
    return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)


def parse_sheet_date(value: str, today: dt.date | None = None) -> dt.date | None:
    """Parse the loose date formats a human types into a spreadsheet.

    Handles 5/17, 5/17/26, 2026-05-17. Bare month/day gets the current year,
    rolled back if that would put it in the future.
    """
    today = today or dt.date.today()
    s = (value or "").strip()
    if not s:
        return None

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    try:
        month, day = (int(p) for p in s.split("/")[:2])
    except (ValueError, IndexError):
        return None
    try:
        guess = dt.date(today.year, month, day)
    except ValueError:
        return None
    if guess > today + dt.timedelta(days=30):
        guess = guess.replace(year=today.year - 1)
    return guess


class SheetStore:
    def __init__(self, spreadsheet_id: str, service=None):
        self.spreadsheet_id = spreadsheet_id
        self._service = service or build(
            "sheets", "v4", credentials=_credentials(), cache_discovery=False
        )
        self._values = self._service.spreadsheets().values()
        self._meta: dict | None = None

    # ---- plumbing ------------------------------------------------------

    def meta(self, refresh: bool = False) -> dict:
        if self._meta is None or refresh:
            self._meta = (
                self._service.spreadsheets()
                .get(spreadsheetId=self.spreadsheet_id)
                .execute()
            )
        return self._meta

    def tab_names(self) -> list[str]:
        return [s["properties"]["title"] for s in self.meta()["sheets"]]

    def first_tab_name(self) -> str:
        return self.meta()["sheets"][0]["properties"]["title"]

    def tab_gid(self, title: str) -> int | None:
        for s in self.meta()["sheets"]:
            if s["properties"]["title"] == title:
                return s["properties"]["sheetId"]
        return None

    def read(self, tab: str) -> list[list[str]]:
        resp = self._values.get(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab}'",
            majorDimension="ROWS",
        ).execute()
        return resp.get("values", [])

    def ensure_tab(self, title: str, header: list[str]) -> bool:
        """Create the tab with its header if it is not there. Returns True if created."""
        if title in self.tab_names():
            return False
        self._service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()
        self._values.update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{title}'!A1",
            valueInputOption="RAW",
            body={"values": [header]},
        ).execute()
        self.meta(refresh=True)
        return True

    def append(self, tab: str, rows: list[list[str]]) -> None:
        if not rows:
            return
        self._values.append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

    def overwrite(self, tab: str, rows: list[list[str]]) -> None:
        self._values.clear(
            spreadsheetId=self.spreadsheet_id, range=f"'{tab}'", body={}
        ).execute()
        self._values.update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()

    def update_cell(self, tab: str, row: int, col: int, value: str) -> None:
        col_letter = chr(ord("A") + col)
        self._values.update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab}'!{col_letter}{row}",
            valueInputOption="USER_ENTERED",
            body={"values": [[value]]},
        ).execute()

    # ---- domain reads --------------------------------------------------

    def read_intro_tab(self, catalog: Catalog, today: dt.date | None = None) -> list[LogEntry]:
        """Seed history from the original hand-kept tab.

        Columns are Food, Date Started, Allergic Reaction?, Notes. We treat each
        row as one exposure on that date. Whether she liked it is inferred from
        the notes, which is imprecise but better than nothing.
        """
        rows = self.read(self.first_tab_name())
        entries: list[LogEntry] = []
        for raw in rows:
            cells = list(raw) + [""] * (4 - len(raw))
            name, date_s, reaction_s, notes = (c.strip() for c in cells[:4])
            if not name or name.lower() == "food":
                continue
            date = parse_sheet_date(date_s, today)
            if date is None:
                continue
            food = catalog.get(name)
            reaction = parse_reaction(reaction_s)
            if reaction == "other" and notes:
                reaction = parse_reaction(notes)

            # "Maybe" in the reaction column is a real answer, not a missing one.
            # It means they saw something and are not sure it was the food, which
            # is exactly what the attribution column is for.
            attribution = ""
            if any(w in reaction_s.lower() for w in ("maybe", "unsure", "possibly", "not sure")):
                attribution = "unsure"

            lowered = notes.lower()
            if any(w in lowered for w in ("didn't really like", "didnt really like", "refused", "hated")):
                ate = "some"
            elif "loved" in lowered or "liked" in lowered:
                ate = "all"
            else:
                ate = "all"

            entries.append(
                LogEntry(
                    date=date,
                    food_name=name,
                    food=food,
                    ate=ate,
                    reaction=reaction,
                    attribution=attribution,
                    notes=notes,
                    source="intro",
                )
            )
        return entries

    def read_log(self, catalog: Catalog, tab: str, today: dt.date | None = None) -> list[LogEntry]:
        if tab not in self.tab_names():
            return []
        rows = self.read(tab)
        entries: list[LogEntry] = []
        for i, raw in enumerate(rows, start=1):
            if i == 1:
                continue  # header
            cells = list(raw) + [""] * (7 - len(raw))
            date_s, name, ate_s, reaction_s, attribution, notes, source = (
                c.strip() for c in cells[:7]
            )
            if not name:
                continue
            date = parse_sheet_date(date_s, today)
            if date is None:
                continue
            entries.append(
                LogEntry(
                    date=date,
                    food_name=name,
                    food=catalog.get(name),
                    ate=parse_ate(ate_s),
                    reaction=parse_reaction(reaction_s),
                    attribution=attribution.lower().replace(" ", "_"),
                    notes=notes,
                    source=source or "manual",
                    row=i,
                )
            )
        return entries

    def read_plans(self, tab: str, today: dt.date | None = None) -> dict[dt.date, dict]:
        if tab not in self.tab_names():
            return {}
        out: dict[dt.date, dict] = {}
        for i, raw in enumerate(self.read(tab), start=1):
            if i == 1:
                continue
            cells = list(raw) + [""] * (6 - len(raw))
            date = parse_sheet_date(cells[0].strip(), today)
            if date is None:
                continue
            out[date] = {
                "row": i,
                "main": cells[1].strip(),
                "also": cells[2].strip(),
                "why": cells[3].strip(),
                "confirmed": cells[4].strip(),
                "generated": cells[5].strip(),
            }
        return out

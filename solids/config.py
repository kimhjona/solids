"""Tunable knobs. Everything the engine argues about lives here.

Nothing personal belongs in this file. The real values live in a config JSON
outside the repo, at SOLIDS_CONFIG or ~/.config/solids/config.json. See
solids.config.example.json.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("SOLIDS_CONFIG", Path.home() / ".config" / "solids" / "config.json")
)

# The nine common allergens, in the order the rotation walks them.
ALLERGENS = [
    "peanut",
    "tree_nut",
    "egg",
    "dairy",
    "wheat",
    "soy",
    "sesame",
    "fish",
    "shellfish",
]

ALLERGEN_LABELS = {
    "peanut": "Peanut",
    "tree_nut": "Tree nut",
    "egg": "Egg",
    "dairy": "Dairy",
    "wheat": "Wheat",
    "soy": "Soy",
    "sesame": "Sesame",
    "fish": "Fish",
    "shellfish": "Shellfish",
}


@dataclass
class Config:
    # Who. Set these in your own config file, not here.
    birthday: str = "2025-01-01"
    baby_name: str = "the baby"

    # Where the data lives
    spreadsheet_id: str = ""
    log_tab: str = "Log"
    plan_tab: str = "Plan"
    status_tab: str = "Status"

    # Email
    mail_to: str = ""
    mail_from: str = ""
    # outbox needs no credential at all, see appsscript/Code.gs.
    # resend needs a send-only API key. smtp needs an app password, which also
    # grants full read access to the mailbox.
    mail_transport: str = "outbox"
    outbox_tab: str = "Outbox"
    # The weekly email goes out on this day. 0 = Monday, so 5 = Saturday.
    weekly_email_weekday: int = 5
    # Plan the seven days starting tomorrow, so a Saturday email covers Sunday
    # through Saturday and there is time to shop.
    week_starts_tomorrow: bool = True

    # Goals
    iron_days_per_week: int = 6           # iron-rich food on most days
    bitter_per_week: int = 4              # bitter vegetable exposures
    veg_to_fruit_ratio: float = 3.0       # vegetables per sweet fruit, 14-day window
    new_foods_by_first_birthday: int = 100
    acceptance_target_exposures: int = 12  # keep re-offering until this many tries
    reoffer_gap_days: int = 4             # wait this long before re-offering a rejection
    rechallenge_gap_days: int = 21        # wait this long before retrying after a reaction

    # Planning rules
    max_new_foods_per_day: int = 1
    mains_per_day: int = 1
    allergen_keepers_per_day: int = 2
    secondaries_per_day: int = 5
    lookahead_days: int = 7
    prefer_weekend_for_new_allergen: bool = True

    # Foods we never want recommended (allergy confirmed, or family preference)
    excluded: list = field(default_factory=list)

    @property
    def birth_date(self) -> dt.date:
        return dt.date.fromisoformat(self.birthday)

    def age_months(self, on: dt.date | None = None) -> int:
        on = on or dt.date.today()
        b = self.birth_date
        months = (on.year - b.year) * 12 + (on.month - b.month)
        if on.day < b.day:
            months -= 1
        return months

    def age_band(self, on: dt.date | None = None) -> str:
        """Which prep instructions apply."""
        m = self.age_months(on)
        if m >= 12:
            return "12"
        if m >= 9:
            return "9"
        return "6"

    def save(self, path: Path | None = None) -> Path:
        path = path or CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")
        return path


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        return Config()
    data = json.loads(path.read_text())
    known = {f for f in Config.__dataclass_fields__}
    return Config(**{k: v for k, v in data.items() if k in known})

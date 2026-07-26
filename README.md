# Solids

A baby-led weaning tracker. It reads a Google Sheet you already keep by hand,
decides what to feed the baby, says why, and emails the week ahead every
Saturday afternoon so there is time to shop.

The goal is not record keeping. It is that nobody has to hold nine allergens, a
hundred foods, and a set of half-remembered reactions in their head.

## What it optimizes, in order

1. **Iron.** Around seven months this is the one that actually matters
   medically, so it is checked daily rather than treated as a nice-to-have.
   Iron from beans, lentils, grains, greens and seeds is non-heme and poorly
   absorbed on its own, so when the day's iron is plant-based the plan pairs it
   with a vitamin C food, which makes a large difference. Iron from meat and
   fish does not need the help and does not get a partner.
2. **Allergen rotation.** All nine stay in the diet instead of being ticked off
   once. See below for why this ignores dates.
3. **Bitter vegetables.** The real anti-picky-eater lever. Sweet acceptance is
   innate and bitter is learned, and the window where a baby will accept
   broccoli and spinach without an argument is early.
4. **Re-offering food that was rejected.** Acceptance usually takes 8 to 15
   exposures. Most trackers drop a food after a bad reception, which is
   backwards.
5. **New foods**, toward roughly 100 by the first birthday.

Every recommendation carries the reason that produced it. If the reason looks
wrong to whoever is reading the email, they are probably right, and the sheet is
the source of truth, not this program.

## Why allergens are not tracked by date

A hand-kept sheet reliably records the *first* time a food is given. Repeat
feedings mostly do not get written down, because nobody wants to log a spoonful
of yogurt for the fortieth time.

So "sesame is 20 days overdue" would be fiction, and the program never says it.
Instead all nine allergens are walked on an even weekly cycle: nine allergens
across seven days at two slots a day is fourteen turns, so each comes round once
or twice a week regardless of what was logged. The starting point shifts each
week so the same allergen is not always on a Monday.

The one allergen fact the sheet *does* support is "not tried yet", so that is
the only allergen line in the status table.

## What the Saturday email contains

- **The week as a table**: day, the main, what to serve alongside it, and what
  is new. Rows with something new or a re-try on them are highlighted.
- **The shopping list**, grouped by aisle, covering everything in the week
  including the flexible secondaries, so nothing needs a second trip.
- **The ones to read about first**: prep instructions for the age band and a
  Solid Starts link for each new food and each re-try.
- **Worth knowing**: choking notes, first-allergen protocol, age-stage reminders.
- **Where she stands**: iron, allergens not yet tried, vegetable-to-fruit ratio,
  bitter exposures, foods tried.

A separate job runs each morning to keep the Plan and Status tabs current. It
does not email.

## Two rules that are not negotiable in the code

**Reactions are never assumed.** If nobody confirms a day, the plan is logged as
`assumed` so the counters keep moving, but a reaction only ever gets recorded
because a human typed it.

**One new thing at a time.** Never two new foods in a day, never a first-time
allergen next to anything else new, and a re-challenge owns the whole day.
Otherwise a reaction tells you nothing about what caused it. First-time
allergens are also held for a weekend, when someone is home and unhurried.

## Setup

    solids setup

walks through it. In short:

1. **Google Cloud** — create a project, enable the Sheets API, create a service
   account, download a JSON key to `~/.config/solids/service-account.json`.
2. **Share the sheet** with that service account's email address, as Editor.
3. **Sending** — see below.
4. `solids init` creates the `Log`, `Plan` and `Status` tabs.
5. `solids doctor` checks all of the above and tells you what to click if
   something is missing.

Your own settings live in `~/.config/solids/config.json`, outside this repo.
Copy `solids.config.example.json` as a starting point. For the scheduled run,
put that same JSON in the `SOLIDS_CONFIG_JSON` repository secret along with
`GOOGLE_SERVICE_ACCOUNT_JSON`.

Don't use `cron` on a laptop. It will be closed.

## How the email gets sent

Set `mail_transport`. Three options, worst last.

**`outbox`** — the tracker writes the message to an `Outbox` tab and a small
Apps Script on the sheet mails it on a daily trigger. No new credential, no new
account. The script runs with permissions you already have on your own sheet and
can only send mail, never read your inbox. Install once from
`appsscript/Code.gs`. The tradeoff is two scheduled pieces; the script checks
the timestamp and sends nothing rather than mailing a stale plan.

**`resend`** — a send-only API key from resend.com, read from `RESEND_API_KEY`.
One process, no staleness window, and the key cannot do anything but send. Note
that an unverified Resend account can only send to the address that owns it, and
the shared `onboarding@resend.dev` sender has worse deliverability than a domain
you control. Verify a domain and point `mail_from` at it before relying on this.

**`smtp`** — a Gmail app password. Least code, but an app password is not scoped
to sending: it grants full IMAP access to the entire mailbox. Poor trade for one
message a week, especially sitting in a CI secret store.

## Commands

    solids today                    what to feed the baby now, and why
    solids plan --days 7            the week
    solids grocery --days 7         the shopping list
    solids status                   progress against the goals
    solids log broccoli --ate all
    solids log strawberry --reaction hives --attribution not_food
    solids foods --search bitter    what the catalog knows
    solids daily                    refresh the sheet for today, no email
    solids weekly --dry-run         render the Saturday email without sending
    solids doctor                   check credentials, sheet access and mail

Every command takes `--date`, so you can ask what it would have said on any day.

## The sheet

The hand-kept tab is **read-only** to this program. It is read once at startup to
seed history and never written to. Everything the tracker writes goes into tabs
it created:

- **Log** — one row per food per day: date, food, how much was eaten, reaction,
  whether you think it was actually the food, notes, and where the row came from.
- **Plan** — what was recommended each day, why, and a `Confirmed` column.
- **Status** — the dashboard, rewritten every morning.

The `Sure it was the food?` column matters most. Put `not_food` in it when you
think it was skin rather than something eaten, and the tracker believes you and
keeps the food in rotation. `Maybe` in the original tab's reaction column is read
as `unsure`, which still counts but records the doubt.

## The food catalog

`solids/data/foods.json`, about 150 foods, each with category, allergen, iron
level, vitamin C level, whether it is bitter, the earliest age it will be
suggested, prep notes by age band, and choking warnings where they matter.

Prep guidance is written here rather than copied from Solid Starts, and each food
links out to its Solid Starts page. Age bands are 6 to 8 months, 9 to 11, and 12
plus, so the shift from finger-length strips to bite-size pieces happens on its
own when the pincer grasp arrives around nine months.

To stop recommending something, add it to `excluded` in your config. To add a
food, add a row to the JSON.

## Tests

    .venv/bin/python -m pytest tests/ -q

The interesting ones are the invariants: never two new foods in a day, a
first-time allergen is always alone, re-challenges are spaced at least three days
apart, plant iron always gets a vitamin C partner, nothing below the baby's age
is suggested, honey never appears before one, nothing is ever described as
overdue, and every anchor comes with a reason.

`SOLIDS_FIXTURE=tests/fixture_history.json` runs any command against a local
sample history instead of Google, which is how to try changes without touching a
real sheet. That fixture is invented, not anyone's actual record.

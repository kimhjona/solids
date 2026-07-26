# Solids

A baby-led weaning tracker for Ava. It reads the Google Sheet Lisha already keeps,
decides what to feed her, says why, and emails it every morning.

The goal is not record keeping. It is that nobody has to hold nine allergens, a
hundred foods, and a set of half-remembered reactions in their head at 6am.

## What it optimizes, in order

1. **Iron.** At seven months this is the one that actually matters medically.
   Breastfed babies need an iron source most days, so it is checked daily rather
   than treated as a nice-to-have.
2. **Allergen rotation.** All nine stay in the diet instead of being ticked off
   once. Each one is targeted about every four days.
3. **Bitter vegetables.** This is the real anti-picky-eater lever. Sweet
   acceptance is innate and bitter is learned, and the window where she will
   accept broccoli and spinach without an argument is now.
4. **Re-offering food she rejected.** Acceptance usually takes 8 to 15 exposures.
   Most trackers drop a food after a bad reception, which is backwards. Apple got
   "didn't really like it" on 5/19, so apple comes back.
5. **New foods**, toward roughly 100 by her first birthday. This gets the least
   weight because she is already ahead of pace.

Every recommendation carries the reason that produced it. If the reason looks
wrong to whoever is reading the email, they are probably right, and the sheet is
the source of truth, not this program.

## What the daily email looks like

- **Yesterday**, and a link that takes one tap to say whether it happened.
- **Today's main**: one substantial food, with prep for her exact age band and a
  Solid Starts link.
- **Keep these in too**: one or two allergens that are due, usually a spread or a
  spoonful alongside the main rather than a separate production.
- **Round it out with any of these**: five familiar things she has taken before,
  so there is no shopping trip hiding inside the plan.
- **Worth knowing**: choking notes, first-allergen protocol, age-stage reminders.
- **Where she stands**: iron, allergens, vegetable-to-fruit ratio, foods tried.
- **Coming up**: the rest of the week, so the Saturday shop is real.

Saturday also gets a grocery email covering the next seven days.

## Two rules that are not negotiable in the code

**Reactions are never assumed.** If nobody confirms a day, the plan is logged as
`assumed` so the counters keep moving, but a reaction only ever gets recorded
because a human typed it.

**One new thing at a time.** Never two new foods in a day, never a first-time
allergen next to anything else new, and a re-challenge owns the whole day.
Otherwise a reaction tells you nothing about what caused it.

## Setup

    solids setup

walks through it. In short:

1. **Google Cloud** — create a project, enable the Sheets API, create a service
   account, download a JSON key to `~/.config/solids/service-account.json`.
2. **Share the sheet** with that service account's email address, as Editor.
3. **Sending** — see below. The default needs no credential.
4. `solids init` creates the `Log`, `Plan`, `Status` and `Outbox` tabs.
5. `solids daily --dry-run` renders the email to a file without sending it.

For the scheduled run, put the service account JSON in the
`GOOGLE_SERVICE_ACCOUNT_JSON` repository secret and let
`.github/workflows/daily.yml` handle it. It runs at 13:00 UTC, which is 6am
Pacific in summer.

Don't use `cron` on the laptop. It will be closed at 6am.

## How the email actually gets sent

Set `mail_transport` in the config. Three options, worst last.

**`outbox`** (the default). The tracker writes the message to an `Outbox` tab and
a 60-line Apps Script on the sheet mails it on a daily trigger. No new
credential, no new account, no billing. The script runs as whoever installs it,
using permissions they already have on their own sheet, and it can only send
mail. It cannot read anyone's inbox. Install it once from `appsscript/Code.gs`,
per the instructions at the top of that file.

The tradeoff is two scheduled things instead of one: GitHub Actions generates at
6am, the Apps Script trigger sends between 7 and 8am. If the generation step
fails, the script sees a stale timestamp and sends nothing rather than mailing
yesterday's plan.

**`resend`** (currently in use). A send-only API key from resend.com, read from
`RESEND_API_KEY`. Everything stays in one process, so there is no staleness
window, and the key cannot do anything except send.

Two things to know about the current setup. The account is owned by
`lishajonfamily@gmail.com`, and on an unverified Resend account you can only send
to the address that owns it. That is fine today because that is exactly where the
email goes, but it means the tracker cannot mail anyone else until a domain is
verified. And the `from` address is `onboarding@resend.dev`, Resend's shared test
sender, which is not something to depend on long term: deliverability through a
shared sender is worse and the first few may land in spam.

The durable fix is to verify a domain at resend.com/domains and change
`mail_from` to an address on it. `send.tellfutureme.com` is already on the other
Resend account in a failed state, so its DKIM and SPF records need fixing.

**`smtp`** — a Gmail app password. It works and it is the least code, but an app
password is not scoped to sending. It grants full IMAP access to the entire
mailbox, which is a poor trade for one message a day, especially sitting in a CI
secret store. Use it only if the other two are genuinely worse for you.

## The sheet

The tab Lisha keeps is **read-only** to this program. It is read once at startup
to seed history and never written to. Everything the tracker writes goes into
tabs it created:

- **Log** — one row per food per day: date, food, how much she ate, reaction,
  whether you think it was actually the food, notes, and where the row came from.
- **Plan** — what was recommended each day, why, and a `Confirmed` column that
  the email links to.
- **Status** — the dashboard, rewritten every morning.

The `Sure it was the food?` column is the one that matters most. Put `not_food`
in it when you think it was her skin rather than something she ate, and the
tracker will believe you and keep the food in rotation.

## Commands

    solids today                    what to feed her now, and why
    solids plan --days 7            the week
    solids grocery --days 7         the shopping list
    solids status                   where she stands against the goals
    solids log broccoli --ate all
    solids log strawberry --reaction hives --attribution not_food
    solids foods --search bitter    what the catalog knows
    solids daily --dry-run          render the email without sending

Every command takes `--date` so you can ask what it would have said on any day.

## The food catalog

`solids/data/foods.json`, about 150 foods, each with category, allergen, iron
level, whether it is bitter, the earliest age we will suggest it, prep notes by
age band, and choking warnings where they matter.

Prep guidance is written here rather than copied from Solid Starts, and each food
links out to its Solid Starts page. Age bands are 6 to 8 months, 9 to 11, and 12
plus, so the shift from finger-length strips to bite-size pieces happens on its
own when her pincer grasp arrives around nine months.

To stop recommending something, add it to `excluded` in the config. To add a
food, add a row to the JSON.

## Two things to check

**Her birthday** is set to 2025-12-27 in `solids.config.json`, derived from "two
days short of seven months" on 25 July 2026. But the sheet's first entry is
5/17, which would have put her at about four months and three weeks, earlier than
solids usually start. One of those is off. Fix `birthday` if it is the config,
because every prep instruction depends on it.

**The three reactions** in the sheet: raspberry 5/21, edamame 6/21, blueberry
7/10, plus the pistachio face splotches on 7/17 that were logged as no reaction.
The tracker treats all of these as re-challengeable rather than permanent, on the
basis that acidic foods commonly cause harmless contact rash around the mouth,
and that soy already passed on re-challenge when tofu was fine on 7/14. That is a
judgement call encoded in software. Tell the pediatrician about all four and let
them set the terms, then change `rechallenge_gap_days` or `excluded` to match
what they say.

## Tests

    .venv/bin/python -m pytest tests/ -q

48 tests. The interesting ones are the invariants: never two new foods in a day,
a first-time allergen is always alone, re-challenges are spaced at least three
days apart, nothing below her age is suggested, honey never appears before one,
and every anchor comes with a reason.

`SOLIDS_FIXTURE=tests/fixture_history.json` runs any command against a local copy
of the sheet's history instead of Google, which is how to try changes without
touching the real thing.

# Plan: tap instead of type (admin quality-of-life)

Date: 2026-10-07 · Status: **draft, awaiting treasurer answers (bottom)**
Research: `docs/research/2026-10-07-easier-admin-commands.md` (every Telegram
fact there is cited to the official docs).

## The problem

Admin commands need typed arguments. `/newterm` is the worst: ~100 characters
of dates and prices, typed on a phone, once a term — exactly the kind of thing
you forget the format of by next term.

## What Telegram allows (short version)

- **Buttons under a message** (inline buttons) work everywhere, need nothing
  extra on the Pi. This is what `/pay` already uses (shirt yes/no, sizes).
- **The `/` menu** can show a *different* command list per person: members see
  4 commands, admins see theirs, the treasurer sees everything.
- **No date picker exists** in Telegram for bots. Best substitute: preset date
  buttons ("Today", "Next Mon", "+13 weeks") with typing as a fallback.
- Fancy option rejected: a mini web form (needs a separate web page to host and
  maintain) — overkill for one form a term.

## Build items

### 1. Role-based `/` menu — small
Tap `/` (or the Menu button) and see only the commands you can use, with a
one-line description each. Members: `/start /status /pay /help`. Admins: plus
`/unpaid /stats /members /roster /setgroup`. Treasurer: plus everything else.
Refreshed on startup and whenever `/addadmin`, `/removeadmin` or
`/transfertreasurer` changes someone's role. (Cosmetic only — the existing
permission checks stay.)

### 2. `/newterm` button wizard — medium
Send `/newterm` alone (tap it in the menu) and the bot asks, one step at a time:

1. **Name** — button with a suggestion (e.g. "Term 2" if last was "Term 1"), or type one.
2. **Start date** — `Today` · `Next Mon` · type a date.
3. **End date** — `Same length as last term (→ 1 Dec)` · `+13 weeks` · type.
4. **Deadline** — `2 weeks after start (→ 15 Sep)` · `3 weeks` · type.
5. **Prices** — `Same as last term: comp $20 · rec $25 · rec+shirt $30 · shirt $15`
   · or type new ones (see Q2).
6. **Confirm** — full summary in plain words → `Create term` / `Cancel`.

Nothing is created until **Create term**, so a restart or a mistake mid-way
costs nothing. Typed dates accept `2026-09-01`, `1/9/2026` or `1 Sep 2026`.
The old one-line `/newterm ...` keeps working. All existing checks (dates in
order, fee rules, shirt=0) still apply — the wizard just fills them in.

### 3. Confirm buttons on risky commands — small
`/markpaid 1010123` → "Mark **Alice Tan (1010123)** paid for Term 1, S$25?
[Confirm] [Cancel]". Same for `/revoke` and `/transfertreasurer`. Catches a
mistyped ID before it moves money or powers.

### 4. Tap lists instead of IDs — small
- `/removeadmin` alone → one button per current admin.
- `/relink` alone → armed relinks, each with a `Cancel` button.
- `/unpaid` → a `Mark paid` button per person (for cash payments), which
  goes through the same confirm as item 3. *(See Q3.)*

### 5. Lock the Telegram library version — tiny
`requirements.txt` says `>=21.0`, so a future Pi reinstall could silently pull
a new major version (23.x) the tests never ran on. Pin to `>=22.8,<23` (both
the Pi and laptop run 22.8 today).

## Not doing (and when to revisit)

| Idea | Why not |
|---|---|
| Calendar grid of dates | ~45 buttons per month for a once-a-term input; presets cover it. Add if typed dates prove annoying. |
| Mini web form | Needs a hosted page + second thing to maintain. Revisit if several more long forms appear. |
| Buttons for `/settings`, `/roster`, `/setgroup` | Used once ever / input is a pasted list — typing is fine. |
| Copy-command button | The wizard replaces it. |

## Effort & safety

Items 1–5: roughly a day of build, then tests for every new path, a
`/code-review`, then `bash ~/clubbot/deploy/update.sh` on the Pi. Can be built
**after** your real-Telegram test run — nothing here blocks it.

## Questions for the treasurer

**Q1 — Scope.** Build all of 1–5? *Recommended: yes, all five.* (Minimum
worthwhile: 1 + 2 + 5.)

**Q2 — Typing new prices.** When prices change, either
(a) one line in a fixed order: `20 25 30 15` (comp, rec, rec+shirt, shirt), the
bot echoes it back labelled before you confirm; or
(b) four separate questions, each with a `Same as last term ($20)` button.
*Recommended: (b)* — slower by three taps but impossible to get the order wrong.

**Q3 — `Mark paid` buttons on `/unpaid`.** Useful only if people still pay you
cash/outside the bot. *Recommended: yes* — it's cheap and always needs a confirm.

**Q4 — Order vs the test run.** Do the real-Telegram test run (checklist step
3) first with the copy-paste command, then build this? *Recommended: yes* — a
test run may surface bugs that should be fixed first.

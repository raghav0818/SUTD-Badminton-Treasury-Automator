# v2 build list (grilling outcome, 2026-10-03 → 2026-10-04)

Status: **awaiting treasurer confirmation. Nothing here is built yet.**
Context: the bot works but nobody used it, and the real problem is members not
paying. v2 makes it chase them, handles the three prices, and drops the weekly
audit.

## Decisions (settled with the treasurer)

- **Keep Telegram.** Both club group chats already live there, and only a bot
  can DM and nag people. Limitation: a bot can't list group members or DM
  someone who never opened it. The roster and group posts work around this.
- **Who is a member.**
  - Competitive = on the **roster**, a list (name + SUTD ID) the exco pastes
    into the bot each term. Everyone on the roster must pay S$20.
  - Recreational = anyone in the rec group chat who chooses to buy membership.
  - Only people in either group chat can register (checked with `getChatMember`).
  - Someone on the roster who is also in the rec chat is treated as competitive.
    One S$20 payment covers both competitive and rec sessions; there is no
    extra fee.
- **Prices per term:** competitive S$20, competitive + shirt S$35 (S$20 plus
  a S$15 shirt), rec S$25, rec with shirt S$30. They are set in `/newterm`,
  never hardcoded:
  `/newterm Term 1 2026-09-01 2026-12-01 deadline=2026-09-15 comp=20 rec=25 recshirt=30 shirt=15`
- **Shirts.** Everyone taps "with shirt" or "without shirt", then picks a size
  (XS–XXL) if they want the shirt. The QR has the amount locked in: S$25 or
  S$30 for rec, S$20 or S$35 for competitive. The verifier accepts either
  amount for that member's category and records the shirt from what was
  actually paid.
- **No weekly audit.** The treasurer accepts that a fake receipt could go
  unnoticed. Mitigation: once per term, compare the per-price counts in
  `/stats` against FLYMAX.
- **The bot replaces the MS Form completely.** Once it's live, the form is
  retired and every membership payment goes through the bot.
- **After the deadline there's no penalty.** The treasurer checks `/unpaid` to
  see who never paid; the bot does nothing more.
- **Updates are manual and on demand** (the treasurer runs one command on the
  Pi). No monitoring or heartbeat.
- **Gemini:** a 3.x model ID on paid billing. The free tier may train on
  receipts, and `gemini-2.5-flash` is no longer offered to new keys.

## Build items

### A. Get it running again (do first)
1. Switch the default `GEMINI_MODEL` to a 3.x Flash model. Preflight must pass
   on a real receipt.
2. Fresh Pi setup with `git clone` (the repo is public), plus `deploy/update.sh`
   (`git pull` + `setup_pi.sh`). Move the Pi's existing `.env` aside first.
3. A weekly `clubbot.db` backup sent to the treasurer's Telegram DM.

### B. Three prices + shirts
4. Terms store the three fees and a deadline. Update `/newterm` parsing to match.
5. Payments store the category, the shirt (yes/no) and the shirt size. The
   verifier takes the set of valid amounts for that member's category.
6. `/pay` flow for everyone: shirt buttons, then size buttons, then the QR at
   the matching amount.
7. `/stats`: count × price per category, plus totals (the once-per-term
   FLYMAX check).
8. Sheet: add a Shirts tab with each person, their size, and counts per size.

### C. Chase people to pay
9. `/roster`: the treasurer pastes `Name, 1010xxxx` lines, which replace the
   whole list. `/unpaid` and `/stats` then include roster people who never
   opened the bot (e.g. "18/30 paid").
10. Deadline reminders by DM, each with the QR: days 3, 7, 10 and 13 after the
    term starts (only the ones before the deadline), plus a last call on
    deadline morning.
11. Group posts twice a week until the deadline, showing progress counts only
    (no names) and a tap-to-pay link (`t.me/<bot>?start=pay`). Can be switched
    off with `/settings group_posts off`.
12. A "Not continuing this term" button on reminders. It sets
    `members.active = 0` for that term and stops the nagging.
13. Send the QR immediately to anyone who registers mid-term.

### D. Clean-up and robustness
14. Delete the Monday audit digest, the "All found" button, `/audit` and
    `/flag`. Keep `/revoke`. Leave the old DB columns alone (no migration).
15. If Gemini fails, keep the payment pending and retry later from the stored
    Telegram file_id. After a few failures, ping the treasurer.
16. Add a consent line at `/start` (what's stored, and why).

## Implementation notes (binding for whoever builds it)

- **Gemini default model:** `gemini-3.1-flash-lite` (cheapest 3.x per the
  research doc). It can still be overridden via `GEMINI_MODEL` in `.env`.
- **Category:** a member is `competitive` if their SUTD ID is in the `roster`
  table, otherwise `recreational`. Compute it when the payment row is set up
  for the term and store it on the payment. The roster is one global table
  (`sutd_id` PK, `name`) that `/roster` replaces wholesale.
- **Fees:** `terms` gains `comp_fee_cents`, `rec_fee_cents`,
  `recshirt_fee_cents`, `shirt_fee_cents` and `deadline`. The legacy
  `fee_cents` stays NOT NULL: fill it with the comp fee. Valid amounts are comp
  `{comp, comp+shirt}` and rec `{rec, recshirt}`. Payments record
  `with_shirt = (amount paid != base fee)`.
- **Pay flow:** `/pay`, the "Pay now" button and `/start pay` all go through
  [With shirt] [Without shirt], then size [XS S M L XL XXL], then the QR at the
  matching amount. Choosing again just reissues a QR. `qr_issued_at` keeps its
  COALESCE first-QR semantics, because the payment-time window depends on it.
- **Blast/reminders don't attach a QR.** Because the amount now depends on the
  shirt choice, they send text plus the [Pay now] and
  [Not continuing this term] buttons.
- **Opt-out is per term:** a nullable `payments.opted_out_at` column. Opted-out
  members are skipped by reminders and left out of `/unpaid`. Paying later
  still works. `members.active` is not used for this.
- **Group chats:** an admin sends `/setgroup rec` or `/setgroup comp` inside
  the group, which stores that chat's id in `settings`. Registration gate: you
  must be in one of the configured groups (`getChatMember` status `member`,
  `administrator`, `creator` or `restricted`) or on the roster. If no group is
  configured yet, the gate is skipped.
- **Scheduling:**
  - Reminders and group posts are restart-safe through a
    `term_events(term_id, event, sent_at)` stamp table with PK
    `(term_id, event)`, like `reminder7_sent_at`.
  - DM reminders go at 10:00 SGT. Group posts go Mon and Thu at 12:00 SGT,
    from term start until the deadline.
- **Backup:** Sundays at 03:00 SGT, `sqlite3` `.backup()` to a temp file,
  `send_document` to the treasurer, then delete the temp file.
- **Retry:** a pending payment whose extraction failed keeps its
  `screenshot_file_id`. A job retries every 15 min, up to 4 tries, then the
  treasurer gets one message. The image hash stays reserved while it retries.

## Still open

- The launch date: the first real term opened with `/newterm` after part A is
  live on the Pi. Before that, wipe the test data once (see the CLAUDE.md
  launch checklist).

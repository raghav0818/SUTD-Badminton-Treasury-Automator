# Plan: catch fake receipts by matching against FLYMAX

Date: 2026-10-11. Research: `docs/research/2026-10-11-fake-receipt-defences.md`.
Status: treasurer approved; built 2026-10-11 (`clubbot/reconcile.py`,
`payments.reconcile`). Instead of a new column/table, it reuses
`audit_confirmed_at` (bank-confirmed) and `flagged_at` (no match); bank lines
aren't stored, because already-confirmed payments keep their own line during
each run. The nudge rides on the weekly backup DM.

## Why

A careful AI-made receipt that copies the real Billing ID passes every
screenshot check. No detector can tell (research §4). The only proof that money
moved is the school's bank record, which is FLYMAX.

## Decisions (grilled)

1. **Provisional = paid.** A receipt that passes the checks still marks the
   member paid right away: no reminders, no removal, sessions OK. A new
   `payments.bank_confirmed_at` column (NULL = provisional) records the bank
   match, so every existing `status = 'verified'` query keeps working.
2. **Bank data comes only from the treasurer**, via `/reconcile` in a private
   chat: screenshots of the FLYMAX transaction list (always possible). Add CSV
   import only once a sample export is seen.
3. **Gemini reads each FLYMAX screenshot into bank lines** (amount, time,
   payer name, transaction ID if shown). Lines go in a `bank_lines` table,
   deduplicated (by transaction ID, otherwise amount + time + name), so
   overlapping screenshots are safe.
4. **Matching, one bank line confirms at most one payment.**
   - The transaction ID equals the receipt's ID → confirmed.
   - Otherwise: same amount, time within ±15 min (same day if FLYMAX shows no
     time), name close to the registered name → confirmed. If only the name
     differs (e.g. a parent paid) → listed under ⚠️ for the treasurer.
   - A fake that "steals" a real line leaves the real payer unmatched, so it
     still surfaces.
5. **"Unmatched" only counts inside the covered window**: the receipt time is
   between the earliest and the latest line sent, with a 1-day buffer for bank
   lag. Anything outside that window stays provisional.
6. **Never automatic punishment.** Each ⚠️ line gets [Revoke] / [It's fine]
   buttons. Revoke uses the existing revoke path; the member then becomes
   unpaid and the existing reminders and rec-chat sweep take over. A Gemini
   misread can only cause a false ⚠️, never a revoke.
7. **What gets checked:** every verified payment, including ones the treasurer
   approved from review. Not `/markpaid`, which the treasurer has vouched for.
8. **Report after each `/reconcile`:** ✅ confirmed N · ⏳ still provisional N ·
   ⚠️ no bank match (list + buttons) · 🏦 bank lines with no member N.
9. **Cadence:** a Sunday nudge (alongside the backup DM) while provisional
   payments older than 2 days exist. Weekly also keeps within MAX's 90-day
   history.
10. **Shirts:** the treasurer orders only bank-confirmed shirts. The Sheet
    gains a "Bank" column (Payments + Shirts tabs).
11. **Members see nothing new**, except a deterrence line in the pay prompt:
    "Every payment is matched against SUTD's bank record. Fake or edited
    receipts mean removal from the club and may be reported."
12. **Launch now** with the current checks + the deterrence line; reconcile by
    hand in FLYMAX until `/reconcile` ships.

## Rejected

- AI-image / forgery detection: at chance on AI document fakes.
- Unique cents per member: a receipt swap costs the club no money, and
  one-to-one matching + name handles identity. Revisit only if matching is
  ambiguous in practice.
- Payment gateways: break the Billing ID constraint.
- Auto-revoke: a mismatch can be honest.

## Treasurer to check (not blocking)

- In FLYMAX, find today's payments (UOB refs `2610112184655201`,
  `2610112184752256`). Does FLYMAX show the same number? If yes, matching is
  exact; if not, it uses amount + time + name.
- Whether FLYMAX has a download/report option. If yes, send one sample file.

## Build list

1. Deterrence line in the pay prompt.
2. `bank_confirmed_at` column + `bank_lines` table (db.py migration).
3. Gemini prompt/schema for a FLYMAX list screenshot (gemini.py).
4. `/reconcile` (treasurer only): collect images → Done → match → report with
   buttons (admin.py, plus a pure matching function in payments.py).
5. Sunday nudge (scheduler.py); a "Bank" column in the Sheet (sheets.py);
   provisional vs confirmed counts in `/stats`.
6. Tests: the matcher (ID match, fuzzy match, one-line-one-payment, window
   edges, name mismatch → ⚠️, dedupe of overlapping screenshots).

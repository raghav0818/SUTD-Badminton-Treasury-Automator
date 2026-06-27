  ### 1. Member registration

  A member messages the Telegram bot and runs /start.

  The bot records:

  - Telegram user ID
  - Name
  - SUTD student ID
  - Telegram username

  The Telegram user ID is the main identity because usernames can change.

  ### 2. Treasurer starts a collection

  You create a term and specify its membership fee—for example, S$20.

  The bot marks every member as unpaid and sends them payment instructions.

  This term-management part has not been built yet.

  ### 3. Bot creates the payment QR

  The member requests a QR using /pay.

  The QR contains:

  - The club’s UEN.
  - The required school Billing ID.
  - The exact fee, locked so it cannot be edited.
  - A unique reference such as BDM-T5-047.

  All generated QRs pay the same club account. Only the member reference differs.

  Your experiment proved that this arrangement works:

  - Flimax receives the payment because the original Billing ID remains.
  - The member’s bank receipt shows the unique reference.
  - Flimax does not show that reference.

  ### 4. Member pays

  The member scans their personal QR using a banking application and completes the payment.

  Their banking receipt should show:

  - Successful payment
  - SUTD as recipient
  - Exact fee
  - Unique reference
  - Payment date
  - Transaction ID

  ### 5. Member uploads the receipt

  The member sends a screenshot of the successful payment page to the Telegram bot.

  The bot does not permanently download or store the screenshot. It retains Telegram’s file reference and the extracted information.

  ### 6. Gemini reads the screenshot

  Gemini examines the screenshot and extracts structured fields, such as:

  Status: Successful
  Amount: S$20.00
  Recipient: Singapore University of Technology and Design
  Reference: BDM-T5-047
  Date: 20 June 2026
  Transaction ID: 123456789

  Gemini does not decide whether the member has paid. It only reads the image.

  ### 7. Normal code verifies the details

  The bot then performs fixed checks:

  1. Does the amount equal the membership fee?
  2. Is the recipient the school?
  3. Does BDM-T5-047 belong to this member?
  4. Is the payment date recent and within the collection period?
  5. Has this transaction ID or screenshot already been submitted?
  6. Does the image represent a completed payment rather than a confirmation page?

  If every check passes, the member is provisionally marked paid. If something fails, the bot asks for another screenshot or sends the case to you.

  ### 8. Treasurer audits against Flimax

  The important limitation is that a screenshot alone is not absolute proof. Someone could potentially edit a screenshot.

  Since the bot cannot access Flimax, it cannot immediately confirm that money arrived. Instead, it sends you a weekly list such as:

  Expected in Flimax:

  Raghav Rajesh — S$20 — 20 June
  Alice Tan — S$20 — 21 June

  You compare this with Flimax, which shows payer name, amount, date, transaction ID, and Billing ID.

  - If all payments appear, you press “All found.”
  - If one is missing, you flag that member.
  - The bot revokes their paid status and contacts them.

  ### What is automated?

  The bot handles:

  - Registration
  - QR creation
  - Reminders
  - Reading receipts
  - Initial verification
  - Duplicate detection
  - Member status
  - Preparing the weekly audit list

  You only perform a short weekly comparison with Flimax and handle unusual cases.

  ### What your QR test established

  Variant A provides the workable arrangement:

  Member’s bank receipt:
  Unique reference visible → automatic screenshot matching

  Flimax:
  Payer name visible → weekly confirmation that money arrived

  Variant B failed because replacing the school Billing ID disrupted routing. Therefore, the original Billing ID must always remain unchanged.

  So the bot provides fast provisional verification from the receipt, while your weekly Flimax audit provides the final bank-side safeguard.


### STARTING
how to start tele bot: .venv\Scripts\python.exe -m clubbot
make sure that all the requirements are downloaded. 

###  COMMANDS

 /start  │ Register (asks name → SUTD ID → confirm). If already registered, shows your status instead. │
  ├─────────┼─────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /status │ Your membership + payment status for the current term.                                      │
  ├─────────┼─────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /pay    │ Get your personal PayNow QR for the current term.                                           │
  ├─────────┼─────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /help   │ Show the command list (treasurer sees extra commands here).                                 │
  ├─────────┼─────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /cancel │ Cancel registration — only works while you're mid-registration.                             │
  └─────────┴─────────────────────────────────────────────────────────────────────────────────────────────┘

  (Not a command, but part of the flow: after paying, a member sends the payment screenshot to the bot and it gets checked automatically.)

  For you (treasurer)

  ┌─────────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────┐
  │               Command               │                                           What it does                                           │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /newterm <name> <fee> <start> <end> │ Open a paying term, e.g. /newterm Test 0.05 2026-06-20 2026-07-04. Auto-sends everyone their QR. │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /unpaid                             │ List members who haven't paid this term.                                                         │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /stats                              │ Summary: registered / paid / unpaid / exceptions / flagged.                                      │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /members                            │ List all registered members.                                                                     │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /markpaid <sutd_id>                 │ Manually mark someone paid (for cash payers).                                                    │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /remind                             │ Send the reminder to unpaid members right now.                                                   │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /audit                              │ Get the weekly FLYMAX checklist on demand.                                                       │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /flag <sutd_id>                     │ Flag a member whose money you couldn't find in FLYMAX (no message sent to them).                 │
  ├─────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ /revoke <sutd_id>                   │ Remove a member's paid status (also DMs them).                                                   │

  Phase 4 treasurer commands (added for launch):

  - /addadmin <sutd_id>          Grant a registered member admin access.
  - /removeadmin <sutd_id>       Remove a member's admin access (never the treasurer).
  - /transfertreasurer <sutd_id> Hand over the treasurer role (asks you to confirm).
  - /relink <sutd_id> [new_id]   Move a member to a new Telegram account, keeping
                                 their payment history. With no new_id, uses the
                                 pending request from when the new account ran /start.
  - /settings                    Show the PayNow config. /settings set <key> <value>
                                 changes it; the UEN/Billing ID ask you to confirm.
  - /sync                        Refresh the read-only Google Sheet now.
  - /backup                      Save a database backup now (also runs nightly).




  ❯ ok so now that everything works, what is the next step in development                                                                                                                          

● Based on MEMORY.md, there are actually two immediate things before jumping into Phase 4:

  Right now (before Phase 4):
  1. Phase 3 has never been committed to git — all the scheduler, admin commands, and audit work is only in your working tree. You should commit it.
  2. Phase 3 has never been tested on live Telegram — the suggested test path from MEMORY.md is:
  $env:DB_PATH="test.db"; .venv\Scripts\python -m clubbot
  2. Then in Telegram: /newterm Test 0.05 (with yesterday as start date) → expect an auto QR to be DM'd, then test /stats, /markpaid, /audit, /flag, /revoke, /remind.

  Phase 4 (the next engineering phase):
  - /addadmin / /removeadmin — let you delegate admin access to committee members
  - /transfertreasurer — hand off the bot to the next treasurer
  - /relink <sutd_id> — for members who switch Telegram accounts (already mentioned in registration text)
  - /settings — make the PayNow UEN/merchant name editable without touching code
  - Google Sheet mirror — read-only view of payment records, useful for the club's records

  Pending decisions you need to make:
  - Real term fee (S$20.00) and dates to open the first real term
  - Gemini free tier vs paid at launch
  - Whether to ask SUTD finance about per-transaction email alerts (optional, would give bank-side confirmation)

  I'd suggest: commit Phase 3 → test it live → then decide if you want Phase 4 now or just open the first real term and monitor it. Which do you want to tackle first?
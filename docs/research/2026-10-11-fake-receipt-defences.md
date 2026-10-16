# Defences against fake payment receipts

Research date: 2026-10-11. Scope: how ShuttleBuddy could stop a fabricated
bank success screenshot from being auto-verified. No code was changed.

This builds on `2026-10-03-payment-verification-alternatives.md` (called "the
10-03 note" below). That note already covers: no OCR or vision model can spot
a forged screenshot; error-level analysis doesn't work on PNGs; the DBS IDEAL
Incoming Funds Alert; DBS RAPID; and Stripe, HitPay and Telegram Payments
being ruled out. Those points are not repeated here.

**Trigger.** Today the treasurer sent the bot an AI-generated receipt. It went
to manual review only because the generated Billing ID was slightly wrong.
Every value the checks look at (success wording, amount, "SINGAPORE UNIVERSITY
OF T", Billing ID `200913519CSL5EIU616138169`, a plausible time) is visible on
any genuine receipt. A careful fake that copies them and invents a new
transaction number passes every check today. The image hash and the bank
reference are both new, so neither duplicate check catches it.

---

## TL;DR

- **Everyone who has dealt with this uses the same fix: check the receiving
  account, never the screenshot.** Singapore Police, Carousell, Paytm and DBS
  itself all say so (section 1). Nobody relies on detecting the fake.
- **"FLYMAX" is very likely the DBS MAX app** (UNVERIFIED, but the fields fit).
  DBS has no product called FLYMAX. DBS MAX is DBS's PayNow/SGQR collections
  app and portal. If that's right, three of its documented features change the
  picture (section 2):
  1. Transactions can be **downloaded as xls/csv**.
  2. **Outlet Managers get a push notification for every payment received.**
  3. DBS's own FAQ answers "customer showed me a successful QR payment screen"
     with "check the MAX notification or the Transactions list".
  It also has a limit: **history and reports cover only the last 90 days**.
  The end-of-term count check could therefore miss early-term payments.
- **Best cheap fix: match transactions against a bank-side export.** The
  treasurer downloads the MAX transaction CSV each week and sends it to the
  bot. The bot matches each auto-verified receipt's bank reference, amount and
  time against it. A fake with an invented transaction number has no matching
  line. This needs no SUTD Finance involvement if the treasurer's MAX role can
  download reports. One thing to check first: is the "transaction ID" FLYMAX
  shows the same reference that appears on payer receipts? (UNVERIFIED.)
- **Add unique cents per member** (for example S$20.37) so that every bank line
  maps to exactly one member. The QR already locks the amount, so this works
  with the fixed Billing ID. It also closes the "two members swap one receipt"
  risk that the treasurer had accepted. The catch is non-round fees.
- **Mark payments "provisional" until they are reconciled.** Auto-verification
  becomes "probably paid". Only a matching bank line confirms a payment.
- **Forensics won't save you.** AI-generated receipts beat commercial
  detectors. On a 2026 benchmark of AI-forged documents, GPT-4o scored AUC
  0.509, which is chance. C2PA and SynthID only ever prove that an image *has*
  a signature or watermark. A missing one proves nothing. Telegram's photo mode
  probably strips metadata anyway, and an edit made in an ordinary image editor
  carries no AI watermark at all.
- **Payment gateways still fail the constraint.** HitPay requires the payout
  account name to match the registered business. Only SUTD could be the
  merchant, and payouts would arrive without the Billing ID (section 3).

---

## Options at a glance

| Option | What it proves | Needs from whom | Cost | Residual risk |
|---|---|---|---|---|
| Today: screenshot checks + end-of-term count | Receipt looks right and is not a reuse | — | $0 | A careful fake auto-verifies. The count shows only *that* something is missing, not *who*. MAX's 90-day window may hide early payments (UNVERIFIED that FLYMAX = MAX). |
| **Weekly MAX CSV reconciliation** (treasurer forwards the export to the bot) | A real credit with this reference/amount/time exists in the school account | Treasurer (about 2 min a week); a MAX role that can download reports | $0 | Depends on the receipt ref = MAX transaction ID (UNVERIFIED). A fake is caught days later, not instantly. Receipt swapping still possible (fixed by unique cents). |
| **Unique cents per member** | Each bank line belongs to one specific member | Treasurer decides on non-round fees | $0 | Members may be confused. Cents run out above ~99 members per fee tier unless the range widens. |
| **Provisional status until reconciled** | Nothing new; limits the damage while unconfirmed | Treasurer accepts that "paid" lags by a few days | $0 | Only as good as the reconciliation behind it |
| MAX Outlet Manager push notifications | Each real credit, in real time, on the treasurer's phone | MAX Group User at SUTD sets the treasurer as Outlet Manager (if not already) | $0 | Human-read only; the bot can't see a phone notification |
| MAX emailed EOD / ad-hoc reports to a club inbox | Bank-side daily list | Treasurer's MAX user email set to a club inbox; report password | $0 | The bot needs inbox access and must parse a password-protected file (format UNVERIFIED) |
| IDEAL Incoming Funds Alert (10-03 note) | Per-credit bank email | SUTD Finance; alert covers the whole account, not only the club | Historically S$8/user/month (2013 form) | Finance unlikely to forward all of SUTD's credits. Alert fields UNVERIFIED. |
| Screen recording instead of screenshot | Makes faking harder | All members (extra effort) | $0 | Raises the effort only; AI video and fake apps exist. Not proof. |
| C2PA / SynthID / AI detector | Only that *some* images are signed/watermarked | — | $0 | Missing signals prove nothing. Detectors are at chance on AI document forgeries. Not a defence. |
| Random spot checks (sample against MAX) | A sample is genuine | Treasurer | $0 | Weak: checking 20 of 200 catches one of 2 fakes only ~19% of the time |
| Deterrence (published line-by-line audit + consequences) | Nothing | Exco states the policy | $0 | Doesn't stop a determined faker, but makes the expected gain worthless |
| Payment gateway (Stripe/HitPay/Xendit…) | Server-side webhook | SUTD as merchant | ~1–1.3% per payment | Violates "money lands via the Billing ID". Not viable for the club. |
| DBS MAX API / RAPID (SUTD integrates) | Real-time bank confirmation | SUTD Finance/IT as API customer | Unknown | Not achievable for a club (10-03 note) |

---

## 1. How others handle "screenshot as proof of payment"

The answer is the same everywhere: **don't trust the screenshot; check the
receiving account.** Nobody depends on detecting the forgery.

- **Singapore Police.** In 2025 a man was charged after he "produced an edited
  screenshot of a PayNow transfer and left with electronic products without
  making actual payment". The release reminds the public that cheating "carries
  an imprisonment term of up to 10 years and a fine", and says to "verify
  payments before releasing goods or services"
  ([SPF, 2 Nov 2025](https://www.police.gov.sg/media-hub/news/2025/11/20251102_man_to_be_charged_for_cheating)).
  An earlier case involved doctored PayNow screenshots to a money changer's
  corporate account for over S$13,000
  ([SPF, 27 Jun 2022](https://www.police.gov.sg/media-room/news/20220627_man_arr_for_cheating)).
- **A hawker case** (S$55.50) was found only by "cross-referencing physical
  cashier receipts with PayNow transaction records", which is a
  reconciliation. The son's other tip, to watch for animated rather than
  static success pages, only works face to face
  ([Mothership, Jan 2025](https://mothership.sg/2025/01/paynow-scam-hawker-east-coast/),
  news report).
- **DBS itself**, in the DBS MAX FAQ, under "Why did I not receive the funds
  when customer showed me a successful QR payment screen?", tells merchants to
  check that the "Payment notification is received on MAX App or transaction is
  recorded under Transactions"
  ([DBS MAX FAQ](https://www.dbs.com.sg/sme/dbs-max-oms/1ms-faq.pdf)).
- **Carousell:** "Never ship an item based on a screenshot of a payment
  confirmation. Always log in to your bank account or payment app independently"
  ([Carousell](https://support.carousell.com/hc/en-us/articles/49107638285849-Common-Scam-Types-on-Carousell)).
- **India (UPI) is the closest large-scale parallel.** Fake-payment apps were
  common enough that Paytm built the Soundbox, a speaker that reads out
  server-confirmed payments. Paytm's own advice is "Don't Rely Only on
  Screenshots: Verify in the app, as screenshots can be fake"
  ([Paytm support](https://paytm.com/support/paytm-soundbox/how-merchants-can-verify-payment-receipts)).
  A real-time bank-side signal is the industry answer.
- **Corporate expense tools** report that AI-generated receipts jumped after
  GPT-4o image generation (March 2025). AppZen says fake AI receipts were about
  14% of fraudulent documents in September 2025. SAP Concur's line is "do not
  trust your eyes"
  ([Daily Dot](https://dailydot.com/chatgpt-faking-receipts),
  [Slashdot summary of FT](https://slashdot.org/story/25/10/27/1829258/companies-battle-wave-of-ai-generated-fake-expense-receipts),
  secondary sources). Their response is to match receipts against card-feed
  data, the same idea as reconciliation (UNVERIFIED from a primary vendor doc).
- **Student clubs that collect by screenshot plus Google Form** mostly accept
  the risk and reconcile by hand. No primary source exists for this; it is
  anecdotal (UNVERIFIED).

---

## 2. Bank-side confirmation for the school account

### 2a. Is "FLYMAX" actually DBS MAX?

DBS has no product called FLYMAX. The search hits are drone apps (the 10-03
note found the same). The PRD (§4) says FLYMAX shows "payer name, amount,
date, transaction ID, and the fixed school Billing ID". That matches the DBS
MAX Transactions view, which can be searched by "Bill reference number and
Original Transaction ID" ([DBS MAX FAQ](https://www.dbs.com.sg/sme/dbs-max-oms/1ms-faq.pdf)).
The school QR's proxy is "UEN + suffix", the form DBS MAX/SGQR uses (same FAQ).
**UNVERIFIED. The treasurer can confirm in one look: the app icon/name, or
whether it logs in with an IDEAL ORG ID / User ID.** Everything in 2b assumes
it is DBS MAX.

### 2b. What DBS MAX offers (all from the [DBS MAX FAQ](https://www.dbs.com.sg/sme/dbs-max-oms/1ms-faq.pdf))

- **Three user types.**
  - Group Users can view everything and edit user rights.
  - Outlet Managers can "view his/her own outlet transactions".
  - Wallet Users "will only be able to see the transactions that they
    themselves have collected".
  - Because the treasurer sees other people's payments, they are probably an
    Outlet Manager on the club's outlet, or have something similar
    (UNVERIFIED).
- **Notifications.**
  - "User can set notification for collections, refunds, ad-hoc sweep &
    creation of QR."
  - For SGQR: "Only outlet managers can view SGQR transactions. To receive
    notification, merchant is required to change its role to an outlet
    manager."
  - So the "no alerts" in CLAUDE.md may only be a setting or a role. Worth
    checking in the app (More > Settings).
- **Export.**
  - "You will be able to download the transaction data, across all payment
    channels, in xls or csv format. Click on Transactions Tab > 'Get Adhoc
    report'". This is described for the MAX Merchant services portal, which is
    reached through IDEAL. Whether an Outlet Manager on the app alone can do it
    is UNVERIFIED.
  - Ad-hoc reports are sent "to the email address stated during user creation".
    A password "must be set before user starts receiving the reports".
  - Group Users automatically receive end-of-day payout reports by email.
- **90-day limit.**
  - "Transaction history of up to 90 days will be displayed in MAX app."
  - Reports go back "up to 90 days", with "a maximum of 30-day length per
    report".
  - **This matters now:** if a term runs longer than 90 days from the first
    payment, an end-of-term count check in FLYMAX cannot see the earliest
    payments.
- **References.**
  - On a MAX app QR, "the reference field will be auto-populated and
    non-editable".
  - On an SGQR, payers "are free to enter any reference detail".
  - Phase 0 found the club QR's tag 62 subfield 05 label (`BDM...`) does not
    show in FLYMAX. Only the fixed bill number (62-01) shows. The EMVCo spec
    defines 62-01 as "Bill Number" and 62-05 as "Reference Label", both up to
    25 characters ([EMVCo MPM v1.1](https://mvallim.github.io/emv-qrcode/docs/EMVCo-Merchant-Presented-QR-Specification-v1.1.pdf),
    mirror). **So no per-member reference can travel through the QR.** The
    bill number is fixed by the constraint and the label is dropped. The only
    per-member signals on the bank side are **payer name, amount and time**,
    plus the transaction ID if it matches the payer's receipt.
- **Is the MAX transaction ID the same as the reference on payer receipts?**
  - DBS documents its corporate statement line as "Inward PayNow \<Customer
    reference\> \<Payer bank transaction reference\> \<Purpose code\>
    \<Payer's name\>" ([DBS PayNow](https://www.dbs.com.sg/sme/paynow), cited
    in the 10-03 note). That suggests the payer bank's reference reaches the
    receiving side.
  - Whether MAX's "Transaction ID" equals what DBS, OCBC, UOB and the others
    print on their success screens is **UNVERIFIED**.
  - **Cheapest test:** compare the live S$0.05 receipt from 2026-06-20 (its
    extracted `transaction_id` is in `clubbot.db`) with the FLYMAX entry for
    that payment. Repeat with a second bank.

### 2c. DBS IDEAL (SUTD Finance side)

- **IDEAL eReports** (2013 form) offers "Incoming Funds Alert — To notify you
  when credit above specified threshold occurs to your account", daily, weekly
  or monthly eStatements, and "Remittance Advice". It costs "S$ 8.00 Per User
  /Month". "Each user is entitled to receive reports and alerts to 1 designated
  email"
  ([eReports form](https://www.dbs.com.sg/documents/276102/594912/SG_EN_IDeal_eReports_Editable.pdf/af9b3dd0-6a51-13cb-084c-2f789f80b3cb?t=1563533390158)).
  Current IDEAL pricing is UNVERIFIED.
- **Drawbacks of the alert:**
  - It is set per *account* with a threshold. S$20 credits need a very low
    threshold.
  - It would cover all of SUTD's credits to that account, not only the club's.
  - Finance forwarding that to a student is unlikely. It would only work if
    the club's collection has its own account or sub-account, which is
    UNVERIFIED.
- **Who can create alerts.** The IDEAL alert page says alerts go by "IDEAL
  mobile app alert or email" and "To have the same alert sent to other
  coworkers, add their email address", but doesn't say which role may set them
  up ([IDEAL alerts](https://www.dbs.com.sg/corporate/contact-us/create-and-manage-ideal-alerts)).
  A view-only "IDEAL Enquiry" user must be upgraded through the IDEAL
  Maintenance Form to reach Merchant Services ([DBS MAX FAQ](https://www.dbs.com.sg/sme/dbs-max-oms/1ms-faq.pdf)).
  **Whether a view-only user can receive alerts is UNVERIFIED.** Ask Finance.
- **MT940/camt.053 statement files** exist in corporate banking generally, but
  I found no primary DBS SG page describing IDEAL file statements for this
  account type (UNVERIFIED). In any case they are a Finance-side output.

**Realistic ask to SUTD Finance, if the treasurer's own MAX role can't
export:** a weekly CSV of the club outlet's MAX transactions, or a change of
the treasurer's role to one that can download. This is narrower and easier to
approve than per-credit alerts on the whole account.

---

## 3. PayNow payment gateways

The 10-03 note covered Stripe (1.3%, payers see "STRIPE PAYMENTS SINGAPORE")
and HitPay. New detail:

- **HitPay** online PayNow costs "0.9%" (min S$0.20) under S$100, so about
  S$0.20 to S$0.27 per payment ([HitPay pricing](https://hitpayapp.com/sg/pricing)).
  Sign-up needs a "Business registration number (e.g., ACRA UEN …)" and
  "Business bank account details". The payout "account name matches your
  registered business name"
  ([HitPay sign-up](https://hitpayapp.com/blog/hitpay-sign-up)).
  - The club has no UEN.
  - The treasurer's own account would put the money in a personal account.
  - Only SUTD could be the merchant.
- **Xendit SG** quotes PayNow at roughly 1% to 1.3% plus possibly S$0.30.
  Their pricing page returned 403, so this is UNVERIFIED.
- **2C2P, FOMO Pay, NETS**: all are merchant-of-record or acquirer models
  where funds settle to the merchant's own account. Specific fees and
  onboarding are UNVERIFIED (no public price pages found).
- **The structural blocker.**
  - In every gateway, the payer pays the gateway's PayNow proxy, not
    `200913519CSL5` with the Billing ID.
  - The school receives a lump payout as an ordinary transfer. Phase 0 showed
    that payments without the right bill number "never cleared into the club
    account", so a payout would need manual allocation by Finance.
  - That breaks the hard constraint. Treat it as a real blocker, not just a
    convention.
- **DBS MAX is effectively the "gateway with server confirmation" that already
  settles into the school's account.** The FAQ mentions "merchants with MAX
  API". But SUTD would be the API customer, so it is not available to the club
  (same conclusion as RAPID).

---

## 4. Detecting a fabricated screenshot technically

**Bottom line: nothing here is reliable against a motivated forger. Use it
only as a signal for manual review, never as a verdict.**

- **Document-forgery detectors fail on AI edits.** AIForge-Doc (Feb 2026)
  benchmarks diffusion inpainting on financial documents:
  - TruFor falls to AUC 0.751 (0.96 on NIST16).
  - DocTamper falls to 0.563 (0.98 in-distribution).
  - **GPT-4o scores 0.509, "essentially at chance"**
  ([arXiv 2602.20569](https://arxiv.org/abs/2602.20569)).
  - The older "Find it again!" receipt-forgery dataset (ICDAR 2023, 988
    receipts, 163 forged) uses *scanned paper* receipts, not phone screenshots
    ([project page](https://l3i-share.univ-lr.fr/2023Finditagain/index.html)).
- **AI-image detectors can be attacked.** Detectors are "highly vulnerable" to
  adversarial examples, even after social-media compression
  ([arXiv 2410.01574](https://arxiv.org/html/2410.01574v4)).
  - A fake made by editing a real screenshot with a normal editor isn't
    "AI-generated" at all.
- **SynthID.**
  - Google says the Gemini *app* can check uploaded images for its SynthID
    watermark ([Google, 20 Nov 2025](https://blog.google/innovation-and-ai/products/ai-image-verification-gemini-app/)).
  - The SynthID Detector portal is waitlisted, and I found no public
    developer API (UNVERIFIED as of today).
  - It only detects images made with Google tools. Open-weight generators and
    hand edits carry no watermark.
- **C2PA / Content Credentials.**
  - The C2PA explainer states "provenance information alone cannot tell you
    whether the digital content is true, accurate or factual", and that assets
    without credentials should not be "universally less trusted"
    ([C2PA explainer 2.4](https://spec.c2pa.org/specifications/specifications/2.4/explainer/Explainer.html)).
  - Pixel 10 signs "any JPEG photo capture" from Pixel Camera, and Google
    Photos signs AI edits. Screenshots are not mentioned
    ([Google, Sep 2025](https://blog.google/security/pixel-android-trusted-images-c2pa-content-credentials/)).
  - Nano Banana Pro images carry C2PA ([Google, Nov 2025](https://blog.google/innovation-and-ai/products/ai-image-verification-gemini-app/)).
    Removing it only takes a resave.
  - Bank apps: I found no evidence that any Singapore bank signs success
    screens or shared receipts (UNVERIFIED).
- **EXIF/metadata.**
  - A Telegram bot receives a photo as "available sizes of the photo"
    ([Bot API Message](https://core.telegram.org/bots/api#message)), meaning
    Telegram's re-encoded copies. Secondary sources say this strips metadata
    ([sammapix](https://www.sammapix.com/blog/does-telegram-strip-exif-metadata)).
  - "Send as file" keeps it, but screenshot metadata is trivially editable and
    proves nothing.
- **Bank-app "Share" receipts.**
  - DBS digibank has a Share/Download button on transaction details
    ([DBS](https://www.dbs.com.sg/personal/support/bank-deposit-accounts-account-transactions.html)).
    The format and any signature are undocumented (UNVERIFIED).
  - An unsigned PDF is no harder to fake than a PNG.
- **Screen recording** (open the bank app, go to history, tap the payment).
  - This raises the effort, and the hawker case suggests live, animated
    screens are harder to fake.
  - But AI video and fake banking apps (the "spoof app" pattern from India)
    exist, so it is not proof.
  - It costs every honest member effort. Use it only as a spot-check or
    escalation step (reliability UNVERIFIED).

---

## 5. Process-level mitigations

- **Reconcile against the bank export, line by line.** This is the only
  measure here that turns "looks paid" into "is paid". Match on the transaction
  reference if 2b's test passes. Otherwise match on (amount, payer name, time
  within a few minutes). A count check says *that* something is missing.
  Line-by-line matching says *who*.
- **Unique cents per member (amount fingerprinting).**
  - The QR locks tag 54 (amount), so the payer cannot round it.
  - FLYMAX already shows amount and payer name. A unique amount makes each
    line identify one member even when names differ (parent's account,
    nicknames).
  - It defeats receipt swapping between members, which the transaction ID
    can't.
  - It is compatible with the fixed Billing ID because nothing in the QR
    except the amount changes.
  - Costs:
    - Fees look odd.
    - 99 cent values per fee tier. Above that, widen the range (for example
      S$19.50 to S$20.49) or reuse cents across non-overlapping weeks.
    - The current "a payment is a shirt only if it matches the exact
      with-shirt fee" rule would need rethinking.
  - Whether members will accept non-round fees is a treasurer decision.
- **Provisional status.** "Auto-verified" becomes "pending bank match". Only
  the reconciled state counts as paid for anything irreversible. The bot
  already has a "verified" idea; this adds a second stage.
- **Random spot checks are weak at this scale.** With 2 fakes in 200, checking
  20 at random finds at least one with probability 1 − C(198,20)/C(200,20) ≈
  **19%**. Full reconciliation from a CSV is cheaper than that sampling.
- **Better count check, if no CSV is possible.**
  - Run it **within 90 days** of the first payment (MAX history limit), for
    example the week after the deadline, not at term end.
  - Compare counts per fee tier, not the total. Since amounts are locked, the
    sum adds little beyond counts per tier.
  - Watch for unrelated payments to the same Billing ID (for example someone
    paying with the old static QR). They inflate the FLYMAX count and can hide
    a fake.
- **Deterrence.**
  - Tell members, in the pay prompt and the consent line, that every payment
    is matched against the school's bank record and mismatches lead to
    membership revocation and a report to SUTD.
  - Doctored PayNow screenshots have been prosecuted as cheating in Singapore
    (section 1).
  - Deterrence does not stop a determined faker. But once reconciliation
    exists it is true, and true deterrence is free.

---

## 6. What each viable option needs, and what risk remains

1. **Weekly MAX CSV reconciliation.**
   - Treasurer: confirm FLYMAX = DBS MAX; find "Get Adhoc report" or the
     equivalent; set the email-report password; forward the CSV to the bot
     weekly.
   - Possibly SUTD Finance, only if the treasurer's role can't export (upgrade
     the role or send the CSV).
   - Residual risk: a fake gets a few days of membership before it is caught.
     Swapped receipts still pass unless unique cents are used. Depends on the
     column format (UNVERIFIED until a sample CSV is seen).
2. **Unique cents.** Treasurer decision only.
   - Residual risk: member confusion; a little more admin when someone pays
     the wrong QR.
3. **Provisional status.** Treasurer accepts a lag before "paid" is final.
   - Residual risk: the same as whatever confirms it.
4. **MAX push notifications.** The treasurer checks settings, or SUTD's MAX
   Group User makes them an Outlet Manager.
   - Residual risk: it is a human glance, not automation. Still useful as a
     same-day sanity check during the pay rush.
5. **IDEAL Incoming Funds Alert.** SUTD Finance, per the 10-03 note.
   - Residual risk: account-wide scope makes approval unlikely; fields
     unknown.

---

## Open questions for the treasurer

1. ~~Is the FLYMAX app actually **DBS MAX**?~~ **Confirmed 2026-10-16** —
   see section 7 below.
2. On your S$0.05 test payment, does FLYMAX's **transaction ID equal** the
   reference on the payer's receipt? Is it the same for a second bank?
   Still open — see section 7 (the digit counts don't match, but that's not
   a same-transaction comparison).
3. Does FLYMAX history go back further than 90 days?
4. Would members accept **non-round fees** (for example S$20.37) in exchange
   for automatic bank matching?
5. If your role can't export: will SUTD Finance either upgrade it or email you
   a weekly CSV of the club outlet's transactions?

---

## 7. Confirmed FLYMAX screenshot format (2026-10-16)

The treasurer sent two real screenshots (kept locally only, in
`docs/research/flymax-samples/`, gitignored — they show real member names and
test payment amounts). They confirm:

- **FLYMAX is DBS MAX**, header literally reads "DBS MAX". Bottom nav: My QR,
  History, Overview, More.
- **Overview tab → "Earning insights"** shows a bar chart (Amount/Volume
  toggle) and weekly totals ("Total earnings this week", "Average daily
  earnings/transactions"). Not the row-by-row list `/reconcile` needs.
- **History tab is the one that matters.** Rows are grouped under a date
  header (e.g. "Tue, 13 Oct 2026"), each row:
  - PayNow icon
  - Payer name, sometimes with an alternate name in parentheses, e.g.
    `CLARICE LIM( LIN YINGXIN )`
  - Amount, right-aligned (`SGD 0.02`)
  - `TXN ID: <digits>` — a 20-digit numeric ID (e.g.
    `26101312242399990572`, `26101118373588771833`,
    `26091723451299650485`). The leading 4 digits look like YYMM
    (`2610` = Oct 2026, `2609` = Sep 2026).
  - `<collector username> collected at <h:mm am/pm>`, e.g.
    "RAGHAVRAJESH collected at 12:24 pm"
  - **No Billing ID is shown per row.** The PRD (§4) assumption that FLYMAX
    shows the Billing ID per line does not hold — only payer name, amount,
    date, time and TXN ID are visible. `clubbot/payments.py`'s
    `BankLine` already only uses amount/time/name/transaction_id, so no code
    change is needed; this just corrects the design doc's assumption.
  - The search bar says "Search by name, TXN ID or amount" — TXN ID is a
    first-class searchable field, consistent with it being FLYMAX's own
    reference.
- **The real Clarice Lim test payment (section "Trigger" history) shows up
  fine in FLYMAX**, S$0.02 on 13 Oct with TXN ID `26101312242399990572`,
  even though her own bank receipt had no visible transaction ID (the
  missing-txn-ID case from the 2026-10-14 CLAUDE.md status entry). This is
  exactly the case `/reconcile`'s amount + time + name-word matching exists
  for, and it works: the payment is findable in FLYMAX by amount/time/name
  even with no ID on her side.
- **Still open:** whether FLYMAX's 20-digit TXN ID equals the reference a
  payer's own bank app shows. The CLAUDE.md status entry for 2026-10-11
  records UOB payer-receipt transaction IDs as 16 digits
  (`2610112184655201`, `2610112184752256`) — a different length than
  FLYMAX's 20-digit TXN IDs here. That's a signal the two may be different ID
  systems, not a confirmation (they weren't the same transaction). The
  cheapest real test is still: compare one specific payment's FLYMAX TXN ID
  against that same payer's own receipt transaction ID.

---

## Sources

- SPF, man charged for edited PayNow screenshot (2 Nov 2025): https://www.police.gov.sg/media-hub/news/2025/11/20251102_man_to_be_charged_for_cheating
- SPF, money changer case (27 Jun 2022): https://www.police.gov.sg/media-room/news/20220627_man_arr_for_cheating
- Mothership, hawker fake PayNow receipt (Jan 2025): https://mothership.sg/2025/01/paynow-scam-hawker-east-coast/
- Carousell common scam types: https://support.carousell.com/hc/en-us/articles/49107638285849-Common-Scam-Types-on-Carousell
- Paytm Soundbox payment verification: https://paytm.com/support/paytm-soundbox/how-merchants-can-verify-payment-receipts
- Daily Dot, ChatGPT fake receipts (secondary): https://dailydot.com/chatgpt-faking-receipts
- Slashdot summary of FT on AI expense receipts (secondary): https://slashdot.org/story/25/10/27/1829258/companies-battle-wave-of-ai-generated-fake-expense-receipts
- DBS MAX FAQ (PDF): https://www.dbs.com.sg/sme/dbs-max-oms/1ms-faq.pdf
- DBS MAX (SME): https://www.dbs.com.sg/sme/max
- DBS MAX portal: https://www.dbs.com.sg/corporate/solutions/cash-management/max
- DBS IDEAL alerts: https://www.dbs.com.sg/corporate/contact-us/create-and-manage-ideal-alerts
- DBS IDEAL eReports form (2013): https://www.dbs.com.sg/documents/276102/594912/SG_EN_IDeal_eReports_Editable.pdf/af9b3dd0-6a51-13cb-084c-2f789f80b3cb?t=1563533390158
- DBS PayNow Corporate (SME): https://www.dbs.com.sg/sme/paynow
- DBS digibank transaction details / share: https://www.dbs.com.sg/personal/support/bank-deposit-accounts-account-transactions.html
- EMVCo MPM QR spec v1.1 (mirror): https://mvallim.github.io/emv-qrcode/docs/EMVCo-Merchant-Presented-QR-Specification-v1.1.pdf
- HitPay pricing: https://hitpayapp.com/sg/pricing
- HitPay sign-up requirements: https://hitpayapp.com/blog/hitpay-sign-up
- Xendit SG pricing (403 when fetched): https://www.xendit.co/en-sg/pricing/
- AIForge-Doc benchmark: https://arxiv.org/abs/2602.20569
- Find it again! receipt forgery dataset: https://l3i-share.univ-lr.fr/2023Finditagain/index.html
- Adversarial robustness of AI-image detectors: https://arxiv.org/html/2410.01574v4
- Google, AI image verification in Gemini (SynthID, C2PA): https://blog.google/innovation-and-ai/products/ai-image-verification-gemini-app/
- Google, Pixel/Android C2PA: https://blog.google/security/pixel-android-trusted-images-c2pa-content-credentials/
- C2PA explainer 2.4: https://spec.c2pa.org/specifications/specifications/2.4/explainer/Explainer.html
- Telegram Bot API (Message.photo): https://core.telegram.org/bots/api#message
- Telegram EXIF stripping (secondary): https://www.sammapix.com/blog/does-telegram-strip-exif-metadata

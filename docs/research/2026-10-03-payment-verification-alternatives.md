# Payment verification: alternatives to the Gemini API

Research date: 2026-10-03. Scope: how ShuttleBuddy could verify PayNow payments
without (or with less) reliance on the Gemini API. No code was changed.

Context in this repo: `clubbot/gemini.py` is already a swappable adapter
(`extract(image_bytes, mime_type) -> ExtractedPayment`), and every accept/reject
decision is made in `payments.verify_extracted_payment`. Any alternative below
only has to fill the same 7 fields; the rule engine does not change.

---

## TL;DR

- **Best near-term option: stay with a cloud vision model, but use a paid tier.**
  At 50–200 receipts a term the bill is cents to about US$1 per term (table
  below). Paid tiers from Google, Anthropic and OpenAI do not train on your
  data. Google's own terms say the free tier may be used for training and read
  by human reviewers, and tell you not to send personal information to it
  ([Gemini API terms](https://ai.google.dev/gemini-api/terms)). Receipts
  contain payer names, so the free tier is the wrong place for them.
- **Check this first: your new Gemini key may not reach `gemini-2.5-flash`.**
  Google is "limiting access to the 2.5 models to users who have actively used
  them in the past" and points new projects to 3.x models
  ([deprecations](https://ai.google.dev/gemini-api/docs/deprecations)). That
  could explain the Gemini FAIL in preflight. The likely fix is to change the
  model ID to `gemini-3.1-flash-lite` or `gemini-3.5-flash-lite`.
- **Second provider for redundancy: Claude Haiku 4.5**, about US$0.0025 per
  receipt, and not trained on your data
  ([pricing](https://platform.claude.com/docs/en/about-claude/pricing),
  [commercial terms](https://www.anthropic.com/legal/commercial-terms)). It
  would be a second ~60-line adapter.
- **Local OCR on the Pi works but isn't worth it at this volume.** RapidOCR
  (ONNX, Apache-2.0, aarch64 wheels) is the right tool if you want no cloud
  dependency at all. The real cost is writing and maintaining a text parser for
  each bank's screen layout, and that buys a saving of under US$1 per term. If
  you do it, send failures to the treasurer's existing review buttons, not to a
  cloud model.
- **A local vision model on a Pi 4 is not practical.** Published Pi 5 numbers
  are roughly 9–19 s just to encode one 512 px image, and the Pi 4 is 2–3x
  slower. Shrinking a tall phone screenshot to 512 px also makes the small text
  unreadable.
- **No OCR or vision model can detect a forged screenshot.** Error-level
  analysis does not work on PNG screenshots. The only real defence is
  confirmation from the bank side.
- **The strategic fix is to ask SUTD Finance for a DBS IDEAL "Incoming Funds
  Alert"** sent by email to a club-controlled inbox. The bot could then match
  each receipt's bank transaction reference against an email from the bank
  ([IDEAL alerts](https://www.dbs.com.sg/corporate/contact-us/create-and-manage-ideal-alerts)).
  DBS statements list each payer's bank transaction reference and name
  ([DBS PayNow Corporate](https://www.dbs.com.sg/sme/paynow)).
- **Ruled out:** payment aggregators (HitPay, Stripe), DBS RAPID APIs and
  Telegram Payments. In each case the money lands with someone other than the
  school, or the school would have to become the API customer.

---

## Comparison

Per-receipt costs are **estimates** for a 1080x2400 PNG screenshot plus a
~250-token prompt and ~150 output tokens, worked out from each provider's
published price and image-token rules (see section 4).

| Approach | Accuracy on receipts | Pi 4 feasibility | Cost / term (200 receipts) | Privacy | Fraud resistance | Effort |
|---|---|---|---|---|---|---|
| Gemini free tier (today) | High (live-proven) | n/a (cloud) | $0 | **Poor**: may be used for training and read by humans | None beyond existing dedup checks | 0 |
| Cloud VLM, paid tier (Gemini Flash-Lite / Haiku 4.5 / gpt-4.1-mini) | High | n/a (cloud) | ~US$0.06–0.50 | Good: no training on paid API data | Same as today | Very low (model ID + billing; or new adapter for Haiku) |
| RapidOCR + anchored regex | Good on clean screenshots; parser fails on unseen layouts | Yes: aarch64 onnxruntime wheels; latency UNVERIFIED (est. a few seconds) | $0 | Best (on-device) | Same as today | Medium–high: parser per bank, corpus needed |
| Tesseract + regex | Fair; dark mode needs inverting | Yes: Debian arm64 package; ~3 s for an 800 px invoice on a Pi 4 | $0 | Best | Same | Medium–high |
| EasyOCR / docTR | Good | Heavy (PyTorch); EasyOCR last release Sep 2024 | $0 | Best | Same | High |
| Local VLM (Moondream / SmolVLM / Qwen2.5-VL-3B) | Low–medium at Pi-feasible resolution | Marginal: tens of seconds or more per image; RAM-tight on 4 GB | $0 | Best | Same | High |
| Hybrid: local OCR, then cloud VLM on failure | High | Yes | <US$0.50 | Mostly local | Same | High (two paths to maintain) |
| Hybrid: local OCR, then treasurer review | Good, with more manual taps | Yes | $0 | Best | Same | Medium–high |
| IDEAL Incoming Funds Alert email, parsed | **Bank-confirmed** | Yes (email polling) | $0 | Bank data flows to the club inbox | **Strong**: forged screenshot has no matching credit | Medium, but needs SUTD Finance |
| DBS RAPID Inward Credit Notification API | Bank-confirmed | Would need a public HTTPS endpoint | Unknown | n/a | Strong | Not achievable: SUTD must be the API customer |
| Aggregator (HitPay / Stripe PayNow) | Bank-confirmed (webhook) | Yes | ~S$40–52 in fees | Aggregator holds data | Strong | Not allowed: money must land in the school account |

---

## 1. Local OCR on a Raspberry Pi 4

Phone screenshots are an easy case for OCR: rendered fonts, no skew, no
lighting problems. The hard part is what comes after the OCR (section 2).

| Engine | ARM64 install | RAM | Pi 4 latency | Licence / maintenance |
|---|---|---|---|---|
| **RapidOCR** (PP-OCR models converted to ONNX) | `pip install rapidocr onnxruntime`. rapidocr is a pure-Python wheel ([PyPI](https://pypi.org/project/rapidocr/)) and onnxruntime 1.30.0 ships `manylinux_2_28_aarch64` wheels for cp312/cp313 ([PyPI](https://pypi.org/project/onnxruntime/#files)). The docs list Raspberry Pi as a deployment target ([docs](https://rapidai.github.io/RapidOCRDocs/main/)). | UNVERIFIED; the models are small (PP-OCRv4 mobile detector is 4.7 MB per [codesota](https://www.codesota.com/ocr/paddleocr-vs-tesseract), secondary source) | **UNVERIFIED**: no published Pi 4 benchmark found. A guess of a few seconds per screenshot needs measuring on the Pi. | Apache-2.0; v3.9.2 released 2026-07-21 ([GitHub](https://github.com/RapidAI/RapidOCR), [PyPI](https://pypi.org/project/rapidocr/)) |
| **PaddleOCR** (native) | paddlepaddle 3.3.1 has **no Linux aarch64 wheel** on PyPI ([PyPI](https://pypi.org/project/paddlepaddle/#files)). Use RapidOCR to run the same models on the Pi. | n/a | n/a | Apache-2.0; v3.7.0 released 2026-06-11 ([GitHub](https://github.com/PaddlePaddle/PaddleOCR)). PP-OCRv6 "tiny" is reported 3.9x faster than v5 mobile on Xeon ([arXiv 2606.13108](https://arxiv.org/abs/2606.13108)). |
| **Tesseract** + pytesseract | `apt install tesseract-ocr`; 5.3.0 is packaged for arm64 in Debian bookworm ([Debian](https://packages.debian.org/bookworm/tesseract-ocr)) | Low | One measured Pi 4 (4 GB) data point: ~3.2 s of OCR on an invoice resized to 800 px wide, `--oem 1 --psm 6`, down from 20 s at full resolution ([derkuba.de](https://derkuba.de/posts/en/0625/tesseract-on-raspberry/), blog). A sparse screenshot is plausibly similar or faster (UNVERIFIED). | Apache-2.0; 5.x stable ([GitHub](https://github.com/tesseract-ocr/tesseract)). Needs dark text on a light background, so invert dark-mode screenshots ([tessdoc](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)). Use `--psm 11` for sparse text (same source). |
| **EasyOCR** | Needs PyTorch ("All deep learning execution is based on Pytorch"); runs on CPU with `gpu=False` ([GitHub](https://github.com/JaidedAI/EasyOCR)) | High (PyTorch) | No primary Pi benchmark; secondary claims of 20–30 s per image are UNVERIFIED | Apache-2.0; last release 1.7.2 on 2024-09-24, so effectively stale |
| **docTR / OnnxTR** | docTR needs PyTorch/TF. OnnxTR is an ONNX wrapper with no torch dependency ([GitHub](https://github.com/felixdittrich92/OnnxTR)) | Medium | UNVERIFIED on Pi. On an i7-14700K, OnnxTR does ~0.25–0.57 s/page vs docTR 0.60–1.29 s (same source). | Apache-2.0; docTR 1.1.0, maintained by t2k GmbH ([GitHub](https://github.com/mindee/doctr)) |

**Pick:** RapidOCR if you go local. It runs the strongest open model family
(PP-OCR), has the lightest dependency on aarch64, and is actively released.
Before writing any parser, benchmark it on the Pi with 20 real receipts.

---

## 2. Parsing the OCR text

### What the screens show

**UNVERIFIED.** Bank help pages describe the PayNow flow but not the fields on
the final success screen. DBS
([guide](https://www.dbs.com.sg/personal/support/bank-local-funds-transfer-paynow-transfer.html))
and OCBC ([guide](https://www.ocbc.com/personal-banking/digital-banking/step-by-step-guides/payment-transfer/transfer-funds-to-uen))
only say "you're done". I could not find a primary source for the layout of
DBS/POSB, PayLah!, OCBC, UOB TMRW, Trust, GXS, MariBank, Citi, HSBC or
Standard Chartered success screens.

**The best evidence you already have is your own database.** The bot stores
Gemini's extracted fields for every receipt. Querying it shows which fields
each bank's screen actually produced (null `transaction_id`, null
`billing_id`, minute-only timestamps). Do that before designing any parser.

### Regex with keyword anchors vs layout-aware parsing

- **Anchored regex** finds a label (`Amount`, `To`, `Reference`, `Bill`,
  `Transaction`), then takes the nearest value to the right of it or on the next
  line. RapidOCR returns a bounding box for each text line, so "nearest box to
  the right or below" takes about 20 lines of code. That is enough layout
  awareness here; no layout model is needed.
- **Some fields are easy whatever the layout.** The Billing ID is a fixed
  string, so a fuzzy exact-match search across all the text works with no
  anchor. The amount must equal the term fee, so search for that one number
  in its `S$`/`SGD` variants.
- **Hard fields:** the success indicator (the wording differs per bank), the
  timestamp (formats differ), and the transaction reference (the label and
  format differ per bank).

### Realistic failure modes

- **Timestamps without seconds.** The code already handles these by comparing
  against the start of the QR-issue minute (`payments.py` lines 141–145). OCR
  also has to cope with `20 Jun 2026, 10:38 AM`, `20/06/2026 10:38`, 24-hour
  vs AM/PM, and a missing year.
- **Amount formats:** `S$20.00`, `SGD 20.00`, `$20`, and `20.00` sitting next
  to an `SGD` box that OCR reads as a separate line. OCR may also confuse `O`
  and `0`, or `S` and `5`, in references.
- **Truncated recipient names:** the merchant name is already truncated
  ("SINGAPORE UNIVERSITY OF T"), and the existing check uses a substring match,
  which is right.
- **Dark mode:** Tesseract 4+ needs the image inverted. Detect this by mean
  brightness.
- **Cropped screenshots** missing the Billing ID or time, and **share-receipt
  images** that look different from the on-screen version.
- **Unseen bank layouts** fail silently as nulls. That is safe, because null
  fields route to retry or review, but it adds manual taps.

---

## 3. Small local vision models on the Pi 4

**Supported:** llama.cpp's multimodal tools support Qwen2-VL 2B, Qwen2.5-VL
3B, SmolVLM/SmolVLM2, Gemma 3 4B and Moondream2
([llama.cpp multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)).

**Measured on a Pi 5 (Moondream).** The 2B model takes 18.6 s to encode the
image and the 0.5B model 8.75 s, for an image at or under 512x512. Larger
images take 30–45 s. One setup used "nearly 7GB of RAM" (desktop running)
([Core Electronics](https://core-electronics.com.au/guides/getting-started-with-moondream-on-the-pi-5-human-like-computer-vision/)).

**Pi 4 is slower.** The Pi 5 has "between two and three times the CPU and
GPU performance" of the Pi 4
([raspberrypi.com](https://www.raspberrypi.com/news/introducing-raspberry-pi-5/)).
Expect roughly 20–60 s or more per receipt on a Pi 4 (extrapolation,
UNVERIFIED).

**Model size and quality:**
- Qwen2.5-VL-3B is a 3.2 GB download in Ollama
  ([ollama.com](https://ollama.com/library/qwen2.5vl)). On a 4 GB Pi 4 that
  also runs the bot, it will be swap-bound.
- SmolVLM-256M fits in under 1 GB but scores 52.6 on OCRBench, and its authors
  say it "is not intended for high-stakes scenarios"
  ([model card](https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct)).

**Verdict:** not feasible for receipts. The resolution you'd need to read a
tall screenshot reliably costs minutes on a Pi 4. At Pi-friendly resolution
the models misread digits, and digits are exactly the fields you check.

---

## 4. Hybrid, and the cost and privacy of cloud fallbacks

### Prices (official pages, USD per 1M tokens, paid tier)

| Model | Input | Output | Source |
|---|---|---|---|
| gemini-2.5-flash | 0.30 | 2.50 | [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| gemini-2.5-flash-lite | 0.10 | 0.40 | same |
| gemini-3.1-flash-lite | 0.25 | 1.50 | same |
| gemini-3.5-flash-lite | 0.30 | 2.50 | same |
| gemini-3.8-flash | 0.75 (1.50 from 2027-01-01) | 3.75 (7.50 from 2027) | same |
| Claude Haiku 4.5 | 1.00 | 5.00 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| gpt-4.1-mini / gpt-4.1-nano | 0.40 / 0.10 | 1.60 / 0.40 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| gpt-4o-mini | 0.15 | 0.60 | same |

### Image tokens and per-receipt estimates (1080x2400 screenshot)

- **Gemini:** 768x768 tiles at 258 tokens each, so about 8 tiles, roughly
  2,000 tokens ([image docs](https://ai.google.dev/gemini-api/docs/image-understanding)).
  - gemini-2.5-flash: about **US$0.001** per receipt, *plus thinking*. Thinking
    is on by default for 2.5 Flash and is billed as output
    ([thinking docs](https://ai.google.dev/gemini-api/docs/thinking)).
    `gemini.py` does not set a thinking option, so each 1,000 thinking tokens
    adds about US$0.0025.
  - gemini-3.1-flash-lite: about **US$0.0008** per receipt.
- **Claude:** cost is `ceil(w/28) x ceil(h/28)` tokens. Haiku 4.5 uses the
  standard tier with a 1568 px long edge, so the image is resized to about
  706x1568, roughly 1,456 tokens
  ([vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)).
  That is about **US$0.0025** per receipt.
- **OpenAI gpt-4o-mini:** high detail costs 2,833 + 5,667 per 512 px tile
  ([vision docs](https://developers.openai.com/api/docs/guides/images-vision)),
  8 tiles, about 48k tokens, roughly **US$0.007**. gpt-4.1-mini uses
  32 px patches x 1.62 (same source), about 2.5k tokens, roughly
  **US$0.0013**.

**What this means:** even sending every receipt to the dearest of these is
about US$1.50 per term. A hybrid design saves less than a dollar per term. Its
only real benefit is privacy (fewer images leave the Pi) and resilience when
the cloud is down. Neither needs the extra complexity, because the existing
retry and treasurer-review path already covers an outage.

### Privacy terms

| Provider | Free tier | Paid tier |
|---|---|---|
| Google Gemini API | Content is used "to provide, improve, and develop Google products"; "human reviewers may read, annotate, and process your API input and output"; "Do not submit sensitive, confidential, or personal information to the Unpaid Services" ([terms](https://ai.google.dev/gemini-api/terms)) | "Google doesn't use your prompts ... or responses to improve our products"; logged only for abuse detection and legal reasons (same source) |
| Anthropic | No ongoing free tier; new accounts get small trial credits ([pricing FAQ](https://platform.claude.com/docs/en/about-claude/pricing)) | "Anthropic may not train models on Customer Content from Services" ([commercial terms](https://www.anthropic.com/legal/commercial-terms)) |
| OpenAI | n/a | API data "is not used to train or improve OpenAI models (unless you explicitly opt in)"; abuse logs kept up to 30 days ([your data](https://developers.openai.com/api/docs/guides/your-data)) |

Free-tier Gemini rate limits are shown only inside AI Studio
([rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)), so this
research could not check them (UNVERIFIED).

---

## 5. Fraud resistance

None of these approaches can tell a real screenshot from an edited one. OCR and
vision models both report what the pixels say. Anthropic states that Claude
"cannot determine whether an image is AI-generated" and should not be relied
on to detect fake images
([vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)).

**Error-level analysis (ELA) is not usable here.** It "is explicitly designed
for use with JPEG", fails on resaved images, and misses single-pixel edits
([FotoForensics](https://fotoforensics.com/tutorial-ela.php)). Phone
screenshots are PNGs, and Telegram's photo mode re-encodes them (UNVERIFIED
here). So ELA is not a realistic defence.

### What already works, regardless of extractor

These checks are in `payments.py` and `db.py`:
- The exact Billing ID must match.
- Payment time must fall after this member's QR was issued.
- The image SHA-256 must be globally unique.
- The normalised bank reference must be globally unique.

Together these stop simple replays: reusing your own or someone else's old
receipt, or the same image twice. They do not stop an edited copy of a real
receipt with a changed reference, time or amount.

### Cheap improvements, in order of value

1. **Put the extracted bank transaction reference into the weekly digest.**
   DBS shows incoming PayNow as "Inward PayNow \<Customer reference\> \<Payer
   bank transaction reference\> \<Purpose code\> \<Payer's name\> S$ \<Amount\>"
   ([DBS PayNow Corporate](https://www.dbs.com.sg/sme/paynow)). If FLYMAX shows
   the same thing (UNVERIFIED), the treasurer can match references exactly, not
   just by name and amount. A forged reference would then fail the audit.
2. Pick bank-alert confirmation (section 6) over any detection heuristic.
3. Per-bank reference format checks (length, prefix) catch only careless
   forgers. Low value. The formats are UNVERIFIED and would need your own data.

---

## 6. Routes that give bank-confirmed verification

**DBS IDEAL Incoming Funds Alert (most promising).**
- IDEAL can send an "Incoming Funds Alert", which "notifies upon fund credit
  to account", by email. It lets you "add coworkers' email addresses"
  ([IDEAL alerts](https://www.dbs.com.sg/corporate/contact-us/create-and-manage-ideal-alerts)).
- DBS also says eAlert subscribers "receive an email confirmation for incoming
  PayNow transactions" ([DBS PayNow](https://www.dbs.com.sg/sme/paynow)).
- If SUTD Finance points that alert, filtered to the club's collection, at a
  club Gmail, the bot can poll it with IMAP or the Gmail API. Gmail push needs
  Pub/Sub and a `watch` renewal at least every 7 days; polling `history.list`
  is the documented fallback
  ([Gmail push](https://developers.google.com/workspace/gmail/api/guides/push)).
- A consumer Gmail needs OAuth user credentials. The existing service account
  cannot read it (UNVERIFIED here).
- **Unknowns:** what fields the alert email contains, whether it can be
  limited to the club's Billing ID or sub-account rather than all SUTD
  credits, and whether Finance will allow it. Ask for a sample alert.

**DBS RAPID API.** RAPID offers real-time collections and payment-status APIs.
DBS also documents an "Inward Credit Notification" service (cited via search
summary; the RAPID page itself lists collections and status tracking). Signup
goes through a Relationship Manager, takes "four to six weeks", and uses REST
with PGP ([DBS RAPID](https://www.dbs.com.sg/corporate/solutions/rapid)). SUTD
would have to be the integrating customer. Not achievable for a club.

**DBS MAX / "FLYMAX".** I found no DBS product called "FLYMAX" (UNVERIFIED).
DBS MAX is a merchant collections portal with transaction export and user
roles ([DBS MAX](https://www.dbs.com.sg/corporate/solutions/cash-management/max)).
If the school account is a MAX-style portal, a view-only role might still be
able to export. Ask.

**PayNow QR and SGQR features.** A PayNow QR can carry a reference and an
amount ([DBS PayNow](https://www.dbs.com.sg/sme/paynow)). Phase 0 showed the
`BDM...` label does not appear in FLYMAX or on receipts, and the QR alone gives
no confirmation to the receiving side. The statement format above includes a
"Customer reference" field, so the full IDEAL statement might show `BDM...`
even though the FLYMAX app does not (UNVERIFIED). That is worth one question
to Finance, because it would allow exact per-member matching.

**PayNow Corporate for the club itself.** Requires a Singapore UEN
([DBS PayNow](https://www.dbs.com.sg/sme/paynow)). The club is not an ACRA
entity, so not possible.

**Aggregators: fail the "money lands in the school account" constraint.**
- Stripe PayNow: payers see "STRIPE PAYMENTS SINGAPORE PTE. LTD.", funds go
  to the Stripe balance and pay out T+1, at a 1.3% fee
  ([Stripe](https://docs.stripe.com/payments/paynow)).
- HitPay: online PayNow costs 0.9% with a S$0.20 minimum under S$100
  ([HitPay](https://www.hitpayapp.com/pricing)). Account eligibility is
  UNVERIFIED.
- Either would need SUTD to own the merchant account. Fees would be about
  S$0.20–0.26 per S$20 payment.

**Telegram Payments: not viable.** Telegram "does not process payments"
itself; it hands off to third-party providers configured through BotFather
([Telegram](https://core.telegram.org/bots/payments)). Money goes to that
provider's merchant account, not the school's, and no PayNow provider is
listed.

---

## Open questions for the treasurer

1. **Gemini key:** does the new project's key work with `gemini-2.5-flash`, or
   only with 3.x models? Will you enable billing (paid tier) so receipts are
   not used for training?
2. **Second provider:** do you want a Claude Haiku 4.5 adapter as a backup
   (about US$0.50 per term at full volume)?
3. **SUTD Finance, alerts:** can they set up an IDEAL "Incoming Funds Alert"
   for the club's collection to a club email? What does a sample alert contain
   (payer name, amount, bank reference, customer reference)?
4. **SUTD Finance, statements:** does the IDEAL statement (not the FLYMAX app)
   show the `BDM...` customer reference or the payer's bank transaction
   reference?
5. **FLYMAX:** what exactly is it (a DBS MAX portal? an IDEAL view-only role?),
   and does it have any export or download?
6. **Local OCR:** is "no receipt images leave the Pi" a requirement for you? If
   not, local OCR is not worth building.
7. **Test corpus:** may the bot re-fetch past receipts by Telegram `file_id`
   for a one-off accuracy test, held in memory only and with members told?
   CLAUDE.md forbids storing screenshots on disk.

---

## Sources

- Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing
- Gemini API terms: https://ai.google.dev/gemini-api/terms
- Gemini deprecations: https://ai.google.dev/gemini-api/docs/deprecations
- Gemini image understanding: https://ai.google.dev/gemini-api/docs/image-understanding
- Gemini thinking: https://ai.google.dev/gemini-api/docs/thinking
- Gemini rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Anthropic vision: https://platform.claude.com/docs/en/build-with-claude/vision
- Anthropic commercial terms: https://www.anthropic.com/legal/commercial-terms
- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- OpenAI images and vision: https://developers.openai.com/api/docs/guides/images-vision
- OpenAI data controls: https://developers.openai.com/api/docs/guides/your-data
- RapidOCR: https://github.com/RapidAI/RapidOCR, https://pypi.org/project/rapidocr/, https://rapidai.github.io/RapidOCRDocs/main/
- onnxruntime wheels: https://pypi.org/project/onnxruntime/#files
- PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR; paddlepaddle wheels: https://pypi.org/project/paddlepaddle/#files; PP-OCRv6: https://arxiv.org/abs/2606.13108
- Tesseract: https://github.com/tesseract-ocr/tesseract; Debian package: https://packages.debian.org/bookworm/tesseract-ocr; quality guide: https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html
- Tesseract on Pi 4 (blog): https://derkuba.de/posts/en/0625/tesseract-on-raspberry/
- EasyOCR: https://github.com/JaidedAI/EasyOCR
- docTR: https://github.com/mindee/doctr; OnnxTR: https://github.com/felixdittrich92/OnnxTR
- PaddleOCR vs Tesseract (secondary): https://www.codesota.com/ocr/paddleocr-vs-tesseract
- llama.cpp multimodal: https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
- Moondream on Pi 5: https://core-electronics.com.au/guides/getting-started-with-moondream-on-the-pi-5-human-like-computer-vision/
- Raspberry Pi 5 announcement: https://www.raspberrypi.com/news/introducing-raspberry-pi-5/
- Ollama qwen2.5vl: https://ollama.com/library/qwen2.5vl
- SmolVLM-256M: https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct
- FotoForensics ELA: https://fotoforensics.com/tutorial-ela.php
- DBS PayNow transfer guide: https://www.dbs.com.sg/personal/support/bank-local-funds-transfer-paynow-transfer.html
- OCBC UEN transfer guide: https://www.ocbc.com/personal-banking/digital-banking/step-by-step-guides/payment-transfer/transfer-funds-to-uen
- DBS PayNow Corporate (SME): https://www.dbs.com.sg/sme/paynow
- DBS IDEAL alerts: https://www.dbs.com.sg/corporate/contact-us/create-and-manage-ideal-alerts, https://www.dbs.com.sg/sme/day-to-day/ways-to-bank/alerts
- DBS RAPID: https://www.dbs.com.sg/corporate/solutions/rapid
- DBS MAX: https://www.dbs.com.sg/corporate/solutions/cash-management/max
- Gmail push notifications: https://developers.google.com/workspace/gmail/api/guides/push
- ABS PayNow: https://www.abs.org.sg/consumer-banking/pay-now
- Stripe PayNow: https://docs.stripe.com/payments/paynow
- HitPay pricing: https://www.hitpayapp.com/pricing
- Telegram bot payments: https://core.telegram.org/bots/payments

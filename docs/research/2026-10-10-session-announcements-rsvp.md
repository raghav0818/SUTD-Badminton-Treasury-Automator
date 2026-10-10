# Recre session sign-ups (Mitup replacement) + random "in charge" picks

Research date: 2026-10-10. Scope: replacing the vice captain's use of the
third-party bot **Mitup** (@mitupbot) for recreational session sign-ups with our
own bot, plus a new feature: the bot randomly picks two participants to be "in
charge" of nets and shuttlecocks (primary, and secondary as backup), DMs both and
@-mentions them in the group. No code was changed.

Sources: Mitup's own user guide ([mitup.social](https://mitup.social/)) and its
MIT-licensed source ([GitHub mirror](https://github.com/AAraKKe/mitup-telegram-bot),
canonical [GitLab](https://gitlab.com/meetupbot/mitup-telegram-bot)); the Telegram
Bot API (current version **Bot API 10.3**, 2026-08-24,
[changelog](https://core.telegram.org/bots/api-changelog)), the
[Bots FAQ](https://core.telegram.org/bots/faq) and
[Bots intro](https://core.telegram.org/bots); the python-telegram-bot (PTB) docs
and source; the Python docs. Anything marked **UNVERIFIED** was not confirmed in
a primary source.

Repo facts used below: `requirements.txt` pins
`python-telegram-bot[job-queue]>=22.8,<23`. PTB 22.8 (2026-06-12) has "full
support for Bot API 9.6 and Bot API 10.0"
([PTB changelog](https://docs.python-telegram-bot.org/en/stable/changelog.html)),
so features added in Bot API 10.2/10.3 have no PTB wrappers yet. The bot is already
an **admin** in both club groups, and `/setgroup rec` stores `rec_group_id`
(`clubbot/db.py`, `GROUP_KEYS`).

---

## TL;DR

| # | Recommendation | Why |
|---|---|---|
| 1 | **Post one session card directly in the rec group** (`send_message` to `rec_group_id`), with inline buttons **Join** / **Leave**, and **edit that card in place** as people tap. Do not use inline mode. | Mitup needs inline mode only because it is never added to groups. Our bot is already a group admin, so it can post and edit its own message. This is the pattern Mitup, PollBot and every small RSVP bot found use. |
| 2 | **Per-user feedback through `query.answer(text, show_alert=…)`** ("You're in (#14)", "Session full, you're on the waitlist (#2)"). | The group card looks the same to everyone. The answer toast/alert shows only to the person who tapped. It is 0–200 characters. Ephemeral messages (Bot API 10.2+) are not in PTB 22.8, so skip them. |
| 3 | **Re-render the card from SQLite on every change, and coalesce bursts**: at most one edit per ~1.5 s per session, with a trailing edit that reads the latest state. Ignore the "Message is not modified" error. | The Telegram FAQ caps group sends at 20/min. A rush of taps when the card is posted is the one real flood risk. Mitup and BaivaruRSVP both do exactly this. |
| 4 | **The participant list shows plain names, not mentions.** Only the two "in charge" people get a real mention (`telegram.helpers.mention_html(user_id, name)`). | Mentions ping people. 18 pings on every edit would be spam. Mentions by user ID work in the group without a username, because the people picked are group members. |
| 5 | **Pick the two people once, at a fixed time** (for example when sign-ups close, or N hours before start), and **store the pick in SQLite before announcing it**. Use `random.sample(pool, 2)` (or `secrets.SystemRandom().sample`): element 0 is primary, element 1 is secondary. | `sample` is without replacement and in selection order. The Python docs describe exactly this "grand prize / second place" use. A stored pick survives restarts, like the existing `term_events` stamps. |
| 6 | **Fairness: choose from the people with the fewest past picks**, random among ties. | The simplest rotation that is still random. Weighted `random.choices` is *with* replacement, so it can pick the same person twice. |
| 7 | **DM both, but always also mention them in the group.** Catch `Forbidden` (they never started the bot) and fall back to the group mention only. | "Bots can't start conversations with users." |
| 8 | **Keep native Telegram polls as a non-option** (or a fallback). | No capacity, no waitlist, no "in charge" line on the card. The bot only learns votes via `poll_answer` updates that it must record itself. |

Open design questions for the treasurer and vice captain (not research):
- Must a person be **registered with the bot** (or be in the rec group) to Join?
  Registration guarantees the bot can DM them, which is needed for waitlist
  promotion and "in charge" DMs.
- Capacity. In the example, "18 **of 20**" may be **Mitup's free-tier cap of 20
  participants**, not the real court limit (see §1).
- When do sign-ups close? Does the card **lock at start** like Mitup's "Lock on
  start"? When is the "in charge" pick made? What happens if the primary leaves
  after being picked?
- Who may create a session: the vice captain only, all admins, or a new role?

---

## 1. What Mitup does

Mitup is an open-source (MIT) Telegram events bot, active since 2015, written in
Python on **PTB 22.8** with Postgres and AWS
([About](https://mitup.social/faq/about/);
[repo](https://github.com/AAraKKe/mitup-telegram-bot), `apps/bot/pyproject.toml`
pins `python-telegram-bot[ext]==22.8`).

**Creation is a DM wizard.** The bot "lives in your DMs" rather than joining group
chats ([repo README](https://github.com/AAraKKe/mitup-telegram-bot)). You tap
"➕ New meeting" and send a title, which is "the only thing the bot asks for". The
meeting then appears as an editable card with sections (title, images,
description, when, where, participants), each with "chips" to edit it. Dates come
from a calendar picker, or from Telegram's own date formatting in the title
message
([Create a meeting](https://mitup.social/user-guide/create_a_meeting/)). Card
buttons are Share, Settings, Delete and Refresh. The host is **not** added
automatically and must tap Join.

**Sharing uses inline mode or a share picker.** Tap "📨 Share" and pick a chat, or
type `@mitupbot` in any chat. "The bot never has to be added to that chat." Posting
needs the group to allow *Send Stickers & GIFs* (and *Send Photos* for cards with
images)
([Sharing and joining](https://mitup.social/user-guide/sharing_and_joining/);
[Inline mode](https://mitup.social/user-guide/inline_mode/)). This is why the
group card says "Built with Mitup": it is an inline message sent *via* the bot.
Copies shared into several chats "keep themselves current"
([Mitup 2.0](https://mitup.social/news/mitup_2_0/)).

**Join and leave.** "✅ Join" adds you and updates the count on every copy of the
card. "❌ Leave takes you back off." People who have never used Mitup can still
join: "Tapping ✅ Join signs them up from their Telegram profile and adds them to
the meeting." Joining is refused when the meeting is full with no waiting list,
or when lock-on-start is on and the meeting is in progress
([Sharing and joining](https://mitup.social/user-guide/sharing_and_joining/)).

**Capacity and waitlist.** The host can "Change limit" and "Kick out"
([Create a meeting](https://mitup.social/user-guide/create_a_meeting/)). The
waiting list is optional per meeting: "When the meeting is full, new joiners land
on a waiting list and move into the meeting automatically as spots free up, in the
order they joined"
([Meeting settings](https://mitup.social/user-guide/meeting_settings/)). "When a
confirmed participant leaves, the freed spot goes to whoever has waited longest,
and the bot messages them to say so"
([Sharing and joining](https://mitup.social/user-guide/sharing_and_joining/)).
**The free tier caps participants at 20 per meeting**, and only paid tiers have
"No cap" ([Limits](https://mitup.social/user-guide/limits/)). So "18 of 20" in the
example may be Mitup's cap rather than the club's chosen number. Worth asking the
vice captain.

**Other settings** ([Meeting settings](https://mitup.social/user-guide/meeting_settings/)):
- **Public**: anyone who receives the card may share it again.
- **Open invitations**: participants can add friends, even friends without
  Telegram.
- **Incognito**: the card shows the count only, not the names.
- **Lock on start**: "nobody can join or leave while it is in progress".

**"🔍 Searchable in this chat".** "Make it searchable ties a shared card to the
chat it sits in. After that, anyone in the chat can pull the meeting back up
through Mitup's inline mode without scrolling back to find the original message"
([Sharing and joining](https://mitup.social/user-guide/sharing_and_joining/)).
It is an inline-mode search index. **We do not need it.** Our bot can pin the
card (§2.6), or answer a `/session` command.

**Reminders.** "Everyone on the list with notifications on gets two reminders":
one before the start (the lead time is configurable, or off) and one when it
begins, both showing schedule and place. **After the end** (plus a timeout),
shared cards "keep their layout, lose their buttons and say the meeting has
finished"
([Meeting lifecycle](https://mitup.social/user-guide/meeting_lifecycle/)).

**Useful engineering details from Mitup's source**
([repo](https://github.com/AAraKKe/mitup-telegram-bot)):
- `libs/telegram/mitup_bot/api_wrapper.py` swallows edit errors matching
  `"Message is not modified"`.
- A migration (`add_render_digest_to_messages`) stores a fingerprint of the last
  card text Telegram confirmed. A refresh "that would re-send identical content can
  skip the `editMessageText` round trip instead of spending a call to be told the
  message is not modified". The docstring notes that this round trip "counts against
  the bot-wide flood limit".
- `handlers/meeting/join_leave.py` locks the meeting row before reading capacity
  and waitlist state, so two simultaneous taps cannot both take the last spot.
  Leave returns the list of "promoted links" and DMs them.
- It installs a custom PTB rate limiter (`MitupRateLimiter`) that retries on
  `RetryAfter`.

### How other bots do RSVP

- **@vote** (Telegram's own poll bot): you create polls in its DM, then share
  them via inline mode with `@vote`
  ([t.me/vote](https://telegram.me/vote); details from a directory listing,
  [botostore](https://botostore.com/c/vote), so **partly UNVERIFIED**).
- **Ultimate Poll Bot** (Nukesor; Python + PTB; **archived 2024-01-24**)
  ([GitHub](https://github.com/Nukesor/ultimate-poll-bot)). It has "doodle"
  (yes/no/maybe) and "limited vote" modes and syncs one poll across many chats.
  In `pollbot/poll/update.py`, the message that was tapped is edited immediately.
  Every other copy is marked in an `Update` row (count + `next_update`) and a
  background job edits them later. On `RetryAfter`, the next update is pushed back
  by `retry_after + 1` s. "Message is not modified" is ignored.
- **GroupHelp / Combot**: these are moderation bots. No RSVP feature was found
  in their docs (**UNVERIFIED**, not researched further).
- **Small open-source RSVP bots** (§4) all use the same shape: an admin creates an
  event, the bot posts **one live message** with **Join/Leave inline buttons**,
  edits it on every change, puts extras on a **waitlist** with promotion on leave,
  and lets admins **close sign-ups** (remove buttons, post a final list).

**Common pattern, which we should copy:** one card per session, Join/Leave
callback buttons, a list rendered from the database, edits throttled, a capacity
and a FIFO waitlist with a DM on promotion, and close/lock that strips the buttons.

---

## 2. Telegram API constraints (PTB v22)

### 2.1 Callback buttons on a group message

- **Anyone who can see the message can tap.** The update carries
  `CallbackQuery.from` ("Sender") and `message` ("Message sent by the bot with the
  callback button that originated the query")
  ([CallbackQuery](https://core.telegram.org/bots/api#callbackquery)). The tapper
  needs no prior contact with the bot, and Mitup's "signs them up from their
  Telegram profile" relies on this. Authorization (for example "registered members
  only") is our code's job.
- **`callback_data` is 1–64 bytes**
  ([InlineKeyboardButton](https://core.telegram.org/bots/api#inlinekeyboardbutton)).
  Use `ss:j:<session_id>` and `ss:l:<session_id>` (about 12 bytes). The repo
  already uses `prefix:` patterns (`pay:`, `optout:`, `nt:`).
- **Always answer.** "Telegram clients will display a progress bar until you call
  answerCallbackQuery. It is, therefore, necessary to react by calling
  answerCallbackQuery even if no notification to the user is needed"
  ([CallbackQuery](https://core.telegram.org/bots/api#callbackquery)).
- **Per-user feedback.** `answerCallbackQuery.text` is "0-200 characters".
  `show_alert`: "If True, an alert will be shown by the client instead of a
  notification at the top of the chat screen"
  ([answerCallbackQuery](https://core.telegram.org/bots/api#answercallbackquery)).
  In PTB this is `await query.answer("You're in (#14 of 20)")`. Use
  `show_alert=True` for things that need acknowledging ("Session full, you're #2 on
  the waitlist", "Sign-ups closed"). The answer goes only to the tapper, which is
  how a shared group message gives per-user responses.
- **Ephemeral messages (Bot API 10.2, 2026-07-14; reshaped in 10.3).** These are
  group messages "visible only to a specific user and the bot". Any bot may send
  one "within 15 seconds" of a callback query by passing its `callback_query_id`.
  An **admin** bot "can send an ephemeral message to any non-bot member of the chat
  at any time". Delivery "is not guaranteed … especially if the user is offline"
  ([Ephemeral Messages](https://core.telegram.org/bots/api#ephemeral-messages-and-commands);
  [changelog](https://core.telegram.org/bots/api-changelog)). PTB 22.8 stops at
  Bot API 10.0, so this would need raw `bot.do_api_request(...)`. The parameter
  shape also changed between 10.2 and 10.3. **Skip for now.** The callback alert
  covers the need.

### 2.2 Editing the card

- `editMessageText` takes `chat_id` + `message_id` (or `inline_message_id` for
  inline-mode messages). `text` is "1-4096 characters after entity parsing"
  ([editMessageText](https://core.telegram.org/bots/api#editmessagetext)). Store
  `chat_id` and `message_id` per session in SQLite. Messages the bot sent itself
  have **no edit time limit** in the docs. The 48-hour note applies only to
  *business* messages.
- **"Message is not modified".** Editing to identical text and markup fails with
  `BadRequest("Message is not modified…")`. This error string is **not documented**
  in the Bot API page. It is confirmed by both Mitup
  (`EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS`) and Ultimate Poll Bot, which catch it.
  Catch it and treat it as success. It happens with double taps (Join while
  already joined) and with coalesced edits.
- **Rate limits (official FAQ):** "In a single chat, avoid sending more than one
  message per second. We may allow short bursts that go over this limit, but
  eventually you'll begin receiving 429 errors. In a group, bots are not be able to
  send more than 20 messages per minute"
  ([Bots FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this)).
  Whether **edits** count toward the 20/min group budget is **UNVERIFIED**:
  Telegram does not document it. PTB's wiki says "Telegram does *not* document
  the precise limits … the limits may differ between bots and also over time"
  ([PTB wiki: Avoiding flood limits](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Avoiding-flood-limits)).
  Mitup's code assumes edits do count. Plan for it.
- **How bots debounce.**
  - *Leading + trailing throttle* (BaivaruRSVP, `bot.py`): the first tap edits at
    once. Later taps within `EDIT_THROTTLE = 1.5` s schedule **one** trailing edit,
    which re-renders the *latest* state. Code comment: "bursts of clicks collapse
    to a single update."
  - *Content digest* (Mitup): skip the edit when the rendered text equals the last
    confirmed text.
  - *Background flush* (Ultimate Poll Bot): mark the poll dirty and let a job do
    the edits, honouring `RetryAfter`.

  **For this repo:** keep a per-session "edit pending" flag. Schedule the edit with
  `context.job_queue.run_once(..., when=1.5, name=f"ss-edit-{id}")` if none is
  pending. The job renders from the DB. Data correctness never depends on the edit:
  the DB is the truth, and the card is a view.
- **Concurrency.** The app runs updates sequentially (`concurrent_updates` = 1,
  noted in [2026-10-07 research](2026-10-07-easier-admin-commands.md)), so two
  taps cannot race on the "last spot" inside one process. Still do the
  capacity check and the insert in one SQLite transaction (as Mitup does with a
  row lock), so the rule holds if concurrency ever changes.
- PTB ships an optional `AIORateLimiter` (needs the `rate-limiter` extra and
  `aiolimiter`). The wiki calls it "a minimal effort reference implementation"
  ([wiki](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Avoiding-flood-limits)).
  It is not needed at one club's volume if edits are coalesced.

### 2.3 Mentioning users without a @username

- Entity type `text_mention` is "for users without usernames"
  ([MessageEntity](https://core.telegram.org/bots/api#messageentity)). In HTML
  parse mode, write `<a href="tg://user?id=123456789">Name</a>`
  ([formatting options](https://core.telegram.org/bots/api#formatting-options)).
- Official conditions: "Links tg://user?id=<user_id> can be used to mention a user
  by their identifier without using a username. Please note: These links will work
  only if they are used inside an inline link or in an inline keyboard button …
  **Unless the user is a member of the chat where they were mentioned**, these
  mentions are only guaranteed to work if the user has contacted the bot in private
  in the past or has sent a callback query to the bot via an inline button and
  doesn't have Forwarded Messages privacy enabled for the bot"
  ([formatting options](https://core.telegram.org/bots/api#formatting-options)).
  For us: the people picked are rec-group members **and** have tapped Join (a
  callback query), so the mention works in the group.
- **Does it notify?** The Bot API does not say. That a mention by ID notifies the
  user like an @mention is standard Telegram behaviour, but **UNVERIFIED** in a
  primary source. The unofficial [limits.tginfo.me](https://limits.tginfo.me/en)
  lists "Mentions number in a single message: up to 50, only first 5 from list will
  receive notification" and "Formatting entities … up to 100 items". The Bot API
  documents **no** entity cap (searched `api` page: none). Either way, this is
  another reason to mention only the 2 people in charge and render everyone else
  as plain text.
- **PTB helpers.** `telegram.helpers.mention_html(user_id, name)` returns
  `f'<a href="tg://user?id={user_id}">{escape(name)}</a>'`. It HTML-escapes the
  name (checked in [source](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/helpers.py)).
  `User.mention_html(name=None)` does the same with `full_name` by default
  ([helpers docs](https://docs.python-telegram-bot.org/en/stable/telegram.helpers.html)).
  When the card is re-rendered later from the DB, there is no `User` object, so
  use the helper with the stored ID and name. **Escape every other name** in the
  list with `html.escape` (names are user-controlled).

### 2.4 DMs need the user to have started the bot

- "Bots can't start conversations with users. A user must either add them to a
  group or send them a message first"
  ([Bots intro](https://core.telegram.org/bots)). Otherwise `sendMessage` fails
  with a `Forbidden` error (PTB `telegram.error.Forbidden`). The repo already
  handles `Forbidden` in reminders.
- Tapping Join on a group card does **not** open a private chat, so a non-registered
  tapper may be un-DM-able. Options:
  - require registration to Join (`answer("Register with me first", url=…)`;
    `answerCallbackQuery.url` accepts "links like t.me/your_bot?start=XXXX"
    ([answerCallbackQuery](https://core.telegram.org/bots/api#answercallbackquery)));
  - or accept that some picks only get the group mention.

### 2.5 Message length

`sendMessage` text is "1-4096 characters after entities parsing"
([sendMessage](https://core.telegram.org/bots/api#sendmessage)). At about 30
characters per line, a 20-person list plus a 10-person waitlist is around 1,000
characters, well inside the limit. The repo already has a long-reply splitter, but
it is not needed for a card.

### 2.6 Pinning

"The bot must be an administrator with the 'can_pin_messages' right … to pin
messages in groups" ([pinChatMessage](https://core.telegram.org/bots/api#pinchatmessage)).
The bot is already a group admin, but the *pin* right must be ticked separately.
Pinning the current session card replaces Mitup's "Searchable in this chat". Use
`disable_notification=True` to avoid a second ping.

### 2.7 Native Telegram polls as an alternative

- `sendPoll`: question 1–300 characters, "1-12 answer options", `is_anonymous`
  "defaults to True", `allows_revoting`, `open_period` / `close_date` (5 s to about
  30 days), and new `hide_results_until_closes` and `description`. A poll can carry
  an inline keyboard (`reply_markup`)
  ([sendPoll](https://core.telegram.org/bots/api#sendpoll)).
- **Who voted.** `poll_answer`: "A user changed their answer in a non-anonymous
  poll. Bots receive new votes only in polls that were sent by the bot itself."
  `PollAnswer.option_ids` "May be empty if the vote was retracted"
  ([Update](https://core.telegram.org/bots/api#update);
  [PollAnswer](https://core.telegram.org/bots/api#pollanswer)). There is **no**
  method to fetch the voter list later. `Poll` only has `total_voter_count`. So the
  bot must record every `poll_answer` as it arrives. Telegram keeps undelivered
  updates "not … longer than 24 hours"
  ([getUpdates](https://core.telegram.org/bots/api#getupdates)), so a Pi outage
  longer than a day loses votes.
- **Pros:** native UI, no edit traffic, and people can see the voters in the client.
- **Cons:** **no capacity and no waitlist** (a 21st person can still vote "Going"),
  so the bot cannot enforce the court limit. It cannot show "in charge" or a
  waitlist on the poll itself. Votes are per option rather than a named list. The
  poll's own voter list is the truth, not ours.
- **Verdict:** inline-keyboard card. A poll is fine only if capacity never matters.

---

## 3. Random selection and fairness

- `random.sample(population, k)` is "Used for random sampling without replacement
  … The resulting list is in selection order so that all sub-slices will also be
  valid random samples. This allows raffle winners (the sample) to be partitioned
  into grand prize and second place winners"
  ([random docs](https://docs.python.org/3/library/random.html#random.sample)).
  So `primary, secondary = random.sample(pool, 2)` is exactly right.
- `random` "should not be used for security purposes". `secrets` is for
  "passwords, account authentication, security tokens"
  ([random](https://docs.python.org/3/library/random.html);
  [secrets](https://docs.python.org/3/library/secrets.html)). Picking a shuttle
  carrier is not a security decision. `random.sample` is fine. If someone wants
  "nobody can predict it", `secrets.SystemRandom().sample(pool, 2)` is a drop-in
  (OS entropy, `seed()` ignored). It costs nothing to use either.
- **Do not use `random.choices` for two people.** It is "chosen … *with*
  replacement" ([docs](https://docs.python.org/3/library/random.html#random.choices)),
  so it can return the same person twice.
- **Fairness (design choice):** pure random lets the same person be picked several
  sessions running. Common approaches, simplest first:
  1. **Fewest-picks-first**: keep `times_in_charge` per member (or count rows in a
     picks table). The pool is the participants with the minimum count. Sample 2
     from it; if it has only 1, take that person plus 1 from the next tier. This is a
     fair rotation that stays random among equals. *Recommended.*
  2. **Exclude recent**: drop anyone picked in the last N sessions, then sample.
  3. **Weighted without replacement**: weight `1/(1+times_picked)` and draw twice,
     removing the first winner. This needs a small loop because the stdlib has no
     weighted `sample`. More code, little gain.
- **Edge cases to define:** fewer than 2 participants (pick 1 or none). Count only
  confirmed participants, not the waitlist. If the primary leaves after the pick,
  promote the secondary and pick a new secondary? (This is a design question.)
  Record whether the secondary was "needed", if fairness should count only real
  duty.
- **Persist before announcing.** Write the pick to SQLite, then DM and mention.
  This follows the repo rule "stamps written after delivery", applied the same way
  as `term_events`, so a reboot never re-rolls a pick that has already been
  announced.

---

## 4. Open-source reference implementations

| Repo | Stack | Worth copying |
|---|---|---|
| [AAraKKe/mitup-telegram-bot](https://github.com/AAraKKe/mitup-telegram-bot) (Mitup itself, MIT, active 2026-10) | Python, **PTB 22.8**, Postgres, AWS | The real thing. Look at `apps/bot/mitup_bot/handlers/meeting/join_leave.py` (row-locked join/leave, waitlist promotion + DM) and `libs/telegram/mitup_bot/api_wrapper.py` (ignore "not modified", render digest, flood back-off). Far bigger than we need |
| [phoenixatom/BaivaruRSVP](https://github.com/phoenixatom/BaivaruRSVP) | Python, PTB 21.6, single `bot.py`, JSON files | Closest in size to us. Join/Leave on one live message, a hard cap with a "full" alert, `/endevent` closes with a 🔒 banner and posts the final list, and the **leading + trailing edit throttle** (`schedule_edit`, 1.5 s) |
| [Namanbhatia7/rsvp-bot](https://github.com/Namanbhatia7/rsvp-bot) | Python, Redis | Minimal cap + **waitlist with promotion on drop-out** (cricket matches) |
| [Mr-Sunglasses/OpenEventBot](https://github.com/Mr-Sunglasses/OpenEventBot) | Python, SQLite | `/event <description>`, Going / Can't-go buttons, live attendee list, admin-only create/delete, SQLite like us |
| [samzoozi/sport-league-bot](https://github.com/samzoozi/sport-league-bot) | Python, DynamoDB, Lambda | Sports-club domain: monthly squad + per-game skip, **waitlist replacement offered to the next person with a group tag**, game-day roster. Everything in-group, no DMs |
| [quekster/im_in_tele_bot](https://github.com/quekster/im_in_tele_bot) | Python, PTB, FastAPI webhook, Firestore | "One live signup message" with waitlists in forum topics |
| [Nukesor/ultimate-poll-bot](https://github.com/Nukesor/ultimate-poll-bot) (archived) | Python, PTB (old), Postgres | Background batched edits with `RetryAfter` handling, for cards shared to many chats |

None of the small repos was run or reviewed in depth. Star counts are 0–4, so
treat them as idea sources, not dependencies. Adding a library is not needed:
everything above is plain PTB plus SQLite, which the repo already has.

---

## Sketch for this repo (for discussion, not a spec)

1. **Create** (`/session` wizard in private chat, like `/newterm`): date, time,
   venue (preset "ISH 2"?), capacity, waitlist on/off, and when sign-ups close.
   Confirm → `send_message` to `rec_group_id` with `[✅ Join] [❌ Leave]`; store
   `chat_id` and `message_id`; optionally pin.
2. **Tap** → `ss:j:<id>` / `ss:l:<id>`: in one transaction, check capacity, then
   insert into participants or the waitlist (or delete and promote the waitlist
   head). `query.answer(...)` with the user's position. Schedule a coalesced card
   edit. DM anyone promoted (if `Forbidden`, mention them in the group instead).
3. **Close / pick** (JobQueue at the close time, plus an hourly catch-up like the
   existing scheduler): pick primary and secondary (§3), store them, edit the card
   to show "🧺 In charge: <mention> (backup: <mention>)", post one short group
   message with both mentions, and DM both.
4. **Remind** (optional, Mitup-style): a DM to participants N hours before.
5. **Finish**: after the end time, edit the card to remove the buttons and say
   "finished".

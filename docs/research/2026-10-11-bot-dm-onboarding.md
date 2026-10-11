# Getting members into a DM with the bot

Research date: 2026-10-11. Scope: the Bot API constraint that a bot cannot
message a user first, and what Telegram-native mechanisms exist to reliably
get a member from a group chat into a private chat with the bot so they can
register, `/pay`, send a receipt, or `/session` RSVP. No code was changed.

Sources: the Telegram Bot API page (current version noted on the page; see
[changelog](https://core.telegram.org/bots/api-changelog)), the MTProto
"Deep links" doc, the `messages.startBot` method page, the Bot `features`
page, the Telegram Login Widget page, the python-telegram-bot (PTB) deep-link
example and docs, and a Telegram Desktop GitHub issue that quotes the official
doc text back. Secondary sources (aiogram docs, blog posts) are used only to
triangulate, with the primary-source quote given alongside. Anything marked
**UNVERIFIED** was not confirmed in a primary source.

Repo facts used below: `clubbot/scheduler.py` already builds deep links as
**plain text** inside group posts — `group_post_texts()` (line 176) and
`post_payment_open()` (line 218) both write
`f"Tap to pay: https://t.me/{bot.username}?start=pay"` as a text URL, not an
`InlineKeyboardButton`. `clubbot/bot.py` line 166 already parses the payload:
`if context.args and context.args[0] == "pay":  # t.me/<bot>?start=pay`. The
join-request DM window is already implemented in `clubbot/removal.py`
(`request.user_chat_id`, line 479) for the rec-chat removal flow.

---

## 1. Deep links (`t.me/<bot>?start=<payload>`)

- **Private chats:** "In private chats, you can use the `start` parameter to
  automatically pass any value to your bot whenever a user presses the link."
  Example: `https://t.me/your_bot?start=airplane` → the bot receives
  `/start airplane`
  ([Bot features: Deep linking](https://core.telegram.org/bots/features#deep-linking)).
- **The exact client behavior** is specified on the MTProto side, not the Bot
  API page: "if provided and the `bot_username` is indeed a bot, the text
  input bar should be replaced with a `Start` button **(even if the user has
  already started the bot)**"
  ([Deep links: Bot links](https://core.telegram.org/api/links)). So tapping
  the link does **not** skip straight into `/start` for an existing user —
  every client is specified to show a `Start` button that still needs one
  more tap, with one documented exception: "if the `bot_username` is equal to
  the `premium_bot_username` configuration value, clicking on this link should
  immediately invoke `messages.startBot`" (same page). That configuration
  value names Telegram's own Premium bot, not an arbitrary club bot, so the
  exception does not apply here.
- `messages.startBot` is described as "Start a conversation with a bot using
  a deep linking parameter"
  ([messages.startBot](https://core.telegram.org/method/messages.startBot)).
  It is the call the client makes once the user taps `Start`.
- **Does opening a group link reliably land in a private chat, not just the
  bot's profile?** The doc's wording ("the text input bar should be replaced
  with a Start button") describes opening the *chat screen* for the bot
  with a Start button overlay, not a separate "profile page" screen — so the
  specified behavior is: tap the link → the bot's chat screen opens → tap
  `Start` → `/start <payload>` is sent. A **platform bug**, not the
  specified behavior, is documented: Telegram Desktop (4.11.5–5.2.2) was
  reported to always show the `Start` button (matching spec), while "mobile
  Telegram (iOS/Android), macOS, and web versions automatically execute the
  start command without requiring a button click" for users who had already
  started the bot before — i.e. those clients skip the extra tap in
  practice, which a maintainer closed by quoting the "even if … already
  started" doc text back, confirming Desktop's behavior (not the single extra
  tap) is the documented one
  ([tdesktop#27064](https://github.com/telegramdesktop/tdesktop/issues/27064)).
  Net: **expect at least one extra tap (`Start`) on first contact everywhere,
  and possibly on every tap depending on client/version** — never zero taps.
- **Group-add links exist but are a different thing.** `?startgroup=<payload>`
  is for *adding the bot to a group*, not for DM'ing an existing user: "In
  groups, you can add the parameter `startgroup` to this link" → opens a
  group-picker dialog to add the bot, then sends
  `/start@your_bot <payload>` to the new group
  ([Bot features: Deep linking](https://core.telegram.org/bots/features#deep-linking);
  [Deep links: Group/channel links](https://core.telegram.org/api/links)).
  Irrelevant to onboarding an existing member into a DM.
- **Payload constraint:** "A-Z, a-z, 0-9, `_` and `-` are allowed. The
  parameter can be up to 64 characters long" ([Bot features: Deep
  linking](https://core.telegram.org/bots/features#deep-linking)). Base64url
  is suggested for binary payloads. Our `start=pay` payload is well inside
  this.
- **Blocked bot.** No primary source states the client behavior for a user
  who has blocked the bot and then taps a deep link. Secondary reports (GitHub
  issues on PTB and other wrapper libraries) describe the *server-side*
  symptom once blocked: outbound sends from the bot fail with `Forbidden:
  bot was blocked by the user`
  ([python-telegram-bot `error.py`](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/error.py),
  already handled in `clubbot/scheduler.py`'s `_deliver`). Whether tapping the
  deep link itself silently unblocks the bot (common anecdotal claim) is
  **UNVERIFIED** against a primary source.

### PTB's own handling

PTB does nothing special for deep links beyond ordinary `/start` argument
parsing: the official example registers `CommandHandler("start",
start_callback)` and reads `context.args`, noting "Since Telegram doesn't
support spaces in deep linking parameters, you will have to manually split"
on a delimiter
([`examples/deeplinking.py`](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/deeplinking.py)).
This matches what `clubbot/bot.py` already does at line 166.

---

## 2. Inline URL buttons in group messages

- `InlineKeyboardButton.url`: "HTTP or tg:// URL to be opened when the button
  is pressed" ([InlineKeyboardButton](https://core.telegram.org/bots/api#inlinekeyboardbutton)).
  There is nothing in the field's spec restricting it to private chats — it
  is a generic property of a button attached to any message the bot sends,
  group or private, basic group or supergroup.
- **No special admin right is required to attach a URL button.** The
  `ChatAdministratorRights` the repo already checks for other group actions
  (`can_restrict_members`, `can_invite_users`, etc. — see
  `docs/research/2026-10-10-removing-unpaid-members.md` §3–4) govern
  moderation actions (ban/unban, invite links), not sending a message with an
  inline keyboard. Any bot that can post in the chat can attach
  `reply_markup=InlineKeyboardMarkup(...)` to that post — this is how the
  repo's own `/session` cards (`clubbot/sessions.py`) and admin menus
  (`clubbot/admin.py`) already work.
- **Privacy mode is irrelevant here.** Privacy mode governs which *incoming*
  group messages a non-admin bot receives ("Privacy mode is enabled by
  default for all bots, except bots that were added to a group as admins"
  — [Bot features: Privacy Mode](https://core.telegram.org/bots/features#privacy-mode)).
  It says nothing about what a bot may attach to its own *outgoing* messages.
  A URL button on a bot's own post works the same whether the bot is a group
  admin or not.
- **Basic groups vs. supergroups.** Inline keyboards have been a core Bot API
  feature since inline keyboards were introduced and are documented at the
  `Message`/`sendMessage` level with no chat-type carve-out
  ([sendMessage](https://core.telegram.org/bots/api#sendmessage),
  [InlineKeyboardMarkup](https://core.telegram.org/bots/api#inlinekeyboardmarkup)).
  No primary-source text distinguishes basic groups from supergroups for
  button behavior; the repo's groups are supergroups in practice (the
  `removal.py` join-request flow and `chat_member` updates it depends on are
  supergroup/channel-only features per the Bot API's own method
  descriptions), so this caveat is moot for this project.
- **Conclusion: this is just a standard, allowed URL button.** The repo's
  current group posts (`clubbot/scheduler.py` lines 176, 218) write the deep
  link as plain clickable text inside the message body rather than as a
  button. Both are valid Telegram UX (Telegram auto-linkifies `t.me/...` text
  into a tappable link too), but a dedicated `InlineKeyboardButton(text="Pay
  now / Register", url="https://t.me/<bot>?start=pay")` is the more
  conventional "get into my DM" affordance and is no riskier or more
  restricted than the plain-text link already in use.

---

## 3. UX of tapping a "Start chat" deep-link button

Per §1's primary sources, the specified/common experience is:

1. **Never contacted the bot before:** tapping the link/button opens the
   bot's chat screen with a `Start` button replacing the message input bar.
   One more tap sends `/start <payload>`
   ([api/links](https://core.telegram.org/api/links)).
2. **Already started the bot before:** per the doc text itself the `Start`
   button is shown "even if the user has already started the bot" — i.e. the
   *specified* behavior is still one more tap. In practice, several mobile/
   web/macOS client versions were reported to skip that tap and resend
   `/start <payload>` immediately for returning users, which a Telegram
   Desktop maintainer treated as the Desktop app being the one that matches
   the documented (always-show-Start) behavior, implying client behavior here
   is **not uniform across platforms/versions**
   ([tdesktop#27064](https://github.com/telegramdesktop/tdesktop/issues/27064)).
3. **Previously blocked the bot:** no primary-source description found. The
   only confirmed primary fact is the server-side consequence (`Forbidden:
   bot was blocked by the user` on any bot-initiated send) that the project's
   own `_deliver()` already handles. Treat "does tapping the link
   auto-unblock" as **UNVERIFIED**; do not rely on it.
4. **Skipping the extra tap entirely:** no general mechanism is documented.
   The one documented exception (`premium_bot_username` → immediate
   `messages.startBot`, no button) is Telegram's own Premium bot, not
   something a third-party bot can opt into
   ([api/links](https://core.telegram.org/api/links)).

**Bottom line:** budget for at least one tap (`Start`) beyond the link tap
itself on every cold contact, and possibly on every contact depending on the
member's client. There is no API-level way to force zero taps for a
third-party bot.

---

## 4. Alternative Telegram-native mechanisms

### `KeyboardButtonRequestChat` / `chat_shared`

- Added in Bot API 6.5 (2023-02-03): the `request_chat` field on
  `KeyboardButton`, the `ChatShared` class, and the `chat_shared` field on
  `Message`. `request_title`, `request_username`, `request_photo` were added
  2024-03-31
  ([Bot API changelog](https://core.telegram.org/bots/api-changelog), per the
  official changelog's own dated entries). The mechanism: "the `request_chat`
  field in `KeyboardButton` is optional. If specified, pressing the button
  will open a list of suitable chats, and tapping on a chat will send its
  identifier to the bot in a `chat_shared` service message."
- **What it actually does:** it lets a user, from inside a chat where the bot
  already has a custom reply keyboard, *pick and share the identifier of some
  other chat* (e.g. "pick a channel you admin") with the bot — it is a chat
  **picker**, not a contact-initiation mechanism. It requires the bot to
  already be in a conversation where it can show a custom keyboard (i.e. the
  user already has to be interacting with the bot), and it hands over a
  *chat ID*, not a promise that a DM is now open.
- **Not applicable** to "get a stranger from the group into my DM." It solves
  a different problem (letting a user already talking to the bot point it at
  a chat).

### Telegram Login Widget

- "The Telegram login widget is a simple and secure way to authorize users on
  your website" via `/setdomain` with BotFather and an embedded JS widget that
  redirects with `id, first_name, last_name, username, photo_url, auth_date,
  hash`, verified via HMAC-SHA-256 of the bot token
  ([Login Widget](https://core.telegram.org/widgets/login)).
- **Confirmed irrelevant**, as expected: it is a web-page OAuth-style login
  widget for *authenticating a website visitor's Telegram identity*, not a
  way to open or trigger a Telegram DM. It needs a website and a domain
  registered to the bot; this project has neither.

### Business accounts / Mini Apps

- Recent Bot API changelog entries (per search results against the official
  changelog) add things like business-bot token management
  (`getManagedBotToken`, `replaceManagedBotToken`), checklist messages "on
  behalf of business accounts," and a `savePreparedKeyboardButton` /
  `PreparedKeyboardButton` mechanism letting **Mini Apps** request users/chats
  from inside a web app. These all presuppose either (a) a Telegram Business
  account (a paid personal-account feature unrelated to a club bot) or (b) an
  existing Mini App the user has already opened. None of them solves "a
  stranger in a group has never DMed the bot" — they all still require the
  user to have already reached the bot or a Mini App it controls.
- **Conclusion: not applicable.** No Bot API 7.x/8.x addition removes the
  fundamental "user must initiate" rule from
  [the bots intro page](https://core.telegram.org/bots#how-are-bots-different-from-users):
  "Bots can't start conversations with users. A user must either add them to
  a group or send them a message first." (Already quoted in
  `docs/research/2026-10-10-removing-unpaid-members.md` §5 with the same
  citation.)

---

## 5. How this is solved in practice

- **Deep-link button in a group post / pinned message.** This is the
  pattern the Bot API itself documents and names: the entire "Deep linking"
  page under `bots/features` exists to describe exactly this use case —
  "deep linking allows bots to receive additional parameters on startup,"
  with the private-chat `?start=` form as the primary mechanism
  ([Bot features: Deep linking](https://core.telegram.org/bots/features#deep-linking)).
  PTB ships an entire example (`examples/deeplinking.py`) built around this
  pattern, confirming it as the library's own recommended idiom
  ([python-telegram-bot/examples/deeplinking.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/deeplinking.py)).
  The aiogram framework documents the identical idea with its own
  `create_start_link()` helper under a page titled "Deep Linking"
  ([aiogram docs: Deep Linking](https://docs.aiogram.dev/en/latest/utils/deep_linking.html)) —
  independent confirmation that this is the standard cross-framework pattern,
  not something specific to PTB.
- **The join-request DM window** (already built in `clubbot/removal.py`) is
  the Bot-API-documented way to reach someone who has *never* contacted the
  bot at all, without waiting for them to tap anything: `ChatJoinRequest.
  user_chat_id` — "the bot can use this identifier for 5 minutes to send
  messages until the join request is processed"
  ([ChatJoinRequest](https://core.telegram.org/bots/api#chatjoinrequest),
  added Bot API 6.5). This is a bot-initiated push, not dependent on the user
  tapping a link at all, but it only fires once, at join time, and only for
  people who join *through* that gated link.
- **Requiring `/start` before some group action** is the pattern the
  paid-community bots documented in
  `docs/research/2026-10-10-removing-unpaid-members.md` §6 all converge on:
  every one of them (InviteMember, LaunchPass, BotSubscription, Tribute)
  issues a personal/join-request link specifically so that *getting into the
  group at all* forces the DM step first. That is a stronger lever than a
  friendly button because it is a precondition, not an invitation.
- **A pinned message with the deep-link button** is the common community
  practice referenced by both the official deep-linking page's framing (a
  bot-wide "message me to get started" affordance) and multiple third-party
  walkthroughs (not independently primary-sourced, but consistent with the
  official pages above): post once, pin it, and let the group's existing
  "pinned message" UI do the discovery work instead of relying on a scheduled
  group post scrolling out of view.

---

## Options at a glance

| Approach | What it is | Reliability | Friction |
|---|---|---|---|
| **Plain-text deep link in a group post** (current: `clubbot/scheduler.py` lines 176, 218) | `t.me/<bot>?start=pay` written as text, auto-linkified by Telegram clients | Same deep-link mechanics as the button form (§1); works, but a wall of text is easy to skim past in a busy group, and the raw URL reads less like a call-to-action | One tap on the link + the documented `Start` tap (possibly skipped on some clients per §3) |
| **Inline URL button in a group post** (`InlineKeyboardButton(url=...)`) | The same deep link, as a labeled button instead of inline text | Identical underlying guarantee to the text link (§1–2); no extra admin right or privacy-mode requirement (§2) | Same tap count as above; more visually discoverable, no reliability change |
| **Pinned message with the deep-link button** | Either of the above, pinned | Same mechanics; durability differs — it stays visible instead of scrolling away | Same tap count; lower *discovery* friction over time, not lower per-tap friction |
| **Join-request DM window** (already built, `clubbot/removal.py`) | Bot pushes a DM during the 5-minute `user_chat_id` window at join time | Strongest guarantee for brand-new joiners — no tap or prior `/start` needed at all (§5) | Zero taps for the user, but only available once, at the moment they join via that gated link — does not reach existing members who joined before the gate existed or through a different link |
| **Require `/start` as a precondition of a group action** (e.g. the existing join-request gate, or a future "must DM the bot to RSVP/be counted") | Mirrors every paid-community bot surveyed in the prior research doc (§5) | Reliable in the sense that it's enforced, not merely suggested — but still depends on the member completing the DM, which depends on the same deep-link/tap mechanics above | Higher friction (a hard gate, not an invitation), but converts "maybe" into "must," at the cost of being a harder ask of the treasurer's members |

No approach above removes the single documented floor: a user must press
something (the link, then very likely a `Start` button) at least once before
the bot can ever message them outside the 5-minute join-request window. Every
option is a variation on *where and how hard* that one unavoidable tap is
presented, not a way around it.

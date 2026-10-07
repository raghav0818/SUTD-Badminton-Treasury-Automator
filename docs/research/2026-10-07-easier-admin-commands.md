# Easier admin commands: tappable alternatives to typed arguments

Research date: 2026-10-07. Scope: how the treasurer could run admin commands
(especially `/newterm`) by tapping instead of typing, on a phone, with the bot
long-polling from a Raspberry Pi that has **no public HTTPS endpoint**. No code
was changed.

Sources: Telegram Bot API docs (current version **Bot API 10.3**, 2026-08-24,
[changelog](https://core.telegram.org/bots/api-changelog)), Telegram's bot
features, Mini Apps and Serverless pages, and the python-telegram-bot (PTB) docs,
source and examples. Every claim is cited. Anything marked **UNVERIFIED** was not
confirmed in a primary source.

Repo facts used below: `requirements.txt` pins `python-telegram-bot[job-queue]>=21.0`
with no upper bound. The dev machine has **21.6** installed, which implements
**Bot API 7.10** (checked with `telegram.__bot_api_version__`). The newest PTB on
PyPI is **22.8** ([PyPI](https://pypi.org/project/python-telegram-bot/)). A fresh
`pip install` on the Pi would get 22.x.

---

## TL;DR

Ranked recommendation. Effort is a rough size estimate. Fit means how well it
works on a Pi with no HTTPS.

| # | Option | Effort | Fit (Pi, no HTTPS) | Verdict |
|---|---|---|---|---|
| 1 | **Bare-command button wizard for `/newterm`**: ConversationHandler + inline buttons. Preset date buttons, typed-date fallback, **"Same prices as last term"** button, and a confirm screen | Medium (~150–250 lines + tests) | Perfect: pure Bot API, works with long polling | **Do this** |
| 2 | **Scoped `/` command menu**: `setMyCommands` with `BotCommandScopeAllPrivateChats` for members and `BotCommandScopeChat(chat_id=<admin id>)` for each admin | Small (~30–40 lines) | Perfect | **Do this.** Combined with #1, the treasurer taps `/newterm` in the menu and never types it |
| 3 | **Confirm buttons + pick lists for SUTD-ID commands**: `/markpaid` and `/revoke` show "Confirm: Alice Tan (1010123)?" buttons. `/removeadmin` and `/relink` list current entries as buttons | Small–medium | Perfect | Do after #1 and #2. The main gain is catching typos on money and role actions |
| 4 | **CopyTextButton template** (stopgap): a bare `/newterm` replies with a pre-filled command built from last term, which the treasurer copies, pastes and edits | Tiny | Perfect, but needs PTB ≥ 21.7 | Cheap interim step. Editing a long string on a phone is still tedious |
| 5 | **Mini App form on GitHub Pages**: a static HTTPS page with a native `<input type="date">` sends results via `Telegram.WebApp.sendData` from a reply-keyboard button | Medium (HTML/JS page + handler + validation + a second thing to deploy) | Works: no server needed, the page only has to be HTTPS | Possible, but overkill for one form a term |
| 6 | Telegram Serverless hosting (new 2026-10-06) | — | **Conflicts**: the platform manages a webhook, and webhooks break `getUpdates` long polling | Do not use |
| — | `switch_inline_query_current_chat`, inline mode | — | Needs inline mode turned on, and inserts `@bot query`, not a command | Not useful here |

**Suggested order: #2, then #1 (with "same prices as last term"), then #3.**
Skip #5 unless several more multi-field forms appear. Telegram has **no native
date picker** in the Bot API, up to and including 10.3 (§2).

---

## 1. Inline-keyboard wizards (InlineKeyboardMarkup + CallbackQueryHandler + ConversationHandler)

### Bot API facts

- `callback_data` is "Data to be sent in a callback query to the bot when the
  button is pressed, **1-64 bytes**"
  ([InlineKeyboardButton](https://core.telegram.org/bots/api#inlinekeyboardbutton)).
- After a button press, "Telegram clients will display a progress bar until you
  call answerCallbackQuery. It is, therefore, necessary to react by calling
  answerCallbackQuery even if no notification to the user is needed"
  ([CallbackQuery](https://core.telegram.org/bots/api#callbackquery)). The
  answer's toast text is 0–200 characters
  ([answerCallbackQuery](https://core.telegram.org/bots/api#answercallbackquery)).
- Telegram recommends editing the keyboard in place rather than sending new
  messages: "consider editing your keyboard when the user toggles a setting
  button or navigates to a new page – this is both faster and smoother"
  ([features: Inline Keyboards](https://core.telegram.org/bots/features#inline-keyboards)).
- Maximum number of buttons per keyboard: **UNVERIFIED**. The Bot API page does
  not state one. 100 is commonly cited. Stay well under it, for example by
  splitting member lists across pages.
- Message text is 1–4096 characters after entity parsing
  ([sendMessage](https://core.telegram.org/bots/api#sendmessage)).
- Bot API 10.3 added `style` (red/green/blue) for buttons (added in 9.4) and a
  `disabled` button state
  ([changelog](https://core.telegram.org/bots/api-changelog)). PTB 21.6 does not
  support these. They are cosmetic, so do not depend on them.

### PTB ConversationHandler facts (docs are v22.8)

Source: [ConversationHandler docs](https://docs.python-telegram-bot.org/en/stable/telegram.ext.conversationhandler.html)
and [source](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/ext/_handlers/conversationhandler.py).

- Signature defaults: `allow_reentry=False, per_chat=True, per_user=True,
  per_message=False, conversation_timeout=None, name=None, persistent=False`.
- **per_\* settings** ([PTB FAQ](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-Asked-Questions#what-do-the-per_-settings-in-conversationhandler-do)):
  the default `per_user=True, per_chat=True` means "in each chat each user can
  have its own conversation". `per_message=True` keys the conversation on the
  keyboard message's ID, but "this approach can only work, if all the handlers in
  the conversation are CallbackQueryHandlers".
- **Mixing warning.** The source emits a warning for each case:
  - `per_message=True` with any non-CallbackQueryHandler: "all entry points,
    state handlers, and fallbacks must be 'CallbackQueryHandler'".
  - `per_message=False` with a CallbackQueryHandler: "'CallbackQueryHandler' will
    not be tracked for every message."

  A `/newterm` wizard needs both typed text (name, custom dates, custom fees) and
  buttons, so it **must use `per_message=False`** (the default) and will log that
  warning. In practice this means a tap on an *older* wizard message is handled
  as if it were on the current one. Mitigation: remove the old message's keyboard
  when moving to the next step (`edit_message_reply_markup`), or put a short
  wizard ID in `callback_data`.
- **Concurrency.** "ConversationHandler heavily relies on incoming updates being
  processed one by one … `concurrent_updates` should be set to False" (docs). The
  app is built with `Application.builder()` and its `concurrent_updates` is 1
  (sequential), checked locally. This is fine.
- **Timeout.** `conversation_timeout` ends an inactive conversation and runs
  handlers in the `ConversationHandler.TIMEOUT` state. "This feature relies on
  the `Application.job_queue`" (docs). If there is no JobQueue the source logs
  "Ignoring `conversation_timeout`". The repo already installs `[job-queue]`. The
  docs say timeouts are not supported with nested conversations.
- **Persistence and restarts.** `persistent=True` requires `name` and a
  persistence object set on the Application (docs). Otherwise conversation state
  is an in-memory dict (`self._conversations: ConversationDict = {}`, source).
  It is loaded from `application.persistence.get_conversations(self.name)` only
  when persistence is configured. **Without persistence, a restart silently drops
  an in-progress wizard** (systemd `Restart=always`, `deploy/update.sh`):
  - The treasurer's next typed reply matches no handler and is ignored.
  - Taps on the old wizard's buttons match nothing if their CallbackQueryHandlers
    exist only inside `states`. No `answerCallbackQuery` is sent, so the client
    shows a spinner (Bot API note above).

  **Fix without adding persistence:** register a top-level catch-all
  `CallbackQueryHandler(pattern=r"^nt:")` after the ConversationHandler. It
  answers "This form expired, send /newterm again." PTB's built-in
  `PicklePersistence` would also work (see `persistentconversationbot.py`) with
  no new dependency. It is not worth it for a one-minute wizard, and it would add
  a second state file next to `clubbot.db`.
- **Reference examples** (PTB repo `examples/`):
  - [inlinekeyboard2.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/inlinekeyboard2.py):
    CallbackQueryHandlers routed by `pattern="^…$"` inside ConversationHandler
    states, `query.answer()` on every tap, `edit_message_text` to move between
    steps.
  - [conversationbot.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/conversationbot.py):
    step-by-step questions using a ReplyKeyboardMarkup with
    `one_time_keyboard=True, input_field_placeholder=...` and `ReplyKeyboardRemove()`.
  - [conversationbot2.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/conversationbot2.py):
    collects several fields, then confirms.
  - [nestedconversationbot.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/nestedconversationbot.py):
    child conversations with `map_to_parent`. Not needed here.
  - [persistentconversationbot.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/persistentconversationbot.py):
    `PicklePersistence(filepath=...)` with `name=..., persistent=True`.
  - [arbitrarycallbackdatabot.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/arbitrarycallbackdatabot.py):
    `arbitrary_callback_data(True)` lets Python objects stand in for callback
    data. It needs the `[callback-data]` extra and uses persistence so buttons
    survive restarts. Not needed: all our payloads fit easily in 64 bytes, for
    example `nt:start:2026-09-01` is 19 bytes.

### Fit with this repo

`clubbot/bot.py` already uses the needed pieces: one ConversationHandler (the
`/start` registration, text-only, no timeout, no persistence) and top-level
CallbackQueryHandlers with `prefix:` patterns (`pay:start`,
`pay:shirt:(yes|no)`, `pay:size:…`, `optout:\d+`, `payment:(approve|reject):\d+`).
A wizard would add one more prefix, for example `nt:`. Setting
`allow_reentry=True` lets a second `/newterm` restart the wizard. Add `/cancel`
as a fallback.

### Sketch of the `/newterm` wizard

1. Bare `/newterm` asks for the name. Buttons offer a suggestion such as "Term 2"
   (last name with the number incremented) or "Type a name".
2. **Start date**: buttons "Today", "Next Mon", "Type a date".
3. **End date**: buttons computed from the start, e.g. "+13 weeks (2026-12-01)",
   "+14 weeks", "Type".
4. **Deadline**: buttons "+2 weeks from start", "+3 weeks", "Type".
5. **Prices**: button "Same as last term: comp S$20 / rec S$25 / rec+shirt S$30 /
   shirt S$15", or "Enter prices" (four typed steps, or one line `20 25 30 15`).
6. **Confirm** screen with the whole summary, and buttons "Create term" /
   "Cancel". Then call the existing `db.create_term(...)` and
   `scheduler.schedule_term_jobs(...)` unchanged. All validation stays in
   `create_term` and `_parse_fee_cents`.

---

## 2. Date input

- **No native date picker exists in the Bot API.** KeyboardButton can request
  users, chats, managed bots, contact, location, poll, or open a Mini App, and
  nothing else ([KeyboardButton](https://core.telegram.org/bots/api#keyboardbutton)).
  InlineKeyboardButton has no date type either
  ([InlineKeyboardButton](https://core.telegram.org/bots/api#inlinekeyboardbutton)).
  The 2025–2026 changelog (Bot API 9.2 to 10.3) adds no date input
  ([changelog](https://core.telegram.org/bots/api-changelog)).
- Bot API 9.5 (2026-03-01) added a `date_time` **MessageEntity**, "allowing bots
  to show a formatted date and time to the user"
  ([changelog](https://core.telegram.org/bots/api-changelog);
  [MessageEntity](https://core.telegram.org/bots/api#messageentity)). This is
  for display only, not input.
- Telegram itself lists "a personalized calendar for selecting dates" as a use
  for keyboard-button **Mini Apps**
  ([Mini Apps: Keyboard Button Mini Apps](https://core.telegram.org/bots/webapps#keyboard-button-mini-apps)).
  That confirms a calendar UI means either a Mini App (§6) or a calendar you
  build from buttons.
- Third-party inline calendar libraries exist on PyPI (for example
  `python-telegram-bot-calendar`). Their maintenance and PTB 21/22 compatibility
  are **UNVERIFIED**. Do not add one. If it is ever needed, a month grid built
  from stdlib `calendar.monthcalendar()` is about 40 lines: 7 columns × up to 6
  rows plus ◀ ▶ navigation, about 45 buttons, with callback data like
  `nt:d:2026-09-01`.
- **Recommendation: preset buttons plus a typed fallback.** Term dates are
  usually known in advance and fall in a few patterns, so presets relative to the
  chosen start date cover most taps. For typing, accept several formats with
  stdlib `datetime.strptime` (for example `2026-09-01`, `1/9/2026`,
  `1 Sep 2026`). Always echo the result back as an unambiguous date such as
  "Tue 1 Sep 2026" on the confirm screen. Keep the Singapore-time rule
  (`db.SINGAPORE_TIME`) for "Today" and "Next Mon".

---

## 3. Command menu per role (setMyCommands + BotCommandScope)

- `setMyCommands(commands, scope, language_code)` accepts "At most **100**
  commands". The scope defaults to `BotCommandScopeDefault`
  ([setMyCommands](https://core.telegram.org/bots/api#setmycommands)).
- BotCommand: `command` is 1–32 characters, "only lowercase English letters,
  digits and underscores". `description` is **1–256** characters
  ([BotCommand](https://core.telegram.org/bots/api#botcommand)).
- There are 7 scopes: Default, AllPrivateChats, AllGroupChats,
  AllChatAdministrators, Chat, ChatAdministrators, ChatMember
  ([BotCommandScope](https://core.telegram.org/bots/api#botcommandscope)).
- **Precedence** for the chat with the bot. "The first list of commands which is
  set is returned":
  1. `botCommandScopeChat + language_code`
  2. `botCommandScopeChat`
  3. `botCommandScopeAllPrivateChats (+ language_code)`
  4. `botCommandScopeDefault (+ language_code)`

  `botCommandScopeChatMember` appears only in the **group** list
  ([Determining list of commands](https://core.telegram.org/bots/api#determining-list-of-commands)).
  So a per-admin private menu uses **`BotCommandScopeChat(chat_id=<admin's
  private chat id>)`**, not ChatMember. ChatMember covers "a specific member of a
  group or supergroup chat"
  ([BotCommandScopeChatMember](https://core.telegram.org/bots/api#botcommandscopechatmember)).
- `BotCommandScopeChat.chat_id` is "Unique identifier for the target chat …"
  ([BotCommandScopeChat](https://core.telegram.org/bots/api#botcommandscopechat)).
  That a private chat's ID equals the user's ID is **UNVERIFIED** in the Bot API
  text. However, this repo already relies on it: `context.bot.send_message(chat_id=member["telegram_user_id"], …)`
  in `admin.py`, which worked live in v1.
- **Re-setting is required when roles change.** A scope is a stored list. To
  remove one, call `deleteMyCommands`: "After deletion, higher level commands will
  be shown to affected users"
  ([deleteMyCommands](https://core.telegram.org/bots/api#deletemycommands)). Plan:
  - On startup, set the member list for `AllPrivateChats` and the admin list for
    each admin's `Chat` scope.
  - After `/addadmin`, set that admin's `Chat` scope.
  - After `/removeadmin`, delete it.
  - After `/transfertreasurer`, re-set both people's scopes (if the treasurer and
    admins see different lists).
  - Re-running everything on each startup self-heals any drift.
- **The menu is not security.** "Bot API updates will not contain any
  information about the scope of a command sent by the user – in fact, they may
  contain commands that don't exist at all … Your backend should always verify
  that received commands are valid and that the user was authorized"
  ([features: Command Scopes](https://core.telegram.org/bots/features#command-scopes)).
  The existing `_is_admin` and `_is_treasurer` checks stay.
- The menu "can hold some or all of a bot's commands, including a short
  description for each. Users can then select a command from the menu without
  needing to type it out"
  ([features: Menu Button](https://core.telegram.org/bots/features#menu-button)).
  Whether tapping sends the command immediately or inserts it for editing on each
  mobile client is **UNVERIFIED**. Either way, this is why **bare commands must
  start a wizard** (§1). Today a bare `/newterm` only returns a usage error.
- The repo does not call `set_my_commands` anywhere today. The list is
  presumably set by hand in @BotFather (**UNVERIFIED**). A per-scope list set by
  the API takes precedence over the default one.
- PTB 21.6 has `BotCommandScopeChat`, `BotCommandScopeAllPrivateChats` and
  `Bot.set_my_commands` / `delete_my_commands` (checked locally).

---

## 4. Persistent reply keyboards (ReplyKeyboardMarkup / ReplyKeyboardRemove)

- Fields ([ReplyKeyboardMarkup](https://core.telegram.org/bots/api#replykeyboardmarkup)):
  - `is_persistent`: "always show the keyboard when the regular keyboard is
    hidden. Defaults to False, in which case the custom keyboard can be hidden
    and opened with a keyboard icon."
  - `resize_keyboard`: shrink to fit. "Defaults to False, in which case the
    custom keyboard is always of the same height as the app's standard keyboard."
  - `one_time_keyboard`: "hide the keyboard as soon as it's been used. The
    keyboard will still be available … the user can press a special button in
    the input field to see the custom keyboard again."
  - `input_field_placeholder`: 1–64 characters.
  - Bot API 10.3 also added `force_reply`.
- `ReplyKeyboardRemove`: "By default, custom keyboards are displayed until a new
  keyboard is sent by a bot". `remove_keyboard` means "user will not be able to
  summon this keyboard"
  ([ReplyKeyboardRemove](https://core.telegram.org/bots/api#replykeyboardremove)).
- A plain text button "will be sent as a message when the button is pressed"
  ([KeyboardButton](https://core.telegram.org/bots/api#keyboardbutton)). A
  button whose text is literally `/newterm` therefore sends the command, and the
  existing CommandHandler fires. This is an inference from the docs: the message
  starts with `/`, so it carries a `bot_command` entity.
- Assessment: an "admin keypad" (`is_persistent=True, resize_keyboard=True`) with
  `/newterm`, `/unpaid`, `/stats`, `/remind` is possible, but it mostly
  duplicates the scoped `/` menu (§3). Telegram keeps a custom keyboard per chat
  until replaced or removed, so it would need careful `ReplyKeyboardRemove`
  handling when roles change. **Low priority.** The one place a reply keyboard is
  required is for launching a Mini App that uses `sendData` (§6), and for
  `request_users` / `request_chat` pickers (§9).
- `ForceReply` makes clients "act as if the user has selected the bot's message
  and tapped 'Reply'" and supports `input_field_placeholder`
  ([ForceReply](https://core.telegram.org/bots/api#forcereply)). This is useful
  for wizard steps that need typed text (term name, custom fee, a `/settings`
  value): it pops up the keyboard with a hint such as "e.g. 2026-09-01".

---

## 5. Menu button (setChatMenuButton)

- `setChatMenuButton(chat_id?, menu_button?)` changes "the bot's menu button in a
  private chat, or the default menu button"
  ([setChatMenuButton](https://core.telegram.org/bots/api#setchatmenubutton)).
- Types ([MenuButton](https://core.telegram.org/bots/api#menubutton)):
  - `MenuButtonCommands` opens the command list.
  - `MenuButtonWebApp(text, web_app)` launches a Web App.
  - `MenuButtonDefault` means not set. "By default, the menu button opens the
    list of bot commands."
- A menu-button Mini App works "in the exact same way as when using inline
  buttons"
  ([Mini Apps: menu button](https://core.telegram.org/bots/webapps#launching-mini-apps-from-the-menu-button)).
  So it **cannot use `sendData`** (§6). It would need `answerWebAppQuery` or a
  server.
- Assessment: keep the default, which opens the commands list. That list is the
  per-role command menu from §3. Changing the menu button is only relevant if a
  Mini App is adopted, and even then the `sendData` route needs a reply-keyboard
  button, not the menu button.

---

## 6. Mini Apps (Web Apps) as a form, with no server

- **HTTPS is required.** `WebAppInfo.url` is "An HTTPS URL of a Web App"
  ([WebAppInfo](https://core.telegram.org/bots/api#webappinfo)).
- **sendData works only from a keyboard button.** "Mini Apps launched from a
  web_app type keyboard button can send data back to the bot in a service message
  using Telegram.WebApp.sendData. This makes it possible for the bot to produce a
  response without communicating with any external servers"
  ([Keyboard Button Mini Apps](https://core.telegram.org/bots/webapps#keyboard-button-mini-apps)).
  `sendData(data)` sends "data of the length **up to 4096 bytes**, and the Mini
  App is closed … This method is only available for Mini Apps launched via a
  Keyboard button"
  ([WebApp methods](https://core.telegram.org/bots/webapps#initializing-mini-apps)).
- The KeyboardButton `web_app` field: "The Web App will be able to send a
  “web_app_data” service message. **Available in private chats only.**"
  ([KeyboardButton](https://core.telegram.org/bots/api#keyboardbutton)). This is
  fine, because admin commands are already private-only.
- The bot receives `Message.web_app_data` of type
  [WebAppData](https://core.telegram.org/bots/api#webappdata) with `data` and
  `button_text`. **"Be aware that a bad client can send arbitrary data in this
  field."** So the bot must re-validate everything. Reusing
  `parse_newterm_args`, `_parse_fee_cents` and `db.create_term` does that.
- **initData validation: not applicable.** `WebAppInitData` "is empty if the Mini
  App was launched from a keyboard button"
  ([WebAppInitData](https://core.telegram.org/bots/webapps#webappinitdata)).
  Validation exists for data the page sends to *your own server*
  ([Validating data](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)).
  With `sendData` the payload arrives as an ordinary update in the bot's private
  chat, so authorization is the usual `update.effective_user.id` role check.
  That this sender field is trustworthy is an inference: it is the same mechanism
  every command relies on.
- **A static page works.** PTB's own
  [webappbot.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/webappbot.py)
  example uses `application.run_polling(...)`, a `KeyboardButton(web_app=WebAppInfo(url="https://python-telegram-bot.org/static/webappbot"))`,
  and `MessageHandler(filters.StatusUpdate.WEB_APP_DATA, ...)` with
  `json.loads(update.effective_message.web_app_data.data)`. Its docstring: "The
  static website for this website is hosted by the PTB team … Currently only
  showcases starting the WebApp via a KeyboardButton, as all other methods would
  require a bot token." The hosted page loads
  `https://telegram.org/js/telegram-web-app.js` and calls
  `Telegram.WebApp.sendData(data)` (fetched 2026-10-07). This exact pattern
  (static HTTPS page + long-polling bot) is the one this repo would use.
- **GitHub Pages hosting.** Pages is "a static site hosting service that takes
  HTML, CSS, and JavaScript files straight from a repository"
  ([What is GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)).
  Sites on `github.io` "created after June 15, 2016 … are served over HTTPS
  automatically"
  ([Securing with HTTPS](https://docs.github.com/en/pages/getting-started-with-github-pages/securing-your-github-pages-site-with-https)).
  This repo is public. Plan eligibility for Pages was not checked
  (**UNVERIFIED**, though public repos are the commonly supported case). The page
  holds no secrets, so being public is fine.
- **Native date picker.** Inside the form, `<input type="date">` would show the
  phone's own date picker. Whether Telegram's in-app webview renders it natively
  on every client is **UNVERIFIED**. It is standard browser behaviour on Android
  WebView and iOS WKWebView.
- **Security hardening (Bot API 10.2, 2026-07-14).** Telegram now disallows "the
  usage of Mini App methods from origins different from the original Mini App
  domain", enabled for all Mini Apps on 2026-07-20
  ([changelog](https://core.telegram.org/bots/api-changelog)). Keep the form
  self-contained on one origin.
- **Telegram Serverless (new, do not use here).** As of 2026-10-06 Telegram can
  host a Mini App front-end at `https://app<app_id>.tgcloud.ai/`
  ([Telegram Serverless](https://core.telegram.org/bots/serverless)). However,
  "Telegram delivers updates to your bot through a webhook, which the platform
  manages for you", and the Bot API says `getUpdates` "will not work if an
  outgoing webhook is set up"
  ([getUpdates](https://core.telegram.org/bots/api#getupdates)). Turning it on
  would likely cut off the Pi's long polling. Whether a static-files-only
  project leaves the webhook unset is **UNVERIFIED**. It is not worth the risk
  when GitHub Pages works.
- **Inline mode is a separate feature.** "Remember that inline functionality has
  to be enabled via @BotFather, or your bot will not receive inline Updates"
  ([features: Inline Requests](https://core.telegram.org/bots/features#inline-requests);
  [/setinline](https://core.telegram.org/bots/inline)). It is not needed for any
  option above.
- Assessment: this works with no server, but it adds a second deployment surface
  (the Pages site), JavaScript to maintain, and a reply keyboard to manage, all
  for one form used once a term. The button wizard (§1) gives most of the benefit
  in Python the repo already tests. Revisit only if more long forms appear.

---

## 7. Pre-filling the input field: switch_inline_query_current_chat, CopyTextButton

- `switch_inline_query_current_chat`: "pressing the button will insert the bot's
  username and the specified inline query in the current chat's input field …
  This offers a quick way for the user to open your bot in **inline mode**"
  ([InlineKeyboardButton](https://core.telegram.org/bots/api#inlinekeyboardbutton)).
  The inserted text is `@MyClubFinanceBot <query>`, which is an inline query,
  not a `/command`. If the user sends it as a message, it would not start with
  `/`, so PTB's `CommandHandler` would not match (inference). What clients do
  when inline mode is disabled is **UNVERIFIED**. **Not useful for commands.**
- **There is no Bot API option to put arbitrary text into the user's input
  field** other than the inline-query insertion above. Searched the Bot API page:
  no such parameter exists.
- **CopyTextButton**: an inline button "that copies specified text to the
  clipboard". `text` is **1–256 characters**
  ([CopyTextButton](https://core.telegram.org/bots/api#copytextbutton)). A full
  `/newterm Term 2 2027-01-12 2027-04-20 deadline=2027-01-26 comp=20 rec=25 recshirt=30 shirt=15`
  is about 95 characters, so it fits. The flow: bare `/newterm` → bot replies
  "Tap to copy, paste, edit the dates, send" with a copy button pre-filled from
  last term.
  - **PTB support: added in 21.7** ("Full Support for Bot API 7.11")
    ([PTB changelog 21.7](https://docs.python-telegram-bot.org/en/stable/changelog.html)).
    The installed **21.6 has no `CopyTextButton`** (checked locally). Using it
    means raising the floor to `python-telegram-bot[job-queue]>=21.7`. The Pi
    would pick up 22.x on a fresh install anyway.
  - Assessment: a 15-line stopgap. It still leaves the treasurer editing dates
    inside a long one-line string on a phone. The wizard is better.

---

## 8. "Same prices as last term" (design note)

- No new storage is needed. The `terms` table already keeps every term's
  `comp_fee_cents`, `rec_fee_cents`, `recshirt_fee_cents`, `shirt_fee_cents` and
  `deadline` (`clubbot/db.py`). `db.list_terms()` (ordered by `start_date, id`)
  or `db.get_latest_started_term()` gives "last term".
- Wizard step 5 shows one button with the actual numbers, "Same as last term:
  comp S$20 · rec S$25 · rec+shirt S$30 · shirt S$15", plus "Enter new prices".
  No fee is hardcoded, which keeps the "fees configurable per term" ground rule.
  The confirm screen shows the final numbers before anything is created.
- The same idea gives default date offsets: suggest end = start + (last term's
  length) and deadline = start + (last term's start-to-deadline gap) as preset
  buttons.
- With no previous term (first run), skip the button and ask for prices.

---

## 9. Command-by-command recommendation

Arguments and parsing come from `clubbot/admin.py` and `clubbot/bot.py`.
`_resolve_member` reads `context.args[0]` as a SUTD ID. All admin commands except
`/setgroup` use the `PRIVATE` filter.

| Command | Today | Who | Suggested input | Priority |
|---|---|---|---|---|
| `/newterm` | `<name…> <start> <end> deadline= comp= rec= recshirt= shirt=`, parsed by `parse_newterm_args` (`=` options + positionals; the last two positionals are the dates) | Treasurer | **Button wizard (§1)**: date presets + typed fallback, "same prices as last term", confirm. Keep the one-line form working for power use: args present → old path, no args → wizard | **High** |
| `/markpaid` | `<sutd_id>` | Treasurer | Keep the typed ID, but add a **confirm step**: "Mark Alice Tan (1010123) paid for Term 1? [Confirm] [Cancel]". Optionally add a "Mark paid" button per row in `/unpaid` (paginate; button count limit is UNVERIFIED, §1) | Medium |
| `/revoke` | `<sutd_id>` | Treasurer | Typed ID + **confirm button** (it is destructive and DMs the member) | Medium |
| `/transfertreasurer` | `<sutd_id>` | Treasurer | Typed ID + **confirm button** (it gives away the treasurer's own powers). Optional: a `KeyboardButtonRequestUsers` picker returns the chosen user's `user_id` in a `users_shared` message ([KeyboardButtonRequestUsers](https://core.telegram.org/bots/api#keyboardbuttonrequestusers), [SharedUser](https://core.telegram.org/bots/api#shareduser)). That matches "members keyed by Telegram user ID", but "the bot may not be able to use the identifier … if the corresponding chat or user is not already known" ([features: Chat and User Selection](https://core.telegram.org/bots/features#chat-and-user-selection)) | Medium |
| `/addadmin` | `<sutd_id>` | Treasurer | Typed ID + confirm; same optional user picker. Also re-set that admin's command scope (§3) | Low–medium |
| `/removeadmin` | `<sutd_id>` | Treasurer | Bare command → **list current admins as buttons** (the list is small). Remove that admin's command scope (§3) | Low–medium |
| `/relink` | none (list armed), `<sutd_id>` (arm), `<sutd_id> cancel` (disarm) | Treasurer | Bare `/relink` already lists armed relinks. Add a **"Cancel" button per row**. Keep arming by typed ID (rare) | Low |
| `/settings` | none (show), `<key> <value>` | Treasurer | Bare `/settings` shows **one button per key**, then a ForceReply asks for the value (§4). `group_posts` becomes an **On/Off toggle**. The QR dry-run check stays | Low–medium |
| `/roster` | multi-line `Name, 1010xxx` pasted after the command | Admin | **Fine as is.** The input is a list pasted from a spreadsheet. Optional: bare `/roster` replies with ForceReply "Paste the list", so the paste can be a separate message | Low |
| `/setgroup` | `rec`/`comp`, sent inside the group | Admin | Fine as is (once ever). Optional: a `KeyboardButtonRequestChat` with `bot_is_member=True` lets the treasurer pick the group from the private chat, and `bot_administrator_rights` can grant the bot rights "if appropriate" ([KeyboardButtonRequestChat](https://core.telegram.org/bots/api#keyboardbuttonrequestchat)). Not worth building | None |
| `/unpaid`, `/stats`, `/members`, `/remind` | no args | Admin / treasurer | Fine. They only benefit from the per-role `/` menu (§3) | via §3 |

---

## Limits & gotchas

- **callback_data is 1–64 bytes**
  ([InlineKeyboardButton](https://core.telegram.org/bots/api#inlinekeyboardbutton)).
  Use short prefixes (`nt:`, `mp:`) and IDs, never names.
- **Always `answerCallbackQuery`**, or the client spins
  ([CallbackQuery](https://core.telegram.org/bots/api#callbackquery)). This
  includes stale buttons after a restart: add a top-level catch-all per prefix.
- **ConversationHandler state is in memory.** A restart (`Restart=always`,
  `update.sh`) drops an in-progress wizard unless persistence is configured
  ([docs](https://docs.python-telegram-bot.org/en/stable/telegram.ext.conversationhandler.html),
  [source](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/ext/_handlers/conversationhandler.py)).
  The wizard should create nothing until the final Confirm, so losing it is
  harmless.
- **per_message=False + CallbackQueryHandler logs a PTB warning.** It is
  expected for mixed text-and-button wizards
  ([FAQ](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-Asked-Questions#what-do-the-per_-settings-in-conversationhandler-do)).
  Strip old keyboards so old messages cannot drive the current step.
- **conversation_timeout needs the JobQueue** and is unsupported with nested
  conversations (docs). The repo has `[job-queue]`.
- **Command scopes are cosmetic.** Keep the role checks
  ([features: Command Scopes](https://core.telegram.org/bots/features#command-scopes)).
  Per-admin scopes must be re-set or deleted when roles change
  ([deleteMyCommands](https://core.telegram.org/bots/api#deletemycommands)).
  Use `BotCommandScopeChat` for private menus. `BotCommandScopeChatMember` is
  for groups only
  ([precedence](https://core.telegram.org/bots/api#determining-list-of-commands)).
- **setMyCommands**: at most 100 commands, command 1–32 characters
  `[a-z0-9_]`, description 1–256 characters
  ([setMyCommands](https://core.telegram.org/bots/api#setmycommands),
  [BotCommand](https://core.telegram.org/bots/api#botcommand)).
- **No native date picker** in Bot API ≤ 10.3. The `date_time` entity is for
  display only ([changelog](https://core.telegram.org/bots/api-changelog)).
- **`sendData`** works only from a reply-keyboard `web_app` button, in private
  chats, with at most 4096 bytes, and the payload is untrusted
  ([webapps](https://core.telegram.org/bots/webapps#keyboard-button-mini-apps),
  [WebAppData](https://core.telegram.org/bots/api#webappdata)). Menu-button and
  inline-button Mini Apps cannot use it.
- **Mini App URLs must be HTTPS**
  ([WebAppInfo](https://core.telegram.org/bots/api#webappinfo)). GitHub Pages on
  `github.io` serves HTTPS automatically
  ([GitHub docs](https://docs.github.com/en/pages/getting-started-with-github-pages/securing-your-github-pages-site-with-https)).
- **Any webhook breaks long polling**
  ([getUpdates](https://core.telegram.org/bots/api#getupdates)). Avoid
  Telegram Serverless, which manages a webhook
  ([serverless](https://core.telegram.org/bots/serverless)).
- **CopyTextButton needs PTB ≥ 21.7**; 21.6 is installed locally
  ([PTB changelog](https://docs.python-telegram-bot.org/en/stable/changelog.html)).
  The text is at most 256 characters
  ([CopyTextButton](https://core.telegram.org/bots/api#copytextbutton)).
- **Unbounded PTB pin.** `>=21.0` means the Pi installs 22.x while tests may run
  on 21.6. Consider pinning a range such as `>=22,<23` and testing against it
  before relying on newer Bot API fields (`style`, `disabled`). **UNVERIFIED**:
  which Bot API version PTB 22.8 fully covers. Its changelog shows full support
  through at least Bot API 10.0.
- **Reply keyboards stay until replaced or removed**
  ([ReplyKeyboardRemove](https://core.telegram.org/bots/api#replykeyboardremove)).
  If one is used (Mini App launcher, user/chat picker), send
  `ReplyKeyboardRemove` when done, as `conversationbot.py` does.
- `KeyboardButtonRequestUsers` / `RequestChat` work in private chats only, and
  the bot may not be able to use an ID for a user or chat it does not already
  know ([KeyboardButton](https://core.telegram.org/bots/api#keyboardbutton),
  [features](https://core.telegram.org/bots/features#chat-and-user-selection)).

# Removing unpaid members from the recreational group chat

Research date: 2026-10-10. Scope: how a Telegram bot can automatically remove
people who never paid the term fee from the **recreational** supergroup
(~100–200 people) after a grace period, instead of creating a fresh group
every term. What the Bot API allows, what it does not, and how paid-community
bots handle it. No code was changed.

Sources: the Telegram Bot API page (current version **Bot API 10.3**,
2026-08-24, [changelog](https://core.telegram.org/bots/api-changelog)), the Bot
FAQ, the bot features and bots intro pages, Telegram's MTProto API docs, the
python-telegram-bot (PTB) source and examples, the Telethon source/docs, and
the official help centres of InviteMember, LaunchPass, BotSubscription and
Tribute. Every claim is cited. Anything marked **UNVERIFIED** was not
confirmed in a primary source.

Repo facts used below: `requirements.txt` pins
`python-telegram-bot[job-queue]>=22.8,<23`. `clubbot/__main__.py` calls
`app.run_polling()` with no `allowed_updates`. `clubbot/bot.py`
(`_in_club_group`) already calls `get_chat_member` on the club groups for the
registration gate, and `/setgroup` (`clubbot/admin.py`) already requires the
bot to be a group admin. Members are keyed by Telegram user ID.

---

## Verdict / what this means for our bot

In plain English:

1. **The bot cannot ask Telegram "who is in this group?".** The normal bot
   interface (Bot API) only gives the member *count*, the *admin list*, and a
   yes/no check for *one person whose ID the bot already knows* (§1). So the bot
   can only remove people it has a Telegram ID for.
2. **Who the bot already knows:** everyone who registered with the bot (our
   database). From the day we switch it on, the bot can also log **every join
   and leave** in the group (`chat_member` updates, §2), and, as a group admin,
   it sees every message, so it learns the ID of anyone who posts (§2).
3. **The gap is the "silent lurkers"**: people who joined before the bot was
   watching, never talked, and never opened the bot. The Bot API cannot find
   them. The only way to list them automatically is a one-off script using
   Telegram's *full* API (MTProto, e.g. the Telethon library). It can log in
   **with our existing bot token**, but it needs an `api_id`/`api_hash` that the
   treasurer creates once at my.telegram.org with their phone number (§1).
   The low-tech alternative is to remove lurkers by hand once, in the Telegram
   app, and from then on rely on (2).
4. **Removing someone is easy and allowed.** The bot needs the admin right
   "Ban users" (`can_restrict_members`). A "kick" (out, but free to rejoin later)
   is a ban immediately followed by an unban (§3). No admin or owner should be
   targeted: skip everyone on the admin list.
5. **Warnings:** the bot **cannot DM someone who never pressed Start** on it
   (§5). It can post a warning **in the group** that tags the unpaid people by
   name. One group message per warning, not one per person, because groups
   allow only 20 bot messages a minute (§3).
6. **Getting back in after paying:** give out an invite link that creates a
   *join request*. The bot approves requests from people who have paid. For
   anyone else, it gets a 5-minute window to DM them "register and pay first",
   even if they never started the bot (§4). This also stops new unpaid people
   from slipping in.
7. **What the paid-community bots do:** the same pieces. They use
   per-person or join-request links, automatic removal at expiry, reminders,
   an optional grace period, and "admins/whitelisted people are never removed"
   (§6). The vendors document different choices between kick and ban (§6).
8. **Telegram's built-in paid subscriptions do not fit.** They work only for
   *channels* (not groups), only in Telegram Stars, and only for a fixed 30-day
   period (§7). Our fee is per term, in S$, by PayNow to DBS FLYMAX.

**Suggested shape (a recommendation, not a primary-source fact):** at
deadline + N days (the treasurer picks N), post one group message listing the
unpaid people who are still in the group. A few days later, kick those still
unpaid, with a pause of about 1 second between kicks. Never touch admins.
Switch the group's invite link to a join-request link the bot approves for
paid members. Do one manual or MTProto sweep at the start to catch lurkers.

---

## 1. Can a bot list all members of a group?

### What the Bot API offers

- `getChatMemberCount`: "Use this method to get the number of members in a
  chat. Returns Integer on success." It returns a number only, no IDs
  ([getChatMemberCount](https://core.telegram.org/bots/api#getchatmembercount)).
- `getChatAdministrators`: "Use this method to get a list of administrators in a
  chat. Returns an Array of ChatMember objects." Since Bot API 10.0 it takes
  `return_bots`: "By default, bots other than the current bot are omitted"
  ([getChatAdministrators](https://core.telegram.org/bots/api#getchatadministrators);
  [changelog 10.0](https://core.telegram.org/bots/api-changelog)).
- `getChatMember(chat_id, user_id)`: "Use this method to get information about a
  member of a chat. The method is only guaranteed to work for other users if the
  bot is an administrator in the chat"
  ([getChatMember](https://core.telegram.org/bots/api#getchatmember)). It needs a
  `user_id` the bot already has.
- The result has one of 6 statuses: owner (`creator`), `administrator`,
  `member`, `restricted`, `left`, or `kicked` (= banned)
  ([ChatMember](https://core.telegram.org/bots/api#chatmember),
  [ChatMemberLeft](https://core.telegram.org/bots/api#chatmemberleft),
  [ChatMemberBanned](https://core.telegram.org/bots/api#chatmemberbanned)). For
  `restricted`, `is_member` is "True, if the user is a member of the chat at the
  moment of the request"
  ([ChatMemberRestricted](https://core.telegram.org/bots/api#chatmemberrestricted)).
  `clubbot/bot.py` already handles this case.
- **There is no "list all members" method in the Bot API.** The method list
  has only the three above (searched the Bot API page). This is an inference
  from the method list. The Bot API itself has no sentence saying "you cannot
  list members".
- Group-admin tools came in Bot API 2.1 as "get a list of administrators and
  members count in a group, check a user's current status" — again, no member
  list ([changelog 2.1](https://core.telegram.org/bots/api-changelog)).

### How a Bot API bot learns IDs of people already in the group

All of these only see people from the moment the bot is listening:

- **`chat_member` updates** (joins, leaves, kicks; §2).
- **Group messages.** A bot that is an admin receives every message (§2), and
  each message carries the sender's `from` user.
- **People who talk to the bot**: registration, `/start`, button taps. This is
  already our database.
- **Join service messages** (`new_chat_members` / `left_chat_member` on
  `Message`) exist
  ([Message](https://core.telegram.org/bots/api#message)). Telegram warned in
  2021 that "Service messages about non-bot users joining the chat will be soon
  removed from large groups. We recommend using the “chat_member” update as a
  replacement" ([changelog, Bot API 5.2 notes](https://core.telegram.org/bots/api-changelog)).
  Whether this applies to a ~200-person group is **UNVERIFIED**. Use
  `chat_member` instead.

Nothing in the Bot API reveals a member who joined earlier, never posted, and
never contacted the bot.

### The MTProto route (Telethon `get_participants`)

- Telegram's full client API has `channels.getParticipants` (a supergroup is a
  "channel" at this level). Its page says **"Both users and bots can use this
  method"**, and lists `CHAT_ADMIN_REQUIRED` — "You must be an admin in this
  chat to do this" — as a possible error
  ([channels.getParticipants](https://core.telegram.org/method/channels.getParticipants)).
  So a **bot account** can call it over MTProto. A personal "userbot" account
  is not required.
- Requirement: an `api_id` and `api_hash`. To get them: "Log in to your
  Telegram core: https://my.telegram.org. Go to 'API development tools' and
  fill out the form … For the moment each number can only have one api_id
  connected to it"
  ([Obtaining api_id](https://core.telegram.org/api/obtaining_api_id)). The
  same page warns: "all accounts that log in using unofficial Telegram API
  clients are automatically put under observation". Logging in with the bot
  token rather than the treasurer's own account avoids putting a personal
  account at risk. That is an inference: the warning is about user accounts.
- Telethon logs in with a bot token like this:
  `TelegramClient('bot', api_id, api_hash).start(bot_token=bot_token)`
  ([Telethon signing-in docs](https://github.com/LonamiWebs/Telethon/blob/v1/readthedocs/basic/signing-in.rst)).
  Then `client.iter_participants(chat)` lists the members ("Show all user IDs
  in a chat") in chunks of 200 (`_MAX_PARTICIPANTS_CHUNK_SIZE = 200`)
  ([Telethon `chats.py`](https://github.com/LonamiWebs/Telethon/blob/v1/telethon/client/chats.py)).
  Telethon's `aggressive` flag "Does nothing … There have been several changes
  to Telegram's API that limits the amount of members that can be retrieved"
  (same source). The size cap is **UNVERIFIED**. ~10,000 is commonly cited,
  far above our ~200.
- **UNVERIFIED:** that a *bot* account gets the *complete* list (not only
  "recent" participants) from `channels.getParticipants`. Test it once on the
  real group and compare with `getChatMemberCount`.
- **Hidden members.** Supergroup admins can hide the member list with
  `channels.toggleParticipantsHidden`. This needs a minimum group size set by
  a client config value
  ([toggleParticipantsHidden](https://core.telegram.org/method/channels.toggleParticipantsHidden)).
  The admin right `can_manage_chat` lets an admin "see hidden supergroup and
  channel members" and is "Implied by any other administrator privilege"
  ([ChatAdministratorRights](https://core.telegram.org/bots/api#chatadministratorrights)).
  So an admin bot should still see them (inference).
- Practical fit: this would be a **one-off script** (e.g.
  `scripts/list_rec_members.py`) that prints or stores the IDs. It is not a new
  part of the running bot. It adds the `telethon` dependency and two new
  secrets (`api_id`, `api_hash`) for `.env`. Running it while the main bot
  long-polls should be fine: MTProto sessions do not consume Bot API
  `getUpdates`. This is **UNVERIFIED**; stop the service while running it to
  be safe.

---

## 2. `chat_member` updates and privacy mode

### Requirements

- `Update.chat_member`: "A chat member's status was updated in a chat. **The bot
  must be an administrator in the chat and must explicitly specify
  "chat_member" in the list of allowed_updates** to receive these updates"
  ([Update](https://core.telegram.org/bots/api#update)).
- `getUpdates.allowed_updates`: "Specify an empty list to receive all update
  types except chat_member, message_reaction, and message_reaction_count
  (default). **If not specified, the previous setting will be used.**"
  ([getUpdates](https://core.telegram.org/bots/api#getupdates)). Today our
  `run_polling()` passes nothing, so `chat_member` is **off**.
- `Update.my_chat_member` is about the bot's own status, e.g. it being made admin
  or removed. Such updates are delivered by default ("By default, only
  my_chat_member updates about the bot itself are received" —
  [changelog 5.1](https://core.telegram.org/bots/api-changelog)).

### What the update contains

[ChatMemberUpdated](https://core.telegram.org/bots/api#chatmemberupdated) has:

- `chat`, `from` ("Performer of the action, which resulted in the change"),
  `date`, `old_chat_member` and `new_chat_member`.
- `invite_link`: "Chat invite link, which was used by the user to join the
  chat; for joining by invite link events only".
- `via_join_request`: "True, if the user joined the chat after sending a direct
  join request without using an invite link and being approved by an
  administrator".
- `via_chat_folder_invite_link`.

It fires on any status change: join (including via a link), leave, kick/ban,
promote, restrict. The bot tells them apart by comparing old and new status
(`left`/`kicked` ↔ `member`/`administrator`/…). PTB's example does exactly
this in `extract_status_change`
([chatmemberbot.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/chatmemberbot.py)).

### Enabling it in python-telegram-bot

- `ChatMemberHandler(callback, ChatMemberHandler.CHAT_MEMBER)` handles
  `Update.chat_member`. The default `MY_CHAT_MEMBER` handles only the bot's own
  status, and `ANY_CHAT_MEMBER` handles both. An optional `chat_id=` filter
  limits it to given chats
  ([chatmemberhandler.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/ext/_handlers/chatmemberhandler.py)).
- `application.run_polling(allowed_updates=Update.ALL_TYPES)`. The example's
  comment says: "We pass 'allowed_updates' handle *all* updates including
  `chat_member` updates"
  ([chatmemberbot.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/chatmemberbot.py)).
  `Update.ALL_TYPES` is "A list of all available update types"
  ([`_update.py`](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/_update.py)).
  `run_polling`'s `allowed_updates` defaults to `None` and is "Passed to
  `telegram.Bot.get_updates`"
  ([`_application.py`](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/ext/_application.py)).
  A narrower explicit list (`["message", "edited_message", "callback_query",
  "chat_member", "my_chat_member", "chat_join_request"]`) also works. Include
  every type the bot already handles.

### Do admin bots see all group messages?

Yes:

- "Privacy mode is enabled by default for all bots, except bots that were added
  to a group as admins (**bot admins always receive all messages**)"
  ([features: Privacy Mode](https://core.telegram.org/bots/features#privacy-mode)).
- Bot FAQ: "Bot admins and bots with privacy mode disabled will receive all
  messages except messages sent by other bots"
  ([FAQ: What messages will my bot get?](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get)).

Our bot must already be an admin in the groups for `/setgroup`.

---

## 3. Kicking: `banChatMember` + `unbanChatMember`

- `banChatMember`: "Use this method to ban a user in a group, a supergroup or a
  channel. In the case of supergroups and channels, the user will not be able to
  return to the chat on their own using invite links, etc., unless unbanned
  first. The bot must be an administrator in the chat for this to work and must
  have the appropriate administrator rights"
  ([banChatMember](https://core.telegram.org/bots/api#banchatmember)).
  - `until_date`: "Date when the user will be unbanned; Unix time. If user is
    banned for **more than 366 days or less than 30 seconds** from the current
    time they are considered to be banned forever. Applied for supergroups and
    channels only."
  - `revoke_messages`: "Pass True to delete all messages from the chat for the
    user that is being removed. If False, the user will be able to see messages
    in the group that were sent before the user was removed. **Always True for
    supergroups and channels.**" Read in context, this controls whether the
    *removed user* keeps a copy of the history. It does not delete their posts
    for everyone else. That reading is **UNVERIFIED**; check on a test group
    that a kicked person's old messages stay visible.
- `unbanChatMember`: "The user will not return to the group or channel
  automatically, but will be able to join via link, etc. … **By default, this
  method guarantees that after the call the user is not a member of the chat,
  but will be able to join it. So if the user is a member of the chat they will
  also be removed from the chat.** If you don't want this, use the parameter
  only_if_banned." `only_if_banned`: "Do nothing if the user is not banned"
  ([unbanChatMember](https://core.telegram.org/bots/api#unbanchatmember)).
  `only_if_banned` was added in Bot API 5.1 "to allow safe unban"
  ([changelog](https://core.telegram.org/bots/api-changelog)).
- **What a "kick" is.** Telegram has no separate kick method. The old name
  `kickChatMember` was renamed to `banChatMember` in Bot API 5.3
  ([changelog](https://core.telegram.org/bots/api-changelog)). Two ways to
  kick, both inferences from the docs above:
  1. `banChatMember(chat, user)` then `unbanChatMember(chat, user,
     only_if_banned=True)`. The user is out and can rejoin later through a link.
  2. `banChatMember(chat, user, until_date=now + 60s)`. This is a temporary ban
     that lifts itself. Anything under 30 s counts as "forever", so stay above
     that.

  Option 1 is clearer, and the person can rejoin straight away once they pay.
  A plain `unbanChatMember` without `only_if_banned` also removes a current
  member (quote above), so that one call alone works as a kick. It is less
  obvious to read.
- **Admin right needed:** `can_restrict_members` — "True, if the administrator
  can restrict, ban or unban chat members, or access supergroup statistics"
  ([ChatAdministratorRights](https://core.telegram.org/bots/api#chatadministratorrights)).
  In the Telegram app this is "Ban users". LaunchPass's docs confirm the same
  two rights for a paywall bot: "Ban Users … Add New Members (may appear as
  'Invite Users via Link')"
  ([LaunchPass](https://help.launchpass.com/en/articles/7963095-getting-started-with-telegram)).
- **Admins and owner.** The Bot API does not say whether a bot can ban an admin
  or the owner. The MTProto `channels.editBanned` lists `USER_ADMIN_INVALID`
  as a possible error
  ([channels.editBanned](https://core.telegram.org/method/channels.editBanned)).
  `ChatMemberAdministrator.can_be_edited` is "True, if the bot is allowed to edit
  administrator privileges of that user"
  ([ChatMemberAdministrator](https://core.telegram.org/bots/api#chatmemberadministrator)).
  That a bot cannot ban the owner or admins it did not promote is **UNVERIFIED**
  but widely reported. Either way, **skip everyone returned by
  `getChatAdministrators`**. InviteMember does the same: "InviteMember does not
  remove admins" (§6).
- **Rate limits.** The Bot FAQ documents *message* limits only:
  - "In a single chat, avoid sending more than one message per second."
  - "In a group, bots are not be able to send more than **20 messages per
    minute**."
  - "For bulk notifications, bots are not able to broadcast more than about
    **30 messages per second**". Going over gives "429 errors".

  ([FAQ: My bot is hitting limits](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this))

  A limit for *ban/unban calls* is **UNVERIFIED** (not documented). For ~50
  kicks, pause ~1 s between them and honour PTB's `RetryAfter` ("Raised when
  flood limits where exceeded",
  [error.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/error.py)).
  PTB also has an optional `AIORateLimiter` (extra `[rate-limiter]`). Its
  defaults are the overall-per-second and per-group-per-minute flood limits
  ([`_aioratelimiter.py`](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/ext/_aioratelimiter.py)).
  It is not needed for this volume.

---

## 4. Join requests (gatekeeping who comes back in)

- `createChatInviteLink(chat_id, name?, expire_date?, member_limit?,
  creates_join_request?)`
  ([createChatInviteLink](https://core.telegram.org/bots/api#createchatinvitelink)):
  - `name` is 0–32 characters.
  - `expire_date` is the "Point in time (Unix timestamp) when the link will
    expire".
  - `member_limit` is "The maximum number of users that can be members of the
    chat simultaneously after joining the chat via this invite link; 1-99999".
  - `creates_join_request` means "True, if users joining the chat via the link
    need to be approved by chat administrators. **If True, member_limit can't
    be specified.**"
  - It requires admin with "appropriate administrator rights". Revoke with
    `revokeChatInviteLink`.
  - A per-person one-use link is `member_limit=1`. A shared gatekept link is
    `creates_join_request=True`. The two cannot be combined.
- `Update.chat_join_request`: "A request to join the chat has been sent. The bot
  must have the **can_invite_users** administrator right in the chat to receive
  these updates" ([Update](https://core.telegram.org/bots/api#update)). This
  update type is on by default (it is not in the excluded list of
  `allowed_updates` above). PTB handles it with `ChatJoinRequestHandler`,
  optionally filtered by `chat_id`
  ([chatjoinrequesthandler.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/ext/_handlers/chatjoinrequesthandler.py)).
- `approveChatJoinRequest` / `declineChatJoinRequest(chat_id, user_id)`: "The bot
  must be an administrator in the chat for this to work and must have the
  can_invite_users administrator right"
  ([approveChatJoinRequest](https://core.telegram.org/bots/api#approvechatjoinrequest),
  [declineChatJoinRequest](https://core.telegram.org/bots/api#declinechatjoinrequest)).
- **The 5-minute DM window.** `ChatJoinRequest.user_chat_id`: "Identifier of a
  private chat with the user who sent the join request … The bot can use this
  identifier for **5 minutes to send messages until the join request is
  processed**, assuming no other administrator contacted the user"
  ([ChatJoinRequest](https://core.telegram.org/bots/api#chatjoinrequest); added in
  Bot API 6.5). This is the one documented way to message someone who never
  started the bot. Use it to say "Register and pay with @MyClubFinanceBot first,
  then request again." Then decline, or leave the request pending. How long
  pending requests last is **UNVERIFIED**.
- Bot admins receive one update per join request, and admins can approve or
  dismiss in bulk on the client side
  ([MTProto: invites / join requests](https://core.telegram.org/api/invites)).
- **New in Bot API 10.1 (2026-06-11): "Join Request Queries".** A chat can have
  a `guard_bot`, "The bot that processes join request queries in the chat".
  Such a bot must answer within 10 seconds with `answerChatJoinRequestQuery`
  (`approve` / `decline` / `queue`) or `sendChatJoinRequestWebApp`
  ([ChatJoinRequest.query_id](https://core.telegram.org/bots/api#chatjoinrequest),
  [answerChatJoinRequestQuery](https://core.telegram.org/bots/api#answerchatjoinrequestquery),
  [changelog 10.1](https://core.telegram.org/bots/api-changelog)). How a bot is
  assigned as guard bot is **UNVERIFIED**. The Bot API only exposes
  `supports_join_request_queries`, "Returned only in getMe". The ordinary
  approve/decline flow above is enough. Not needed.
- The Bot API has **no method to add a user to a group directly** (no
  `addChatMember` exists on the Bot API page). BotSubscription's FAQ says the
  same: "Telegram doesn't allow bots to add users directly. The bot generates
  an invite link" ([BotSubscription FAQ](https://docs.botsubscription.com/FAQ/)).

---

## 5. Can the bot DM someone who never started it?

- No. "Bots can't start conversations with users. A user must either add them to
  a group or send them a message first"
  ([Telegram bots intro](https://core.telegram.org/bots#how-are-bots-different-from-users)).
- The attempt fails with a 403 error, which PTB raises as
  `telegram.error.Forbidden`: "Raised when the bot has not enough rights to
  perform the requested action"
  ([error.py](https://github.com/python-telegram-bot/python-telegram-bot/blob/master/src/telegram/error.py)).
  The exact server text, commonly "Forbidden: bot can't initiate conversation
  with a user", is **UNVERIFIED** (not on an official page).
  `clubbot/scheduler.py` already catches `Forbidden`.
- Exceptions:
  - The 5-minute `user_chat_id` window after a join request (§4).
  - The bot *can* tag a group member in a group message. A `tg://user?id=`
    link works inside an inline link. "Unless the user is a member of the chat
    where they were mentioned, these mentions are only guaranteed to work if
    the user has contacted the bot in private". So tagging *members of that
    group* works ([Formatting options](https://core.telegram.org/bots/api#formatting-options)).
    PTB's `mention_html()` builds such links (inference). The maximum number of
    mentions per message is **UNVERIFIED**. Message text is limited to 4096
    characters ([sendMessage](https://core.telegram.org/bots/api#sendmessage)).

---

## 6. How paid-community bots handle it

### InviteMember

Source: [How the Telegram Integration Works](https://www.invitemember.com/help/telegram-integration-details).

- **Joining:** "Each customer receives a **unique join request link**, which
  InviteMember monitors in real-time. If someone else tries to use a link, it
  will be revoked, and the unauthorized user will be removed immediately."
  Join-request links have been used since 2024-10-24.
- **Unban before rejoining:** "We also unban users before showing them join
  request links (in case they were previously banned). Without unbanning, the
  join request link would result in an error message from Telegram."
- **Removal triggers:** "Their subscription or free trial has ended. A
  recurring subscription has been canceled. The user has joined your group.
  InviteMember runs a periodic check. The system checks all users, including
  those who have not made payments."
  - How it finds users it has never seen is **not stated**. It is presumably
    the users it knows from join events and its bot (**UNVERIFIED**).
- **Kick, not ban:** "Starting October 24, 2024, InviteMember **no longer bans
  users—it simply removes them** from the group. Since users cannot rejoin
  without approval, banning is no longer necessary. Revoked join request links
  prevent removed users from re-entering without making a payment."
- **Exemptions:** "Whitelisted members and administrators are never removed …
  Make the user an administrator (InviteMember does not remove admins)."
  Whitelisting is `/whitelist USER_ID`
  ([How to Whitelist a User](https://www.invitemember.com/help/how-to-whitelist-a-user)).
- **Warnings:** members "Receive renewal reminders and removal
  notifications" through the bot (same page).
- **Rights:** connecting a group asks to add InviteMember "as an admin of your
  group with permission to add and remove users"
  ([How to Connect a Telegram Group](https://www.invitemember.com/help/how-to-add-a-telegram-paid-group)).

### LaunchPass

Source: [Getting Started with Telegram](https://help.launchpass.com/en/articles/7963095-getting-started-with-telegram), dated 2026-05-18.

- **Members from before the bot:** "If you already have a Telegram group or
  channel with members in it, that works too. **Existing members stay** when you
  connect LaunchPass. They won't be removed unless they cancel a subscription."
  That is, LaunchPass does not chase pre-existing members.
- **Grace period:** "When this is on, LaunchPass removes members who cancel or
  miss a payment … You can **set a grace period** if you want to give people a
  few extra days before removal." Also: "If you set a grace period, removal
  happens after the grace period, not immediately."
- **Ban, not kick:** "When the bot removes someone, Telegram treats that as a
  ban … The ban stays in place so cancelled members can't rejoin through a
  shared invite link." A returning member must be manually unbanned (Removed
  Users list). Note: the Bot API allows ban-then-unban (§3), so "Telegram
  treats that as a ban" describes LaunchPass's choice, not a Telegram limit.
- **Links:** "LaunchPass generates a **unique invite link for each paying
  subscriber**."
- **Rights:** "Ban Users so it can remove members who cancel or miss a payment.
  Add New Members … so it can generate invite links." The group should be
  private.

### BotSubscription

Source: [FAQ](https://docs.botsubscription.com/FAQ/), [Subscription Lifecycle](https://docs.botsubscription.com/subscriptions/lifecycle/).

- Uses "unique, single-use invite links. After a member uses a link, that link
  becomes invalid". Join requests can be switched to manual admin approval.
- On cancellation, "The user stays in the group until their paid time runs
  out". "Termination" kicks immediately.
- Rights: "Invite Users via Link … and Ban Users".
- "A failed renewal does not always revoke access immediately". Renewal
  reminders are sent before expiry. At expiry, the member is "removed from the
  Telegram channel".

### Tribute

Source: [Tribute FAQ](https://wiki.tribute.tg/faq).

- "The bot automatically removes subscribers from the channel if their
  subscription is not renewed. The bot also reminds subscribers if their
  subscription is about to expire and offers to renew it."
- Grace period, pre-existing members, and kick vs ban: not documented there.

### Discord equivalents (Patreon, Discord Server Subscriptions)

Patreon's help centre returns HTTP 403 to automated fetches. Search snippets
say Patreon removes the Discord role when "a member deletes their pledge or
their payment declines", and retries declined payments during the month
([Patreon: Setting up Discord](https://support.patreon.com/hc/en-us/articles/213552323-Setting-up-Discord-for-your-members)).
For Discord Server Subscriptions bought through the iOS App Store, snippets
mention a 28-day grace period
([Discord: Past Due](https://support.discord.com/hc/en-us/articles/23082866222871-Why-is-My-Subscription-Showing-as-Past-Due)).
All of this is **UNVERIFIED** (page text not read). Discord removes a *role*
rather than the person. Telegram has no roles, so removal is the analogue.

### Common pattern

The documented tools have these in common:

- per-person or join-request links;
- automatic removal at expiry, after an optional grace period;
- reminders before removal, sent by bot DM (their members have all started
  the bot, because they paid through it);
- admins and whitelisted people exempt.

Two points differ between them:

- **Members from before the bot:** LaunchPass leaves them alone. InviteMember
  "checks all users", but how is undocumented.
- **Kick or ban:** InviteMember kicks. LaunchPass bans.

**None of the docs describes enumerating silent pre-existing members.** That
matches the Bot API limit in §1.

---

## 7. Telegram-native paid subscriptions (Stars)

- `createChatSubscriptionInviteLink`: "Use this method to create a subscription
  invite link for a **channel chat**. The bot must have the can_invite_users
  administrator rights." `chat_id` is the "Unique identifier for the target
  **channel chat**". `subscription_period`: "Currently, it **must always be
  2592000 (30 days)**". `subscription_price`: "The amount of **Telegram Stars**
  a user must pay initially and after each subsequent subscription period to be
  a member of the chat; 1-10000"
  ([createChatSubscriptionInviteLink](https://core.telegram.org/bots/api#createchatsubscriptioninvitelink);
  added in Bot API 7.9, 2024-08-14,
  [changelog](https://core.telegram.org/bots/api-changelog)).
- MTProto docs say the same: "Channel administrators can create special invite
  links that allow joining a channel in exchange for a monthly payment in
  Telegram Stars … passing in peer the **private channel**". Also: "Currently
  the only allowed subscription period is 30*24*60*60"
  ([Star subscriptions](https://core.telegram.org/api/subscriptions)).
- `ChatMemberMember.until_date`: "Date when the user's subscription will
  expire" ([ChatMemberMember](https://core.telegram.org/bots/api#chatmembermember)).
- Bot API 10.2 added `Update.subscription` (`BotSubscriptionUpdated`: "canceled",
  "active", "failed"). It concerns "a user payment subscription toward the
  current bot", which is a Stars bot subscription, not a group
  ([BotSubscriptionUpdated](https://core.telegram.org/bots/api#botsubscriptionupdated)).
- **Fit: no.**
  - It is for channels only. Our recreational chat is a group where members
    talk.
  - It is paid in Stars, which do not land in DBS FLYMAX.
  - The period is a fixed 30 days, not a term.
  - It would replace the PayNow + screenshot verification the club relies on.

---

## Limits & gotchas

- **No member list in the Bot API.** Only count, admins, and per-ID lookup
  ([getChatMemberCount](https://core.telegram.org/bots/api#getchatmembercount),
  [getChatAdministrators](https://core.telegram.org/bots/api#getchatadministrators),
  [getChatMember](https://core.telegram.org/bots/api#getchatmember)).
- **`chat_member` is off until asked for.** Pass it in `allowed_updates`. The
  setting persists server-side once sent ("If not specified, the previous
  setting will be used",
  [getUpdates](https://core.telegram.org/bots/api#getupdates)).
- **Removal = ban (+ unban to allow return).** `until_date` under 30 s or over
  366 days means forever
  ([banChatMember](https://core.telegram.org/bots/api#banchatmember)). Use
  `only_if_banned=True` when only lifting a ban
  ([unbanChatMember](https://core.telegram.org/bots/api#unbanchatmember)).
- **Rights:** `can_restrict_members` to remove, `can_invite_users` for links and
  join requests
  ([ChatAdministratorRights](https://core.telegram.org/bots/api#chatadministratorrights)).
- **Skip admins and the owner.** Whether a bot can ban them is **UNVERIFIED**.
  Do not try.
- **Group message limit: 20/minute.** Send one warning message listing names,
  not one per person
  ([FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this)).
  The ban-call rate limit is **UNVERIFIED**. Pace ~1/s and handle `RetryAfter`.
- **No DMs to people who never started the bot**
  ([bots intro](https://core.telegram.org/bots#how-are-bots-different-from-users)).
  The only exception is the 5-minute join-request window
  ([ChatJoinRequest](https://core.telegram.org/bots/api#chatjoinrequest)).
- **`creates_join_request` and `member_limit` cannot be combined**
  ([createChatInviteLink](https://core.telegram.org/bots/api#createchatinvitelink)).
- **Lurkers need MTProto or manual removal.** `channels.getParticipants` works
  for bots that are admins
  ([method page](https://core.telegram.org/method/channels.getParticipants)). It
  needs an `api_id`/`api_hash` from my.telegram.org
  ([Obtaining api_id](https://core.telegram.org/api/obtaining_api_id)). Whether
  bots get the *complete* list is **UNVERIFIED**; test once.
- **The old invite link stays valid.** Anyone holding the group's existing
  link can walk back in after a kick. Revoke the old links (`revokeChatInviteLink`,
  or in the app) and move to a join-request link, or kicks will not stick. This
  is an inference from §3–§4. It matches InviteMember's "Revoked join request
  links prevent removed users from re-entering"
  ([InviteMember](https://www.invitemember.com/help/telegram-integration-details)).
- **Competitive group is out of scope.** The same calls would work there. Keep a
  per-group switch so only the `rec` group (`/setgroup rec`) is swept.

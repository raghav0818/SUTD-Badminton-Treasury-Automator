# Plan: remove unpaid members from the rec group chat

Date: 2026-10-10 · Status: **approved 2026-10-10 and built on branch `removing-unpaid-members` (not yet live-tested)**
Research: `docs/research/2026-10-10-removing-unpaid-members.md` (every
Telegram fact cited to the official docs).

## The problem

Every term the rec group chat fills up with people who never pay. Removing
them by hand is so painful that the club makes a new group chat every term.
Goal: **keep one rec group forever**, and have the bot remove people who
haven't paid, after a buffer, with no work from the treasurer.

**Scope: the rec chat "SUTD Badminton 26/27" ONLY.** The competitive chat
"Chun Mun Fan Club" is never touched. Every new piece of code acts only on
the chat saved by `/setgroup rec`; events from any other chat are ignored.

## What Telegram allows (short version, see research)

- A bot **cannot list a group's members**. It can only check one person whose
  Telegram ID it already knows. So the bot can only remove people it knows.
- From now on the bot can **see every join** (`chat_member` updates; needs
  one-line change to how the bot polls) and every **join request**.
- **People already in the chat** who never registered with the bot are
  invisible to the normal bot interface. A **one-off script** using Telegram's
  full API (Telethon, logging in with our bot token) can list them once.
- "Kick" = ban then unban immediately: they're out but *can* come back later.
- A bot **can't DM someone who never pressed Start**, except for 5 minutes
  after that person asks to join a group the bot manages (join request).
- Paid-community bots (InviteMember, LaunchPass, Tribute) all do the same
  thing: a join-request link, automatic removal after a grace period,
  reminders first, admins/whitelisted people never removed.

## How it works (one term, example dates)

Term 1: start 1 Sep, **deadline 15 Sep**, grace 7 days → **removal day 22 Sep**.

| When | What happens |
|---|---|
| Any time someone asks to join the rec chat | Bot DMs them (5-min window) "Welcome! Membership S$25 — pay by <their date> or you'll be removed: tap here", then **approves**. Exception: someone whose date already passed and who still hasn't paid is **declined** with "Pay first (tap here), then ask to join again." |
| 1 Sep → 15 Sep | Existing reminders (d3/d7/d10/d13/last call) and Mon/Thu group posts — now with one extra line: "Unpaid members are removed from the rec chat on 22 Sep." |
| 20 Sep (removal day − 2), 10:00 | Treasurer gets a **preview**: who will be removed if still unpaid, with a **Keep** button per person, plus "chat has 183 members, I can identify 183". Each unpaid *registered* person in the rec chat gets a final-warning DM. |
| 22 Sep, 10:00 | **Sweep**: everyone still unpaid is kicked (1 s apart). Registered ones get a DM: "Removed because Term 1 is unpaid. Pay with /pay, then rejoin with <join link>." Treasurer gets a one-message summary. |
| Every day after, 10:00, until term end | Same sweep, catches **late joiners** whose own date has passed. |
| Between terms | Nothing is removed; all join requests approved. |

### Who stays in the rec chat (the rule)

During an open term, a person in the rec chat is removed once **their date**
has passed, unless **any** of these is true:

1. They have a **verified** payment this term — rec *or* competitive (the S$20
   competitive fee covers rec too).
2. They have a receipt **still being checked** (pending / waiting for
   treasurer review). A Gemini outage never gets anyone kicked.
3. They're the treasurer or a bot admin, or a Telegram admin/owner of the chat.
4. The treasurer tapped **Keep** for them (coaches, alumni helpers). Keep is
   permanent until undone in `/keep`.
5. It's a bot account.

**Their date** = the later of (term deadline, the day they first joined the
chat *this term*) + grace days. So people present at term start get until
22 Sep; a freshman who joins on 1 Oct gets until 8 Oct. Leaving and rejoining
does **not** reset it (first join this term counts), so nobody can farm free
grace periods.

Opted out ("Not continuing this term"), rejected, revoked, never registered —
all count as unpaid and are removed.

## What the treasurer does once

1. In the rec chat, give the bot admin rights **Ban users** and **Invite users
   via link**. Send `/setgroup rec` again — it now checks those two rights and
   replies with the bot-made **join-request link**. Use this link everywhere
   (orientation, Instagram) from now on.
2. In the chat's Invite Links screen, **revoke every old link** (otherwise
   removed people walk straight back in). `/setgroup rec` revokes the main
   link automatically; extra links made by humans must be revoked by hand.
3. **One-time import of people already in the chat** (so silent lurkers can
   be removed too): create an `api_id`/`api_hash` at my.telegram.org (2 min,
   your phone number), put them in `.env`, and Claude runs
   `scripts/import_rec_members.py` on the Pi once (service stopped for a
   minute). It prints "imported 183 of 183". If the numbers don't match, fall
   back to: make the next term's rec group the **last new group ever**, with
   the bot added before anyone joins.
4. Optional: rename the chat to drop "26/27", since it's permanent now.
5. First term: leave `remove_unpaid` on **preview** (default). You get the
   preview list but nobody is removed. When it looks right:
   `/settings remove_unpaid on`.

## Build items

1. **Track the rec chat's people.** New table `rec_group_people`
   (`telegram_user_id` PK, `name`, `first_seen_at`, `keep`). Filled by:
   `chat_member` updates (rec chat only), join requests, and the one-off
   import. `first_seen_at` is set once and never moves (built simpler than
   the per-term join date first drafted: same effect, rejoining never resets
   the grace; a returning member who left gets the term's normal date).
   `__main__.py`: `run_polling(allowed_updates=Update.ALL_TYPES)`.
2. **Join-request gate** (`ChatJoinRequestHandler`, rec chat only): DM via
   `user_chat_id` **first** (the window closes once the request is handled),
   then approve or decline using the same "date passed and unpaid" rule as
   the sweep. A paid, kept, or not-yet-due person is approved.
3. **Registration gate fix:** someone in `rec_group_people` may register even
   while outside the chat (a removed lurker must be able to `/start` and pay
   before rejoining). One extra condition in `_in_club_group`.
4. **Sweep** in the hourly due-check (10:00–22:00 window, existing pattern):
   once per day per term (stamp `sweep-<date>` in `term_events` after the run),
   candidates = registered members ∪ `rec_group_people`; filter by the rule;
   confirm each is really in the chat with `getChatMember` (also skips
   admins/owner); `banChatMember` + `unbanChatMember(only_if_banned=True)`;
   1 s pause; DM registered ones (`Forbidden` = fine). Failures (e.g. lost the
   Ban right) are listed in the treasurer summary; tomorrow retries.
5. **Preview + final warning** on removal day − 2 (stamp `removal-preview`;
   warning DMs stamped per member like reminders). Preview has Keep buttons
   (≤30, same cap as `/unpaid` Mark-paid buttons; beyond that "use /keep").
   In preview mode the preview says nobody will be removed, and no warning
   DMs or kicks happen.
6. **`/keep`** (treasurer): lists kept people with an Unkeep button each.
   Keep buttons callback `kp:<user_id>`, unkeep `uk:<user_id>`.
7. **Settings:** `remove_unpaid off|preview|on` (default `preview`),
   `removal_grace_days` (default 7, 0–60). Shown in `/settings`.
8. **Text tweaks** (only when `remove_unpaid` is `on`): reminder/last-call DMs
   and the **rec** group post get "Unpaid members are removed from the rec
   chat on <date>." The comp group post is unchanged.
9. **`/setgroup rec`**: also require `can_restrict_members` and
   `can_invite_users`; revoke the old primary link (`exportChatInviteLink`: "any previously
   generated primary link is revoked",
   [Bot API](https://core.telegram.org/bots/api#exportchatinvitelink))
   and create + store the join-request link (`createChatInviteLink`
   `creates_join_request=True`); reply with it. `/setgroup comp` unchanged.
10. **`scripts/import_rec_members.py`**: Telethon, bot-token login,
    `iter_participants(rec chat)` → insert into `rec_group_people`
    (first seen = import day, so imported people get the normal grace from
    then: they could not be warned before); prints imported vs
    `getChatMemberCount`. Telethon is **not** added to `requirements.txt`
    (one-off: `pip install telethon` when running it).

Code lives in one new module `clubbot/removal.py` (rule, sweep, preview,
handlers, `/keep`), wired from `bot.py` and `scheduler.run_due_events`.
Tests: `tests/test_removal.py` — the date rule, spared cases (paid comp,
pending, exception, keep, admin, bot), preview mode kicks nobody, kick =
ban+unban, comp chat events ignored, join request approve/decline + DM
before decision, daily stamp, sweep failure reported.

## Accepted / not doing

- **Telegram shows "Bot removed X" in the chat.** Not hidden (would need the
  delete-messages right + another handler). Leaving a chat is visible anyway.
- **No names in group posts**, ever (existing privacy rule). Unregistered
  people are warned by the join-request welcome DM and the group posts.
- **No treasurer confirmation tap before kicks.** The preview 2 days before
  (with Keep), preview mode for the first term, and "a kick is reversible"
  (Keep + pay → rejoin is approved) are the safety net.
- **No per-term removal date in `/newterm`** — one global grace setting.
- **No welcome post in the group** for late joiners — the join-request DM
  does it privately.
- `rec_group_people` stores Telegram ID + display name of people who never
  used the bot (needed to remove them). Nothing else.

---

## Grilling log (questions asked and answered by Claude)

### Round 1 — the rule

**Q1. Who must pay to stay in the rec chat?** Anyone in it — rec or
competitive. ➡️ Verified payment of *either* category keeps you (S$20 comp
covers rec, per v2 decisions). Unpaid comp-roster players get removed from the
**rec** chat only; their comp chat is the comp exco's business.

**Q2. How long is the buffer?** ➡️ Deadline + 7 days (setting), so 3 weeks
from term start with a 2-week deadline. Late joiners get 7 days from their
first join this term. Matches LaunchPass/InviteMember "grace period" settings.

**Q3. Kick or ban?** ➡️ Kick (ban+unban). A permanent ban would block them
from rejoining after paying and would need manual unbanning (LaunchPass's
known pain). Re-entry is controlled by the join-request gate instead.

**Q4. Is a pending receipt "paid"?** ➡️ Yes for removal purposes: pending and
exception spare the person. Kicking someone because Gemini was down or the
treasurer hadn't reviewed yet would be unfair. Rejected/revoked = unpaid.

**Q5. Opted-out members?** ➡️ Removed like any unpaid person; they said
they're not continuing. They can still pay later and rejoin.

**Q6. Exemptions (coaches, alumni)?** ➡️ Permanent **Keep** list, tapped from
the preview; undo via `/keep`. Telegram admins and bot admins are exempt
automatically (bots can't reliably ban admins anyway).

**Q7. Automatic, or treasurer confirms each sweep?** ➡️ Automatic, with a
preview 2 days earlier. The treasurer asked for zero recurring work; a kick
is reversible; preview mode covers the trust-building first term.

### Round 2 — knowing who's in the chat (needed the research)

**Q8. How does the bot learn who's in the chat?** ➡️ Registered members +
`chat_member` join events + join requests. *Not* a "log everyone who speaks"
handler: once the import has run, joins cover everyone, so it's redundant.

**Q9. People already in the chat before the bot watched?** Options: (a)
Telethon one-off import, (b) remove by hand once, (c) one last new group.
➡️ (a), because (b) means comparing ~200 names by hand — the exact pain we're
removing — and (c) is what the club is trying to stop. (c) is the fallback if
the import's count doesn't match. Bot-token login means no personal account
is put at risk.

**Q10. Plain invite link or join-request link?** ➡️ Join request. It is the
only way to DM people who never started the bot (welcome + their date), it
stops removed people walking back in, and rejoining after payment becomes
automatic (no per-person links to send). If the bot is down, requests just
wait (other human admins can still approve by hand).

**Q11. A removed person who never registered wants back — how?** The
registration gate requires being in a club chat. ➡️ Anyone in
`rec_group_people` passes the gate, so: decline DM → `/start` → pay → ask to
join → approved.

**Q12. Rate limits?** ➡️ Kicks 1 s apart (no documented ban limit; this is
the safe reading). DMs already fit under 30/s. One group post per day max.

### Round 3 — failure modes

**Q13. Wrong term dates / bug causes a mass removal?** ➡️ Preview 2 days
ahead lists everyone; nothing is removed without an active term; preview mode
by default; anyone wrongly removed taps-Keep → approved on rejoin.

**Q14. Bot loses its Ban right or is removed from the chat?** ➡️ Sweep
reports "couldn't remove N: <reason>" to the treasurer; stamped for the day,
retried tomorrow (no hourly spam).

**Q15. Bot down on removal day?** ➡️ The hourly due-check catches up on
restart (same as reminders). Pending join requests wait.

**Q16. Paid from a different Telegram account / relinked?** ➡️ The account in
the chat is unpaid → listed in the preview → treasurer taps Keep or the
person contacts the treasurer. Kick DM says "contact the treasurer if you
paid".

**Q17. Treasurer ignores an exception for weeks?** ➡️ The person stays
(spared). The preview line "N receipts waiting for your review" nudges.

**Q18. Does any of this touch the comp chat?** ➡️ No. Handlers return early
unless the chat id equals `rec_group_id`; the sweep only targets that id; the
comp group post text is unchanged. A test asserts comp chat events are
ignored.

**Q19. Does the welcome DM have to come before approval?** ➡️ Yes — the
5-minute `user_chat_id` window ends once the request is handled. DM first,
then approve/decline; a failed DM never blocks the decision.

**Q20. Privacy of storing non-members?** ➡️ Telegram ID + display name only,
needed to remove them; no consent flow possible (they never opened the bot).
Accepted.

Frontier empty: every branch above is settled.

# Plan: recre session sign-ups + "in charge" picks (replaces Mitup)

Date: 2026-10-10 · Status: **v2 approved 2026-10-10** (D1 yes · D2 anyone in rec group · D3 noon · D4 fair rotation · D5 default 30 spots: 4 courts × 6, max ~30)
Research: `docs/research/2026-10-10-session-announcements-rsvp.md` (every
Telegram/Mitup fact there is cited).

## The problem

The vice captain (@mikeclh) posts each recre session with a separate bot
(Mitup) so people can sign up. That is one more bot for everyone, and nobody is
ever made responsible for bringing the nets and shuttles from the store room.

## What it will look like

**1. The vice captain DMs our bot `/session`.** The bot replies with a button
for the usual case and a copy-and-edit template for anything different:

```
[ Same as last time: Thu 16 Oct · 7pm–11pm · ISH 2 · 30 spots ]

Or copy this, change what you need, and send it back:
/session
Badminton Recre🏸
Thu 16 Oct
7pm-11pm
ISH 2
30
```

The lines are title, date, time, place, number of spots (the same shape as the
Mitup card). Dates like `16 Oct`, `Thu 16 Oct`, `16/10`, `2026-10-16` work;
times like `7pm-11pm`, `7-11pm`, `7:30pm-10pm`, `19:00-23:00` work. With no
year given, the bot assumes the next 16 Oct. Past dates, an end time before the
start time, and 0 spots are refused with a plain message.

**2. Preview first.** The bot shows the exact card and **[Post to rec group]
[Cancel]**. Nothing reaches the group until he taps Post.

**3. The card in the rec group:**

```
Badminton Recre🏸
Thu 16 Oct · 7pm–11pm
📍 ISH 2
Hosted by: Mike

👥 Participants · 18 of 30
1. speckls
2. Kaelan Phan
…
⏳ Waitlist · 1
1. Chenyu

🧺 Nets & shuttles: Kaelan Phan (backup: Xu)      ← appears after the pick
[ ✅ Join ]  [ ❌ Leave ]
```

- **Join / Leave** edit the card in place. Only the person who tapped sees a
  small pop-up: "You're in (#14)", "Session full, you're #2 on the waitlist",
  "You've left".
- **Waitlist.** The list is ordered by join time. The first 30 play and the rest
  wait. When a player leaves, the first person on the waitlist moves up
  automatically, and the bot DMs them "A spot opened, you're in". The DM only
  reaches people who have started the bot; the card shows it either way.
- The card shows **plain names**, which ping nobody, so a busy card doesn't spam
  20 people on every tap.
- From the session's start time, Join and Leave are refused ("This session has
  started").

**4. The pick.** At **12:00 noon on the session day** (noon the day before for a session starting by 2pm, so the primary always gets notice) the bot randomly picks two
of the people *playing* (not the waitlist):

- **Primary**: collects the nets and shuttles from the store room.
- **Secondary**: only steps in if the primary can't make it.

Picking is random but fair. Each role goes to someone among the players who have
been primary the **fewest** times before, chosen at random among them, so
nobody gets picked three weeks running while others never are. The bot then:

- posts in the group, as a reply to the card, **@-mentioning** both (this pings
  them, and works even for people with no @username):
  "🧺 Thu 16 Oct: @Kaelan please collect the nets and shuttles from the store
  room. Backup: @Xu (only if Kaelan can't make it).";
- DMs the primary, with an **[❌ I can't make it]** button;
- DMs the secondary: "You're backup; I'll message you if Kaelan drops out".
- If a DM can't be delivered (that person never started the bot), the group
  mention still reaches them.

**5. When the primary can't make it.** They tap **Leave** on the card, or **I
can't make it** in the DM (the same action). The secondary becomes primary, a
**new backup** is picked the same fair way, and both are DMed and mentioned in
the group: "Kaelan can't make it: @Xu is now collecting the nets and shuttles.
New backup: @Minghao." If the secondary leaves, only a new backup is picked.

**6. Cancelling.** After posting, the vice captain's DM has a **[Cancel
session]** button (with a Confirm step). It marks the card "❌ Cancelled",
removes its buttons and posts a short reply in the group so people notice.

## Who can do what

- **Host a session:** any admin. The treasurer runs `/addadmin <vice captain's
  SUTD ID>` once (he must have registered with `/start` first). `/session`
  appears in his `/` menu. *(See decision D1.)*
- **Join:** anyone in the rec group who can see the card, exactly like Mitup.
  No registration needed. *(See decision D2.)*

## How it's built (for the record)

| Piece | Where |
|---|---|
| `/session` command, parser, preview, Post/Cancel, Join/Leave, pick + handoff, card rendering | new `clubbot/sessions.py` |
| Tables `sessions` + `session_signups`, their queries | `clubbot/db.py` (holds every query, per the code map) |
| Handlers registered (command: private chats only; buttons: anywhere) | `clubbot/bot.py` |
| `session` in the admin `/` menu, `/help` text | `clubbot/admin.py` |
| Noon pick inside the existing hourly due-check | `clubbot/scheduler.py` (`_job_due`) |
| Tests | new `tests/test_sessions.py` |

Data kept: Telegram user ID + display name + join time per sign-up (members are
keyed by user ID, never @username, per the ground rules). Sessions store the
venue, times, spots, the card's message ID, who was picked and when.

Safety rules (the same ones v2 uses):

- **The pick is saved before it's announced**, so a restart can't re-roll it.
  An `announced_at` stamp is written only after the group post is delivered;
  the hourly job re-sends a pick that was saved but never announced.
- **Card edits are bundled.** A rush of taps right after posting produces at
  most one edit every ~1.5 s, always showing the latest list (Telegram allows
  about 20 group messages a minute). "Message is not modified" is ignored, and a
  Telegram "slow down" reply is retried after the wait it asks for.
- **The card is the source of truth.** It always shows the current in-charge
  pair. If a handoff ping fails to send, the card is still right.
- Join/Leave can't double-book: the bot handles taps one at a time, and a
  sign-up is unique per person per session.
- The card stays under Telegram's 4096-character limit: at most 100 spots, and
  a long waitlist shows "…and N more".

## Not doing (and when to revisit)

| Idea | Why not now |
|---|---|
| Editing a posted session (new time or place) | Rare. Cancel and repost. Add if it keeps happening. |
| Reminders before the session (Mitup sends two) | The noon pick already reminds the two people who matter. Add a "starts in 2h" group post if people ask. |
| Pinning the card | Needs an extra admin right. The vice captain can pin it by hand. |
| Comp-group sessions | Only recre was asked for. Later this is one extra button on the preview. |
| Removing the buttons when the session ends | Taps are already refused after the start. Cosmetic. |
| Google Sheet tab for sessions | No one asked. The DB has the data if needed. |
| Only paid members can join | See D2. |

## Effort & rollout

About a day of build plus tests, then `/code-review`, then
`bash ~/clubbot/deploy/update.sh` on the Pi. Rollout: the vice captain registers
with the bot, the treasurer runs `/addadmin`, and he posts the next session with
`/session` instead of Mitup. The bot is already an admin in the rec group, so no
new permissions are needed. This can share the real-Telegram smoke test that's
already on the launch checklist.

## Decisions for the treasurer (my recommendation first)

- **D1. Vice captain = admin?** Yes (recommended). Admin also lets him see
  `/unpaid`, `/stats`, `/members` (names + SUTD IDs). That's normal for an exco
  member, and useful for chasing fees. The alternative is a separate
  "host-only" role, which is more work (the role list is fixed in the
  database) for little gain.
- **D2. Who can join?** Anyone in the rec group (recommended, same as Mitup,
  zero friction). The alternative, *only registered members*, guarantees the
  bot can DM everyone (waitlist and in-charge DMs), but people who haven't done
  `/start` would be turned away at the door.
- **D3. When is the pick made?** 12:00 noon on the session day (recommended:
  most people have signed up, and the primary still has hours of notice). The
  alternatives are 24 hours before (more notice, more later drop-outs) or 3
  hours before.
- **D4. Fair rotation or pure random?** Fair rotation (recommended; still
  random, but spreads the job around). With pure random, someone can get it
  several weeks in a row.
- **D5. Is 20 the real court limit?** Mitup's free version caps every event at
  20, so "of 20" may be Mitup's limit, not yours. It doesn't change the build
  (spots are typed per session); just confirm the number with the vice captain.

---

## Appendix: grill log (questions I put to v1, answered myself)

v1 was a 30-line sketch: admins host via a one-line command; the card refuses
when full; the pick is made 3h before; no waitlist and no handoff. The grill
found these gaps and fixed them in v2 above.

**Round 1: roots**

1. *Who hosts, a new role or admin?* `admins.role` has a fixed CHECK
   `('treasurer','admin')`. A new role means rebuilding that table (SQLite can't
   alter a CHECK). Admin costs nothing → **admin**, flagged as D1.
2. *Which group does the card go to?* The rec group (`rec_group_id`, already
   set by `/setgroup rec`). If none is set, `/session` says "send /setgroup rec
   in the rec group first". Comp deferred.
3. *Who may join?* Mitup lets anyone join from their Telegram profile. Gating
   on registration would break parity on day one → **open**, flagged as D2.
4. *How is the session typed?* v1's one-liner `/session 16 Oct 7pm-11pm ISH 2
   cap=20` had **no title** and is fiddly on a phone. **Fixed:** a
   multi-line template shaped like the card, plus a one-tap "Same as last time"
   button for the weekly case. This follows the treasurer's "tap instead of
   type" preference (2026-10-07).

**Round 2: depends on round 1**

5. *Full: refuse or waitlist?* Refusing means people have to keep checking
   back for a free spot. A waitlist costs almost nothing: sign-ups are ordered
   by join time, the first N play, the rest wait, so promotion happens without
   any extra code. **Fixed:** waitlist added, plus a best-effort promotion DM.
6. *Dates without a year, past dates, end before start?* Next occurrence of
   that date; past dates refused; end must be after start on the same day. The
   preview shows the weekday so a wrong date is caught before posting.
   `newterm.parse_date` is reused for full dates.
7. *Names: @username or real name?* Store the Telegram user ID (ground rule)
   plus the display name at join time. Plain names on the card; real mentions
   (by ID, `mention_html`) only in the in-charge posts.
8. *When is the pick made?* v1's "3h before", checked hourly, could land under
   2h before the session, which is too little notice for the store room.
   **Fixed:** noon on the session day, flagged as D3. If the bot is down at noon
   it picks at the next hourly check, but never after the session has started.

**Round 3: the pick**

9. *Who is in the pool?* Only people playing, never the waitlist. The host is
   included if he joined. With 1 player, a primary only; with 0, the host gets a
   DM saying nobody could be picked.
10. *Fairness?* Pure `random.sample` can repeat someone. **Fixed:** random
    among the players with the fewest past primary duties. Counted from the
    final `primary_id` of past sessions, so a handoff credits whoever actually
    did it. Flagged as D4.
11. *How does the primary say "can't make it"?* v1 had no path at all.
    **Fixed:** Leave on the card, or the DM button, which is the same callback
    with the same code. Then secondary → primary and a new secondary is picked.
    If the secondary leaves, a new secondary is picked. If someone without a
    role leaves, nothing changes.
12. *Restart mid-pick?* Save the pick first, stamp `announced_at` after the
    group post is delivered, and re-send it if it was never stamped. DMs are
    best effort, and `Forbidden` counts as delivered (the existing v2 rule). A
    crash between the DMs and the stamp can repeat a DM, which is accepted (same
    as the existing reminders).
13. *What if a handoff ping fails?* **Fixed:** the card always shows the
    current pair, so it stays correct even when a ping is lost.

**Round 4: Telegram limits and failure modes**

14. *A rush of taps right after posting?* Up to 20 edits a minute in one group
    is the risk. **Fixed:** edits are bundled to one per ~1.5 s per card using
    the JobQueue, the same debounce pattern as `request_sheet_sync`, and
    `RetryAfter` is retried. Without a JobQueue (in tests), the card is edited
    immediately.
15. *Two people tap for the last spot?* PTB handles updates one at a time
    (no `concurrent_updates`), and each tap is a synchronous SQLite
    read-then-write with no awaits in between. A unique `(session, user)` key
    stops double joins.
16. *Taps after the session?* Refused from the start time, checked when the
    tap arrives, so no scheduled job is needed.
17. *Card too long?* At most 100 spots; the waitlist shows "…and N more"
    past 20.
18. *Card forwarded to another chat?* Join/Leave only act for the person who
    tapped, so the harm is limited. Not guarded. Revisit if strangers start
    appearing on cards.
19. *Session called off?* v1 had no cancel. **Fixed:** a Cancel button in the
    host's DM, with a confirm step, then the card is marked cancelled plus a
    short group reply (edits alone don't notify anyone).
20. *Should the commands work inside the group?* No. `/session` is
    private-chat only, like every other admin command, so the group never sees
    drafts. Only the card's buttons work in the group.

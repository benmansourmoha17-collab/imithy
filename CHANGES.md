# imithy.py change log (hand this file + imithy.py to another AI to continue)

Single-file Discord bot (discord.py 2.x). Original copy: none kept in this folder; the edits below are all done in `imithy.py`.

## Request list and status

| # | Request | Status |
|---|---------|--------|
| 1 | Racist embed: show the percentage first, then the verdict | DONE (step 1) |
| 2 | Add `,ratio` command (replies: "if yk the meme do the cmd urself bc someone told me to add it but idk what ratio means") | DONE (step 1) |
| 3 | Add `+prefix` command to change the prefix of ANY command at any time | DONE (step 1) |
| 4 | Use Discord custom emojis (bl_* pack) instead of phone emojis | DONE (status lines, racist, puh, family cards, leaderboard) |
| 5 | Family roles not updating in the embed | DONE (step 2) |
| 6 | Base roles not working | DONE (step 2), needs a real server test |
| 7 | Owner name shouldn't be listed with the members | DONE (step 2) |
| 8 | Family embeds cleaner, bleed style | DONE (step 2) |
| 9 | Server owner gets access to every bot-owner-only command | DONE (step 3) |

## Step 1 details

- `emo(name, fallback)` finds the custom emoji `bl_<name>` in any server the bot is in (no IDs needed). Without the emoji it prints the fallback text.
  Names: check, cross, warn, lock, owner, member, family, role, base, heart, percent (+ optional animated bl_*_anim).
- Status lines (`status_text`) use bl_check / bl_warn / bl_cross. Warning bar color is now amber (`WARN_COLOR`).
- `,racist`: line 1 = percentage + bar, line 2 = icon + mention + verdict.
- `,ratio`: new fun command (prefix `,`).
- `+prefix`:
  - `+prefix` lists every command grouped by its current prefix.
  - `+prefix puh !` makes `!puh` work (and `,puh` stop working). `+prefix puh reset` restores the default.
  - Works for any root command name or alias (`fam`, `wf`, ...). Prefix: 1-3 chars, no spaces, no `@`, `<`, or backtick.
  - Saved in `settings.json` under `"prefixes"`. Bot owner only (set `OWNER_IDS` in env if the owner isn't detected).
  - Safety: `@Bot prefix prefix reset` always works, even after changing the prefix of `prefix` itself.
  - Code: `prefix_for()`, `default_prefix_for()`, `all_prefixes()`, `get_prefix()` (used as the bot's `command_prefix`) and the check in `on_message`.

## Step 2 details (family)

- `sync_family(guild, fam)` (new): makes sure the family role exists (recreated if deleted), has the right name/color, sits under the
  base role, every member has it and nobody else does. Runs every time `family info` is used and for all families when the bot starts.
  This is the fix for "family roles aren't updating": before, the role was only ever created when someone joined.
- `place_role` rewritten (base role fix): reads FRESH role positions with `guild.fetch_roles()` (cached positions were stale right after a
  role was created, so the role landed in the wrong spot), moves it with `guild.edit_role_positions`, and now returns a readable reason
  when it can't (bot role too low, missing Manage Roles, ...) instead of failing silently.
- `family baserole` can now be used by the server owner, any Administrator, or the bot owner (it used to be server owner only). It also
  places the "Best family" role under the base role and reports why anything couldn't be moved.
- `info_embed` redone in Bleed's look: author line, bold title, stacked bold-label fields. The owner has their own `Owner` field, then
  `Admins (n)`, `Members (n)` (owner NOT included), `Information` (role, created, kidnaps), footer `Rank #x of y - n/10 members - Module: family`.
- `family top` redone like Bleed's top lists (`1` crown for #1, `Your family: ... - Rank: #n`, `Page 1/2 (13 entries)` footer) and paginated.
- Hints in messages now use the current prefix (`prefix_for('family')`) so they stay right after `+prefix`.

## Step 3 details (server owner access)

- New helpers in bot.py (above the `+prefix` section): `is_server_owner(user, guild)`, `has_full_access(user, guild)` (server owner OR bot owner)
  and the decorator `owner_check()`, which replaces `commands.is_owner()`.
- `+prefix` now uses `owner_check()` (server owner + bot owner). Note the prefix settings are still global, not per server.
- `.customize`, the server-side family commands (`family_admin_check`) and `family baserole` all use `has_full_access()` too.
  Discord already gives the server owner every permission, so `.wordfilter`, `/match panel|unpanel|server` and the Manage Server /
  Administrator commands worked for them already.
- `mmsync` (matchmaking.py) uses the new `need_admin()`: the server owner or the bot owner.
- (Reversed in Step 10: server owners now get the staff tools too.) Was closed: the matchmaking STAFF tools (`match staff chat|log|profile|stop|chats|add|remove|feed`, `match reports|resolve|ban|unban`).
  Matchmaking is global and includes 13-17 year olds, and those tools read private chats and full profiles (date of birth) of users from
  every server. Giving them to every server owner would expose other servers' users. The bot owner can still add a server owner with
  `match staff add @user`. Family rank commands (promote, rename, delete...) still follow the family's own ranks.

## Still to do on your side

1. Upload the emoji pack (bl_*.png / bl_*_anim.gif) to any server the bot is in. The bot finds them by name. No code edit needed.
2. Test on a real server: `.family baserole @role`, then `.family info`. The base role placement could not be run against Discord here.
3. Developer Portal: Server Members Intent must be ON (already required by the original code).

## Prompt to give another AI if you hit your limit

I have a Discord bot in one file, imithy.py (discord.py 2.x), and CHANGES.md describes everything already done. Please read both,
then check each item on the request list in CHANGES.md and fix anything still marked TODO or "needs a real server test", without
rewriting the parts that already work. The goals: family embeds must look like Bleed bot embeds (dark embed, author line, bold labels
with the value under them, footer with details), the family owner must be shown in their own field and never in the members list,
family roles must stay in sync with Discord (role exists, members have it, it sits right under the base role), `,racist` must show the
percentage first and the verdict second, `,ratio` must reply "if yk the meme do the cmd urself bc someone told me to add it but idk what
ratio means", and `+prefix` must let the bot owner change the prefix of any command at any time (saved in settings.json). Use Discord
custom emojis named bl_check, bl_cross, bl_warn, bl_lock, bl_owner, bl_member, bl_family, bl_role, bl_base, bl_heart and bl_percent
(looked up by name through bot.emojis, see `emo()`) instead of phone emojis. Return the complete updated imithy.py and a short list of
what you changed.

## Step 3 (emoji + family help)

- `emo()` now also finds the emojis already uploaded to the server by their real names (`EMOJI_ALIASES`): small_check_white, x_white,
  08excla, white_crown, white_mod, elife_white_star, elife_White_Own..., info_white, etc. Case-insensitive, and a name also matches by
  its start (Discord cuts long names). bl_<n> names still win if they exist. `lock`, `member`, `heart`, `percent` have no alias on purpose,
  so the fun commands are unchanged.
- Status lines (ok / warn / err) now use those emojis; family owner/admin icons and the top list crown too.
- `family` help is paged (Pager): page 1 = group card (Aliases, Parameters, Information, Usage), then 5 subcommands per page, footer
  `Page x/y (n entries) • Module: family`. The wordfilter group uses the same pages. The Family category in the +help dropdown shows only
  the syntax lines when the full text would be long.
- `,racist` redone to match the reference bot (see Step 1 note: percentage-first no longer applies).

## Step 4 (info emoji in help)

- `info_white` (alias `info`) now shows in the help embeds: the `+help` home line, the Information field of a group card when the
  command is open to everyone (restricted ones keep the warning icon), and the `Details:` hint under the Family category list.
- `info_icon()` returns the emoji plus a space, or nothing if the emoji can't be found.

## Friend matchmaking (matchmaking.py) - older notes, the 18+ and gaming parts are removed in Step 6

- New file `matchmaking.py` next to bot.py. `/match panel #channel` posts a sticky sign-up embed (Create / Edit Profile, Find a Friend, My Profile buttons) that moves to the bottom when people chat; `/match unpanel` removes it.  bot.py only changed in `setup_hook`: it loads the extension (a failure there is logged and never stops the bot).
- Commands work as slash commands (`/match find`) AND with the prefix (`.match find`). Run `.mmsync` once as bot owner to register the slash commands. Commands: `.match profile`, `.match find`, `.match leave`, `.match end`, `.match block`, `.match me`, `.match delete`, `.match unblockall`,
  `.match server on|off` (Manage Server), owner only: `.match ban <id> [reason]`, `.match unban <id>`, `.match reports [id]`, `.match resolve <id> [dismissed]`.
- 18+ only, friends only. Age is asked once, locked, and anyone who says under 18 is refused permanently. Default age gap 2 years.
- Data: `matchmaking.json` (profiles, bans, blocks, reports). Env vars are listed at the top of matchmaking.py.
- Not run against real Discord here: needs a real test (two accounts, DMs open).
- TODO: add `match` to HELP_COMMANDS / HELP_CATEGORIES so it shows in `+help`.

## Step 5 (matchmaking embeds rebuilt to match the reference screen recording)

Files: `matchmaking.py` (replaced) + `mm_assets.py` (unchanged, but the file MUST be named `mm_assets.py` and sit next to it; it holds the banner and ribbon images as base64). Without it the bot just skips the images.

- Panel (`/match panel #channel`, also shown by `/match profile`): title "Customize your profile", animated banner (FLAIR IT UP / CREATE YOUR PROFILE / GET DISCOVERED.), buttons **About You**, **Your Vibes**, **Your Match** (row 1: **Find a Friend**, **My Profile**). Old panels with the previous button ids stop working: run `/match panel` again.
- Profile is now 3 steps like the reference: About You (nickname, age on first use, timezone), Your Vibes (introduce yourself optional, what do you like required, dislikes optional), Your Match (friend type select + "only same age or older" toggle, saves on click). A profile can only be matched once Your Vibes is filled in.
- 18+ gate unchanged: first press of About You shows the rules and "I'm 18 or older / I'm under 18"; under 18 is permanent. Links are now refused in every profile field. Sexuality / romantic match type from the reference are NOT copied (friends only).
- Profile card: ribbon image for the friend type (friends / just chatting / gaming buddies / music and anime), title `Name (age)`, About me / My likes.. / My dislikes.., footer `friend id 123456 - n likes` + the created date. The friend id is a random 6-digit number, never the Discord id. Discord limit: an embed image sits at the bottom of the embed, so the banner and ribbon are under the text, not above it.
- Find: `/match find` has a mode choice, Quick Find (within 2 years, fast match) or Deep Find (within 2 years, shared interests & lifestyle: same friend type or a shared word in likes/about). Prefix: `.match find deep`. The panel button asks Quick or Deep. Result text: "Found someone for you! (Quick Match) Matched with Name#123456. Say hello in my DMs."
- Match DM buttons: **Like Profile**, End chat, Report, Block. End chat and `/match end` (alias `cancel`) now ask "Leave this match?" with Approve / Decline, then "Unpaired from Name#123456. Run /match find when you want me to look again." The other person is told their match didn't want to carry on.
- Not copied on purpose: the reference's daily "lookups", Suggestion button and avatar picker.
- Tested here only with a stub of discord.py (matching, deep mode, older-only, leave flow, old-profile migration, images): the real Discord API calls (modals, select, hybrid command choices, file attachments in embeds) still need a test on a real server with two accounts.
- Bot needs Attach Files in the panel channel (the banner is uploaded with the message). `/match panel` checks for it.

## Still to do
1. Test on a real server (two accounts, DMs open), then run `.mmsync` once so `/match find` shows the mode choice.
2. Add `match` to HELP_COMMANDS / HELP_CATEGORIES in bot.py so it shows in `+help`.
3. `bot_updated.zip` is an older snapshot: its bot.py does not load the extension. The live bot.py needs, inside `setup_hook`: `await self.load_extension("matchmaking")` (wrapped in try/except so a failure never stops the bot).

## Step 6 (matchmaking: no 18+ gate, no gaming, more of the reference copied, staff safety tools)

Files: `matchmaking.py` + `mm_assets.py` (the gaming ribbon was removed from it). Both replace the old ones. Run `/match panel` again
(the panel gained a **Suggestion** button) and `.mmsync` once so the new slash commands show up.

**Removed**
- The 18+ gate. Minimum age is now 13 (Discord's own minimum). Anyone whose date of birth is under 13 is refused for good.
- The `gaming buddies` friend type (old profiles that had it become `friends`).

**Copied from the reference (new)**
- About You now asks the **date of birth** (like the reference, locked once saved, age is computed from it), then a **gender** select. Timezone is checked (`GMT+1`, `UTC-5`, `Europe/Paris`).
- Your Vibes ends with the **avatar picker** (cat faces: smirk, happy, grin, lol, cute, sweet, shocked, sad, mad), shown in front of the name on the profile card.
- Your Match: **preferable gender**, **preferable timezone** (global / within 3 hours), **preferable match type**, **only need older match**.
- **Daily lookups**: `MATCH_DAILY_LOOKUPS` (default 3, 0 = unlimited). The result says "You have N lookups left for today."
- **Suggestion** button on the panel: sends the text to the staff.

**Kept on purpose (not copied)**
- The reference's Sexuality field and its Romantic match type. Matching stays friends-only.
- Because under-18s can now use it: under-18s are only matched with under-18s, adults only with adults (on top of the 2-year age gap).

**Staff safety tools** (`.match staff`, or `/match staff ...`)
- Everything is written to `matchmaking_log.jsonl` (every relayed message, matches, ends, searches, profile edits, blocks, reports, bans,
  blocked links/files, suggestions). Entries older than `MATCH_LOG_DAYS` (default 30) are pruned automatically.
- `staff chats` live chats + who is searching | `staff chat <user> [partner] [count]` read a chat (live or latest) |
  `staff log [user|all] [count]` everything that happened | `staff profile <user>` full profile | `staff stop <user> [reason]` end a chat |
  `staff list`. `<user>` = Discord ID, mention, or the 6-digit friend id.
- Output is private: slash commands answer ephemerally, prefix commands are sent to the staff member's DMs. Long output becomes a .txt file.
- Every look a staff member takes is itself logged (`staff_view`), and shown in the live feed.
- Staff can also use `match reports`, `match resolve`, `match ban`, `match unban` (they were owner-only).
- Users are told: the rules screen, the match intro DM and the panel footer say chats are logged and can be read by the staff.

**Owner-only**
- `match staff add @user` / `match staff remove @user` choose the matchmaking staff (saved in `matchmaking.json` under `"staff"`). The bot owner is always staff.
- `match staff feed #channel|off` mirrors every message and event live into a private channel (refused if @everyone can see the channel).
- Reports and suggestions go to `MATCH_REPORT_CHANNEL_ID`, else the live feed channel, else a DM to every staff member and the owner.

**Testing**: checked with a stub of discord.py (profile creation, under-13 refusal, matching rules, lookups, relay + logging, every staff
command, permissions, log pruning, live feed batching). The real Discord calls (modals, selects, hybrid subgroup `/match staff ...`) still
need a test on a real server with two accounts. Not done: add `match` to HELP_COMMANDS / HELP_CATEGORIES in bot.py, and the
`load_extension("matchmaking")` line in the live bot.py's `setup_hook` (see "Still to do" above).

## Step 7 (packaging)

- `bot.py` `setup_hook` now loads the extension: `await self.load_extension("matchmaking")` inside try/except (a failure is logged and never
  stops the bot). This resolves item 3 of "Still to do" above.
- This folder = `bot.py` + `matchmaking.py` + `mm_assets.py` (the Step 6 versions) + `CHANGES.md` + `.env` + `requirements.txt`.
  The token goes in `.env` (`DISCORD_TOKEN=`). `mm_assets.py` must stay next to `matchmaking.py`.
- Still open: add `match` to HELP_COMMANDS / HELP_CATEGORIES in bot.py so it shows in `+help`; test on a real server (two accounts,
  DMs open); run `.mmsync` once as bot owner so the slash commands register.

## Step 8 (matchmaking prefix)

- The matchmaking commands now use the `,` prefix: `,match` (alias `,mm`) and `,mmsync`. `.match` no longer answers (the slash commands
  `/match ...` are unchanged). In the older notes above, `.match` / `.mmsync` now mean `,match` / `,mmsync`.
- Code: `MATCH_PREFIX` (env var `MATCH_PREFIX`, default `,`) and `MATCH_COMMANDS = {"match", "mmsync"}` in bot.py, used by
  `default_prefix_for()` and `all_prefixes()`. `+prefix match (new prefix)` still overrides it at any time and `+prefix match reset`
  restores `,`. Hints inside matchmaking.py already use `ctx.clean_prefix`, so they always show the prefix that was actually used.

## Button icons
- Panel, match DM and Quick/Deep Find buttons now get custom emoji icons (see MM_EMOJI / BUTTON_ICONS in matchmaking.py).
- Looks for bl_<key> first, then the server's own names (user, a_sparkle, 004_heart, ...). Missing icons are skipped.
- Re-send the panel (or start a new match) to see them; old messages keep their old buttons.

## Avatar faces
- The cat avatars now use custom emojis in the profile card title and the avatar picker (see AVATAR_EMOJI in matchmaking.py).
- Looks for bl_cat_<name> first, then meep_<name> / the fallbacks listed there, else the plain cat emoji.

## Staff icons
- Staff embeds (staff tools menu, chats, transcripts, logs, staff profile view, suggestions) get the shield (WhiteStaff).
- The report embed sent to staff gets the announcement megaphone.

## Avatars: all meep emojis, emoji only
- The avatar picker lists every custom emoji named meep_* (any server the bot is in), emoji only, no names shown.
- The saved avatar is the emoji name. Old profiles that saved a cat name map to a meep emoji, else the plain cat emoji.
- If no meep_* emojis exist, the picker falls back to the plain cat faces.

## Slash commands + help
- The bot now syncs its slash commands (/match ...) automatically on startup (check the console for "Synced N slash commands").
- Added a Matchmaking category to the help menu (dropdown) with every match command, and "match" to the Popular list.

## Rules message, new banner, romantic match type
- The "Before you start" rules message now disappears when you press "I understand - continue".
- New panel banner (anime gif with "pair up with ur match on .gg/imyy") and a romantic ribbon, both in mm_assets.py.
- New "romantic" match type, first in the list. Adults (18+) only: under-18s don't see it, the bot refuses it
  server-side, and romantic only matches with another adult who also chose romantic.
- Rules, panel footer and help text updated to say so.

## Step 4: Bleed style status emojis (bot.py + matchmaking.py)

Uses the emojis added with `,emoji add`: approve, deny, warning, add, remove, cooldown, left, telegram, spotify, lastfm, dove
(eth has no natural spot, so it isn't used). Names are matched exactly (`EMOJI_ALIASES` in bot.py, `MM_EMOJI` in matchmaking.py);
the older names (small_check_whi, x_white, 08excla ...) stay as a backup. Anything missing falls back to plain text.

- Message kinds (`status_embed`, `status_line`, `fam_say`, `note(..., kind=...)`):
  ok = approve, green | add = add, green | remove = remove, green | warn = warning, amber | err = deny, red |
  cooldown = clock, amber | wait = clock, blurple | dm = telegram, green.
- Permission and "only the owner" messages are now amber warnings like Bleed. "Slow down" and the kidnap wait use the clock.
- Wordfilter add / remove, family join / promote use `add`; leave / kick / demote / delete / cleared use `remove`.
- Buttons: pager ‹ ✕ use left / deny (the `›` button picks up a `right` emoji automatically if one named `right` is added),
  Accept / Confirm / Approve use approve, Decline / Cancel use deny, Join uses add, customize reset uses remove.
- Gayest / tightest: Join button icon and a clock on "Rolling...". Their other text is unchanged.
- Matchmaking: `note()` picks the icon and bar from the color (green / red / amber) or from `kind=`. Match types show
  dove (friends), telegram (just chatting), spotify + lastfm (music), heart (romantic) on the "Your Match" card.
- NOT touched: `,puh`, `,racist`, `,ratio` and their meters (same code as before).

## Step 9 (profile forms: everything inside the popup, like the screen recording)

- About You, Your Vibes and Your Match are now ONE popup each. The dropdowns and the checkbox sit inside the popup (no more
  follow-up message with a separate gender / avatar / preferences menu). Submit saves everything and answers with
  "<bot> has updated your profile. Check `/match me` to see your more details."
- About You: name, date of birth (first time only, locked), **gender dropdown**, timezone (optional).
- Your Vibes: introduce yourself, what do you like, what do you dislike, **avatar dropdown** (meep_* emojis, emoji only).
- Your Match: **preferable gender (pick any number, none = anyone)**, **preferable timezone** (close to me / can be global),
  **preferable match type**, **"Only need older match" checkbox**. Romantic is refused server-side for under-18s.
- Code: classes `AboutForm`, `VibesForm`, `MatchForm` (+ `FormModal` base, `_older_label`, `gpref_list`, `gpref_text`) in matchmaking.py.
  `MODAL_SELECTS = hasattr(discord.ui, "Label")`: on discord.py older than 2.6 the bot silently keeps the old flow
  (`AboutModal`, `VibesModal`, `MatchPrefsView`). `requirements.txt` asks for discord.py>=2.7.
- If this discord.py has no `discord.ui.Checkbox`, the checkbox becomes a Yes/No dropdown with the same label.
- Preferable gender is now stored as a list (`["female", "nonbinary"]`); old profiles with a single value (`"male"`, `"any"`) still work.
- A failed form submit now tells the person ("Something went wrong while saving that") and logs the error instead of "interaction failed".
- `/match find` now answers privately (ephemeral) for slash commands, like the reference ("Only you can see this").
- Sexuality field from the reference is still NOT copied (friends-only matching, and 13-17 year olds can use it).
- Tested with a stub of discord.py only (all three forms, validation, edit round-trip, defaults, checkbox fallback, gender matching).
  The real popup needs one test on Discord: press About You / Your Vibes / Your Match, submit, then `/match me`.

## Step 10 (server owner can use ALL commands)

- The matchmaking staff tools are now open to the server owner as well: `,match staff chats|chat|log|profile|stop|list`,
  `,match reports|resolve|ban|unban`, and the owner-only `,match staff add|remove|feed`. This was closed before by mistake (not requested).
- Code: `Matchmaking.owns_server(ctx)` is checked in `need_staff`, `need_owner`, and the help texts. The bot owner and listed staff work as before.
- Note: matchmaking is global, so a server owner can now read chats and profiles (incl. date of birth) of users from any server.
  Every look is still written to the log (`staff_view`).

## Step 11 (emoji check against the server's emoji list)

Checked every emoji name in the screenshots against `EMOJI_ALIASES` (bot.py) and `MM_EMOJI` (matchmaking.py). Fun commands (`,puh`, `,racist`, `,ratio`, gayest/tightest) were NOT touched.

- All names match exactly: approve, deny, warning, add, remove, cooldown, left, telegram, spotify, lastfm, dove, eth, WhiteStaff, announce, user, a_sparkle, 004_heart, white_crown, white_mod, elife_white_star, elife_White_Own..., info_white, meep_*.
- NEW: `family info` -> the Information field now uses the `whitereplyextend` / `whitereply` tree connectors (Bleed style). Plain lines if either emoji is missing.
- NEW: matchmaking "Before you start" rules message gets `elife_white_rules` in front of its title.
- NEW (only used when no dedicated emoji exists): My Profile = info_white, Suggestion = elife_white_chat, End chat = left, Report = warning, Block = deny.
- Still missing on the server: `right` (the pager `>` button shows plain text until a `right` emoji is added with `,emoji add`).
- Not wired on purpose: `lock2` (only the fun command tightest asks for a lock), `eth`, `giveaway`, `white_gift`, `elife_white_link`, `red_arrow`, and the decorative ones (pumpkin, panda, etc.).

## Step 12 (shorter help embeds)

- Category pages (`+help` dropdown): command descriptions are cut to the first sentence (max 70 characters), one blank line less per command,
  and a category switches to the compact view above 700 characters (was 1000). Compact view = `.family` create · invite · info ... on a few wrapped lines
  instead of one line per command (`compact_names()`). Family went from 20 lines to about 3, Matchmaking from 25 to about 5.
- Group help (`+help family`, `+help match`): 4 subcommands per page (was 5), no blank line between them. The group card lost its
  "Parameters: n/a" row and shows Aliases and Information side by side.
- Single command help: the Permissions field moved into the footer (`Module: family • Requires: Manage Server`).

## Step 13 (search wording + blue sparkle / like icons on the profile card)

- Quick/Deep Find text no longer says "within 2 years" next to the speed (it read like a waiting time). New wording: Quick Find =
  "the fastest match", Deep Find = "shared interests & lifestyle, so it can take a bit longer", plus a separate line
  "I only match you with people up to 2 years older or younger than you". Helper: `age_gap_text()` (uses `MATCH_MAX_AGE_GAP`).
  Changed in `mode_text()` (slash command choices), the panel's Find a Friend message and the Your Match embed.
- Profile card (`card_parts`): the last line is now `<sparkle> friend id 123456 - <thumb> 1 like`. Footers can't show custom emojis, so the
  line sits at the end of the card text. It only switches to this when BOTH emojis exist on a server the bot is in; otherwise the old
  plain footer is used. Upload `sparkle_blue.png` and `like_blue.png` as emojis (names `sparkle_blue` and `like_blue`, or `bl_id` / `bl_like`).
  `/match me` tidies its footer when the card has no footer.
- (Done in Step 14) Still different from the reference screenshots: the ribbon + name on one line with the avatar as a picture on the right,
  the divider line above the id/likes line, the "drop a hello in the chat" box, Like/Report only under the card, the "can't unmatch for
  another Xm" lock and the lookup refund text on the leave prompt. Those need the newer container layout (discord.py LayoutView).

## Step 14 (cupi container layout: profile card, "drop a hello" box, unmatch lock, lookup refund)

Needs discord.py 2.6+ (`HAS_V2`). On an older version every piece below falls back to the old embed version. If sending a container
message fails for any reason other than closed DMs, the bot logs a warning and sends the old embed card instead, so a match is never lost.

- Profile card (`profile_items` / `profile_view` / `match_view`): one container. Top: big heading `<gender ribbon emoji> Name (age)` with the
  avatar as a picture (Thumbnail, the avatar emoji's image) on the right, then **About me**; then **My likes..** and **My dislikes..**;
  a divider; then `<sparkle> friend id 123456 - <thumb> 1 like`. The gender ribbon is a custom emoji (`ribbon_female`, `ribbon_male`,
  `ribbon_nonbinary`; PNGs are in the outputs, upload them as emojis). Without the emoji the gender is written as small text instead.
  The match type only shows for non-default types (romantic / just chatting / music and anime) as a small line under the name.
  The timezone and the old banner / type ribbon images are no longer on the card.
- Card emojis are made by the bot itself: on start-up `make_app_emojis()` creates `ribbon_female`, `ribbon_male`, `ribbon_nonbinary`,
  `sparkle_blue` and `like_blue` as APPLICATION emojis (the bot's own account, usable in every DM; needs discord.py 2.5+) from the images
  in `EMOJI_PACK` at the bottom of the file. It skips any that already exist, as an application emoji or in a server the bot is in
  (so manually uploaded ones still win). `mm_emoji()` checks `bot.mm_app_emojis` as its last place to look. Nothing has to be uploaded by hand;
  if creation fails the log says why and the card just uses plain text instead of the icons.
- Match DM = card container + a second container "drop a **hello in the chat here** while you wait for their reply." + buttons
  **Like Profile** and **Report** (both grey). No intro embed anymore. End chat / Block are no longer buttons: use `/match end`
  and `/match block` (the small grey line in the hello box says so). New persistent custom ids: `mm2:like`, `mm2:report`
  (the old `mm:*` ones still work on old messages). Shared logic: `like_partner()` / `report_partner()` on the cog.
- My Profile button and `/match me` use the same container card (`show_card`, `profile_view`) with a small "Status: ..." line on top.
- Unmatch lock: `/match end` (or the old End chat button) within the first `MATCH_UNMATCH_LOCK_MINUTES` (default 10) minutes of a match
  only shows "You can't unmatch for another **9m**, give them a chance - say hi and see where it goes." (`lock_left`, `lock_text`).
  Blocking and reporting are never locked.
- Leave prompt (`leave_prompt`, `LeaveLayout`): container "Leave this match?" + divider + small print about lookups, green Approve /
  red Decline underneath. Leaving within `MATCH_REFUND_MINUTES` (default 90) of the match uses up the leaver's lookup and RETURNS the
  other person's (`refund_lookup`, they are told "Your lookup has been returned."); after that both lookups stay used. Logged as a
  `lookup` event in the audit log.
- After Approve the prompt turns into "Unpaired from X. Run </match find:id> when you want me to look again." The command is a clickable
  mention when the slash command ids can be fetched (`cmd_mention`, cached 10 minutes), else plain `/match find`.
- `leave_prompt()` now returns keyword arguments for send()/reply() (`{"view": ...}` or `{"embed":..., "view":...}`) or None.
- Helpers: `box_view` / `box_kwargs` (plain container notice), `wait_text` ("9m" / "45s").
- NOT tested against Discord itself (the sandbox has no discord.py): only the logic was run against a stand-in. If a container message
  looks wrong or fails, the log line "Container ... failed" has the Discord error.

## Step 15 (`,ratio` is now a W / L aura roll)

- `,ratio [member]` no longer replies with the joke text ("if yk the meme do the cmd urself..."). It now picks W or L at random (50/50)
  and a random amount of aura from 1 to `RATIO_AURA_MAX` (10,000, change it at the top of the fun commands in bot.py):
  `🏆 **W ratio**` + "@user earned **4,321** aura", or `💀 **L ratio**` + "@user lost **4,321** aura".
  Same look as `,puh` (author line, "just for fun" footer), 5 second cooldown, servers only. Nothing is saved: it is a fresh roll every time.
  Code: `roll_ratio()` and the `ratio` command in bot.py; the help entry and the `+prefix` example were updated too.
  This replaces the older note above that `,ratio` must reply with the joke text.

## Step 16 (`,puh` perfectly balanced)

- `,puh` now says "**perfectly balanced**, as all things should be" when the roll is exactly 50% tight / 50% loose (about 1 roll in 100).
  Before, an exact 50/50 just counted as "tight". The two bars still show underneath.

## Step 17 (a command blocklist with a custom reply: NOVIE_USERS)

- `NOVIE_USERS` in bot.py (next to the command error handler) holds usernames (lowercase) and/or numeric Discord ids. When one of them runs ANY
  command (prefix or slash, including the `match` commands), the command does not run and the bot replies with the usual one-line status embed
  (`@user: fuck outta my face novie`, text in `NOVIE_TEXT`). Implemented as a global `@bot.check` (`novie_check`) that raises `NovieBlocked`;
  `on_command_error` ignores that error because the reply was already sent. Buttons and menus are not commands and are not blocked.
  Currently contains `cupckces`; add the numeric id too, usernames can change.

## Step 18 (`,gayest` / `,tightest`: winner is a new message, 20 second window)

- The join window is now `GAME_SECONDS = 20` (was 15; defined next to `FUN_COMMANDS` so the help text uses the same number).
- When the time is up, the original Join embed is edited to "**Who is the gayest?** / This round ended. N people joined." (button removed)
  and the result is posted as a NEW message: "Rolling..." for 2.5 seconds, then "**The gayest is** @winner / Picked at random from N people."
  If nobody joined, the join message just says "This round ended, nobody joined so there is no winner." (no new message).
  Code: `run_pick_game()` in bot.py.

## Step 19 (trophy + skull emojis)

- New emoji names in `EMOJI_ALIASES`: `trophy` (the server emoji `86269trophy`) and `skull` (`Skull_2`). Added to the server with `,emoji add`;
  `bl_trophy` / `bl_skull` also work.
- `,ratio`: W ratio starts with the trophy emoji, L ratio with the skull. If the server doesn't have them it falls back to the unicode 🏆 / 💀.
- `,gayest` / `,tightest`: the winner message starts with the trophy emoji (nothing is shown if the emoji is missing, so it looks as before).

## Step 20 (trophy only on `,tightest`)

- The trophy in front of the winner is now only on `,tightest`. `,gayest` shows its winner message plain, as before Step 19.
  `run_pick_game(..., trophy=False)` has a new `trophy` argument; only `tightest` passes `trophy=True`. `,ratio` is unchanged.

## Step 21 (rainbow flag on `,gayest`)

- `,gayest` winner message now starts with the rainbow flag (a server emoji named `rainbow_flag` or `pride_flag` if there is one, otherwise the
  unicode 🏳️‍🌈). `,tightest` keeps the trophy. `run_pick_game()` now takes `prize_key` / `prize_fallback` instead of the old `trophy` flag.

## Step 22 (aura system, family war, reveal / icebreaker / stats, /invite, /stats)

New files created at run time: `aura.json` (next to families.json). Run `,mmsync` once after updating so Discord learns the new slash
commands (`/invite`, `/stats`, `/match reveal`, `/match icebreaker`, `/match stats`).

Aura (bot.py, fun commands with the `,` prefix; all saved per server in aura.json by `AuraStore`):
- `,ratio` (changed): rolls W or L for YOU (the old `@member` argument is gone, so nobody can ratio someone else's aura away), the aura is
  now saved, and it has a persistent cooldown (`RATIO_COOLDOWN`, 1800 s = 30 minutes, survives restarts). On cooldown it answers
  "Slow down, try again in **29m 12s**." in the usual status embed. The embed also shows the new total.
- `,aura [member]` shows the balance, the server rank and the family. `,aura top` (aliases `leaderboard`, `lb`) is the paged leaderboard, same
  layout as `.family top` (only people still in the server).
- `,daily`: once per UTC day. Starts at `DAILY_AURA` (1000) and every extra streak day adds `DAILY_STREAK_BONUS` (100), up to 10 extra days
  (max 2000). Missing a day resets the streak to 1.
- `,duel @member <amount>`: amount can be `500`, `1,500`, `2k`, `1.5m`, `half` or `all`. Both people need at least that much. The other person gets
  Accept / Decline buttons (60 s, only they can press). Accept = fair coin flip, the winner takes the bet. Cooldown `DUEL_COOLDOWN` (30 s).
  Only one pending duel per person (`duel_busy`).
- Aura can go below zero (an L ratio can do that). Long cooldowns now read as `12m 30s` / `5h 12m` (`human_time`), also in the error handler.
- Optional emojis: a server emoji named `aura` shows in front of balances, `duel` on the duel challenge (nothing is shown until they exist).

Family war (bot.py, `.family war`):
- Every gain from `,ratio` W, `,daily` and duel wins also counts as score for the member's family this week (`give_aura`). Losses and the war bonus do
  not count. Weeks run Monday 00:00 UTC to Monday 00:00 UTC (`week_id`, `week_end`).
- `.family war` shows the standings (paged, same style as the other top lists) with the time left and last week's winner.
- When a week ends (`war_roll`, checked every 10 minutes by `war_loop` and also whenever aura changes or `.family war` is used), the family with the most
  score (a tie goes to the older family) gets `WAR_BONUS` (5000) aura for EVERY member, and scores reset. The result is kept in the server's
  `last_war` and announced in the channel set with `.family war channel #channel` (Manage Server; `off` stops it).

Matchmaking (matchmaking.py):
- `/match reveal`: asks the match to swap Discord usernames. The other person gets Accept / Decline buttons (5 minutes). Only when both agree does the bot
  DM each person the other's username. If they had already asked you, asking back swaps right away. Both people must be at least `MATCH_REVEAL_MIN_AGE`
  (default 18) years old, otherwise it is refused. A decline blocks a new request for 5 minutes. Logged as `reveal` events (asked / swapped).
- `/match icebreaker`: sends a random conversation starter (30 in `ICEBREAKERS`, never the same one twice in a row) to both people, 45 s cooldown per chat.
- `/match stats`: your matches, profile likes and match streak (days in a row with a match; counted in `bump_match_stats` when a match starts).
- `/stats`: servers, members, profiles, matches made (counted from this update on, `totals.matches`), chatting now, searching now.
- `/invite`: the bot's invite link as a button (`INVITE_PERMISSIONS` lists what it asks for: send messages, embeds, manage messages / webhooks / roles /
  nicknames and so on; edit it there if you want different permissions).
- The "drop a hello" box now also mentions `/match reveal`.
- `invite` and `stats` use the matchmaking prefix (`MATCH_COMMANDS`), they also work as slash commands.

## Step 23 (aura leaderboard redesign: like the jimyy "Message Leaderboard")

- `,aura top` is now one embed in that style: the small round picture in the author line is the TOP person's avatar (server avatar if they have one)
  next to the title "Aura Leaderboard"; then a numbered list `1. name · **12,345** aura` (top 10, names are display names with markdown escaped);
  then two small grey lines (`-#`): a total ("N aura across M people", or "N aura earned in the last 7 days") and "Last refreshed <relative time>".
  Code: `aura_board_embed()`, `aura_board_rows()`, `AuraBoardView` in bot.py. It no longer has page buttons (only the top 10, like the reference).
- Buttons under it (anyone can press them, they time out after 5 minutes): **My Stats** (private: your aura, server rank, change in the last 7 days,
  daily streak, family) and a range button that flips between **All Time** and **7 Days**. In the 7 Days view the list is ranked by net change
  (`+700`) over the last 7 days (today + 6 back, UTC).
- To make 7 Days possible `AuraStore.add()` now also keeps per-day [gained, lost] totals per person (`days`, kept 14 days; `window()` and
  `ranking_recent()` read them). Aura earned before this update has no day record, so the 7 Days view starts empty and fills from now on. The weekly
  war bonus now goes through `add()` too.
- Ranks shown by `,aura` and My Stats now count only people still in the server (`aura_rank`), so they always match the leaderboard.
- Optional emojis (nothing is shown until they exist): `mystats` on the My Stats button, `calendar` on the All Time / 7 Days button.
- The family lists (`.family top`, `.family war`) were not changed.

## Step 24 (your emojis, aura + duel emojis, /help, automatic slash sync)

- Your new server emojis are used: `:stats:` on the leaderboard's My Stats button and `:fr_calendar:` on the All Time / 7 Days button
  (`EMOJI_ALIASES["mystats"]` = stats, mystats, my_stats; `["calendar"]` = fr_calendar, calendar).
- `aura` and `duel` emojis were drawn (a glowing silhouette, crossed swords) and added to `EMOJI_PACK` in matchmaking.py, so the bot creates
  them itself on start-up like the ribbons / sparkle / like (`make_app_emojis`). bot.py's `find_emoji()` now also looks at those
  application emojis (`bot.mm_app_emojis`), after the server emojis. Where they show: `,aura`, My Stats, the "You now have ... aura" line of
  `,ratio`, and the duel challenge. The PNGs are also in the `card_emojis` folder of the zip.
- `/help` is now a slash command too (`@bot.hybrid_command`): `/help` and `/help command:family war`, and `+help` still works.
  `/invite` was already a hybrid command (slash + prefix) and `/stats` too.
- The bot now registers slash commands by itself: `Matchmaking.auto_sync_slash()` compares the names of all slash commands with
  `slash_synced.json` (next to the data files) and syncs only when the list changed (first start after this update, or after adding a command).
  If it fails the log says so and `,mmsync` still works by hand.

## Step 25 (smaller aura numbers)

- `,ratio` now gives or takes 1 to 500 aura (`RATIO_AURA_MAX`, was 10,000). `,daily` gives 100 aura (`DAILY_AURA`, was 1,000) plus 10 for every extra
  streak day, up to 10 extra days, so 200 at most (`DAILY_STREAK_BONUS`, was 100). The weekly family war bonus is 500 per member (`WAR_BONUS`, was 5,000).
  Duel bets are whatever people choose; the examples in the help now say 50. All of these can still be overridden in `.env`
  (`DAILY_AURA`, `DAILY_STREAK_BONUS`, `WAR_BONUS`); `RATIO_AURA_MAX` is a constant in bot.py.
- Aura that was already earned stays as it is. For a fresh start, stop the bot and delete `aura.json` (the family war scores live in families.json
  and reset every Monday by themselves).

## Step 26 (`.stafflist`: a sticky staff board)

- `.stafflist` (Manage Server, default prefix `.`) posts the staff list in the current channel and remembers where (`staffboard.json`).
  It lists everyone with the **Staff** role as `1- @name Owner`, highest rank first, then A-Z inside a rank. The top person's picture is in the author
  line (title "Staff Team"), like the aura leaderboard, with a small grey "Last updated" line.
- Ranks come from `STAFF_RANKS` in bot.py (top first): `own` -> Owner, `co own` -> Co Owner, `Admin`, `Moderator`, `Helper`, `Partnering` -> Partnerships manager.
  Role names are matched ignoring capitals, emojis and symbols (so "⚡ Co-Own" works), a few spellings count too (owner, mod, partnership...).
  Someone with several rank roles shows with the highest. Someone with the Staff role but no rank role is listed last as "Staff". Someone with a
  rank role but not the Staff role is not listed. The Staff role name can be changed with `STAFF_ROLE_NAME` in `.env`.
- Sticky like the matchmaking panel: when other people talk in that channel the board is reposted at the bottom (and the old one deleted) after
  `STAFF_BOARD_DELAY` (5) quiet seconds. When someone gets or loses the Staff or a rank role, or a staff member leaves, the board is edited in place.
  If somebody deletes it, a fresh one is posted. Several triggers at once become a single refresh.
- `.stafflist off` (aliases `remove`, `stop`) deletes it. Running `.stafflist` in another channel moves it. If a rank role can't be found the command
  says which ones.

## Step 27 (staff board in the same style as the other lists)

- The staff board embed now matches the other top lists: title **Current staff**, the server's picture as the thumbnail (top right), the lines
  `1- @name Owner`, color from the theme, and the footer `Page 1/1 (N entries)`. The top person's picture in the author line and the
  "Last updated" line from Step 26 are gone.
- It also updates when a role is renamed into or out of the Staff / rank names (`on_guild_role_update`). Everything else (role added or removed, member left,
  chat in the channel moving it to the bottom) works as in Step 26.

## Step 28 (FIX: matchmaking.py failed to load; staff list rules)

THE BUG: in Step 22 the `/stats` command method in matchmaking.py was named `bot_stats`. discord.py refuses any command or listener inside a cog whose
method name starts with `bot_` or `cog_`, so the whole matchmaking cog failed to load ("Could not load matchmaking.py" in the console). Everything from that
file was missing (`,match ...`, `,stats`, `,invite`, `/match ...`), and because bot.py syncs the slash commands on every start (`on_ready`), only `/help`
(which lives in bot.py) was registered and the other slash commands were removed from Discord. Fix: the method is now `server_stats`. The slash commands
come back by themselves on the next start. If the console ever says "Could not load matchmaking.py" again, the traceback right under it names the cause.

- `Matchmaking.auto_sync_slash()` (Step 24) is removed again: bot.py already syncs on every start, so it was redundant. `,mmsync` still works.
- A static checker (not part of the bot) now checks the rules discord.py enforces when a cog is loaded (no cog_/bot_ command names, description
  length 1-100, valid slash names, duplicate command names between bot.py and matchmaking.py) and that nothing needs Python 3.12 to parse.
- One f-string in `war_page_embed` (a `'#'` inside the braces) needed Python 3.12; rewritten so it also parses on older Pythons.

Staff list (`.stafflist`):
- Everyone with at least ONE rank role is listed (own, co own, Admin, Moderator, Helper, Partnering), whether or not they also have the Staff role, so the
  owner shows even without Staff. People who only have the general Staff role are NOT listed, and "Staff" is never shown as a rank.
- Role names are matched in any font: `own`, `𝗼𝘄𝗻`, `ᴏᴡɴ`, `ｏｗｎ`, `⚡ own` all count (`role_key()` uses Unicode normalisation + a small-caps table).
- The Staff role is no longer needed by the command; `STAFF_ROLE_NAME` is gone. If none of the rank roles can be found the command says so.
  Changes to only the Staff role no longer refresh the board.

## Step 29 (all prefixes reset, every command is a slash command, help order, bug sweep)

- NEW PREFIX RULES: everything uses `.` except `help` = `+` and `puh`, `racist`, `ratio`, `tightest`, `gayest` = `,`.
  So `.aura`, `.daily`, `.duel`, `.family`, `.match`, `.stats`, `.invite`, `.prefix`, `.stafflist`, `.wordfilter`, `.customize`, `.mmsync` (they used `,` or `+` before).
  Code: `FUN_COMMANDS` (now only the 5 `,` commands), `HELP_PREFIX_COMMANDS` (only `help`), `MATCH_PREFIX` default `.`.
  A command typed with the wrong prefix (like `.puh` or `,aura`) is still silently ignored, on purpose.
  IMPORTANT: prefixes saved with `.prefix` live in `settings.json` ("prefixes") and WIN over these defaults. If a command still needs an old
  prefix, run `.prefix` (or `@Bot prefix`) to see them, `.prefix <command> reset` to clear one, or delete the "prefixes" key from settings.json.
- "halo" -> "aura": there is no "halo" left anywhere in bot.py / matchmaking.py (it was already renamed in earlier steps; only aura exists).
- ALL commands are now hybrid (slash + prefix): `bot.command/group` -> `bot.hybrid_command/hybrid_group` and every subcommand has a description (1-100 chars).
  matchmaking.py's `mmsync` is hybrid too. Groups whose base command does something get a slash "fallback" subcommand, because Discord can't run a
  group on its own: `/aura show`, `/family help`, `/wordfilter help`, `/family war view`, `/stafflist post`.
  Slash tree: /help /puh /racist /ratio /gayest /tightest /aura [show|top] /daily /duel /family [... 21 subcommands + war [view|channel]]
  /match [...] /invite /stats /prefix /stafflist [post|off] /wordfilter [help|add|remove|list] /customize /mmsync.
  Discord always lists slash commands alphabetically, so the order can't be changed there; the order is done in `+help` instead.
- `/family banner` has a real `image` option (prefix: attach the image to the message as before). It used `ctx.message.attachments`, which doesn't exist for slash.
- Slash commands from bot.py are deferred first (`defer_slash`, a `bot.before_invoke`) so slow ones (family sync, banner upload) don't hit Discord's 3 second limit.
  Matchmaking (a cog) is skipped on purpose: it answers ephemerally by itself and a defer would make those answers public.
  `typing_for(ctx)` replaces `ctx.typing()` (3 places) because typing() defers again on slash and would raise "already responded".
- `+help` order: Info (help, invite, stats) -> Fun (puh, racist, ratio, gayest, tightest, aura, aura top, daily, duel) -> Family -> Matchmaking ->
  Server (prefix, wordfilter, stafflist, customize). New category "Server"; `HELP_CATEGORIES`, `_SERVER_ROOTS`, `_ORDER` in bot.py.
- Checked without running Discord (no network in the sandbox): both files compile, no undefined names, and a stub of discord.py was used to
  import both files and verify the command tree: every command is hybrid, names/descriptions are valid for Discord, max depth 3, no group over 25
  subcommands, no duplicate names or aliases. NOT tested against real Discord: see the list of things to try in the chat reply.

## Step 30 (aura -> reputation, `,` prefix back for the reputation commands)

- The aura system is now called **reputation** (another bot in the server already has an `aura` command). Command: `,reputation` with alias `,rep`
  (`/reputation show`, `/reputation top`, prefix `,rep top` / `,rep lb`). Every message, help page and leaderboard title says "reputation" now.
- Prefix `,` is back for the reputation commands: `,reputation` / `,rep`, `,daily`, `,duel` (plus `,puh ,racist ,ratio ,gayest ,tightest` as before).
  `FUN_COMMANDS` in bot.py = {puh, racist, ratio, gayest, tightest, reputation, daily, duel}. Everything else stays `.`, help stays `+`.
- Internals left as they were on purpose so nothing saved is lost: the data file is still `aura.json` with the key `"aura"`, the code names (`auras`, `give_aura`,
  `AuraStore`, `DAILY_AURA`...) and the custom emoji name `aura` (card_emojis/aura.png) did not change. Existing balances carry over.
- If you had saved a prefix for `aura` with `.prefix`, it is ignored now (the command is called `reputation`).

## Step 31 (live reputation board, top-user reward role, mmsync fix)

- `,rep board [#channel]` (server owner / bot owner; also `/reputation board`): posts the reputation leaderboard ALONE in a channel, in the same look as the
  message leaderboard (author line with the #1 picture, numbered list, totals line, "Last refreshed" that ticks, My Stats + range buttons).
  The message edits itself every `REP_BOARD_REFRESH` (60) seconds, is re-posted if someone deletes it, and its buttons are permanent (work after restarts).
  The range button (All Time / 7 Days) is shared and saved; a new board starts on `REP_BOARD_MODE` (default "all"). `,rep unboard` removes it.
  Code: `RepBoardStore` (repboard.json), `RepBoardView`, `refresh_rep_board`, `rep_board_loop` (started in setup_hook), `rep_stats_embed`.
- `,rep reward @role` (server owner / bot owner): the #1 person on the ALL-TIME leaderboard (needs more than 0 reputation) gets that role and it is taken from
  everybody else, checked on every refresh and right when the command is used. `,rep reward` shows the current role, `,rep unreward` stops (the current holder keeps it).
  The bot needs Manage Roles and its own role must sit ABOVE the reward role. Works even if no board is posted. Code: `sync_rep_role`.
- mmsync (matchmaking.py): the server owner is checked first now (before, a failing "is bot owner" lookup could block the server owner), and the slash
  version defers so a slow sync can't time out; failures are shown instead of silently failing.

## Step 32 (starter trigger words)

- `DEFAULT_WORDS` in bot.py (the 20 words from the old server's `.wordfilter list`, pages 1-2) is copied into every server the bot has never seen
  (`WordStore._seed`); a list someone emptied on purpose stays empty. `.wordfilter defaults` (Manage Server) adds any that are missing to an existing server.
  Page 3 of the old list (the 21st word) wasn't in the screenshots, so it isn't included: add it to `DEFAULT_WORDS`.

## Step 33

- `wordfilter` (alias `wf`) now uses `,` : `,wordfilter add/remove/list/defaults`. Added to `FUN_COMMANDS`. A prefix saved earlier with `.prefix wordfilter ...` would still win.

## Step 34 (ratio fix, quests, collect)

- `,ratio` was a 50/50 coin flip with the same 1-500 size for wins and losses, and an L could push you below zero, so people felt they only lost.
  Now: 60% of rolls are W (`RATIO_WIN_CHANCE`), a W gives 1-500 (`RATIO_WIN_MAX`), an L takes 1-200 (`RATIO_LOSS_MAX`) and CAN take you below zero, but once someone is
  already below zero an L only takes 30% of its roll (`RATIO_DEBT_LOSS`, minimum 1) so they can climb back out. Cooldown is **5 minutes** now (`RATIO_COOLDOWN`, default 300; it used to be 1800).
  All three numbers are env variables, so they can be tuned without touching the code. `,duel` is unchanged (zero-sum, both sides must have the bet).
- `,quests` (alias `,quest`, also `/quests`): every person gets 3 random daily quests (reset 00:00 UTC) and 2 random weekly quests (reset Monday 00:00 UTC).
  Quests count: messages (not commands, at least 3 characters, one per `QUEST_MESSAGE_GAP` = 3 s), ratios used, ratios won, daily claimed, duels won,
  gayest / tightest rounds joined, and "claim 10 daily quests" (weekly). The card shows a bar, the reward and a **Claim rewards** button (only the owner of the card can press it);
  `,quests claim` does the same. Rewards go through `give_aura`, so they also count for the family war. Pools and rewards are `DAILY_QUESTS` / `WEEKLY_QUESTS` in bot.py.
  Progress is saved in `quests.json` (separate from aura.json so people who only chat don't appear on the reputation leaderboard); it is written every 30 s
  (`quest_save_loop`) and once more when the bot shuts down. Hooks: `quest_message` (on_message), `quest_progress` calls in ratio / daily / duel accept / `JoinView.join`.
- `,collect` (also `/collect`): staff and server boosters get `COLLECT_AMOUNT` (100) reputation, once every `COLLECT_COOLDOWN` (24 h). Staff = server owner, Administrator permission,
  or any role from the staff list ranks (own, co own, admin, mod, helper, partnering) or a role called "Staff"; booster = Nitro boosting the server. Someone who is both still gets 100 once.
  Everyone else is told it's only for staff and boosters. `,quests` and `,collect` were added to `FUN_COMMANDS` (`,` prefix), the help menu and `HELP_EXAMPLES`.

## Step 35 (smash or pass, `,sop`)

- `,sop` (aliases `,smashorpass`, `,smashpass`; also `/sop play`): posts a random PICTURE from the server's list with **Smash** / **Pass** buttons. Anyone can vote (pressing the other button
  changes the vote), the card shows live smash / pass bars and closes after `SMASHPASS_SECONDS` (30) with a verdict. One round per channel at a time, 5 s cooldown per person, the same picture
  never comes twice in a row. In `FUN_COMMANDS` (`,` prefix) and the help menu (group `sop`).
- The list is filled by staff (Manage Server), anyone can read it: `,sop upload` (attach up to 10 pictures to the message; one picture can be named with `,sop upload Name`, otherwise the file name
  is used; the files are saved in the `sop/` data folder so they never expire, 8 MB each), `,sop add <picture link> <name>` (direct link ending in .jpg/.png/.webp/.gif), `,sop remove <name>`
  (also deletes the saved file), `,sop list`. Saved per server in `smashpass.json`, 200 pictures per server (`SMASHPASS_MAX`).
  `SMASHPASS_DEFAULTS` in the sop block of bot.py is a `[("Name", "https://...jpg")]` list every server gets as well. It ships EMPTY: nothing is pulled from the internet automatically.
- (Replaced in Step 36: random sources now exist, but only age-gated ones.) Originally: no random picture source (anime or real people), because the age of whoever is in a random picture can't be checked. It only uses what staff add and never server members'
  avatars or profiles (some members may be under 18). Staff should only add adult (18+) characters / people.

## Step 36 (`,sop` random sources, age-gated)

- `,sop` now also draws from two random sources next to the server's own list (each round picks one of: list, anime, people). Nothing is shown unless it passes an age check first:
  - **anime**: AniList GraphQL, the 2000 most popular characters (random page of 50). Only characters whose age is a plain number of **18+** ("17", "17-18", "18+", "Unknown", empty are all skipped). Shown as `Name (Series)`.
  - **people**: Wikidata SPARQL, living humans (no death date) with a photo on Wikimedia Commons, a recorded birth date that makes them **21+** (the birth year must also be 22+ years back, because Wikidata fills unknown month/day with 01-01),
    an actor / singer / model occupation and 30+ Wikipedia language editions (so they are well known). A random 5-year birth window is queried each round.
  - Pictures are downloaded by the bot (https only, hosts `wikimedia.org` / `anilist.co` only, image content types only, 8 MB) and sent as an attachment, so no hotlink problems.
  - Pages / windows are cached 6 h in memory (`SOP_REMOTE_TTL`). If a source fails the round falls back to the server's list; with no list it says to try again.
- Staff (Manage Server): `,sop source anime|people|both|off` (per server, saved in `smashpass_cfg.json`; no argument shows the current setting) and `,sop block` (never show the last random picture again here, for characters who pass
  the stated-age check but shouldn't be there, like a childlike character with a "500 years old" age). `SOP_RANDOM_SOURCES` env var sets the default for servers that haven't chosen (default `anime,people`, empty = off).
- Known limit: a stated age can't tell what a character LOOKS like, so some will slip through; that is what `,sop block` is for. Still never uses server members' avatars or profiles.

## Step 37 (`,sop` split and picture quality)

- Random rounds are now **55% people / 45% anime** (`SOP_WEIGHTS`, env `SOP_WEIGHT_PEOPLE` / `SOP_WEIGHT_ANIME`). If one source fails the other is tried, and the server's own list is only used when the random sources are off or both fail.
- Picture quality gate (`sop_good_picture`, reads the size from the file header, no extra library): people need to be at least 400 px wide, anime at least 200 px, and the picture has to be a portrait or square
  (height / width between 0.9 and 2.0 for people, 0.8 and 2.0 for anime). Wide group shots and tiny thumbnails are dropped and another one is tried (up to 6 tries).
- The age checks from Step 36 stay for BOTH sources (people 21+ by birth date, anime a stated age of 18+).
- Known limit: the bot can't see who is in the picture, so a people photo can still occasionally be a poor crop or have another person in it. `,sop block` handles those.

## Step 38 (`,sop` embed polish)

- The Smash button had no icon (the global `heart` emoji key is left unmatched on purpose, so other commands stay as they were) while Pass had the `:deny:` one. New key `smash` -> the server's `:004_heart:` (white outline heart), used ONLY by ,sop
  (`EMOJI_ALIASES["smash"]`). Pass keeps `cross` (`:deny:`). Both fall back to a plain button when the server doesn't have the emoji.
- Card layout now matches the other game cards: bold name, blank line, the bars with the same icons as the buttons (`heart `██████░░░░` **60%** smash · 3` / `deny `████░░░░░░` **40%** pass · 2`), blank line, then `:cooldown:` Voting ends <t:...:R>.
  Closed: `Chat says **SMASH**` (heart) or `**PASS**` (deny) / `**Split decision**` / `Nobody voted.`. Names are markdown-escaped. No emoji on the server = same layout without icons.

## Step 39 (`,sop` simplified: picture, two buttons, a message per press)

- The card is now just the picture with the name (embed) and the two buttons **Smash** / **Pass** under it (heart / deny icons). No bars, no timer, no tally.
- Pressing a button posts a normal message in the channel: `@user smashes **Name** 😳` or `@user passed on **Name**` (mentions don't ping). One answer per person per picture
  (a second press gets a private "You already answered this one."). For anime the message uses the plain character name, the card shows `Name (Series)`.
- The buttons stay for `SMASHPASS_SECONDS` (default now 120 s, env var), then they are removed and the picture stays. The "one round per channel" lock is gone: `,sop` can be run again right away
  (still a 5 s cooldown per person). The age checks, 55/45 split, quality checks, `sop source` and `sop block` are unchanged.

## Step 40 (`,sop` running totals)

- The message after a press now carries that person's running total in the server: `@user smashes **Name** 😳 · 5 smashes total` and `@user passed on **Name** · 3 passes total`
  (singular at 1: "1 smash total" / "1 pass total"). A smash press shows the smash total, a pass press shows the pass total.
- Totals are per person per server, never reset, saved in `smashpass_stats.json` (`sop_stats`, `sop_count`, `sop_answer_text` in bot.py). Only the first press on a picture counts (a second press is still "You already answered this one.").

## Step 41 (`,sop` answers as embeds)

- The answer after a Smash / Pass press is now a one-line embed in the same style as the other replies (icon, `@user: text`, side bar), not plain text:
  `:004_heart: @user: smashes **Name** 😳 · 5 smashes total` (green bar, like the Smash button) and `:deny: @user: passed on **Name** · 3 passes total` (dark default bar, like the grey Pass button).
  The icons are the same ones as on the buttons (`smash` / `cross` emoji keys), left out if the server doesn't have them. Bar colors: `SOP_SMASH_COLOR` / `SOP_PASS_COLOR` next to `sop_answer_embed`.
- "You already answered this one." is now the same warning-style embed (only the person who pressed sees it). Mentions inside the embeds never ping.

## Step 42 (`,sop` answers ping)

- The Smash / Pass answer now pings the person who pressed. Discord never pings from inside an embed, so the answer is sent as the mention (message text, which pings) with the embed under it;
  the `@user:` inside the embed stays as part of the style. `allowed_mentions` only allows that one user. The private "already answered" embed doesn't ping.


## ,sop update
- ,sop never goes silent: random fetch capped at 20s, failed sources skipped for 5 min, then a saved picture is used (data/sop_cache, up to 300, filled in the background).
- Second anime source (MyAnimeList via Jikan) when AniList fails. Anime need a stated age of 18+.
- People are 18+ (was 21+), birth date must be recorded.
- Portrait-shaped pictures only; optional opencv makes people pictures show exactly one face.
- "No image" placeholders are detected (same picture on two characters) and banned; ,sop block also bans the picture itself.
- A funny line sometimes appears under the picture (SOP_JOKES). Buttons stay 10 minutes (SMASHPASS_SECONDS).
- ,sop upload with no attachment gives a friendly message.
- Gifs: about 20% (SOP_GIF_PERCENT) of ,sop pictures are a gif of an age-checked character / person, searched on Giphy (GIPHY_API_KEY) and/or Tenor (TENOR_API_KEY). No key = gifs off. Saved in the picture cache like the rest.
- Fixed cut-off pictures (half-loaded, grey below): downloads are now read fully and checked, broken ones are dropped from the cache.
- People were showing less than anime: Wikidata answers are now saved for a week (data/sop_people.json) and keep loading in the background if one ,sop stops waiting; 3 candidates are checked at once; people pictures may be a little wider; the saved-picture fallback follows the 55/45 weights.
- Second source of real people: Wikipedia's most-read articles of a random recent day, checked through the Wikidata API (human, alive, birth date known to the day and 18+, has a picture). Saved in data/sop_trending.json. Used 60% of the time, the slow Wikidata birth-year list is the other source and the fallback.

## Step 43 (`,sop` is now a 5-round vote, and the buttons no longer freeze)

- `,sop` starts a session of `SOP_ROUNDS` (5) pictures. Each picture is open `SOP_VOTE_SECONDS` (10) seconds with a live "Voting ends in X seconds" line
  (a Discord timestamp, so it counts down without the bot editing). Press Smash / Pass to vote (private confirmation; you can change your vote until the time is up).
  When time is up the same message is edited to show who smashed and who passed, the counts and the verdict, and the buttons go away. `SOP_PAUSE_SECONDS` (3) later the next picture
  is posted. After the 5th the session stops; type `,sop` again. One session per channel at a time. The next picture is fetched while people vote, so the pause really is 3 s.
- Running smash / pass totals are still counted (once per picture per person, at the end of the round).
- BUG FIX "didn't respond in time": the old button handler wrote a file before answering, and picture fetching did disk writes, hashing and image checks on the bot's main thread,
  which froze every button for a moment. Now a press only touches memory and answers at once; file writes (`save_async`, `sop_cache_put`) and hashing / quality checks run in threads.
- Code: `SopRoundView` (replaces `SmashPassView`), `sop_get_entry`, `sop_send_round`, `sop_group`, `sop_running`, `SmashPassStore.save_async`.

## Step 44 (less CPU: Wispbyte stopped the server at 15% average CPU)

- `,sop` no longer fills its picture cache in the background by default (`SOP_WARM=1` in `.env` turns it back on) and checks one candidate picture at a time
  (`SOP_CHECK_AT_ONCE`, default 1, was 3). If opencv is installed, uninstall it (`opencv-python-headless`, `numpy`): face detection is the heaviest part.

## Step 45 (more anime, famous people only, 20 s votes, votes as a new embed)

- Random rounds are now 75% anime / 25% people (`SOP_WEIGHT_ANIME` / `SOP_WEIGHT_PEOPLE`).
- Real people must be famous: a Wikipedia page in at least `SOP_MIN_FAME` (70) languages, checked in both people sources (the Wikidata list and the most-read-articles list).
  Occupations now also include politicians, rappers, record producers and athletes (was actors / singers / models). Pictures must be at least 500 px wide (was 400). Still 18+ with a recorded birth date.
  The saved people lists use new file names (`sop_people_v2.json`, `sop_trending_v2.json`) so old unfiltered lists are ignored. Delete `data/sop_cache` once to drop old saved people pictures.
- Voting is 20 seconds (`SOP_VOTE_SECONDS`).
- The picture message is no longer edited with the result (only its buttons are removed). The votes come as a NEW embed under it: character name + thumbnail, who smashed, who passed, verdict.

## Step 46
- `,sop` voting time is now 30 seconds (`SOP_VOTE_SECONDS`, default 30).

## Step 47 (goat role, quests every 5 minutes, removed board / reward / mmsync / invite)

- REMOVED: `,rep board`, `,rep unboard`, `,rep reward`, `,rep unreward` (and the live-board loop, `RepBoardStore`, `RepBoardView`, `repboard.json` is no longer read), `.mmsync` and `/invite`.
  The slash commands that were removed disappear by themselves on the next start (the bot syncs on every start). A leaderboard message posted earlier stays in its channel but its buttons stop working: delete it.
- NEW: automatic **goat** role (`sync_goat_role`, `schedule_goat_role`, name from `GOAT_ROLE_NAME`, default `goat`). The bot creates the role itself (gold) the first time somebody has more than 0 reputation,
  and gives it to the all-time #1 of the reputation leaderboard; it is taken from everybody else. If a role called "goat" already exists it uses that one. No command needed.
  It is checked 20 seconds after any reputation change (several changes = one check, hooked in `AuraStore.add`) and 5 seconds after start-up. No timer loop. Needs the Manage Roles permission, and the bot's role must be above `goat`
  (a role the bot creates itself is always below the bot's role). Problems are written to the log once per server.
- Quests: progress is written to disk every 5 minutes instead of 30 seconds (`QUEST_SAVE_SECONDS`, default 300) and once more when the bot shuts down. Counting messages is still instant (in memory).
  Restarting the bot with a kill (no clean shutdown) can lose up to 5 minutes of quest progress.

## Step 48 (`,reputation give`)
- `,reputation give @member <amount>` (alias `,rep give`, also `/reputation give`): the giver sends ANY whole number of THEIR OWN reputation, up to what they have (37, 12345, ...; `2k`, `1m`, `half`, `all` are just shortcuts).
  It comes out of the giver's balance (nothing is created), not to yourself or a bot, 10 second cooldown, everyone can use it. It doesn't count for the family war or quests. The receiver is pinged.
  It moves the goat role like any other reputation change. Code: `rep_give` next to `,daily`.
  (An owner-only version that created reputation was tried and replaced by this one.)
- Checked `,ratio`: an L roll (1-200) is cut to 30% (minimum 1) only when the balance is below zero (`RATIO_DEBT_LOSS`); at exactly 0 or above it takes the full roll.

## Step 49
- `,sop` random rounds are now 90% anime / 10% people (`SOP_WEIGHT_ANIME` / `SOP_WEIGHT_PEOPLE`; was 75 / 25).

## ,sop mostly females + clean rep leaderboard
- `,sop` now leans female: each pick first decides "female" (SOP_FEMALE_PERCENT, default 65 = 65% female / 35% male) or "male", then draws from that group (falls back to the other if the group is empty, so a round never stalls). Applies to live pictures, gifs and the saved-picture fallback.
  Gender comes from AniList (`gender`), Wikidata (`P21`, trans women count as female, trans men as male) and, for the MAL fallback, a "Gender: Female/Male" line in the character text. Unknown / non-binary entries are only used when nothing else is left.
- Data files `sop_people_v2.json` / `sop_trending_v2.json` became `_v3` so the lists are re-fetched once WITH gender (old ones are ignored; the old picture cache has no gender and is only used as a fallback).
  Same age checks as before (18+ stated age / birth date known to the day), same quality checks, `,sop block` unchanged.
- `,rep top` (aliases `,rep lb`, `,rep leaderboard`) now looks like the family top / Bleed lists: your name on top, title "Top reputation" (or "Top reputation (7 days)"), `1` **name** - value lines (10 per page),
  "Your reputation: **N** - Rank: `#n`", footer "Page 1/N (X entries)". Buttons: ‹ › (only when there is more than one page), All Time / 7 Days, My Stats. The old "Last refreshed" and totals lines are gone.

## New reputation emoji
- The purple person "aura" emoji is replaced by a white four-point sparkle `rep_star` (PNG in EMOJI_PACK in matchmaking.py; same white, minimal look as the other icons). It has a new name because the bot never re-creates an emoji whose name already exists on its account.
  On start-up `make_app_emojis` creates `rep_star` and deletes the old `aura` app emoji. To use your own picture instead, upload any emoji named `bl_aura` to a server the bot is in: it wins over the built-in one.

## ,sop male split + female default 65
- Default is now 65% female / 35% male (SOP_FEMALE_PERCENT=65). The gender is decided FIRST for each picture, then the source:
  male pictures are 80% anime / 20% real people (`SOP_MALE_ANIME_PERCENT`, default 80); female pictures keep the old split (`SOP_WEIGHT_PEOPLE` 10 / `SOP_WEIGHT_ANIME` 90).
  The same decision is used for live pictures, gifs and the saved-picture fallback. Overall that is about 28% anime males, 7% real-person males, and the rest female.

## ,sop female split 70 / 30
- Female pictures are now 70% anime / 30% real women (`SOP_FEMALE_ANIME_PERCENT`, default 70); male pictures stay 80% / 20% (`SOP_MALE_ANIME_PERCENT`). Gender is still 65% female / 35% male.
  `SOP_WEIGHT_PEOPLE` / `SOP_WEIGHT_ANIME` are no longer used by the picker (they only remain defined).
  Overall: about 45.5% anime females, 19.5% irl females, 28% anime males, 7% irl males.

## ,sop female split 85 / 15
- Female pictures are now 85% anime / 15% real women (`SOP_FEMALE_ANIME_PERCENT`, default 85); males stay 80% / 20%; gender stays 65% female / 35% male.
  Overall: about 55% anime females, 10% irl females, 28% anime males, 7% irl males.

## `.backup` / `.restore` (reputation + quest progress only)
- `.backup` (server owner / bot owner, guild only, 30 s cooldown): DMs you a small text file `reputation-backup-YYYY-MM-DD.txt` with a code (`REPBK1:` + compressed JSON) holding this server's reputation (aura.json: balance, streak, daily, cooldown, 7-day history)
  and quest progress (quests.json). The bot owner can use `.backup all` for every server. The code is never posted in the channel; if DMs are closed it says so.
- `.restore` (same permission): attach that file (or paste the code after the command). People in the backup get their saved numbers (overwrites those people, everyone else is untouched). The server owner restores only their own server; the bot owner restores every server in the file.
  It validates the file (header, size limit 8 MB, unpack limit 64 MB, only numeric ids and whole-number reputation), saves aura.json + quests.json, and refreshes the goat role.
- Costs nothing while idle: no background task, no timer. 3000 people = about 40 KB and ~30 ms to restore.
- Server ids are the same on a new host, so a new Wispbyte server just needs the bot invited to the same Discord server; run `.restore` there.
- NOT included (on purpose): families, wordfilter words, matchmaking, settings. (The `,sop` list and its uploaded pictures ARE included since the step below.)


## Step 36 (`,sop` is now one picture, the old session is `,sop vote`)

- `,sop vote` (also `/sop vote`): the old 5-picture session with timed votes and the result embed. Same behaviour as before, 5 s cooldown.
- `,sop` (also `,smashorpass`, `,smashpass`, `/sop play`): posts ONE picture with Smash / Pass buttons. Anyone can press (once per picture). Each press posts a new embed that
  mentions the person: `@user smashed Name 😳` and, in small text (`-#`), `user has 5 smashes` (or `passed on Name` / `has 3 passes`). Totals are the same ones `,sop vote` counts (`smashpass_stats.json`).
- Cooldown: 60 s per person between two `,sop` (`SOP_COOLDOWN_SECONDS`, default 60). If no picture could be shown the cooldown is given back. Buttons disappear after `SMASHPASS_SECONDS`.
- Help menu: `sop` text updated, new `sop vote` entry.

## Step 37 (better `,sop` pictures: featured list + young real people)

- `SOP_FEATURED` (top of the featured block, easy to edit): Trump, Diddy, Netanyahu, Putin, Saddam Hussein, Stephen Hawking, Sabrina Carpenter, Bonnie Blue. Looked up by name on Wikidata, picture from
  Wikimedia Commons, kept 30 days (`sop_featured_v1.json`). Must be human, birth date known and 18+, and have a free picture, otherwise skipped (logged). 40% of the real-person pictures
  come from this list (`SOP_FEATURED_PERCENT`).
- Other real people are now young (18 to `SOP_MAX_AGE_PEOPLE`, default 35) and only actors, singers, models and rappers (politicians, producers and athletes removed). `SOP_MIN_FAME` default 70 -> 40
  because the young pool is smaller. Anime is still ~80-85% of the pictures (`SOP_MALE_ANIME_PERCENT`, `SOP_FEMALE_ANIME_PERCENT`).
- New cache names (`sop_cache_v2`, `sop_people_v4.json`, `sop_trending_v4.json`) so older people saved earlier are not shown again.

## Step 38 (more anime, footballers, Tom Holland, featured anime characters)

- Anime is now ~90% of the pictures (`SOP_MALE_ANIME_PERCENT` / `SOP_FEMALE_ANIME_PERCENT` default 90). 35% of the anime pictures come from `SOP_ANIME_FEATURED` (Luffy, Goku, Kaneki, Gojo, Levi, Zoro,
  Makima, Yor, Nami, Robin...; looked up by name on AniList, `SOP_ANIME_FEATURED_PERCENT`), the rest from the 1500 most popular characters.
- Age check for anime: the old rule (a plain single number) dropped many adults with text like "30-34" or "24 (Part II)". Now every number AniList lists must be 18+: "19", "30-34", "24 (Part II)" pass;
  "17", "17-18", "15 (Pre-timeskip), 19 (Post-timeskip)", "Unknown" are dropped, so anyone who is under 18 at ANY stage is never used.
- `SOP_FEATURED` (real people) now has the infamous six, 10 footballers, Tom Holland (must be the actor), 8 more young men, and Sabrina Carpenter, Bonnie Blue + 12 more young women. 90% of the real-person
  pictures come from it (`SOP_FEATURED_PERCENT`); the rest are random young actors / singers / models. Up to 6 new names are looked up per picture, so the list fills in over the first few `,sop`.

## Step 39 (photo date check, Lamine Yamal)

- Real-person pictures are now checked for the age the person had IN THE PHOTO, not only today: for anyone born in the last 40 years, the photo's own "date taken" on Wikimedia Commons must be known and
  its year at least 19 years after their birth year. Photos with no date, or taken before they were 18, are never shown (`sop_photo_adult`). Applies to random people and the featured list.
- New cache names again (`sop_cache_v3`, `sop_people_v5.json`, `sop_trending_v5.json`, `sop_featured_v2.json`) so nothing saved before this check is reused.
- Added Lamine Yamal to `SOP_FEATURED`. He is only shown if his Commons picture was taken after he turned 18 (July 2025).
- Added Michael Olise to `SOP_FEATURED` (Step 39 list of footballers).


## Step 40 (`.backup` / `.restore` now include the `,sop` pictures, staff can `sop add`, `sop source` enable / disable)

### 1. `.backup` and `.restore` include the `,sop` list and its pictures
- `.backup` (server owner / bot owner, unchanged) now sends in DMs:
  1. the same `reputation-backup-DATE.txt` as before. It now also holds the `,sop` entries that are picture LINKS (`sop add`), they are tiny. The code format is still `REPBK1:` / `"v": 1`, the new part is an extra `"sop"` key, so old codes still restore.
  2. if the server has pictures added with `sop upload` / `sop addpic`: one or more `sop-pictures-DATE-partNofM.zip` files, sent one per DM right after. Each zip is self-contained: the pictures stored as `<server id>/<id>.<ext>` plus a `manifest.json` (`{"v":1,"sop":{server:[{name,file}]}}`).
  Zips are split so each stays under `BACKUP_PART_MB` (env, default 9 MB; Discord DMs take about 10 MB per file). If a zip can't be sent the channel gets a warning that names this setting. `backup all` (bot owner) includes every server, pictures too.
  Pictures are read and zipped one part at a time in a thread (`ZIP_STORED`, pictures are already compressed), so memory stays at about one part and the bot never freezes. Nothing runs in the background.
- `.restore` accepts the .txt, the .zip files, or both: all attached to ONE message (prefix command), or one run per file (slash command only has one file box). A pasted code still works.
  - Links are added to the list (already-present links skipped). Pictures are written back to `data/sop/<server>/<id>.<ext>` with the SAME file names, so restoring the same zip twice adds nothing twice; an entry whose picture file was wiped gets its file back.
  - The list limit (`SMASHPASS_MAX`, 200) still applies and is reported. The server owner restores only their own server, the bot owner every server in the files (same rule as before).
  - Safety for zips from outside: only names matching exactly `<digits>/<32 hex>.(jpg|jpeg|png|webp|gif)` are read (no path tricks), the server id in the name must match the manifest and be allowed, max 500 files / 8 MB each / 64 MB total, the bytes must really start like a jpg / png / gif / webp, names are cleaned (no backticks, 60 characters).
- Code: `backup_sop_links`, `backup_sop_files`, `backup_sop_parts`, `backup_build_zip`, `backup_read_zip`, `sop_restore_links`, `sop_restore_pictures`, `backup_command`, `restore_command`. New import: `zipfile`.
- NOT included: smash / pass totals (`smashpass_stats.json`), the per-server `sop source` switches and blocked pictures (`smashpass_cfg.json`), the random-picture cache.

### 2. `sop add` for staff
- `sop add <link> <name>` can now be used by: anyone with the **Staff** role, plus the other staff roles ,collect already counts (Owner, Co Owner, Admin, Moderator, Helper, Partnerships; any font, see `role_key`), Manage Server, Administrator, the server owner and the bot owner.
  Same definition of "staff" as `,collect`: `collect_reason(member) == "staff"`. Boosters do NOT count. Code: `sop_can_add`, `sop_staff_check`.
- Everything else in `,sop` is unchanged: `sop upload`, `sop remove`, `sop block` still need Manage Server.
  To let staff use another one, replace its `@commands.has_permissions(manage_guild=True)` with `@sop_staff_check()`.

### 3. `sop source`: three sources the server owner can switch on and off
- The three sources, shown in this order: **1) people irl** (random real people, internal name `people`), **2) anime** (random characters), **3) uploaded pics** (internal name `uploaded`: the server's own list, `sop upload` + `sop add`).
- Allowed to change them: the server owner, the bot owner, and anyone with Manage Server / Administrator (as before). The Staff role alone is NOT enough. Code: `sop_source_check`.
- `sop source` shows each one as on / off. New: `sop source enable <people|anime|uploaded|random|all>` and `sop source disable <...>`. `random` = people + anime, `all` = all three.
  Aliases: `irl` / `real` = people; `pics` / `pictures` / `upload` / `uploads` / `list` = uploaded. `sop source anime off` / `sop source pics on` work too (either order).
  Older one-word forms still work and leave uploaded pics alone: `anime`, `people`, `both` set exactly those random sources, `off` turns the random ones off. `all` alone turns everything on. `enable` / `disable` alone only print the usage.
  Nothing is saved when nothing changes. Saved in `smashpass_cfg.json`: `sources` (the random ones, as before) and the new `list` (false = uploaded pics off; missing = on, so existing servers keep working).
- BEHAVIOUR CHANGE: before, the server's list was only a fallback (used when the random sources failed), so uploaded pics hardly ever came up while anime / people were on. Now it is a real source: when uploaded pics AND a random source are both on,
  `SOP_LIST_PERCENT` (env, default 30) % of the pictures come from the server's list. Only uploaded on = always the list. Only random on = never the list (also not as a fallback when a random source is down; the picture just fails and the cooldown is given back).
  Everything off: `,sop` says every source is off and how to turn one on. While uploaded pics is off, `sop add` / `sop upload` / `sop list` tell you so. `sop add`, `upload`, `remove` and `list` still work on the list while it is off.
- Code: `sop_list_on`, `sop_enabled`, `sop_active_pool`, `sop_nothing_text`, `sop_off_note` (next to `sop_sources`), the top of `sop_get_entry`, `sop_source`. Help menu texts updated.

### Testing note
discord.py could not be installed where this was written, so the real functions were pulled out of bot.py and run against stubs (zip round trip, split parts, restore twice, wiped files, malicious zips, permission checks, every `sop source` form, the picker with uploaded pics on / off): 113 checks passed. It has NOT been run against real Discord. First real test: `.sop upload` a picture, `.backup`, `.sop remove` it, `.restore` with the files.


## Step 41 (gambling with reputation: `,coinflip` and `,slots`)

### What was added
- `,coinflip <amount> [heads|tails]` (aliases `,cf`, `,flip`; also `/coinflip`): a coin flip against the bot. Win = you get your bet again on top, lose = the bet is gone. `,coinflip heads 50` works too. No side given = random side.
- `,slots <amount>` (alias `,slot`; also `/slots`): three reels with a short "spinning" line that is then edited into the result. Three matching pay 3x (cherry), 4x (lemon), 5x (grape), 8x (bell), 20x (diamond), 40x (three sevens = JACKPOT). Any two matching pay 1.5x. No match loses the bet. Exact payback is about 96.7% (3.3% house edge), checked by counting every combination and by simulation.
- Both are in the `,help` menu (Fun), use the `,` prefix, and use the same one-line embed style (green check = won, red cross = lost).

### Rules
- Bets go from 10 to 10,000 reputation. Amount can be `50`, `2k`, `1.5m`, `half` or `all` (`all` above the max bet is refused with the max shown).
- Nobody can bet into debt: you need at least the minimum bet in your balance, and never more than you have.
- One bet every 5 seconds per person; coinflip and slots share that cooldown. A refused bet does not start the cooldown.
- A bet is ONE change to the balance (win = +profit, lose = -bet), so the 7 Days leaderboard shows the real net. Winnings do NOT count for the family war and do not count for quests.
- Nobody else's reputation is touched (it is only you against the bot).

### Settings (environment variables / .env, all optional)
`GAMBLE_MIN_BET` (10), `GAMBLE_MAX_BET` (10000), `GAMBLE_COOLDOWN` (5), `COINFLIP_WIN_CHANCE` (0.5 = fair coin; 0.48 gives the house a small edge).
Slot symbols, weights and payouts are in `SLOTS_SYMBOLS` and `SLOTS_PAIR_PAYOUT` (near `WAR_BONUS`).

### Emojis
No new server emojis are needed. The coin and the machine are plain Unicode emojis. Name a server emoji and say so if you want them replaced.

### Code
`gamble_start`, `slots_spin`, `slots_payout`, `coinflip_command`, `slots_command` (just before the `,quests` section); constants after `WAR_BONUS`; help entries after `duel`.

### Testing note
discord.py is not installed where this was written, so the new functions were run against stubs: exact and simulated slots payback, every refusal (below minimum, negative, nan, inf, more than balance, all over the max, in debt, bad side, cooldown), swapped arguments, 20,000 coinflips (50.4% wins) and 20,000 real command runs of `slots`. It has NOT been run against real Discord. First real test: `,slots 10`, `,coinflip 10 heads`, `/slots 10`.


## Step 42 (blackjack, drawn emojis, dark gambling embeds, exploit audit of the gambling commands)

### What was added
- `,blackjack <amount>` (alias `,bj`; also `/blackjack`): Hit / Stand / Double buttons under a dark table embed. The rules live in `gambling.py` (no Discord code in it, so it can be tested alone):
  fresh shuffled 6-deck shoe for EVERY game, dealer checks for blackjack first (a double down can't lose to a dealer blackjack), blackjack pays 3:2 rounded down (a bet of 15 wins 22),
  a win pays 1:1, a tie gives the bet back, the dealer stands on every 17, double down only on the first two cards (one card, then the dealer plays). No splitting, no insurance, no surrender.
  One game at a time per person and server. Same bet limits and cooldown as coinflip / slots. Nobody else can press your buttons. If nobody presses for `BLACKJACK_TIMEOUT` seconds (60) you stand automatically and the game is paid out normally.
- Every gambling embed (coinflip, slots, blackjack, and their refusals) is now the same dark card (`theme()` color = invisible bar, your name + avatar on top, bold lines, then a Bet / Result / Balance row). The other commands keep their colored status bars.
- Slots now stop one reel at a time (3 short edits, `SLOTS_SPIN_SECONDS` = 0.8 between frames, was one 1.2 s wait). The result is still settled BEFORE the first frame is sent.
- 71 new drawn emojis (all named `bl_<name>`, created by the bot on start-up as application emojis, in the same step as the card emojis; each one that fails is retried on the next start):
  `c_<rank><suit>` x52 (`c_as`, `c_10h`, `c_kd`...), `c_back`, slot tiles `s_cherry s_lemon s_grape s_bell s_diamond s_seven s_spin`, `chip`, `coin_h`, `coin_t`, `up`, `down`, `push` (the Result row),
  button icons `hit stand double`, title icons `slotmachine cardsicon`. Until an emoji exists the embed shows plain text instead (cards as `A♠`). Look: `gamble_emojis/` has the PNGs (128x128), drawn with Pillow; `gamble_emojis.py` holds the same images as base64.
  The first start creates ~70 emojis, which can take a few minutes if Discord rate-limits it (it runs in the background, the bot works meanwhile). A server emoji with the same name (`bl_chip`...) wins over the bot's own, so you can replace any of them.

### Exploit audit: what was wrong and what changed
1. FOUND: `,duel` went through `give_aura`, which adds WAR points for every gain. Two accounts could duel each other all day (reputation only moves between them) and farm the family war score, and the winning family gets `WAR_BONUS` (500) per member every week = reputation out of thin air.
   FIXED: duels use `auras.add` directly, so they never count for the war.
2. FOUND: the duel quests (win 1 duel = 100, win 3 duels = 350) could be farmed the same way with a second account and a 1-reputation bet.
   FIXED: a duel win only counts for quests when the bet is at least `GAMBLE_MIN_BET`, and the same two people only count once per UTC day (`duel_counts_for_quests`). Real alt accounts can still earn some quest progress this way; that is the same limit every quest has (an alt can also send 20 messages).
3. FOUND: pressing Accept twice quickly could resolve the same duel twice. FIXED: a `done` flag in `DuelView` (also for Decline and the timeout); later presses do nothing.
4. HARDENED: every bet (coinflip, slots, blackjack, duel) now uses `gambling.RNG`, which is `random.SystemRandom` (the operating system's generator). Python's normal generator can in theory be predicted from enough of its outputs.
5. HARDENED: `.env` typos can't open a hole: `GAMBLE_MIN_BET` is at least 1, `GAMBLE_MAX_BET` at least the minimum and at most 1,000,000,000, a negative `GAMBLE_COOLDOWN` becomes 0, and `COINFLIP_WIN_CHANCE` above 0.5 (or not a number) becomes 0.5, because the house must never lose on average.
   At start-up the bot also logs an error if the slots table (`SLOTS_SYMBOLS`, `SLOTS_PAIR_PAYOUT`) would pay back 100% or more (`slots_payback()`; the default is 96.7%).
6. BLACKJACK money is never "in the air": starting a game takes the bet out of the balance in ONE step that also checks the balance (`AuraStore.hold`, saved together with a `bj_hold` number in the person's record). Double down takes the second bet the same way (refused if you can't pay it).
   The game is paid out exactly once (`AuraStore.release`, guarded by a lock and a `settled` flag, so double clicks, a click at the same moment as the timeout, or a late click can't pay twice). If the bot restarts in the middle of a game the held bet is given back on start-up (`recover_holds`).
   If the table can't be shown (send fails) or the game code crashes, the bet is given back. The 7 Days leaderboard gets ONE net change per game (payout - stake), like coinflip and slots.
7. UNCHANGED and checked: coinflip and slots settle before anything is shown (nothing to cancel), the bet is checked and taken with no `await` in between (50 commands sent at once = one bet), a refused bet doesn't start the cooldown, gambling never counts for the family war or quests, nobody can bet into debt, `parse_amount` rejects negative / nan / inf / huge / garbage amounts.

### Settings (environment variables / .env, all optional)
New: `BLACKJACK_TIMEOUT` (60, between 10 and 600). The ones from Step 41 still work (and are clamped as above).

### Code
`gambling.py` (new: `Blackjack`, `hand_value`, `payout_for`, `RNG`), `gamble_emojis.py` + `gamble_emojis/` (new), `AuraStore.hold / release / recover_holds / _tally / _goat` (next to `add`), the gambling section before `,quests` (rewritten: `gamble_start`, `coinflip_command`, `slots_command`, `BlackjackView`, `blackjack_command`, embed helpers `gnote`, `gamble_embed`, `add_result_row`...),
`duel_counts_for_quests` + `DuelView` (next to `duel_busy`), `make_app_emojis` in matchmaking.py (one failing emoji no longer stops the rest; it also reads `GAMBLE_PACK`).

### Testing note
discord.py is not installed where this was written, so nothing was run on real Discord. The real code was cut out of bot.py and run against a fake Discord (106 checks, all passed): every refusal, 300 all-in bets sent at once (never below zero), 50 slots commands at once (one played),
double clicks, a press racing the timeout, a failing message edit, a failing send, a crash inside a press, a restart in the middle of a game, double down rules, natural blackjacks and every payout, duel pressed twice, and 3000 random blackjack games through the real buttons (balance = before - stake + payout every time, nothing left held).
Engine: 40,000 random games (payouts always one of 0 / stake / 2x / 1.5x+stake, dealer always plays to 17). House edge: perfect basic strategy without splitting still loses 0.65% (800,000 games, even bets); the engine was also checked against an independent simulator (mimic-the-dealer: -5.7% vs -5.75%).
Blackjack pays 3:2 rounded down, so an odd bet loses half a point on a natural; that is why the simulations use even bets. Slots payback 96.7%.


## Step 43 (three animated emojis)

- 3 animated GIF emojis were added to the pack (now 74 emojis: 71 still, 3 animated). All are 128x128 and between 33 and 52 KB (Discord's limit is 256 KB). The bot creates them on start-up like the others (`gamble_emojis.py` / `gamble_emojis/*.gif`).
  - `bl_s_spinning`: a slot reel scrolling through the symbols. It replaces the still `s_spin` tile in the slots animation (reels that haven't stopped yet). The still tile stays in the pack as the fallback.
  - `bl_coin_flip`: a gold coin tossed in the air, turning from H to T. `,coinflip` now shows "Flipping..." with it for `COINFLIP_SPIN_SECONDS` (1.0, a constant next to `gamble_last`) and then edits the message into the result. The bet is settled BEFORE that, exactly as with slots. If the animated coin doesn't exist (yet), there is no pause: the result is sent straight away.
  - `bl_jackpot`: a pulsing gold sparkle. Shown in the slots headline on three sevens (instead of the machine icon) and in front of "Blackjack!" on a natural blackjack.
- If an edit or send fails during the animation, the result is still shown as a new message (or logged) and the balance was already changed once.
- Testing: the fake-Discord harness now also runs the animated paths (failing edit, failing send, jackpot, natural blackjack): 116 checks passed.


## Step 44 (the odds now favour the players)

The owner asked for gambling where players win more than they lose. This REPLACES what Step 42 says about the house edge, the coin chance being limited to 0.5, and the start-up error about slots paying back 100%.
Nothing about how bets are checked, held or paid changed (all the exploit fixes from Step 42 still apply): only the odds.

### New defaults
- `,coinflip`: the player wins 55% of flips (`COINFLIP_WIN_CHANCE`, default 0.55; measured 54.7% over 40,000 flips). A win pays the bet again on top, so the player gains 10% per bet on average. Allowed range 0 to 1 (nan / text = 0.55).
- `,slots`: any two matching symbols now pay 1.75x (was 1.5x; `SLOTS_PAIR_PAYOUT` is now an env setting, default 1.75, allowed 0 to 10). Three matching still pay 3x / 4x / 5x / 8x / 20x / 40x. The payback is 109.2% (it was 96.7%).
- `,blackjack`: a natural blackjack pays 2:1 (was 3:2; `BLACKJACK_PAYOUT`, default `2:1`, any `a:b` with both sides 1 to 20, junk = 2:1; the result line shows the real ratio). The result is rounded down. With perfect basic strategy the player now gains about 1.4% per bet (800,000 simulated games, even bets); with 3:2 the house wins about 0.65%.
- The start-up log now prints the odds in force ("Gambling odds: coinflip 55% to win, slots pay back 109.2% of every bet, blackjack pays 2:1").

### Careful: reputation inflates
With these odds every bet is worth more than it costs, so reputation is created out of the games. A player betting the maximum (10,000) on coinflip every 5 seconds gains about 1,000 per flip (12,000 a minute) on average, which dwarfs `,ratio` and `,daily` and makes the leaderboard a contest of who gambles most.
To dial it back without touching code, in .env: lower `GAMBLE_MAX_BET`, raise `GAMBLE_COOLDOWN`, move `COINFLIP_WIN_CHANCE` toward 0.5 (0.5 = fair, below 0.5 = the house wins), `SLOTS_PAIR_PAYOUT=1.5` (house edge about 3%), `BLACKJACK_PAYOUT=3:2` (house edge about 0.65%).

### Testing
Fake-Discord harness updated (124 checks passed): payback numbers, coin chance clamp, blackjack payout ratios (2:1, 3:2, 6:5, junk).


## Step 45 (goat role survives renaming, `.goatrole`, the novie block is gone)

### The goat (top reputation) role
- BUG: the bot found its role by NAME (`GOAT_ROLE_NAME`, "goat"). When the owner renamed the role in the server (to "top 1#"), the bot no longer found it, created a NEW role called "goat" and gave it to the new #1, while the old #1 kept the renamed role.
- FIX: the bot now remembers the role by its ID (`goat_roles.json`, class `GoatRoleStore`, function `find_goat_role`). The role can be renamed, recolored or moved at will. Order of lookup: the remembered id, else an existing role named "goat" (which is then remembered), else a new role is created (and remembered). If an admin deletes the role, one new role is made.
- NEW command `.goatrole @role` (also `/goatrole`, needs Manage Server): pick which role the #1 gets (the bot gives it to the #1 and takes it from everyone else). `.goatrole` alone shows the current one. Roles above the bot's role, @everyone and bot-managed roles are refused. The role used before is taken off everyone who still has it.
- To fix a server that already has the problem: run `.goatrole @top 1#` once (the old #1 loses it, the new #1 gets it within a few seconds), then delete the extra "goat" role by hand.
- Alternative without the command: set `GOAT_ROLE_NAME=top 1#` in .env before the first start of this version.
- Tested against a fake server (15 checks): update from the broken state, picking the renamed role, renaming again, the id surviving a restart, a deleted role, refused roles.

### The novie block is removed
- `NOVIE_USERS`, `NOVIE_TEXT` (the reply that cursed at the blocked person), `NovieBlocked`, `is_novie` and `novie_check` are deleted from bot.py. Nobody is blocked from commands any more.


### Step 45b: the role is called "Top 1#" and sits under Staff
- The role the bot creates is now named **Top 1#** (`GOAT_ROLE_NAME`, default changed from "goat"). The lookup ignores upper/lower case, so a role you already renamed to `top 1#` is found by name automatically on the first start (no `.goatrole` needed). The extra "goat" role the old version made is left alone: delete it by hand.
- When the bot CREATES the role it places it right under the **Staff** role (`goat_anchor_role` / `place_goat_role`): the plain "Staff" role if the server has one, otherwise under the lowest of the staff rank roles (Partnering, Helper, Moderator, Admin, Co Owner, Owner). If the Staff role is above the bot's own highest role (a bot can't put a role at or above its own), the role goes right under the bot's role instead. If Discord refuses the move the role is still created and given.
- Placement happens only at creation: after that move the role wherever you like (and change its color / icon, the bot never touches those), it stays there. `.goatrole @role` doesn't move the role you pick either.
- Tested against a fake server (23 checks): your current situation (renamed role + extra "goat"), renaming again, a new server, Staff above the bot, no Staff role, nothing to anchor to, a refused move, a deleted role, `.goatrole` and its refusals.


## Step 46 (gambling embeds redone: minimal, Bleed style, big cards)

Why: the old gambling cards were too cluttered, had too much text, didn't look like Bleed, and the cards/emojis were too small.
Only the look changed. Bets, odds, holds, payouts, cooldowns and the blackjack rules are untouched.

- Every gambling result is now: big emojis on top, ONE result line (`@you won +500`), and a tiny grey line (`Heads · Bet 500 · Balance 1,500`).
  The name+avatar line, the Bet / Result / Balance fields, the `Blackjack · chip` header, the "Hit, Stand or Double down" footer and the
  "Spinning..." / "Flipping..." headlines are gone.
- Big emojis: embeds draw emojis at text size, so the emoji lines now start with `# ` (a markdown header = big text, so the emojis scale with it).
  A blackjack hand of 5+ cards uses `## ` so it still fits on a phone. `GAMBLE_BIG_EMOJI=0` in .env switches this off (plain small emojis).
  NOT tested on real Discord (no discord.py here): if the cards don't come out bigger, set that to 0 and tell the next AI.
- Helpers (next to `gamble_embed`): `big()`, `small()` (the `-# ` grey text), `net_text()`, `result_line()`, `stats_line()`.
  Removed: `chip_text`, `rep_text`, `add_result_row`. `gamble_embed(user, text)` keeps its signature but no longer adds the author line
  (to get it back: return `with_author(discord.Embed(...), user)` there).
- Blackjack: `**Dealer** \`10 + ?\`` / big cards / `**<your name>** \`17\`` / big cards / grey `Bet 100`. When it ends: result line + net
  (`**You win** ▲ +100`, `**Bust** ▼ −100`, `**Push**`) and the grey `Bet · Balance` line. Result texts are shorter (no "It pays 2:1" any more,
  that stays in the help text). Busted totals no longer say "bust".
- Slots: big reels, then `@you won ▲ +75` and `Two match 1.75x · Bet 100 · Balance 1,075`. Jackpot: `<jackpot emoji> @you hit the **JACKPOT** ▲ +4000`.
  `slots_card(user, shown, line, stats)` has a new signature (the headline argument is gone).
- Coinflip: big coin, `@you won ▲ +500`, `Heads · Bet 500 · Balance 1,500`.
- Tested with a stub harness that runs the real helper code (text of every embed checked by eye); a simulated preview image was rendered.


## Step 47 (colored Hit / Stand / Double buttons and a gold reputation sparkle)

Why: the blackjack button icons were plain white outline circles and the reputation icon was a flat white sparkle with no color.
Only the pictures changed. No rules, odds, bets or payouts were touched.

- Redrawn (all 128x128 PNG, glossy filled circles with a darker rim and a white symbol; they read well on dark and light themes):
  - `bl_hit`: green circle, white plus.
  - `bl_stand`: red circle, white pause bars.
  - `bl_double`: gold-orange circle, white "x2".
  - `rep_star` (the reputation icon, `emo("aura")`): gold four-point sparkle with a small one top right and a darker gold rim, so it is visible on a light theme too.
- Files changed: `gamble_emojis/bl_hit.png`, `bl_stand.png`, `bl_double.png`, `card_emojis/rep_star.png`, the matching entries in `gamble_emojis.py` (`GAMBLE_PACK`) and `matchmaking.py` (`EMOJI_PACK["rep_star"]`). The pack entries decode to exactly the PNG files.
- IMPORTANT, why a new picture alone would not have shown up: `make_app_emojis` skipped every name that already existed on the bot's account, so the old white emojis would have stayed for ever.
  It now remembers the sha1 of each picture it uploaded in `emoji_hashes.json` (next to the other data files, `EMOJI_HASH_FILE`).
  When a picture in the pack changes, that ONE emoji is deleted and created again on the next start. Because the file doesn't exist yet on the first start with this version,
  the four names in `EMOJI_REDRAWN` are replaced once; every other emoji is left alone and just gets its hash recorded.
  A failed delete or create is retried on the next start. To redraw an emoji in the future: change its picture in the pack and restart, nothing else.
- If someone uploaded these PNGs to a server BY HAND (`bl_hit`, `bl_stand`, `bl_double`, `rep_star`), that server emoji is found before the bot's own, so it must be replaced by hand too.
- Messages that were posted before the restart and still show the old emoji (an old leaderboard, a game in progress) show a broken emoji until they are refreshed, because the old emoji is deleted.
- Testing: the emoji refresh code was run against a fake Discord (14 checks passed): first start with old emojis and no hash file, second start (nothing happens), one picture changed later, delete failing, create failing after a delete, a brand new bot, a corrupt hash file.
  discord.py is still not installed where this was written, so it was not run on real Discord.


## Step 48 (an idle blackjack game now cancels and gives the bet back)

Why: a player didn't press Hit / Stand / Double, the bot stood for them after 60 seconds and the game was played out, so it "won" by itself once and lost the next time.
This REPLACES what Step 42 says about a timeout standing automatically (the line "If nobody presses for `BLACKJACK_TIMEOUT` seconds you stand automatically").

- NEW setting `BLACKJACK_TIMEOUT_ACTION` in .env (`BJ_TIMEOUT_ACTION` in bot.py), what happens when nobody presses anything for `BLACKJACK_TIMEOUT` seconds (60, between 10 and 600):
  - `refund` (DEFAULT, the owner's choice): the game is cancelled and the WHOLE bet comes back. The table shows `Cancelled · bet returned` and the grey line `Too slow · Bet 100 · Balance 1,000`; the dealer's hole card stays face down.
  - `stand`: the old behaviour, the hand is played out like a Stand press and paid by the rules.
  - `lose`: the bet is lost (`Timed out ▼ −100`).
  - Anything else = `refund`.
- Only `BlackjackView.on_timeout` changed, plus the new `blackjack_timeout_embed` helper next to `blackjack_embed`. The payment still goes through `settle()` (runs once, so a press racing the timeout can't pay twice) and nothing stays held.
- Known loophole of `refund`: a player can look at their first two cards and the dealer's card, and walk away from a bad hand to get the bet back. If people abuse it, set `BLACKJACK_TIMEOUT_ACTION=lose` (or `stand`) in .env and restart, no code change needed.
- Testing: the real `on_timeout`/`settle`/embed code run against a fake Discord with the real blackjack engine (210 checks passed): refund returns the full bet and clears the hold, lose keeps it, stand pays 200 random games by the rules, a second timeout does nothing, an already settled game is ignored, a failing message edit still refunds once.
  discord.py is not installed where this was written, so it was not run on real Discord.

## Step 49 (slots nerfed, family bank, family-only rep give, ,rob)

- `,slots`: the pair payout default is 1.6x (`SLOTS_PAIR_PAYOUT`) and the symbol weights are flatter (22/20/18/16/8/4), so about 51% of spins hit something and the payback is about 95%.
- Family bank: every family has a `bank` (reputation). `.family rep` (alias `bank`) shows it, `.family deposit <amount>` puts your own reputation in, `.family withdraw <amount>` (owner only) takes it out. When a family ends, its bank is shared out between the members. The family info card shows the bank.
- `.family top` ranks families by their bank (a tie goes to the family with more members, then the older one) and each line still shows the member count. The "Best family" role follows the same ranking.
- `,rep give` only works between members of the same family.
- NEW `,rob @member`, once per `ROB_COOLDOWN` (1800 s) per person: a rob works `ROB_CHANCE` (45%) of the time and takes 10-25% of the victim's reputation (at most `ROB_MAX` 1500); when caught the robber pays the victim that amount instead. Reputation only moves between the two (nothing is created) and it doesn't count for the family war. You can't rob yourself, bots or your own family; the victim needs `ROB_MIN_BALANCE` (100) and is safe for `ROB_IMMUNITY` (3600 s) after being robbed. A fine can put the robber in debt but never lower than `-ROB_DEBT_LIMIT` (500); at that point `,rob` is refused.
- Not run on real Discord (discord.py isn't installed where this was written); the code was compile-checked.

## Step 50 (`.backup` and `.restore` now include smash/pass counts and everything about families)

- `.backup` also saves: how many times each person smashed and passed (`smashpass_stats.json`), and every family of the server: members and ranks, bank, description, role ids, war score and cooldowns (`families.json`). The family banners go in their own zip file(s) (`family-banners-...zip`), sent in the same DM after the .txt file.
- `.restore` loads them back: the counts replace the saved people's numbers (like reputation), families are loaded only in a server that has NO families yet (otherwise it is skipped with a note, delete them with `family purge` first), then the family roles / [TAG] nicknames and the Best family role are synced again. Banner zips are told apart from picture zips by their manifest, and only files named exactly like the bot's banners are written.
- Old backups (without these parts) still restore fine.
- Tested with stand-ins for the stores: pack -> encode -> decode round trip keeps counts, banks and next family id, damaged families are left out, the banner zip is recognised and a server not allowed is skipped. Not run on real Discord.

## Step 51 (`.family dep` / `.family with`, matchmaking in `.backup` and `.restore`, coinflip default 54%)

- `.family dep` works like `.family deposit` and `.family with` like `.family withdraw` (listed as aliases in `.help`).
- `.backup` now also saves matchmaking (`matchmaking.json`), and `.restore` loads it back. Matchmaking is global (people from every server), so:
  - the **bot owner's** backup has all of it: profiles (with date of birth), bans, the under-age list, blocks, rematch memory, reports (with the chat snapshot they carry), chats in progress, staff, the live-feed channel, daily lookups, totals, plus every server's off switch and sticky sign-up panels;
  - a **server owner's** backup only has that server's matchmaking off switch and sticky panels, never anybody's profile;
  - NOT saved: `matchmaking_log.jsonl` (the safety log with the relayed chat text). The bot deletes it after `MATCH_LOG_DAYS` days on purpose, a backup file would keep it forever.
- `.restore` adds to what is already there instead of replacing it: a profile in the backup replaces the same person's profile (a clashing friend id gets a new one), bans / staff / blocks / the under-age list are added to, reports are added when their id isn't there yet (`next_report` never goes down), chats in progress only come back when both people are free, the live-feed channel is only set when none is set, existing panels are kept, totals keep the higher number. Only the bot owner restores the global part; a server owner restoring a full backup only gets their own server's switch and panels (and is told the rest was skipped).
- Old backups (no matchmaking part) still restore fine, and old bots ignore the new part. If `matchmaking.py` didn't load, the matchmaking part is skipped with a note.
- Coinflip: the default win chance is now 54% (`COINFLIP_WIN_CHANCE`, was 55%; Step 44 still describes the older 55%). The start-up log shows it with one decimal.
- Testing: 68 checks run against the real functions (pulled out of `bot.py`, fake matchmaking data, no Discord): pack for bot owner / server owner, encode-decode round trip, restore into an empty and into a busy store, twice in a row (no duplicates), damaged and forged data. Not run on real Discord.

## Step 52 (offline / back online notices)

Why: when the bot stops (owner updates, high CPU) people type commands and nothing happens, with no word about why.

- A bot that is switched off can't talk, so there are two messages: **"Bot is going offline"** when it is stopped the normal way (host Stop / Restart = SIGTERM, Ctrl+C), and **"Bot is back online"** every time it starts again. After a crash or a kill for high CPU only the second one can be sent.
  - Going offline: "The owner is making updates, or the bot hit high CPU usage. I'll send a message here as soon as I'm back."
  - Back online: "I was offline because the owner was making updates or because of high CPU usage. The bot is back to work, you can use it now." plus a small grey line, `Offline for about 5m`.
- Where: ONE channel per server, the channel where a command was last used, and only servers with a command in the last `STATUS_NOTICE_HOURS` (24) before the bot went down. Nothing is sent on the very first start, and nothing for a restart faster than `STATUS_MIN_OFFLINE` (15 s).
- Anti-spam: at most one notice of each kind per `STATUS_NOTICE_GAP` (300 s), so a crash loop doesn't flood a channel. `STATUS_NOTICES=0` in .env turns it all off.
- New file `status.json` (next to the other data files): when the bot was last alive (written once a minute, tiny) and the last used channel per server. It is not part of `.backup`. On a brand new host there is no file, so the first start sends nothing.
- Code: the section "Offline / back online notices" in bot.py (`StatusStore`, `note_activity`, `announce_back`, `announce_shutdown`, `status_loop`), a SIGTERM handler in `setup_hook`, `announce_shutdown()` at the top of `WordfilterBot.close`, `announce_back()` started from `on_ready` and `note_activity(ctx)` in `defer_slash` (the shared before_invoke).
- The going-offline notice is limited to 6 seconds (all servers at once) because hosts only wait a few seconds after SIGTERM. A host that kills with SIGKILL gives no chance to say anything; the back-online notice covers that.
- Testing: 13 checks against the real functions with a fake Discord (who gets notices, text, duration, first start, quick restart, crash loop, one channel failing, once-only shutdown, full offline->back cycle, switch off). Not run on real Discord (discord.py can't be installed where this was written).

## Step 53 (family war is daily)

This REPLACES what Step 22 says about the war week (Monday to Monday) and the 10-minute check.

- The family war now runs one day at a time: it ends every day at 00:00 UTC (01:00 in Algeria). The family with the most score that day gets `WAR_BONUS` (500, set it in .env) for EVERY member, ties go to the older family, then every score resets to 0.
- What counts is unchanged: `,daily`, `,ratio` W and quest rewards add to the family's score. Duels, `,rob`, `,rep give`, gambling and the war bonus itself don't.
- `.family war` shows "Ends in ..." until midnight UTC and "Yesterday: <family> won with ...". The empty-board hint no longer lists `,duel` (duels never counted) and lists `,quests` instead. Texts like "weekly" / "last week's" in the help, the announcement and `.family war channel` say "daily" / "yesterday's".
- `war_loop` checks every 60 seconds (was 10 minutes) so the bonus lands right after midnight; the check is only a string comparison, no CPU cost.
- Code: new `war_day_id()` and `war_end()` (the war used `week_id()` / `week_end()` before; those stay for the WEEKLY QUESTS, which still reset on Monday 00:00 UTC). The data keeps the old key names (`war_week`, `war.week`) so families.json and old backups load unchanged.
- First start with this version: the old weekly period ("2026-W44") is closed like a finished war, so the family leading the unfinished week gets the bonus right away and scores start at 0 for today.
- Testing: war_roll / give_aura run with fake stores (old weekly data closes once, tie goes to the older family, nobody scored = no bonus, once per day, only gains count). Not run on real Discord.

## Step 54 (fixed status channel, `.gambling on/off`)

- The offline / back online notices (Step 52) now go to ONE channel: `STATUS_CHANNEL_ID`, default `1563262719124050021`. Change it in .env (a channel id; the bot must be in that server and be able to send embeds there). `STATUS_CHANNEL_ID=` (empty) goes back to "the channel where a command was last used in each server". With a fixed channel the very first start also posts "Bot is back online" (without the "Offline for ..." line), so you can see right away that it works. If the id is wrong the log says `Status notice: I can't see channel ...` and nothing else breaks.
- NEW `.gambling` (prefix `.`, needs Manage Server, so the server owner and admins can use it; also `/gambling`):
  - `.gambling off [#channel]` turns `,coinflip`, `,slots` and `,blackjack` off in that channel (the one you type it in when you leave the channel out). People who try get "Gambling is turned off in this channel." with no cooldown and nothing taken.
  - `.gambling on [#channel]` turns them back on. `.gambling` (or `.gambling list`) shows the state of this channel and every channel where it is off.
  - A thread follows the channel it belongs to. A blackjack game that is already running keeps going and pays out normally.
  - Saved in `gambling_off.json` (per server). It is not part of `.backup` / `.restore` yet, so after rebuilding on a new host run `.gambling off` again.
  - Code: `ChannelSwitch` / `gambling_off` and the check at the very top of `gamble_start` (all three games go through it), `gambling_command`, one entry in the help list and the Server category.
- Testing: the notice logic with a fake Discord (18 checks, now including the fixed channel, first start and an unknown channel) and the on/off store (per server, threads, survives a restart, a damaged file leaves gambling on). Not run on real Discord.

## Step 55 (the "gambling is off" message points to the gambling channel)

- When someone gambles in a channel where it is turned off, the message now ends with "Use it in #channel instead." The channel is `GAMBLING_CHANNEL_ID`, default `1557045887572906014` (change it in .env; empty = no hint). The hint is only added when that channel is in the same server as the command, so it never shows a broken `#deleted-channel` link.


## Step 56 (reputation shop, custom role, animated titles, bounties, role placement fix)

New files: `roleplace.py` (moves roles under an anchor role), `shopkit.py` (shop rules), `titlecard.py` (draws the animated titles), `fonts/` (the two fonts it needs).
`requirements.txt` now lists Pillow (the bot also installs it by itself; without it titles show as plain text).

### Role placement fix (family, Best family, Top 1 under Staff)
What was wrong:
- The Top 1 role was moved under Staff only ONCE, when the bot made it, with role positions the bot had cached (often stale right after a role is made).
- Family roles only moved when a base role was set by hand. With no base role they never went under Staff.
- Top 1, Best family and the family syncs could run at the same moment and undo each other's moves.
- Nothing ever checked again, so a role that was dragged away (or never moved) stayed wrong.
What it does now:
- `roleplace.place_under` reads FRESH positions from Discord whenever a move is needed, runs under one lock per server, and lets the bot's own roles sit next to each other under the anchor.
- Anchor = the role picked with `.roles staff @role`, else a role named Staff (any font), else the highest role with the word staff in it, else the lowest staff rank role. Family roles use the family base role when one is set, else Staff.
- A timer (`role_check_loop`, every ROLE_CHECK_SECONDS = 900) re-checks Top 1, Best family, every family role and every custom role. It also hands back expired bounties.
- IMPORTANT: Discord does not let a bot put a role at or above its own highest role. If the bot's role is BELOW Staff, nothing can be placed under Staff. The bot now says so ("drag my role above it") in `.roles` and `.roles fix` instead of failing silently. Fix: Server Settings > Roles > drag the bot's role above Staff (and above Soul).
- `.roles` (Manage Server): shows the Staff / Soul roles, where each bot role sits, and the last problems. `.roles fix` moves everything now. `.roles staff @role`, `.roles soul @role`, `.roles forget staff|soul` pin or unpin an anchor.

### Shop (`,shop`, `,shop buy <item>`, `,inventory`)
Everything is saved inside the person's reputation record (aura.json, key "items"), so `.backup` / `.restore` already carry it.
All prices and limits can be changed in .env (names at the top of bot.py, "Reputation shop settings").
| Item | Price | What it does |
|------|-------|--------------|
| Rob Shield | 1,000 | Nobody can rob you for 24h (stacks up to 72h). Robbing someone yourself ends your own shield. |
| Streak Freeze | 500 | Saves the daily streak when you miss a day. Used automatically, hold up to 3. |
| Quest Boost | 1,000 | The next quest claim pays double (up to +1,000 extra). Hold up to 3. |
| Custom Role | 15,000 | Your own role, right under the Soul role. |
| Animated Title | 5,000 | Any text with an animated effect on your `,rep` card. Changing it later costs 1,000. |
- `,daily` uses freezes (`shopkit.streak_after`), `quest_claim` uses boosts (it now returns 4 values), `,rob` checks shields and pays bounties.

### Custom role (`,myrole`)
- Bought in the shop. The role is made first and only paid for once it exists, so a failed purchase costs nothing. It has no permissions.
- Sits right under the Soul role (found by name in any font, or pinned with `.roles soul @role`). New custom roles also go right under Soul.
- Edit it like a booster role: `,myrole name <text>`, `,myrole color <hex>`, `,myrole icon <emoji or picture>` (the server needs Boost level 2), `,myrole delete`.
- Names can't be taken by another role, can't be staff-like (staff, owner, admin, mod, soul ...), can't contain mentions or links, and can't contain a wordfilter trigger word.
- Deleted by staff, or left the server? The role is made again for free with the saved name and color the next time they use `,myrole`.

### Animated titles (`,title`)
- `,title set <effect> <text>` (rainbow, flow, neon, glitch, gold, fire; no effect = rainbow), `,title color <hex>` (flow / neon), `,title remove`, `,title reset @member` (staff). The shop menu opens a form for the same thing.
- Discord can't animate text, so a title is a 480x96 looping GIF name plate, drawn ONCE when it is set (about 0.1 s, in a worker thread) and saved in `titles/`. Showing it later only sends the file, so there is no CPU cost. Files nobody uses are deleted.
- It is shown as the big picture on the `,rep` card. In `,rep top` the title text shows after the name.
- Text: up to 20 characters, Latin / Greek / Cyrillic and symbols. No emoji, Arabic or Chinese (they can't be drawn with these fonts). No links or mentions, no wordfilter words.

### Bounties (`,bounty`)
- `,bounty` lists them, `,bounty @member` shows one, `,bounty @member 500` puts 500 on them (minimum 100, plus a 10% fee that is gone for good).
- Whoever robs that member successfully collects every bounty on them (not their own). Unclaimed bounties go back to their owner after 3 days (without the fee).

### Tested
`tests/` is not in the zip. 152 checks were run against a fake server with the real discord.py code: role placement (300 random servers), shop, daily, quests, rob, bounty, titles, custom role, `.roles`, help entries and the slash command limits. Bugs found by them and fixed: a loop variable shadowing a helper in `quest_claim`, a constant name clash with the family code, and help text using prices before they were defined.
Not testable here: the real Discord server (hierarchy and permissions are only checked through `.roles`).


### Step 56b (role and title managed from inside the shop)
- `,shop` now has a menu row plus two buttons, **My role** and **My title**, so everything can be done without typing a command.
- **My role** (a private panel): Edit name / color (a form that starts with the current values) and Delete role (asks Yes / Cancel first). Picking Custom Role in the menu when you already own one opens this panel instead of buying again. A role someone deleted is made again for free when you open it.
- **My title** (a private panel): shows your animated title with Change title (the form starts with your current text, effect and color) and Remove title (asks first). Without a title the button opens the form to make one.
- The shop list marks what you own ("yours, press My role").
- The commands and the shop forms share one code path (`edit_role_core`, `delete_my_role`, `remove_my_title`, `apply_title`), so they follow the same rules (reserved names, wordfilter, prices, cooldowns).
- Role icons still need a picture upload, so they stay a command: `,myrole icon`.
- Tested with fake button presses and forms (`tests/`): 180 checks pass.


## Step 57 (shop as one card with Buy / Edit buttons, role and title editor panels)

New file: `shopstyle.py` (color styles, hex codes and emoji pages; no Discord needed). Changed: `bot.py`.
Needs discord.py 2.6 or newer (requirements.txt already says 2.7+), because the shop is now one Discord "container" card.

### The shop (`,shop`)
- One card that looks like an embed, with the `:dcshop:` emoji in the title. Every item has its own button right beside it:
  **Buy** while you don't own it. For the Custom Role and the Animated Title the button becomes **Edit** (with the `:update:` emoji) once you own it.
- The old dropdown and the My role / My title buttons are gone. A **Refresh** button at the bottom redraws the balance.
- The card redraws itself after a purchase, after you save or delete in an editor, and when you press Refresh. Only the person who opened it can press.
- Emojis: `shop` = server emoji `dcshop`, `update` = server emoji `update` (added to EMOJI_ALIASES). Without them a shopping bag and a refresh arrows emoji are shown.

### Role editor (Edit next to Custom Role)
A private panel. Nothing changes until you press **Save**; Save is free and is ONE edit on Discord.
- **Icon**: a dropdown of the server's emojis (25 per page, Back / Next buttons), with a **No icon** button. The emoji's picture becomes the role icon (needs the server at Boost level 2). Static emojis work best; Discord can refuse a very big or animated one, and the panel says so.
- **Color style**: Solid, Gradient or Holographic. Holographic uses Discord's fixed holographic colors, so it needs no hex codes.
- **Name & colors** button: a form with the role name, the main hex code and the second hex code (gradient). Typing a second code while the style is Solid switches it to Gradient. Hex codes like `#ff00aa`, `ff00aa`, `#f0a` and a few color words are accepted.
- Gradient and Holographic need the server perk "enhanced role colors". Without it the panel says so and nothing is sent to Discord. A gradient needs two different colors.
- **Reset** puts the panel back to what is saved. **Delete role** asks first (no refund).
- The look is saved with the member's shop record (`role_look`), so a role someone deleted is made again with its style and icon (free).
- `,myrole color` still works: it makes the role Solid and keeps the icon.

### Title editor (Buy / Edit next to Animated Title)
- A private panel with a **live preview** of the animated title: pick the **style** (rainbow, flow, neon, glitch, gold, fire) in the dropdown, press **Name & color** for the name (and the hex code for flow / neon).
- **Buy** (5,000) the first time, **Save** (1,000) when changing. Same rules as `,title set`: the picture is drawn before paying, cooldown, wordfilter, characters that can be drawn.
- Previews are drawn into a temporary file that is deleted at once (the last 40 are kept in memory), so they leave nothing in `titles/`. Only a saved title is kept on disk.
- **Reset** goes back to the saved title, **Remove** asks first (no refund).

### Tested
67 checks against a stand-in for discord.py (layout of the card, every button and form, paging over 60 emojis, saving the three styles, refusals, costs, previews leaving no files, `,myrole` commands with the new data). NOT tested here: the real Discord server, so the first run on your server is the real test. If the shop card doesn't show, check that discord.py is 2.6+.


## Step 58 (higher shop prices, bigger bets, family admins can withdraw)

- **Shop prices** (every one can still be changed in .env): Rob Shield 1,000 -> 3,000, Streak Freeze 500 -> 2,500, Quest Boost 1,000 -> 2,000 (and what it can add to one claim 1,000 -> 2,000, so it still pays for itself), Custom Role 15,000 -> 50,000, Animated Title 5,000 -> 15,000, changing a title 1,000 -> 5,000. The shop card, the editors and `.help` read the prices from these numbers, so they show the new ones by themselves.
- **Biggest bet** (coinflip, slots, blackjack) 10,000 -> 25,000 (`GAMBLE_MAX_BET`). The smallest bet and the cooldown are unchanged.
- **Family bank**: `.family withdraw` now works for family admins as well as the owner (before: owner only). Same rules as before: not more than the bank holds, the money goes to the person who withdrew. `.help` shows it as "Family admin".
- If your .env sets any of these names (GAMBLE_MAX_BET, SHIELD_PRICE, ...), the .env value wins over the new defaults.


## Step 59 (shop is an embed with categories, Edit opens a form, any emoji for the role icon)

- **Shop embed**: `,shop` is a normal embed now (author line, neutral theme color, balance line with the gold sparkle, footer), like `,quests` / `,inventory`. It replaces the old Discord container card (`ShopLayout`).
- **Categories**: a dropdown under the shop switches between **Upgrades** (Rob Shield, Streak Freeze, Quest Boost) and **Customization** (Custom Role, Animated Title). They live in `SHOP_CATEGORIES` (bot.py), so a new category or item only needs one line there. Every item shows what you hold under it; each has a **Buy** button that becomes **Edit** once you own the role / title. **Refresh** redraws.
- **Edit opens a form** (a popup with boxes and dropdowns, the same kind as matchmaking's *About You*), not a panel:
  - Custom Role: name, color style dropdown (solid / gradient / holographic), main color hex, second color hex, role icon.
  - Animated Title: name, style dropdown (rainbow, flow, neon, glitch, gold, fire), color hex (flow / neon). Pressing Submit is the purchase (first title) or the change (costs shown under the name box).
  - After a successful save the shop page redraws by itself.
- **Role icon = any emoji**: paste a normal emoji, a custom emoji from ANY server the bot is in (`<:name:id>`, or just `:name:`), or an emoji id. Empty box or `none` removes it. Normal emojis are sent to Discord as text, custom ones as the emoji picture (`emoji_icon_bytes` now falls back to any server and then to Discord's picture of the id). Still needs the server's ROLE_ICONS perk (Boost level 2).
- `shopstyle.py`: the saved look gets `uemoji` (normal emoji) next to `emoji` (custom emoji id); new `looks_like_emoji` and `parse_icon`.
- Removed: the old editor panels (`RoleStudio`, `TitleStudio`, `StudioBase`, `ConfirmAction`, live title preview, the emoji paging) and the unused one-box `TitleModal`. Deleting the role / removing the title is still `,myrole delete` and `,title remove`.
- `.help shop` text updated.
- NOT tested here: the real Discord server (discord.py was not installable in the sandbox), so only syntax and the emoji parsing were checked. Needs discord.py 2.6+ (forms with dropdowns).

## Step 60 (higher shop prices, Luck Boost, Half Loss, ,plinko)

- **Prices** (defaults in bot.py, each can still be set in .env): Rob Shield 3,000 -> 7,500, Streak Freeze 2,500 -> 6,000, Quest Boost 2,000 -> 5,000, Custom Role 50,000 -> 150,000, Animated Title 15,000 -> 40,000, title change 5,000 -> 12,000.
- **New shop category "Gambling"** with two items. The Buy button opens a small form that asks how many to buy; `,shop buy luck 3` / `,shop buy halfloss 2` do the same from chat. Each one bought adds 5 games, they stack (up to `BOOST_GAMES_MAX`, 100 games each) and can be bought again at any time, also after they ran out.
  - **Luck Boost** (`LUCK_PRICE` 20,000, `LUCK_GAMES` 5, `LUCK_WIN_CHANCE` 0.70): for those games ,coinflip ,slots and ,plinko are a win 70% of the time and a loss 30% of the time. It never makes a game worse than its normal chance (plinko on low risk already wins 73%, so it keeps that). It only raises the odds, it does not guarantee a win. Blackjack and ,duel are NOT affected.
  - **Half Loss** (`HALF_PRICE` 12,000, `HALF_GAMES` 5): for those games a lost bet only costs half of it (rounded up). Works in ,coinflip ,slots ,plinko. Not blackjack, not ,duel.
  - Every game played with a boost active uses up one game of it (win or lose). The stats line under the result shows `Luck Boost 4 left` / `Half Loss 4 left`. ,inventory and the shop card show the games left.
  - Saved in the member's shop items as `luck_games` and `half_games` (so .backup / .restore carry them).
- **,plinko `<amount> [low|medium|high]`** (medium if left out, `,plinko high 500` works too, also as `/plinko`): the ball falls through 8 rows (animated in 4 frames) into one of 9 slots that pay a multiplier of the bet. Low 2.5x 1.5x 1.2x 1.05x 0.4x..., medium 11x 3.2x 1.5x 0.6x 0.3x..., high 26x 4x 1.2x 0.4x 0.2x... (mirrored). Average payback about 94.5-96%, like the other games. Same bet limits, cooldown and `.gambling off` as coinflip and slots.
- Code: the rules are in `gambling.py` (`PLINKO_*`, `plinko_drop`, `half_loss`, `soften_loss`, `luck_decides_win`, `draw_matching`); bot.py has `boosts_start`, `boost_tags`, `luck_roll`, `buy_boost`, `BoostForm`, `plinko_command`.
- Fixed: the shop card printed the characters `\n` and `\u00b7` as text between items (doubled backslashes in `shop_embed`).
- Checked here: syntax, and the buying / stacking / using-up / odds logic with a fake store (coinflip and slots with a boost come out at 70%). NOT run against real Discord (discord.py was not installable in the sandbox): the shop form, the plinko animation and the slash command still need a test on a real server. The new `/plinko` slash command shows up after the bot syncs its commands (the same way the other slash commands do).

## Step 61 (plinko can't be farmed with boosts)

Problem: a Luck Boost made plinko medium / high return about 160-180% of the bet (the forced wins could land on the 11x / 26x edge slots), and with big bets a boost paid for itself many times over.
- **A Luck Boost never lands the ball in a slot above 5x** (`PLINKO_LUCK_MAX_MULT` in gambling.py), so the 11x and 26x edges only come up by normal luck.
- **Boosts only work on bets up to 5,000** (`BOOST_MAX_BET`, env var of the same name). A bigger bet is played normally and uses no game; the stats line says "Boosts only work on bets up to 5,000". This covers ,coinflip ,slots and ,plinko.
- Checked by simulation at the 5,000 cap: the average profit of one 5-game purchase stays below its price in every case (best case: plinko medium with Luck Boost about +11,500 for a 20,000 item; both boosts together about +12,100 for 32,000), so buying boosts again and again loses reputation on average.
- Raise `BOOST_MAX_BET` (or lower `LUCK_WIN_CHANCE`) in .env only together with `LUCK_PRICE` / `HALF_PRICE`.

## Step 62 (plinko is fair without a boost, a bit positive with one)

Goal: plain plinko pays back about 100% (a fair game, nobody has an edge), and the boosts give a small edge, which is why people buy them.
- **New payouts** (`PLINKO_TABLES` in gambling.py, mirrored to 9 slots). Unboosted payback is about 100% at every bet from 50 up:
  - Low: 2.5x 1.5x 1.25x 1.15x 0.4x. Payback 99.9%, wins 72.7% of the time. (before 94.5%)
  - Medium: 11x 3.5x 1.3x 0.7x 0.4x. Payback 100.5%, wins 28.9% of the time. (before 95.9%)
  - High: 26x 4x 1.4x 0.4x 0.25x. Payback 100.3%, wins 28.9% of the time. (before 94.5%)
  - Bets under 50 can come out a little under or over (payouts are whole numbers, wins round up and losses round down): about 97.7% to 103% depending on the exact bet.
- **A Luck Boost now never lands the ball in a slot above 3x** (`PLINKO_LUCK_MAX_MULT` 5 -> 3). On medium / high that leaves only the 1.3x / 1.4x slots as boosted wins, so a Luck Boost is worth about +8% per game there instead of about +45%. Low is not changed by a Luck Boost (it already wins 73%, more than the boost's 70%).
- **Boosted edge per game at a 5,000 bet** (average): Luck Boost medium +8.5%, high +8.3%, low 0%. Half Loss medium +3%, high +11.5%, low +2.7%. Both together medium +9.7%, high +13%.
- **Farming check** (`check_boosts.py`, new: exact expected profit of 5 boosted games at the 5,000 cap against the price): no boost pays for itself. Plinko: Luck Boost +2,100 for a 20,000 item (about 10%), Half Loss +800 to +2,900 for 12,000 (7-24%), both +2,400 / +3,250 for 32,000 (8-10%). The closest case in the whole bot is still coinflip with Half Loss (65% of its price), unchanged by this step. Run `python check_boosts.py` after changing any table, price or cap (the slots / coinflip / price numbers at the top of that file are copies from bot.py, keep them in sync).
- Also checked with 300,000 real `plinko_drop` calls per case: matches the exact numbers, and a boosted drop never pays more than 3x.
- `.help plinko` text and the "Gambling odds" log line at startup show the new numbers.
- NOT run against real Discord (discord.py was not installable in the sandbox): only syntax, the plinko rules and the odds were checked.

## Step 63 (shop: buttons inside the card, arrows to switch category)

- **The shop is now one card with the buttons inside it** (a Components V2 container, `ShopView` is a `discord.ui.LayoutView`; discord.py 2.7+ is already required). Discord embeds can't hold buttons, so the card replaces the embed + the buttons under it.
  - Top: `Reputation shop` and your balance.
  - Then a row: **left arrow, the category name with its place (`Gambling · 2/3`), right arrow, refresh**. The arrows switch category and wrap around (after the last one comes the first, and the other way round). The category name in the middle is only a label.
  - Below: every item of the category, with its **Buy button right next to it** (it says **Edit** and turns blue when you already own the Custom Role / Animated Title). Prices, texts and the grey "you hold" lines are the same as before.
- Removed: the category dropdown, the embed (`shop_embed`), the author line and the footer. A message made this way can't hold an embed or plain text, so `,shop` now sends only the card; `refresh_shop` (used by the role / boost / title forms) edits it the same way.
- Unchanged: who can press (only the person who opened it), the 3 minute timeout (all buttons, also the ones inside the card, are switched off), the forms, `,shop buy ...`, prices.
- `.help shop` text updated.
- Checked here with a stand-in for the discord.ui classes: building every category, the arrows (wrapping both ways), refresh, the timeout, and Discord's limits (about 15-19 components, well under 4000 characters of text). NOT run against real Discord (discord.py was not installable in the sandbox), so the real layout still needs one look on a server. If a message can't be edited into the card because it was an old embed shop from before this update, just run `,shop` again.

## Step 64 (,plinko is now a card with buttons and an animated board)

Goal: `,plinko` looks and plays like the reference video: one card, buttons inside it, a drawn board with the ball falling.
- **The card** (`PlinkoView`, a Components V2 `LayoutView` like the shop): `Plinko`, a line, `Bet` and `Risk`, a line, and the buttons **Set Bet, Low, Medium, High, Start**. The chosen risk is blue, the others grey, Start is green. Only the person who opened it can press.
  - `,plinko` / `,plinko 500` / `,plinko 500 high` / `,plinko all` / `,plinko high 500` (also `/plinko`). The amount and the risk are optional now (default bet = the smallest bet, default risk = medium). `all` / `half` show what you really have (never more than the biggest bet).
  - **Set Bet** opens a small form (`PlinkoBetModal`): `500`, `2k`, `half`, `all`. **Low / Medium / High** switch the risk. **Start** plays a drop.
- **A drop** (all in the same message): the card shows `Plinko - Bet: 16` with the risk, a drawn board (pegs, yellow ball, slot boxes: green = pays 1x or more, red = pays less) and the ball falls peg by peg (animated GIF) -> the same board with the landing slot outlined in yellow, `Bucket: 5/9 | Multiplier: 0.25x` and the result line `Landed in 0.25x. Returned 4, net loss 12.` -> back to the panel with that result under the risk. The bet on the panel is lowered to what you have left (16 -> 4 after losing 12), so the next drop is one press away.
- **Nothing about the money changed.** Payouts (`PLINKO_TABLES`), Luck Boost, Half Loss, the cooldown, bet limits, `.gambling off` and the balance check are the same code as before (`gamble_start`, `boosts_start`, `plinko_drop`); they run when Start is pressed, and the drop is paid before the first picture is shown. The pictures are only the show. If a picture can't be sent, the panel with the result still comes back. Boost words (`Luck Boost 4 left`) show in grey under the result when a boost was used.
- **Not copied from the video:** its high-risk slots (30x 3.5x 0.95x 0.3x 0x) pay back only about 79%. This bot keeps its own fair tables (step 62), the board just shows whatever the table says.
- New file **plinko_render.py** (Pillow only, no Discord): `render_gif(table, path)` and `render_png(table, slot)`. Run `python plinko_render.py` to write a test picture. Drawn with the bundled `fonts/DejaVuSans-Bold.ttf`.
- Code: bot.py `PlinkoView`, `PlinkoBetModal`, `plinko_command`, `plinko_gallery`, `plinko_result_text`; `gamble_start` now calls the new `gambling_off_note` (same text as before). The old text board (`plinko_board`, `plinko_strip`, `PLINKO_FRAME_SECONDS`) is gone. The amounts in the plinko texts use the reputation icon (`emo("aura")`, the same as ,reputation and the shop; `plinko_money()`), or the word `rep` if that emoji doesn't exist.
- Checked here: syntax, the renderer (GIF about 45 KB, 2.7 s), and the whole Start flow with a stand-in for discord.ui (panel -> falling board -> landed board -> panel, balance and bet clamping, refusal when you can't pay). NOT run against real Discord (discord.py was not installable in the sandbox): the card, the form, the media gallery and the picture edits still need a test on a real server.

## Step 65 (animated title shown next to names in the family card)

- The family card (`,family info`) now shows each person's Animated Title as text after their name (`『 title 』`, from `title_tag`), for the owner, the admins and the members. The reputation leaderboard already did this. A text list can't play the animation, so it is the plain text version; the animated picture stays on `,rep` and `,title`.
- `mention_list` also stops adding people when the field would go over Discord's 1024 character limit (titles make the lines longer) and says "and N more" instead.
- Checked: syntax only, not run against real Discord.

## Step 66 (shop: Duel Insurance, Lockpick, Vault)

- **Duel Insurance** (`DUELINS_PRICE` 8,000, `DUELINS_DUELS` 5; gambling category, buy several, they stack like Luck Boost / Half Loss): when you lose a `,duel` while insured, the bet is cut to half (rounded up) and the winner receives that same half, so no reputation is created. Each insured player uses up one duel when the duel is settled (win or lose). Only bets up to `BOOST_MAX_BET`; a bigger duel is played normally and uses nothing. The challenge card tells the other player who is insured before they press Accept. Saved as `duel_ins_games`. Code: `duel_insure()`, `DuelView.accept`, `duel`, entry in `BOOST_ITEMS`.
- **Lockpick** (`LOCKPICK_PRICE` 3,000, `LOCKPICK_MAX` 3, `LOCKPICK_CHANCE` 0.65): your next `,rob` works 65% of the time instead of `ROB_CHANCE` (45%). Used up by that rob, win or lose; never lowers the normal chance. Saved as `lockpicks`. Best case it adds about 600 expected on a maxed rob (`ROB_MAX` 1,500), far below its price.
- **Vault** (`VAULT_PRICE` 50,000, `VAULT_PERCENT` 50): bought once. A robber's share is taken only from the unprotected part of the balance (`vault_safe()` in `rob_command`); if what is left is under `ROB_MIN_BALANCE` the rob is refused. Works together with Rob Shield. Saved as `vault`; the shop button says Owned (disabled) once you have it.
- Shop categories are now: Upgrades (Streak Freeze, Quest Boost), **Rob and protection** (Rob Shield, Lockpick, Vault), Gambling (Luck Boost, Half Loss, Duel Insurance). Rob Shield moved from Upgrades.
- `,shop buy` takes `lockpick`, `vault`, `duelins` (aliases pick, insurance, duel). `,inventory` and the `.help` texts for shop, shop buy, duel and rob are updated. All numbers can be set in .env (names above).
- Checked: syntax only. NOT run against real Discord: the new shop page, the duel card line and a rob with a Vault / Lockpick need a test on a server.


## Shop additions: Daily Boost, Quest Reroll, Leaderboard Badge

- **Daily Boost** (800, hold up to 3): the next `,daily` pays double, up to 300 extra. Used by itself when you claim. `.env`: DAILYBOOST_PRICE, DAILYBOOST_MAX, DAILYBOOST_CAP.
- **Quest Reroll** (2,500, hold up to 3): `,quests reroll <number>` swaps one unfinished quest for a new one (starts at 0). Quests are numbered on the card while you hold a Reroll. `.env`: REROLL_PRICE, REROLL_MAX.
- **Leaderboard Badge** (5,000, change 1,500): an emoji or up to 6 plain letters shown after the name on the leaderboards (also family lists), via the shop's Customization page or `,badge <text>`. Custom emojis must be from a server the bot is in; the word filter applies. `.env`: BADGE_PRICE, BADGE_CHANGE_PRICE, BADGE_MAX_CHARS.
- Also in this version: boost max bet raised to 10,000; `all` bets are silently capped at the max bet instead of showing the limit.


## Step 67 (join log: invite tracker, full member profile, name history, alt check)

- New file `invitetracker.py`, loaded from `setup_hook` in bot.py (a failure there never stops the rest of the bot). Data is saved in `invites.json` in DATA_DIR.
- Only command: `.invitelog #channel|off` (Manage Server). Every join posts up to 4 embeds there: the member (names, ID, account age, badges, avatar / banner links, roles, flags, screening / timeout), how they joined (invite code, owner, uses, expiry, channel, vanity URL, added bot), the alt check, and name history + extras (mutual servers, elevated permissions, join history).
- How the inviter is found: the bot keeps every invite's use counter and compares after each join. A limited invite that vanishes on its last use is shown as "probably". Two people joining in the same instant are shown as "unknown".
- Name history: Discord gives bots NO old usernames. The bot records usernames, global names, nicknames and avatar hashes it sees (all members are saved at startup, changes live through on_user_update / on_member_update). History starts when this version is installed.
- Alt check: a score from account age, default avatar, auto-generated name, same avatar as another known / banned user, same or similar name as a banned / past / current member, accounts made within 3 minutes of each other, several people on one invite in 15 minutes, and an inviter with banned invitees. 0-1 normal, 2-3 slightly suspicious, 4-6 possible alt, 7+ likely alt. These are hints, never proof.
- Needs the bot permissions Manage Server (invites) and Ban Members (ban list). Without them those parts are skipped and the profile says so.
- Checked: syntax, and the invite detection + alt scoring with a mock. NOT run against real Discord.


## Step 68 (join guard: staff buttons, quarantine, raid alert, leave log, early watch)

- `invitetracker.py` now also posts, under every join profile, a row of staff buttons: **Approve** (only when the member was quarantined), **Timeout 24h**, **Kick**, **Ban** (kick and ban ask "Really?" first) and **Dismiss**. They are `discord.ui.DynamicItem` buttons (custom id `jl:<action>:<user id>`), so they keep working after a restart. Each button checks that the person pressing it has the matching permission, and refuses the server owner and anyone whose top role is not below the presser's.
- New command `.joinguard [setting] [value]` (Manage Server) with: `raid off|alert|lockdown` (8 joins in 30 s -> alert; lockdown also raises the verification level to the highest for 10 minutes, then restores it, also after a restart), `quarantine @role|off` (new accounts under `minage` days, and alt score 4+, get the role until Approve), `minage <days>`, `autotimeout on|off` (24 h timeout at score 7+, off by default), `earlywatch on|off`, `leavelog on|off`. Settings are saved per server in invites.json (key `guard`).
- Early watch: for the first hour after joining, a member's messages are checked for an invite link to another server, Nitro / gift scam links and mass pings (5+ mentions or @everyone). The bot only alerts (with the same buttons), it never deletes anything. Links are shown inside a code block so they can't be clicked.
- Leave log: who left, was kicked or banned (and by whom, with the reason, from the audit log), how long they stayed, roles, invite, alt score and first message time.
- The alt check gets +2 for joining during a raid alert. Each member record also keeps `score`, `verdict`, `first_msg`.
- Needs the bot permissions Manage Server, Ban Members, Kick Members, Moderate Members, Manage Roles (quarantine role must be BELOW the bot's role) and View Audit Log. A missing permission only skips that part.
- Checked: syntax and a mock test of the button permission / hierarchy rules and the raid counter. NOT run against real Discord (buttons, timeouts, bans, role changes and verification-level changes need a test on a spare server).


## Step 69 (boosts work on every bet, up to 25,000)

- Request: Luck Boost wasn't worth its price. Raised the boost work limit to the biggest allowed bet.
- `BOOST_MAX_BET` default 10,000 -> **25,000** (bot.py, same value as `GAMBLE_MAX_BET`). Luck Boost, Half Loss and Duel Insurance now work on bets up to 25,000, so every ,coinflip ,slots ,plinko bet is boosted. The shop text, `,help`, the duel notice and the stats line read the limit from this number, so they show 25,000 by themselves. `.env` can still override it (`BOOST_MAX_BET`).
- `check_boosts.py`: `BET` changed to 25000 to match (re-run it after changing the limit).
- Effect (exact expected profit of one 5-game purchase at a 25,000 bet, from `check_boosts.py`): Luck Boost coinflip +50,000 (price 20,000), slots +38,000, plinko medium +10,700, plinko high +10,300, plinko low about 0. Half Loss coinflip +38,800 (price 12,000), slots +24,900, plinko high +14,400.
- WARNING: coinflip and slots with Luck Boost / Half Loss now pay for themselves at the top bet (the check prints FARMABLE for those rows; coinflip Half Loss was already above its price at 10,000). To tone it down later: lower `BOOST_MAX_BET` / `LUCK_WIN_CHANCE` in .env or raise `LUCK_PRICE` / `HALF_PRICE`. Plinko Luck Boost is still below its price (about 52%): `PLINKO_LUCK_MAX_MULT` in gambling.py (3) is what holds it back.
- Checked: syntax (py_compile) and `check_boosts.py`. NOT run against real Discord.


## Step 70 (`,goonlock`)

- Request: a lock command that rewrites someone's messages, with a `*nuts* 💦` tag. NOTE: this project had no `,uwulock` / `,gaylock`, so `,goonlock` is built on the wordfilter's webhook repost (delete the original, repost it with the same name and avatar).
- (Step 70 first shipped this as a toggle, `,goonlock @member`. Step 71 below turned it into add / remove / list.) Needs **Manage Messages** (or server owner / bot owner). Bots can't be locked. The server owner and anyone with an equal or higher top role can't be locked by someone below them (the server owner and bot owner can lock anyone). Saved in `goonlock.json` (`{server: {user: time locked}}`), so locks survive a restart. Uses the `,` prefix (`FUN_COMMANDS`), so `+prefix goonlock !` works too.
- What a locked person's message becomes (`goonify`): their own words with one tag at the end, plus up to 2 more (50% chance per sentence end) inside longer messages, never the same tag twice in one message. Messages with markdown (`* _ ~ ` | [`) only get the tag at the end, so bold / code / spoilers / links never break. Numbered lists (`1. `) and decimals (`3.14`) are not treated as sentence ends. A message that is too long is cut so the tag still fits in Discord's 2000 characters; an unclosed ``` block gets closed first. Picture/file with no text just gets the tag.
- Edit the `GOON_TAGS` tuple (bot.py, next to `goonify`) to change the wording; `GOON_EXTRA_TAGS` / `GOON_EXTRA_CHANCE` set how many extra tags long messages get.
- Left alone on purpose (`goon_applies`), so a lock never makes anything disappear: stickers, polls, voice messages, forwards, and attachments the bot couldn't copy (more than 10, or over the server's upload limit). Commands typed by a locked person still work (and so do `,goonlock` to unlock themselves if they have Manage Messages).
- If a message matches a wordfilter word AND the author is locked, it is reposted once with both applied.
- Code: `GoonLockStore` / `goon_locks`, `GOON_TAGS`, `goonify`, `goon_applies` (after `repost`), the 2 changed lines in `repost` / `on_message`, `goonlock_check` + the `goonlock` command (after `tightest`), plus the help entry, `HELP_EXAMPLES` and `_ORDER`.
- Needs the same bot permissions as the wordfilter: Manage Messages and Manage Webhooks in the channel.
- Checked: syntax (py_compile), every global name used by the new code exists, and `goonify` + the store were run in isolation (length limits at 1 to 4000 characters, markdown, code fences, numbered lists, tag never repeated, persistence, corrupt file). NOT run against real Discord (discord.py wasn't installable here): lock a test account and try a reply, a picture, a link and a long message.


## Step 71 (`,goonlock add` / `remove` / `list`)

- Request: put the lock on with `,goonlock add @member` and take it off with `,goonlock remove @member` (instead of running the same command again to toggle).
- `goonlock` is now a command group like `wordfilter`: `,goonlock add <member>`, `,goonlock remove <member>` (alias `delete`), `,goonlock list` (who is locked here). `,goonlock` on its own shows the help pages. `,goonlock @member` no longer locks anyone, it just shows the help.
- `add` says so when the member is already locked, and `remove` says so when they aren't locked (no more silent toggling). The rules are the same as before: Manage Messages (or server owner / bot owner) on every subcommand, bots can't be locked, and the server owner or someone with an equal / higher top role can't be locked by someone below them.
- Slash commands: `/goonlock add`, `/goonlock remove`, `/goonlock list`, `/goonlock help`. Still the `,` prefix (`FUN_COMMANDS` matches the root name, so `+prefix goonlock !` moves all of them).
- `+help goonlock` is a group page (`HELP_GROUPS`) with the 3 subcommands; `HELP_EXAMPLES` has an example for each. A missing member on add / remove shows the example from the help entry.
- Nothing else changed: the lock store (`goonlock.json`, existing locks keep working), `goonify`, `goon_applies` and the repost hook are the same as Step 70.
- Checked: syntax, undefined names, the help entries and decorators (parsed, not run), and the text / store tests again. NOT run against real Discord.


## Step 72 (`,goonlock` replies in the bot's status-line style)

- Request: the embed shown when someone is added / removed should match the bot's embed style.
- Before, the add reply was a long two-sentence line with a raw emoji and a literal `@member` in it. Now `add` / `remove` use the same short, verb-first status line as `,bounty`, `.title remove` and `.wordfilter add`: `+ @mod: put @jay on **goon lock**.` (green bar, bl_add icon) and `- @mod: took @jay off **goon lock**.` (bl_remove icon).
- The warnings use the same wording: `@jay is already on **goon lock**.`, `@jay is not on **goon lock**.`, `Bots can't be put on **goon lock**.`, and the hierarchy refusal. The empty list says `Nobody is on goon lock yet. Add someone with ,goonlock add (member).` (the `(member)` placeholder style of the wordfilter hints). The list embed header is now **On goon lock** (n).
- Only text changed: no logic, storage or permission rules. All in `goonlock_add` / `goonlock_remove` / `goonlock_list` (bot.py).
- Checked: syntax and undefined names. NOT run against real Discord.


## Step 73 (`,goonlock` for staff too)

- Request: anyone with the Manage Messages permission OR a staff role can use `,goonlock`.
- New `goonlock_can_use(member)` (bot.py, before `goonlock_check`). It lets in: Manage Messages, **staff** (the same people `,collect` and `sop add` count, `collect_reason(member) == "staff"`: server owner, Administrators, a role named Staff, and the staff ranks Owner / Co Owner / Admin / Moderator / Helper / Partnerships), and whoever has the Staff role picked with `.roles staff @role` (`staff_anchor`, never @everyone). The server owner and bot owner always pass (`has_full_access`).
- Applies to every subcommand (`add`, `remove`, `list`) through `goonlock_check`. Someone who is not allowed gets: "Only **staff** (the Staff role) or members with the **Manage Messages** permission can use this."
- Unchanged: the role-hierarchy rule on `add` (nobody can lock someone whose highest role is equal to or above theirs, except the server owner / bot owner), bots can't be locked, and the bot itself still needs Manage Messages + Manage Webhooks in the channel. The help pages say "staff / manage messages".
- Checked: syntax, undefined names, and the permission logic with fake members (Manage Messages, staff by name, the pinned Staff role, a pinned @everyone, a plain member, a non-member). NOT run against real Discord.


## Step: `,ask` AI assistant (askai.py) - v2 general assistant (21 tools, web search, memory, confirm buttons; ASK_CONFIRM, ASK_WEB)
- New file askai.py (loaded in setup_hook). `,ask <anything>` sends the sentence to Claude with tools: search_messages (saved log), member_info, joins_since, timeout_member / remove_timeout (confirm button).
- Needs ANTHROPIC_API_KEY in .env. Optional: ASK_MODEL, ASK_ACCESS (owner|staff), ASK_COOLDOWN.
- Logs server messages (text, no bots) to askai_messages.db. `,askbackfill [amount]` / `,askbackfill all [amount]` imports old history.
- bot.py: "ask", "askbackfill" added to FUN_COMMANDS (prefix `,`), extension load added after invitetracker.
- askai.py v3: run_code (bot owner only, shows the code, always needs Confirm, 90s limit, secrets redacted from output, NOT sandboxed) and read_bot_source (search the bot's own .py files) so the AI can do things that have no dedicated tool. ASK_CODE=0 turns it off. Also dm_member, remember_alias/forget_alias, font-proof name matching.
- askai.py v4 access tiers: everyone = general questions only (chat + web search, no tools, 15s cooldown, 20/day); Administrator permission / server owner = every server tool; bot owner = also run_code + read_bot_source. Env: ASK_ACCESS (admin|staff|owner), ASK_PUBLIC, ASK_PUBLIC_COOLDOWN, ASK_PUBLIC_DAILY.
- askai.py v5: the SERVER OWNER now gets the same power as the bot owner in `,ask` (incl. run_code + read_bot_source). ASK_OWNER_CODE=0 turns that off, ASK_CODE_GUILDS=id,id limits it to the listed servers.

- Renamed `,ask` to `,assist` (and `,askbackfill` to `,assistbackfill`). The file is still askai.py and the .env settings still start with ASK_.
- askai.py v6: pictures. The log now keeps picture metadata (who/when/where, not the picture). New tools find_images, view_images (the AI looks at up to 6 pictures), forward_message (repost). Only channels the asker can read.
- askai.py v7: normal members can search message + picture history (search_messages, recent_messages, find_images, view_images) but ONLY channels they can read themselves and never deleted messages (deleted ones are flagged, still visible to admins). Edits are saved. ASK_PUBLIC_HISTORY=0 turns it off. Risky regex patterns are refused.


## Step: `,assist` can run on a FREE model (askai.py)

- Request: use a free model instead of the paid Anthropic API.
- New: if there is no `ANTHROPIC_API_KEY`, `,assist` uses the first free key it finds in `.env`: `GROQ_API_KEY` (console.groq.com), `GEMINI_API_KEY` (aistudio.google.com) or `OPENROUTER_API_KEY` (openrouter.ai). `ASK_PROVIDER` picks one on purpose, `ASK_FREE_MODEL` overrides the model name. All the same tools and permission checks as before.
- Free mode has no web search and can't look at pictures (the free models are text only). Everything else is unchanged; the Anthropic path is untouched.
- New functions `_pick_provider` and `_ask_free` (askai.py). Checked: syntax. NOT run against the real APIs.


## Step: `,assist` can look up people who LEFT the server (askai.py)

- Request: let `,assist` do more, e.g. "when did kyler leave, how many messages did he send, who did he last reply to, his last pic, DM him to rejoin".
- New tools: `user_lookup` (id, message count, first / last message, pictures, when they left), `leave_log` (who left recently), `last_messages`, `reply_history` (who a person replied to). `search_messages`, `find_images` and `dm_member` now also work for people who left (by name from the log, or by id).
- New saved data (askai_messages.db): a `leaves` table (filled by an `on_member_remove` listener) and a `reply_to` column on messages. Leaves are only known from now on; reply info exists for new messages, and running `,assistbackfill` again fills it in for old ones.
- `dm_member` on someone who left tries the user directly; Discord often refuses DMs from a bot to someone who shares no server with it, and then it says so.
- Same permission checks as before: the new tools are for admins only, never for normal members.
- Checked: syntax, plus the lookups against a test database with stand-in Discord objects. NOT run against real Discord.


## Step: `,assist` replies in the bot's embed style (askai.py)

- Request: make `,assist` look clean and match the other embeds.
- Answers are now an embed with the theme bar (the color picked in `.customize`) and the small avatar + name line on top, the same look as the meters (`theme()` + `with_author()` from bot.py).
- Confirm prompts (kick / ban / DM / run code ...) are a yellow warn status embed with the Confirm / Cancel buttons; "Only admins can use this", "Wait 4s", the daily limit and the `,assistbackfill` messages use the usual green / red / wait status lines (`status_embed()` from bot.py).
- Only the look changed: no logic, tools or permissions. If bot.py's helpers can't be reached it falls back to a plain embed.
- Checked: syntax. NOT run against real Discord.


## Step: `.backup` / `.restore` carry the `,assist` history

- Request: add the assist history and everything related to it to the backup command.
- `.backup` now also sends zip file(s) named `assist-history-<date>-partN.zip` with: the saved message log (text, who, where, deleted flag, who each message replied to), the picture records (who sent which picture, when, the link source stays on Discord), the leave log (who left, when, their roles) and the nicknames taught with `,assist`. The big message log is split into parts that fit a DM (`BACKUP_PART_MB`, like the pictures). The text in the DM says how many of each were saved.
- A server owner's backup has this server's assist history; the bot owner's `.backup all` has every server's. Keep the files private: they hold the saved chat.
- `.restore` reads those zips (attach them with the .txt, all at once or one at a time). It only ADDS what is missing: nothing already saved is overwritten, so restoring the same file twice adds nothing, and a server owner only gets their own server's part. Damaged or odd rows are skipped, and the summary lists what was added.
- If `askai.py` isn't loaded, `.backup` just leaves this part out and `.restore` says the assist history was skipped.
- New in askai.py: `backup_counts`, `backup_iter`, `restore_zip`. New in bot.py: `backup_zip_is_assist`, plus the extra lines in the two commands and their help text.
- Checked: syntax, and a full export / wipe / restore round trip on a test database (counts, deleted flags, replies, nicknames, splitting into parts, a second restore adding nothing, junk rows skipped). NOT run against real Discord.


## Step: `,assist` pings and pictures are normal messages, not embeds

- Request: not every reply should be an embed; a ping or a picture must be a normal message.
- Rule (askai.py, `_send_answer`): the answer is an embed, EXCEPT when it contains a mention (`<@id>`) or a link. Then it is sent as a normal message, so a ping really pings and links / message previews show the way Discord shows them (embeds never ping and never unfurl links).
- Safety: only admins can make the bot ping, and only real members of this server, at most 5 per reply, never roles or @everyone. For normal members a mention is shown but never pings.
- The AI is told to write the `<@id>` mention when asked to ping someone, and to use `forward_message` to show a picture (that already posts the picture as a normal message).
- Checked: syntax, and the embed / plain choice and the ping allow-list with stand-in objects. NOT run against real Discord.


## Step: `,assist` free-AI fallback (v18)

- Problem: the free AI replied "The free AI is busy / out of free requests" on the very first `,assist`. Cause: Groq's free tier allows only ~8K tokens per minute for the default model, and one owner-level request (34 tool definitions + system prompt + a 3000-token reply reserve) is about that big, so Groq answered HTTP 429.
- New free providers in `FREE_PROVIDERS` (askai.py): `mistral` (`MISTRAL_API_KEY`, default model `mistral-small-latest`) and `nvidia` (`NVIDIA_API_KEY`, default `openai/gpt-oss-20b`). Order in auto mode: mistral, gemini, nvidia, groq, openrouter. Gemini default is now `gemini-3.5-flash-lite` (the old default `gemini-2.5-flash` was shut down by Google on 2026-10-16, which made Gemini answer HTTP 404).
- Automatic fallback (`_ask_free_chain`): if a provider is rate-limited (429/413), down (5xx/timeout/no connection) or rejects the request (400/401/403/404/422), the bot tries the next provider that has a key. It only falls back BEFORE any tool ran, so an action (mute, DM, announce...) is never repeated. If a provider fails after a tool already ran, the user is told to check before retrying.
- Smaller reply budget for free providers: `max_tokens` 3000 -> 1200 (owner/admin) and 1500 -> 600 (normal members).
- Model names: `ASK_MODEL_<PROVIDER>` (e.g. `ASK_MODEL_GEMINI`) per provider. `ASK_FREE_MODEL` applies to the provider chosen with `ASK_PROVIDER`, or to the only free key present.
- Checked: syntax, and the fallback chain with simulated provider responses (429 -> next, network error -> next, all busy -> final message, model overrides). NOT run against the real provider APIs.


## Step: smarter `,assist` on free models (v18b)

- **Pinged a user called "me"**: the prompt never told the model who the asker is. Now the system prompt has the asker's id and `<@id>` mention plus a rule that "me / my / I" always means the asker; `send_message` also turns a written `@me` / `<@me>` into the asker's real mention.
- **Changing a role color created a new role**: there was no tool to edit a role. New tools `edit_role` (color as hex or basic name, name, hoist, mentionable) and `edit_channel` (rename, topic); `create_role` / `create_channel` now say they are only for brand-new ones.
- **"Run this code?" prompts**: the prompt pushed the model to `run_code` whenever no tool fit. For free providers `run_code` and `read_bot_source` are now hidden unless `ASK_FREE_CODE=1`, and the prompt says to use the dedicated tools first. Claude (Anthropic key) is unchanged.
- **No web search on free models**: new `web_search` tool using Tavily (`TAVILY_API_KEY`, 1000 free searches a month) or Serper (`SERPER_API_KEY`). Capped at `ASK_SEARCH_DAILY` (default 30) searches a day for the whole bot. Normal members can use it too. With an Anthropic key the built-in Claude web search is still used.
- New tool `current_time` (any country, city or zone, no key needed). `tzdata` added to requirements.txt.
- Mistral default model is now `mistral-large-latest` (better at tools than small).
- Checked with simulated tests only (colors, @me, time zones, Tavily/Serper request + parsing, daily cap, role edit, tool lists per provider and level, public gating). NOT run against real Discord or the real search APIs.

## Step: `,assist` gets smarter (v18c)

- **Bug fixed:** the free-model prompt still ended with "You have NO web search in this mode", which made the bot refuse even with the new `web_search` tool. It now only says that when no search key exists.
- **Temperature 0.2** on every free-model request (the default 0.7-1.0 makes small models sloppy with tool calls).
- **Tool routing** (`ASK_TOOL_ROUTING=0` turns it off): the model only sees the tools that fit the request (roles, channels, moderation, messaging, lookups...) plus a few always-on ones, 11-20 instead of 36. Nothing recognised (another language, "yes do it") keeps every tool. Normal members and the Anthropic path are unchanged.
- **Duplicate guard:** `create_role` / `create_channel` refuse when one with that name already exists and tell the model to use `edit_role` / `edit_channel`.
- **Broken tool arguments** (invalid JSON) are no longer run with empty arguments: the model is told and retries.
- **Prompt examples** for the common requests (ping me, change a role color, mute, join date, time) using the asker's real id, plus "ask ONE short question when the request is unclear".
- **NVIDIA** default model is now `openai/gpt-oss-120b` (free on build.nvidia.com, strong at tool calls); provider order is mistral, nvidia, gemini, groq, openrouter.
- Checked with simulated tests only, not against the real services.

## Step: `,assist` survives retired models and any provider error (v18d)

- **Problem:** `,assist` answered "The AI service returned an error (410)". NVIDIA answers HTTP 410 Gone when it retires a hosted model, and the fallback only skipped to the next provider for a few error codes (429, 5xx, 400, 401, 403, 404, 422), so a 410 stopped everything.
- **Any** error before something was done (any non-200, no connection, empty answer) now moves on to the next model / provider. After an action already ran, the error is reported instead of trying another provider (so nothing is done twice).
- **Model lists** (`FREE_MODELS` in askai.py): when a model is gone (404 / 410) or answers nothing, the next model of the same provider is tried before the next provider. NVIDIA now starts with `nvidia/nemotron-3-ultra-550b-a55b`, then `nvidia/nemotron-3.5-lightning-30b-a3b`, then `openai/gpt-oss-120b`. `ASK_MODEL_<PROVIDER>` accepts several models separated by commas.
- **Nemotron** gets `chat_template_kwargs: {thinking: false}` so it does not spend the whole reply budget thinking (retried without it if NVIDIA rejects the field).
- **When everything fails, staff now see why** per provider and model, using the provider's own message, e.g. `mistral/mistral-large-latest: error 401 (Unauthorized); nvidia/...: error 410 (... end of life ...)`. Normal members only see "The free AI is busy right now".
- Checked with simulated provider responses (401, 410, 429, retired model, thinking switch rejected, empty answer, error after a tool ran). NOT run against the real services.

## ,assist smarter (v19)

Problems seen in the chat screenshots: `,assist ban massi when he says moha` was answered with "I can't set up automatic bans... you'd need a moderation
bot" (assist had no tool for it and didn't know the bot's own features), and `,assist get me the clip` / `web search it` only got "search it on YouTube
yourself" instead of the clip.

- NEW tools in askai.py: `add_auto_rule` (when someone says a word -> ban / kick / timeout / delete, saved in `askai_rules.json`, asks Confirm first),
  `list_auto_rules`, `remove_auto_rule`. The cog checks every message (`_run_rules`); owners, admins and people above the bot are skipped. Whole-word match
  that ignores fancy fonts/case. Rules for "anyone" that ban/kick are only allowed for the server owner. Max 50 rules per server.
- NEW tool `find_gif`: searches Tenor/Giphy (keys in .env) and posts the gif itself. Everybody can use it. Hidden when no key is set.
- System prompt: assist now knows the bot's commands/prefixes, that `.wordfilter` only reposts, to use add_auto_rule instead of refusing, to post real links
  (YouTube etc.) from web_search instead of telling people to search, and to answer jokes in the same casual tone.
- Tool routing: "when he says ..." / "whenever" / "rule" and "gif / clip / video / meme" now select the right tools for the small free models.

## ,assist gets the missing tools (v20b) - new file `askai_more.py`

Trigger: the bot listed "Things I can't do (no tool exists)". Everything on that list that Discord lets a bot do now has a tool. `askai.py` loads
`askai_more.py` right before `TOOLS` is built (`REG.update(askai_more.build(globals()))`); if that file is missing or breaks, the old tools keep working
(the error is logged).

NEW tools (17): `manage_emoji`, `manage_sticker` (list/add/rename/delete; source = link, attached or replied-to picture, or another server's `<:name:id>`;
pictures are shrunk with Pillow to fit Discord's limits), `manage_thread`, `voice_member` (move/disconnect/mute/deafen one person or a whole channel),
`edit_voice_channel` (bitrate, user limit, region), `manage_stage`, `manage_event` (scheduled events), `edit_server` (name, description, icon, banner, splash),
`manage_integrations` (webhooks + integrations; webhook URLs are DMed to the asker and never printed), `audit_log`, `set_channel_permissions`,
`set_role_permissions` (never touches Administrator), `prune_members` (counts first, then Confirm), `message_action` (delete ANY message incl. other bots',
pin, unpin, react, unreact, edit the bot's own message), `manage_automod` (Discord's built-in AutoMod), `export_data` (channel dump or server structure, sent by DM),
`boost_info` (read only: level, boosters, vanity URL).

Safety: same `gate()` (asker AND bot need the permission), hierarchy checks and Confirm buttons as the old tools. New confirm names are added to `RISKY`
(emoji_delete, sticker_delete, thread_delete, event_delete, webhook_create, webhook_delete, integration_remove, perms_change, prune_members, automod_change,
server_edit, message_delete). Image links must be public http(s) addresses (private/loopback IPs are refused, redirects re-checked, size capped).
The tools are also added to `ACTION_TOOLS` and to the free-model tool routing (`ROUTE_GROUPS` in askai_more.py) and the system prompt (`PROMPT`).

Still impossible for any bot (the prompt tells the model to say so in one sentence): edit other people's / other bots' messages (deleting works), read DMs,
change someone's global profile or nickname outside this server, change the boost level, set the vanity URL, discovery settings, upload/manage other bots' applications.

Checked only with a stubbed discord module (the file compiles, every tool schema is valid JSON, the routing regexes pick the right tools). NOT run against real Discord:
needs a real server test, and the bot needs the matching permissions (Manage Expressions, Manage Threads, Move/Mute/Deafen Members, Manage Events, Manage Webhooks,
View Audit Log, Manage Roles/Channels, Kick Members, Manage Messages, Manage Server).

## ,assist second batch of tools (v21) - new file `askai_more2.py`

Loaded by askai.py right after askai_more.py (`REG.update(askai_more2.build(globals()))`); if it fails, the rest keeps working (logged). `setup()` starts the
reminder loop, `AskAI.cog_unload` stops it.

NEW tools (16): `member_history` (avatar history = "last pfp", old usernames / display names / nicknames, who invited someone, top inviters; it READS invitetracker.py's
records (`invitetracker.store`), so history starts when the tracker first saw a person), `send_file`, `send_embed` (+ link buttons), `send_poll`,
`reminders` (DM reminders for everyone, channel pings / scheduled posts for admins; table `reminders` in askai_messages.db, checked every 15 s, survives restarts),
`manage_warnings` (table `warns`: warn / note / list / remove / clear, DMs the member), `list_bans`, `manage_invites`, `manage_layout` (categories, move / reorder
channels and roles, NSFW, sync permissions, clone), `aura` (balance / top / items / give / take / set via bot.py's `auras` + `give_aura`, Confirm for changes),
`shop_info` (all `*_PRICE` constants + what someone owns), `family_info` (read only), `edit_image` (Pillow), `generate_image` (free Pollinations service by default,
change with IMAGE_GEN_URL in .env; explicit / gore prompts are refused), `music` (join / play / pause / resume / skip / queue / volume / stop / speak),
`lookup` (weather, crypto, currency, Wikipedia, dictionary, country: free APIs, no key).

Everybody (not only admins) may use `lookup` and `reminders` (only for themselves, max 10 pending). Everything else is admin-level like before.

Music needs `pip install PyNaCl yt-dlp` (+ `gTTS` for speak) and ffmpeg on the host (see requirements.txt). Streaming YouTube audio with yt-dlp can go against YouTube's terms.
New confirm names in RISKY: aura_change, warn_clear, layout_change, invite_delete (+ the ones from askai_more.py).

Left out on purpose: matchmaking data (private, includes ages), quests (no stable API to write to), warn auto-escalation, buttons that need a callback (only link buttons).
Not possible: Instagram / TikTok / X posts, stock quotes (use web_search), joining a voice channel without the libraries above.

Tested with a stub discord module, a real SQLite db and real Pillow: reminders (add / list / cancel / delivery loop), warnings, aura / shop reads, every edit_image operation,
tool schemas and routing. NOT tested against real Discord, real yt-dlp / ffmpeg or the image service.

## v22: ,assist is less dumb (askai.py only)

- It now sees the last `ASK_CONTEXT` (default 12) chat messages with every question, so "he", "that", "what did she mean" point at something real.
- Replying to a message now passes its embed text (the ,sop card, other bots' cards), notes about pictures/files/stickers, and the message that one answered. Before, only plain text was read, so a reply to an image or embed was empty.
- Pictures (attached or in embeds, Discord-hosted only) are shown to Claude, Gemini and Mistral. Other models get a note that a picture exists and are told not to guess what it shows.
- Prompts: today's date spelled out with an instruction to count ages from it, a "never invent names/ages/dates" rule, and ask-or-say-unsure when the target isn't in the context.
- The regular-member prompt now knows the bot's commands (it had none, so it guessed) and may explain them.

## v23: ,assist fixes for "slow, asks twice, says done but nothing happened" (askai.py only)

- Said "done" without calling a tool: if an admin's request ends with a reply like "done, removed the role" but no tool ran, the model is told once to actually call the tool
  (this was the "I have to ask twice" case). If it still does nothing the bot says so honestly instead of pretending. Logged as "said done without calling a tool".
- Role add / remove is now checked with Discord afterwards (fetch_member). If the role is still there the answer says so ("another bot or integration may be giving it back").
  "Nobody needed a change" is now "NOTHING CHANGED: ..." so the model can't turn it into "done".
- Slow provider: one AI request now gets `ASK_STEP_TIMEOUT` seconds (default 35, was 90) before the next provider is tried. 60 s once actions already ran.
- Follow-ups like "remove it now" / "she still has it": when the sentence itself matches no tool group, the last 3 chat lines decide which tools are sent (was: all 75 tools, ~9k tokens, every time).
- Cooldown starts when the answer is finished, not when it starts. An error ("None of the free AIs...", crash, too many steps) costs no cooldown and no daily question, and the failed
  try is not remembered. A second ,assist from someone whose first one is still running waits instead of running twice.
- Tested with a stub discord module and a fake HTTP layer (nudge, honest failure, timeouts, routing, role check, cooldown / busy). NOT run against real Discord or the real AI services.

## v23b: fast path, the AI is skipped for clear role / mute requests (askai.py)

- These sentences no longer go to the AI at all, so they are instant and exact: `give <name> the <role> role`, `give the <role> role to <name>`, `remove the <role> role from <name>`,
  `take <role> role off <name>`, `mute <name> for 10 mins / 2h`, `unmute <name>` (also with please / pls / rn). Names can be several words ("el diablo").
- They run the same tools as the AI (permission checks, role hierarchy, Confirm for mute, the Discord role check), and the reply is the real result:
  `done, took **Staff** from maja` / `nothing changed: maja does not have **Staff**` / `couldn't, <the real reason>`.
- Anything unclear still goes to the AI like before: he / she / him / her / them / it / that, unknown names or roles, several matches, longer sentences. A pronoun is never sent to a name search
  (so "give her the staff role" can't hit "Esther").
- The role tool messages no longer contain instructions for the model (they are shown to people directly).
- Tested with a fake discord layer. NOT run against real Discord.

## v24: ,assist fixes (askai.py, askai_more2.py)

Problems seen in real chats and what changed:

- "say X whenever someone mentions Y" was turned into a DELETE rule. Auto rules now have a `reply` action (`add_auto_rule(action="reply", reply_text=...)`):
  the bot answers the message with exactly that text. Works on everybody, never pings, max one answer per rule per channel every 5 s, other rules keep running.
  `list_auto_rules` / `remove_auto_rule` show and remove them like the others.
- "send me an image of <real thing>" made an AI drawing of something the model never saw. New tool `find_web_image` (Tavily images, Serper as fallback;
  needs TAVILY_API_KEY or SERPER_API_KEY): finds the real picture, downloads it and uploads it as a file; `pick=2,3..` gives another one. Admins only unless `ASK_PUBLIC_IMAGES=1`.
  `generate_image` is now only for NEW original art; it retries once, refuses to post a flat/blank picture, and no longer prints the prompt under the picture.
- A bare "hi / hey / yo" no longer goes to the AI (it commented on a .restore card). Prompt rule added: answer only what was asked, never narrate the chat context.
- NVIDIA model order: nemotron ultra -> gpt-oss-120b -> nemotron lightning 30B (the small one is the last resort).
- Biggest remaining lever: the free models. Setting `ANTHROPIC_API_KEY` in `.env` makes ,assist use Claude (`ASK_MODEL`, default claude-haiku-5-5) with built-in web search.

## v25: free model list refreshed (2026-11-07) + model_test.py

- Dead ids removed: groq `llama-3.3-70b-versatile` (left the free plan in August), openrouter `meta-llama/llama-3.3-70b-instruct:free` (no longer free), nvidia kimi-k3 (retired 2026-10-15).
- New order (FREE_MODELS in askai.py). nvidia: deepseek-v4.1-flash, nemotron-3-ultra, glm-5.3-flash, gpt-oss-120b, nemotron-3.5-lightning. gemini: 3.8-flash, 3.5-flash, 3.5-flash-lite. groq: gpt-oss-120b, qwen3.8-27b. openrouter: nemotron-3-ultra:free, openrouter/free.
- A model that is too slow now falls through to the next model of the SAME provider (before, one stuck thinking model skipped the whole provider).
- `python model_test.py` sends 5 real ,assist problems to every model you have a key for and prints a score table. Put the 5/5 ones in `.env`:
  `ASK_PROVIDER=nvidia` and `ASK_MODEL_NVIDIA=model1,model2` (also ASK_MODEL_GEMINI, ASK_MODEL_MISTRAL...). No code edit needed.
- Paid option that needs no code: `ASK_PROVIDER=openrouter`, `ASK_MODEL_OPENROUTER=moonshotai/kimi-k3.1-pro`, OPENROUTER_API_KEY with a few dollars of credit.


## v28 - scheduled moderation + assist personality
- `,assist ban massi in 2 hours` (also kick / mute / unban, `in_minutes` or `at 8pm`): new `schedule_action` tool, stored in the `sched_actions` table, survives restarts, run by the existing reminder loop. Admin only, asks for confirmation when scheduled, re-checks permissions and role hierarchy when it fires, then posts the result in the channel. List with "show scheduled bans", cancel with "cancel scheduled action <id>".
- `,assist` has a human, sarcastic personality for silly questions (no slurs).

## v30: .wordfilter replies are plain text (no embeds)

Asked for: the wordfilter replies should look like a normal chat bot's replies (plain text with the little reply arrow) instead of embeds.

- New helper `plain_reply(ctx, kind, text)` (next to `status_embed`): `ctx.reply(status_text(kind, text))`, no ping. Same icons (bl_add / bl_remove / bl_warn) and the same wording, just without the embed and without the `@user:` prefix (the reply arrow already shows who it answers).
- `.wordfilter add / remove / defaults` and the "no words yet" / "invalid word" warnings use it.
- `.wordfilter list` is now plain text: a bold header, then `1. word` per line; over 1900 characters it continues in more messages (replaces the embed + page buttons; `words_page_embed` is now unused but left in place).
- `on_command_error`: errors of `.wordfilter` / `.wf` commands (missing permission, missing argument...) are sent as a plain reply too; every other command keeps its embed error.
- The wordfilter repost itself (webhook copy of the message) was never an embed and is unchanged. `+help wordfilter` is still an embed help page.
- Syntax-checked only (py_compile), not run against Discord.

## v31: clean embeds (Bleed's new look: no side line, one rounded box)

Asked for: copy the look of Bleed's new embeds (screenshots: leaderboard, command help, status lines). A normal Discord embed always has a coloured bar on its left edge; the new look is a Components V2 *container* with no accent colour, which has no bar and can hold the buttons inside the box.

- New file `cleanbox.py` (next to bot.py) + 3 small edits in bot.py: `import cleanbox`, `cleanbox.install()` right after `bot = WordfilterBot(...)` (wrapped in try/except, so a failure keeps the old embeds), and `ShopView`'s container no longer sets `accent_colour=theme()`.
- No command was rewritten. `install()` wraps discord.py's send / reply / edit / interaction response / followup calls. When a call carries an embed, the same call is made with a container instead:
  - author -> small grey line on top; title -> `###` heading (linked when the embed has a url); description -> text
  - fields -> under a divider; a short inline field is one line `**Aliases:** n/a`, every other field is `**Label**` with the value under it
  - thumbnail -> picture on the right of the first text; image -> picture in the box; footer + timestamp -> small grey line (`-# Page 1/15 (150 entries)`)
  - buttons / dropdowns of the old `discord.ui.View` -> inside the box under a divider. The old View stays in charge (`interaction_check`, `on_error`, timeout, `stop()`, `view.message`), so no View class was touched.
  - the embed colour is ignored on purpose (that is the side line). `.customize` embed colour therefore no longer shows; the green / amber / red status bars are gone too. (Bleed itself keeps a green bar on success; if that is wanted, pass `accent_colour=` for `OK_COLOR` in `build_view`.)
  - content text written next to an embed (a mention to ping someone) stays above the box. The bot's default `allowed_mentions=none()` still applies, so mentions inside the box do not ping, same as inside an embed.
- Sent exactly as before (old embed) when a container is not possible: more than 3900 characters of text, more than 40 components, a non-picture file attached, buttons that are not plain Buttons / Selects, `poll` / `stickers` / `suppress_embeds` / `tts`, channel webhooks (the wordfilter repost), and any `LayoutView` (shop, plinko, matchmaking cards already were containers).
- If Discord refuses a container message the same message is sent again as the old embed. Three refusals in a row switch the clean look off until restart (the log says so).
- `CLEAN_EMBEDS=0` in `.env` turns the whole thing off without touching code. Needs discord.py 2.6+ (requirements.txt already says 2.7+); on an older version nothing is wrapped.
- Known side effects: a container message has no `message.embeds`, so code reading `.embeds` of the bot's own messages sees nothing (only the reply preview in bot.py ~line 1124 and `,assist` reading context do this; both just skip). A normal message can only become a container if the same edit empties its text / embeds, and a container message can never go back (Discord rule).
- Tested only against a stand-in for discord.py (35 logic tests: conversion, fields, buttons moving into the box and still working, timeouts, edits, webhooks, fallbacks). NOT run against real Discord here: test `,lb`, `+help jail`, a paged list with arrows, a status reply and a slash command once, and send the log if something falls back.

## v32: clean icon set (the bot's own emojis, Bleed-style flat look) + `.emojisync`

Asked for: draw clean icons in the style of the reference bot's UI (grey arrows, page-jump, red close button with a white cross, tick / warning / on-off pills) and put them on the bot's buttons and replies without adding emojis by hand.

- New: `ui_emojis.py` (the pack, 18 drawn 128x128 PNGs as base64, same format as `gamble_emojis.py`), `ui_emojis/` (the same PNGs as files, for uploading by hand if ever wanted) and `make_ui_emojis.py` (the generator, needs `rsvg-convert` + Pillow; the bot itself does NOT need it). All icons are original flat drawings, not copies of anyone's emoji pack.
- The pack is merged into `EMOJI_PACK` (matchmaking.py, same place as the gambling pack), so the existing start-up step `make_app_emojis` creates them as application emojis on the bot's own account. Nothing to upload. A changed picture is detected by hash and replaced on the next start.
- Names (all `bl_<name>`, found with `emo("<name>")` / `find_emoji("<name>")`):
  | name | picture | used for |
  |------|---------|----------|
  | left, right | white chevrons | pager and leaderboard arrows |
  | jump | white `#` | pager "goto" button |
  | cross_w, check_w, add_w, remove_w | white cross / tick / plus / minus | the same icon on a green, red or blurple button (picked automatically) |
  | check, cross, warn, info, add, remove, wait | green tick, red cross, amber triangle, blue info, green plus, red minus, clock | status lines (ok / err / warn, add / remove, cooldown), `info_icon()` |
  | search, edit | white magnifier / pencil | `Find a Friend` buttons (search); `edit` is ready but not placed anywhere yet |
  | on, off | green / grey pill | the matchmaking "Only need older match" toggle; `emo("on")` / `emo("off")` ready for future status lines |
- bot.py
  - `find_emoji` order changed: 1) a server emoji named `bl_<n>` (a server can still override any icon by uploading its own), 2) the bot's own pack, 3) the old server names in `EMOJI_ALIASES` (approve, deny, warning, left, right...), 4) app emojis by alias. If the pack could not be created, everything silently falls back to the old server emojis like before.
  - New `button_icon(key, style)`; `style_buttons` uses it: on a green / red / blurple button it takes `bl_<key>_w` when the pack has it (a green tick on a green button would be invisible), grey buttons keep the coloured icon.
  - `Pager` (every paged list: help, family top, wordfilter...): `‹` `›` -> grey arrows, `goto` -> the jump icon, `✕` -> white cross on the RED button (Bleed look). The close button only turns grey (old behaviour) when the white cross is missing.
  - New command `.emojisync` (bot owner): runs the start-up emoji step again right now, so a new pack shows up without a restart. Says how many icons it created / replaced / failed. Listed in `+help` (Server). Not needed in normal use: the bot does the same on every start.
- matchmaking.py: `mm_emoji` uses the same new order (`MM_TO_PACK` maps ok -> check, err -> cross, ...); `add_icons` uses the white version on coloured buttons; the "older match" toggle shows the on / off pill (and stays grey, a green button would hide the green pill); `make_app_emojis` now returns a result (made / replaced / failed / total) and two runs at the same time wait for each other (start-up + `.emojisync`).
- Visible change to know about: status lines (`approve` / `deny` / `warning` / `cooldown` / `info` / `add` / `remove` emojis from your server) now show the new icons. To keep an old one for a single icon, upload an emoji named `bl_check`, `bl_cross`, `bl_warn`... to a server the bot is in: that wins over the pack.
- Not changed: the shop, gambling, owner / role / family icons keep their own emojis. The reference bot's antinuke / settings panels (toggle lines, pencil buttons) have no equivalent in this bot, so on / off / edit have no place to show yet.
- Tested: 47 logic checks against a stand-in for discord.py (lookup order, fallbacks, white icons on coloured buttons, the real `Pager` class, matchmaking icons, PNG sizes / names, `make_app_emojis` with success, partial failure, crash, old discord.py and two runs at once) and `py_compile` of every file. NOT run against real Discord (discord.py is not installed where this was written): start the bot, check the log for `Emoji pack: 18 created`, then open `,lb` / `+help jail` and look at the arrows and the red X.

## v33: reputation leaderboard buttons (`,rep top` / `,rep lb`)

- `AuraBoardView.refresh_buttons` (bot.py): the page arrows use the clean pack arrows (`bl_left` / `bl_right`, icon only), the All Time / 7 Days button is plain text (the calendar emoji is gone, and its now unused `calendar` entry was removed from `EMOJI_ALIASES`). `My Stats` still has its emoji (`stats`); to drop it too, delete `"My Stats": "mystats"` from that `style_buttons` call.
- Tested with the real `AuraBoardView` class against the discord.py stand-in (7 more checks, 54 in total): arrows are the new ones even when the server still has the old `left` / `right` / `fr_calendar` emojis, no emoji on All Time / 7 Days (also after switching), arrows disabled with one page.

## v34: one clean join log embed + status icons, family roles and forced [TAG] nicknames removed

Asked for: the join log (guard) embeds look like the bot's other clean embeds (no colored side line), a red alert icon when an alt is detected and jailed, a yellow alert for suspicious, a green check for safe accounts, everything in ONE embed instead of several, and no more family tags forced into nicknames and no more family role.

**Join log (`invitetracker.py`)**
- `_profile_embeds` now returns ONE embed (it used to return 4: member, how they joined, alt check, name history). No color is set on it; `cleanbox.py` draws it as a clean box without the side line.
- First line is the status: `_status(alt, jailed, is_bot)` -> red alert `bl_alert` "Alt detected, jailed" / "Alt detected, not jailed" (score 4+), yellow alert `bl_warn` "Suspicious" (score 2-3, or "Suspicious, jailed" when a young account was quarantined), green check `bl_check` "Safe account" (score 0-1). A bot joining shows the yellow alert "Bot added"; a failed alt check shows the yellow alert "Alt check failed". "Jailed" = the quarantine role was given, or the automatic timeout ran (`jailed` is worked out in `on_member_join`).
- Icons come from the bot's own pack (`_icon()` -> `emo(...)` in bot.py) and fall back to the plain emoji when the pack isn't there: 🚨 / ⚠️ / ✅.
- Same information as before, condensed: names, created / joined, profile links, badges, member flags, roles, status (screening / timeout, only when set), how they joined (invite, uses, channel, expiry, inviter + stats), suggested action, warning signs, matches, automatic actions, name + join history, other servers / permissions / verification level, footer with user ID and what the alt check compared with. Empty parts (no badges, no roles ...) are left out instead of printing "none". If the text gets too long (`TEXT_BUDGET`, 3400 characters) the least important parts are dropped first (More details, history, Profile, flags, badges).
- Staff buttons (Approve / Timeout / Kick / Ban / Dismiss) are `DynamicItem`s, which cleanbox did not accept, so the message would have fallen back to a colored embed. `cleanbox._plain_item` / `_legacy_ok` now accept a DynamicItem that wraps a Button. Discord does not allow `view=None` or a bare button View when editing a clean (Components V2) message, so Dismiss / Approve / the end of Kick and Ban rebuild the box from the message (`_relayout`, `_set_buttons` in invitetracker.py) and replace the button row. Old join logs (normal embeds) are edited the old way.
- Not changed: the raid alert, leave log and early-watch alert (they were already drawn clean by cleanbox; early watch uses the same buttons and gets the same fix).

**New icon `bl_alert`** (red twin of `bl_warn`): added to `ui_emojis/bl_alert.png`, `ui_emojis.py` (UI_PACK now has 19 icons) and `make_ui_emojis.py`. The bot creates it on the next start (log: `Emoji pack: 1 created`), or run `.emojisync`.

**Family (`bot.py`)**
- No family role any more and no forced `[TAG] name` nicknames. `apply_member` does nothing (joining, creating, kidnapping and `admin_add` no longer give a role or rename anyone), `release_member` only removes a leftover old role, the `on_member_update` listener that forced nicknames is gone, `family newtag` only changes the tag, `family rename` no longer renames a role, `family role` is removed (help entry and example too), the family card no longer has a Role line, and `family baserole` now only places the Best family role.
- The tag still exists, but only as a label: shown on the family card title, `family top` and the war announcement.
- One-time clean-up of what older versions left behind: `sync_family` (called at start-up for every server, by `family info`, `.roles check` and after a backup restore) now deletes the old family role and removes the `[TAG] ` prefix from members' nicknames, then sets `cleaned: true` on the family (families.json) so it never runs again. If something fails (bot role too low ...) it is tried again on the next start.
- The Best family role (top family) is NOT removed.

Tested: `py_compile` of every changed file, the new embed with a stand-in for discord.py (safe / suspicious / alt jailed / alt not jailed / young jailed / bot / failed check / huge input: 8 cases, no color, all under the size limit), the button rebuild (`_relayout`) and `cleanbox._legacy_ok` with DynamicItem buttons against the stand-in, and an AST scan of bot.py for undefined names. NOT run against real Discord: check one real join (clean box, icons, buttons Dismiss / Approve / Timeout), and that the old family roles and [TAG] nicknames disappear on the first start.

### v34 follow-up: dedicated drawn icons for the join log, no classic emoji

- The first v34 version reused `bl_warn` / `bl_check` and fell back to the phone emojis 🚨 ⚠️ ✅. Replaced: three new drawn icons, one matching set of flat round badges with a white glyph: `bl_jl_alert` (red, !), `bl_jl_warn` (amber, !), `bl_jl_safe` (green, tick). Made by `make_joinlog_icons.py` (Pillow only, drawn at 4x and scaled down), files in `ui_emojis/`, added to `UI_PACK` in `ui_emojis.py` (21 icons now). The earlier `bl_alert` was removed.
- `invitetracker._icon(name)` returns the icon or an empty string. There is NO unicode fallback any more: if the pack isn't created yet the status line is just bold text until the next start or `.emojisync`.
- `make_ui_emojis.py` rewrites ui_emojis.py without the three badges, so run `make_joinlog_icons.py` after it.

## v35: `.backup` sends ONE file, `,puh` emojis are back, real 50 / 50

**`.backup` / `.restore` (bot.py)**
- `.backup` now DMs ONE zip file, `backup-<date>.zip`, with everything inside it: `backup.txt` (the backup code: reputation, quests, families, matchmaking, ,sop links, counts) plus the family banner zip, the ,sop picture zip(s) and the ,assist history zip(s). Before it was a .txt plus up to several .zip files in separate messages.
- It is written to a temp folder on disk piece by piece (`BackupBundle`) and the folder is deleted afterwards, so the pictures are never all in memory at once (Wispbyte RAM).
- Only when the backup is bigger than one Discord upload (`BACKUP_PART_MB`, default 9) does it come as `backup-<date>-part1of2.zip`, ... and every file is still a complete bundle. Same fix as before if DMs refuse it: a smaller `BACKUP_PART_MB`.
- `.restore` reads the new file (`backup_zip_is_bundle` / `backup_unpack_bundle`): it finds `bundle.json`, unpacks only `backup.txt` and top-level `*.zip` names (nothing with a folder or `..` in it), counts the real sizes while reading, then sends the contents through the same code as before. Old backups (a .txt, picture zips, a pasted code) still restore the old way. Slash `/restore` takes the single file, which is now enough.
- `+help` text for backup and restore updated.
- Not in the backup (same as before): the word filter list, `.customize` settings, prefixes, shop prices, goonlock list.

**`,puh` (bot.py)**
- Emojis are back: header `🔒 @user is **tight**` / `🔓 @user is **loose**` / `⚖️ @user is **perfectly balanced**, as all things should be`, and each bar line starts with 🔒 (tight) or 🔓 (loose). The lock still uses a server emoji named `bl_lock` if there is one.
- Real 50 / 50: the "perfectly balanced" message was there since Step 16 but only fires on exactly 50 / 50 (1 roll in 101), while the 10 block bars looked equal for every roll from 45 to 55, so a 53 / 47 looked like a tie with no message. New `puh_bars()`: the bars are only equal on a real 50 / 50, otherwise the leader gets one block more. `,racist` / `meter_bar` unchanged.
- Tested (stand-in for discord.py): all 101 rolls (bars equal only at 50, leader never shorter), the 50 / 50 text, bundle with one file / split into 5 files / bad names / too big / damaged zip.

## v36: no more doubled user names in embeds

Asked for: some embeds showed the user twice (the @mention and the name line on top, or a mention plus the username), which only made them longer.

- `with_author` (bot.py): the small name line on top is skipped when the embed text already @mentions that same person. This fixes `,puh` / `,racist` / `,ratio` / `,rep` / `My Stats` when you run them on yourself (or `,racist @x`, where the name line was the target and the text mentioned the target). When the target is somebody else (`,puh @kai`), the runner's name line stays, because those are two different people. Help pages, leaderboards, quests, inventory and the family cards have no mention in their text, so they are unchanged.
- Join log (`invitetracker.py`): the join profile, the "Member left / kicked / banned" card and the early-watch alert no longer have the `username` name line on top (the mention under it shows the name, and the id is next to it). The avatar is kept as the picture on the right. The username is still in the join profile's "Names" field.
- Not changed: status lines (`@user: text`) have no name line, so they were never doubled.
- Tested (stand-in for discord.py): self mention, `<@!id>` form, other target, no mention, no user, and id 10 not matching `<@100>`. `py_compile` of bot.py and invitetracker.py.

## v37: gem for reputation, W ratio emoji, quiet quest claim

Asked for: use the server emoji `:m_gem:` for reputation, a fitting emoji for the W ratio, no emoji on the quests Claim button, and a claim result that only the claimer sees.

- **Reputation emoji** (bot.py, `EMOJI_ALIASES["aura"]`): now `["m_gem", "rep_star"]`. Every place that shows reputation already goes through `emo("aura")` (`,rep`, `,ratio`, shop, leaderboard...), so they all switch at once. The bot finds `m_gem` on any server it shares with it; if it doesn't see it, the old gold sparkle (`rep_star`) is the fallback.
- **Optional pin**: `REP_EMOJI=<:m_gem:123456789012345678>` in the env (type `\:m_gem:` in Discord to copy that text) makes the bot use that emoji even when it isn't in the server that owns it. Empty or not in that form: ignored. Code: `_rep_emoji_from_env()`, `REP_EMOJI`, first lines of `find_emoji`.
- **W ratio emoji**: new drawn icon `bl_wratio` (shiny 3D gold W with bevels, reflections, outline, shadow and two sparkles, shaded like the skull of the L ratio; a first flat green badge was redrawn on request), made by `make_wratio_icon.py` (Pillow + numpy + scipy, only to draw it), saved as `ui_emojis/bl_wratio.png` and added to `UI_PACK` in `ui_emojis.py`, so the bot creates it by itself on start-up. `,ratio` uses `emo('wratio')` and falls back to the trophy while it doesn't exist. L ratio keeps the skull; the trophy is unchanged for duels, bounties and the `,gayest` / `,tightest` winner. Re-run order if you ever regenerate the packs: `make_ui_emojis.py`, then `make_joinlog_icons.py`, then `make_wratio_icon.py`.
- **Quests Claim button** (`QuestView`): no emoji on the button any more (the `style_buttons(... "trophy")` line is gone). The claim result (`claimed N quests for X reputation... You now have Y`) is sent with `ephemeral=True`, so only the person who pressed the button sees it. The quest card itself still updates in place for everybody.
- Not changed: the text command `quests claim` still answers in the channel, because a message sent with a prefix can't be hidden (only the slash version could be).
- Tested (stand-in for discord.py): emoji lookup with m_gem on a server, without it (falls back to the sparkle), with `REP_EMOJI`, with a bad `REP_EMOJI`, and with no emojis at all; `py_compile` of bot.py, ui_emojis.py and both icon scripts; `bl_wratio` is in `EMOJI_REDRAWN` so a flat badge that was already created gets replaced once; `UI_PACK` decodes to a valid PNG.
- **`,ratio` has no reputation emoji** any more: the `You now have N reputation.` line under W / L ratio is plain text (the gem still shows everywhere else reputation is shown).
- **No more "just for fun" text** in any embed: it was the footer of `meter_embed` (`,puh`, `,ratio`) and a line inside the `,racist` embed. Both are gone (older notes above that mention the footer are history). Nothing else in the code uses that text.

## v37 (continued): no doubled mentions in the ping commands and the smash / pass answer

Asked for: the rest of the "user shown twice" cleanup.

- **Smash / pass answer** (`sop_answer_embed`): the second small line was `<display name> has 5 smashes` under `@user smashed Name`, so the same person was named twice. It is now `5 smashes in total`.
- **Ping line above the box** (`,duel`, `,family invite`, `,rep give`, `,rob` and kidnap): these commands send the person's @mention as message text above the box so they get a notification, and the box mentions the same person again. Fixed in one place, `cleanbox._plan_send` (new `_without_repeated_pings`), so every send with that pattern is covered, now and later:
  - message text that is ONLY user @mentions counts as a ping line. When the box already mentions that person, the line above is dropped. The box's own mention still pings them, because it is part of a container message and the call's `allowed_mentions` is unchanged (each command already passes the right ones).
  - a mention that is NOT in the box stays as a one-line @mention above it: kidnap keeps a line with the old family owner, who is pinged but not named in the box.
  - real text next to an embed (anything besides mentions, or a role mention) is left exactly as it was.
  - if Discord refuses the container version and the normal embed is sent instead, that send still carries the original ping line (a mention inside a normal embed never pings), so nobody loses a notification.
  - `CLEAN_KEEP_PING_LINE=1` in the env puts the ping line back above the box for good. Use it if a test shows the ping does not land (see below).
- To test on Discord: run one `,duel @someone` with a second account (or have a friend do it) and check that the challenged person still gets the notification and sees one box with no @mention line above it. This can't be checked without Discord.
- Tested (stand-in for discord.py): duel, invite, rep give, rob (line dropped), kidnap (only the old owner's line is left), a ping for someone not in the box, real text next to the embed, a role mention, no content, the `<@!id>` form, and `CLEAN_KEEP_PING_LINE=1`. `py_compile` of bot.py and cleanbox.py.


## `,bottle` (kiss, slap, marry)

- New fun command `,bottle` (slash: `/bottle`). The bot picks 3 random active members (people who wrote in the last 30 minutes; if fewer
  than 3, members shown as online, then anyone who can see the channel). The public message shows the 3 people and a **Choose** button
  only the player can press. The choices happen in a message only the player sees (kiss -> slap, the last one is married), then the public
  message is edited into the result. Just for fun: no reputation changes. Cooldown `BOTTLE_COOLDOWN` (60 s), 2 minutes to choose.
- A small `on_message` listener keeps the last-message time per member in memory (`recent_chatters`), nothing is saved to disk.
- Code: `bottle_pick`, `BottleView`, `BottlePrivateView`, `bottle` in bot.py; added to FUN_COMMANDS, the help list and examples.
- Not tested against real Discord (buttons, ephemeral replies, editing the public message): try it on a server with a test account.

## Families: `.family` is now `,fam`

- The family group is now called `fam` and uses the fun prefix `,` (`,fam create`, `,fam info`, `,fam top`, `,fam war`, ...). `family` is NOT a command
  anymore (`,family` / `.family` do nothing). The subcommand aliases stay (`,fam lb`, `,fam bank`, `,fam dep`, `,fam with`, `,fam desc`, `,fam profile`).
- Changed in bot.py: the group name (`name="fam"`, the old `aliases=["fam"]` is gone), `FUN_COMMANDS`, help entries / examples / `HELP_GROUPS` / `POPULAR`, and
  every usage hint (`{prefix_for('fam')}fam ...`). askai.py's description of the prefixes was updated too.
- A prefix saved earlier with `+prefix family ...` is no longer used (the command is called `fam` now): set it again with `+prefix fam (prefix)` if you want another one.
- Not run against Discord here (discord.py isn't installed in this sandbox); only a syntax check was done.

## `,bottle`: drawn icons and a cleaner look

- 4 new drawn icons (lit and shaded like the W ratio badge): `bl_bottle`, `bl_kiss`, `bl_slap`, `bl_ring`. Script: `make_bottle_icons.py`
  (run it AFTER make_ui_emojis.py, like make_wratio_icon.py); the PNGs are in `ui_emojis/` and the pictures are in `ui_emojis.py`, so the bot
  creates them as application emojis on the next start (look them up as `emo("bottle")`, `emo("kiss")`, `emo("slap")`, `emo("ring")`).
  There is no unicode fallback on purpose: if an icon isn't there yet, the line simply has no icon.
- The embeds are cleaner: bold title, small grey subtext, mentions on their own lines. The private choice screen (kiss / slap) is plain text with no icons;
  the icons only show on the public message, on the Choose button and in the result.
- Not tested on Discord (discord.py isn't installed in the sandbox where this was made): after the restart, check that the 4 emojis appear.

## `,bottle`: the button is now "Pick"

- The **Choose** button is now **Pick**. It sits right under the embed (Discord can't put buttons inside an embed) and opens the private page where the player picks.

## `,bottle`: result embed

- The result embed no longer has the "Spin the bottle" title or the bottle icon: a small grey "@player spun the bottle" line, then Kissed / Slapped / Married with their icons.

## `,bottle`: Pick opens a form (modal)

- **Pick** now opens a form like the Bot Style one: three dropdowns (Kiss, Slap, Marry), each with the 3 members, and a Submit button. Submitting turns the
  public message into the result. If the same person is chosen twice, only the player gets "Pick a different person for each one" with a **Pick again** button.
- The old two-step private page (`BottlePrivateView`) is gone; the code is `BottleModal` and `BottleRetryView` in bot.py. Same discord.py 2.7 feature (selects inside modals)
  that the Bot Style form already uses.
- Not run on Discord from here: test it once with a second account (submit with a repeated person, and after the 2 minutes are over).

## `,bottle`: result wording

- The result embed's small grey line now says "@player picked" instead of "@player spun the bottle".

## `,bottle`: no more same people, and the result is a new message

- **Same people again and again:** the old pick took 3 random people from those who wrote in the last 30 minutes and only looked further when
  there were fewer than 3. In a quiet chat that is 3 to 5 people, so the same ones kept coming back (the list of talkers is also empty after a restart).
  Now the bot remembers who each player was shown in their last 4 bottles (`BOTTLE_MEMORY` = 12 people, in memory only, nothing saved to disk) and
  skips them while there are enough other people. It still goes active talkers first, then members shown as online, then anyone who can see the
  channel, so with few talkers the rest are filled in from the other groups instead of repeating. If a server is really small, the people shown
  longest ago are used first, so it rotates and the same trio doesn't come twice in a row. Code: `bottle_pick`, `bottle_shown`.
- **Result as a new message:** when the player submits the form, the result is posted as a NEW message (it lands at the bottom of the chat) and the first
  "Spin the bottle / Pick" message is deleted. The result message does not ping the 3 people (same as the first message). If Discord refuses the new
  message, the first message is turned into the result instead, so nothing is lost. If the first message can't be deleted it simply stays.
  Code: `BottleModal.on_submit`.
- Tested with stand-in members (6 talkers over 6 rounds, 4 people over 8 rounds, active talkers first, fewer than 3 people, bots and the player skipped) and
  `py_compile` of bot.py. Not run on Discord: after the restart, spin a few bottles in a row and check the faces change, and check the first message disappears
  after Submit (this needs the bot to be allowed to delete its own messages, which it always is).

## Reputation leaderboard: crown title, no family tags in names

- **Title:** `,rep lb` (and its 7 Days page) now starts with the server emoji `:crown:` in front of the title ("<crown> Top reputation"). The emoji is found by
  its name `crown` on any server the bot is in (`EMOJI_ALIASES["crown"]`, same way as the other server emojis). If the bot can't find it, the title is plain text.
- **No family tags in names:** the `[🍃]` / `[🤫]` in front of some names were old forced `[TAG] name` nicknames (removed in v34). The one-time clean-up can't rename
  people the bot has no power over (the server owner, anyone above the bot's role), so those nicknames still had the tag. The lists now cut it off when
  they show a name: new helper `plain_name` removes the tag of any family in the server, and any tag made only of emojis / symbols (left by a family that was
  deleted). A nickname like `[AFK] name` is left alone. Only what is shown changes, nobody's nickname is touched.
  Used in the reputation leaderboard and the bounty board (the two lists that rank members by name).
- The family card and `,fam top` still show a family's own `[TAG]` label (it is part of the family, not of anyone's name).
- Tested `plain_name` with stand-in nicknames (emoji tag, letter tag of a current family, deleted-family emoji tag, `[AFK]`, tag only, tag after the name) and
  `py_compile` of bot.py. Not run on Discord.

## `,ratio`: the W ratio icon is now a gold medal

- The gold letter W in front of **W ratio** is replaced by a drawn first-place medal: a gold medal with a raised star on a red ribbon, lit and shaded the same way
  as the W was (rounded rim, recessed face, metal reflections, dark outline, soft shadow, two sparkles), so it still sits well next to the shaded skull of the L ratio.
- Same emoji name (`bl_wratio`, looked up as `emo("wratio")`), so bot.py is unchanged. The picture is in `ui_emojis/bl_wratio.png` and in `ui_emojis.py`;
  `make_wratio_icon.py` now draws the medal (run it AFTER make_ui_emojis.py like before). On the next start the bot sees the changed picture and replaces the old
  emoji on its own account (`bl_wratio` is in `EMOJI_REDRAWN`), so nothing has to be deleted or uploaded by hand.
- Checked: the picture inside `ui_emojis.py` is byte-for-byte the PNG, 128x128, about 21 KB (Discord's limit is 256 KB), and looked at on dark and light
  backgrounds at 128, 48 and 22 px. Not run on Discord: after the restart, send one `,ratio` and check the new icon shows (a W ratio is needed to see it).

## `,ratio`: shorter result

- The **"You now have N reputation."** line is gone from the `,ratio` result, so the embed is just the W / L ratio line and who earned or lost how much. Nothing else about
  the command changed (amounts, cooldown, quests and the saved reputation work as before). Other commands (`,daily`, `,rep give`, quests, shop...) still show the total.
  Code: `ratio` in bot.py. `py_compile` of bot.py only; not run on Discord.

## Reputation leaderboard: "Top users", no name line on top

- The title of `,rep lb` is now the crown + **Top users** (the 7 Days page: **Top users (7 days)**). The crown is the server emoji `:crown:` (found by its name `crown`,
  see the step above); without it the title is just the text.
- The name of the person who ran the command is no longer shown above the title. Their own line is still at the bottom ("Your reputation: ... Rank: ...").
  Code: `aura_board_embed`. The family top and the other lists were not changed. `py_compile` of bot.py only; not run on Discord.

## v49b: ,assist speed (askai.py)

- `ASK_STEP_TIMEOUT` default 35 -> 15 s: a stuck provider is dropped (and the next one tried) after 15 s instead of 35 s. Raise it in `.env` if a good model needs longer.
- One shared keep-alive HTTP session for all AI requests (was a new session, so new DNS + TLS handshake, for every ,assist). Closed in `cog_unload`.
- The chat context, the replied-to message and the asker's pictures are fetched at the same time (asyncio.gather) instead of one after the other.
- Gemini thinking: `reasoning_effort` now starts at `minimal` (`ASK_GEMINI_EFFORT` in `.env` changes it). If a model rejects it, that model is remembered and gets `low`, and if that is rejected too, no switch is sent.
- Not changed: when a request matches no tool group, all ~75 tools (~9k tokens) are still sent. If it is still slow, check the `ask timing:` lines in the log (they show provider, seconds, request size, tool count).
- Compiles; NOT run against real Discord or the real AI services.

## v49c: coloured side bar back for status messages (cleanbox.py, bot.py, askai.py)

- cleanbox.py: an embed that is only text in the success (green), error (red) or warning (amber) colour is drawn as a clean box WITH that coloured side bar.
  Cards with a title, fields, picture, thumbnail or author line stay bar-less like before. `CLEAN_STATUS_BAR=0` in `.env` removes the bars again.
  Same colours as OK_COLOR / ERR_COLOR / WARN_COLOR in bot.py (0x3BA55D / 0xD9534F / 0xFAA61A); any other text-only embed using one of those colours gets the bar too.
- The icons were already the green tick (bl_check), red cross (bl_cross) and yellow alert (bl_warn); the fallbacks used when the custom emoji is missing are now ✓ / ✕ / ⚠️ (cooldown ⏳).
- ,assist: failures ("None of the free AIs...", "Something broke...") are now a red status line instead of a plain answer.
- Compiles; NOT run against real Discord (the container accent colour is the standard discord.py 2.6+ `Container(accent_colour=...)`).

## v49g: new status emojis (drawn), no more family tags (bot.py, matchmaking.py, ui_emojis.py, make_status_icons.py, askai_more2.py)

- **New status emojis:** bl_check (green tick), bl_cross (red cross) and bl_warn (yellow warning triangle) are redrawn to look like the ones from your screenshot:
  flat, chunky and rounded, the tick with a darker lower edge, a salmon-red cross, a yellow triangle with an orange rim and a dark blue-grey exclamation mark.
  Pictures: `ui_emojis/bl_check.png`, `bl_cross.png`, `bl_warn.png` (also inside `ui_emojis.py`); drawn by the new `make_status_icons.py`
  (run it AFTER make_ui_emojis.py, like make_wratio_icon.py). The bot replaces the old emojis on its own at the next start (the three names are in `EMOJI_REDRAWN` in matchmaking.py).
  If the bot is NOT creating its own emojis (discord.py older than 2.5) or a server already has emojis called bl_check / bl_cross / bl_warn, those win: delete them there or upload the new PNGs from `ui_emojis`.
- **Every status line uses them:** ok / add / remove -> bl_check, err -> bl_cross, warn -> bl_warn (plain \u2705 \u274c \u26a0\ufe0f only if the emoji can't be found). Code: `STATUS_EMOJI`, `status_icon`, `status_text`.
- **No red minus on a green box:** a removal that worked (family deleted, member kicked, demoted, description cleared, goon-lock removed, SOP entry removed...) is a success,
  so it uses the same green tick as ok. Red bar + cross is only for errors, amber + warning only for warnings.
- **Same icons in every module (v49g):** matchmaking.py (`status_icon`, `KINDS`: its add / remove messages are now the green tick too, with \u2705 \u274c \u26a0\ufe0f as the last fallback),
  the join log's "approved" line (invitetracker.py) and the family base-role warning lines now use the same tick / cross / warning as bot.py. The join log's own alert / warning / safe
  header icons (bl_jl_*) are a separate design and were not changed. Duel / SOP result cards are results, not status messages, and keep their own icons.
- **Family tags are gone:** `,fam create (name)` takes just a name (example `,fam create Wolves`); `,fam newtag` is removed. No `[TAG]` on the family card, `,fam top`, the war messages,
  and families are found by name only (`fam purge`, `fam admin_add`, ,assist). Families made before keep a hidden tag in family.json that is never shown or used. Help text updated.
- Compiles (py_compile on every file); NOT run against real Discord (discord.py isn't installed where this was edited).

## v49h: `,bottle` fixes: no strangers, no "Original message was deleted", no double picks (bot.py)

- **Unknown users (`@unknown-user`):** the bot has the Members intent but NOT the Presence intent, so every member's status reads "offline" and the
  "online members" step never found anyone. After a restart the list of recent talkers is empty too, so the old code fell through to its last step,
  "anyone who can see the channel": people who aren't around, which Discord draws as `@unknown-user`. That last step is gone. Now only people who
  wrote in THIS channel in the last 30 minutes are picked (`recent_chatters` is per channel now, not per server). When the memory is empty (after a restart) the bot reads
  the channel's last 100 messages once to refill it (`bottle_seed_chatters`, at most once a minute per channel, skipped when the memory already knows 6+ people; needs
  Read Message History, otherwise it just uses what it has). Bots, the player, people who left and people who can't see the channel are skipped. Online members still
  count IF the Presence intent is ever turned on (it is NOT turned on here: it is a privileged intent and the bot would refuse to start until it is switched on in the Developer Portal).
  With fewer than 3 active people the command says "I need at least 3 other people who wrote in this channel in the last 30 minutes" and doesn't use up the cooldown.
  `BOTTLE_ACTIVE_MINUTES` can now be set in `.env` (default 30).
- **"Original message was deleted":** the result was sent with `interaction.followup`, and Discord shows a followup as a reply to the message the button was on, so after that message was
  deleted the result had the grey "Original message was deleted" line above it. The result is now a plain `channel.send`, so it has no reply line at all. Code: `BottleView.finish`.
  If the new message can't be sent, the Pick message is edited into the result instead (as before).
- **Same person picked twice:** the form (Kiss / Slap / Marry dropdowns) stays. A Discord form can't hide a person already chosen in another dropdown, so choosing the same person
  twice now gets a clear warning that only the player sees: "You can't pick the same person twice. Choose a different person for Kiss, Slap and Marry." (amber warning line) with a
  **Pick again** button that opens the form again (`BottleModal`, `BottleRetryView`). The warning is removed again when the next try works or is replaced by the next warning, so they
  don't pile up (`drop_ephemeral`). The public message also says "a different person for each one". (A private page that hides picked people from the other dropdowns was tried first
  and replaced by this warning on request.)
- Tested with stand-in Discord objects (`test_bottle.py`, not shipped): who gets picked (no talkers, 2 talkers, old talkers, bots, can't-see-channel, left the server, per channel, rotation),
  history refill, all 27 ways to fill the form (only the 6 with three different people go through, the 21 with a repeat get the warning and post nothing), Pick again, the warning
  clean-up, result posted as a channel message and not a followup, the fallbacks. `py_compile` of bot.py. NOT run against real Discord: after the restart, spin one in a busy channel and check (1) only people who just wrote show up,
  (2) the result has no "Original message was deleted" line, (3) choosing the same person twice shows the warning and Pick again works.

## v49h: `,ratio` shows your balance again (bot.py)

- After a W or L ratio the result now ends with a small grey line with your new balance, e.g. `-# Balance 1,312` (it can be negative, e.g. `Balance -150`).
  Same grey "Balance" line style as the gambling cards. It is the total AFTER the win or loss; the bot already had it (`give_aura` returns the new total), so nothing extra is read or saved.
  This brings the balance back in a shorter form than the old "You now have N reputation." sentence that was removed earlier; the rest of the result (W / L line, who earned or lost how much) is unchanged.
  The cooldown message is unchanged. The `,ratio` help text mentions it. Code: `ratio` in bot.py.
- Tested the real `ratio` function with stand-ins (win, loss, loss below zero, the 30% debt rule, climbing out of debt, big numbers with commas, cooldown); `py_compile` of bot.py. Not run on Discord.

## v49h: `,puh` keeps only the result emoji (bot.py)

- The two bar lines ("`██░░` 2% tight" / "`░░██` 98% loose") no longer start with a lock / unlock emoji. The only emoji left is the one on the result line:
  `🔒 @user is **tight**`, `🔓 @user is **loose**` or `⚖️ @user is **perfectly balanced**, as all things should be` (the lock / unlock still use the server emojis `bl_lock` / `bl_unlock` when they exist).
  This undoes the "each bar line starts with a lock" part of v35; the bars, percentages, rolls and cooldown are unchanged. Code: `puh` in bot.py.
- Tested the real `puh` function with stand-ins (loose, tight, perfectly balanced: exactly one emoji per result); `py_compile` of bot.py. Not run on Discord.


## v49i: `,wordstats <word>` (bot.py)

- New command `,wordstats moha` (also `/wordstats`): a short card that says how many times YOU said the word in THIS channel and, in small grey text underneath, how many times everyone said it there, e.g.
  `@user said **moha** **7 times** here` / `-# **14 times** by everyone here`. Cooldown 5 seconds (a typo like two words does not use it up). Uses the "," prefix (`FUN_COMMANDS`), has a `.help` entry and example.
- **One channel only:** it counts the channel (or thread) where the command is used, nothing else. Run it in another channel to see that channel. No date is shown.
- **Nothing new is saved.** It counts the messages the assistant already logs in `askai_messages.db` (table `msgs`, written by askai.py for every server text message). So it only knows the messages that were logged: everything since askai.py started logging, plus whatever `,assistbackfill` imported (`,assistbackfill all 100000` for old messages, server owner / bot owner only).
- Rules: whole words only (`moha` does not count `mohamed`; `moha's` is a different word), upper / lower case is the same (also é / É, Arabic...), links, @mentions, #channels and custom emojis are ignored (an emoji called :moha: is not the word), messages marked deleted are not counted, bot commands are not counted (so `,wordstats moha` itself never counts). One word at a time.
- Code (all in bot.py, right after `ratio`): `wordstats_words`, `wordstats_target`, `wordstats_patterns`, `wordstats_is_command`, `wordstats_count`, `wordstats_lookup`, `wordstats_cmd`. The lookup runs in a worker thread on its OWN read-only connection (the askai log is never blocked or changed) and one lookup runs at a time (`wordstats_gate`). SQL narrows the messages (server + channel + LIKE), Python then counts exact words.
- Tested with a real SQLite file (same table layout as askai's) and stand-in Discord objects: 32 checks (counts per user / everyone, other server, other channel, empty channel, deleted and NULL rows, commands, links / emojis / mentions, accents, Arabic, apostrophes, singular "1 time", missing database, missing table) and speed (about 0.3 s for 300,000 saved messages on the test machine). NOT run against real Discord: after the restart, try `,wordstats <a word>` once. The new `/wordstats` slash command appears after the bot syncs its commands at start.

## v22: `,assist` smarter + 7 new tools (askai.py, new file askai_more3.py; needs the newest bot.py too)

Why it felt dumb (found by reading the code): Gemini ran at `reasoning_effort=minimal` (fast, barely thinks); every answer was capped at "1-3 short casual sentences" so real questions got cut short;
the persona said "tease them, THEN answer", so jokes sometimes replaced answers; the AI had no tool for maths / dates / units / counting words (it guessed); the public prompt listed only a stale set of commands.

**Smarter (askai.py)**
- Gemini reasoning effort default `minimal` -> `low` (`ASK_GEMINI_EFFORT`; `minimal` = the old speed, `medium` = more brains and slower). Replies can take a second or two longer. If you can't edit .env, change the `"low"` in `os.getenv("ASK_GEMINI_EFFORT", "low")` in `_ask_free`.
- Length rule: short for actions and chit-chat, but a real question (explanation, advice, comparison, how-to, lookup with several results) gets a complete answer of up to ~8 short sentences or a short list. Same for normal members. Reply budget 1200 -> 1800 tokens (public 600 -> 900), because thinking uses part of it.
- New `HOW TO WORK` block (`_THINK`, `_THINK_PUBLIC`, added in `_system`): work out what is really wanted, look things up with tools instead of guessing, never do maths / dates / units in its head, when a tool errors or finds nothing change the arguments or try another tool before giving up (no identical repeat call), answer knowledge questions with real substance, say so when unsure instead of inventing. Persona: "a joke never replaces the answer".
- Memory of the conversation: 6 exchanges for 30 minutes (was 4 for 15).
- The tool router always keeps `calculate` and `bot_help` (`_TOOL_CORE`); the new tools have routing groups in askai_more3.ROUTE_GROUPS.
- `_PUBLIC_BOT`: for a command it doesn't know, it calls `bot_help` first (then says it isn't sure it exists).

**7 new tools (askai_more3.py, loaded after askai_more2.py; if the file is missing the rest of ,assist keeps working)**
| tool | who | what |
|---|---|---|
| `calculate` | everybody | exact maths from a parsed expression (never run as Python): + - * / // % **, brackets, sqrt, round (half up), log, sin, factorial, min, max, pi... Refuses huge powers / factorials, names, strings, calls to anything else |
| `date_math` | everybody | days / weeks / years-months-days between dates, a date plus N days / weeks / months / years (month ends clamped), weekday of a date, age |
| `convert_units` | everybody | length, mass, volume, temperature, speed, area, data (KB and KiB), time; offline (money stays in `lookup`) |
| `random_pick` | everybody (members: admins only) | dice (2d6, d20, 3d8+2), coin, number, one or more of a list; random member (optionally with a role / active in the last N hours) |
| `word_stats` | admins + normal members (only channels they can read) | how many times a word or short phrase was said: total, messages, top people, top channels; filters user / channel / since_hours. Same rules as `,wordstats` (uses its helpers from bot.py) |
| `chat_stats` | admins | top chatters, busiest channels, activity by hour (UTC) / by day from the saved messages |
| `bot_help` | everybody | looks up a command of THIS bot live from the +help list (usage with the current prefix, what it does, who can use it, aliases), so the AI never explains a command from memory |

- Tools total: 83 -> 90. `word_stats` / `chat_stats` read askai_messages.db through their own read-only connection (the log is never locked), with a 5 second limit on big counts.
- Tested: 124 checks for the new tools (real SQLite file, stand-in Discord objects: maths incl. injection attempts, dates, units, random, word / phrase counts, channel / user / time filters, deleted + commands skipped, public restrictions, chat stats, bot_help, routing) and 24 wiring checks that load the REAL askai.py with stand-in discord / aiohttp modules (tools registered, prompts, routing, settings).
  NOT tested against the real AI models or Discord (no keys here): "smarter" is the removal of the causes above, not a measured result. Watch a few real questions after the restart; `python model_test.py` still checks the models themselves.

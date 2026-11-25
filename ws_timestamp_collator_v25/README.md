# WS Timestamp Collator
**v25**

Batch-scrapes WhoScored match centre data for a list of match IDs, filters
for one action type (Shot, KeyPass, TakeOn, etc.), and exports a flat
spreadsheet of raw timestamps — no offset conversion, just WhoScored's own
in-game clock — so you can jump straight to clip retrieval.

## Run

- **Windows**: double-click `Launch_WS_Timestamp_Collator.bat`
- **Mac/Linux**: double-click `Launch_WS_Timestamp_Collator.sh` (or run
  `./Launch_WS_Timestamp_Collator.sh` in Terminal — you may need
  `chmod +x Launch_WS_Timestamp_Collator.sh` once first)

The Python environment lives in a **shared location outside this folder**
(`%USERPROFILE%\.ws_tools\ws_timestamp_collator_venv` on Windows,
`~/.ws_tools/ws_timestamp_collator_venv` on Mac/Linux). Downloading or
extracting a newer version later won't force a full reinstall.


## Hosting (Streamlit Cloud) — needs a residential proxy

WhoScored returns **403** to datacentre IPs, which includes Streamlit Cloud.
Running locally works because it uses your home connection. To host it:

1. Get a **residential** proxy with a UK sticky session.
2. In Streamlit Cloud: App → Settings → Secrets, paste the contents of
   `.streamlit/secrets.toml.example` with your real values. Never commit
   real secrets to GitHub.
3. The app then routes WhoScored traffic through the proxy automatically.
   Hosted extras: a per-run match cap (`max_matches`, default 40), 24h
   caching of scraped matches, an optional `app_password`, and a
   **Retry failed matches** button. The sidebar shows a proxy-active note
   and an approximate data-used figure after each run.

Running locally needs none of this — leave the secrets out.

## Use

0. **Can't find a player's matches?** WhoScored doesn't have a per-team
   season archive — but competitions do. Navigate to the competition +
   season you want on WhoScored (e.g. Premier League → 2022/2023 →
   Fixtures tab) and copy that page's URL — it'll look like
   `whoscored.com/regions/252/tournaments/2/seasons/9075/stages/20934/fixtures/england-premier-league-2022-2023`.
   Paste it into "Find match IDs by team & season" at the top, optionally
   type a team name to filter to just their games, hit **Find match
   IDs**, then **Add these IDs to the batch box below**.
1. Paste in your WhoScored match IDs (one per line, or comma-separated).
   The ID is the number in the match URL:
   `whoscored.com/Matches/1953858/Live` → `1953858`
2. Pick the action type from the dropdown (or choose "Custom…" and type the
   exact WhoScored event name if it's not in the list).
3. Optionally filter to one player.
4. Hit **Run batch scrape**.
5. Review the table, then download as `.xlsx`.

Output columns: `match_id`, `home_team`, `away_team`, `team`, `player`,
`period`, `timestamp` (MM:SS), `minute`, `second`, `type`, `outcome`.

## Desktop .exe (optional)

Want this as a double-clickable app with its own window instead of a
browser tab? See `BUILD_EXE.md` — it's a one-time build you run yourself
on Windows (PyInstaller + pywebview), not something that ships
pre-built, since a Windows exe has to actually be built on Windows.

## Notes

- **v23:** added the desktop-exe build path (`desktop_launcher.py`,
  `ws_timestamp_collator.spec`, `BUILD_EXE.md`) — a pywebview window
  wrapping the same Streamlit server this app already runs, so it opens
  in its own window rather than a browser tab. This is genuinely a
  one-time build step on your end, not something verified end-to-end
  here — there's no Windows environment available to actually produce
  and test a `.exe` from this side, and Streamlit+PyInstaller bundling
  has well-documented rough edges (see the comments at the top of the
  `.spec` file) that sometimes take a rebuild pass or two to clear. The
  launcher script and spec file are written defensively around the two
  most common failure modes for this combination, but "should work" is
  the honest framing here, not "verified working."
- **v22 fix:** per-match archive filenames now include the player's name
  when the archive is filtered to one player — e.g.
  `2022-08-05_Michael_Olise_Crystal_Palace_vs_Arsenal_1640674.xlsx`
  instead of just the date/teams/match ID, which made a folder of 38
  files impossible to tell apart from an unfiltered archive at a glance.
  Uses the actual resolved player name from the data, not just the raw
  filter text, and only adds it when the filter narrows a match down to
  exactly one player — stays out of the filename otherwise.
- **v21 — Generate Markers.** New section at the bottom of the app, once
  you've run a scrape: pick one match, optionally filter to a player and
  a set of event types, enter kickoff timecodes for each half (where
  kickoff lands in YOUR video file, not match time), and generate
  Premiere Pro XML markers and/or a LosslessCut CSV directly — no
  separate tool, no re-scraping. Also supports the same broadcast cut
  offset system as the WS Marker Tool (optional, collapsed by default)
  for handling hydration breaks or other footage cut from a half.
  `marker_generator.py` ports the XML template, colour mapping, and
  offset math **verbatim** from the WS Marker Tool's Export XML/
  LosslessCut pages — not reconstructed from memory, since Premiere's XML
  import is picky about the exact structure. Verified before shipping:
  the kickoff/broadcast-cut offset math against manual hand calculation,
  the XML output as well-formed and parseable, and the full DataFrame →
  event → marker pipeline against realistic scraped data.
  Video cutting/scrubbing itself (the Marker Tool's heaviest feature)
  intentionally wasn't ported — this app doesn't touch video files, and
  the standalone Marker Tool still does that part.
- **v20 fix:** the per-match archive now respects the player filter from
  your scrape. v19 deliberately ignored it and pulled every player, which
  wasn't what "archive everything" was supposed to mean — filtering to a
  player should get you every action *type* that player did, not the
  entire squad's stats. Leave the player filter blank to still get
  everyone, same as before. Verified with a mixed-squad test match:
  filtering to a named player now correctly returns only their events
  across every action type, not other players' events and not restricted
  to one type either.
- **v19:** two additions.
  - **"All Events"** added to the action-type dropdown — skips the type
    filter entirely and keeps every event (still respects the player
    filter if set).
  - **Per-match archive**: after any batch scrape, a new section below
    the main download offers a `.zip` containing one styled `.xlsx` per
    match — every event, every player, completely unfiltered — regardless
    of whatever action type/player filter you used for the main export.
    The idea is you scrape once and keep a permanent local record per
    match, so building markers or clip lists later doesn't mean hitting
    WhoScored again. Filenames are `{date}_{home}_vs_{away}_{match_id}.xlsx`.
    Verified with a constructed multi-match, multi-type dataset before
    shipping — each per-match file correctly contains all event types,
    not just whatever was selected in the dropdown that run.
- **v18:** re-checked the full WS Marker Tool source (all pages, not just
  the scrape page) against what got ported in v17 — found one missed
  qualifier flag, `LongBall`, now added as a dropdown option alongside
  Shot/KeyPass/Cross/Corner/ThroughBall/Header/Freekick. Also confirmed
  again, across the entire tool this time rather than one file, that
  WhoScored genuinely has no official gameweek/matchday field anywhere —
  the `match_num` column added in v15 staying as a chronological count
  rather than a real gameweek number was correct, not a gap.
- **v17 bugfix — real one, not a false alarm:** "Shot" and "KeyPass" as
  action-type filters could never match anything, for anyone. Neither is
  a literal WhoScored event type — a key pass is a normal `Pass` event
  with a `KeyPass` tag (a "qualifier") attached, and "Shot" is really six
  different type values (`Goal`, `SavedShot`, `MissedShot`, `ShotOnPost`,
  `AttemptSaved`, `BlockedShot`). Earlier versions dropped qualifier
  parsing during a rewrite and filtered on raw type equality only, so
  those two options silently returned zero results always — which looked
  exactly like "this player didn't do that" but wasn't. Fixed by parsing
  qualifiers again and adding derived category filters (Shot, KeyPass,
  Cross, Corner, ThroughBall, Header, Freekick) that check the right
  underlying signal. Verified against constructed test events matching
  WhoScored's real event/qualifier shape before shipping.
- **v16 bugfix:** when the action type + player filter combination
  matched zero events (e.g. a player had no KeyPasses in the specific
  matches scraped), the app crashed with a pandas `ValueError` instead of
  saying so plainly — pandas' `.apply()` behaves differently on an empty
  DataFrame than a normal one. Now shows a clear "no events found"
  message and stops cleanly instead of crashing.
- **v15:** added `date` and `match_num` columns. `date` comes straight
  from WhoScored's match data. `match_num` is a sequential count (1, 2,
  3…) through whatever matches ended up in your export, ordered
  chronologically — **not** the official competition gameweek/matchday,
  since postponed or rearranged fixtures can shift those out of date
  order and WhoScored's data doesn't expose an official gameweek number
  anywhere. Rows are now shaded in alternating bands **per match** rather
  than per row, so it's obvious at a glance where one game's events end
  and the next begin even when matches have very different row counts.
- **v14:** the exported spreadsheet now has a bold dark header row, frozen
  in place so it stays visible while you scroll, a working autofilter,
  auto-sized columns, and the outcome column colour-coded (green for
  Successful, red for Unsuccessful). The filename is built from what's
  actually in the file — e.g. `Eze_TakeOn_34matches_timestamps.xlsx`
  instead of a generic `action_timestamps.xlsx` every time.
- **v13:** the fixture finder is now built against WhoScored's actual
  confirmed data API, found via a real browser HAR capture rather than
  guessed at:
  `https://www.whoscored.com/tournaments/{stageId}/data/?d={YYYYMM}&isAggregate=false`
  — a clean JSON endpoint returning that month's matches (id, team names,
  team IDs, score, kickoff time). The stage ID and season year range are
  pulled straight out of the pasted fixtures URL, then every month of the
  season is fetched (with a short pause between each) and combined.
  Verified against a real captured season: 380 matches total for a
  20-team Premier League season, exactly right, and filtering to a named
  team correctly returns that team's full 38-game season. A team name
  filter narrows the results client-side after fetching, since the API
  itself covers the whole competition, not one team.
- **"Debug one month"** in the same panel spot-checks a single month
  directly against that API if something looks off, without re-running
  the whole season.
- **v10:** the match-centre scraper (for individual match IDs) uses
  `curl_cffi` (Chrome TLS impersonation) rather than a real browser —
  WhoScored embeds that page's event data directly in the HTML, so no
  JavaScript rendering is needed for it either.
- No kickoff or broadcast cut offsets are applied here on purpose — this
  tool is for pure collation of WhoScored's raw event clock, not
  broadcast-aligned timestamps. Feed the output into the WS Marker Tool
  if you need offset-corrected markers later.
- Failed match IDs (bad ID, page didn't load, WhoScored bot-blocked the
  request) are reported in an expandable warning rather than killing the
  whole batch.
- **v24:** hosting support — optional residential proxy (Streamlit secrets), one shared session per batch, pause between matches, retry-on-403 with fresh session, retry-failed button, 24h match cache, per-run cap, optional password.
- **v25:** scraper now impersonates the newest Chrome fingerprint instead of chrome120 — Cloudflare was challenging chrome120 on residential proxy IPs.

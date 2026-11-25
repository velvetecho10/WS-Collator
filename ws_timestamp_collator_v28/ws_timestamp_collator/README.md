# WS Timestamp Collator
**v28**

Batch-scrapes WhoScored match centre data for a list of match IDs, filters by
action type and/or player, and exports a styled spreadsheet of raw timestamps
(WhoScored's own in-game clock, no offset conversion) for clip retrieval.

## Quick start (local)

- **Windows:** double-click `Launch_WS_Timestamp_Collator.bat`
- **Mac/Linux:** run `./Launch_WS_Timestamp_Collator.sh` (you may need
  `chmod +x` on it once)

The Python environment lives outside this folder
(`%USERPROFILE%\.ws_tools\ws_timestamp_collator_venv` on Windows,
`~/.ws_tools/ws_timestamp_collator_venv` on Mac/Linux), so updating the app
never forces a reinstall. Running locally uses your own connection and needs
no proxy.

## Using it

1. *(Optional)* Open **Find match IDs by team & season**, paste a WhoScored
   competition fixtures URL, filter by team, and add the IDs to the batch box.
2. Paste match IDs (one per line or comma-separated). The ID is the number in
   the match URL: `whoscored.com/Matches/1953858/Live` → `1953858`.
3. Choose an action type, optionally filter to a player, and hit
   **Run batch scrape**.
4. Pick a workbook layout (one sheet per action type, per match, or
   everything on one sheet), optionally keep the Summary tab, hit
   **Build workbook**, then download.
5. *(Optional)* Build the **per-match archive** (one workbook per match, every
   event) or **generate Premiere XML / LosslessCut markers** for one match.

Scraping always downloads the whole match, so filters only trim the output.
Re-running the same match within 24 hours uses the cache and costs nothing.

### Action filters
Literal event types (Pass, TakeOn, Tackle…), qualifier-based categories (Shot,
KeyPass, Cross, Corner, ThroughBall, Header, Freekick, LongBall), and
coordinate-based tactical filters (⚡ Box entry, Final third entry, Switch of
play, Diagonal long ball, Deep completion, Progressive 10m+). The tactical
columns are calculated from pitch coordinates already in the match data, so
they add no extra requests.

### Columns
`match_num`, `date`, `match_id`, `home_team`, `away_team`, `H/A`, `team`,
`player`, `period`, `timestamp`, `minute`, `second`, `type`, `outcome`,
`start_zone`, `end_zone`, `prog_distance`, `xT`, then tick columns for each
flag. `match_num` is a chronological count within the export, not the official
gameweek.

### File names
Exports name themselves from what's in them: player (if filtered), team,
action, date range and match count, e.g.
`Eberechi_Eze_Crystal_Palace_TakeOn_2023-08-12_to_2024-05-19_26matches.xlsx`.
The team is the club that appears in every match; a single unfiltered match
reads `Home_v_Away`. The per-match archive zip and marker files follow the same
pattern (marker files start with the match date).

## Hosting on Streamlit Cloud

WhoScored returns 403 to datacentre IPs, which includes Streamlit Cloud, so a
hosted copy needs a **residential proxy** (UK, sticky session).

1. Put the app files at the **root of your GitHub repo** and set the app's
   main file to `App.py`.
2. In Streamlit Cloud choose **Python 3.12** (newer versions may lack package
   builds).
3. App → Settings → Secrets: paste the contents of
   `.streamlit/secrets.toml.example` with your real values. Plain keys must
   come before the `[proxy]` section. Never commit real secrets.
4. Hosted extras switch on automatically: shared session with pacing, retry
   with a fresh session on 403, a **Retry failed matches** button, a per-run
   match cap (`max_matches`, default 40), 24-hour caching, and an optional
   `app_password`.

The scraper impersonates the newest Chrome fingerprint `curl_cffi` supports;
the older pinned `chrome120` was being challenged by Cloudflare on residential
IPs. Real proxy cost is roughly 0.2 MB per match.

## Desktop .exe (optional)

See `BUILD_EXE.md`. It's a one-time build you run yourself on Windows
(PyInstaller + pywebview) and hasn't been verified end to end.

## Credits

Tactical flags and the xT grid are adapted from ClipMaker (MIT, © 2026 B4L1);
see `config/CLIPMAKER_LICENSE`.

## Changelog

- **v28:** file names now include player, team, action, date range and match
  count (zip and marker files too).
- **v27:** final clean-up. Coordinate-derived columns (zones, xT, progressive
  distance, tactical flags) and tactical filters ported from the WS Marker
  Tool. Version removed from the page title (it's in the sidebar footer).
  Repo layout no longer carries a version in its folder name.
- **v26:** spreadsheet styling engine from the Marker Tool (`xlsx_style.py`):
  coloured event types, H/A column, DD/MM/YYYY dates, ticks, Summary tab, layout choice.
- **v25:** newest Chrome fingerprint instead of `chrome120`.
- **v24:** hosting support (proxy via secrets, shared session, pacing, retry,
  cache, run cap, optional password).
- **v1–v23:** fixture finder, per-match archive, "All Events", marker
  generation, desktop-exe build path, and numerous scraper fixes (notably
  qualifier-based Shot/KeyPass detection and the `match_num` column).

"""
WS Timestamp Collator
Batch-scrapes WhoScored match centre data for a list of match IDs, filters
for a chosen action type, and outputs a flat spreadsheet of raw timestamps
(minute/second, no offset conversion) for clip retrieval.
"""

import io
import re
import zipfile

import pandas as pd
import streamlit as st
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from scraper import scrape_match, events_to_rows
from fixture_finder import find_matches_from_fixtures_url, debug_month_data
import marker_generator as mg
import style

APP_VERSION = "v23"

st.set_page_config(page_title=f"WS Timestamp Collator {APP_VERSION}", layout="wide")
style.inject()
st.title(f"WS Timestamp Collator {APP_VERSION}")
st.caption(
    "Batch-scrape WhoScored match IDs, filter by action type, and export a "
    "spreadsheet of raw timestamps for clip retrieval. No offset conversion — "
    "these are WhoScored's own in-game clock values."
)

def build_styled_workbook(df: pd.DataFrame) -> io.BytesIO:
    """
    Writes df to an .xlsx with a bold header row, frozen top row, an
    autofilter, sensible column widths, and light colour-coding on the
    outcome column — plain pandas.to_excel output is functional but
    unreadable once there are more than a screen's worth of rows.
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Timestamps")
        ws = writer.sheets["Timestamps"]

        header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
        header_font = Font(name="Arial", bold=True, color="FFFFFF")
        body_font = Font(name="Arial")
        success_font = Font(name="Arial", color="1A7F37")
        fail_font = Font(name="Arial", color="B42318")

        outcome_col_idx = None
        for col_idx, col_name in enumerate(df.columns, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if col_name == "outcome":
                outcome_col_idx = col_idx

            max_len = max(
                [len(str(col_name))] + [len(str(v)) for v in df[col_name].astype(str)]
            )
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 3, 40)

        band_fill_a = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
        band_fill_b = PatternFill(start_color="EEF2F7", end_color="EEF2F7", fill_type="solid")
        match_num_col_idx = None
        for col_idx, col_name in enumerate(df.columns, start=1):
            if col_name == "match_num":
                match_num_col_idx = col_idx

        for row_idx in range(2, len(df) + 2):
            # Shade by match_num so alternating BANDS mark match boundaries
            # (not alternating individual rows) — makes it obvious at a
            # glance where one game's events end and the next begins,
            # even when one match has many more rows than another.
            band_fill = band_fill_a
            if match_num_col_idx:
                match_num_val = ws.cell(row=row_idx, column=match_num_col_idx).value
                if match_num_val is not None and int(match_num_val) % 2 == 0:
                    band_fill = band_fill_b

            for col_idx in range(1, len(df.columns) + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.font = body_font
                cell.fill = band_fill
                if col_idx == outcome_col_idx:
                    val = str(cell.value or "")
                    if val == "Successful":
                        cell.font = success_font
                    elif val == "Unsuccessful":
                        cell.font = fail_font

        ws.freeze_panes = "A2"
        last_col_letter = get_column_letter(len(df.columns))
        ws.auto_filter.ref = f"A1:{last_col_letter}{len(df) + 1}"
        ws.row_dimensions[1].height = 20

    buf.seek(0)
    return buf


def build_filename(action_type: str, player_filter: str, match_count: int) -> str:
    """
    'action_timestamps.xlsx' every time tells you nothing a week later —
    build something that names what's actually in the file.
    """
    parts = []
    if player_filter.strip():
        parts.append(player_filter.strip())
    parts.append(action_type)
    parts.append(f"{match_count}matches")
    parts.append("timestamps")
    name = "_".join(parts)
    name = re.sub(r"[^\w\-]+", "_", name).strip("_")
    return f"{name}.xlsx"


def sanitize_filename_part(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(s)).strip("_")


PER_MATCH_COLUMNS = [
    "date",
    "match_id",
    "home_team",
    "away_team",
    "team",
    "player",
    "period",
    "timestamp",
    "minute",
    "second",
    "type",
    "outcome",
    "is_shot",
    "is_key_pass",
    "is_cross",
    "is_corner",
    "is_through_ball",
    "is_header",
    "is_freekick",
    "is_long_ball",
]


def build_per_match_zip(raw_df: pd.DataFrame, player_filter: str = "") -> io.BytesIO:
    """
    Builds one styled .xlsx per match — every event TYPE, no action-type
    filter applied — so the batch scrape you just ran becomes a permanent
    local archive you don't need to hit WhoScored for again later, whether
    that's for building markers, LosslessCut CSVs, or anything else down
    the line. If player_filter is set, only that player's events are
    included (still every action type they did, just not the whole squad).
    """
    df = raw_df
    if player_filter.strip():
        df = df[df["player"].str.contains(player_filter.strip(), case=False, na=False)]

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for match_id, group in df.groupby("match_id"):
            if group.empty:
                continue
            g = group.sort_values(["minute", "second"]).reset_index(drop=True)
            g = g[[c for c in PER_MATCH_COLUMNS if c in g.columns]]

            date = group["date"].iloc[0]
            home = group["home_team"].iloc[0]
            away = group["away_team"].iloc[0]

            # Use the actual resolved player name (not just the filter
            # text) when the filter narrows this match down to exactly
            # one player — makes it obvious at a glance in the file
            # browser whose archive this is, without guessing from a
            # partial filter string. Falls back to no player name when
            # the archive covers more than one player (filter blank, or
            # the filter still matched several squad members).
            unique_players = group["player"].dropna().unique()
            name_parts = [date]
            if player_filter.strip() and len(unique_players) == 1:
                name_parts.append(unique_players[0])
            name_parts += [home, "vs", away, str(match_id)]

            match_buf = build_styled_workbook(g)
            fname = sanitize_filename_part("_".join(name_parts)) + ".xlsx"
            zf.writestr(fname, match_buf.getvalue())

    zip_buf.seek(0)
    return zip_buf


# WhoScored's raw event 'type' values — these match df["type"] directly.
RAW_TYPES = [
    "Goal",
    "SavedShot",
    "MissedShot",
    "ShotOnPost",
    "AttemptSaved",
    "BlockedShot",
    "Pass",
    "TakeOn",
    "Tackle",
    "Interception",
    "Clearance",
    "Foul",
    "Foul Drawn",
    "Card",
    "SubstitutionOn",
    "SubstitutionOff",
]

# Things people naturally think of as "an action type" but which WhoScored
# actually stores as a qualifier tag on some other event, or as a set of
# several type values — not a literal type string. Filtering these with a
# plain type== check always returns zero, which was a real bug in earlier
# versions ("Shot" and "KeyPass" could never match anything). These map to
# a derived boolean column instead (see scraper.py's is_shot/is_key_pass/etc).
CATEGORY_FILTERS = {
    "Shot": "is_shot",
    "KeyPass": "is_key_pass",
    "Cross": "is_cross",
    "Corner": "is_corner",
    "ThroughBall": "is_through_ball",
    "Header": "is_header",
    "Freekick": "is_freekick",
    "LongBall": "is_long_ball",
}

COMMON_TYPES = ["All Events"] + list(CATEGORY_FILTERS.keys()) + RAW_TYPES + ["Custom…"]

with st.sidebar:
    st.header("Settings")
    nav_timeout = st.number_input(
        "Request timeout (seconds)", min_value=5, max_value=120, value=30
    )
    st.markdown("---")
    st.caption(
        "Match IDs are the numeric ID in a WhoScored match URL, e.g. "
        "`whoscored.com/Matches/1953858/Live` → `1953858`"
    )
    st.markdown("---")
    st.caption(f"WS Timestamp Collator {APP_VERSION}")

if "match_id_box" not in st.session_state:
    st.session_state.match_id_box = ""

with st.expander("🔎 Find match IDs by team & season", expanded=False):
    st.caption(
        "WhoScored doesn't have a per-team season archive, but competitions "
        "do — navigate to the competition + season you want on WhoScored "
        "(e.g. Premier League → 2022/2023 → Fixtures) and paste that page's "
        "URL below. This fetches the whole season via WhoScored's own "
        "month-by-month data API and filters to whichever team you name, "
        "so you get every match that team played without needing to know "
        "the exact dates in advance."
    )
    fixtures_url = st.text_input(
        "Fixtures page URL",
        placeholder="https://www.whoscored.com/regions/252/tournaments/2/seasons/9075/stages/20934/fixtures/england-premier-league-2022-2023",
        key="finder_url",
    )
    finder_team_filter = st.text_input(
        "Team name (optional — leave blank to pull the whole competition/season)",
        placeholder="e.g. Crystal Palace",
        key="finder_team_filter",
    )

    fbtn1, fbtn2 = st.columns([1, 1])
    with fbtn1:
        find_run = st.button("Find match IDs")
    with fbtn2:
        month_debug_run_toggle = st.button("Debug one month")

    if find_run:
        if not fixtures_url.strip():
            st.error("Paste a fixtures page URL first.")
        elif "whoscored.com" not in fixtures_url:
            st.error("That doesn't look like a WhoScored URL.")
        else:
            with st.spinner("Loading the season's fixtures (this fetches ~12 months)…"):
                try:
                    matches = find_matches_from_fixtures_url(
                        fixtures_url.strip(),
                        team_filter=finder_team_filter.strip() or None,
                        nav_timeout_ms=nav_timeout * 1000,
                    )
                    st.session_state.finder_results = matches
                except Exception as e:
                    st.session_state.finder_results = []
                    st.error(f"Couldn't load fixtures: {e}")

    if month_debug_run_toggle:
        st.session_state.show_month_debug = True

    if st.session_state.get("show_month_debug"):
        st.markdown("---")
        st.caption(
            "Spot-check a single month directly against WhoScored's data "
            "API — useful if the full season pull above looks wrong."
        )
        month_input = st.text_input(
            "Month to check (YYYYMM)", placeholder="e.g. 202304", key="finder_month"
        )
        if st.button("Fetch month"):
            if not fixtures_url.strip() or not month_input.strip():
                st.error("Need both the fixtures URL above and a YYYYMM month.")
            else:
                with st.spinner("Fetching…"):
                    try:
                        st.session_state.month_debug = debug_month_data(
                            fixtures_url.strip(),
                            month_input.strip(),
                            nav_timeout_ms=nav_timeout * 1000,
                        )
                    except Exception as e:
                        st.session_state.month_debug = {"error": str(e)}

        month_debug = st.session_state.get("month_debug")
        if month_debug:
            if month_debug.get("error"):
                st.error(month_debug["error"])
            else:
                st.write(f"**URL:** `{month_debug.get('url')}`")
                st.write(
                    f"**Status:** {month_debug.get('status')}  \n"
                    f"**Matches parsed:** {month_debug.get('matches_found', 0)}"
                )
                st.code(month_debug.get("raw") or "(empty)", language="json")

    matches = st.session_state.get("finder_results")
    if matches:
        st.success(f"Found {len(matches)} match(es).")
        for m in matches:
            st.caption(f"`{m['match_id']}` — {m['row_text']}")
        found_ids = "\n".join(str(m["match_id"]) for m in matches)
        if st.button("Add these IDs to the batch box below"):
            existing = st.session_state.match_id_box.strip()
            st.session_state.match_id_box = (
                (existing + "\n" + found_ids).strip() if existing else found_ids
            )
            st.session_state.finder_results = []
            st.rerun()
    elif matches == []:
        st.warning(
            "No matches found. If you gave a team name, double check the "
            "spelling matches WhoScored's own team name — try 'Debug one "
            "month' above to see the raw team names for a specific month."
        )

match_id_input = st.text_area(
    "Match IDs (comma or newline separated)",
    placeholder="1953858\n1953859\n1953860",
    height=120,
    key="match_id_box",
)

action_choice = st.selectbox("Action type to collate", COMMON_TYPES, index=1)
if action_choice in CATEGORY_FILTERS:
    st.caption(
        f"'{action_choice}' isn't a single WhoScored event type — it's "
        "detected from tags on other events (e.g. a key pass is a normal "
        "Pass with a KeyPass tag), so this matches across whichever "
        "underlying event types actually carry that tag."
    )
custom_type = ""
if action_choice == "Custom…":
    custom_type = st.text_input(
        "Enter exact WhoScored event type name",
        help="Must match WhoScored's raw type exactly — e.g. 'SavedShot', not 'Shot'.",
    )

player_filter = st.text_input(
    "Filter to a specific player (optional, leave blank for all players)"
)

run = st.button("Run batch scrape", type="primary")

if "results_df" not in st.session_state:
    st.session_state.results_df = None

if run:
    raw_ids = [x.strip() for x in match_id_input.replace(",", "\n").splitlines() if x.strip()]
    match_ids = []
    for x in raw_ids:
        try:
            match_ids.append(int(x))
        except ValueError:
            st.warning(f"Skipping invalid match ID: {x}")

    if not match_ids:
        st.error("No valid match IDs provided.")
    else:
        target_type = custom_type.strip() if action_choice == "Custom…" else action_choice

        all_rows = []
        failed = []
        progress = st.progress(0.0, text="Starting…")
        status = st.empty()

        for i, mid in enumerate(match_ids):
            status.write(f"Scraping match {mid} ({i + 1}/{len(match_ids)})…")
            try:
                mcd = scrape_match(mid, nav_timeout_ms=nav_timeout * 1000)
                rows = events_to_rows(mid, mcd)
                all_rows.extend(rows)
            except Exception as e:
                failed.append((mid, str(e)))
            progress.progress((i + 1) / len(match_ids), text=f"{i + 1}/{len(match_ids)} matches done")

        status.empty()
        progress.empty()

        if failed:
            with st.expander(f"⚠️ {len(failed)} match(es) failed to scrape"):
                for mid, err in failed:
                    st.write(f"**{mid}**: {err}")

        if not all_rows:
            st.error("No events collected. Check match IDs and try again.")
        else:
            raw_df = pd.DataFrame(all_rows)
            raw_df["timestamp"] = raw_df.apply(
                lambda r: f"{int(r['minute']):02d}:{int(r['second'] or 0):02d}", axis=1
            )
            # Every event TYPE, no action-type filter — kept separately so
            # the per-match archive doesn't need a re-scrape later just
            # because today's action-type filter was narrower. Does still
            # respect the player filter from this run, though — "archive
            # everything" means every action Olise took, not literally
            # every player on the pitch.
            st.session_state.raw_events_df = raw_df
            st.session_state.raw_events_player_filter = player_filter.strip()

            df = raw_df.copy()

            if action_choice == "All Events":
                pass  # no type filter — keep everything
            elif action_choice in CATEGORY_FILTERS:
                flag_col = CATEGORY_FILTERS[action_choice]
                df = df[df[flag_col] == True]  # noqa: E712 - explicit bool compare reads clearer here
            else:
                df = df[df["type"] == target_type]

            if player_filter.strip():
                df = df[df["player"].str.contains(player_filter.strip(), case=False, na=False)]

            if df.empty:
                st.warning(
                    f"No '{target_type}' events found"
                    + (f" for player matching '{player_filter.strip()}'" if player_filter.strip() else "")
                    + " in the scraped match(es). Nothing to export."
                )
                st.session_state.results_df = None
            else:
                # Sequential match number, chronological by date within this
                # export — NOT the official competition gameweek/matchday,
                # since postponed or rearranged fixtures can shift those out
                # of date order. Labelled "match_num" rather than "matchday"
                # for that reason.
                match_order = (
                    df[["match_id", "date"]]
                    .drop_duplicates()
                    .sort_values(["date", "match_id"])
                    .reset_index(drop=True)
                )
                match_order["match_num"] = match_order.index + 1
                df = df.merge(match_order[["match_id", "match_num"]], on="match_id", how="left")

                df = df[
                    [
                        "match_num",
                        "date",
                        "match_id",
                        "home_team",
                        "away_team",
                        "team",
                        "player",
                        "period",
                        "timestamp",
                        "minute",
                        "second",
                        "type",
                        "outcome",
                    ]
                ].sort_values(["date", "match_id", "minute", "second"])

                st.session_state.results_df = df
                st.session_state.results_meta = {
                    "action_type": target_type,
                    "player_filter": player_filter.strip(),
                }

if st.session_state.results_df is not None:
    df = st.session_state.results_df
    meta = st.session_state.get("results_meta", {"action_type": "events", "player_filter": ""})
    st.success(f"Collected {len(df)} events across {df['match_id'].nunique()} match(es).")
    st.dataframe(df, use_container_width=True, hide_index=True)

    buf = build_styled_workbook(df)
    filename = build_filename(meta["action_type"], meta["player_filter"], df["match_id"].nunique())

    st.download_button(
        "Download spreadsheet (.xlsx)",
        data=buf,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

raw_df = st.session_state.get("raw_events_df")
if raw_df is not None and not raw_df.empty:
    st.markdown("---")
    archive_player_filter = st.session_state.get("raw_events_player_filter", "")

    archive_scope_df = raw_df
    if archive_player_filter:
        archive_scope_df = raw_df[
            raw_df["player"].str.contains(archive_player_filter, case=False, na=False)
        ]
    n_matches_in_archive = archive_scope_df["match_id"].nunique()

    if archive_player_filter:
        st.caption(
            f"**Per-match archive** — every action type for players "
            f"matching '{archive_player_filter}' (same player filter as "
            f"your last scrape), no action-type filter, split into one "
            f"spreadsheet per match ({n_matches_in_archive} match(es) "
            f"they appear in). Keep these locally so you don't need to "
            f"re-scrape later for a different action type — useful for "
            f"building markers or clip lists match-by-match."
        )
    else:
        st.caption(
            f"**Per-match archive** — every event, every player, no "
            f"filters at all, split into one spreadsheet per match "
            f"({n_matches_in_archive} match(es) from your last scrape). "
            f"Keep these locally so you don't need to re-scrape later for "
            f"a different action type or player."
        )

    if st.button("Build per-match archive (.zip)"):
        with st.spinner("Building spreadsheets…"):
            zip_buf = build_per_match_zip(raw_df, player_filter=archive_player_filter)
        st.session_state.per_match_zip = zip_buf.getvalue()

    if st.session_state.get("per_match_zip"):
        fname_suffix = sanitize_filename_part(archive_player_filter) + "_" if archive_player_filter else ""
        st.download_button(
            "Download per-match archive (.zip)",
            data=st.session_state.per_match_zip,
            file_name=f"per_match_archive_{fname_suffix}{n_matches_in_archive}matches.zip",
            mime="application/zip",
        )

# ── Generate Markers (single match) ─────────────────────────────────────
raw_df_for_markers = st.session_state.get("raw_events_df")
if raw_df_for_markers is not None and not raw_df_for_markers.empty:
    st.markdown("---")
    st.subheader("🎬 Generate markers")
    st.caption(
        "Turn one match's events into Premiere Pro XML markers and/or a "
        "LosslessCut CSV — same format the WS Marker Tool produces, built "
        "directly from whatever you've already scraped above, no "
        "re-scraping needed."
    )

    match_options = (
        raw_df_for_markers[["match_id", "date", "home_team", "away_team"]]
        .drop_duplicates()
        .sort_values("date")
    )
    match_labels = {
        int(row.match_id): f"{row.date} — {row.home_team} vs {row.away_team} ({int(row.match_id)})"
        for row in match_options.itertuples()
    }
    selected_match_label = st.selectbox(
        "Match", list(match_labels.values()), key="marker_match_select"
    )
    selected_match_id = next(mid for mid, label in match_labels.items() if label == selected_match_label)

    match_df = raw_df_for_markers[raw_df_for_markers["match_id"] == selected_match_id]

    mk_col1, mk_col2 = st.columns(2)
    with mk_col1:
        marker_player_filter = st.text_input(
            "Player (optional, blank = all players in this match)", key="marker_player_filter"
        )
    with mk_col2:
        available_types = sorted(match_df["type"].dropna().unique().tolist())
        marker_types = st.multiselect(
            "Event types to include", available_types, default=available_types, key="marker_types"
        )

    marker_scope_df = match_df
    if marker_player_filter.strip():
        marker_scope_df = marker_scope_df[
            marker_scope_df["player"].str.contains(marker_player_filter.strip(), case=False, na=False)
        ]
    if marker_types:
        marker_scope_df = marker_scope_df[marker_scope_df["type"].isin(marker_types)]

    st.caption(f"{len(marker_scope_df)} event(s) match this selection.")

    st.markdown("**Kickoff timecodes** — where each half's kickoff lands in your video file")
    ko_col1, ko_col2 = st.columns(2)
    with ko_col1:
        tc1_input = st.text_input(
            "1st half kickoff (HH:MM:SS)", value="00:00:00", key="marker_tc1",
            help="Scrub to the exact kickoff frame in your editor and read the timecode.",
        )
    with ko_col2:
        tc2_input = st.text_input(
            "2nd half kickoff (HH:MM:SS)", value="00:45:00", key="marker_tc2",
        )

    with st.expander("Extra time / penalties (optional)"):
        had_et = st.checkbox("Match went to extra time", key="marker_had_et")
        tc_et1_input = tc_et2_input = "00:00:00"
        if had_et:
            et_col1, et_col2 = st.columns(2)
            with et_col1:
                tc_et1_input = st.text_input("ET 1st half kickoff", value="00:00:00", key="marker_tc_et1")
            with et_col2:
                tc_et2_input = st.text_input("ET 2nd half kickoff", value="00:00:00", key="marker_tc_et2")
        tc_pso_input = st.text_input(
            "Penalty shootout start (optional)", value="00:00:00", key="marker_tc_pso"
        ) if had_et else "00:00:00"

    with st.expander("Broadcast cuts (optional — only needed if your file has hydration breaks or other footage cut from a half)"):
        n_cuts = st.number_input(
            "Number of cuts", min_value=0, max_value=6, value=0, step=1, key="marker_n_cuts"
        )
        cut_offsets = []
        for i in range(int(n_cuts)):
            st.markdown(f"**Cut {i + 1}**")
            cc1, cc2, cc3 = st.columns([1, 1.4, 1.4])
            with cc1:
                cut_half = st.selectbox("Half", ["1st half", "2nd half"], key=f"marker_cut_half_{i}")
            with cc2:
                cut_match_time = st.text_input(
                    "Time into half when play resumed (mm:ss)", value="", key=f"marker_cut_match_{i}"
                )
            with cc3:
                cut_file_tc = st.text_input(
                    "File timecode at that moment", value="", key=f"marker_cut_file_{i}"
                )
            match_s = mg.parse_tc_to_secs(cut_match_time) if cut_match_time.strip() else None
            file_s = mg.parse_tc_to_secs(cut_file_tc) if cut_file_tc.strip() else None
            if match_s is not None and file_s is not None:
                half_key = "FirstHalf" if cut_half == "1st half" else "SecondHalf"
                cut_offsets.append((half_key, match_s, file_s))

    mk_col3, mk_col4, mk_col5 = st.columns(3)
    with mk_col3:
        pre_buffer = st.number_input("Pre-roll (secs before event)", min_value=0, max_value=120, value=5, key="marker_pre")
    with mk_col4:
        post_buffer = st.number_input("Post-roll (secs after event)", min_value=0, max_value=120, value=5, key="marker_post")
    with mk_col5:
        fps_key = st.selectbox("Frame rate", list(mg.FPS_OPTIONS.keys()), index=5, key="marker_fps")

    merge_overlaps = st.checkbox(
        "Merge overlapping clips into single segments", value=False, key="marker_merge"
    )

    if st.button("Generate markers", type="primary"):
        events_for_marking = [
            {
                "minute": int(r.minute),
                "second": int(r.second),
                "period": r.period,
                "type": r.type,
                "outcome": r.outcome,
                "playerName": r.player,
            }
            for r in marker_scope_df.itertuples()
        ]

        if not events_for_marking:
            st.warning("No events match this selection — nothing to generate.")
        else:
            tc1_s = mg.parse_tc_to_secs(tc1_input)
            tc2_s = mg.parse_tc_to_secs(tc2_input)
            tc_et1_s = mg.parse_tc_to_secs(tc_et1_input)
            tc_et2_s = mg.parse_tc_to_secs(tc_et2_input)
            tc_pso_s = mg.parse_tc_to_secs(tc_pso_input)

            enriched = mg.enrich_events(
                events_for_marking, tc1_s, tc2_s, tc_et1_s, tc_et2_s, tc_pso_s, cut_offsets=cut_offsets
            )

            home = match_options[match_options["match_id"] == selected_match_id]["home_team"].iloc[0]
            away = match_options[match_options["match_id"] == selected_match_id]["away_team"].iloc[0]
            seq_name = sanitize_filename_part(f"{home}_vs_{away}_{selected_match_id}")

            xml_str = mg.generate_xml(enriched, seq_name, fps_key, pre_buffer, post_buffer, merge_overlaps)
            csv_str = mg.generate_llc_csv(enriched, pre_buffer, post_buffer, merge_overlaps)

            st.session_state.marker_xml = xml_str
            st.session_state.marker_csv = csv_str
            st.session_state.marker_fname = seq_name
            st.success(f"Generated {len(enriched)} marker(s) for {seq_name}.")

    if st.session_state.get("marker_xml"):
        dl_col1, dl_col2 = st.columns(2)
        with dl_col1:
            st.download_button(
                "Download Premiere XML",
                data=st.session_state.marker_xml,
                file_name=f"{st.session_state.marker_fname}_markers.xml",
                mime="application/xml",
            )
        with dl_col2:
            st.download_button(
                "Download LosslessCut CSV",
                data=st.session_state.marker_csv,
                file_name=f"{st.session_state.marker_fname}_llc.csv",
                mime="text/csv",
            )

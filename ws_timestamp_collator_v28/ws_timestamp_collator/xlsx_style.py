"""
core/xlsx_style.py
Excel export engine for the Collator.

Three sheet layouts:
  "single"     — everything on one sheet (the v31 behaviour)
  "by_action"  — one sheet per event type (Shots / Pass / TakeOn / ...)
  "by_match"   — one sheet per match

Plus a Summary sheet, category colour-coding of the `type` column, text
wrapping on the wide columns, and tick marks instead of TRUE/FALSE — eight
columns of mostly-FALSE is unreadable at a glance.
"""

import io
import re

import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── Event categories ─────────────────────────────────────────────────────────
# Drives both the colour coding and the by_action sheet grouping. Keys are the
# WhoScored `type` values actually seen in scraped data (including ones the old
# hardcoded filter list omitted: BallRecovery, Aerial, BallTouch, Dispossessed,
# BlockedPass, OffsidePass, CornerAwarded).
CATEGORIES = {
    "Goal":            ("Goals",      "0B6B3A", "D6F3E3"),   # deep green on mint
    "Shots":           ("Shots",      "9A3412", "FFE9D6"),   # burnt orange on peach
    "Passing":         ("Passing",    "1E3A8A", "DCE6FA"),   # navy on pale blue
    "Dribbling":       ("Dribbling",  "6B21A8", "EEDCFB"),   # purple on lilac
    "Defending":       ("Defending",  "115E59", "D3EFEC"),   # teal on pale teal
    "Duels":           ("Duels",      "854D0E", "FBF0D0"),   # amber on cream
    "Discipline":      ("Discipline", "9F1239", "FBDDE5"),   # crimson on pink
    "Goalkeeping":     ("Goalkeeping","3F3F46", "E4E4E7"),   # slate on grey
    "Substitutions":   ("Subs",       "52525B", "ECECEF"),   # grey on light grey
    "Other":           ("Other",      "44403C", "F0EFED"),
}

TYPE_TO_CATEGORY = {
    "Goal": "Goal",
    "SavedShot": "Shots", "MissedShot": "Shots", "ShotOnPost": "Shots",
    "BlockedShot": "Shots", "AttemptSaved": "Shots",
    "Pass": "Passing", "OffsidePass": "Passing", "BlockedPass": "Passing",
    "Cross": "Passing", "CornerAwarded": "Passing",
    "TakeOn": "Dribbling", "Dispossessed": "Dribbling", "BallTouch": "Dribbling",
    "Tackle": "Defending", "Interception": "Defending", "Clearance": "Defending",
    "BallRecovery": "Defending", "Blocked": "Defending",
    "Aerial": "Duels", "Challenge": "Duels", "Foul": "Discipline",
    "Foul Drawn": "Discipline", "Card": "Discipline",
    "Save": "Goalkeeping", "Claim": "Goalkeeping", "Punch": "Goalkeeping",
    "KeeperPickup": "Goalkeeping", "Smother": "Goalkeeping",
    "SubstitutionOn": "Substitutions", "SubstitutionOff": "Substitutions",
}


def category_for(event_type: str) -> str:
    return TYPE_TO_CATEGORY.get(event_type, "Other")


# ── Column presentation ──────────────────────────────────────────────────────
HEADER_BG = "111827"
HEADER_FG = "FFFFFF"
BAND_A = "FFFFFF"
BAND_B = "F4F6F9"
GRID = "D9DEE7"

WRAP_COLS = {"home_team", "away_team", "team", "player", "type",
             "start_zone", "end_zone", "depth_zone"}
NARROW_COLS = {"match_num", "minute", "second", "period", "timestamp", "outcome",
               "ha", "prog_distance", "xT"}
COL_WIDTHS = {
    "match_num": 7, "date": 12, "match_id": 10,
    "home_team": 17, "away_team": 17, "ha": 5, "team": 17, "player": 18,
    "period": 12, "timestamp": 10, "minute": 8, "second": 8,
    "type": 16, "outcome": 12,
    "start_zone": 15, "end_zone": 15, "depth_zone": 15,
    "prog_distance": 10, "xT": 9,
}
FLAG_WIDTH = 11

# Column groups — a medium divider is drawn at each group's left edge so the
# sheet reads as blocks (which match / who / when / what) instead of 21 columns
# of undifferentiated text.
GROUP_STARTS = {"home_team", "period", "type", "start_zone", "is_shot"}

# Home/away tinting. The player's own team is bold and dark; the opponent is
# muted, so you can tell at a glance whether they were home or away without
# reading both names.
HOME_TINT = "E8EEF7"      # cool blue-grey
AWAY_TINT = "F7EFE8"      # warm sand
OWN_TEAM_FG = "111827"
OPP_TEAM_FG = "8A8F98"
HA_HOME_FG, HA_HOME_BG = "1E3A8A", "DCE6FA"
HA_AWAY_FG, HA_AWAY_BG = "9A3412", "FFE9D6"

DATE_FMT = "DD/MM/YYYY"

THIN = Side(style="thin", color=GRID)
MEDIUM = Side(style="medium", color="9AA4B2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BORDER_GROUP = Border(left=MEDIUM, right=THIN, top=THIN, bottom=THIN)


def _pretty_header(col: str) -> str:
    """is_key_pass -> Key Pass; match_num -> Match #."""
    if col == "match_num":
        return "Match #"
    if col == "match_id":
        return "Match ID"
    if col == "ha":
        return "H/A"
    if col.startswith("is_"):
        return col[3:].replace("_", " ").title()
    return col.replace("_", " ").title()


def add_venue_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Inserts an H/A column after away_team saying whether the player's own team
    was home or away. Derived, not scraped — it's the fastest way to read a row.
    """
    if not {"home_team", "away_team", "team"}.issubset(df.columns) or "ha" in df.columns:
        return df
    out = df.copy()
    ha = out.apply(
        lambda r: "H" if str(r["team"]).strip() == str(r["home_team"]).strip()
        else ("A" if str(r["team"]).strip() == str(r["away_team"]).strip() else ""),
        axis=1,
    )
    pos = list(out.columns).index("away_team") + 1
    out.insert(pos, "ha", ha)
    return out


def _safe_sheet_name(name: str, used: set) -> str:
    """Excel: <=31 chars, no []:*?/\\ , must be unique."""
    clean = re.sub(r"[\[\]\:\*\?\/\\]", "-", str(name)).strip() or "Sheet"
    clean = clean[:31]
    base, n = clean, 2
    while clean.lower() in used:
        suffix = f" ({n})"
        clean = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(clean.lower())
    return clean


def _write_sheet(writer, df: pd.DataFrame, sheet_name: str, band_by: str = "match_num"):
    """Writes one styled sheet."""
    df = add_venue_column(df)
    flag_cols = [c for c in df.columns if c.startswith("is_")]
    out = df.copy()
    # Ticks read far better than eight columns of FALSE. Still filterable
    # (blank vs "✓") and still sortable.
    for c in flag_cols:
        out[c] = out[c].map(lambda v: "✓" if bool(v) else "")
    # Real dates, displayed DD/MM/YYYY — kept as datetimes so sorting stays
    # chronological rather than alphabetical.
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")

    out.columns = [_pretty_header(c) for c in out.columns]
    out.to_excel(writer, index=False, sheet_name=sheet_name)
    ws = writer.sheets[sheet_name]

    orig_cols = list(df.columns)
    ncols, nrows = len(orig_cols), len(out)
    idx = {c: i + 1 for i, c in enumerate(orig_cols)}

    header_fill = PatternFill("solid", start_color=HEADER_BG, end_color=HEADER_BG)
    header_font = Font(name="Calibri", bold=True, color=HEADER_FG, size=11)
    for i, col in enumerate(orig_cols, start=1):
        cell = ws.cell(row=1, column=i)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER_GROUP if col in GROUP_STARTS else BORDER
        width = FLAG_WIDTH if col.startswith("is_") else COL_WIDTHS.get(col)
        if width is None:
            longest = max([len(_pretty_header(col))] +
                          [len(str(v)) for v in df[col].astype(str).head(400)])
            width = min(max(longest + 2, 10), 28)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 42

    band_idx = idx.get(band_by) if band_by else None
    type_idx = idx.get("type")
    outcome_idx = idx.get("outcome")
    home_idx, away_idx, team_idx, ha_idx = (idx.get("home_team"), idx.get("away_team"),
                                            idx.get("team"), idx.get("ha"))
    date_idx = idx.get("date")

    body_font = Font(name="Calibri", size=10)
    ok_font = Font(name="Calibri", size=10, color="1A7F37", bold=True)
    bad_font = Font(name="Calibri", size=10, color="B42318", bold=True)
    tick_font = Font(name="Calibri", size=11, color="0B6B3A", bold=True)
    own_font = Font(name="Calibri", size=10, bold=True, color=OWN_TEAM_FG)
    opp_font = Font(name="Calibri", size=10, color=OPP_TEAM_FG)

    for r in range(2, nrows + 2):
        band = BAND_A
        if band_idx:
            v = ws.cell(row=r, column=band_idx).value
            try:
                if v is not None and int(v) % 2 == 0:
                    band = BAND_B
            except (TypeError, ValueError):
                pass
        band_fill = PatternFill("solid", start_color=band, end_color=band)

        for c in range(1, ncols + 1):
            cell = ws.cell(row=r, column=c)
            col_name = orig_cols[c - 1]
            cell.font = body_font
            cell.fill = band_fill
            cell.border = BORDER_GROUP if col_name in GROUP_STARTS else BORDER
            wrap = col_name in WRAP_COLS
            align = "center" if (col_name in NARROW_COLS or col_name.startswith("is_")) else "left"
            cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)

            if col_name.startswith("is_") and cell.value == "✓":
                cell.font = tick_font
            elif c == outcome_idx:
                val = str(cell.value or "")
                if val == "Successful":
                    cell.font = ok_font
                elif val == "Unsuccessful":
                    cell.font = bad_font

        if date_idx:
            ws.cell(row=r, column=date_idx).number_format = DATE_FMT

        # xT and progressive distance: green when the action moved the ball
        # toward goal, red when it went backwards. Blank for non-move events.
        for col_key, fmt in (("xT", "0.000"), ("prog_distance", "0.0")):
            ci = idx.get(col_key)
            if not ci:
                continue
            cell = ws.cell(row=r, column=ci)
            try:
                v = float(cell.value)
            except (TypeError, ValueError):
                continue
            cell.number_format = fmt
            if v > 0:
                cell.font = Font(name="Calibri", size=10, bold=True, color="0B6B3A")
            elif v < 0:
                cell.font = Font(name="Calibri", size=10, color="B42318")

        # Home / away: tint the two columns differently and bold whichever is
        # the player's own side.
        own = str(ws.cell(row=r, column=team_idx).value or "").strip() if team_idx else ""
        for col_idx, tint in ((home_idx, HOME_TINT), (away_idx, AWAY_TINT)):
            if not col_idx:
                continue
            cell = ws.cell(row=r, column=col_idx)
            cell.fill = PatternFill("solid", start_color=tint, end_color=tint)
            same = own and str(cell.value or "").strip() == own
            cell.font = own_font if same else opp_font

        if ha_idx:
            cell = ws.cell(row=r, column=ha_idx)
            v = str(cell.value or "")
            if v == "H":
                cell.fill = PatternFill("solid", start_color=HA_HOME_BG, end_color=HA_HOME_BG)
                cell.font = Font(name="Calibri", size=10, bold=True, color=HA_HOME_FG)
            elif v == "A":
                cell.fill = PatternFill("solid", start_color=HA_AWAY_BG, end_color=HA_AWAY_BG)
                cell.font = Font(name="Calibri", size=10, bold=True, color=HA_AWAY_FG)

        # Colour the type cell by its category — the main visual cue.
        if type_idx:
            tcell = ws.cell(row=r, column=type_idx)
            cat = category_for(str(tcell.value or ""))
            _label, fg, bg = CATEGORIES[cat]
            tcell.fill = PatternFill("solid", start_color=bg, end_color=bg)
            tcell.font = Font(name="Calibri", size=10, bold=True, color=fg)

    ws.freeze_panes = "A2"
    if nrows:
        ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}{nrows + 1}"


def _write_summary(writer, df: pd.DataFrame, layout: str):
    """Counts per category/type and per match — a 30-match pull needs a map."""
    ws = writer.book.create_sheet("Summary", 0)
    title_font = Font(name="Calibri", size=14, bold=True, color="111827")
    head_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", start_color=HEADER_BG, end_color=HEADER_BG)
    body = Font(name="Calibri", size=10)

    # A caller-supplied column subset may have dropped any of these, so every
    # lookup below is guarded rather than assumed.
    has = lambda c: c in df.columns

    ws["A1"] = "Collator Export — Summary"
    ws["A1"].font = title_font
    bits = [f"{len(df)} events"]
    if has("match_id"):
        bits.append(f"{df['match_id'].nunique()} matches")
    if has("player"):
        bits.append(f"{df['player'].nunique()} player(s)")
    bits.append(f"layout: {layout}")
    ws["A2"] = " · ".join(bits)
    ws["A2"].font = Font(name="Calibri", size=10, color="52525B")

    r = 4
    if has("type"):
        ws.cell(row=r, column=1, value="By event type").font = Font(bold=True, size=11)
        r += 1
        for c, h in enumerate(["Category", "Type", "Count"], start=1):
            cell = ws.cell(row=r, column=c, value=h)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center")
        r += 1
        for t, n in df["type"].value_counts().items():
            cat = category_for(t)
            _lbl, fg, bg = CATEGORIES[cat]
            c1 = ws.cell(row=r, column=1, value=CATEGORIES[cat][0])
            c1.font = Font(size=10, bold=True, color=fg)
            c1.fill = PatternFill("solid", start_color=bg, end_color=bg)
            ws.cell(row=r, column=2, value=t).font = body
            ws.cell(row=r, column=3, value=int(n)).font = body
            r += 1
        r += 1

    if has("match_id") and has("home_team") and has("away_team"):
        ws.cell(row=r, column=1, value="By match").font = Font(bold=True, size=11)
        r += 1
        for c, h in enumerate(["Date", "Fixture", "Events"], start=1):
            cell = ws.cell(row=r, column=c, value=h)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center")
        r += 1
        for _mid, g in df.groupby("match_id", sort=False):
            dcell = ws.cell(row=r, column=1)
            if has("date"):
                d = pd.to_datetime(g["date"].iloc[0], errors="coerce")
                if pd.notna(d):
                    dcell.value = d.to_pydatetime()
                    dcell.number_format = DATE_FMT
                else:
                    dcell.value = str(g["date"].iloc[0])
            dcell.font = body
            ws.cell(row=r, column=2,
                    value=f"{g['home_team'].iloc[0]} v {g['away_team'].iloc[0]}").font = body
            ws.cell(row=r, column=3, value=int(len(g))).font = body
            r += 1

    for col, w in [(1, 16), (2, 34), (3, 10)]:
        ws.column_dimensions[get_column_letter(col)].width = w


def build_workbook(df: pd.DataFrame, layout: str = "single",
                   include_summary: bool = True,
                   columns: list = None) -> io.BytesIO:
    """
    layout:
      "single"    one sheet, everything
      "by_action" one sheet per event type
      "by_match"  one sheet per match
    """
    if columns:
        df = df[[c for c in columns if c in df.columns]]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        used = set()

        if layout == "by_action":
            order = df["type"].value_counts().index.tolist()
            for t in order:
                sub = df[df["type"] == t]
                if sub.empty:
                    continue
                name = _safe_sheet_name(f"{t} ({len(sub)})", used)
                _write_sheet(writer, sub.reset_index(drop=True), name, band_by="match_num")

        elif layout == "by_match":
            for mid, g in df.groupby("match_id", sort=False):
                num = g["match_num"].iloc[0] if "match_num" in g.columns else 0
                d = pd.to_datetime(g["date"].iloc[0], errors="coerce") if "date" in g.columns else None
                dpart = d.strftime("%d-%m") if d is not None and pd.notna(d) else ""
                label = (f"{int(num):02d} {dpart} {g['home_team'].iloc[0]} v "
                         f"{g['away_team'].iloc[0]}").replace("  ", " ")
                name = _safe_sheet_name(label, used)
                _write_sheet(writer, g.reset_index(drop=True), name, band_by=None)

        else:
            _write_sheet(writer, df.reset_index(drop=True),
                         _safe_sheet_name("Timestamps", used), band_by="match_num")

        if include_summary and len(df):
            _write_summary(writer, df, layout)

    buf.seek(0)
    return buf

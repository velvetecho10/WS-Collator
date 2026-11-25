"""
fixture_finder.py
Finds WhoScored match IDs for a whole competition season, given a pasted
fixtures page URL (e.g.
https://www.whoscored.com/regions/252/tournaments/2/seasons/9075/stages/20934/fixtures/england-premier-league-2022-2023).

Confirmed via a real browser HAR capture (not guessed) that WhoScored's
fixtures page pulls each month's data from a clean, well-structured JSON
API:

    https://www.whoscored.com/tournaments/{stageId}/data/?d={YYYYMM}&isAggregate=false

Response shape (trimmed):
    {
      "tournaments": [{
        "stageId": 20934, "seasonName": "2022/2023", ...
        "matches": [{
          "id": 1640947, "startTime": "2023-04-01T12:30:00",
          "homeTeamId": 167, "homeTeamName": "Manchester City",
          "awayTeamId": 26,  "awayTeamName": "Liverpool",
          "homeScore": 4, "awayScore": 1, "elapsed": "FT", ...
        }, ...]
      }]
    }

Confirmed across a real 10-month capture that this endpoint returns 380
matches total for one Premier League season (20 teams x 38 games / 2) —
exactly right, so the month-by-month loop genuinely covers the whole
season with no gaps. No HTML scraping needed for this part at all.
"""

import re
import time
from typing import Optional

from scraper import _make_session

STAGE_ID_PATTERN = re.compile(r"/stages/(\d+)")
SEASON_SLUG_PATTERN = re.compile(r"(\d{4})-(\d{4})(?:$|/)")


def _extract_stage_id(fixtures_url: str) -> int:
    m = STAGE_ID_PATTERN.search(fixtures_url)
    if not m:
        raise ValueError(
            "Couldn't find a stage ID in that URL — expected something like "
            "'.../stages/20934/...'. Paste the full fixtures page URL from "
            "WhoScored's address bar."
        )
    return int(m.group(1))


def _month_range_from_url(fixtures_url: str) -> list[str]:
    """
    Guesses the season's month range (YYYYMM strings) from a "2022-2023"
    style slug in the URL. Covers July of the start year through June of
    the end year — a one-month buffer either side of the typical
    August-to-May season, to catch early-starting or delayed fixtures
    without needing to fetch a full extra year.
    """
    m = SEASON_SLUG_PATTERN.search(fixtures_url)
    if not m:
        raise ValueError(
            "Couldn't find a season (e.g. '2022-2023') in that URL to work "
            "out which months to fetch. Paste the full fixtures page URL."
        )
    year1, year2 = int(m.group(1)), int(m.group(2))
    months = []
    for y, start_m in ((year1, 7), (year2, 1)):
        end_m = 12 if y == year1 else 6
        for mo in range(start_m, end_m + 1):
            months.append(f"{y}{mo:02d}")
    return months


def _fetch_month(stage_id: int, yyyymm: str, session, timeout_s: float) -> list[dict]:
    url = f"https://www.whoscored.com/tournaments/{stage_id}/data/?d={yyyymm}&isAggregate=false"
    resp = session.get(url, timeout=timeout_s)
    if resp.status_code != 200:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    tournaments = data.get("tournaments") or []
    if not tournaments:
        return []
    return tournaments[0].get("matches") or []


def find_matches_from_fixtures_url(
    fixtures_url: str,
    team_filter: Optional[str] = None,
    nav_timeout_ms: int = 30000,
) -> list[dict]:
    """
    Loops every month of the season (worked out from the URL) through the
    confirmed tournament data API, and returns every match found as
    {match_id, row_text, home_team, away_team, date}. If team_filter is
    given, only matches involving a team whose name contains that text
    (case-insensitive) are kept — this happens client-side after fetching,
    since the API itself is tournament-wide, not team-scoped.
    """
    stage_id = _extract_stage_id(fixtures_url)
    months = _month_range_from_url(fixtures_url)
    session, _ = _make_session()

    try:
        session.get("https://www.whoscored.com", timeout=15)
        time.sleep(1.5)
    except Exception:
        pass

    all_matches = []
    for yyyymm in months:
        matches = _fetch_month(stage_id, yyyymm, session, nav_timeout_ms / 1000)
        all_matches.extend(matches)
        time.sleep(0.3)  # light pacing across ~12 requests

    results = []
    seen = set()
    for m in all_matches:
        mid = m.get("id")
        if mid is None or mid in seen:
            continue
        home = m.get("homeTeamName", "")
        away = m.get("awayTeamName", "")
        if team_filter:
            tf = team_filter.strip().lower()
            if tf not in home.lower() and tf not in away.lower():
                continue
        seen.add(mid)
        date = (m.get("startTime") or "")[:10]
        home_score = m.get("homeScore")
        away_score = m.get("awayScore")
        score = f"{home_score}-{away_score}" if home_score is not None else ""
        results.append(
            {
                "match_id": mid,
                "row_text": f"{date} {home} {score} {away}".strip(),
                "home_team": home,
                "away_team": away,
                "date": date,
            }
        )

    results.sort(key=lambda r: r["date"])
    return results


def debug_month_data(fixtures_url: str, yyyymm: str, nav_timeout_ms: int = 30000) -> dict:
    """
    Fetches one month directly from the confirmed data endpoint and
    returns the raw response plus a parsed match count, for spot-checking
    a specific month without running the whole season.
    """
    info = {
        "url": None,
        "status": None,
        "raw": "",
        "matches_found": 0,
        "error": None,
    }
    try:
        stage_id = _extract_stage_id(fixtures_url)
        session, _ = _make_session()
        try:
            session.get("https://www.whoscored.com", timeout=15)
            time.sleep(1.5)
        except Exception:
            pass

        url = f"https://www.whoscored.com/tournaments/{stage_id}/data/?d={yyyymm}&isAggregate=false"
        info["url"] = url
        resp = session.get(url, timeout=nav_timeout_ms / 1000)
        info["status"] = resp.status_code
        info["raw"] = resp.text[:3000]
        try:
            data = resp.json()
            tournaments = data.get("tournaments") or []
            if tournaments:
                info["matches_found"] = len(tournaments[0].get("matches") or [])
        except Exception:
            pass
    except Exception as e:
        info["error"] = str(e)

    return info

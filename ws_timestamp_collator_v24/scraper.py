"""
scraper.py
WhoScored match-centre scraping, rebuilt to match the approach proven out
in the WS Marker Tool: a curl_cffi session impersonating a real Chrome TLS
fingerprint, a warm-up GET to the homepage, then a direct GET on the match
URL — no browser automation needed, since WhoScored serves the event data
embedded directly in the initial HTML. This replaced an earlier
Playwright-based version that reliably hit empty/blocked responses on
team-level pages, almost certainly due to headless-browser fingerprinting
rather than anything about the page structure itself.
"""

import json
import os
import re
import time


def resolve_proxy_url() -> str | None:
    """
    Where the proxy comes from, in priority order:
      1. The sidebar override for this browser session (st.session_state)
      2. Streamlit secrets:  [proxy]  url = "http://user:pass@host:port"
      3. The WS_PROXY_URL environment variable
    Returns None for a direct connection (what you want when running locally
    from your own home IP).
    """
    try:
        import streamlit as st

        override = (st.session_state.get("proxy_override") or "").strip()
        if override:
            return override
        try:
            url = st.secrets.get("proxy", {}).get("url")
            if url:
                return str(url).strip()
        except Exception:
            pass  # no secrets file locally — completely normal
    except Exception:
        pass
    return (os.environ.get("WS_PROXY_URL") or "").strip() or None


def _make_session(proxy_url: str | None = None):
    """
    curl_cffi impersonates a real browser's TLS handshake, which is what
    gets past WhoScored's bot detection — a plain `requests` session with
    just a spoofed User-Agent header is not enough on its own. Falls back
    to plain requests if curl_cffi isn't installed, with a warning, since
    it may still get blocked.

    If a proxy is configured (see resolve_proxy_url) all traffic goes
    through it. Needed when hosting: WhoScored returns 403 to datacentre
    IPs such as Streamlit Cloud's, so a residential proxy is required there.
    """
    if proxy_url is None:
        proxy_url = resolve_proxy_url()
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    try:
        from curl_cffi import requests as r

        if proxies:
            return r.Session(impersonate="chrome120", proxies=proxies), True
        return r.Session(impersonate="chrome120"), True
    except ImportError:
        import requests

        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-GB,en;q=0.9",
            }
        )
        if proxies:
            s.proxies.update(proxies)
        return s, False


def _extract_json_object(text: str, start_idx: int) -> str | None:
    """
    String-aware brace counting so embedded quotes/braces inside JSON
    string values don't throw off the match — a plain regex on braces
    alone breaks as soon as any event field contains a literal '{' or '}'.
    """
    depth = 0
    in_string = False
    escape = False
    for i in range(start_idx, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx : i + 1]
    return None


def _find_match_data(html: str) -> dict | None:
    """
    WhoScored has used a few different variable names for the embedded
    match data over time, so this tries all of them before falling back
    to a generic search for any JSON object containing an "events" array.
    """
    for var in [
        "matchCentreData",
        "initialMatchDataForScorers",
        "matchCentreEventTypeJson",
        "matchCentreEventData",
        "matchCentreEventsData",
        "matchData",
        "matchCentreJsonData",
    ]:
        for sep in (f"{var} = {{", f"{var}={{", f"{var}: {{", f"{var}:{{"):
            pos = html.find(sep)
            if pos != -1:
                raw = _extract_json_object(html, html.index("{", pos))
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    if len(data.get("events") or data.get("matchEvents") or []) > 5:
                        return data
                except Exception:
                    pass

    for m in re.finditer(r'"events"\s*:\s*\[', html):
        back = html[max(0, m.start() - 2000) : m.start()]
        lb = back.rfind("{")
        if lb == -1:
            continue
        raw = _extract_json_object(html, max(0, m.start() - 2000) + lb)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            if len(data.get("events") or data.get("matchEvents") or []) > 5:
                return data
        except Exception:
            pass

    return None


PERIOD_MAP = {
    1: "FirstHalf",
    2: "SecondHalf",
    3: "ExtraTimeFirstHalf",
    4: "ExtraTimeSecondHalf",
    5: "PenaltyShootout",
}
VALID_PERIODS = {
    "FirstHalf",
    "SecondHalf",
    "ExtraTimeFirstHalf",
    "ExtraTimeSecondHalf",
    "FirstPeriodOfExtraTime",
    "SecondPeriodOfExtraTime",
    "PenaltyShootout",
}


def _parse_period(raw) -> str:
    if isinstance(raw, dict):
        val = raw.get("value")
        if val in PERIOD_MAP:
            return PERIOD_MAP[val]
        name = raw.get("displayName", "")
        return name if name else "Unknown"
    if isinstance(raw, int) and raw in PERIOD_MAP:
        return PERIOD_MAP[raw]
    return str(raw)


SHOT_TYPES = {"Goal", "SavedShot", "MissedShot", "ShotOnPost", "AttemptSaved", "BlockedShot"}


def _qualifiers(event: dict) -> set:
    """
    WhoScored events carry a separate 'qualifiers' list for tags like
    KeyPass, Cross, Head, CornerTaken, etc. — these are NOT the same as
    the event's primary 'type'. A key pass, for example, is a normal
    Pass-type event with a KeyPass qualifier attached, not its own type.
    Filtering on type alone misses these entirely — this was a real bug
    in earlier versions, where "KeyPass" and "Shot" were offered as
    filterable action types but could never match anything, since neither
    is a literal WhoScored type value.
    """
    names = set()
    for q in event.get("qualifiers") or []:
        if not isinstance(q, dict):
            continue
        qtype = q.get("type", {})
        name = qtype.get("displayName", "") if isinstance(qtype, dict) else str(qtype)
        if name:
            names.add(name)
    return names


def parse_events(match_data: dict) -> tuple[list[dict], str, str, str]:
    """
    Flattens matchCentreData into plain event rows. Mirrors the WS Marker
    Tool's field set (minute, second, period, playerName, team, type,
    outcome) plus derived category flags (is_shot, is_key_pass, is_cross,
    is_corner, is_through_ball, is_header, is_freekick, is_long_ball)
    pulled from each
    event's qualifiers, since several common "action types" people
    actually want to filter by are not real WhoScored 'type' values on
    their own.
    """
    events_raw = match_data.get("events") or match_data.get("matchEvents") or []
    player_dict = match_data.get("playerIdNameDictionary", {})
    home_team = "Home"
    away_team = "Away"
    player_team = {}

    home_data = match_data.get("home") or match_data.get("homeTeam") or {}
    away_data = match_data.get("away") or match_data.get("awayTeam") or {}
    if isinstance(home_data, dict):
        home_team = home_data.get("name") or home_data.get("teamName") or "Home"
        for p in home_data.get("players", []):
            pid = str(p.get("playerId") or p.get("id") or "")
            if pid:
                player_team[pid] = home_team
    if isinstance(away_data, dict):
        away_team = away_data.get("name") or away_data.get("teamName") or "Away"
        for p in away_data.get("players", []):
            pid = str(p.get("playerId") or p.get("id") or "")
            if pid:
                player_team[pid] = away_team

    rows = []
    for ev in events_raw:
        try:
            pid = str(ev.get("playerId", ""))
            player = player_dict.get(pid, "") or ev.get("playerName", "") or ""
            minute = int(ev.get("minute") or ev.get("expandedMinute") or 0)
            second = int(ev.get("second") or 0)
            period = _parse_period(ev.get("period", 1))
            if period not in VALID_PERIODS:
                continue
            etype = ev.get("type", {})
            otype = ev.get("outcomeType", {})
            type_name = etype.get("displayName", "Unknown") if isinstance(etype, dict) else str(etype)
            outcome = otype.get("displayName", "") if isinstance(otype, dict) else str(otype)
            display = "Foul Drawn" if type_name == "Foul" and outcome == "Successful" else type_name
            display = display.replace("MissedShots", "MissedShot")

            qnames = _qualifiers(ev)

            rows.append(
                {
                    "minute": minute,
                    "second": second,
                    "period": period,
                    "playerName": player,
                    "team": player_team.get(pid, ""),
                    "type": display,
                    "outcome": outcome,
                    "is_shot": display in SHOT_TYPES,
                    "is_key_pass": "KeyPass" in qnames,
                    "is_cross": "Cross" in qnames,
                    "is_corner": "CornerTaken" in qnames,
                    "is_through_ball": "Throughball" in qnames,
                    "is_header": "Head" in qnames,
                    "is_freekick": "FreekickTaken" in qnames,
                    "is_long_ball": "Longball" in qnames,
                }
            )
        except Exception:
            continue

    period_order = list(VALID_PERIODS)
    rows.sort(
        key=lambda r: (
            period_order.index(r["period"]) if r["period"] in period_order else 99,
            r["minute"],
            r["second"],
        )
    )
    return rows, f"{home_team} vs {away_team}", home_team, away_team


# Waits (seconds) before each retry after a 403 / connection error. Each retry
# also throws the session away and warms up a fresh one.
RETRY_WAITS = (8, 20)


class WSScraper:
    """
    One WhoScored session reused across a whole batch (a single homepage
    warm-up rather than one per match), with retry-on-block.

    Counters used by the app:
      fetches  — matches actually downloaded (cache hits don't count)
      calls    — network attempts made, including failed ones
      bytes    — uncompressed size of everything downloaded, so an upper
                 bound on real proxy usage (transfer is compressed)
    """

    HOME = "https://www.whoscored.com"

    def __init__(self, proxy_url: str | None = None, timeout_s: float = 30.0):
        self.proxy_url = proxy_url
        self.timeout_s = timeout_s
        self.session = None
        self.using_cffi = True
        self.fetches = 0
        self.calls = 0
        self.bytes = 0

    def _new_session(self):
        self.session, self.using_cffi = _make_session(self.proxy_url)
        try:
            r = self.session.get(self.HOME, timeout=15)
            self.bytes += len(r.content)
        except Exception:
            pass
        time.sleep(1.5)

    def fetch_match(self, match_id: int) -> dict:
        """Returns the matchCentreData dict. Raises ValueError on failure."""
        url = f"{self.HOME}/Matches/{int(match_id)}/Live"
        last_err = None
        for attempt in range(len(RETRY_WAITS) + 1):
            if self.session is None:
                self._new_session()
            self.calls += 1
            try:
                resp = self.session.get(url, timeout=self.timeout_s)
                self.bytes += len(resp.content)
                if resp.status_code == 200:
                    data = _find_match_data(resp.text)
                    if data is None:
                        # Not a block, so retrying won't help.
                        raise ValueError(
                            "Could not find event data in the match page — "
                            "match may not be supported."
                        )
                    self.fetches += 1
                    return data
                if resp.status_code == 403:
                    hint = (
                        ""
                        if self.using_cffi
                        else " Install curl_cffi (`pip install curl_cffi`) — plain requests is more likely to be blocked."
                    )
                    last_err = ValueError(f"WhoScored blocked the request (403).{hint}")
                else:
                    last_err = ValueError(f"HTTP {resp.status_code}")
            except ValueError:
                raise
            except Exception as e:
                last_err = ValueError(f"Connection error: {str(e)[:120]}")

            if attempt < len(RETRY_WAITS):
                time.sleep(RETRY_WAITS[attempt])
                self.session = None  # fresh session + warm-up on the next loop
        raise last_err


def scrape_match(match_id: int, nav_timeout_ms: int = 30000, scraper: "WSScraper | None" = None) -> dict:
    """
    Fetches a WhoScored match centre page by ID and returns the parsed
    matchCentreData dict. Raises on failure so callers can log/skip.
    Pass a shared `scraper` to reuse one session across many matches.
    """
    if scraper is None:
        scraper = WSScraper(timeout_s=nav_timeout_ms / 1000)
    return scraper.fetch_match(match_id)


def get_match_date(mcd: dict) -> str:
    """
    Extracts the match date (YYYY-MM-DD) from matchCentreData's startTime
    field, mirroring the WS Marker Tool's date parsing.
    """
    start_time = mcd.get("startTime", "")
    return start_time[:10] if start_time else ""


def events_to_rows(match_id: int, mcd: dict) -> list[dict]:
    """
    Wraps parse_events() and tags each row with match_id + date, for the
    batch collator's use.
    """
    events, match_name, home_team, away_team = parse_events(mcd)
    match_date = get_match_date(mcd)
    rows = []
    for e in events:
        rows.append(
            {
                "match_id": match_id,
                "date": match_date,
                "home_team": home_team,
                "away_team": away_team,
                "team": e["team"],
                "player": e["playerName"],
                "period": e["period"],
                "minute": e["minute"],
                "second": e["second"],
                "type": e["type"],
                "outcome": e["outcome"],
                "is_shot": e["is_shot"],
                "is_key_pass": e["is_key_pass"],
                "is_cross": e["is_cross"],
                "is_corner": e["is_corner"],
                "is_through_ball": e["is_through_ball"],
                "is_header": e["is_header"],
                "is_freekick": e["is_freekick"],
                "is_long_ball": e["is_long_ball"],
            }
        )
    return rows

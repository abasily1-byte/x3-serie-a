#!/usr/bin/env python3
"""Generate a monochrome-compatible Serie A dashboard BMP."""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont


WIDTH = 528
HEIGHT = 792
PACIFIC = ZoneInfo("America/Los_Angeles")
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/scoreboard"
FIXTURE_FEED = "https://fixturedownload.com/feed/json/serie-a-{year}"
HTTP_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "x3-serie-a/1.0 (+GitHub Actions)",
}


def http_get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers=HTTP_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not fetch Serie A data from {url}: {exc}") from exc


def parse_feed_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_season_feed(now: datetime) -> tuple[int, list[dict[str, Any]]]:
    likely_start = now.year if now.month >= 7 else now.year - 1
    errors: list[str] = []
    for season_year in (likely_start, likely_start - 1):
        url = FIXTURE_FEED.format(year=season_year)
        try:
            payload = http_get_json(url)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(payload, list) or not payload:
            errors.append(f"{url} returned no fixtures")
            continue
        if not all("RoundNumber" in item and "DateUtc" in item for item in payload):
            errors.append(f"{url} did not include round and kickoff data")
            continue
        return season_year, payload
    raise RuntimeError("No usable Serie A season feed was available: " + "; ".join(errors))


def select_current_round(fixtures: list[dict[str, Any]], now: datetime) -> tuple[int, list[dict[str, Any]]]:
    rounds: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for fixture in fixtures:
        rounds[int(fixture["RoundNumber"])].append(fixture)

    schedule = []
    for number, items in rounds.items():
        dates = [parse_feed_datetime(item["DateUtc"]) for item in items]
        schedule.append((number, min(dates), max(dates), items))
    schedule.sort(key=lambda entry: entry[0])

    # Keep a round current throughout its match window, with a small buffer for
    # late final-status updates. Between rounds, show the next scheduled round.
    active = [
        entry
        for entry in schedule
        if entry[1] - timedelta(hours=24) <= now <= entry[2] + timedelta(hours=18)
    ]
    if active:
        selected = min(active, key=lambda entry: abs((entry[1] - now).total_seconds()))
    else:
        future = [entry for entry in schedule if entry[1] > now]
        selected = min(future, key=lambda entry: entry[1]) if future else schedule[-1]

    number, _start, _end, items = selected
    items.sort(key=lambda item: parse_feed_datetime(item["DateUtc"]))
    return number, items


def normalize_team_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    compact = re.sub(r"[^a-z0-9]", "", ascii_name.lower())
    aliases = {
        "internazionale": "inter",
        "intermilan": "inter",
        "acmilan": "milan",
        "asroma": "roma",
    }
    return aliases.get(compact, compact)


def preferred_team_name(name: str) -> str:
    aliases = {
        "Internazionale": "Inter",
        "Inter Milan": "Inter",
        "AC Milan": "Milan",
        "AS Roma": "Roma",
    }
    return aliases.get(name, name)


def fetch_espn_round(round_fixtures: list[dict[str, Any]], season_year: int) -> dict[tuple[str, str], dict[str, Any]]:
    round_dates = [parse_feed_datetime(item["DateUtc"]) for item in round_fixtures]
    date_from = (min(round_dates) - timedelta(days=1)).strftime("%Y%m%d")
    date_to = (max(round_dates) + timedelta(days=1)).strftime("%Y%m%d")
    query = urllib.parse.urlencode({"dates": f"{date_from}-{date_to}", "limit": 100})
    payload = http_get_json(f"{ESPN_SCOREBOARD}?{query}")

    leagues = payload.get("leagues", [])
    if not leagues or "Serie A" not in leagues[0].get("name", ""):
        raise RuntimeError("ESPN response was not identified as Italian Serie A")

    result: dict[tuple[str, str], dict[str, Any]] = {}
    for event in payload.get("events", []):
        if int(event.get("season", {}).get("year", -1)) != season_year:
            continue
        competition = event.get("competitions", [{}])[0]
        competitors = competition.get("competitors", [])
        home = next((item for item in competitors if item.get("homeAway") == "home"), None)
        away = next((item for item in competitors if item.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        key = (
            normalize_team_name(home["team"]["displayName"]),
            normalize_team_name(away["team"]["displayName"]),
        )
        result[key] = parse_espn_event(event, competition, home, away)
    return result


def parse_espn_event(
    event: dict[str, Any],
    competition: dict[str, Any],
    home: dict[str, Any],
    away: dict[str, Any],
) -> dict[str, Any]:
    status = competition.get("status", {}).get("type") or event.get("status", {}).get("type", {})
    state = status.get("state", "pre")
    completed = bool(status.get("completed"))
    internal_status = "C" if completed else ("U" if state == "pre" else "L")

    def team_info(entry: dict[str, Any]) -> dict[str, str]:
        team = entry["team"]
        name = preferred_team_name(team.get("displayName", "Unknown"))
        short = preferred_team_name(team.get("shortDisplayName") or name)
        return {
            "name": name,
            "short": short,
            "abbr": team.get("abbreviation") or short,
        }

    kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
    return {
        "id": event["id"],
        "kickoff": kickoff.astimezone(PACIFIC),
        "status": internal_status,
        "home": team_info(home),
        "away": team_info(away),
        "home_score": int(home["score"]) if internal_status != "U" else None,
        "away_score": int(away["score"]) if internal_status != "U" else None,
    }


def fetch_dashboard_data(now: datetime) -> dict[str, Any]:
    season_year, season_fixtures = load_season_feed(now)
    round_number, round_fixtures = select_current_round(season_fixtures, now)
    espn_matches = fetch_espn_round(round_fixtures, season_year)

    matches = []
    missing = []
    for fixture in round_fixtures:
        key = (
            normalize_team_name(fixture["HomeTeam"]),
            normalize_team_name(fixture["AwayTeam"]),
        )
        match = espn_matches.get(key)
        if match is None:
            missing.append(f"{fixture['HomeTeam']}–{fixture['AwayTeam']}")
        else:
            matches.append(match)
    if missing:
        raise RuntimeError("ESPN was missing scheduled Serie A fixtures: " + ", ".join(missing))
    if len(matches) != len(round_fixtures):
        raise RuntimeError("Serie A round mapping was incomplete")

    matches.sort(key=lambda match: match["kickoff"])
    return {
        "season": f"{season_year}/{(season_year + 1) % 100:02d}",
        "matchweek": round_number,
        "matches": matches,
        "updated": now.astimezone(PACIFIC),
    }


def find_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        [
            "DejaVuSansCondensed-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
        if bold
        else [
            "DejaVuSansCondensed.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def center_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    draw.text(xy, text, font=font, fill=fill, anchor="mm")


def fitting_team_name(
    draw: ImageDraw.ImageDraw,
    team: dict[str, str],
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    for candidate in (team["name"], team["short"], team["abbr"]):
        if text_width(draw, candidate.upper(), font) <= max_width:
            return candidate.upper()
    return team["abbr"].upper()


def render_dashboard(data: dict[str, Any], output: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)

    title_font = find_font(43, bold=True)
    subtitle_font = find_font(17, bold=True)
    stat_font = find_font(19, bold=True)
    section_font = find_font(17, bold=True)
    team_font = find_font(18, bold=True)
    score_font = find_font(18, bold=True)
    meta_font = find_font(12, bold=False)
    footer_font = find_font(12, bold=True)

    draw.rectangle((6, 6, WIDTH - 7, HEIGHT - 7), outline="black", width=3)
    draw.rectangle((17, 17, WIDTH - 18, 94), fill="black")
    center_text(draw, (WIDTH // 2, 48), "SERIE A", title_font, "white")
    center_text(
        draw,
        (WIDTH // 2, 78),
        f"{data['season']}  •  PACIFIC TIME",
        subtitle_font,
        "white",
    )

    matches = data["matches"]
    completed = [match for match in matches if match["status"] == "C"]
    live = [match for match in matches if match["status"] not in {"C", "U"}]
    upcoming = [match for match in matches if match["status"] == "U"]
    draw.line((17, 105, WIDTH - 18, 105), fill="black", width=2)
    center_text(draw, (WIDTH // 4, 127), f"GIORNATA {data['matchweek']}", stat_font, "black")
    center_text(
        draw,
        (WIDTH * 3 // 4, 127),
        f"{len(completed)} OF {len(matches)} FINAL",
        stat_font,
        "black",
    )
    draw.line((17, 149, WIDTH - 18, 149), fill="black", width=2)

    groups = [("COMPLETED", completed), ("IN PLAY", live), ("UPCOMING", upcoming)]
    groups = [(label, items) for label, items in groups if items]
    top = 158
    footer_top = 744
    header_height = 27
    available_rows = footer_top - top - header_height * len(groups)
    row_height = max(42, min(54, available_rows // len(matches)))

    y = top
    for label, group_matches in groups:
        draw.rectangle((17, y, WIDTH - 18, y + header_height - 1), fill="black")
        draw.text((25, y + header_height // 2), label, font=section_font, fill="white", anchor="lm")
        draw.text(
            (WIDTH - 25, y + header_height // 2),
            str(len(group_matches)),
            font=section_font,
            fill="white",
            anchor="rm",
        )
        y += header_height
        for match in group_matches:
            draw_match_row(draw, match, y, row_height, team_font, score_font, meta_font)
            y += row_height

    updated = data["updated"].strftime("%a %b %-d  •  %-I:%M %p PT").upper()
    draw.line((17, footer_top, WIDTH - 18, footer_top), fill="black", width=2)
    center_text(draw, (WIDTH // 2, 760), f"UPDATED {updated}", footer_font, "black")
    center_text(draw, (WIDTH // 2, 777), "DATA: ESPN + FIXTUREDOWNLOAD", meta_font, "black")

    # Threshold antialiased TrueType text so every final pixel is pure B/W.
    image = image.convert("L").point(lambda pixel: 255 if pixel >= 128 else 0, mode="1").convert("RGB")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="BMP", compression="raw")


def draw_match_row(
    draw: ImageDraw.ImageDraw,
    match: dict[str, Any],
    y: int,
    height: int,
    team_font: ImageFont.ImageFont,
    score_font: ImageFont.ImageFont,
    meta_font: ImageFont.ImageFont,
) -> None:
    meta = match["kickoff"].strftime("%a %b %-d  •  %-I:%M %p PT").upper()
    center_text(draw, (WIDTH // 2, y + 11), meta, meta_font, "black")

    team_y = y + 31
    max_team_width = 181
    home = fitting_team_name(draw, match["home"], team_font, max_team_width)
    away = fitting_team_name(draw, match["away"], team_font, max_team_width)
    draw.text((216, team_y), home, font=team_font, fill="black", anchor="rm")
    draw.text((312, team_y), away, font=team_font, fill="black", anchor="lm")

    if match["status"] == "U":
        center = "v"
    else:
        status = "FT" if match["status"] == "C" else "LIVE"
        center = f"{match['home_score']}–{match['away_score']} {status}"
    center_text(draw, (WIDTH // 2, team_y), center, score_font, "black")
    draw.line((22, y + height - 1, WIDTH - 23, y + height - 1), fill="black", width=1)


def validate_bmp(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Missing output file: {path}")
    with path.open("rb") as bmp:
        header = bmp.read(54)
    if header[:2] != b"BM":
        raise RuntimeError("Output is not a BMP file")
    bits_per_pixel = struct.unpack_from("<H", header, 28)[0]
    compression = struct.unpack_from("<I", header, 30)[0]
    if bits_per_pixel != 24:
        raise RuntimeError(f"Expected 24-bit BMP, found {bits_per_pixel}-bit")
    if compression != 0:
        raise RuntimeError(f"Expected uncompressed BMP, compression={compression}")

    with Image.open(path) as image:
        image.load()
        if image.size != (WIDTH, HEIGHT):
            raise RuntimeError(f"Expected {WIDTH}x{HEIGHT}, found {image.size}")
        if image.mode != "RGB":
            raise RuntimeError(f"Expected RGB image, found mode {image.mode}")
        colors = set(image.getdata())
    allowed = {(0, 0, 0), (255, 255, 255)}
    if not colors or not colors.issubset(allowed):
        raise RuntimeError(f"Image contains non-black/white pixels: {colors - allowed}")
    print(
        f"Validated {path}: {WIDTH}x{HEIGHT}, RGB, 24-bit uncompressed BMP, "
        f"{len(colors)} pure B/W colors"
    )


def print_source_summary(data: dict[str, Any]) -> None:
    print(f"Serie A {data['season']} — Giornata {data['matchweek']}")
    for match in data["matches"]:
        when = match["kickoff"].strftime("%Y-%m-%d %H:%M %Z")
        if match["status"] == "C":
            detail = f"{match['home_score']}-{match['away_score']} FT"
        elif match["status"] == "U":
            detail = "upcoming"
        else:
            detail = f"{match['home_score']}-{match['away_score']} LIVE"
        print(f"  {when} | {match['home']['name']} {detail} {match['away']['name']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("SerieA.bmp"))
    parser.add_argument("--validate", action="store_true", help="validate the BMP after generation")
    parser.add_argument("--validate-only", action="store_true", help="validate an existing BMP")
    args = parser.parse_args()

    if args.validate_only:
        validate_bmp(args.output)
        return 0

    data = fetch_dashboard_data(datetime.now(timezone.utc))
    print_source_summary(data)
    render_dashboard(data, args.output)
    print(f"Wrote {args.output}")
    if args.validate:
        validate_bmp(args.output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

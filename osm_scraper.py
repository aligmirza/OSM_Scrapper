#!/usr/bin/env python3
"""Find OpenStreetMap places from city/country/query rows."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import ssl
import sys
import time
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_INPUT = "osm_input_sample.csv"
DEFAULT_USER_AGENT = "BtB OSM scraper"
SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_QUERY_TAGS_PATH = SCRIPT_DIR / "osm_common_query_tags.json"
QUERY_ALIASES_PATH = SCRIPT_DIR / "osm_query_aliases.json"
OUTPUT_COLUMNS = [
    "input_city",
    "input_country",
    "input_query",
    "osm_type",
    "osm_id",
    "name",
    "category",
    "category_value",
    "lat",
    "lon",
    "street",
    "city",
    "state",
    "postcode",
    "country",
    "phone",
    "website",
    "email",
    "opening_hours",
    "osm_url",
]


def load_common_query_tags(path: Path = COMMON_QUERY_TAGS_PATH) -> tuple[str, ...]:
    with path.open("r", encoding="utf-8") as handle:
        tags = json.load(handle)

    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError(f"{path} must contain a JSON list of tag names")

    return tuple(tags)


def load_query_aliases(path: Path = QUERY_ALIASES_PATH) -> dict[str, tuple[dict[str, str], ...]]:
    with path.open("r", encoding="utf-8") as handle:
        aliases = json.load(handle)

    if not isinstance(aliases, dict):
        raise ValueError(f"{path} must contain a JSON object")

    loaded_aliases = {}
    for alias, filters in aliases.items():
        if not isinstance(alias, str) or not isinstance(filters, list):
            raise ValueError(f"{path} aliases must map strings to filter lists")

        loaded_filters = []
        for filter_spec in filters:
            if not isinstance(filter_spec, dict) or not isinstance(filter_spec.get("key"), str):
                raise ValueError(f"{path} alias {alias!r} has an invalid filter")

            key = filter_spec["key"].strip()
            value = filter_spec.get("value")
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{path} alias {alias!r} value must be a string")

            loaded_filter = {"key": key}
            if value:
                loaded_filter["value"] = value.strip()
            loaded_filters.append(loaded_filter)

        loaded_aliases[alias.lower().strip()] = tuple(loaded_filters)

    return loaded_aliases


COMMON_QUERY_TAGS = load_common_query_tags()
QUERY_ALIASES = load_query_aliases()


def ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    if not verify_ssl:
        return ssl._create_unverified_context()

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()

    return ssl.create_default_context(cafile=certifi.where())


def should_retry_without_ssl_verification(error: URLError) -> bool:
    return isinstance(error.reason, ssl.SSLCertVerificationError)


def http_get_json(
    url: str,
    params: dict[str, str],
    user_agent: str,
    verify_ssl: bool,
) -> object:
    request_url = f"{url}?{urlencode(params)}"
    request = Request(request_url, headers={"User-Agent": user_agent})

    try:
        with urlopen(request, timeout=60, context=ssl_context(verify_ssl)) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code} from {url}: {error.reason}") from error
    except URLError as error:
        if verify_ssl and should_retry_without_ssl_verification(error):
            print(
                "SSL certificate verification failed. Retrying without verification; "
                "fix your local Python certificates for a safer permanent setup."
            )
            return http_get_json(url, params, user_agent, verify_ssl=False)
        raise RuntimeError(f"Could not reach {url}: {error.reason}") from error


def http_post_json(url: str, data: str, user_agent: str, verify_ssl: bool) -> object:
    request = Request(
        url,
        data=data.encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": user_agent,
        },
    )

    try:
        with urlopen(request, timeout=120, context=ssl_context(verify_ssl)) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code} from {url}: {error.reason}") from error
    except URLError as error:
        if verify_ssl and should_retry_without_ssl_verification(error):
            print(
                "SSL certificate verification failed. Retrying without verification; "
                "fix your local Python certificates for a safer permanent setup."
            )
            return http_post_json(url, data, user_agent, verify_ssl=False)
        raise RuntimeError(f"Could not reach {url}: {error.reason}") from error


def geocode_city(
    city: str,
    country: str,
    user_agent: str,
    verify_ssl: bool,
) -> tuple[float, float]:
    results = http_get_json(
        NOMINATIM_URL,
        {
            "q": f"{city}, {country}",
            "format": "jsonv2",
            "limit": "1",
        },
        user_agent,
        verify_ssl,
    )

    if not isinstance(results, list) or not results:
        raise ValueError(f"No coordinates found for {city}, {country}")

    return float(results[0]["lat"]), float(results[0]["lon"])


def overpass_filter_fragment(key: str, value: str | None = None) -> str:
    if value:
        return f'nwr["{key}"="{value}"](around:{{radius}},{{lat}},{{lon}});'
    return f'nwr["{key}"](around:{{radius}},{{lat}},{{lon}});'


def overpass_filter_fragments(query: str) -> list[str]:
    clean_query = query.strip()
    lowered_query = clean_query.lower()

    if "=" in clean_query:
        key, value = (part.strip() for part in clean_query.split("=", 1))
        return [f'nwr["{key}"="{value}"](around:{{radius}},{{lat}},{{lon}});']

    if lowered_query in QUERY_ALIASES:
        return [
            overpass_filter_fragment(filter_spec["key"], filter_spec.get("value"))
            for filter_spec in QUERY_ALIASES[lowered_query]
        ]

    fragments = []
    for tag in COMMON_QUERY_TAGS:
        fragments.append(f'nwr["{tag}"~"{clean_query}",i](around:{{radius}},{{lat}},{{lon}});')
    fragments.append(f'nwr["name"~"{clean_query}",i](around:{{radius}},{{lat}},{{lon}});')
    return fragments


def build_overpass_query(lat: float, lon: float, radius: int, query: str, limit: int) -> str:
    fragments = "\n".join(
        fragment.format(radius=radius, lat=lat, lon=lon)
        for fragment in overpass_filter_fragments(query)
    )
    return f"""
[out:json][timeout:60];
(
{fragments}
);
out center tags {limit};
"""


def fetch_places(
    city: str,
    country: str,
    query: str,
    radius: int,
    limit: int,
    user_agent: str,
    verify_ssl: bool,
) -> list[dict[str, str]]:
    lat, lon = geocode_city(city, country, user_agent, verify_ssl)
    time.sleep(1)

    overpass_query = build_overpass_query(lat, lon, radius, query, limit)
    result = http_post_json(
        OVERPASS_URL,
        urlencode({"data": overpass_query}),
        user_agent,
        verify_ssl,
    )
    if not isinstance(result, dict):
        return []

    rows = []
    for element in result.get("elements", []):
        if not isinstance(element, dict):
            continue
        rows.append(place_to_row(city, country, query, element))

    return rows


def first_tag(tags: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = tags.get(key, "").strip()
        if value:
            return value
    return ""


def category(tags: dict[str, str]) -> tuple[str, str]:
    for key in COMMON_QUERY_TAGS:
        value = tags.get(key, "").strip()
        if value:
            return key, value
    return "", ""


def element_coordinates(element: dict[str, object]) -> tuple[str, str]:
    lat = element.get("lat")
    lon = element.get("lon")

    center = element.get("center")
    if isinstance(center, dict):
        lat = center.get("lat", lat)
        lon = center.get("lon", lon)

    return str(lat or ""), str(lon or "")


def place_to_row(
    input_city: str,
    input_country: str,
    input_query: str,
    element: dict[str, object],
) -> dict[str, str]:
    tags = element.get("tags", {})
    if not isinstance(tags, dict):
        tags = {}

    tags = {str(key): str(value) for key, value in tags.items()}
    lat, lon = element_coordinates(element)
    category_key, category_value = category(tags)
    osm_type = str(element.get("type", ""))
    osm_id = str(element.get("id", ""))

    return {
        "input_city": input_city,
        "input_country": input_country,
        "input_query": input_query,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "name": tags.get("name", ""),
        "category": category_key,
        "category_value": category_value,
        "lat": lat,
        "lon": lon,
        "street": first_tag(tags, ("addr:street", "addr:full")),
        "city": first_tag(tags, ("addr:city", "addr:town", "addr:village")),
        "state": tags.get("addr:state", ""),
        "postcode": tags.get("addr:postcode", ""),
        "country": tags.get("addr:country", ""),
        "phone": first_tag(tags, ("phone", "contact:phone")),
        "website": first_tag(tags, ("website", "contact:website")),
        "email": first_tag(tags, ("email", "contact:email")),
        "opening_hours": tags.get("opening_hours", ""),
        "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_type and osm_id else "",
    }


def input_value(row: dict[str, str], names: tuple[str, ...]) -> str:
    lowered = {key.lower().strip(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name, "").strip()
        if value:
            return value
    return ""


def read_input(path: Path) -> list[tuple[str, str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for line_number, row in enumerate(reader, start=2):
            city = input_value(row, ("city", "cities"))
            country = input_value(row, ("country",))
            query = input_value(row, ("query", "search"))

            if not city or not country or not query:
                raise ValueError(
                    f"{path}:{line_number} needs city/cities, country, and query values"
                )

            rows.append((city, country, query))

    return rows


def open_output_writer(path: Path) -> tuple[TextIO, csv.DictWriter]:
    handle = path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
    writer.writeheader()
    flush_output(handle)
    return handle, writer


def flush_output(handle: TextIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def write_output_rows(
    writer: csv.DictWriter,
    handle: TextIO,
    rows: list[dict[str, str]],
) -> None:
    writer.writerows(rows)
    flush_output(handle)


def filename_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"


def unique_values(values: list[str]) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        clean_value = value.strip()
        lowered_value = clean_value.lower()
        if clean_value and lowered_value not in seen:
            seen.add(lowered_value)
            unique.append(clean_value)
    return unique


def summarize_filename_values(values: list[str], max_values: int = 2) -> str:
    unique = unique_values(values)
    if len(unique) <= max_values:
        return "_".join(filename_slug(value) for value in unique)

    shown_values = "_".join(filename_slug(value) for value in unique[:max_values])
    return f"{shown_values}_plus_{len(unique) - max_values}_more"


def meaningful_output_path(input_path: Path, input_rows: list[tuple[str, str, str]]) -> Path:
    queries = [query for _, _, query in input_rows]
    cities = [city for city, _, _ in input_rows]
    countries = [country for _, country, _ in input_rows]
    locations = [f"{city}, {country}" for city, country, _ in input_rows]

    parts = ["osm", summarize_filename_values(queries)]
    unique_locations = unique_values(locations)

    if len(unique_locations) == 1:
        parts.append(filename_slug(cities[0]))
        parts.append(filename_slug(countries[0]))
    elif len(unique_values(countries)) == 1:
        parts.append(filename_slug(countries[0]))
        parts.append(f"{len(unique_values(cities))}_cities")
    else:
        parts.append(f"{len(unique_locations)}_locations")

    return input_path.with_name(f"{'_'.join(parts)}.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search OpenStreetMap places by city, country, and query."
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=Path(DEFAULT_INPUT),
        help=f"Input CSV with city/cities, country, query. Defaults to {DEFAULT_INPUT}.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output CSV path. Defaults to a name based on the scraped query and location.",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=20_000,
        help="Search radius in meters around the city center. Defaults to 20000.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum OSM elements returned per input row. Defaults to 500.",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="User agent for OSM services, ideally including your app name or email.",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_false",
        dest="verify_ssl",
        help="Skip SSL certificate verification if your local Python cert store is broken.",
    )
    parser.set_defaults(verify_ssl=True)
    args = parser.parse_args()

    if len(sys.argv) == 1:
        print(
            f"No arguments provided; using {DEFAULT_INPUT}, a generated output filename, "
            f"and user-agent {DEFAULT_USER_AGENT!r}."
        )

    return args


def main() -> None:
    args = parse_args()
    input_rows = read_input(args.input)
    output_path = args.output or meaningful_output_path(args.input, input_rows)
    total_rows = 0

    output_handle, output_writer = open_output_writer(output_path)
    try:
        for city, country, query in input_rows:
            print(f"Searching {query!r} in {city}, {country}...")
            rows = fetch_places(
                city=city,
                country=country,
                query=query,
                radius=args.radius,
                limit=args.limit,
                user_agent=args.user_agent,
                verify_ssl=args.verify_ssl,
            )
            write_output_rows(output_writer, output_handle, rows)
            total_rows += len(rows)
            print(f"  found and saved {len(rows)} result(s)")
            time.sleep(1)
    finally:
        output_handle.close()

    print(f"Wrote {total_rows} result(s) to {output_path}")


if __name__ == "__main__":
    main()

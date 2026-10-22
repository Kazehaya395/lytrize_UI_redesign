#!/usr/bin/env python3
"""Normalize Natural Earth GeoJSON into Lytrize's compact offline map assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


def norm(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def rounded_coordinates(value):
    if isinstance(value, (int, float)):
        return round(value, 4)
    if isinstance(value, list):
        return [rounded_coordinates(item) for item in value]
    return value


def geometry_center(geometry):
    """Return a stable coarse [longitude, latitude] center for point maps."""
    points = []
    def visit(value):
        if (isinstance(value, list) and len(value) >= 2 and
                isinstance(value[0], (int, float)) and
                isinstance(value[1], (int, float))):
            points.append((float(value[0]), float(value[1])))
        elif isinstance(value, list):
            for child in value:
                visit(child)
    if geometry:
        visit(geometry.get("coordinates"))
    if not points:
        return None
    # Circular longitude averaging keeps dateline-spanning shapes near the
    # date line instead of incorrectly centering them around Greenwich.
    import math
    sin_lon = sum(math.sin(math.radians(lon)) for lon, _ in points)
    cos_lon = sum(math.cos(math.radians(lon)) for lon, _ in points)
    lon = math.degrees(math.atan2(sin_lon, cos_lon))
    lat = sum(lat for _, lat in points) / len(points)
    return [round(lon, 4), round(lat, 4)]


def read_geojson(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("type") != "FeatureCollection" or not data.get("features"):
        raise ValueError(f"{path} is not a non-empty GeoJSON FeatureCollection")
    return data


def write_json(path: Path, value) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--countries", required=True, type=Path)
    parser.add_argument("--admin1", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    countries_src = read_geojson(args.countries)
    admin1_src = read_geojson(args.admin1)
    country_features = []
    country_aliases, country_names, country_centers = {}, {}, {}
    for feature in countries_src["features"]:
        props = feature.get("properties") or {}
        map_key = (props.get("ISO_A3_EH") if props.get("ISO_A3_EH") not in (None, "-99")
                   else props.get("ADM0_A3"))
        display_name = props.get("NAME_EN") or props.get("ADMIN") or props.get("NAME")
        if not map_key or not display_name:
            continue
        names = [display_name, props.get("ADMIN"), props.get("NAME"),
                 props.get("NAME_LONG"), props.get("NAME_ALT"),
                 props.get("ABBREV"), props.get("ISO_A2"), props.get("ISO_A2_EH"),
                 props.get("ISO_A3"), props.get("ISO_A3_EH"), props.get("ADM0_A3"),
                 props.get("ADM0_ISO"), props.get("SOV_A3")]
        for alias in names:
            if alias and str(alias) not in ("-99", "-1"):
                country_aliases.setdefault(norm(alias), map_key)
        country_names[map_key] = display_name
        center = geometry_center(feature.get("geometry"))
        if center:
            country_centers[map_key] = center
        out_props = {"name": map_key, "display_name": display_name,
                     "iso_a3": map_key, "iso_a2": props.get("ISO_A2_EH") or props.get("ISO_A2")}
        country_features.append({"type": "Feature", "id": map_key,
                                 "properties": out_props,
                                 "geometry": rounded_coordinates(feature.get("geometry"))})

    # Preserve the aliases supported by the previous country resolver.
    for alias, code in {
        "usa": "USA", "us": "USA", "united states": "USA", "america": "USA",
        "uk": "GBR", "great britain": "GBR", "england": "GBR",
        "south korea": "KOR", "north korea": "PRK", "russia": "RUS",
        "iran": "IRN", "syria": "SYR", "taiwan": "TWN", "hong kong": "HKG",
        "macau": "MAC", "bolivia": "BOL", "venezuela": "VEN",
        "tanzania": "TZA", "vietnam": "VNM", "laos": "LAO",
        "czechia": "CZE", "czech republic": "CZE", "cape verde": "CPV",
        "ivory coast": "CIV", "cote d ivoire": "CIV", "congo": "COG",
        "dr congo": "COD", "democratic republic of the congo": "COD",
        "south africa": "ZAF", "new zealand": "NZL",
    }.items():
        if code in country_names:
            country_aliases[norm(alias)] = code

    state_aliases, state_names, state_centers = {}, {}, {}
    admin1_aliases, admin1_names, admin1_centers = {}, {}, {}
    bare_admin1_aliases = {}
    admin1_features = []
    for feature in admin1_src["features"]:
        props = feature.get("properties") or {}
        map_key = props.get("iso_3166_2") or props.get("adm1_code")
        display_name = props.get("name_en") or props.get("name")
        if not map_key or not display_name:
            continue
        country_name = props.get("admin") or ""
        out_props = {"name": map_key, "display_name": display_name,
                     "country_name": country_name,
                     "country_iso_a3": props.get("adm0_a3"),
                     "iso_3166_2": props.get("iso_3166_2"),
                     "postal": props.get("postal")}
        admin1_features.append({"type": "Feature", "id": map_key,
                                "properties": out_props,
                                "geometry": rounded_coordinates(feature.get("geometry"))})
        admin1_names[map_key] = display_name
        center = geometry_center(feature.get("geometry"))
        if center:
            admin1_centers[map_key] = center
        aliases = [display_name, props.get("name"), props.get("postal"),
                   props.get("iso_3166_2"), props.get("adm1_code")]
        for alias in aliases:
            if alias:
                normalized = norm(alias)
                bare_admin1_aliases.setdefault(normalized, set()).add(map_key)
                qualified = norm(f"{country_name} {alias}")
                if qualified:
                    admin1_aliases.setdefault(qualified, map_key)
                reversed_qualified = norm(f"{alias} {country_name}")
                if reversed_qualified:
                    admin1_aliases.setdefault(reversed_qualified, map_key)
        if props.get("iso_3166_2"):
            admin1_aliases.setdefault(norm(props["iso_3166_2"]), map_key)
        if props.get("adm0_a3") == "USA":
            state_names[map_key] = display_name
            if center:
                state_centers[map_key] = center
            for alias in aliases:
                if alias:
                    state_aliases.setdefault(norm(alias), map_key)

    # Bare subdivision names are useful when unambiguous. Colliding names are
    # available through country-qualified aliases and subdivision codes.
    for alias, keys in bare_admin1_aliases.items():
        if len(keys) == 1:
            admin1_aliases.setdefault(alias, next(iter(keys)))

    if "US-DC" in state_names:
        state_aliases[norm("District of Columbia")] = "US-DC"
        state_aliases[norm("Washington DC")] = "US-DC"

    if len({f["properties"]["name"] for f in country_features}) != len(country_features):
        raise ValueError("Country map keys are not unique")
    if len({f["properties"]["name"] for f in admin1_features}) != len(admin1_features):
        raise ValueError("Admin-1 map keys are not unique")
    if len(country_features) < 150:
        raise ValueError("Country source unexpectedly has fewer than 150 features")
    if len(admin1_features) < 250:
        raise ValueError("Admin-1 source unexpectedly has fewer than 250 features")

    countries = {"type": "FeatureCollection", "features": country_features}
    admin1 = {"type": "FeatureCollection", "features": admin1_features}
    countries_hash = write_json(args.output / "countries-110m.geojson", countries)
    admin1_hash = write_json(args.output / "admin1-50m.geojson", admin1)
    lookup_hash = write_json(args.output / "lookup.json", {
        "version": args.version, "country_aliases": country_aliases,
        "country_names": country_names, "us_state_aliases": state_aliases,
        "us_state_names": state_names, "country_centers": country_centers,
        "us_state_centers": state_centers, "admin1_aliases": admin1_aliases,
        "admin1_names": admin1_names, "admin1_centers": admin1_centers,
    })
    metadata = {
        "dataset": "Natural Earth Vector",
        "version": args.version,
        "refreshed_utc_date": datetime.now(timezone.utc).date().isoformat(),
        "license": "Public domain",
        "license_url": "https://www.naturalearthdata.com/about/terms-of-use/",
        "source_repository": "https://github.com/nvkelso/natural-earth-vector",
        "source_files": {
            "countries": "geojson/ne_110m_admin_0_countries.geojson",
            "admin1": "geojson/ne_50m_admin_1_states_provinces.geojson",
        },
        "map_assets": {
            "countries-110m.geojson": {"features": len(country_features),
                                        "sha256": countries_hash},
            "admin1-50m.geojson": {"features": len(admin1_features),
                                    "sha256": admin1_hash},
            "lookup.json": {"sha256": lookup_hash},
        },
        "coverage_note": (
            "Countries use Natural Earth 1:110m generalized boundaries. Admin-1 "
            "uses 1:50m generalized boundaries and contains only the subdivisions "
            "published by Natural Earth (294 features in the pinned source). "
            "Admin-1 resolution supports country-qualified names/codes and unique "
            "bare names; US states also have postal aliases. "
            "These are cartographic boundaries, not legal or survey boundaries."
        ),
        "attribution": "Made with Natural Earth (public domain).",
    }
    write_json(args.output / "metadata.json", metadata)
    print(f"Built Natural Earth {args.version}: {len(country_features)} countries, "
          f"{len(admin1_features)} admin-1 features")


if __name__ == "__main__":
    main()

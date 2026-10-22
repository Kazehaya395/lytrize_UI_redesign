#!/usr/bin/env bash
# Refresh bundled Natural Earth GeoJSON from a versioned upstream release.
# Usage: scripts/update_map_boundaries.sh [Natural-Earth-tag]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="${1:-latest}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ "$REF" == "latest" ]]; then
    curl -fL --retry 2 --silent --show-error \
        -H 'Accept: application/vnd.github+json' \
        -o "$TMP/release.json" \
        https://api.github.com/repos/nvkelso/natural-earth-vector/releases/latest
    REF="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["tag_name"])' "$TMP/release.json")"
fi

BASE="https://raw.githubusercontent.com/nvkelso/natural-earth-vector/${REF}/geojson"
curl -fL --retry 2 --silent --show-error \
    -o "$TMP/ne_110m_admin_0_countries.geojson" \
    "$BASE/ne_110m_admin_0_countries.geojson"
curl -fL --retry 2 --silent --show-error \
    -o "$TMP/ne_50m_admin_1_states_provinces.geojson" \
    "$BASE/ne_50m_admin_1_states_provinces.geojson"

python3 "$ROOT/scripts/build_map_boundaries.py" \
    --countries "$TMP/ne_110m_admin_0_countries.geojson" \
    --admin1 "$TMP/ne_50m_admin_1_states_provinces.geojson" \
    --output "$ROOT/backend/assets/echarts/maps/natural-earth" \
    --version "$REF"

#!/usr/bin/env bash
# Refresh bundled Natural Earth GeoJSON from a versioned upstream release.
# Usage: scripts/update_map_boundaries.sh [Natural-Earth-tag]
#
# Ships the high-quality boundary set:
#   countries : Natural Earth 1:50m cultural vectors (~3 MB)
#   admin-1   : Natural Earth 1:10m subdivisions, Douglas-Peucker simplified
#               to ~0.005 degrees (~550 m) when python3-shapely is available,
#               otherwise the full-resolution 1:10m source (~40 MB)
# Both remain fully offline at runtime; this script is the ONLY step that
# needs internet access. Re-run it whenever new Natural Earth data is wanted.
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
    -o "$TMP/ne_50m_admin_0_countries.geojson" \
    "$BASE/ne_50m_admin_0_countries.geojson"
curl -fL --retry 2 --silent --show-error \
    -o "$TMP/ne_10m_admin_1_states_provinces.geojson" \
    "$BASE/ne_10m_admin_1_states_provinces.geojson"

SIMPLIFY=()
if python3 -c "import shapely" 2>/dev/null; then
    SIMPLIFY=(--admin1-simplify 0.005)
else
    echo "note: python3 shapely not found; shipping full-resolution 1:10m admin-1 (~40 MB)" >&2
    echo "      install it with: python3 -m pip install shapely" >&2
fi

python3 "$ROOT/scripts/build_map_boundaries.py" \
    --countries "$TMP/ne_50m_admin_0_countries.geojson" \
    --admin1 "$TMP/ne_10m_admin_1_states_provinces.geojson" \
    --output "$ROOT/backend/assets/echarts/maps/natural-earth" \
    --version "$REF" "${SIMPLIFY[@]}"

echo "Map boundary bundle refreshed to Natural Earth ${REF}."
echo "Rebuild the application packages (.deb/.rpm) after refreshing if you ship installers."

#!/usr/bin/env bash
# Preview or remove generated Lytrize development artifacts.
# Usage: bash scripts/clean_generated.sh [--dry-run | --apply]
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
TMP_BASE="$(realpath -m -- "${TMPDIR:-/tmp}")"
RUNTIME_TMP="$TMP_BASE/lytrize_report_canvas"
MODE="dry-run"

usage() {
    cat <<'EOF'
Lytrize generated-artifact cleanup

Usage:
  bash scripts/clean_generated.sh [--dry-run | --apply]

Default behavior is a dry run. --apply lists the same candidates and asks you
to type CLEAN before removing them. Close Lytrize first to avoid removing a
temporary report that an open window is using.

Targets: project Python/test/tool caches, editor temp/backup files, root build
outputs, Python egg metadata, coverage output, byte-identical Python files with
the project's known "(copy...)" naming pattern, and Lytrize's exact report-cache
directory under TMPDIR (normally /tmp/lytrize_report_canvas).

Never targets: datasets, saved sessions, backups, source files, the old-codebase
archive, .git, virtual environments, node_modules, or ~/.local/share/lytrize.
EOF
}

case "${1:-}" in
    ""|--dry-run) MODE="dry-run" ;;
    --apply) MODE="apply" ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

declare -a CANDIDATES=()
declare -A SEEN=()

add_candidate() {
    local path="$1"
    [[ -e "$path" || -L "$path" ]] || return 0
    [[ -n "${SEEN["$path"]+x}" ]] && return 0
    SEEN["$path"]=1
    CANDIDATES+=("$path")
}

# Prune historical/source snapshots and dependency trees before looking for
# generated cache directories. find does not follow symlinks by default.
while IFS= read -r -d '' path; do
    add_candidate "$path"
done < <(find "$PROJECT_ROOT" \
    \( -path "$PROJECT_ROOT/.git" \
       -o -path "$PROJECT_ROOT/Old codebase (Do not delete)" \
       -o -path "$PROJECT_ROOT/venv" \
       -o -path "$PROJECT_ROOT/.venv" \
       -o -path "$PROJECT_ROOT/node_modules" \) -prune -o \
    -type d \( -name '__pycache__' -o -name '.pytest_cache' \
       -o -name '.mypy_cache' -o -name '.ruff_cache' -o -name '.cache' \
       -o -name '.tox' -o -name '.nox' \) -print0)

# Bytecode and conventional editor scratch/backup files. These are shown in
# the preview and still require the explicit interactive confirmation to go.
while IFS= read -r -d '' path; do
    add_candidate "$path"
done < <(find "$PROJECT_ROOT" \
    \( -path "$PROJECT_ROOT/.git" \
       -o -path "$PROJECT_ROOT/Old codebase (Do not delete)" \
       -o -path "$PROJECT_ROOT/venv" \
       -o -path "$PROJECT_ROOT/.venv" \
       -o -path "$PROJECT_ROOT/node_modules" \) -prune -o \
    -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.tmp' \
       -o -name '*.temp' -o -name '*.swp' -o -name '*.swo' \
       -o -name '*~' -o -name '*.orig' -o -name '*.rej' \
       -o -name '.DS_Store' -o -name 'Thumbs.db' \) -print0)

# The Linux packagers already treat these exact copied-file names as
# accidental duplicates. Remove one only when its expected original exists
# beside it and cmp confirms the file contents are identical.
while IFS= read -r -d '' path; do
    file_name="${path##*/}"
    case "$file_name" in
        *" (copy"*".py")
            original_name="${file_name%% (copy*}.py"
            ;;
        *" (Copy"*".py")
            original_name="${file_name%% (Copy*}.py"
            ;;
        *) continue ;;
    esac
    original_path="${path%/*}/$original_name"
    if [[ -f "$original_path" ]] && cmp -s -- "$path" "$original_path"; then
        add_candidate "$path"
    fi
done < <(find "$PROJECT_ROOT" \
    \( -path "$PROJECT_ROOT/.git" \
       -o -path "$PROJECT_ROOT/Old codebase (Do not delete)" \
       -o -path "$PROJECT_ROOT/venv" \
       -o -path "$PROJECT_ROOT/.venv" \
       -o -path "$PROJECT_ROOT/node_modules" \) -prune -o \
    -type f \( -name '* (copy*).py' -o -name '* (Copy*).py' \) -print0)

# These build/report outputs have fixed generated locations. Keep packaging
# source directories and user-provided package files outside these paths.
add_candidate "$PROJECT_ROOT/build"
add_candidate "$PROJECT_ROOT/dist"
add_candidate "$PROJECT_ROOT/htmlcov"
add_candidate "$PROJECT_ROOT/.coverage"
add_candidate "$PROJECT_ROOT/coverage.xml"

while IFS= read -r -d '' path; do
    add_candidate "$path"
done < <(find "$PROJECT_ROOT" \
    \( -path "$PROJECT_ROOT/.git" \
       -o -path "$PROJECT_ROOT/Old codebase (Do not delete)" \
       -o -path "$PROJECT_ROOT/venv" \
       -o -path "$PROJECT_ROOT/.venv" \
       -o -path "$PROJECT_ROOT/node_modules" \) -prune -o \
    -type d -name '*.egg-info' -print0)

# This exact directory is created by desktop/widgets/report_canvas.py for
# generated HTML and the local renderer bundle. Never target the whole temp dir.
if [[ "$RUNTIME_TMP" == "$TMP_BASE/lytrize_report_canvas" \
      && "$RUNTIME_TMP" != "/" && "$RUNTIME_TMP" != "$PROJECT_ROOT" ]]; then
    add_candidate "$RUNTIME_TMP"
fi

if ((${#CANDIDATES[@]} == 0)); then
    echo "No generated cleanup candidates found."
    exit 0
fi

echo "Mode: $MODE"
echo "Project: $PROJECT_ROOT"
echo "Candidates (${#CANDIDATES[@]}):"
for path in "${CANDIDATES[@]}"; do
    printf '  %q\n' "$path"
done

if [[ "$MODE" == "dry-run" ]]; then
    echo "Nothing removed. Review the list, then run with --apply to clean it."
    exit 0
fi

echo
read -r -p 'Type CLEAN to remove these generated files/directories: ' answer
if [[ "$answer" != "CLEAN" ]]; then
    echo "Cancelled; nothing removed."
    exit 0
fi

for path in "${CANDIDATES[@]}"; do
    # Every path was discovered below PROJECT_ROOT or is the one exact Lytrize
    # temp directory. Refuse unexpected values before invoking rm.
    case "$path" in
        "$PROJECT_ROOT"/*|"$RUNTIME_TMP") ;;
        *) echo "Refusing path outside cleanup scope: $path" >&2; exit 1 ;;
    esac
    rm -rf -- "$path"
done

echo "Removed ${#CANDIDATES[@]} generated cleanup candidate(s)."

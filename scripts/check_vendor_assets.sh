#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Check vendorized frontend assets against npm latest versions.

Usage:
  ./scripts/check_vendor_assets.sh [--strict] [--local-only]

Options:
  --strict      Exit non-zero when any asset is outdated or any check fails.
  --local-only  Skip npm queries, only print local detected versions.
  --help        Show this help message.
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 2
  fi
}

STRICT=0
LOCAL_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --local-only) LOCAL_ONLY=1 ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      usage
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

require_cmd grep
require_cmd sed

if [[ "$LOCAL_ONLY" -eq 0 ]]; then
  require_cmd npm
fi

bootstrap_css="nova/static/vendor/bootstrap/css/bootstrap.min.css"
bootstrap_icons_css="nova/static/vendor/bootstrap-icons/bootstrap-icons.min.css"
htmx_js="nova/static/vendor/htmx/htmx.min.js"

for f in "$bootstrap_css" "$bootstrap_icons_css" "$htmx_js"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing vendor asset: $f" >&2
    exit 2
  fi
done

extract_bootstrap_version() {
  grep -Eo 'Bootstrap[[:space:]]+v[0-9.]+' "$bootstrap_css" \
    | head -n1 \
    | sed -E 's/.*v([0-9.]+)/\1/'
}

extract_bootstrap_icons_version() {
  grep -Eo 'Bootstrap Icons v[0-9.]+' "$bootstrap_icons_css" \
    | head -n1 \
    | sed -E 's/.*v([0-9.]+)/\1/'
}

extract_htmx_version() {
  grep -Eo 'version:"[0-9.]+"' "$htmx_js" \
    | head -n1 \
    | sed -E 's/version:"([0-9.]+)"/\1/'
}

fetch_latest_version() {
  local package_name="$1"
  npm view "$package_name" version 2>/dev/null | tr -d '[:space:]'
}

print_header() {
  printf "%-20s %-12s %-12s %-18s\n" "Asset" "Local" "Latest" "Status"
  printf "%-20s %-12s %-12s %-18s\n" "--------------------" "------------" "------------" "------------------"
}

check_one() {
  local asset_name="$1"
  local npm_package="$2"
  local local_version="$3"

  local latest_version="n/a"
  local status="local_only"

  if [[ -z "$local_version" ]]; then
    local_version="unknown"
    status="local_parse_failed"
  fi

  if [[ "$LOCAL_ONLY" -eq 0 ]]; then
    latest_version="$(fetch_latest_version "$npm_package" || true)"
    if [[ -z "$latest_version" ]]; then
      latest_version="unknown"
      status="registry_error"
    elif [[ "$local_version" == "$latest_version" ]]; then
      status="up_to_date"
    else
      status="update_available"
    fi
  fi

  printf "%-20s %-12s %-12s %-18s\n" "$asset_name" "$local_version" "$latest_version" "$status"

  if [[ "$status" == "update_available" ]]; then
    OUTDATED_COUNT=$((OUTDATED_COUNT + 1))
  fi
  if [[ "$status" == "registry_error" || "$status" == "local_parse_failed" ]]; then
    ERROR_COUNT=$((ERROR_COUNT + 1))
  fi
}

OUTDATED_COUNT=0
ERROR_COUNT=0

print_header
check_one "bootstrap" "bootstrap" "$(extract_bootstrap_version)"
check_one "bootstrap-icons" "bootstrap-icons" "$(extract_bootstrap_icons_version)"
check_one "htmx" "htmx.org" "$(extract_htmx_version)"

if [[ "$LOCAL_ONLY" -eq 0 ]]; then
  echo
  echo "Summary: $OUTDATED_COUNT outdated, $ERROR_COUNT errors."
fi

if [[ "$STRICT" -eq 1 ]]; then
  if [[ "$OUTDATED_COUNT" -gt 0 || "$ERROR_COUNT" -gt 0 ]]; then
    exit 1
  fi
fi

exit 0

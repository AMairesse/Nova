#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Update vendorized frontend assets from npm packages.

Usage:
  ./scripts/update_vendor_assets.sh [--bootstrap <version>] [--bootstrap-icons <version>] [--htmx <version>]

Examples:
  # Update all assets to latest published versions
  ./scripts/update_vendor_assets.sh

  # Update with pinned versions
  ./scripts/update_vendor_assets.sh --bootstrap 5.3.8 --bootstrap-icons 1.11.4 --htmx 2.0.8
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 2
  fi
}

BOOTSTRAP_VERSION="latest"
BOOTSTRAP_ICONS_VERSION="latest"
HTMX_VERSION="latest"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bootstrap)
      BOOTSTRAP_VERSION="${2:-}"
      shift 2
      ;;
    --bootstrap-icons)
      BOOTSTRAP_ICONS_VERSION="${2:-}"
      shift 2
      ;;
    --htmx)
      HTMX_VERSION="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

require_cmd npm
require_cmd mktemp
require_cmd cp
require_cmd mkdir

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "Installing npm packages in temporary directory..."
pushd "$TMP_DIR" >/dev/null
npm init -y >/dev/null
npm install --no-audit --no-fund \
  "bootstrap@${BOOTSTRAP_VERSION}" \
  "bootstrap-icons@${BOOTSTRAP_ICONS_VERSION}" \
  "htmx.org@${HTMX_VERSION}" >/dev/null
popd >/dev/null

echo "Copying assets into nova/static/vendor..."
mkdir -p nova/static/vendor/bootstrap/css
mkdir -p nova/static/vendor/bootstrap/js
mkdir -p nova/static/vendor/bootstrap-icons
mkdir -p nova/static/vendor/bootstrap-icons/fonts
mkdir -p nova/static/vendor/htmx

cp "$TMP_DIR/node_modules/bootstrap/dist/css/bootstrap.min.css" \
  "nova/static/vendor/bootstrap/css/bootstrap.min.css"
cp "$TMP_DIR/node_modules/bootstrap/dist/js/bootstrap.bundle.min.js" \
  "nova/static/vendor/bootstrap/js/bootstrap.bundle.min.js"
cp "$TMP_DIR/node_modules/bootstrap-icons/font/bootstrap-icons.min.css" \
  "nova/static/vendor/bootstrap-icons/bootstrap-icons.min.css"
cp "$TMP_DIR/node_modules/bootstrap-icons/font/fonts/bootstrap-icons.woff2" \
  "nova/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2"
cp "$TMP_DIR/node_modules/bootstrap-icons/font/fonts/bootstrap-icons.woff" \
  "nova/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff"
cp "$TMP_DIR/node_modules/htmx.org/dist/htmx.min.js" \
  "nova/static/vendor/htmx/htmx.min.js"

bootstrap_resolved="$(
  npm --prefix "$TMP_DIR" view bootstrap version 2>/dev/null | tr -d '[:space:]'
)"
bootstrap_icons_resolved="$(
  npm --prefix "$TMP_DIR" view bootstrap-icons version 2>/dev/null | tr -d '[:space:]'
)"
htmx_resolved="$(
  npm --prefix "$TMP_DIR" view htmx.org version 2>/dev/null | tr -d '[:space:]'
)"

echo "Updated vendor assets:"
echo "  bootstrap:       ${bootstrap_resolved:-unknown}"
echo "  bootstrap-icons: ${bootstrap_icons_resolved:-unknown}"
echo "  htmx:            ${htmx_resolved:-unknown}"
echo
echo "Next step:"
echo "  git diff -- nova/static/vendor"

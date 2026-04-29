#!/usr/bin/env bash
set -euo pipefail

# Package ./fullDataExtractionForBTC/data into a versioned zip for GitHub Release assets.
# Usage:
#   scripts/package_data_release.sh                # auto version by timestamp
#   scripts/package_data_release.sh v2026.04.29   # custom version label

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_PARENT_DIR="$ROOT_DIR/fullDataExtractionForBTC"
DATA_DIR="$DATA_PARENT_DIR/data"
DIST_DIR="$ROOT_DIR/dist/dataset"
VERSION="${1:-$(date +%Y%m%d-%H%M%S)}"
ZIP_NAME="data-${VERSION}.zip"
ZIP_PATH="$DIST_DIR/$ZIP_NAME"
SHA_PATH="$ZIP_PATH.sha256"

if [[ ! -d "$DATA_DIR" ]]; then
  echo "[ERROR] data directory not found: $DATA_DIR" >&2
  exit 1
fi

mkdir -p "$DIST_DIR"

if command -v zip >/dev/null 2>&1; then
  (
    cd "$DATA_PARENT_DIR"
    rm -f "$ZIP_PATH"
    zip -r "$ZIP_PATH" data -x "data/.DS_Store" "data/**/.DS_Store" >/dev/null
  )
else
  echo "[ERROR] 'zip' command not found. Please install zip first." >&2
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$ZIP_PATH" > "$SHA_PATH"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$ZIP_PATH" > "$SHA_PATH"
else
  echo "[WARN] sha256 tool not found, skip checksum file." >&2
fi

SIZE_HUMAN="$(du -h "$ZIP_PATH" | awk '{print $1}')"
echo "[OK] Dataset package created"
echo "ZIP: $ZIP_PATH"
echo "SIZE: $SIZE_HUMAN"
if [[ -f "$SHA_PATH" ]]; then
  echo "SHA256: $SHA_PATH"
fi

echo
echo "Next (GitHub Release):"
echo "  gh release create \"$VERSION\" \"$ZIP_PATH\" \"$SHA_PATH\" --repo Zakariyya/fullDataExtractionForBTC --title \"Dataset $VERSION\" --notes \"Dataset package for release $VERSION\""
echo "Or upload to existing release:"
echo "  gh release upload \"$VERSION\" \"$ZIP_PATH\" \"$SHA_PATH\" --repo Zakariyya/fullDataExtractionForBTC --clobber"

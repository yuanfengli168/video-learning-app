#!/usr/bin/env bash
# copy_personal_assets.sh — copy gitignored personal files over the placeholders
#
# WHY THIS EXISTS
# ---------------
# The 4 QR codes (zelle, paynow, wechat, alipay) are personal — we don't
# want them in the public repo. But the site NEEDS real QRs to work.
#
# The workflow:
#   1. Save your real QR as any file containing `.local.` in its name
#      e.g. .zelle.png.local.PNG, .wechat.png.local, .paynow.JPG.local
#      (the .local. substring is the "block this" signal; see .gitignore)
#   2. Before deploying, run this script
#   3. The script finds any .local.* file matching each name and copies
#      the most recent one over the placeholder
#
# USAGE
# -----
#   ./scripts/copy_personal_assets.sh        # copies all 4 if found
#   ./scripts/copy_personal_assets.sh zelle  # copies just zelle
#   ./scripts/copy_personal_assets.sh --dry  # show what would be copied
#   ./scripts/copy_personal_assets.sh --list # just show what's available

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ASSETS_DIR="$SCRIPT_DIR/../assets/images/donate"

# name : placeholder_filename
PERSONAL_ASSETS=(
    "zelle:zelle.png"
    "paynow:paynow.png"
    "wechat:wechat.png"
    "alipay:alipay.png"
)

DRY_RUN=false
LIST_ONLY=false
ONLY=""
if [[ "${1:-}" == "--dry" ]]; then
    DRY_RUN=true
    shift
fi
if [[ "${1:-}" == "--list" ]]; then
    LIST_ONLY=true
    shift
fi
if [[ -n "${1:-}" ]]; then
    ONLY="$1"
fi

# Find all .local.* files in the donate dir (hidden + non-hidden)
find_candidates() {
    local name="$1"
    # Match any file in the donate dir that:
    #   - starts with .<name> or contains the name with .local.
    #   - has .local. anywhere in the filename
    # We use a relaxed match to be friendly to various naming styles.
    find "$ASSETS_DIR" -maxdepth 1 -type f \( \
        -name "*${name}*.local*" -o \
        -name ".${name}.*" -o \
        -name "*.${name}.local.*" \
    \) 2>/dev/null
}

copied=0
skipped=0
missing=()

for entry in "${PERSONAL_ASSETS[@]}"; do
    name="${entry%%:*}"
    placeholder="${entry##*:}"

    # Filter by --only flag
    if [[ -n "$ONLY" && "$ONLY" != "$name" ]]; then
        continue
    fi

    # Find all candidate source files for this name
    mapfile -t candidates < <(find_candidates "$name")

    if [[ ${#candidates[@]} -eq 0 ]]; then
        missing+=("$name (expected: $ASSETS_DIR/.${name}.<ext>.local.* or similar)")
        skipped=$((skipped + 1))
        continue
    fi

    if $LIST_ONLY; then
        for c in "${candidates[@]}"; do
            echo "  $name: $(basename "$c") ($(wc -c < "$c" | tr -d ' ') bytes)"
        done
        continue
    fi

    # Pick the most recently modified candidate (most recent wins)
    src=$(ls -t "${candidates[@]}" 2>/dev/null | head -1)
    dst="$ASSETS_DIR/$placeholder"

    if $DRY_RUN; then
        echo "[DRY] would copy $(basename "$src") → $placeholder"
    else
        cp "$src" "$dst"
        echo "✓ Copied $name → $placeholder ($(wc -c < "$dst" | tr -d ' ') bytes from $(basename "$src"))"
    fi
    copied=$((copied + 1))
done

if $LIST_ONLY; then
    exit 0
fi

echo ""
echo "Copied: $copied, Skipped: $skipped"

if [[ ${#missing[@]} -gt 0 ]]; then
    echo ""
    echo "Missing personal files (placeholders left as-is):"
    for f in "${missing[@]}"; do
        echo "  $f"
    done
    echo ""
    echo "To add a real QR, save it in $ASSETS_DIR/ with '.local.' in the name."
    echo "Examples (any of these work):"
    echo "  $ASSETS_DIR/.zelle.png.local.PNG"
    echo "  $ASSETS_DIR/.zelle.png.local"
    echo "  $ASSETS_DIR/zelle.local.png"
    echo ""
    echo "The '.local.' substring is what keeps it out of git (see .gitignore)."
fi

#!/usr/bin/env bash
# bin/build_ocr.sh — Compile bin/material_ocr.swift → bin/material_ocr
#
# MVP0.2 followup: builds the Swift CLI that wraps Apple's PDFKit +
# Vision framework for image-only PDF OCR. Re-run this whenever you
# edit bin/material_ocr.swift, or after pulling new changes.
#
# Requires: Swift toolchain (ships with Xcode Command Line Tools on
# macOS). Tested with Swift 6.2.
#
# The compiled binary is NOT checked into git (.gitignore); each
# developer builds locally. CI builds it before running tests.

set -euo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$BIN_DIR")"
cd "$PROJECT_ROOT"

SWIFT_SRC="$BIN_DIR/material_ocr.swift"
SWIFT_BIN="$BIN_DIR/material_ocr"

if [[ ! -f "$SWIFT_SRC" ]]; then
    echo "❌ Missing $SWIFT_SRC" >&2
    exit 1
fi

if ! command -v swiftc >/dev/null 2>&1; then
    echo "❌ swiftc not found — install Xcode Command Line Tools:" >&2
    echo "    xcode-select --install" >&2
    exit 1
fi

echo "🔨 Compiling material_ocr (PDFKit + Vision + AppKit)..."
swiftc -O "$SWIFT_SRC" -o "$SWIFT_BIN" \
    -framework PDFKit \
    -framework Vision \
    -framework AppKit

chmod +x "$SWIFT_BIN"
echo "✅ Built $SWIFT_BIN ($(file -b "$SWIFT_BIN" | cut -d: -f2))"
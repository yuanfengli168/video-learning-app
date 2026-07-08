// tests/test_transcript_follow.mjs
//
// Unit tests for the pure helpers exposed by app/static/js/transcript-follow.js.
// We load the production source in a sandbox with a fake `window` and
// assert against `window.TranscriptFollow._internals`. This way the
// production file is the single source of truth — if a refactor changes
// the helper semantics, the test fails here too.
//
// Run with: `node --test tests/test_transcript_follow.mjs`
// or via the pytest wrapper in tests/test_transcript_follow.py.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
    resolve(__dirname, '..', 'app', 'static', 'js', 'transcript-follow.js'),
    'utf8'
);

// Sandbox: run the IIFE with a fake window. The IIFE assigns
// `window.TranscriptFollow = { init, setMode, getMode, destroy, _internals }`.
const window = {};
const fn = new Function('window', SRC);
fn(window);
const T = window.TranscriptFollow._internals;

test('findActiveSegment: returns -1 for empty input', () => {
    assert.equal(T.findActiveSegment(0, []), -1);
    assert.equal(T.findActiveSegment(5, null), -1);
    assert.equal(T.findActiveSegment(5, undefined), -1);
});

test('findActiveSegment: returns -1 when no segment contains the time', () => {
    const segs = [{ start: 0, end: 1 }, { start: 2, end: 3 }];
    assert.equal(T.findActiveSegment(1.5, segs), -1);
    assert.equal(T.findActiveSegment(-1, segs), -1);
    assert.equal(T.findActiveSegment(99, segs), -1);
});

test('findActiveSegment: returns the correct index for in-range time', () => {
    const segs = [
        { start: 0, end: 1 },
        { start: 1, end: 2 },
        { start: 2, end: 3 },
    ];
    assert.equal(T.findActiveSegment(0.5, segs), 0);
    assert.equal(T.findActiveSegment(1.0, segs), 1);
    assert.equal(T.findActiveSegment(1.99, segs), 1);
    assert.equal(T.findActiveSegment(2.5, segs), 2);
});

test('findActiveSegment: half-open interval — segment.end is exclusive', () => {
    // Critical for not double-highlighting: at t=1.0, segment 1 starts
    // (not segment 0 which ends at 1.0).
    const segs = [{ start: 0, end: 1.0 }, { start: 1.0, end: 2.0 }];
    assert.equal(T.findActiveSegment(1.0, segs), 1);
    assert.equal(T.findActiveSegment(0.999, segs), 0);
});

test('shouldScroll: "always" mode always returns true', () => {
    const cr = { top: 0, bottom: 100, left: 0, right: 100, width: 100, height: 100 };
    assert.equal(T.shouldScroll({ top: 50, bottom: 60, height: 10 }, cr, 'always'), true);
    assert.equal(T.shouldScroll({ top: 50, bottom: 50, height: 0 }, cr, 'always'), true);
});

test('shouldScroll: "smart" mode returns false when line is in the safe zone', () => {
    // container height 100, default buffer 0.2 → safe zone is [20, 80]
    const cr = { top: 0, bottom: 100, left: 0, right: 100, width: 100, height: 100 };
    assert.equal(T.shouldScroll({ top: 30, bottom: 40, height: 10 }, cr, 'smart'), false);
    assert.equal(T.shouldScroll({ top: 60, bottom: 70, height: 10 }, cr, 'smart'), false);
    // At the very edge of the safe zone (top=20 or bottom=80), still in
    // (the comparison is strict < and >).
    assert.equal(T.shouldScroll({ top: 20, bottom: 30, height: 10 }, cr, 'smart'), false);
    assert.equal(T.shouldScroll({ top: 70, bottom: 80, height: 10 }, cr, 'smart'), false);
});

test('shouldScroll: "smart" mode returns true when line is above the safe zone', () => {
    // Real DOMRect (from getBoundingClientRect) has top/bottom/left/right/width/height.
    const cr = { top: 0, bottom: 100, left: 0, right: 100, width: 100, height: 100 };
    assert.equal(T.shouldScroll({ top: 5, bottom: 15, height: 10 }, cr, 'smart'), true);
    assert.equal(T.shouldScroll({ top: 0, bottom: 10, height: 10 }, cr, 'smart'), true);
});

test('shouldScroll: "smart" mode returns true when line is below the safe zone', () => {
    const cr = { top: 0, bottom: 100, left: 0, right: 100, width: 100, height: 100 };
    assert.equal(T.shouldScroll({ top: 85, bottom: 95, height: 10 }, cr, 'smart'), true);
    assert.equal(T.shouldScroll({ top: 90, bottom: 100, height: 10 }, cr, 'smart'), true);
});

test('shouldScroll: respects custom bufferFraction', () => {
    // buffer=0.3 → safe zone is the inner 40% ([30, 70])
    const cr = { top: 0, bottom: 100, left: 0, right: 100, width: 100, height: 100 };
    // Line at top=35 is INSIDE the safe zone (35 > 30).
    assert.equal(T.shouldScroll({ top: 35, bottom: 45, height: 10 }, cr, 'smart', 0.3), false);
    // Line at top=20 is ABOVE the safe zone (20 < 30).
    assert.equal(T.shouldScroll({ top: 20, bottom: 30, height: 10 }, cr, 'smart', 0.3), true);
    // Line at top=80 is BELOW the safe zone (80 > 70).
    assert.equal(T.shouldScroll({ top: 80, bottom: 90, height: 10 }, cr, 'smart', 0.3), true);
});

test('shouldScroll: buffer >= 0.5 produces an empty or inverted safe zone (always scroll)', () => {
    // At buffer=0.5 with height=100, safe zone is [50, 50] — a single
    // point. Any non-zero-height line is at least slightly out of it.
    // At buffer=1.0, safeTop=100 and safeBottom=0, so the zone is
    // inverted/empty: every line triggers a scroll. We never use these
    // values in production, but the contract should be explicit.
    const cr = { top: 0, bottom: 100, left: 0, right: 100, width: 100, height: 100 };
    assert.equal(T.shouldScroll({ top: 50, bottom: 55, height: 5 }, cr, 'smart', 0.5), true);
    assert.equal(T.shouldScroll({ top: 49, bottom: 50, height: 1 }, cr, 'smart', 1.0), true);
});

test('public surface: window.TranscriptFollow exposes the documented API', () => {
    // The dropdown in video.html calls these by name; a rename here
    // would silently break the integration. Lock the contract.
    assert.equal(typeof window.TranscriptFollow.init, 'function');
    assert.equal(typeof window.TranscriptFollow.setMode, 'function');
    assert.equal(typeof window.TranscriptFollow.getMode, 'function');
    assert.equal(typeof window.TranscriptFollow.destroy, 'function');
    assert.equal(typeof window.TranscriptFollow._internals, 'object');
});

test('storageKey: lowercases the email', () => {
    assert.equal(T.storageKey('Alice@Example.COM'), 'transcript.followMode.alice@example.com');
    assert.equal(T.storageKey('bob@x.io'), 'transcript.followMode.bob@x.io');
    assert.equal(T.storageKey(''), 'transcript.followMode.anon');
    assert.equal(T.storageKey(null), 'transcript.followMode.anon');
    assert.equal(T.storageKey(undefined), 'transcript.followMode.anon');
});

test('readPersistedMode/writePersistedMode: round-trips through localStorage', () => {
    // The production code reads from window.localStorage. Node 18's
    // built-in localStorage is not present, so we attach a tiny shim
    // for the duration of this test.
    const hadLocalStorage = typeof globalThis.localStorage !== 'undefined';
    if (!hadLocalStorage) {
        const store = new Map();
        globalThis.localStorage = {
            getItem: (k) => (store.has(k) ? store.get(k) : null),
            setItem: (k, v) => store.set(k, String(v)),
            removeItem: (k) => store.delete(k),
            clear: () => store.clear(),
            key: (i) => Array.from(store.keys())[i] || null,
            get length() { return store.size; },
        };
    }
    const key = T.storageKey('test-roundtrip@example.com');
    // Clean up any prior state.
    globalThis.localStorage.removeItem(key);

    // Default when nothing is stored.
    assert.equal(T.readPersistedMode('test-roundtrip@example.com'), 'smart');

    // Write 'always' and read it back.
    T.writePersistedMode('test-roundtrip@example.com', 'always');
    assert.equal(T.readPersistedMode('test-roundtrip@example.com'), 'always');

    // Write 'smart' and read it back.
    T.writePersistedMode('test-roundtrip@example.com', 'smart');
    assert.equal(T.readPersistedMode('test-roundtrip@example.com'), 'smart');

    // Garbage in storage is ignored, default 'smart' returned.
    globalThis.localStorage.setItem(key, 'something-weird');
    assert.equal(T.readPersistedMode('test-roundtrip@example.com'), 'smart');

    // Clean up.
    globalThis.localStorage.removeItem(key);
});

// ── Source-level regression: transcript scroll must be container-only ──
//
// Why this exists: scrollIntoView() scrolls the nearest scrollable
// ancestor, which on a long page is the BROWSER WINDOW — that scrolls
// the whole page and pushes the video out of view. The fix is to
// manually set `container.scrollTop` instead. This source-level test
// fails loudly if anyone re-introduces scrollIntoView in the
// transcript-follow code, so the regression is caught in CI rather
// than reported by users.
test('transcript-follow does NOT use scrollIntoView (it would scroll the window)', () => {
    // Strip comments so an `// scrollIntoView` mention in a comment
    // doesn't false-positive the test.
    const codeOnly = SRC
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/\/\/.*$/gm, '');
    assert.equal(
        codeOnly.includes('scrollIntoView'),
        false,
        'app/static/js/transcript-follow.js uses scrollIntoView, which '
        + 'scrolls the nearest scrollable ancestor (the browser window '
        + 'on long pages). This scrolls the whole page and pushes the '
        + 'video player out of view. Use `container.scrollTop = ...` '
        + 'to scroll ONLY the transcript container. See the '
        + 'scrollContainerToCenter helper for the correct pattern.'
    );
});

// The container-scroll helper uses pure DOM arithmetic (no
// scrollIntoView, no smooth behavior, no browser window scroll). This
// source-level test confirms the helper is wired up and avoids the
// banned APIs.
test('scrollContainerToCenter uses container.scrollTop, not scrollIntoView', () => {
    // Find the helper function in the source.
    const m = SRC.match(/function\s+scrollContainerToCenter[\s\S]*?\n\s{4}\}/);
    assert.ok(m, 'scrollContainerToCenter function must be defined');
    const helper = m[0];
    assert.match(helper, /container\.scrollTop\s*=/,
        'scrollContainerToCenter must assign container.scrollTop');
    assert.equal(helper.includes('scrollIntoView'), false,
        'scrollContainerToCenter must not use scrollIntoView');
});

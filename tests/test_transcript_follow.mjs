// tests/test_transcript_follow.mjs
//
// Unit tests for app/static/js/transcript-follow.js (MVP2.0 item #2).
//
// What we test:
//   1. Pure helpers on _internals (findActiveSegment).
//   2. Source-level regression: the JS must NOT use scrollIntoView
//      (it would scroll the nearest scrollable ancestor — the
//      browser window on long pages — pushing the video out of
//      view; see commit 3c2c895 for the original bug).
//   3. Integration behavior via a tiny DOM shim:
//      - On timeupdate, the active line gets .is-follow-active.
//      - On seeked, the highlight updates immediately (no play needed).
//      - On mouseenter, auto-scroll is suspended (no scrollTop change).
//      - On mouseleave, auto-scroll resumes (scrollTop updates to
//        match the current active line).
//      - rAF-throttled: many timeupdate events in one frame collapse
//        into one scroll.
//      - destroy() removes all listeners (re-init on same elements
//        doesn't double-fire).
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


// ── Tiny DOM shim ───────────────────────────────────────────────────────────
//
// jsdom is overkill for what we need: a fake window with a `requestAnimationFrame`
// that we control manually. We mock just enough of the DOM for the IIFE to
// install `window.TranscriptFollow`. The init() tests below create the elements
// they need and exercise the event handlers directly.


class FakeEventTarget {
    constructor() {
        this._listeners = {};
    }
    addEventListener(type, fn) {
        (this._listeners[type] = this._listeners[type] || []).push(fn);
    }
    removeEventListener(type, fn) {
        const arr = this._listeners[type] || [];
        const i = arr.indexOf(fn);
        if (i >= 0) arr.splice(i, 1);
    }
    dispatch(type, evt = {}) {
        for (const fn of (this._listeners[type] || [])) fn(evt);
    }
}


class FakeContainer extends FakeEventTarget {
    constructor() {
        super();
        this.scrollTop = 0;
        this.scrollHeight = 1000;       // arbitrary — controls the clamp
        this.clientHeight = 200;        // arbitrary — controls the clamp
        this._lines = [];               // FakeLine[]
    }
    setLines(lines) { this._lines = lines; }
    querySelectorAll(sel) {
        if (sel === '.transcript-line') return this._lines;
        return [];
    }
}


class FakeLine {
    constructor(offsetTop) {
        this.offsetTop = offsetTop;
        this.offsetHeight = 20;        // arbitrary line height
        this.classList = {
            _set: new Set(),
            add(c) { this._set.add(c); },
            remove(c) { this._set.delete(c); },
            contains(c) { return this._set.has(c); },
        };
    }
}


class FakeVideo extends FakeEventTarget {
    constructor() {
        super();
        this.currentTime = 0;
    }
    setCurrentTime(t) { this.currentTime = t; }
}


function makeWindow() {
    // Holds the rAF queue and the window.TranscriptFollow handle.
    //
    // The IIFE source uses `requestAnimationFrame(...)` as a free
    // variable, which in a browser resolves on `window` (== global).
    // In Node, we need to put our shim on BOTH `globalThis` (so the
    // IIFE sees it) and the returned win object (so we can drive
    // the queue from tests).
    const win = {
        _rafQueue: [],
        requestAnimationFrame: (cb) => { win._rafQueue.push(cb); return win._rafQueue.length; },
        cancelAnimationFrame: (id) => { delete win._rafQueue[id - 1]; },
    };
    globalThis.requestAnimationFrame = win.requestAnimationFrame;
    globalThis.cancelAnimationFrame = win.cancelAnimationFrame;
    return win;
}


function cleanupGlobalThis() {
    // Remove the rAF shims so the next test installs a fresh queue.
    delete globalThis.requestAnimationFrame;
    delete globalThis.cancelAnimationFrame;
}


function runRafs(win) {
    // Drain the queue. Each rAF callback may schedule another rAF, so we
    // iterate until the queue stops growing. In practice 1-2 passes is enough.
    //
    // We snapshot the current queue length and only run those entries.
    // A callback that calls rAF pushes a new entry at the new length,
    // which is then drained by the next outer pass. This way an
    // undefined slot left by cancelAnimationFrame (which delete[]s
    // an index) never gets visited.
    while (win._rafQueue.length > 0) {
        const snapshot = win._rafQueue.slice();
        win._rafQueue.length = 0;
        for (const cb of snapshot) {
            if (typeof cb === 'function') cb();
        }
        // Loop again if any callback scheduled a new rAF (now in _rafQueue).
    }
}


function loadTranscriptFollow() {
    const win = makeWindow();
    const fn = new Function('window', SRC);
    fn(win);
    // We intentionally leave the rAF shims on globalThis so the
    // IIFE's free `requestAnimationFrame` reference resolves.
    // Tests that loadTranscriptFollow again will overwrite the
    // shim with a new queue.
    return { win, TF: win.TranscriptFollow };
}


// ── 1. Pure helper tests ────────────────────────────────────────────────────

test('findActiveSegment: returns -1 for empty input', () => {
    const { TF } = loadTranscriptFollow();
    const T = TF._internals;
    assert.equal(T.findActiveSegment(0, []), -1);
    assert.equal(T.findActiveSegment(5, null), -1);
    assert.equal(T.findActiveSegment(5, undefined), -1);
});

test('findActiveSegment: returns -1 when no segment contains the time', () => {
    const { TF } = loadTranscriptFollow();
    const T = TF._internals;
    const segs = [{ start: 0, end: 1 }, { start: 2, end: 3 }];
    assert.equal(T.findActiveSegment(1.5, segs), -1);
    assert.equal(T.findActiveSegment(-1, segs), -1);
    assert.equal(T.findActiveSegment(99, segs), -1);
});

test('findActiveSegment: returns the correct index for in-range time', () => {
    const { TF } = loadTranscriptFollow();
    const T = TF._internals;
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
    const { TF } = loadTranscriptFollow();
    const T = TF._internals;
    const segs = [{ start: 0, end: 1.0 }, { start: 1.0, end: 2.0 }];
    assert.equal(T.findActiveSegment(1.0, segs), 1);
    assert.equal(T.findActiveSegment(0.999, segs), 0);
});


// ── 2. Source-level regression tests ────────────────────────────────────────

test('transcript-follow does NOT use scrollIntoView (regression guard)', () => {
    // scrollIntoView would scroll the nearest scrollable ancestor,
    // which on long pages is the BROWSER WINDOW. That scrolls the
    // whole page and pushes the video player out of view. The
    // fix (commit 3c2c895) is to assign container.scrollTop
    // directly. If a future change re-introduces scrollIntoView,
    // this test fails loudly in CI rather than in the user's
    // browser.
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
        + 'to scroll ONLY the transcript container.'
    );
});

test('transcript-follow no longer exposes smart/always modes', () => {
    // MVP2.0 item #2: the dropdown is gone, the modes are gone. If a
    // future change resurrects them, the test fails so we know to
    // reconsider.
    assert.equal(
        /['"]smart['"]\s*:\s*['"]smart['"]|['"]always['"]\s*:\s*['"]always['"]|setMode|getMode|storageKey|readPersistedMode|writePersistedMode|shouldScroll/.test(SRC),
        false,
        'transcript-follow.js still references the removed smart/always '
        + 'API. The MVP2.0 contract is a single top-anchor mode with '
        + 'no dropdown, no localStorage, no per-mode branches.'
    );
});

test('transcript-follow listens to seeked (not just timeupdate)', () => {
    // The highlight must update the instant the user drags the
    // timeline. timeupdate only fires on natural playback, so without
    // a seeked handler the highlight lags until the user hits play.
    assert.match(SRC, /['"]seeked['"]/, 'Must listen to the seeked event');
    assert.match(SRC, /timeupdate/, 'Must still listen to timeupdate');
});

test('transcript-follow has hover-to-pause via mouseenter/mouseleave', () => {
    assert.match(SRC, /['"]mouseenter['"]/, 'Must listen to mouseenter');
    assert.match(SRC, /['"]mouseleave['"]/, 'Must listen to mouseleave');
});

test('transcript-follow uses requestAnimationFrame for scroll', () => {
    // rAF batching: many timeupdate events in one frame collapse to one
    // scroll. Without rAF the scrollTop assignment would happen on
    // every timeupdate (~4 Hz) and jitter on rapid changes.
    assert.match(SRC, /requestAnimationFrame/, 'Must use rAF for scroll batching');
    assert.match(SRC, /cancelAnimationFrame/, 'Must cancel rAF on destroy');
});


// ── 3. Public surface ───────────────────────────────────────────────────────

test('public surface: window.TranscriptFollow exposes init + destroy only', () => {
    const { TF } = loadTranscriptFollow();
    // The contract after MVP2.0 item #2: init, destroy, _internals.
    // No setMode, no getMode — those are gone.
    assert.equal(typeof TF.init, 'function');
    assert.equal(typeof TF.destroy, 'function');
    assert.equal(typeof TF._internals, 'object');
    // Specifically: the removed API is GONE.
    assert.equal(TF.setMode, undefined, 'setMode must be removed (MVP2.0 #2)');
    assert.equal(TF.getMode, undefined, 'getMode must be removed (MVP2.0 #2)');
});

test('_internals: only findActiveSegment is exposed', () => {
    const { TF } = loadTranscriptFollow();
    const T = TF._internals;
    assert.equal(typeof T.findActiveSegment, 'function');
    assert.equal(T.shouldScroll, undefined, 'shouldScroll is gone');
    assert.equal(T.storageKey, undefined, 'storageKey is gone');
    assert.equal(T.readPersistedMode, undefined, 'readPersistedMode is gone');
    assert.equal(T.writePersistedMode, undefined, 'writePersistedMode is gone');
});


// ── 4. Integration: init wires the events; destroy unwires them ─────────────

test('integration: timeupdate highlights the active line', () => {
    const { TF, win } = loadTranscriptFollow();
    const container = new FakeContainer();
    const lines = [new FakeLine(0), new FakeLine(30), new FakeLine(60)];
    container.setLines(lines);
    const video = new FakeVideo();
    TF.init({
        container,
        video,
        segmentsProvider: () => [
            { start: 0, end: 1 },
            { start: 1, end: 2 },
            { start: 2, end: 3 },
        ],
    });
    video.setCurrentTime(1.5);
    video.dispatch('timeupdate');
    // Index 1 should be active, index 0 should not.
    assert.equal(lines[1].classList.contains('is-follow-active'), true);
    assert.equal(lines[0].classList.contains('is-follow-active'), false);
    assert.equal(lines[2].classList.contains('is-follow-active'), false);
    // The scroll should be scheduled (rAF in queue).
    assert.equal(win._rafQueue.length >= 1, true, 'rAF should be scheduled');
    runRafs(win);
    // After rAF fires, scrollTop should reflect line 1's offsetTop.
    // The scrollToTop function uses offsetTop - 4 (4px gap).
    assert.equal(container.scrollTop, lines[1].offsetTop - 4);
});

test('integration: seeked updates the highlight without needing timeupdate', () => {
    const { TF, win } = loadTranscriptFollow();
    const container = new FakeContainer();
    const lines = [new FakeLine(0), new FakeLine(30), new FakeLine(60)];
    container.setLines(lines);
    const video = new FakeVideo();
    TF.init({
        container,
        video,
        segmentsProvider: () => [
            { start: 0, end: 1 },
            { start: 1, end: 2 },
            { start: 2, end: 3 },
        ],
    });
    // Seek to t=2.5 (index 2). No timeupdate dispatched — only seeked.
    video.setCurrentTime(2.5);
    video.dispatch('seeked');
    assert.equal(lines[2].classList.contains('is-follow-active'), true);
    assert.equal(lines[1].classList.contains('is-follow-active'), false);
    runRafs(win);
    assert.equal(container.scrollTop, lines[2].offsetTop - 4);
});

test('integration: mouseenter pauses auto-scroll; mouseleave resumes it', () => {
    const { TF, win } = loadTranscriptFollow();
    const container = new FakeContainer();
    const lines = [new FakeLine(0), new FakeLine(30), new FakeLine(60)];
    container.setLines(lines);
    const video = new FakeVideo();
    TF.init({
        container,
        video,
        segmentsProvider: () => [
            { start: 0, end: 1 },
            { start: 1, end: 2 },
            { start: 2, end: 3 },
        ],
    });
    // User hovers the panel.
    container.dispatch('mouseenter');
    // Move video to line 1 and fire timeupdate.
    video.setCurrentTime(1.5);
    video.dispatch('timeupdate');
    // Highlight class is added (cheap, no scroll).
    assert.equal(lines[1].classList.contains('is-follow-active'), true);
    // rAF was scheduled but its callback will short-circuit because
    // isHovered === true. After running rAFs, scrollTop should NOT
    // have changed.
    const scrollBefore = container.scrollTop;
    runRafs(win);
    assert.equal(container.scrollTop, scrollBefore,
        'mouseleave should pause auto-scroll (scrollTop unchanged)');

    // User leaves the panel; auto-scroll should resume and snap to
    // the current active line.
    container.dispatch('mouseleave');
    // mouseleave re-runs updateActiveLine, which schedules a new rAF.
    runRafs(win);
    assert.equal(container.scrollTop, lines[1].offsetTop - 4,
        'mouseleave should resume scrolling and snap to active line');
});

test('integration: rAF batches rapid timeupdate events into one scroll', () => {
    const { TF, win } = loadTranscriptFollow();
    const container = new FakeContainer();
    const lines = [new FakeLine(0), new FakeLine(30), new FakeLine(60)];
    container.setLines(lines);
    const video = new FakeVideo();
    TF.init({
        container,
        video,
        segmentsProvider: () => [
            { start: 0, end: 1 },
            { start: 1, end: 2 },
            { start: 2, end: 3 },
        ],
    });
    // Fire 5 timeupdate events in the same frame (before any rAF runs).
    for (let t = 1.0; t <= 1.4; t += 0.1) {
        video.setCurrentTime(t);
        video.dispatch('timeupdate');
    }
    // Exactly one rAF should be queued (the rest short-circuited).
    assert.equal(win._rafQueue.length, 1,
        `Expected 1 rAF queued, got ${win._rafQueue.length}`);
    // Run it.
    runRafs(win);
    // scrollTop should reflect the LATEST line (index 1, offsetTop=30).
    assert.equal(container.scrollTop, lines[1].offsetTop - 4);
});

test('integration: destroy() removes all listeners', () => {
    const { TF, win } = loadTranscriptFollow();
    const container = new FakeContainer();
    const lines = [new FakeLine(0), new FakeLine(30), new FakeLine(60)];
    container.setLines(lines);
    const video = new FakeVideo();
    TF.init({
        container,
        video,
        segmentsProvider: () => [
            { start: 0, end: 1 },
            { start: 1, end: 2 },
            { start: 2, end: 3 },
        ],
    });
    TF.destroy();
    // After destroy, timeupdate should not change anything.
    const linesSnapshot = lines.map(l => l.classList.contains('is-follow-active'));
    video.setCurrentTime(1.5);
    video.dispatch('timeupdate');
    runRafs(win);
    // No class should have been added.
    const linesAfter = lines.map(l => l.classList.contains('is-follow-active'));
    assert.deepEqual(linesAfter, linesSnapshot);
});

test('integration: re-init on the same elements does not double-fire', () => {
    // After destroy(), init() should leave the DOM in a clean state.
    // A re-init on the same container/video must produce only one
    // active line, not two.
    const { TF, win } = loadTranscriptFollow();
    const container = new FakeContainer();
    const lines = [new FakeLine(0), new FakeLine(30)];
    container.setLines(lines);
    const video = new FakeVideo();
    const segs = () => [{ start: 0, end: 1 }, { start: 1, end: 2 }];

    TF.init({ container, video, segmentsProvider: segs });
    video.setCurrentTime(1.5);
    video.dispatch('timeupdate');
    runRafs(win);
    // First init: line 1 active.
    assert.equal(lines[1].classList.contains('is-follow-active'), true);
    assert.equal(lines[0].classList.contains('is-follow-active'), false);

    // Re-init: same container/video, new time.
    TF.init({ container, video, segmentsProvider: segs });
    video.setCurrentTime(0.5);
    video.dispatch('timeupdate');
    runRafs(win);
    // Now line 0 should be active and line 1 should NOT still be active.
    assert.equal(lines[0].classList.contains('is-follow-active'), true,
        'Line 0 should be active after re-init');
    assert.equal(lines[1].classList.contains('is-follow-active'), false,
        'Line 1 should NOT still be active after re-init (destroy worked)');
});

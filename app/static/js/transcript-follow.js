/*
 * transcript-follow.js — MVP1.1 experiment: auto-highlight + smart scroll
 * the transcript as the video plays. Behavior is opt-in per-user via a
 * dropdown next to the "📜 Transcript" heading.
 *
 * Design constraints (per the doc, MVP1.0-PostRelease § Optimization #1):
 *  1. Two modes:
 *     - "smart"  (default): highlight follows the active line, but we only
 *       scroll the line into view if it's outside the inner 60% of the
 *       container (20% buffer top + bottom). Matches the "highlight
 *       changes, only scroll when reaching out of the viewport" intent.
 *     - "always": Coursera-style — always scrollIntoView({block:'center'})
 *       on every active-line change.
 *  2. Mode persists in localStorage namespaced by user email so different
 *     accounts on the same browser don't share preferences. Email is
 *     stamped into <meta name="x-user-email"> in base.html.
 *  3. Pure helpers are exposed on window.TranscriptFollow._internals for
 *     testability (see tests/test_transcript_follow.mjs).
 *  4. Designed for easy removal: the only entry points the page touches
 *     are `init`, `setMode`, and `getMode`. The dropdown's onchange is
 *     the single integration point. Delete the <script> tag, the
 *     <select>, and this file, and the rest of the page is unchanged.
 *
 * Throttle: timeupdate fires ~4 Hz normally; we additionally throttle
 * scrollIntoView to once per 250ms to avoid layout thrash on long
 * videos where active lines change rapidly.
 */
(function () {
    'use strict';

    // ── Pure helpers (testable in isolation) ──
    function findActiveSegment(currentTime, segments) {
        if (!Array.isArray(segments) || segments.length === 0) return -1;
        for (let i = 0; i < segments.length; i++) {
            const s = segments[i];
            if (currentTime >= s.start && currentTime < s.end) return i;
        }
        return -1;
    }

    // shouldScroll(lineRect, containerRect, mode, bufferFraction)
    // Returns true when the line is outside the safe zone (top < safeTop
    // OR bottom > safeBottom) in smart mode, or always in 'always' mode.
    // bufferFraction defaults to 0.2 (20% top + 20% bottom, inner 60%).
    function shouldScroll(lineRect, containerRect, mode, bufferFraction) {
        if (mode === 'always') return true;
        const buf = (typeof bufferFraction === 'number' ? bufferFraction : 0.2) * containerRect.height;
        const safeTop = containerRect.top + buf;
        const safeBottom = containerRect.bottom - buf;
        return lineRect.top < safeTop || lineRect.bottom > safeBottom;
    }

    function storageKey(email) {
        return 'transcript.followMode.' + ((email || 'anon').toLowerCase());
    }

    function readPersistedMode(email) {
        try {
            const v = localStorage.getItem(storageKey(email));
            if (v === 'smart' || v === 'always') return v;
        } catch (e) { /* localStorage disabled / quota */ }
        return 'smart';
    }

    function writePersistedMode(email, mode) {
        try { localStorage.setItem(storageKey(email), mode); } catch (e) { /* ignore */ }
    }

    // ── State (closure-scoped) ──
    let mode = 'smart';
    let lastActiveIndex = -1;
    let lastScrollTime = 0;
    const SCROLL_THROTTLE_MS = 250;
    let container = null;
    let videoEl = null;
    let getSegments = null;
    let onTimeUpdate = null;
    let initialized = false;

    function getUserEmail() {
        // The base.html template stamps the email into a meta tag for
        // signed-in users. Anonymous users get 'anon' (local-only).
        const meta = document.querySelector('meta[name="x-user-email"]');
        if (meta && meta.content) return meta.content;
        return 'anon';
    }

    function highlightLine(idx) {
        if (!container) return;
        const lines = container.querySelectorAll('.transcript-line');
        if (lastActiveIndex >= 0 && lines[lastActiveIndex]) {
            lines[lastActiveIndex].classList.remove('is-follow-active');
        }
        if (idx >= 0 && lines[idx]) {
            lines[idx].classList.add('is-follow-active');
            const now = Date.now();
            if (now - lastScrollTime > SCROLL_THROTTLE_MS) {
                const lineRect = lines[idx].getBoundingClientRect();
                const containerRect = container.getBoundingClientRect();
                if (shouldScroll(lineRect, containerRect, mode)) {
                    // Scroll ONLY the transcript container's inner
                    // scrollbar, not the window. Using `scrollIntoView`
                    // here would scroll the nearest scrollable ancestor,
                    // which on a long page is the browser window — that
                    // would scroll the whole page down and push the
                    // video player out of view. We compute the desired
                    // scrollTop by aligning the line's offset within the
                    // container so it sits in the center.
                    scrollContainerToCenter(lines[idx]);
                    lastScrollTime = now;
                }
            }
        }
        lastActiveIndex = idx;
    }

    // Scroll the transcript container so the given line sits roughly at
    // the vertical center of the visible area. Pure DOM arithmetic —
    // no scrollIntoView, no jQuery, no layout thrash beyond what the
    // browser already does. Safe to call from the timeupdate handler
    // (throttled to once per 250ms by the caller).
    function scrollContainerToCenter(lineEl) {
        if (!container || !lineEl) return;
        // Offset of the line relative to the top of the container's
        // scrollable content. scrollTop + line.offsetTop gives the line's
        // position within the scrolled content.
        const lineTopWithinContent = lineEl.offsetTop;
        const lineHeight = lineEl.offsetHeight;
        const containerHeight = container.clientHeight;
        // We want the line to land at the vertical center of the
        // visible area. The visible area is the content from
        // [scrollTop, scrollTop + containerHeight]. To put the line
        // at the center, set scrollTop so that the line's center sits
        // at scrollTop + containerHeight / 2.
        const desired = lineTopWithinContent - (containerHeight / 2) + (lineHeight / 2);
        // Clamp to valid scroll range. Setting scrollTop to a value
        // outside [0, scrollHeight - clientHeight] is a no-op in some
        // browsers and an error in others — clamp defensively.
        const max = Math.max(0, container.scrollHeight - containerHeight);
        container.scrollTop = Math.max(0, Math.min(desired, max));
    }

    function init(opts) {
        if (initialized) destroy();
        container = opts && opts.container;
        videoEl = opts && opts.video;
        getSegments = opts && opts.segmentsProvider;
        const initial = (opts && opts.initialMode) || readPersistedMode(getUserEmail());
        mode = (initial === 'always') ? 'always' : 'smart';
        if (!container || !videoEl) return;
        onTimeUpdate = function () {
            if (!videoEl || !getSegments) return;
            const segs = getSegments();
            if (!segs) return;
            highlightLine(findActiveSegment(videoEl.currentTime, segs));
        };
        videoEl.addEventListener('timeupdate', onTimeUpdate);
        initialized = true;
    }

    function setMode(newMode, email) {
        mode = (newMode === 'always') ? 'always' : 'smart';
        writePersistedMode(email || getUserEmail(), mode);
    }

    function getMode() { return mode; }

    function destroy() {
        if (videoEl && onTimeUpdate) {
            videoEl.removeEventListener('timeupdate', onTimeUpdate);
        }
        if (container && lastActiveIndex >= 0) {
            const lines = container.querySelectorAll('.transcript-line');
            if (lines[lastActiveIndex]) {
                lines[lastActiveIndex].classList.remove('is-follow-active');
            }
        }
        container = null;
        videoEl = null;
        getSegments = null;
        onTimeUpdate = null;
        lastActiveIndex = -1;
        lastScrollTime = 0;
        initialized = false;
    }

    window.TranscriptFollow = {
        init: init,
        setMode: setMode,
        getMode: getMode,
        destroy: destroy,
        // Exposed for unit tests only — not part of the public surface.
        _internals: {
            findActiveSegment: findActiveSegment,
            shouldScroll: shouldScroll,
            storageKey: storageKey,
            readPersistedMode: readPersistedMode,
            writePersistedMode: writePersistedMode,
        },
    };
})();

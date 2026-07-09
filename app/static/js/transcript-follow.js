/*
 * transcript-follow.js — MVP2.0 item #2.
 *
 * Single behavior (was: smart/always dropdown in MVP1.1):
 *   - Find the transcript line whose [start, end) interval contains
 *     the video's currentTime. Mark it `.is-follow-active` and
 *     align the transcript container's scrollTop so the active line
 *     sits at the top of the visible area (4px gap). The page itself
 *     never moves.
 *
 * Why the old smart/always experiment is gone (see doc/manualTodo.txt
 * 2026-07-09 #1, and doc/MVP2.0-first-designQuestions.md #2):
 *   1. "smart" mode's safe-zone math was unreliable on long pages
 *      where the transcript container's own viewport was off-screen
 *      below the fold. The "is the line in the safe zone?" check
 *      used the container's bounding rect, but the user's gaze was
 *      on the video above — so the math said "don't scroll" while
 *      the user had no way to see what got highlighted.
 *   2. "always" mode scrolled the inner container to center on
 *      every line change, which felt disorienting — the text kept
 *      yanking under the reader's eye.
 *   3. The 250ms throttle dropped line changes on fast speech, so
 *      the highlight could lag the audio by half a second.
 *
 * New rules (this file):
 *   - One behavior, no modes, no dropdown, no localStorage, no
 *     per-user email meta. The previous MVP1.1 variants are
 *     preserved in git history (see the commit that added this
 *     file's predecessor if you need to resurrect them).
 *   - Highlight updates on every `timeupdate` event (no throttle;
 *     the work is a single class toggle). Throttling the SCROLL
 *     to once per animation frame is the only rate limit.
 *   - Scroll target: top of the visible area (4px gap), not center.
 *     Center is a worse reading position because the user's eye
 *     tracks the upper portion of the screen.
 *   - `seeked` event: when the user drags the timeline, the
 *     highlight updates immediately (timeupdate only fires on
 *     natural playback; without the seeked hook the highlight
 *     lagged until the user hit play again).
 *   - Hover-to-pause: while the mouse is over the transcript
 *     panel, auto-scroll is suspended. Lifts when the mouse
 *     leaves or the user clicks a different line.
 *
 * Container-only scroll (vs scrollIntoView):
 *   We assign `container.scrollTop` directly. scrollIntoView would
 *   scroll the nearest scrollable ancestor, which on a long page
 *   is the browser WINDOW — pushing the video player out of view.
 *   See commit 3c2c895 (lyf) for the original bug fix this guards
 *   against. A source-level test in test_transcript_follow.mjs
 *   fails loudly if anyone re-introduces scrollIntoView here.
 *
 * Pure helpers exposed for unit tests on window.TranscriptFollow
 * ._internals.findActiveSegment. The rest of the surface is the
 * init/destroy lifecycle.
 */
(function () {
    'use strict';

    // ── Pure helpers (testable in isolation) ──

    /**
     * Find the index of the segment whose [start, end) interval
     * contains `currentTime`. Returns -1 if no segment matches.
     *
     * Half-open interval (currentTime < segment.end) is intentional:
     * at the boundary currentTime === segment.end, the NEXT segment
     * owns the timestamp. This prevents double-highlighting at
     * exact boundaries.
     */
    function findActiveSegment(currentTime, segments) {
        if (!Array.isArray(segments) || segments.length === 0) return -1;
        for (let i = 0; i < segments.length; i++) {
            const s = segments[i];
            if (currentTime >= s.start && currentTime < s.end) return i;
        }
        return -1;
    }

    // ── State (closure-scoped) ──

    let lastActiveIndex = -1;
    let container = null;
    let videoEl = null;
    let getSegments = null;
    let onTimeUpdate = null;
    let onSeeked = null;
    let onMouseEnter = null;
    let onMouseLeave = null;
    let rafId = null;        // one rAF in flight at a time
    let pendingIdx = -1;     // idx waiting for the rAF to fire
    let pendingForce = false;// force flag of the latest scheduleScroll call
    let isHovered = false;   // true while mouse is over the panel
    let initialized = false;

    /**
     * Align the transcript container's scrollTop so `lineEl` is
     * pinned to the top of the visible area, with a 4px breathing
     * gap above it.
     *
     * Why NOT `lineEl.offsetTop`:
     *   offsetTop is measured from the element's offsetParent, which
     *   is the NEAREST POSITIONED ancestor (position !== static).
     *   The transcript container has `position: static` (no Tailwind
     *   utility sets position on it), so its lines' offsetParent is
     *   some distant ancestor — potentially the <body>. That means
     *   offsetTop for line 0 could be 400-700px (the height of the
     *   header + video player + everything above the transcript),
     *   NOT 0. Setting container.scrollTop = offsetTop - 4 would
     *   then scroll to line ~13 instead of line 0.
     *
     * Why `getBoundingClientRect` works:
     *   getBoundingClientRect() always returns viewport-relative
     *   positions, regardless of offsetParent. By subtracting the
     *   container's viewport top from the line's viewport top and
     *   adding container.scrollTop, we get the line's absolute
     *   position within the scrollable content — which IS what we
     *   need to set scrollTop to.
     *
     * Why not scrollIntoView: see the module header comment.
     */
    function scrollToTop(lineEl) {
        if (!container || !lineEl) return;
        const containerRect = container.getBoundingClientRect();
        const lineRect = lineEl.getBoundingClientRect();
        // Line's absolute position in the scrollable content:
        //   container.scrollTop = current scroll offset (content hidden above)
        //   lineRect.top - containerRect.top = line's visible offset from
        //   the top of the container's visible area (can be negative if
        //   the line is scrolled out of view above)
        const lineInContent = container.scrollTop + (lineRect.top - containerRect.top);
        const desired = lineInContent - 4;  // 4px gap
        const max = Math.max(0, container.scrollHeight - container.clientHeight);
        container.scrollTop = Math.max(0, Math.min(desired, max));
    }

    /**
     * rAF-throttled scroll update. Multiple timeupdate events
     * firing within a single frame collapse to one scroll,
     * eliminating jitter on rapid segment changes.
     *
     * `force` (default false) bypasses the isHovered gate. Used by
     * the init handler and the mouseleave handler, both of which
     * represent user actions that should override the hover-pause.
     * The 2026-07-09 #2 review bug: the user opens a video page,
     * the mouse is over the panel (or fires mouseenter for any
     * reason on page load), isHovered is true, the rAF short-
     * circuits, and the panel stays scrolled to wherever the
     * browser initialized it (e.g. a restored scrollTop, or just
     * not 0). The user sees lines from 00:10 even though the video
     * is at 0:00 and the active highlight is on 00:00 — they
     * can't see the highlighted line because it's off-screen.
     *
     * If a rAF is already pending, we don't schedule a new one —
     * we just update pendingIdx and pendingForce so the rAF, when
     * it runs, uses the LATEST values from the most recent
     * scheduleScroll call. This matters for the init case: init
     * calls scheduleScroll(0, true) [force], then a timeupdate
     * before the rAF runs calls scheduleScroll(1, false) [no
     * force]. The pending rAF should use idx=1 AND force=false
     * (the latest), not the values from when it was scheduled.
     */
    function scheduleScroll(idx, force) {
        pendingIdx = idx;
        pendingForce = !!force;
        if (rafId !== null) return;  // already scheduled
        rafId = requestAnimationFrame(() => {
            rafId = null;
            if (!container) return;
            if (pendingIdx < 0) return;
            if (isHovered && !pendingForce) return;  // user is reading; don't yank
            const lines = container.querySelectorAll('.transcript-line');
            if (lines[pendingIdx]) {
                scrollToTop(lines[pendingIdx]);
            }
        });
    }

    /**
     * Toggle the .is-follow-active class on the active line. Updates
     * the rAF-throttled scroll schedule. No scroll if the active
     * line hasn't actually changed (common — timeupdate fires
     * 4 Hz, segments last 5+ seconds).
     *
     * If `forceScroll` is true, schedule a scroll even when the line
     * hasn't changed. Used by the mouseleave handler (panel snaps
     * back when the user moves the mouse off) and by init() (the
     * panel must snap to the active line on first open, regardless
     * of hover state).
     */
    function highlightLine(idx, forceScroll) {
        if (!container) return;
        const lines = container.querySelectorAll('.transcript-line');
        if (lastActiveIndex >= 0 && lines[lastActiveIndex]) {
            lines[lastActiveIndex].classList.remove('is-follow-active');
        }
        if (idx >= 0 && lines[idx]) {
            lines[idx].classList.add('is-follow-active');
        }
        if (idx !== lastActiveIndex || forceScroll) {
            scheduleScroll(idx, forceScroll);
        }
        lastActiveIndex = idx;
    }

    /**
     * Single update path for both `timeupdate` and `seeked`. The
     * two events differ in how often they fire (timeupdate ~4 Hz
     * on natural playback; seeked once per user seek), but they
     * ultimately want the same answer: which line is active now?
     *
     * `forceScroll` is true when called from the mouseleave handler:
     * while hovered, scrolls were suppressed; we need to snap back
     * even if the active line hasn't changed.
     */
    function updateActiveLine(forceScroll) {
        if (!videoEl || !getSegments) return;
        const segs = getSegments();
        if (!segs) return;
        highlightLine(findActiveSegment(videoEl.currentTime, segs), forceScroll);
    }

    function init(opts) {
        if (initialized) destroy();
        container = opts && opts.container;
        videoEl = opts && opts.video;
        getSegments = opts && opts.segmentsProvider;
        if (!container || !videoEl) return;
        isHovered = false;
        lastActiveIndex = -1;
        pendingIdx = -1;
        pendingForce = false;
        rafId = null;

        // Wrap the event handlers so the event object (the first
        // argument the browser passes) is dropped. Otherwise
        // `updateActiveLine` would receive the event as its
        // `forceScroll` parameter (which is truthy), and every
        // timeupdate / seeked would force-scroll even when hovered.
        const wrappedUpdate = function () { updateActiveLine(false); };
        onTimeUpdate = wrappedUpdate;
        onSeeked = wrappedUpdate;
        videoEl.addEventListener('timeupdate', onTimeUpdate);
        videoEl.addEventListener('seeked', onSeeked);

        // Hover-to-pause: while the cursor is over the transcript
        // panel, suspend auto-scroll so the user can read without
        // the panel yanking under them. Mouse leave re-runs the
        // current active-line update, so the panel snaps back to
        // the right line.
        onMouseEnter = function () { isHovered = true; };
        onMouseLeave = function () {
            isHovered = false;
            // forceScroll=true so the panel snaps to the current
            // active line even if it's the same line that was shown
            // before the hover (the prior update was scroll-suppressed).
            updateActiveLine(true);
        };
        container.addEventListener('mouseenter', onMouseEnter);
        container.addEventListener('mouseleave', onMouseLeave);

        // Seed the initial highlight AND force a scroll to the
        // active line, bypassing the isHovered gate. The user just
        // opened the page; the panel MUST show the line at
        // currentTime (typically 0:00) at the top, regardless of
        // whether the mouse happens to be over the panel. See
        // doc/manualTodo.txt 2026-07-09 #1 (the screenshot bug).
        updateActiveLine(true);
        initialized = true;
    }

    function destroy() {
        if (videoEl && onTimeUpdate) {
            videoEl.removeEventListener('timeupdate', onTimeUpdate);
            videoEl.removeEventListener('seeked', onSeeked);
        }
        if (container) {
            if (onMouseEnter) container.removeEventListener('mouseenter', onMouseEnter);
            if (onMouseLeave) container.removeEventListener('mouseleave', onMouseLeave);
        }
        if (container && lastActiveIndex >= 0) {
            const lines = container.querySelectorAll('.transcript-line');
            if (lines[lastActiveIndex]) {
                lines[lastActiveIndex].classList.remove('is-follow-active');
            }
        }
        if (rafId !== null) {
            cancelAnimationFrame(rafId);
            rafId = null;
        }
        container = null;
        videoEl = null;
        getSegments = null;
        onTimeUpdate = null;
        onSeeked = null;
        onMouseEnter = null;
        onMouseLeave = null;
        pendingIdx = -1;
        pendingForce = false;
        lastActiveIndex = -1;
        isHovered = false;
        initialized = false;
    }

    window.TranscriptFollow = {
        init: init,
        destroy: destroy,
        // Exposed for unit tests only — not part of the public surface.
        _internals: {
            findActiveSegment: findActiveSegment,
        },
    };
})();

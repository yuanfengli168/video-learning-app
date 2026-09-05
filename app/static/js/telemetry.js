/* telemetry.js — batched UI-event beacon (2026-09-05).
 *
 * Feeds POST /api/telemetry (see app/routers/telemetry.py for the
 * server contract + security design).
 *
 * Design (friend-tester scale, privacy-conscious):
 *   - QUEUE client-side; flush every 10s (and on page hide via
 *     navigator.sendBeacon so the last ~10s of events survive
 *     navigation — sendBeacon fires even while the page unloads).
 *   - NEVER block the UI: every call is wrapped in try/catch, a
 *     failed flush just retries next interval. The user never sees
 *     telemetry errors.
 *   - SEEK DEBOUNCE: scrubbing fires dozens of 'seeked' events.
 *     We debounce per-video to at most one seek event per 2s.
 *   - AUTH: beacon only sends when the user is signed in — the server
 *     401s anonymous requests anyway, so we skip the wasted payload
 *     client-side too. Signed-in state is read from the fb_token
 *     cookie's presence (session cookie set by the backend on login).
 *
 * Event shapes match the server's allowlist exactly:
 *   ui.login     {}                                  (fired from login.html)
 *   ui.player    {action: play|pause|seek|ended, position_ms?, from_ms?, to_ms?}
 *   ui.materials {tab: <name>}                        (video page tabs)
 *   ui.chat      {}                                   (chat message sent)
 *   ui.actions   {action: transcribe|generate, model?}
 */

(function (global) {
    'use strict';

    var QUEUE = [];
    var FLUSH_INTERVAL_MS = 10_000;
    var SEEK_DEBOUNCE_MS = 2_000;
    var MAX_QUEUE = 100; // hard cap — drop oldest if a tab is left open for days
    var flushing = false;

    var lastSeekAt = 0;      // per-page (single video page per load)
    var lastSeekFrom = null; // from_ms of the debounced seek, for context

    function isSignedIn() {
        try {
            // Session cookie (httpOnly — we can't read it, but its
            // presence pairs with the server's session; cookie name
            // matches middleware_session.py's SESSION_COOKIE_NAME).
            return document.cookie.indexOf('fb_token=') !== -1;
        } catch (e) {
            return false;
        }
    }

    /** Queue one event. Silently drops when unsigned-in or over cap. */
    function track(source, videoId, context) {
        try {
            if (!isSignedIn()) return;
            var ev = { source: source };
            if (videoId) ev.video_id = String(videoId);
            if (context) ev.context = context;
            if (QUEUE.length >= MAX_QUEUE) QUEUE.shift();
            QUEUE.push(ev);
        } catch (e) {
            /* never let telemetry break the page */
        }
    }

    function flushSync() {
        // sendBeacon path — for page hide/unload. Blob keeps the
        // Content-Type: application/json the endpoint expects.
        try {
            if (!QUEUE.length || !isSignedIn()) return;
            var blob = new Blob([JSON.stringify({ events: QUEUE })],
                                { type: 'application/json' });
            var ok = navigator.sendBeacon('/api/telemetry', blob);
            if (ok) QUEUE.length = 0;
        } catch (e) {
            /* best effort only */
        }
    }

    function flush() {
        if (flushing) return;
        flushing = true;
        try {
            if (!QUEUE.length || !isSignedIn()) return;
            var payload = { events: QUEUE.slice() };
            fetch('/api/telemetry', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify(payload),
            })
                .then(function (resp) {
                    // Any 2xx (202) → drop the batch. 400 means our
                    // client contract is wrong — drop too (never
                    // retry garbage). 401 → signed out; drop.
                    if (resp.ok || resp.status === 400 || resp.status === 401) {
                        QUEUE = QUEUE.slice(payload.events.length);
                    }
                    // 5xx → keep queue, retry next interval.
                })
                .catch(function () { /* offline — retry next interval */ });
        } finally {
            flushing = false;
        }
    }

    // ── Auto-flush loop + page-hide ────────────────────────────────────
    setInterval(flush, FLUSH_INTERVAL_MS);
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') flushSync();
    });

    // ── Native <video> player events (uploaded videos) ─────────────────
    // YouTube-iframe play/pause hooks are wired in yt_player.js's
    // onStateChange handler (Day 8 wrapper) — see yt_player.js's
    // telemetry block. This covers the <video> backend; the iframe
    // backend calls the same track() below.
    function wireVideoElement(videoId) {
        var v = document.querySelector('video');
        if (!v || !videoId) return;

        v.addEventListener('play', function () {
            track('ui.player', videoId, { action: 'play', position_ms: Math.round(v.currentTime * 1000) });
        });
        v.addEventListener('pause', function () {
            // 'pause' also fires right before 'ended' — skip that one
            // to avoid double-counting the end of a video.
            if (!v.ended) {
                track('ui.player', videoId, { action: 'pause', position_ms: Math.round(v.currentTime * 1000) });
            }
        });
        v.addEventListener('ended', function () {
            track('ui.player', videoId, { action: 'ended', position_ms: Math.round(v.currentTime * 1000) });
        });
        v.addEventListener('seeked', function () {
            var now = Date.now();
            if (now - lastSeekAt < SEEK_DEBOUNCE_MS) return;
            lastSeekAt = now;
            track('ui.player', videoId, { action: 'seek', to_ms: Math.round(v.currentTime * 1000) });
        });
    }

    // ── Exports ────────────────────────────────────────────────────────
    global.Telemetry = {
        track: track,
        flush: flush,
        wireVideoElement: wireVideoElement,
    };
})(window);
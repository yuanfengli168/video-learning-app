/*
 * yt_player.js — Day 8: unified player wrapper for <video> AND YouTube iframes.
 *
 * Why this exists:
 *   Before Day 8, every piece of JS that wanted to control playback
 *   (seekTo, play, pause, getCurrentTime) called `video.currentTime`
 *   directly on a `<video>` element. That works for legacy uploads
 *   (video.html uses `<video>` when `video.youtube_id` is empty) but
 *   silently no-ops for admin-curated YouTube videos, which use an
 *   `<iframe>` from youtube-nocookie.com. Clicking a transcript line
 *   would do nothing. Clicking a mindmap node would do nothing.
 *   Users saw "the video ignored my click" with no error.
 *
 * What this module provides:
 *   A single `YTPlayer` object with the same shape regardless of
 *   whether the page is showing a `<video>` or a YouTube iframe:
 *
 *     const player = await YTPlayer.init();
 *     await player.seekTo(seconds);   // works for BOTH backends
 *     await player.play();
 *     await player.pause();
 *     player.getCurrentTime();         // sync, polled via getDuration() too
 *     player.getDuration();
 *     player.on('timeupdate', handler);// for transcript-follow.js
 *     player.on('ready', handler);
 *
 * Backend detection (in order):
 *   1. Look for an existing <iframe id="youtube-player"> (Day 8+).
 *   2. Fall back to <video> (legacy / user-uploaded).
 *   3. If neither, init() rejects with a clear error.
 *
 * YouTube IFrame API loading:
 *   - We load https://www.youtube.com/iframe_api exactly once (idempotent).
 *   - It calls window.onYouTubeIframeAPIReady when ready (a global hook
 *     we set BEFORE injecting the script).
 *   - We add `enablejsapi=1` to the iframe src on init so postMessage works.
 *   - We construct YT.Player(target, { events: {...} }) to get a player object.
 *   - We poll getCurrentTime() via requestAnimationFrame (cheap; YT's native
 *     events are sparser).
 *
 * Why not use the <video> element as a peer instead of the API?
 *   Because YouTube content security policy blocks reading content via
 *   <video>. Cross-origin. The only supported way is YT.Player's iframe API.
 *
 * Security:
 *   - We never evaluate YouTube-returned strings (no eval, no Function()).
 *   - We trust YT.Player's typed event payloads only.
 *   - The iframe is from youtube-nocookie.com (no tracking cookies
 *     until the user clicks play).
 *
 * Tradeoff:
 *   YouTube iframe API takes ~500-1500ms to load on first visit (it's a
 *   separate script + postMessage handshake). For users on slow networks
 *   the transcript clicks during that window will queue until ready.
 *   We expose `player.isReady` so callers can decide whether to disable
 *   the transcript click during the warmup window.
 *
 * Loaded via:
 *   <script src="/static/js/yt_player.js" defer></script>
 *   before any script that calls YTPlayer.init().
 */

(function (global) {
    'use strict';

    // ─── State ──────────────────────────────────────────────────────────

    // Singleton — one player per page (we only ever embed one video).
    let playerInstance = null;
    let initPromise = null;
    let apiScriptLoaded = false;
    let apiReady = false;
    const readyHandlers = [];
    const timeUpdateHandlers = [];

    // ─── YouTube IFrame API loader (idempotent) ──────────────────────────

    /**
     * Inject <script src="https://www.youtube.com/iframe_api"> exactly once.
     * Resolves when window.YT.Player is constructable.
     */
    function loadYouTubeApi() {
        if (apiScriptLoaded) {
            // Already loading or loaded — wait for ready callback
            return new Promise((resolve) => {
                if (apiReady) return resolve();
                const prev = global.onYouTubeIframeAPIReady;
                global.onYouTubeIframeAPIReady = function () {
                    if (typeof prev === 'function') prev();
                    apiReady = true;
                    resolve();
                };
            });
        }
        apiScriptLoaded = true;

        return new Promise((resolve, reject) => {
            const prev = global.onYouTubeIframeAPIReady;
            global.onYouTubeIframeAPIReady = function () {
                if (typeof prev === 'function') prev();
                apiReady = true;
                resolve();
            };
            const tag = document.createElement('script');
            tag.src = 'https://www.youtube.com/iframe_api';
            tag.onerror = () => reject(new Error('Failed to load YouTube IFrame API'));
            const firstScript = document.getElementsByTagName('script')[0];
            firstScript.parentNode.insertBefore(tag, firstScript);
        });
    }

    // ─── Backend detection ──────────────────────────────────────────────

    /**
     * Return one of:
     *   { kind: 'youtube', iframe: HTMLIFrameElement }
     *   { kind: 'native',  video:  HTMLVideoElement    }
     *   null  (neither present)
     */
    function detectBackend() {
        const ytIframe = document.getElementById('youtube-player');
        if (ytIframe && ytIframe.tagName === 'IFRAME') {
            return { kind: 'youtube', iframe: ytIframe };
        }
        const video = document.querySelector('video');
        if (video) {
            return { kind: 'native', video: video };
        }
        return null;
    }

    // ─── Backend-specific player impls ───────────────────────────────────

    function buildYouTubePlayer(iframe) {
        // Ensure enablejsapi=1 in the src so postMessage commands work.
        try {
            const url = new URL(iframe.src, window.location.href);
            if (url.searchParams.get('enablejsapi') !== '1') {
                url.searchParams.set('enablejsapi', '1');
                url.searchParams.set('origin', window.location.origin);
                iframe.src = url.toString();
            }
        } catch (e) {
            // If URL parsing fails, fall back to appending the param manually.
            if (iframe.src.indexOf('enablejsapi=1') === -1) {
                iframe.src += (iframe.src.indexOf('?') === -1 ? '?' : '&') + 'enablejsapi=1';
            }
        }

        // We need a unique target id for YT.Player. YT.Player accepts
        // an HTMLElement directly in the modern API.
        const ytPlayer = new global.YT.Player(iframe, {
            events: {
                onReady: () => {
                    // Fire ready handlers
                    readyHandlers.forEach((h) => {
                        try { h(); } catch (e) { console.error('YTPlayer ready handler error:', e); }
                    });
                    startTimeUpdateLoop(() => ytPlayer.getCurrentTime(), ytPlayer.getDuration());
                },
                onStateChange: (event) => {
                    // 1 = playing, 2 = paused, 0 = ended
                    // 2026-09-05 telemetry: forward player state to the
                    // beacon (batches → POST /api/telemetry). Wrapped in
                    // try/catch — telemetry must never break playback.
                    // Only fires when Telemetry is on the page (video
                    // pages); absent on other pages = no-op.
                    try {
                        if (global.Telemetry && window.__telemetryVideoId) {
                            var s = event.data;
                            var t = Math.round((ytPlayer.getCurrentTime() || 0) * 1000);
                            if (s === 1) global.Telemetry.track('ui.player', window.__telemetryVideoId, { action: 'play', position_ms: t });
                            else if (s === 2) global.Telemetry.track('ui.player', window.__telemetryVideoId, { action: 'pause', position_ms: t });
                            else if (s === 0) global.Telemetry.track('ui.player', window.__telemetryVideoId, { action: 'ended', position_ms: t });
                        }
                    } catch (te) { /* telemetry is best-effort */ }
                    // We don't emit a 'statechange' event today; the
                    // transcript-follow component only needs currentTime.
                    // If a future caller needs state, add it here.
                },
                onError: (event) => {
                    // YT error codes: 2 = invalid id, 5 = HTML5 error,
                    // 100 = video not found, 101/150 = embedding disabled.
                    console.warn('YTPlayer iframe error:', event.data);
                },
            },
        });

        return {
            kind: 'youtube',
            isReady: true,
            play: () => ytPlayer.playVideo(),
            pause: () => ytPlayer.pauseVideo(),
            seekTo: (seconds) => ytPlayer.seekTo(seconds, true),
            getCurrentTime: () => ytPlayer.getCurrentTime() || 0,
            getDuration: () => ytPlayer.getDuration() || 0,
            // 2026-09-06 regression fix: transcript-follow.js checks
            // `typeof video.onTimeUpdate === 'function' && video.kind`
            // to decide wrapper-vs-raw-<video>. The Day-8 instance
            // shipped `.kind` but NOT `.onTimeUpdate`, so the check
            // failed, fell into the raw-DOM branch, and
            // `instance.addEventListener` threw a TypeError —
            // silently killing the transcript follow/highlight for
            // every YouTube video. (Masked until the COEP fix because
            // the player itself was a dead black box before that.)
            // Delegates to the module-level subscriber registry — the
            // rAF loop (startTimeUpdateLoop) already polls and fires
            // those handlers for this backend.
            onTimeUpdate: (handler) => onTimeUpdate(handler),
            _raw: ytPlayer,
        };
    }

    function buildNativePlayer(video) {
        const emit = () => {
            const t = video.currentTime;
            timeUpdateHandlers.forEach((h) => {
                try { h(t); } catch (e) { console.error('YTPlayer timeupdate handler error:', e); }
            });
        };
        video.addEventListener('timeupdate', emit);
        video.addEventListener('seeked', emit);
        // Mark ready on next tick (gives callers a chance to wire up first)
        setTimeout(() => {
            readyHandlers.forEach((h) => {
                try { h(); } catch (e) { console.error('YTPlayer ready handler error:', e); }
            });
        }, 0);

        return {
            kind: 'native',
            isReady: true,
            play: () => video.play(),
            pause: () => video.pause(),
            seekTo: (seconds) => {
                video.currentTime = seconds;
                // Don't auto-play on seek (matches the existing behavior
                // for transcript click → seek + auto-play, but the caller
                // can call play() separately if desired)
            },
            getCurrentTime: () => video.currentTime || 0,
            getDuration: () => video.duration || 0,
            // 2026-09-06 regression fix: same as the YouTube instance —
            // transcript-follow.js keys on this method existing. For the
            // native backend the module-level handlers fire from the
            // video element's own timeupdate/seeked events (emit()).
            onTimeUpdate: (handler) => onTimeUpdate(handler),
            _raw: video,
        };
    }

    // ─── Time-update loop (for YouTube, which doesn't fire timeupdate) ──

    let rafId = null;
    let lastSampledTime = 0;

    /**
     * Start the rAF polling loop. Idempotent (safe to call from
     * anywhere, repeatedly) — the guard short-circuits if running.
     *
     * 2026-09-06 robustness fix (transcript-follow dead bug):
     *   The loop used to start ONLY from the YT.Player onReady
     *   callback, and the tick had no try/catch around getCurrent().
     *   Two failure modes produced the user-visible symptom
     *   "video plays but the transcript never highlights/scrolls":
     *     (a) if onReady never fires (or fires before any wiring),
     *         subscribers never receive time updates;
     *     (b) if getCurrent() throws ONCE (e.g. the YT API object in
     *         a transient bad state), the exception escapes tick(),
     *         requestAnimationFrame(tick) never re-schedules, and
     *         the loop dies SILENTLY forever — no console error,
     *         because nothing wraps the tick.
     *   Fixes: the loop now also starts lazily from onTimeUpdate()
     *   (first subscriber), and each tick isolates getCurrent() in
     *   try/catch so a bad sample skips that frame instead of
     *   killing the loop.
     */
    function startTimeUpdateLoop(getCurrent, getDuration) {
        if (rafId !== null) return; // already running
        const tick = () => {
            let t = lastSampledTime;
            try {
                t = getCurrent();
                if (typeof t === 'number' && isFinite(t)) {
                    lastSampledTime = t;
                }
            } catch (e) {
                // Bad sample — keep polling; YT API can throw
                // transiently mid-handshake. Do NOT rethrow (that
                // would kill the loop forever — see comment above).
            }
            timeUpdateHandlers.forEach((h) => {
                try { h(t); } catch (e) { console.error('YTPlayer timeupdate handler error:', e); }
            });
            rafId = requestAnimationFrame(tick);
        };
        rafId = requestAnimationFrame(tick);
    }

    function stopTimeUpdateLoop() {
        if (rafId !== null) {
            cancelAnimationFrame(rafId);
            rafId = null;
        }
    }

    // ─── Public API ─────────────────────────────────────────────────────

    /**
     * Initialize the player. Returns a promise that resolves to the
     * player instance. Idempotent — calling twice returns the same instance.
     */
    async function init() {
        if (playerInstance) return playerInstance;
        if (initPromise) return initPromise;

        initPromise = (async () => {
            const backend = detectBackend();
            if (!backend) {
                throw new Error('YTPlayer.init(): no <video> or #youtube-player iframe found');
            }
            if (backend.kind === 'youtube') {
                await loadYouTubeApi();
                playerInstance = buildYouTubePlayer(backend.iframe);
            } else {
                playerInstance = buildNativePlayer(backend.video);
            }
            return playerInstance;
        })();

        return initPromise;
    }

    /**
     * Get the current instance (or null if init() hasn't been called yet).
     */
    function getInstance() {
        return playerInstance;
    }

    /**
     * Register a one-shot handler for the 'ready' event. Fires when the
     * underlying player (YouTube iframe OR native video) is ready to
     * accept commands.
     *
     * Returns an unsubscribe function. If the player is ALREADY ready,
     * the handler is invoked synchronously and the unsubscriber is a no-op.
     */
    function onReady(handler) {
        if (playerInstance && playerInstance.isReady) {
            try { handler(); } catch (e) { console.error('YTPlayer ready handler error:', e); }
            return () => {};
        }
        readyHandlers.push(handler);
        return () => {
            const idx = readyHandlers.indexOf(handler);
            if (idx !== -1) readyHandlers.splice(idx, 1);
        };
    }

    /**
     * Register a handler for the 'timeupdate' event. Receives the
     * current time in seconds. Fires ~60fps for both backends (rAF
     * polling for YouTube, native events for <video>).
     *
     * 2026-09-06: also (lazily) ensures the rAF polling loop is
     * running for the YouTube backend — previously the loop only
     * started from YT's onReady, which left subscribers dead if
     * onReady never fired. See the comment on startTimeUpdateLoop.
     */
    function onTimeUpdate(handler) {
        timeUpdateHandlers.push(handler);
        // Lazy-start: if we have a YouTube player instance, make sure
        // the polling loop is live (idempotent no-op if already).
        // The instance's _raw is the YT.Player object.
        if (
            playerInstance &&
            playerInstance.kind === 'youtube' &&
            playerInstance._raw &&
            typeof playerInstance._raw.getCurrentTime === 'function'
        ) {
            startTimeUpdateLoop(
                () => playerInstance.getCurrentTime(),
                () => playerInstance.getDuration()
            );
        }
        return () => {
            const idx = timeUpdateHandlers.indexOf(handler);
            if (idx !== -1) timeUpdateHandlers.splice(idx, 1);
        };
    }

    // ─── Last-watched timestamp persistence (Day 8) ─────────────────────
    //
    // Persists the user's last position to localStorage keyed by video id,
    // so refreshing the page or coming back later resumes where they left off.
    // We don't push to the server (no schema field today) — localStorage is
    // per-browser, which matches the "single user on this device" use case.
    //
    // When MV2.1 adds a server-side `last_watched_at` column, swap the
    // storage backend to POST /api/videos/<id>/progress every N seconds.
    //
    // Privacy:
    //   - Stays in the browser; never sent to server, never logged.
    //   - Cleared by the user via Settings > Clear browsing data (like YouTube).
    //
    // Why not server-side today:
    //   - Requires a new column + migration + endpoint + tests = bigger lift
    //   - Day 8's plan says "jump-to-time" which localStorage already enables
    //   - Server-side can be added in Day 9+ when we know which device the
    //     user wants to "continue" from.

    const STORAGE_PREFIX = 'video_last_position_';
    const SAVE_THROTTLE_MS = 5000;     // write at most once per 5s
    const RESUME_THRESHOLD_S = 3;      // only auto-resume if > 3s into the video

    function storageKey(videoId) {
        // Defensive: videoId should be a UUID-ish string, not user input.
        // We strip everything except [a-zA-Z0-9_] so that:
        //   - path-traversal chars (../) can't escape the prefix
        //   - SQL-injection-looking chars (';-/) are neutralized
        //   - dashes are stripped (UUIDs have them but we don't want a
        //     collision attack via "abc-def" vs "abc/def" both being
        //     sanitized to the same key — by collapsing both to
        //     "abcdef" we ensure a deterministic key)
        return STORAGE_PREFIX + String(videoId).replace(/[^a-zA-Z0-9_]/g, '');
    }

    function savePosition(videoId, seconds) {
        if (!videoId || typeof seconds !== 'number' || !isFinite(seconds)) return;
        try {
            localStorage.setItem(storageKey(videoId), String(Math.max(0, seconds)));
        } catch (e) { /* localStorage may be disabled (private mode, quota) */ }
    }

    function loadPosition(videoId) {
        if (!videoId) return 0;
        try {
            const v = localStorage.getItem(storageKey(videoId));
            if (!v) return 0;
            const n = parseFloat(v);
            return (isFinite(n) && n > 0) ? n : 0;
        } catch (e) { return 0; }
    }

    /**
     * Enable auto-resume for a video: on init, seek to the last position
     * (if > RESUME_THRESHOLD_S); on every timeupdate, save the position
     * throttled to once per SAVE_THROTTLE_MS.
     *
     * Idempotent — calling twice for the same videoId doesn't double-save.
     * Returns an unsubscribe function.
     */
    function enableResume(videoId) {
        if (!videoId) return () => {};
        const resumeAt = loadPosition(videoId);
        let lastSaved = 0;
        // Resume on first ready (use the wrapper's onReady so we don't
        // race with the YouTube API handshake)
        const unsubReady = onReady(() => {
            const p = getInstance();
            if (p && resumeAt > RESUME_THRESHOLD_S) {
                try { p.seekTo(resumeAt); } catch (e) { /* ignore */ }
            }
        });
        // Save on timeupdate (throttled)
        const unsubTime = onTimeUpdate((t) => {
            const now = Date.now();
            if (now - lastSaved >= SAVE_THROTTLE_MS) {
                lastSaved = now;
                savePosition(videoId, t);
            }
        });
        // Also save on page hide (user closing tab / navigating away)
        const onHide = () => {
            const p = getInstance();
            if (p) savePosition(videoId, p.getCurrentTime());
        };
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') onHide();
        });
        window.addEventListener('beforeunload', onHide);
        return () => {
            unsubReady();
            unsubTime();
            window.removeEventListener('beforeunload', onHide);
        };
    }

    // ─── Exports ────────────────────────────────────────────────────────

    global.YTPlayer = {
        init,
        getInstance,
        onReady,
        onTimeUpdate,
        enableResume,
        // Exposed for tests
        _detectBackend: detectBackend,
        _loadYouTubeApi: loadYouTubeApi,
        _savePosition: savePosition,
        _loadPosition: loadPosition,
        _storageKey: storageKey,
        // 2026-09-06: one-shot diagnostic for exactly the class of
        // bug where "the video plays but the transcript doesn't
        // follow". Paste into DevTools:
        //   YTPlayer.debug()
        // → {initialized, kind, apiReady, loopRunning, subscribers,
        //    lastTime, ytState}
        debug: () => ({
            initialized: !!playerInstance,
            kind: playerInstance ? playerInstance.kind : null,
            apiScriptLoaded: apiScriptLoaded,
            apiReady: apiReady,
            loopRunning: rafId !== null,
            subscribers: timeUpdateHandlers.length,
            lastTime: playerInstance ? playerInstance.getCurrentTime() : null,
            ytState: (playerInstance && playerInstance._raw && typeof playerInstance._raw.getPlayerState === 'function')
                ? playerInstance._raw.getPlayerState()
                : null,
        }),
    };

})(window);

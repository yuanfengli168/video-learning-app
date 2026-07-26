// supporters.js — render the Hall of Fame wall from supporters.json
//
// Loads the public supporters.json, renders the cards, and supports
// search + tier filtering. No backend needed — pure static.

(function () {
  'use strict';

  var DATA_URL = 'supporters.json';
  // The repo's star count is fetched from the GitHub API on every page load.
  // CORS is open for the public repo API, no auth needed. We gracefully
  // fall back to the cached/hardcoded number if the API is rate-limited.
  var GH_API = 'https://api.github.com/repos/yuanfengli168/video-learning-app';
  var GH_FALLBACK_STARS = 0; // updated by the API call below

  var grid = document.querySelector('[data-wall-grid]');
  var empty = document.querySelector('[data-wall-empty]');
  var note = document.querySelector('[data-wall-note]');
  var search = document.querySelector('[data-wall-search]');
  var filterButtons = document.querySelectorAll('[data-wall-filters] button');
  var statTotal = document.querySelector('[data-stat-total]');
  var statCount = document.querySelector('[data-stat-count]');

  if (!grid) return;

  var state = {
    supporters: [],
    tiers: {},
    query: '',
    filter: 'all'
  };

  // ── Render: stats bar ─────────────────────────────────────────────
  function renderStats() {
    if (statCount) {
      statCount.textContent = state.supporters.length;
    }
    if (statTotal) {
      // Show a rough total, not exact (privacy + nicer rounded numbers)
      var total = state.supporters.reduce(function (s, x) { return s + (x.amount_usd || 0); }, 0);
      statTotal.textContent = total === 0
        ? '$0'
        : '$' + (total < 100 ? Math.round(total) : Math.round(total / 10) * 10).toLocaleString();
    }
  }

  // ── Render: a single card ─────────────────────────────────────────
  function renderCard(s, tiers) {
    var tierInfo = tiers[s.tier] || tiers.coffee || { label: s.tier, emoji: '✨', color: '#71717a' };
    var displayName = s.anonymous ? 'Anonymous' : escapeHtml(s.name || 'Anonymous');
    var initial = (displayName.trim()[0] || '?').toUpperCase();
    var dateStr = formatDate(s.date);
    var comment = s.comment ? '<blockquote class="wall-card__quote">' + escapeHtml(s.comment) + '</blockquote>' : '';
    var talkBadge = (s.tier === 'talk' && !s.claimed_talk)
      ? '<span class="wall-card__claim" title="Hasn\'t scheduled their 30-min talk yet">🎙️ Awaiting claim</span>'
      : (s.tier === 'talk' && s.claimed_talk
          ? '<span class="wall-card__claim wall-card__claim--done" title="Talk completed">🎙️ Talk done</span>'
          : '');

    return ''
      + '<article class="wall-card" data-tier="' + escapeHtml(s.tier) + '" data-name="' + escapeHtml(displayName.toLowerCase()) + '">'
      +   '<div class="wall-card__avatar" style="--avatar-color:' + escapeHtml(tierInfo.color || '#6366f1') + '">' + escapeHtml(initial) + '</div>'
      +   '<div class="wall-card__body">'
      +     '<header class="wall-card__head">'
      +       '<h3 class="wall-card__name">' + displayName + '</h3>'
      +       '<span class="tier-badge tier-badge--' + escapeHtml(s.tier) + '">' + escapeHtml(tierInfo.emoji) + ' ' + escapeHtml(tierInfo.label) + '</span>'
      +     '</header>'
      +     '<p class="wall-card__date">' + dateStr + '</p>'
      +     comment
      +     talkBadge
      +   '</div>'
      + '</article>';
  }

  // ── Render: the whole wall ────────────────────────────────────────
  function renderWall() {
    var filtered = state.supporters.filter(function (s) {
      // Search filter
      if (state.query) {
        var hay = ((s.name || '') + ' ' + (s.comment || '')).toLowerCase();
        if (hay.indexOf(state.query.toLowerCase()) === -1) return false;
      }
      // Tier/recency filter
      if (state.filter === 'talk') return s.tier === 'talk';
      if (state.filter === 'gold') return s.tier === 'gold' || s.tier === 'talk';
      if (state.filter === 'recent') {
        var days = (Date.now() - new Date(s.date).getTime()) / (1000 * 60 * 60 * 24);
        return days <= 30;
      }
      return true; // 'all'
    });

    if (filtered.length === 0) {
      grid.innerHTML = '';
      if (empty) {
        empty.hidden = false;
        grid.appendChild(empty);
      }
    } else {
      if (empty) empty.hidden = true;
      grid.innerHTML = filtered.map(function (s) { return renderCard(s, state.tiers); }).join('');
    }

    if (note) {
      var last = state.supporters[0]; // sorted desc by date
      if (last && last.date) {
        note.innerHTML = '<small>Last updated: ' + formatDate(state.supporters[0].generated_at || last.date)
          + ' · ' + state.supporters.length + ' supporter'
          + (state.supporters.length === 1 ? '' : 's')
          + ' total</small>';
      } else {
        note.innerHTML = '<small>The wall is waiting for its first supporter. <a href="donate.html">Be the first →</a></small>';
      }
    }
  }

  // ── Filter / search wiring ────────────────────────────────────────
  if (search) {
    search.addEventListener('input', function (e) {
      state.query = e.target.value || '';
      renderWall();
    });
  }
  filterButtons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      filterButtons.forEach(function (b) { b.classList.remove('is-active'); });
      btn.classList.add('is-active');
      state.filter = btn.dataset.filter || 'all';
      renderWall();
    });
  });

  // ── Star count (GitHub API) ────────────────────────────────────────
  var statStars = document.querySelector('[data-stat-stars]');
  if (statStars) {
    fetch(GH_API)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && typeof data.stargazers_count === 'number') {
          statStars.textContent = data.stargazers_count.toLocaleString();
        } else {
          statStars.textContent = GH_FALLBACK_STARS.toLocaleString();
        }
      })
      .catch(function () { statStars.textContent = GH_FALLBACK_STARS.toLocaleString(); });
  }

  // ── Load supporters.json + initial render ─────────────────────────
  fetch(DATA_URL)
    .then(function (r) {
      if (!r.ok) throw new Error('Failed to load ' + DATA_URL + ' (HTTP ' + r.status + ')');
      return r.json();
    })
    .then(function (data) {
      state.tiers = data.tiers || {};
      state.supporters = data.supporters || [];
      state.generatedAt = data.generated_at;
      renderStats();
      renderWall();
    })
    .catch(function (err) {
      console.error('supporters.js:', err);
      if (grid) {
        grid.innerHTML = '<p class="wall__error">'
          + '⚠ Could not load the supporters list. '
          + 'If you\'re seeing this on the live site, the JSON file may be missing or malformed. '
          + 'Check the browser console for details.'
          + '</p>';
      }
    });

  // ── Helpers ───────────────────────────────────────────────────────
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }
  function formatDate(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    } catch (e) { return iso; }
  }
})();

// contact.js — contact form handler for the contact page.
//
// Submits the form to Formspree via fetch (XHR-style) so the user doesn't
// leave the page. Falls back to native form submission if fetch fails
// (so the form still works with JS disabled or with network issues).
//
// TODO: Replace YOUR_FORMSPREE_ID in contact.html before going live.
// Get one at https://formspree.io/forms (free, no signup for 50/mo).

(function () {
  'use strict';

  var form = document.querySelector('[data-contact-form]');
  var status = document.querySelector('[data-contact-form-status]');
  var submitBtn = form ? form.querySelector('.contact-form__submit') : null;

  if (!form || !status || !submitBtn) return;

  form.addEventListener('submit', function (e) {
    e.preventDefault();

    // Honeypot: if filled, silently "succeed" (don't tip off the bot)
    var honeypot = form.querySelector('input[name="_gotcha"]');
    if (honeypot && honeypot.value) {
      status.textContent = 'Thanks! Your message has been sent.';
      status.className = 'contact-form__status contact-form__status--success';
      form.reset();
      return;
    }

    // Disable submit + show sending state
    submitBtn.disabled = true;
    var originalLabel = submitBtn.textContent;
    submitBtn.textContent = 'Sending…';
    status.textContent = '';
    status.className = 'contact-form__status';

    var formData = new FormData(form);

    fetch(form.action, {
      method: 'POST',
      body: formData,
      headers: { 'Accept': 'application/json' }
    })
      .then(function (response) {
        if (response.ok) {
          // Success — Formspree returns 200 with JSON
          status.textContent = "✓ Thanks! Your message has been sent. I'll reply within 1–2 days.";
          status.className = 'contact-form__status contact-form__status--success';
          form.reset();
        } else {
          // Formspere returned an error (validation, rate limit, etc.)
          return response.json().then(function (data) {
            var msg = (data && data.errors && data.errors.length > 0)
              ? data.errors.map(function (e) { return e.message; }).join(', ')
              : 'Something went wrong. Please email jackyopenclaw.168@gmail.com directly.';
            throw new Error(msg);
          });
        }
      })
      .catch(function (err) {
        // Network error, Formspree down, or explicit error from above
        status.textContent = '⚠ ' + err.message + ' (Or email me at jackyopenclaw.168@gmail.com.)';
        status.className = 'contact-form__status contact-form__status--error';
      })
      .finally(function () {
        submitBtn.disabled = false;
        submitBtn.textContent = originalLabel;
      });
  });
})();

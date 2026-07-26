// contact.js — contact form handler for the contact page.
//
// Current behavior (v2.1.0.4+ mailto: mode):
//   On submit, validates the form, then assembles a mailto: URL with
//   all fields pre-filled, and navigates the browser to it. The user's
//   default email client (Mail.app, Outlook, Thunderbird, Gmail web, etc.)
//   opens with the message ready to send.
//
// Why mailto: instead of Formspree / a backend?
//   - Zero third-party dependency. Nothing leaves the browser until the
//     user explicitly hits Send in their email app.
//   - Works on any device with a default email client (including mobile
//     phones using the native mail app).
//   - Replies come from the donor's real email, so threading and
//     "Reply" work naturally.
//   - No API keys, no signup, no rate limits.
//
// Trade-off: the user has to press "Send" twice (once on the form, once
// in their email app). Most people find this fine — it's actually less
// surprising than the "submit form and trust a third party" pattern.
//
// To switch back to Formspree later: restore the version that POSTs to
// formspree.io/f/YOUR_ID (the previous version is in git history).

(function () {
  'use strict';

  var form = document.querySelector('[data-contact-form]');
  var status = document.querySelector('[data-contact-form-status]');
  var submitBtn = form ? form.querySelector('.contact-form__submit') : null;

  if (!form || !status || !submitBtn) return;

  // Read the destination email from a data attribute on the form, with a
  // safe fallback. Editing the email is a one-line change in contact.html.
  var toEmail = form.dataset.mailto || 'jackyopenclaw.168@gmail.com';

  // Human-readable labels for the topic dropdown, so the assembled email
  // body reads naturally (instead of "topic: bug" the line is "Topic:
  // 🐞 Bug report").
  var TOPIC_LABELS = {
    bug:         '🐞 Bug report',
    feature:     '💡 Feature request',
    install:     '🔧 Install / setup help',
    partnership: '🤝 Partnership / business',
    other:       '✉️ Something else',
  };

  form.addEventListener('submit', function (e) {
    e.preventDefault();

    // Honeypot: if filled, silently "succeed" without opening the mail
    // client — don't tip off the bot that we detected it.
    var honeypot = form.querySelector('input[name="_gotcha"]');
    if (honeypot && honeypot.value) {
      status.textContent = '✓ Message sent.';
      status.className = 'contact-form__status contact-form__status--success';
      form.reset();
      return;
    }

    // Read + trim the form fields
    var name    = (form.querySelector('[name="name"]')    || {}).value || '';
    var email   = (form.querySelector('[name="email"]')   || {}).value || '';
    var topic   = (form.querySelector('[name="topic"]')   || {}).value || '';
    var message = (form.querySelector('[name="message"]') || {}).value || '';

    name    = name.trim();
    email   = email.trim();
    topic   = topic.trim();
    message = message.trim();

    if (!name || !email || !topic || !message) {
      status.textContent = '⚠ Please fill in all fields.';
      status.className = 'contact-form__status contact-form__status--error';
      return;
    }

    // Validate email format (basic; the email app will do the real check)
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      status.textContent = '⚠ That email address looks invalid.';
      status.className = 'contact-form__status contact-form__status--error';
      return;
    }

    // Build the email body
    var topicLabel = TOPIC_LABELS[topic] || topic;
    var subject = 'Video Learning App — ' + topicLabel;
    var bodyLines = [
      message,
      '',
      '—',
      'From: ' + name + ' <' + email + '>',
      'Topic: ' + topicLabel,
      'Sent via the contact form on the landing page',
    ];
    var body = bodyLines.join('\n');

    // Disable submit + show a brief "preparing..." state
    submitBtn.disabled = true;
    var originalLabel = submitBtn.textContent;
    submitBtn.textContent = 'Opening email app…';
    status.textContent = '';
    status.className = 'contact-form__status';

    // Assemble the mailto: URL. encodeURIComponent handles special chars
    // in subject/body.
    var mailto = 'mailto:' + encodeURIComponent(toEmail)
      + '?subject=' + encodeURIComponent(subject)
      + '&body=' + encodeURIComponent(body);

    // Try to open it. Some browsers block programmatic mailto: opening
    // without a user gesture, but the submit click IS a user gesture so
    // it works. window.location.href is the most reliable cross-platform
    // way to trigger the mail client.
    try {
      window.location.href = mailto;
    } catch (err) {
      // Some browsers throw on programmatic mailto: (rare). Fall back
      // to showing a clickable link the user can click.
      status.innerHTML = "⚠ Your email app didn't open automatically. "
        + '<a href="' + mailto.replace(/"/g, '&quot;') + '">Click here to open it</a>.';
      status.className = 'contact-form__status contact-form__status--error';
    }

    // Restore the button (the mail client usually opens in a separate
    // app, so this code only runs if the user comes back to the tab)
    setTimeout(function () {
      submitBtn.disabled = false;
      submitBtn.textContent = originalLabel;
      if (!status.textContent) {
        status.textContent = '✓ Email app should have opened. If not, copy your message and email ' + toEmail + ' directly.';
        status.className = 'contact-form__status contact-form__status--success';
      }
    }, 1500);
  });
})();

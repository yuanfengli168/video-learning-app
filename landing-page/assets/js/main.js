/* ==========================================================================
   Video Learning App — Landing Page JS
   ~3KB. No dependencies. Theme toggle, mobile menu, lightbox, reveal-on-scroll.
   ========================================================================== */
(function () {
  'use strict';

  // ---------- Theme toggle ----------
  const themeToggle = document.querySelector('[data-theme-toggle]');
  const root = document.documentElement;

  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const current = root.dataset.theme || 'light';
      const next = current === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      try { localStorage.setItem('vla-theme', next); } catch (e) {}
    });
  }

  // Sync across tabs
  window.addEventListener('storage', (e) => {
    if (e.key === 'vla-theme' && e.newValue) {
      root.dataset.theme = e.newValue;
    }
  });

  // ---------- Mobile menu ----------
  const menuToggle = document.querySelector('[data-menu-toggle]');
  const menu = document.querySelector('[data-menu]');
  if (menuToggle && menu) {
    menuToggle.addEventListener('click', () => {
      menuToggle.classList.toggle('is-open');
      menu.classList.toggle('is-open');
    });
    // Close menu when a link is clicked
    menu.querySelectorAll('a').forEach((a) => {
      a.addEventListener('click', () => {
        menuToggle.classList.remove('is-open');
        menu.classList.remove('is-open');
      });
    });
  }

  // ---------- Lightbox ----------
  const lightbox = document.querySelector('[data-lightbox-target]');
  const lightboxImg = lightbox ? lightbox.querySelector('.lightbox__img') : null;
  const lightboxCaption = lightbox ? lightbox.querySelector('.lightbox__caption') : null;
  const lightboxClose = lightbox ? lightbox.querySelector('[data-lightbox-close]') : null;

  function openLightbox(href, caption) {
    if (!lightbox || !lightboxImg) return;
    lightboxImg.src = href;
    lightboxImg.alt = caption || '';
    if (lightboxCaption) lightboxCaption.textContent = caption || '';
    lightbox.hidden = false;
    document.body.style.overflow = 'hidden';
  }

  function closeLightbox() {
    if (!lightbox) return;
    lightbox.hidden = true;
    lightboxImg.src = '';
    document.body.style.overflow = '';
  }

  document.querySelectorAll('[data-lightbox]').forEach((link) => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const caption = link.querySelector('.shot__caption')?.textContent.trim() || '';
      openLightbox(link.href, caption);
    });
  });

  if (lightboxClose) lightboxClose.addEventListener('click', closeLightbox);
  if (lightbox) {
    lightbox.addEventListener('click', (e) => {
      if (e.target === lightbox) closeLightbox();
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && lightbox && !lightbox.hidden) closeLightbox();
  });

  // ---------- Reveal on scroll (IntersectionObserver) ----------
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -50px 0px' });

    // Add reveal attribute to feature/shot/step elements
    document.querySelectorAll('.feature, .shot, .step, .proof__inner, .section-head').forEach((el) => {
      el.setAttribute('data-reveal', '');
      observer.observe(el);
    });
  }

  // ---------- Smooth scroll for in-page anchors (CSS does most, but compensate for sticky nav) ----------
  document.querySelectorAll('a[href^="#"]').forEach((a) => {
    a.addEventListener('click', (e) => {
      const id = a.getAttribute('href');
      if (id === '#' || id.length < 2) return;
      const target = document.querySelector(id);
      if (!target) return;
      e.preventDefault();
      const navHeight = document.querySelector('.nav')?.offsetHeight || 0;
      const y = target.getBoundingClientRect().top + window.pageYOffset - navHeight - 16;
      window.scrollTo({ top: y, behavior: 'smooth' });
    });
  });

  // ---------- Inject real images when they exist (screenshot placeholders) ----------
  document.querySelectorAll('.shot__img[data-img]').forEach((el) => {
    const filename = el.dataset.img;
    const img = new Image();
    img.alt = el.getAttribute('aria-label') || '';
    img.onload = () => {
      el.classList.add('shot__img--loaded');
    };
    img.onerror = () => {
      // Keep the placeholder if image is missing
    };
    img.src = 'assets/images/' + filename;
    el.appendChild(img);
  });

  // ---------- Stats counter (only if reduced motion is not preferred) ----------
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!prefersReducedMotion && 'IntersectionObserver' in window) {
    const stats = document.querySelectorAll('.stat__num');
    const statObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          animateCount(entry.target);
          statObserver.unobserve(entry.target);
        }
      });
    }, { threshold: 0.5 });
    stats.forEach((s) => statObserver.observe(s));
  }

  function animateCount(el) {
    const text = el.textContent.trim();
    const match = text.match(/^(\d+)(.*)$/);
    if (!match) return;
    const target = parseInt(match[1], 10);
    const suffix = match[2] || '';
    const duration = 1200;
    const start = performance.now();

    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      const value = Math.floor(target * eased);
      el.textContent = value + suffix;
      if (t < 1) requestAnimationFrame(tick);
      else el.textContent = target + suffix;
    }
    requestAnimationFrame(tick);
  }

})();

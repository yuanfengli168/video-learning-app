# Marketing & Launch

Everything you need to advocate for the Video Learning App, in one place.

## 📁 Contents

```
doc/marketing/
├── README.md                    # This file
├── social-captions.md           # 3 ready-to-post captions (Twitter, LinkedIn, RedNote)
├── video-demo-script.md         # Full voiceover script + shot list + cut-down versions
└── ../landing-page/             # Static site (deploy to GitHub Pages)
    ├── index.html               # Main landing page
    ├── README.md                # Setup + deploy guide
    ├── _config.yml              # GitHub Pages config
    ├── CNAME                    # Custom domain placeholder
    └── assets/
        ├── css/style.css        # Styles
        ├── js/main.js           # Theme toggle + lightbox
        ├── images/              # Drop screenshots here
        └── video/               # Drop demo video here
```

## 🚀 Quick Start

1. **Read the social captions** — `social-captions.md` has 3 ready-to-post
   versions (Twitter, LinkedIn, RedNote), each with posting strategy.

2. **Record the demo video** — `video-demo-script.md` has the full
   voiceover script, shot list, and 3 cut-down versions (1:30, 1:00, 0:15).

3. **Drop assets into the landing page** — see
   `landing-page/assets/images/README.md` and
   `landing-page/assets/video/README.md` for the capture list and specs.

4. **Deploy the site** — follow `landing-page/README.md`. Two options:
   - **Same repo, `gh-pages` branch** (simpler)
   - **Separate repo** (cleaner URL, recommended)

## ✅ Pre-Launch Checklist

- [ ] Replace `hello@example.com`, `yourhandle`, etc. in `landing-page/index.html`
- [ ] Add 7 screenshots to `landing-page/assets/images/`
- [ ] Record + add demo video to `landing-page/assets/video/`
- [ ] Test locally: `cd landing-page && python3 -m http.server 8000`
- [ ] Run Lighthouse in Chrome DevTools (target: 100/100/100/100)
- [ ] Deploy to GitHub Pages
- [ ] Update social captions with the live URL
- [ ] Post on Twitter, LinkedIn, RedNote (in that order — Twitter threads get the most reach)
- [ ] Pin the Twitter thread to your profile
- [ ] Add the live URL to the GitHub repo's "About" section + "Website" sidebar
- [ ] Add the live URL to your LinkedIn headline

## 💡 Tips for Maximum Reach

- **Post in a 24-hour burst** — Twitter first, then LinkedIn, then RedNote.
  Cross-link between them ("more on my blog/landing page").
- **Engage with every comment** for the first 24 hours — algorithms reward
  early engagement and will push your post to more feeds.
- **Don't just paste links** — the social posts above all include the
  value prop + screenshots + the call-to-action. Paste the URL at the
  END of the post, not the start.
- **Repurpose the screenshots** — each one is a standalone tweet/LinkedIn
  post ("Did you know you can click any line in a transcript to jump the
  video? Here's how I built it."). One launch = 10+ posts.

## 📊 Metrics to Track

After launch, watch these for the first 2 weeks:
- GitHub: stars, forks, unique visitors
- Twitter: thread impressions, link clicks, profile visits
- LinkedIn: post impressions, profile views, connection requests
- RedNote: 收藏 (saves), 评论 (comments), 点赞 (likes)
- Landing page: visitors, bounce rate, time on page, GitHub click-through

If any platform is 10x outperforming the others, double down there.

## 📧 Business Inquiries

Update the email in `landing-page/index.html` to a real business address
(e.g. `business@yourdomain.com`). Consider setting up a separate inbox
so personal email isn't exposed.

## 🤝 Get Help

If you need to iterate on the copy, design, or strategy, the `doc/marketing/`
folder is designed to be edited in-place. Each file is plain markdown —
no CMS, no special tooling needed.

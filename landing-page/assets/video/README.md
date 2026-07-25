# Demo Video

This folder holds the demo video embedded in the landing page.

## Quick start (3 options)

### Option A: Self-host MP4 (recommended for control)

1. Export your screen recording as `demo.mp4` (H.264, 1080p, ~5 Mbps bitrate, 2-3 min long)
2. Drop it in this folder
3. Open `../../index.html`, find the `<div class="demo__placeholder" ...>` block
4. Replace it with:
   ```html
   <video controls poster="../images/screenshot-mindmap.png" preload="metadata">
     <source src="assets/video/demo.mp4" type="video/mp4" />
     Your browser doesn't support embedded video.
     <a href="assets/video/demo.mp4">Download the demo</a> instead.
   </video>
   ```
5. Add this CSS to `assets/css/style.css`:
   ```css
   .demo__player video { width: 100%; height: auto; display: block; aspect-ratio: 16/9; background: #000; }
   ```

### Option B: YouTube embed (recommended for reach)

1. Upload your video to YouTube (unlisted is fine)
2. Get the video ID from the URL: `youtube.com/watch?v=XXXXXXXXXXX`
3. Replace the placeholder in `index.html` with:
   ```html
   <div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;">
     <iframe
       src="https://www.youtube.com/embed/XXXXXXXXXXX?rel=0"
       style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;"
       title="Video Learning App demo"
       allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
       allowfullscreen></iframe>
   </div>
   ```

### Option C: Both (best UX)

Use Option A as the default with a "Watch on YouTube" link as a fallback for users on metered connections.

## Recommended video specs

| Spec | Value |
|---|---|
| Resolution | 1920×1080 (1080p) |
| Aspect ratio | 16:9 |
| Codec | H.264 (universal) or H.265 (smaller) |
| Bitrate | 5-8 Mbps (good balance) |
| Audio | AAC, 128-192 kbps |
| Length | 2-3 minutes (sweet spot for social) |
| File size | Aim for under 50 MB |

## Script + shot list

See [`../../../doc/marketing/video-demo-script.md`](../../../doc/marketing/video-demo-script.md)
for the full voiceover script, shot list, and cut-down versions for each platform.

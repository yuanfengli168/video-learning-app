# Social Media Captions

> **Last updated:** 2026-07-21
> **Product:** Video Learning App (open-source, AI-powered, local-first)
> **Repo:** https://github.com/yuanfengli168/video-learning-app
> **Status:** MVP2.1.0.3 — 633 tests passing, 92% coverage, production-ready

---

## 1. X / Twitter (English, ~280 chars)

**Tone:** punchy, technical credibility, ship-it energy

```
I built a local-first AI learning app that turns any video lecture into:

→ Searchable transcript
→ Mindmap you can click to jump
→ Quiz + flashcards
→ Chat with the whole video as context
→ Auto-generated topic timestamps

Whisper + Ollama, MIT-style license, 633 tests. Self-host it in 5 min.

github.com/yuanfengli168/video-learning-app
```

**Alt version (developer focus):**

```
After 13 versions and 633 tests, my open-source AI learning app is ready for the world.

What it does in 1 line: drop in a lecture video → get a transcript, mindmap, quiz, flashcards, and a ChatGPT-style chat that knows the whole video.

100% local. Whisper + Ollama. No cloud lock-in.

github.com/yuanfengli168/video-learning-app
```

**Alt version (educator focus):**

```
I built this for anyone who watches 2-hour lectures and forgets 90% by Friday.

→ Auto-generated mindmap you can click
→ Quiz that tests what you actually remember
→ Chat that answers questions about the video
→ Transcript with timestamped search

Open source. Runs on your laptop. Free forever.

github.com/yuanfengli168/video-learning-app
```

---

## 2. LinkedIn (English, ~300 words, professional)

**Tone:** founder narrative, technical depth, credibility signals, business-friendly

---

**Headline:** I open-sourced my AI learning app after 13 versions and 633 tests — here's what I learned.

**Body:**

Three months ago I was drowning in 2-hour lecture videos. I'd watch them, take notes, and forget 80% by the next week. So I built a tool to fix that.

Today I'm releasing the 13th version of **Video Learning App** — an open-source, AI-powered study companion that turns any video into a complete learning kit:

- **Searchable transcript** with click-to-seek video integration
- **Interactive mindmap** where every node is a clickable timestamp
- **Auto-generated quiz + flashcards** so you actually test recall (not just re-read)
- **A ChatGPT-style chat** that has the full video as context — ask "explain the part about backprop at 23:15" and it answers
- **Topic timestamps** so you can jump back to the exact 3-minute segment you need to review

**The technical bits (for the devs):**
- Backend: FastAPI + Python 3.14, SQLAlchemy 2.0, SQLite
- AI: Faster-Whisper (local transcription) + Ollama (local LLM)
- Frontend: Jinja2 + vanilla JS + Tailwind, dark/light theme
- **633 tests passing, 92% backend coverage, 0 regressions**
- Background worker pool for batch uploads, plugin system for tools (currently: WebM→MP4 conversion)

**The philosophy:**
- Local-first. Your videos never leave your machine.
- No subscriptions. No rate limits. No "upgrade to Pro."
- Open source (Apache 2.0). Fork it, self-host it, hack on it.
- Works on a stock M1 MacBook with 16 GB RAM.

I built this because I wanted to learn — and I figured other people might want the same tool. If you've ever felt like online lectures are a slog, give it a try.

🔗 **GitHub:** https://github.com/yuanfengli168/video-learning-app
📖 **Docs:** [link to docs site when ready]

Would love your feedback — especially from educators, students, and self-learners. What features would make this 10x more useful for you?

#OpenSource #AI #EdTech #LearningTools #BuildInPublic #Whisper #Ollama #FastAPI

---

## 3. RedNote / 小红书 (Chinese, ~300 words)

**Tone:** 真诚、实用、有点小骄傲、像朋友安利

---

## 📚 我开源了一个AI学习APP，把任何网课视频变成一套"会动的笔记"

**—— 13个版本，633个测试，92%覆盖率，全部跑在你自己的电脑上**

---

**痛点：**

你有没有这种感觉——看了2小时的网课，做了满满一页笔记，结果一周后只记得20%？

我以前每周都有这个困扰。所以我自己写了一个工具来解决。

---

**它能做什么：**

🎬 **上传任何视频**（mp4/mov/webm，最大10GB）

📝 **自动生成逐字稿**，带时间戳，点击就能跳到视频对应位置

🧠 **可点击的思维导图**——每个节点都是一个时间点，点一下直接跳转

📇 **自动生成闪卡和测验题**——不是只让你重读笔记，是真的测试你记不记得

💬 **跟整个视频对话**——AI看过完整转录、摘要、思维导图、测验题，你问"23:15讲的那个反向传播是怎么回事"，它能直接回答

⏱️ **自动生成主题时间戳**——复习时直接跳到重点

---

**技术栈（给开发者看的）：**

- 后端：FastAPI + Python 3.14
- AI：本地 Whisper 转录 + Ollama 本地大模型（数据不出你的电脑）
- 数据库：SQLite
- 633个测试全过，0个回归
- 黑暗/明亮主题，桌面/手机自适应

---

**为什么开源？**

1. 你的视频是你自己的，不应该上传到别人的服务器
2. 教育工具不应该有订阅墙
3. 我自己要用，所以会一直维护

---

**适用人群：**

- 在MOOC/Coursera/B站看课的学生
- 自学编程/AI/任何领域的朋友
- 想给孩子录网课、但没时间做笔记的家长/老师

---

**🔗 GitHub:** https://github.com/yuanfengli168/video-learning-app

**⏱️ 部署时间：** 5分钟（README有详细步骤）

如果你用了觉得不错，欢迎告诉我你最常用的功能是什么～

---

#AI学习工具 #开源 #自我提升 #网课 #学习APP #程序员 #Python #Whisper

---

## Posting Strategy

### Twitter / X
- **Best time:** Tue-Thu, 9-11am or 1-3pm your local time
- **First tweet** should be the punchy 1-liner. Pin it.
- **Follow up** with a 5-tweet thread showing: (1) the upload flow, (2) the mindmap, (3) the chat, (4) the quiz, (5) the install command
- **Tag:** @ollama, @fastapi, @ggerganov (Whisper creator), #buildinpublic

### LinkedIn
- **Best time:** Tue-Thu, 7-9am
- **Add a 1-minute screen recording as the first comment** (boosts reach 3-5x)
- **Ask a question at the end** (algorithm rewards comments)
- **Tag 3-5 people** who'd genuinely find it useful (don't spam)

### RedNote / 小红书
- **Best time:** 晚8-10点 (8-10pm, peak browsing)
- **Add 3-5张截图** at the end (上传页/思维导图/对话/测验) — 算法偏爱图文并茂
- **标题党但要真诚** — 数字（"633个测试"）和痛点（"看完就忘"）都打出来
- **加 5-8 个相关话题标签** at the very end
- **回复每一条前20条评论** — 平台会推得更猛
- **不要放外链** 在正文中（小红书会限流），放评论区第一条

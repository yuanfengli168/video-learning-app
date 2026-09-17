# Video Learning App — Self-Hosting Buyer's Guide

> **Private planning document — NOT for git.**
> Generated 2026-08-10 for the user's self-hosting decision.

---

## 1. Target & Constraints

| Item | Value |
|---|---|
| Target users | 1,000 paid users in Singapore |
| Profit goal | ≥ S$2/user/month |
| Primary app | Video Learning App (FastAPI + SQLite + Whisper + Ollama) |
| Storage model | Transcripts + generated materials + 14B fine-tuned LLM (local) |
| Chat model | `glm-5.2:cloud` (cloud-routed via Ollama) |
| Whisper | On-prem (MLX on Apple Silicon) |
| OCR | macOS Vision → Ollama llava:13b → Tesseract (chain) |
| Bandwidth | 10 Gbps home fibre (SG) |
| Apple Developer fee | S$132/yr (required for iOS Pocket push) |

---

## 2. The Mac Decision — Full Comparison

### 2.1 Used Mac pricing (Aug 2026)

> **All used prices include estimated annual electricity cost at 30% load (idle 70% / load 30% blended).** SP Group tariff ~S$0.32/kWh (incl. GST).
> Formula: `annual_S$ = idle_W × 0.7 × 24 × 365 × 0.32 / 1000`

| Model | CN Used (RMB) | SG Used (SGD) | Idle W | **Annual S$** | Year |
|---|---|---|---|---|---|
| Mac mini M1 8/256 | 1,800–2,500 | S$380–530 | 8 | **S$15** | 2020 |
| Mac mini M1 16/256 | 2,400–3,300 | S$500–700 | 8 | **S$15** | 2020 |
| Mac mini M2 8/256 | 2,600–3,500 | S$530–720 | 8 | **S$15** | 2023 |
| Mac mini M2 16/256 | 3,500–4,700 | S$720–950 | 8 | **S$15** | 2023 |
| Mac mini M2 Pro 16/512 | 6,500–8,000 | S$1,300–1,700 | 10 | **S$19** | 2023 |
| Mac Studio M1 Max 32/512 | 7,800–10,500 | **S$1,500–2,000** | 30 | **S$58** | 2022 |
| Mac Studio M1 Max 64/1TB | 11,500–15,000 | S$2,200–2,900 | 30 | **S$58** | 2022 |
| Mac Studio M1 Ultra 64/1TB | 13,500–18,000 | S$2,600–3,500 | 35 | **S$68** | 2022 |
| Mac Studio M2 Max 32/512 | 10,500–13,500 | **S$2,050–2,700** | 25 | **S$48** | 2023 |
| Mac Studio M2 Max 64/1TB | 14,500–18,000 | S$2,800–3,500 | 25 | **S$48** | 2023 |
| Mac Studio M2 Ultra 64/1TB | 14,000–19,000 | S$2,700–3,700 | 25 | **S$48** | 2023 |
| Mac Studio M2 Ultra 128/2TB | 24,000–30,000 | S$4,800–6,000 | 25 | **S$48** | 2023 |

### 2.2 New SG pricing (Apple SG retail, GST included)

> **All new prices include estimated annual electricity cost at 30% load (idle 70% / load 30% blended).** SP Group tariff ~S$0.32/kWh (incl. GST).
> Formula: `annual_S$ = idle_W × 0.7 × 24 × 365 × 0.32 / 1000`

| Model | SG NEW (SGD) | USD | Idle W | **Annual S$** |
|---|---|---|---|---|
| Mac mini M4 16/256 | S$1,149 | $799 | 10 | **S$19** |
| Mac mini M4 16/256 (10GbE) | S$1,249 | — | 10 | **S$19** |
| Mac mini M4 16/512 | S$1,399 | — | 10 | **S$19** |
| Mac mini M4 16/512 (10GbE) | S$1,499 | — | 10 | **S$19** |
| Mac mini M4 24/256 (10GbE) | S$1,599 | — | 10 | **S$19** |
| Mac mini M4 Pro 24/512 | S$2,399 | — | 12 | **S$23** |
| Mac mini M4 Pro 24/512 (10GbE) | S$2,499 | — | 12 | **S$23** |
| Mac mini M4 Pro 48/512 (10GbE) | S$2,999 | — | 12 | **S$23** |
| Mac mini M4 Pro 64/512 (10GbE) | S$3,499 | — | 12 | **S$23** |
| Mac Studio M4 Max 36/512 | S$3,499 | $2,499 | 20 | **S$38** |
| Mac Studio M4 Max 64/1TB | S$4,029 | $3,099 | 20 | **S$38** |
| Mac Studio M4 Max 96/1TB | S$4,789 | $3,699 | 20 | **S$38** |
| Mac Studio M4 Max 128/2TB | S$6,109 | $4,699 | 20 | **S$38** |
| Mac Studio M3 Ultra 64/512 | S$7,799 | $5,299 | 35 | **S$67** |
| Mac Studio M3 Ultra 128/2TB | S$8,189 | — | 35 | **S$67** |
| Mac Studio M3 Ultra 192/4TB | S$9,489 | — | 35 | **S$67** |
| Mac Studio M3 Ultra 256/4TB | S$10,529 | — | 35 | **S$67** |
| Mac Studio M3 Ultra 256/8TB | S$12,089 | — | 35 | **S$67** |
| Mac Studio M3 Ultra 256/16TB | S$15,209 | $11,699 | 35 | **S$67** |

### 2.2.1 Mac mini M4 vs M4 Pro — quick comparison

| Spec | Mac mini M4 (10-core) | Mac mini M4 Pro (12-core) |
|---|---|---|
| CPU total cores | 10 (4P + 6E) | 12 (6P + 6E) |
| **Performance cores** | **4** | **6** |
| GPU cores | 10 | 16 |
| Neural Engine | 16-core | 16-core |
| Memory bandwidth | 100 GB/s | 200 GB/s |
| Memory options | 16 / 24 GB | 24 / 48 / 64 GB |
| 10GbE option | ✅ (factory BTO +S$100) | ✅ (factory BTO +S$100) |
| Idle power | 10W | 12W |
| Annual electricity | ~S$17 | ~S$20 |
| macOS support until | ~2030-2031 | ~2030-2031 |
| **Max concurrent whisper (CPU-bound)** | **1** (4 cores ÷ 4) | **1** (6 cores ÷ 4 = 1.5 → floor) |
| Runs 14B Q4 LLM + 1 whisper | ⚠️ tight (24GB just fits) | ⚠️ tight (24GB just fits) |
| Runs 14B Q4 + 2 whisper + OCR | ❌ swap | ❌ swap |
| 14B LLM concurrency (Ollama instances) | 1 | 1 |
| **SG NEW price (24GB, 10GbE)** | **S$1,599** | **S$2,499** |
| Difference vs base (no 10GbE, 24GB) | +S$100 | +S$100 |

**Key insight:** M4 Pro has 2 more performance cores, but still only **1 concurrent whisper** because 6 cores ÷ 4 cores per job = 1.5 → floors to 1. The extra GPU cores and memory bandwidth don't help transcribe concurrency.

For 1000 users: **neither Mac mini is sufficient** because:
- Only 1 concurrent whisper (too slow for queue management)
- 24 GB tight for 14B LLM at scale
- Missing 64 GB option for future-proofing

**Mac mini is viable for:**
- Validation phase (first 100-200 users)
- Personal use + small server combo
- Budget-conscious first purchase (~S$1,254 with M4 mini 16/256)

**Mac mini is NOT viable for:**
- Production 1000-user deployment
- Concurrent local chat (need 64GB+)
- Multiple concurrent whisper (need 4+ cores dedicated, like M2/M3 Ultra)

### 2.3 Net cost after trade-in (your old Mac values)

| Model | Listed | Trade-in | Net cost |
|---|---|---|---|
| Mac mini M4 16/256 (NEW) | S$1,149 | -S$345 | **S$804** |
| Mac mini M4 Pro 24/512 (NEW) | S$2,399 | -S$345 | **S$2,054** |
| Mac Studio M1 Max 32/512 (USED) | S$1,774 | -S$680 | **S$1,094** |
| Mac Studio M1 Max 64/1TB (USED) | S$2,600 | -S$800 | **S$1,800** |
| Mac Studio M2 Max 32/512 (USED) | S$2,331 | -S$1,030 | **S$1,301** |
| Mac Studio M2 Max 64/1TB (USED) | S$3,300 | -S$1,000 | **S$2,300** |
| Mac Studio M2 Ultra 64/1TB (USED) | S$3,200 | -S$1,100 | **S$2,100** |
| Mac Studio M4 Max 36/512 (NEW) | S$3,499 | -S$1,400 | **S$2,099** |
| Mac Studio M4 Max 64/1TB (NEW) | S$4,029 | -S$1,400 | **S$2,629** |
| Mac Studio M3 Ultra 64/1TB (NEW) | S$7,149 | -S$2,200 | **S$4,949** |

---

## 3. RAM Math for Your 14B LLM

### Component RAM usage

| Component | RAM |
|---|---|
| macOS + FastAPI + SQLite | ~2 GB |
| Ollama + glm-5.2:cloud proxy | ~0.5 GB |
| **14B LLM Q4_K_M (fine-tuned)** | **~9 GB** |
| llava:13b OCR model | ~8 GB |
| Whisper large-v3 (each job) | ~3 GB |
| Swift OCR (macOS Vision) | ~0.5 GB |

### Workload × RAM table

| Workload | RAM needed | 24GB | 32GB | 36GB | 64GB |
|---|---|---|---|---|---|
| 14B LLM alone | 9 GB | ✅ | ✅ | ✅ | ✅ |
| 14B + 1 whisper | 12 GB | ✅ | ✅ | ✅ | ✅ |
| 14B + 2 whisper | 15 GB | ✅ tight | ✅ | ✅ | ✅ |
| 14B + 3 whisper | 18 GB | ❌ | ✅ | ✅ | ✅ |
| 14B + 2 whisper + llava OCR | 23 GB | ❌ | ⚠️ | ⚠️ | ✅ |
| 14B + 3 whisper + OCR | 26 GB | ❌ | ❌ | ❌ | ✅ |
| 14B + 4 whisper + OCR | 29 GB | ❌ | ❌ | ❌ | ✅ |
| **3 Ollama instances (3 concurrent local chats)** | 27 GB | ❌ | ❌ | ⚠️ | ✅ |
| 22B LLM + 2 whisper | 21 GB | ⚠️ | ✅ | ✅ | ✅ |
| 30B LLM + 1 whisper | 23 GB | ❌ | ✅ | ✅ | ✅ |

---

## 4. Concurrency Capacity

### 4.1 Realistic concurrency at 1000 users

| Activity | % of users | Concurrent |
|---|---|---|
| Uploading video | 5% | 50 concurrent |
| Actively chatting | 3% | 30 concurrent |
| Triggering OCR on PDF | 0.5% | 5 concurrent |
| Triggering new transcribe | 1% | 10 concurrent |

### 4.2 Per-machine capacity

> **Whisper concurrency is CPU-bound**, not RAM-bound. Each MLX whisper-large-v3 job uses ~4 performance cores. So `max_concurrent_whisper = perf_cores // 4`.
> **Chat concurrency is Ollama-bound.** A single Ollama process serves sequentially. To run multiple, you must launch multiple Ollama instances on different ports (requires RAM: ~9 GB per 14B instance).
> **Cloud chat (`glm-5.2:cloud`) is unlimited** — Ollama proxies to the cloud provider who scales for you.

| Operation | M1 Max 32 | M1 Max 64 | M2 Max 32 | M2 Max 64 | M4 mini 16/256 | M4 mini 24/256 | M4 Pro 24/512 | M4 Max 36 | M4 Max 64 | M2 Ultra 64 | M3 Ultra 64 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Perf cores** | 8 | 8 | 8 | 8 | 4 | 4 | 6 | 10 | 10 | **16** | **24** |
| Idle power (W) | 30 | 30 | 25 | 25 | 10 | 10 | 12 | 20 | 20 | 25 | 35 |
| Annual electricity (S$) | S$50 | S$50 | S$42 | S$42 | S$17 | S$17 | S$20 | S$34 | S$34 | S$42 | S$58 |
| 10GbE built-in | ✅ | ✅ | ❌¹ | ❌¹ | ✅ (10GbE SKU) | ✅ (10GbE SKU) | ✅ (10GbE SKU) | ✅ | ✅ | ✅ | ✅ |
| Concurrent file uploads | 10+ | 10+ | 10+ | 10+ | 10+ | 10+ | 10+ | 10+ | 10+ | 10+ | 10+ |
| **Concurrent whisper transcribes (CPU-bound)** | **2** | **2** | **2** | **2** | **1** | **1** | **1** | **2–3** | **2–3** | **4** | **6** |
| Concurrent chat (LOCAL 14B) | 1 | 3 | 1 | 3 | 1 | 1 | 1 | 1 | 3 | 2–3 | 4 |
| Concurrent chat (CLOUD glm-5.2) | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ |
| Concurrent llava:13b OCR | 0–1 | 1 | 0–1 | 1 | ❌ swap | ⚠️ tight | ❌ swap | 1 | 1 | 1 | 1 |
| FastAPI requests | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ | 1000+ |
| SQLite writes | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |

¹ M2 Mac mini was not offered with 10GbE factory BTO; later M4 mini SKUs and Pro/Studio lines have it.

**Key insight:** The "4 fit" for M2 Ultra is **CPU-bound**, not RAM-bound. M2 Ultra has 16 perf cores, MLX whisper-large-v3 uses ~4 cores per job → 16/4 = 4 jobs max.

---

## 5. AppleCare + Warranty (China → Singapore)

### Apple's regional warranty policy

- Apple's 1-year warranty is **regional** (China purchase = China warranty)
- Apple Stores in SG **often** honor international warranties on goodwill basis (~50-70% success)
- AppleCare+ is **globally transferable** if active
- Most used Mac Studios you find in China = warranty already expired

### What to verify when buying used from China

1. Serial number check at https://checkcoverage.apple.com
2. AppleCare+ status screenshot
3. `smartctl -a /dev/disk0` SSD health output (Percentage Used <10%)
4. Original purchase invoice
5. Video call demo (turn on, see Activity Monitor)
6. macOS version (should be 14+)

---

## 6. Shipping from China to Singapore

| Method | Risk | Cost | Time |
|---|---|---|---|
| Carry-on in original Apple box + hard shell case | Low (5% damage) | S$0 + GST 9% | Your flight |
| Checked luggage | High (20% damage) | S$0 + GST 9% | Your flight |
| DHL/FedEx international | Medium (10% damage) | S$200-400 + GST 9% | 7-14 days |

### Singapore Customs (GST)

- Items >S$400: 9% GST applies (declare at red channel)
- Without invoice: Customs estimates value (often higher)
- Cannot avoid GST by declaring as "personal gift"
- **M1 Max 32/512: +S$160 GST**
- **M2 Max 32/512: +S$210 GST**
- **M2 Max 64/1TB: +S$300 GST**

---

## 7. Storage Options

### 7.1 Acasis enclosures

| Model | Drive type | Bays | Speed | Use case | SG price |
|---|---|---|---|---|---|
| EC-7355 | 3.5" SATA HDD | 5 | 150 MB/s | Bulk backup, archive | S$200-280 |
| EC-H004 | 3.5" SATA HDD | 4 | 150 MB/s | Minimum RAID-5 backup | S$180-230 |
| EC-H006 | HDD+SSD mix | 6 | 150/500 MB/s | Mixed workload | S$280-350 |
| TBU405 | M.2 NVMe | 1 | 3 GB/s | Single fast drive | S$150-200 |
| TBU405 Pro | M.2 NVMe | 4 | 3 GB/s | Hot/fast storage RAID | S$300-400 |
| TBU9 | M.2 NVMe | 2 | 2 GB/s | 2-drive mirror | S$200-280 |

### 7.2 UGREEN (cheaper alternative)

| Model | Price | Speed |
|---|---|---|
| UGREEN CM400 2-bay NVMe RAID | S$80 | ~1.5 GB/s |
| UGREEN 40Gbps NVMe USB4 | S$160 | ~3 GB/s |
| Blueendless 2-bay RAID NVMe | S$80 | ~1 GB/s |

### 7.3 RAID levels explained

- **RAID 0**: stripe = max speed, max capacity, 0 redundancy (1 drive dies = all data lost)
- **RAID 1**: mirror = 2 drives, half capacity, 1 drive can die safely
- **RAID 5**: stripe + parity = N-1 capacity, 1 drive can die safely, needs ≥3 drives
- **RAID 10**: mirror of stripes = fast + redundant, half capacity, needs ≥4

**For your use case:** Use UGREEN CM400 with 2× 4TB NVMe in RAID-1 (mirror). Backup target only, not primary.

---

## 8. UPS (Uninterruptible Power Supply)

### What is a UPS?

A battery backup between your wall socket and Mac. When power fails (common in SG during thunderstorms), keeps Mac running for 5-15 minutes for graceful shutdown.

### APC Back-UPS BX600U-SG

- 600VA × 0.6 power factor = ~360W usable
- Mac mini under load = ~70W → 10-15 min runtime
- APC Back-UPS 600VA SG: **S$120-180**
- Protects against: power cuts, surges, brownouts

**Without UPS: sudden power loss = corrupted SQLite DB = data loss.**

---

## 9. Recommended Final BOMs

### Option A — "Cheapest validation" (S$1,254 landed)

- Mac Studio M1 Max 32/512 USED from China (S$1,094 + S$160 GST)
- UGREEN CM400 2-bay NVMe enclosure (S$80)
- 2× 4TB WD SN850X NVMe (S$700)
- APC BX600U-SG UPS (S$150)
- **Total: ~S$2,184**

### Option B — "Best new with warranty" (S$2,099 net)

- Mac Studio M4 Max 36/512 NEW in SG (S$2,099 after trade-in)
- 1TB internal SSD (already included)
- UGREEN CM400 + 2× 4TB NVMe (S$780)
- APC BX600U-SG UPS (S$150)
- **Total: ~S$3,029**

### Option C — "Best with 64GB" (S$2,629 net) ⭐ RECOMMENDED

- Mac Studio M4 Max 64/1TB NEW in SG (S$2,629 after trade-in)
- 1TB internal SSD (already included)
- UGREEN CM400 + 2× 4TB NVMe (S$780)
- APC BX600U-SG UPS (S$150)
- **Total: ~S$3,559**

---

## 10. Realistic User Growth Forecast

### Scenarios (Singapore market)

| Scenario | 6 months | 12 months | What's needed |
|---|---|---|---|
| X — Organic only (no marketing) | 10-30 users | **100-150 users** | Word-of-mouth only |
| Y — Mild marketing | 50-150 users | **500-800 users** | 5-10 hrs/wk content + community |
| Z — Viral hit | 300 users | **2,000+ users** | One viral post + retention work |

### Honest forecast for this user

**Most likely: 100-300 paying users by month 12.**

Why I'm not bullish on 1000:
1. Ollama bottleneck (single-instance, sequential chat)
2. No marketing budget mentioned
3. Free competition (Otter.ai, Notion AI, etc.)
4. Singapore is small, mature market
5. Even 1% of uni students = 900 users, requires serious go-to-market

### What would move you from X to Y/Z

| Lever | Effort | Impact |
|---|---|---|
| "Transcribe from YouTube URL" feature | 1 week | 10× more accessible (huge) |
| Telegram bot demo | 2 days | Low-funnel viral acquisition |
| NUS/NTU student union partnership | 1 week outreach | 100-500 signups |
| Annual plan (S$80/yr, 17% off) | 1 day | 30% retention improvement |

---

## 11. Year-1 Total Cost of Ownership (Path C — recommended)

| Item | S$ |
|---|---|
| Mac Studio M4 Max 64/1TB NEW (after trade-in) | 2,629 |
| UGREEN CM400 + 2× 4TB NVMe | 780 |
| APC BX600U-SG UPS | 150 |
| **Capex total** | **S$3,559** |
| | |
| 10 Gbps fibre (S$109/mo × 12) | 1,308 |
| Electricity (S$20/yr) | 20 |
| Apple Developer fee | 132 |
| Domain (S$18/yr) + Backblaze B2 (~S$1/yr) | 19 |
| **Opex year 1** | **S$1,479** |
| **YEAR 1 TOTAL** | **S$5,038** |

### Monthly opex breakdown

| Item | S$/mo |
|---|---|
| 10 Gbps fibre | S$89-129 (avg S$109) |
| Electricity (Mac Studio) | S$1.70 |
| Apple Developer (÷12) | S$11 |
| Domain (÷12) | S$1.50 |
| Backblaze B2 | S$0.10 |
| **Total opex** | **S$123/mo** |

---

## 12. Profit Math (at 1000 users)

### Pricing scenarios

| Pricing | Monthly revenue (1000 users) | Y1 revenue | Y1 cost | Y1 profit |
|---|---|---|---|---|
| S$2/user | S$2,000 | S$15,000* | S$5,038 | S$9,962 |
| S$5/user | S$5,000 | S$37,500* | S$5,038 | S$32,462 |
| S$8/user | S$8,000 | S$60,000* | S$5,038 | S$54,962 |

*Uses S-curve growth: 10, 25, 50, 90, 140, 210, 310, 440, 600, 760, 900, 1000 users

### Cloud LLM cost warning

If using cloud LLM for 1000 users:
- ~10 chats/user/day × 1000 users = 10,000 chats/day
- ~1500 tokens/chat = 15M tokens/day
- At $3/M input + $15/M output = **$150/day = S$4,500/month**
- This can exceed your revenue at low price points

**Mitigation:**
- Use local 14B for premium subscribers only
- Use cloud glm-5.2:cloud for free tier
- Cap daily chat per user (e.g., 10 chats/day)

---

## 13. The Ollama Bottleneck — Critical Insight

A single Ollama instance serves requests **sequentially**, not in parallel.

| RAM | Max concurrent local 14B chats | What you need to know |
|---|---|---|
| 32GB | 1 | All other chats must use cloud |
| 36GB | 1 | Same |
| 64GB | 3 (multiple instances) | Useful if you want local-only privacy |
| 128GB | 6-8 | Only for very heavy local usage |

**For 1000 users with glm-5.2:cloud:** 32GB is fine because cloud handles scaling.

To run multiple Ollama instances on 64GB:
```bash
ollama serve --model mymodel:14b --port 11434
ollama serve --model mymodel:14b --port 11435
ollama serve --model mymodel:14b --port 11436
```

---

## 14. CPU Cores — The Hidden Bottleneck (DETAILED)

For whisper transcoding, **CPU cores limit concurrency, not RAM**.

Each `mlx-whisper large-v3` job uses ~4 performance cores (Apple Silicon MLX uses ANE + Metal GPU + CPU cores combined, but the CPU perf cores are still the gating factor for parallelism).

### The math (perf cores ÷ 4 = max concurrent whisper)

| Mac | Perf cores | Max concurrent whisper |
|---|---|---|
| **M4 mini (10-core)** | **4 perf cores** | **1** |
| **M4 mini (10-core, 24GB)** | **4 perf cores** | **1** |
| **M4 Pro mini (12-core)** | **6 perf cores** | **1** (6/4 = 1.5) |
| M4 Max (14-core) | 10 perf cores | 2 (10/4 = 2.5 → floors to 2) |
| M2 Max (12-core) | 8 perf cores | 2 |
| M1 Max (10-core) | 8 perf cores | 2 |
| **M2 Ultra (24-core)** | **16 perf cores** | **4** ✅ |
| **M3 Ultra (32-core)** | **24 perf cores** | **6** ✅ |
| M4 Ultra (40-core, rumored 2027-28) | 32+ perf cores | 8+ |

### Why "4 fit" specifically for M2 Ultra

- M2 Ultra = **16 perf cores**
- whisper-large-v3 MLX uses **~4 cores per job**
- 16 ÷ 4 = **4 concurrent jobs**
- This is CPU-bound, NOT RAM-bound
- Even if you had 64GB or 128GB RAM, M2 Ultra still maxes at 4 whisper jobs

### Practical implications

- **64GB RAM does NOT help transcribe concurrency.** The bottleneck is perf cores.
- **More cores = more concurrent whisper.** This is why M2 Ultra and M3 Ultra exist.
- **For 1000 users with 3% concurrent transcribes = 30 concurrent requests**, even M3 Ultra's 6 concurrent max means users queue 5 deep on average.
- **The queue is fine** as long as wait times stay under 5-10 minutes.

### What if you need more concurrent whisper?

| Option | Max concurrent whisper | Cost |
|---|---|---|
| M4 Max (10 perf cores) | 2 | S$2,099 net |
| M2 Ultra (16 perf cores) | 4 | S$2,100 net USED |
| M3 Ultra (24 perf cores) | 6 | S$4,949 net NEW |
| Two M4 Max machines (load-balanced) | 4 | S$4,198 (2× S$2,099) |
| Three M4 Max machines (load-balanced) | 6 | S$6,297 (3× S$2,099) |

**For 1000 users, 2 concurrent whisper on M4 Max is sufficient.** Queue wait = ~5 min average during peak. Most users won't notice.

---

## 15. Decision Framework

### Choose 32GB/36GB if:
- Chat goes through glm-5.2:cloud
- 1000 users is your realistic ceiling
- Budget matters
- Want new + warranty (36GB M4 Max)

### Choose 64GB if:
- Want 3+ concurrent local chats
- Plan to run 22B/30B model locally
- Want peak-load tolerance
- Future-proofing matters
- 2000+ users in 2 years

### Choose M3 Ultra 64GB if:
- Want to run local 70B LLM
- Need 6+ concurrent whisper
- 5000+ users

### The S$530 question (M4 Max 36GB vs 64GB)

| Pricing | Payback for the +S$530 |
|---|---|
| S$8/user × 1000 users | 2 days |
| S$5/user × 1000 users | 3 days |
| S$2/user × 1000 users | 8 days |

If you can stretch the S$530, do it. If budget matters more, 36GB is fine.

---

## 16. Final Recommendation

### 🥇 Buy Mac Studio M4 Max 64/1TB NEW in SG (S$2,629 net)

**Why this is the best pick:**
- New with full Apple 1-year warranty + AppleCare+ eligible
- 64GB RAM future-proofs against bigger local LLMs (22B, 30B)
- 10 perf cores → 2-3 concurrent whisper
- macOS support until 2030-2031 (4+ years beyond M1/M2 Max)
- S$2,629 net is 4 days of revenue at S$8/user × 1000 users
- No shipping risk (buy in SG)
- Future-proofs 2000+ user scaling

### 🥈 Alternative: Mac Studio M2 Max 64/1TB USED (S$2,300 net)

**If you want to save S$329 and trust the seller:**
- 64GB same as M4 Max 64GB
- S$329 cheaper
- Risk: used warranty, China carry-on

### 🥉 Budget pick: Mac Studio M2 Max 32/512 USED (S$1,094 net + S$160 GST = S$1,254 landed)

**If budget is genuinely tight:**
- Cheapest 32GB option
- Sufficient for 1000 users with cloud chat
- Used risk, but proven chip

---

## 17. Action Checklist

Before you buy:
- [ ] Confirm fibre plan is asymmetric 10G/1G (not symmetric 10G/10G)
- [ ] Verify old Mac trade-in value with Apple Trade-In tool
- [ ] Decide: 36GB (S$2,099) or 64GB (S$2,629)?

After purchase:
- [ ] Set up Cloudflare Tunnel (free)
- [ ] Configure 30-day auto-purge for raw uploads
- [ ] Build LLM response cache (50% cost savings)
- [ ] Add Ollama rate-limit middleware (before public signups)
- [ ] Add "transcribe YouTube URL" feature (huge growth lever)
- [ ] Set up Stripe + annual plan (S$80/yr)
- [ ] Configure APC UPS + auto-shutdown script

Month 6 review:
- [ ] Check actual RAM usage in Activity Monitor
- [ ] Measure transcribe queue wait times
- [ ] Review cloud LLM costs
- [ ] Decide: upgrade machine or stay?

---

## 18. Key Files to Build (Pre-Launch)

| File | Purpose | Priority |
|---|---|---|
| Ollama rate-limit middleware | Prevent one user from monopolizing LLM | HIGH |
| LLM response cache (`model, prompt_hash` → response, 7-day TTL) | Save cloud LLM $$ | HIGH |
| 30-day raw upload auto-purge | Save disk space | HIGH |
| Per-user daily transcribe cap (5/day free, unlimited paid) | Prevent abuse | MEDIUM |
| "Transcribe YouTube URL" feature | 10× more accessible | HIGH (growth lever) |
| Stripe integration + annual plan | Better cash flow | MEDIUM |
| Public status page | Trust signal | LOW |
| YouTube URL → transcribe backend | The killer feature | HIGH |

---

## Appendix A: Pricing reference (Aug 2026)

### Acasis enclosures (Singapore)
- EC-7355 5-bay HDD: S$200-280
- EC-H004 4-bay HDD: S$180-230
- EC-H006 6-bay mixed: S$280-350
- TBU405 NVMe: S$150-200
- TBU405 Pro 4-bay NVMe: S$300-400

### Storage drives
- WD SN850X 4TB NVMe: S$280-380
- Samsung 990 Pro 4TB: S$320-400
- WD Red Plus 4TB HDD (NAS): S$200-260
- Seagate Ironwolf 4TB: S$220-280

### UPS
- APC Back-UPS 600VA BX600U-SG: S$120-180
- APC Back-UPS 1000VA: S$200-300
- CyberPower UT650EG: S$80-130

### ISP (Singapore)
- 10 Gbps fibre: S$89-129/mo (ViewQwest, WhizComms, MyRepublic)
- Static IP: S$10/mo (often included)
- Symmetric 10G/10G: S$200+/mo (rare)

### Cloud services
- Backblaze B2: $6/TB/mo (first 10 GB free)
- Cloudflare free: free for personal use
- Apple Developer Program: S$132/yr (for iOS push)
- Domain (.com): S$18/yr

### Electricity (Singapore, Aug 2026)
- SP Group tariff: ~S$0.32/kWh (incl. GST)
- Mac mini M4 idle: ~10W, under load ~70W
- Mac Studio M4 Max idle: ~20W, under load ~120W
- 30W avg × 24h × 30d × S$0.32/kWh = ~S$7/mo

---

## Appendix B: Conversion rates (Aug 2026)

| Currency | Rate (vs SGD) |
|---|---|
| 1 SGD | = 5.4 RMB |
| 1 USD | ≈ 1.30 SGD |
| 1 SGD | ≈ 0.74 USD |

---

*Document generated 2026-08-10. Private planning reference. Not for distribution.*

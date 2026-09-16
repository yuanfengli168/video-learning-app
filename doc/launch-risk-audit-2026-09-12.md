# Launch Risk Audit — 2026-09-12

> **背景**：launch 目标 2026-09-15，计划邀请 100 付费用户。当天下午服务器
> 全站瘫痪 4 小时（dashboard 一直 loading、视频播不了），根因是 8 个重排
> 视频的转写任务把 gunicorn worker 线程死锁在 `rlock_acquire`。本文档是
> 事发当天的事后完整隐患盘点——**只列事实和风险，不含已实施的修复**。
>
> **方法**：只读检查（`ps`/`sample`/`memory_pressure`/`launchctl`/DB 查询/
> git 历史核查），未修改任何系统状态。服务器已通过 stop.sh + restart.sh
> 恢复（dashboard 85ms / health 42ms）。

---

## 🔴 P0 — 已被今天事件证实、launch 必踩的

### 1. 转写/生成跑在请求 worker 进程内，无并发上限

**今天全站瘫痪 4 小时的直接根因。**

- 8 个视频同时转写 → worker 线程死在 `rlock_acquire` → 所有 HTTP 请求无响应
- 50 个用户同时上传 = 同样的剧本，无需任何巧合
- 死锁的具体锁点还没查清（whisper 模型缓存锁 / SQLAlchemy 池锁候选），但
  **架构性结论已定**：进程内跑重计算 + 无闸门 = 复发是时间问题

### 2. 任务队列纯内存（BackgroundTasks），重启即蒸发

- 今天 8 个视频卡 `queued` 的根因；重排后又因为 #1 死锁丢了一批
- **当前 DB 里有 8 个 `ready` 但 `generated_at=NULL` 的视频**（有转录没材料）
  + 1 个卡在 `generating` 的（K8S 架构那节，be417367）——launch 前需要补跑生成
- 没有 startup sweep（Todo #11），每次重启都可能留孤儿

### 3. 进度追踪器 `jobs.py` 也是进程内存字典，且 4 个 worker 各一份

- 上传请求落在 worker A，`/status` 轮询落在 worker B → 偶发进度条消失/永远 0%
- 今天的死锁让这个 bug 的"发作率"从偶发变成常态

### 4. 重排/重试路径 2026-09-12 刚大改，只有测试覆盖、没有线上验证

- `_staggered_transcribe_job` 现在跑完整链（transcribe→generate），昨天那个
  "只转录不生成"的 bug 是用户报出来的，测试当时没抓住
- 今天的死锁恰恰发生在**修复后的新链**上——这个路径的稳定性证据为零

---

## 🟠 P1 — 高概率在 100 用户规模下发作

### 5. 无上传准入限制（Phase 1 还没做）

- 批量无上限、pending 无上限、单用户并发无上限——50 人 × 5 文件 = 250 任务直接灌进 #1
- 这是把 P0 #1 从"偶发"变"必然"的放大器

### 6. SQLite 单写者 × 4 gunicorn workers × 8 线程

- 转写每段落一次库 + 页面请求并发写 events 表 → 写锁竞争。今天死锁的疑似帮凶
- 100 用户下 WAL 模式能顶一阵（读不锁），但写热点（telemetry beacon 每次点击都写 events）会先疼起来

### 7. `generating` 孤儿暴露的状态机缺陷

- 任何一步被打断（kill/死锁），视频永远停在中间状态，**没有自愈，只有人工**
- `generating` 不会出现在 retry-stuck 的条件里（只捞 `queued`）——这个孤儿
  **没有任何按钮能救**，只能手动改库或重新上传
- 状态机缺"中间状态超时回收"这一环

### 8. LLM 生成在重排风暴下无并发护栏

- 8 个视频同时进入 generate → 8 个并发 LLM 调用打给 ollama → 之前实测过 burst 就 429
- Ollama quota tracker 有 90% 自动降级，但没有并发上限——错峰 sleep 是唯一防线，
  而它今天恰好参与了死锁

### 9. 备份是冷快照，没有 PITR（时间点恢复）

- 最新备份 18:00，**今天 4 小时瘫痪期 + 当天所有用户数据在 12:00-18:00 之间
  只有一份 18:00 快照**
- SQLite `.backup` 是好东西，但 launch 后用户聊天记录/上传元数据丢了没法补
- （launch 级够用，只是要知道边界）

---

## 🟡 P2 — 真实但可排队

### 10. 磁盘双卷隐患

系统盘 926GB 用了 17GB（好），Storage-Medium 1.8T 用 41GB（好），但
**uploads 和 DB 在不同卷**——任何一卷掉线，另一卷数据变"孤儿"（有 DB 行
没文件，或有文件没行）。没有启动时的 volume 健康检查。

### 11. `jobs.py` 的 `_jobs` 全局字典无锁

多线程下 `_jobs` 字典本身的并发写也是今天死锁的候选之一。

### 12. 单机单点

Cloudflare tunnel → Mac Studio，硬件故障 = 全站下线，没有降级页面
（用户看到的是浏览器超时）。

### 13. gunicorn `timeout=60` 形同虚设

对被 Whisper 挂住的 worker 无效（今天 worker 根本没超时自杀——说明长任务
让 timeout 机制失灵，值得查为何没触发 worker restart）。

### 14. 没有 request 级隔离

一个上传 500MB 的慢用户占用 worker 12 秒（磁盘 I/O），页面请求和它共享
8 个线程——100 用户时 p95 会难看。

### 15. 密钥躺在项目根目录

`firebase-service-account.json` 在 repo 根目录（gitignore 了、没进历史，
✅ 核查干净）——但它躺在项目根目录意味着**任何误操作 `git add -f` 或打包
工具都会带上它**。launch 前建议挪去 `~/.config/` 之类的位置，路径由 env 指定。

### 16. logs 无轮转

`accesslog = "-"`（stdout → /tmp/r22.log 这种临时文件），今天一个下午就
435KB——launch 后几天就是 GB 级，且日志在 /tmp（重启即丢）。

---

## 排序建议（launch 9/15 前）

> ⚠️ **2026-09-12 晚间修订**：本表是审计当时的原始建议。事后讨论否决了
> startup sweep（见下方"审计后决策"），第 2 行已被替代方案取代；机器
> 事实也修正了（附录环境事实）。修订后的周末执行清单见"审计后决策"末尾。

| 优先级 | 事项 | 工作量 |
|---|---|---|
| 必须 | #5 Phase 1 限制（批量≤5、pending 拒新、每用户 1 并发转写） | 2-3 小时 |
| 必须 | #2+#7 孤儿状态回收（startup sweep 顺手把今天 8+1 个视频救回来） | 半天 |
| 必须 | #4 重排链的线上验证（小文件试一遍全链） | 1 小时 |
| 强烈建议 | #3+#11 jobs.py 落库（修进度条丢失 + 消掉一个死锁候选） | 半天 |
| launch 后 | #1+#8 Phase 2 持久化队列+调度器 | 2 天 |
| 排队 | 其余 P2 逐个来 | — |

---

## 审计后决策（2026-09-12 当晚，用户逐条拍板）

审计发布后与用户过了一遍 P0，以下决策**取代**上表的对应行：

1. **startup sweep：否决。** 用户直觉："有些人的 failed 可能下个月才要，
   等他们自己手动弄，把空位留给别人。" 自动扫描会替用户做决定并烧掉
   Whisper 槽位/LLM 费用。**替代方案：把 retry-stuck 的 stuck 条件扩展为
   `queued >10min OR generating >30min`**——`generating` 孤儿
   （be417367 形态：LLM 进行中被 SIGKILL，永远悬置）此前处于死区，无任何
   按钮能救；扩条件后用户在课程页可见 ↻ 自助。代价（已知情接受）：每次
   重启蒸发进行中任务后，需要用户自己点 ↻——runbook 加一句"重启后提醒
   用户 ↻"。此扩展同时是救回今天 9 个视频的工具（8 个 ready 无材料 +
   1 个 generating 孤儿——现有任何按钮都覆盖不到它们）。

2. **机器事实修正（重要度高于全部条目）**：审计当天误以为被测机器是生产
   机。实际：**审计发生在这台开发机（MacBook Pro M1 Max / 64GB）**；生产
   目标是 **Mac Studio M2 Max / 32GB**（用户 9/12 确认）。后果：
   `gunicorn.conf.py` 的容量注释（"Mac Studio has 10 CPU cores / 64 GB
   RAM"）按不存在的机器写就；**32GB 上 P0.1 的最坏形态从"线程死锁"升级
   为"死锁 + OOM 双杀"**（系统驻留 4-10GB + large-turbo 模型 3GB×N 个
   无锁并发加载）；生产机目前零线上验证。护栏从"强烈建议"升为"上线前
   硬性前提"。另：本地 Ollama 在 32GB 上建议不跑，生成全走云端。

3. **FREE 配额 15 → 10/天**（防御性收紧）。数学：Groq 全 key 250/天 ÷
   10 = 25 人同天聊满；真实瓶颈是"总活跃"不是"个人配额"（50-80 邀请、
   日活聊天 10-16 人时 15/天 本就够），10 把全 key 烧穿概率再压一档，兼
   免费用户感知付费价值。**修正我审计前的一句错话**：付费用户 chain =
   `ollama,openai`，**Groq 根本不在付费链里**（已验证 llm_providers.py），
   250 烧穿只影响 FREE 聊天，付费体验零影响——此项因此零付费侧风险。

4. **`_model_cache` 无锁并发加载确认为今天事故的燃料**：8 个 large-turbo
   转写 5 秒错峰起跑 → 多线程同时 miss 缓存 → 各自加载 3GB 模型副本。
   修法：threading.Lock 包住 check-and-load（10 行）。归入护栏一并落。

5. **P0.4 实弹测试升级**：必须在 **Mac Studio（32GB 生产机）** 上跑，
   不是开发机；通过标准是"转写进行时 /api/health 每 30s 保持响应"。

6. **Stuck 形态分类学 + retry-stuck 最终覆盖方案（2026-09-13 用户追问后
   敲定）**。原扩展方案（queued>10min OR generating>30min）被用户抓到
   两个漏洞形态，最终方案改为**基于事实数据而非 status 字段**——因为
   status 会骗人（说 ready 其实没材料；说 transcribing 其实早死了）：

   | # | 形态 | status | 有转录 | 有材料 | 真实例子 | 判定 |
   |---|---|---|---|---|---|---|
   | 1 | 排队即卡 | queued | ✗ | ✗ | 昨天上午 8 视频 | A: >10min |
   | 2 | 转写中死 | transcribing | ✗ | ✗ | 死锁 kill 时 | B: job 时间戳 >30min |
   | 3 | 生成中死 | generating | ✓ | ✗ | be417367 (progress 90%) | C: job 时间戳 >30min |
   | 5 | ready 无材料 | ready | ✓ | ✗ | 昨天下午 8 视频 | D: generated_at IS NULL（**永久事实，不需超时**） |
   | 4 | 正常完成 | ready | ✓ | ✓ | 其余 37 个 | 排除（不误伤） |

   救法统一按"缺什么补什么"：有 transcript Asset → 只补 generate；
   无 → 走完整链（transcribe→generate）。`error` 状态**故意不救**——
   那是 retry-failed 的语义领域。条件判定抽成共用 `_stuck_condition()`
   （UI stuck_count 与端点共用，避免上次"各算各的"不一致教训）。
   job 时间戳取 `last_*_job` JSON 的 `started_at`（epoch 秒）。
   **已于 2026-09-13 落地（commit 19107cc），live 验证精确抓到 9 孤儿。**

7. **护栏方案演进：拒绝式 → 迷你队列（2026-09-16 讨论敲定）**。
   用户对原方案（槽满即 409 拒绝）提出四个关切：(1) 不要硬编码；
   (2) 没队列时能支持几个付费用户？(3) 有没有更动态/聪明的做法
   （如查 RAM）？(4) **付费用户应尽量少看到拒绝，排队也要告知等多久**。
   第 4 点改变了整个设计方向：

   - **拒绝式护栏废弃**。RAM 检测作为主闸门被否（昨天事故全程内存
     95% free——杀全站的是线程饥饿+锁竞争，不是内存；RAM 检测会给
     绿灯然后照样死锁；且有 TOCTOU 竞态）。**确定性是特性**：最坏
     情况可预算可测试。
   - **采用"迷你队列"**：上传永远成功（零拒绝）；视频 DB 行本来就是
     队列（status='queued'），缺的只是消费循环——每 2-5s 扫 FIFO，
     "全局槽空 + 该用户无进行中"才原子认领（UPDATE...WHERE claimed
     IS NULL，4 worker 竞争安全）。**这正是用户上周方案 2 的公平
     规则**：谁的先到谁上，同用户有在跑的跳过他——保证每人第一个
     视频尽早开跑，没有人被晾几小时。
   - **ETA 数据现成**：队列位置 × 实测平均转写时长 ÷ 槽数（课程页
     T:xx 历史就是数据源），UI 显示"前方 N 位 · 预计 ~X 分钟"。
   - **结构红利**：form A（排队即卡）孤儿从"事故"变"自动恢复"——
     行永远在 DB，重启后循环自动续跑。form-A 孤儿不再需要 ↻ 按钮
     （注意：>10min 即卡死的判定要跟着改，否则会误伤正在排队的行）。
   - **动态化的"聪明版"**：加权槽（不数个数，数内存预算：turbo 占
     大头，base/small 是便宜选项，预算按默认 turbo 标定 ~5GB）+
     RAM 地板副闸门（认领前查 free RAM <8GB 暂停认领，防 OOM，
     作第二道保险而非主闸门）。
   - **10 用户 × 5 视频推演**（已向用户详述并认可）：上传秒成功零
     拒绝 → FIFO 轮流入场（每 ~1-2 分钟放一个新用户）→ ~50-60 分钟
     全部 ready（MLX turbo 吞吐）→ 全程网站在线 + 每人看得见队列
     位置。对比昨天：同样负载 = 全站瘫痪 4 小时。
   - **默认模型修正**：默认是 local-large-turbo（mlx whisper-large-
     v3-turbo，Apple Silicon 自动选，DB 证实 24 视频全用它）——
     不是 small。数字全部按 turbo 重算（~3GB 模型常驻、缓存锁后
     全局一份共享）。MLX 走 GPU 指令，**槽位对 MLX 的意义比 CPU
     whisper 更大**（GPU 争用），2 槽上限的实测校准留给 Mac
     Studio 实弹测试。
   - **用户补充（2026-09-16，待调研核实）**：MLX 转写可能固定使用
     ~4 个 GPU core（Mac Studio M2 Max 共 10 GPU core），这可能是
     "只能 2 并发"的底层原因。→ 见决策 8 的深度调研结果。
   - **代价（已知情）**：工作量 半天→~1 天（调度循环+原子认领+ETA+
     测试）；in-process 风险被 2 槽框住但未根除（独立转写进程仍是
     Phase 2 终点）。

8. **MLX GPU 并发上限深度调研（2026-09-16，回应用户"4 GPU core"假设）**：
   结论——**2 并发不是 GPU core 数量决定的**，核心事实与机制：

   - **MLX 不绑定 GPU core**（源码级证据，mlx 0.32.2）：MLX 的计算
     encoder 用 Metal `MTL::DispatchTypeConcurrent` 派发 kernel
     （`mlx/backend/metal/device.cpp`）——Metal 调度器自动把线程组
     分散到**全部** GPU core，单个转写任务的一次 matmul 就能吃满
     整个 GPU。MLX 没有任何"每个任务预留 N 个 core"的 API。
     `mlx_whisper/audio.py` 里唯一的 `-threads 0` 是 **ffmpeg 音频
     解码的 CPU 线程数**（0=自动），与 GPU 无关。
   - **并发转写的真实机制是 GPU 时间片轮转，不是分核**：MLX 的
     command encoder 是 **thread_local**（`get_command_encoders()`
     返回线程局部表）——gunicorn 里两个转写线程各自建 Metal
     command queue，GPU 驱动在两个队列间**交错执行 kernel**。
     两个并发转写各自变慢、总吞吐接近单任务饱和值，而不是
     "4 core + 4 core = 各跑一半"。
   - **"观察到只用 4 个 core"的可能解释**：Activity Monitor 的
     GPU 核心占用显示是采样近似；whisper 的许多 kernel 是
     memory-bandwidth-bound（编码器小算子），瓶颈在带宽不在算力，
     监控上呈现"部分核在忙"。这不代表有核被预留。
   - **真正的并发约束（2 槽的工程依据）**：(a) GPU 时间片轮转到
     饱和后，并发数不再增加吞吐只增加延迟；(b) unified memory
     预算（模型权重 + 音频解码缓冲）；(c) GPU 还要服务窗口系统/
     视频播放。**"2"是内存预算+爆炸半径的工程判断，不是硬件
     常数**——正确数字只能实测：Mac Studio 上跑 1/2/3 并发的
     聚合吞吐对比（videos/小时），3 并发若打不过 2 并发就定 2。
   - ⚠️ **机器规格疑点（2026-09-16，待用户在 Mac Studio 上核实）**：
     用户描述"Mac Studio M2 Max / 32GB / 10-core GPU"——这三个数
     互相矛盾：**M2 Max 的 GPU 是 30/38 core**；10-core GPU 是
     **M2 base**（Mac Studio 2023 无 M2 base 版，但 M2 Max 的
     10-core CPU 也对不上，M2 Max CPU 是 12 核）。可能机器实为
     M2 Max（30-core GPU）而 core 数记错，也可能整台机器型号
     记错。核实命令：`system_profiler SPDisplaysDataType -json` +
     `sysctl hw.memsize hw.model`。这是 9/12 机器事实事故
     （64GB 假设）的同类问题——容量参数全部取决于这个答案，
     **列为 Mac Studio 实弹测试的第 0 步**。

**修订后周末执行顺序**（9/16 更新，原 9/12-14 周末计划顺延）：
① ✅ retry-stuck 扩展（9/13 落地，19107cc）→ ② WAL + `_model_cache` 锁 +
gunicorn 32GB 实机化（半天）→ ③ **迷你队列**（加权槽+每用户1+FIFO
跳过+ETA+原子认领，~1 天，取代原"拒绝式护栏"）→ ④ Mac Studio 实弹
测试（含 2 vs 3 并发 GPU 校准，需用户在场）→ ⑤ FREE 配额 10/天 →
⑥ go-live。

---

## 附录：事发时间线（供 postmortem 引用）

| 时间 | 事件 |
|---|---|
| 03:29 | 8 个视频批量上传，进入内存队列 |
| 03:29+ | 服务器当天首次重启 → 8 个任务蒸发，DB 行卡 `queued` |
| 下午 | 用户点 ↻ Retry stuck 重排 → 8 个 `local-large-turbo` 大模型转写分批（5s 错峰）跑起 |
| 11:29:52 | 服务器日志最后一行 —— 转写链跑起后 worker 死锁于 `rlock_acquire`，日志从此沉默 |
| 11:29 → 15:5x | 全站无响应 4+ 小时：dashboard loading、duplicate tab 视频播不了 |
| 18:0x | 诊断：内存 95% free（排除 OOM），`sample` 证实主线程卡 `rlock_acquire`，仅剩 2 worker 且烧 CPU |
| 18:1x | `stop.sh` SIGKILL 清死锁，`restart.sh` 恢复：dashboard 85ms / health 42ms |
| 事后 | DB 检查：8 个视频转写完成但 `generated_at=NULL`，1 个卡 `generating`（be417367） |

## 附录：环境事实（审计当天测得）

- Mac Studio，**64GB RAM**（gunicorn.conf.py 注释与实机一致；早前讨论按 32GB 估算的内存压力场景需按 64GB 修正——但**线程死锁与内存无关**，结论不变）
- 内存 free 95%（事发时），排除 OOM 假设
- `sleep=0`（已禁），磁盘 sleep 10（外置卷会休眠——P2 #10 相关）
- 备份守护进程 4 个在 launchctl，DB 快照 6 小时一次落 `/Volumes/Storage-Backup-HDD/db-backup`，18:00 快照存在且 7.1MB
- 无游离 dev server（8001-8010 无监听）
- `firebase-service-account.json` 未被 git 追踪、未进历史（`.secrets.baseline` 存在）
- launchd `backup-db` / `backup-probe` 上次退出码 78（值得在 P2 #9 复查）
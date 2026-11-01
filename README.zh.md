# ghostauthor

> **别让「看着像 AI 写的」当成审计结论——署名才是可审计的证据。**
> Don't let "looks AI-written" pass as an audit conclusion — the signature
> is the auditable evidence.

`ghostauthor` 是一个确定性的 AI 代码溯源账本。它读取 agent 们**已经在签**
的东西——`Co-Authored-By:` git trailer——并对任意仓库回答：**哪个 agent 签了
哪些 commit、多少行、在哪些会话里。** 无 LLM、无风格检测、无网络。Python，
纯标准库。

[English README → README.md](README.md)

## 解决什么问题

「AI 代码占比」已经是一个人人引用、没人能辩护的数字。今天它来自三个地方之一：

1. **用量统计**数会话里烧掉的 token。那度量的是*活动*，不是*产出*——一个
   长会话可以零代码合入。
2. **风格分类器**猜「这看着像 AI 写的」。概率性的——它回答不了审计者真正
   的问题：*哪个 commit、哪个 agent、什么时候*？
3. **绑定宿主方的仪表盘**（比如 GitLab 的 `refs/notes/ai`）很好用——前提
   是你整个组织都活在那个宿主、那套机制上。

真正的问题不是「能不能度量 AI 产出」。真正的问题是：**「AI 写的」目前在
commit 历史里是一个不可验证的断言。** agent 们已经在一个标准地方签了名
——git trailer。只是没人像 `git log` 读取其他一切那样、带着严格去读这些
签名。

`ghostauthor` 就做这一件事，并把答案当成审计产物对待：可复现、可追溯、
可申诉。

## 60 秒上手

```bash
pip install ghostauthor
# （PyPI，v1.3+；永远最新的替代：
#  pip install git+https://atomgit.com/xiangsir/ghostauthor.git）
ghostauthor /path/to/your/repo
ghostauthor /path/to/your/repo --format json --max-ai-ratio 0.5
ghostauthor /path/to/your/repo --since 2026-07-01 --until 2026-10-01
# 审计一个季度，而不是整段历史（epoch、YYYY-MM-DD 或 ISO 8601；
# since 含边界，until 不含）
ghostauthor /path/to/your/repo --ref feature-x
# 审计某个分支/tag/commit，而不是 HEAD
ghostauthor /path/to/your/repo --base main --ref feature-x
# 双分支 diff：只算 feature-x 相对 main 新增的部分（git 区间 main..feature-x）
ghostauthor /path/to/your/repo --format sarif
# SARIF 2.1.0，给合规流水线用
```

签名（opt-in，v1.0）——唯一的写路径，默认关闭：

```bash
ghostauthor sign /path/to/repo --agent claude
# 默认 dry-run：展示将改后的 message，不落地
ghostauthor sign /path/to/repo --agent claude --commit
# 真正 amend HEAD，注入该 agent 的 Co-Authored-By trailer（roster `signing` 身份）
```

commit 时签名（opt-in，v1.1）——仓库级 `commit-msg` hook，只有你明确要求
时才安装：

```bash
ghostauthor install-hook /path/to/repo --agent claude
# 从此每个 commit 都在 commit 时刻带上 trailer——commit 还不存在，所以
# 无 amend、无 sha 改写。幂等替换自己的 hook；永不碰别人的。
ghostauthor uninstall-hook /path/to/repo
# 恢复无签名；一次性绕过用 `git commit --no-verify`（审计会把绕过显示为
# unsigned commit——什么都不隐藏）
```

还有一个 lint——对合法但脆弱的 roster 只告警、不拒绝：

```bash
ghostauthor lint-roster --roster my-team.json
# [warn] lint-catch-all / lint-overlap；无论有无告警都 exit 0。校验仍会
# 拒绝坏 roster（exit 2）；lint 只把脆弱照出来
```

追问一个结论（v1.2）——单 commit 级证据 + 可复现证据包：

```bash
ghostauthor evidence /path/to/repo <sha>
# 每条 trailer 被哪条 roster 规则命中、审计为何这样归因——与审计共用同
# 一个归因代码，两者不可能不一致
ghostauthor evidence /path/to/repo <sha> --format json
# 机器可读；JSON 契约已在 v1.3 冻结（schema/evidence.schema.json）
ghostauthor evidence /path/to/repo --bundle out/bundle
# 可复现证据包：report.json（冻结 schema）+ evidence.json + 实际用的
# roster + 带 sha256 的 manifest.txt——无时间戳，同状态重跑字节一致
# （canonical UTF-8/LF）
```

它读写的 JSON 契约是冻结的：输出带 `schema_version`，
`schema/report.schema.json` 是机器可检的形式，`docs/schema.md` 是单一事实
源（含兼容性策略）。签名意味着什么——以及它刻意**不是**什么——写在
[docs/attestation.md](docs/attestation.md)：读侧报告，写侧做声明，工具让
声明容易且可审计，但不替人验证声明为真。

手边没有仓库？克隆本仓库，构建随附的演示 fixture（确定性：固定日期、身份、
内容——SHA 稳定）：

```bash
git clone https://atomgit.com/xiangsir/ghostauthor.git
cd ghostauthor
python -m examples.make_fixtures out/fixtures
python -m ghostauthor.cli out/fixtures/mixed
```

下面是 `mixed` fixture 上真实运行的逐字输出：

```text
ghostauthor — deterministic AI code provenance ledger
repo: D:\Projects\atomcode\ghostauthor\out\fixtures\mixed   ref: HEAD

commits: 12 total
  agent-signed         6  (50.0%)
  cosigned-unknown     1  (8.3%)
  unsigned             5  (41.7%)

lines touched: 18 total
  agent 9  cosigned 2  unsigned 7

agents:
  ID         NAME                      COMMITS   +ADDED  -REMOVED  SESSIONS  LONGEST
  claude     Claude (Anthropic)              4        4         2         1        4
  codex      Codex (OpenAI)                  3        3         2         1        3

authors x agents:
  AUTHOR                         claude     codex  cosigned  unsigned   TOTAL
  Sam Human <sam@example.com>         4         3         1         5      12

findings (3):
  [warn] cosigned-unknown  9f4f9cc34f31: Co-Authored-By: VibeBot <vibe@example.com>
         Co-Authored-By present but no roster rule matched
  [info] multi-agent-commit  99186ce21e94
         commit carries signatures of several agents: claude, codex
  [warn] trailer-in-body  bf73ac1eecac: Co-Authored-By: Claude <noreply@anthropic.com>
         Co-Authored-By line outside the trailer block is prose, not metadata — ignored by design
```

作为 CI 门禁，退出码就是契约——同样是一次真实运行，`agent-heavy` fixture
（12 个 commit 里 11 个 agent 签的）：

```text
$ python -m ghostauthor.cli out/fixtures/agent-heavy --max-ai-ratio 0.5
...
gate: ai ratio 91.7% vs limit 50.0% — BREACHED
$ echo $?
4
```

| 退出码 | 含义 |
|---|---|
| `0` | 报告已产出；门禁（如有）未超限 |
| `2` | 环境/用法错误：不是 repo、无 commit、roster 坏、参数错 |
| `4` | 门禁超限：agent 签名占比超过 `--max-ai-ratio` |

它还有一个**只读 MCP server**（一个工具 `repo_authorship_report`，stdio，
零依赖）——见 `ghostauthor/mcp_server.py`。它只报告，从不执行。这是不变量，
不是承诺。

## Web UI

同一本账本的本地仪表盘：一个自包含 HTML 页面，由纯标准库 `http.server`
服务。无框架、无构建步骤、无 CDN——「在审计它的那台机器上跑」这个不变量对
web 版同样成立；页面只在指向的仓库里触发只读的 `git log`。它展示、可下载的
JSON 与 CLI 的 `--format json`（同一仓库状态）**逐字节相同**：一个事实源，
两个皮。

```bash
ghostauthor-web                       # → http://127.0.0.1:8321
ghostauthor-web --port 9000           # 换端口
ghostauthor-web --host 0.0.0.0        # 只有你真的想清楚时
```

默认绑定 `127.0.0.1`。输入一个或两个仓库路径——各自可选 scope 到某个 ref
（`main` vs 特性分支）——外加 CLI 有的全部选项：时间窗口、session gap、
`--max-ai-ratio` 门禁、roster 文件或目录。加第二个条目即可并排对比两个代码
库、或同一个代码库的两个 ref。每个数字都能展开成 commit 列表；原始字节稳定
JSON 一键可达。与 CLI 和 MCP server 一样，web 传输层**只读**：只报告，从不
执行。

## 设计取舍

| 决定 | 为什么 |
|---|---|
| 判定路径上没有任何 LLM | 一个能被措辞带偏的审查者，终将被措辞带偏。判定必须可重跑、可审计、可申诉。 |
| 只认 trailer——不做风格检测 | 溯源，不是猜测。「unsigned」是一个类别，不是指控：unsigned ≠ 人类。 |
| 纯标准库、零依赖 | 审计工具必须在被审计的机器上跑。没有 venv 仪式，没有自己的供应链。 |
| 只读 MCP server，一个工具 | 读侧只报告，从不执行。这是不变量，不是承诺。 |
| 唯一写路径、opt-in、受监督（v1.0） | 工具一旦能*行动*，风险模型就从「建议」变成「受监督的决定」。所以写路径**有意且可见**地跨过那条线：`ghostauthor sign` 是显式子命令（无 hook、无默认），默认 dry-run，幂等，冲突拒绝，只用 roster 自己校验过的身份签名。边界写在 [docs/attestation.md](docs/attestation.md)。 |
| 退出码 `0/2/4` 作为 CI 契约 | 接不进流水线的工具只是玩具。 |
| 缺失 ≡ 空，从不异常 | 数据不全才是常态。一个遇到坏 trailer 就崩的工具，等于要求「先补全再审计」——没人压力下会那样做。 |

## 不变量——让这工具多年不烂的东西

这些不是功能。它们是会让这工具悄悄腐烂的东西：

1. **溯源，不是风格。** 只读 git trailer 块——git 的语义，不是我们的发明。
   message *正文*里的 `Co-Authored-By:` 行是散文：作为数据被忽略，作为
   `trailer-in-body` finding 被照出来。没有这条边界，工具就变成戴着账本
   面具的猜测器。
2. **每个数字可追溯。** 每个桶、agent、session 计数在 JSON 报告里都带着
   它的 commit 列表。一个不能展开成证据的判定，就是一个在评审里守不住的
   判定。
3. **上限（cap）。** 一个 commit 可以带多个 agent 签名。per-agent 归因
   可以重叠——但去重后的总数绝不重叠。报告同时展示两个数字，「per-agent
   之和大于总数」是文档化的事实，不是矛盾。
4. **同仓库状态 ⇒ 同报告。** commit 按从 commit 里读出的 `(authored_at,
   sha)` 排序——从不看墙钟，从不靠哈希遍历运气。JSON 输出字节稳定
   （`sort_keys`）：可 diff、CI 友好、可归档。
5. **边界语义由测试钉死。** ratio *恰好等于* `--max-ai-ratio` 是 PASS
   （`>`，不是 `>=`）；窗口*含* `--since` 边界、*不含* `--until` 边界。
   改这些语义中的任何一个，都必须有意地改对应的测试。

写路径（v1.0）加了五条——完整边界见
[docs/attestation.md](docs/attestation.md)：

6. **Round-trip（承重墙）。** 用 roster 某 agent 签一个 commit，审计必须
   把它归给该 agent——绝不是 `cosigned`、绝不是 `unsigned`。读不回来的
   签名在 roster 校验时（`roster-signing-roundtrip`）和签名时
   （`sign-ambiguous`）被拒绝，而不是被写下去。
7. **幂等。** 签一个已签的 commit 是 no-op；第二次运行的 diff 为空。
8. **Opt-in 与同意。** 命令只在显式调用时存在（无默认流程）；dry-run 是
   默认，`--commit` 才是显式同意。v1.1 的 commit-msg hook 延续同一条线：
   它只在显式 `install-hook` 后存在、仓库级、标记保护（别人的
   `commit-msg` 永不被覆盖或删除）、随时可移除——没有任何仓库被默认签名。
9. **冲突不合并。** 已被*另一个* roster agent 签过的 commit 被拒绝
   （`sign-conflict`）——冲突归人解，从不归工具解。
10. **单一事实源。** 匹配（读）与签名（写）共用一份 roster；签名身份是该
    agent roster 记录的一部分，不是 env、不是自由文本。

## 一个可追溯的样本（真实运行，JSON 片段）

同一个 `mixed` fixture——下面每个数字都展开成 `bucket_commit_shas` 下的
commit 列表：

```json
{
  "agents": [
    {
      "commits": 4,
      "display_name": "Claude (Anthropic)",
      "first_ts": 1767229200,
      "id": "claude",
      "last_ts": 1767261600,
      "lines_added": 4,
      "lines_removed": 2,
      "longest_session_commits": 4,
      "sessions": 1
    },
    {
      "commits": 3,
      "display_name": "Codex (OpenAI)",
      "first_ts": 1767240000,
      "id": "codex",
      "last_ts": 1767261600,
      "lines_added": 3,
      "lines_removed": 2,
      "longest_session_commits": 3,
      "sessions": 1
    }
  ],
  "ai_ratio": 0.5,
  "bucket_commit_shas": {
    "agent": [
      "deb8b1e68469d720a92ba6b1ab7efedc0dbb07b2",
      "5dc8f5c47ad6ecda46bc52b1f2ca38f05f70c904",
      "d9bdca2577cb2a312e8397dbef7e94cffe01d298",
      "06ba554e15d5ec38c87d17134470b357deb137e5",
      "b7439afbe93d8edd462b4abe2473a1b3d8a10855",
      "99186ce21e94660c0466914561ccd0e7e0943c09"
    ],
    ...
```

## 如何贡献

对这个项目最有价值的贡献**不是代码——是 roster**。内置 roster 有 8 个
agent——其中 7 个带公认的 `signing` 身份，所以
`ghostauthor sign --agent claude` 开箱即用；你们常用的 agent 大概率不在
里面：

- `ghostauthor/roster/agents.json` 是声明式的：`id`、`display_name`、
  `name_patterns`（对 trailer 名字做正则匹配）和 `email_domains`（对
  trailer 邮箱域名做匹配）。文件序第一个命中者赢——优先序是数据，不是
  魔法。
- 可选的 `signing` 段（`{"name": ..., "email": ...}`）让该 agent 接入写
  路径：`ghostauthor sign --agent <id>` 将用恰好这个身份签名——而校验器
  会拒绝任何读不回它自己 agent 的身份（`roster-signing-roundtrip`）。
- roster **被校验，不被信任**：重复 id、坏正则、空选择器都会被带规则 id
  拒绝——从不悄悄修复。一个被悄悄「修好」的 roster 会让判定不可审计。
- 团队可以把多个 roster 文件放在一个目录里（`--roster-dir`）：按文件名
  排序合并，跨文件的重复 id 被拒绝（`roster-dir-dup`）——冲突归人解，
  从不被合并顺序掩盖。
- 带测试来：`tests/test_roster.py` 就是校验器自己的规格。

完整的写作指南——匹配语义（first-match-wins、前缀匹配、显式锚点）、每条
校验规则 id、lint 规则与最佳实践——在 [docs/roster.md](docs/roster.md)。

代码贡献请先读 `docs/architecture.md`——每个模块都有它禁止做的事（engine
不渲染、report 不重算、MCP server 不执行）。保持这样。

## Roadmap

- [x] v0.2 — `--since/--until` 时间窗口（审计一个季度，而不是整段历史）
- [x] v0.2 — SARIF 2.1.0 输出，给合规流水线
- [x] v0.2 — 多 roster 文件 / `--roster-dir`
- [x] v0.3 — `--ref`：审计某个分支/tag/commit，而不是 HEAD
- [x] v0.3 — web UI：本地只读仪表盘，纯标准库，字节稳定 JSON，并排对比
- [x] v0.3.1 — `--base`：CLI 双分支 diff（git 区间 `base..target`；web UI 并排对比两个 ref）
- [x] v0.3.1 — per-author × per-agent 交叉表（每个格子可追溯到 commit 列表）
- [x] v0.3.2 — 编码加固：UTF-8 stdout/stderr 与 git 子进程（`i18n.logOutputEncoding`、`core.quotepath=false`），中文 Windows（GBK locale）不再搅乱报告；web UI 亮色主题
- [x] v0.4.0 — CI 集成：composite GitHub Action、self-audit workflow、PR 评论模板
- [x] v0.5.0 — 读侧契约冻结：JSON 带 `schema_version`、`schema/report.schema.json`、golden snapshot、verify 强制冻结（一改就红）
- [x] v1.0.0 — 写路径：opt-in 的 `ghostauthor sign`（默认 dry-run、`--commit` 才落地、幂等、冲突拒绝），检测/证明边界写在 `docs/attestation.md`
- [x] v1.1.0 — commit 时签名：仓库级 `commit-msg` hook（`install-hook` / `uninstall-hook`，commit 时刻签名——无 amend、无 sha 改写；标记保护外部 hook；`--no-verify` 是文档化逃生口）+ `lint-roster`（catch-all / 跨 agent overlap 告警，exit 0）+ 内置 roster 公认 `signing` 身份
- [x] v1.2.0 — 单 commit 级 `evidence`（每条 trailer 被哪条规则命中、为何这样归因——与审计同一个归因代码，两者不可能不一致）+ `--bundle` 可复现证据包（report + evidence + roster 快照 + sha256 manifest，无时间戳）+ `roster.match_detail`（带理由的匹配）
- [x] v1.3.0 — 可从 PyPI 安装（`pip install ghostauthor`；OIDC trusted publishing，仓库内无 token）+ evidence JSON 契约冻结（`schema/evidence.schema.json`，verify 强制——一改就红）+ `docs/roster.md`（写作指南）+ `README.zh.md`

## 测试

169 个测试钉的是**性质**，不是覆盖率：

- **可复现性** — 同仓库状态 ⇒ 相同报告，与输入顺序无关
- **trailer 块语义** — 负例：正文里的签名、签名后跟散文、CRLF message、缺邮箱、小写 key、续行
- **校验器自己被校验** — 必须被拒绝的坏 roster，逐个对照规则 id
- **上限（cap）** — 多 agent commit 在总数里只算一次；per-agent 重叠被文档化，不隐藏
- **门禁边界** — 恰好等于上限是 PASS；超限 exit 4
- **窗口边界** — `--since` 含、`--until` 不含；空窗口或倒置窗口是契约错误，不是悄悄空报告
- **窗口边界解析** — epoch / 日期 / ISO 8601 解析到同一时刻；naive 值按 UTC；垃圾被拒绝
- **roster 目录合并** — 文件名排序，跨文件重复 id 按规则 id 拒绝
- **签名身份** — roster `signing` 段可选；读不回自己 agent 的签名身份被拒绝（`roster-signing-roundtrip`），从不修复
- **写路径** — dry-run 是默认（没有 `--commit` 什么都不落地）；sign → audit round-trip 回同一 agent；第二次 sign 是 no-op；别的 roster agent 的签名被拒绝（`sign-conflict`）；roster 优先序会误归的身份被拒签（`sign-ambiguous`）；每条错误路径 exit 2 且不改任何状态
- **commit-msg hook** — 真实 `git commit` 触发已装 hook，trailer round-trip 回审计；预签 message 恰好保留一条 trailer（幂等）；冲突的 commit 被**阻止**（非零 commit-msg 退出中止 commit，什么都不落地）；`--no-verify` 绕过且审计显示 unsigned commit；`install-hook` 拒绝外部 `commit-msg`（`hook-exists`）、`uninstall-hook` 拒绝非己 hook（`hook-not-ours`）；生成脚本 pin 自己的解释器、PYTHONPATH 和 roster
- **roster lint** — 通吃型 `name_patterns` 被告警（`lint-catch-all`）、roster 优先序会误归的签名身份被告警（`lint-overlap`）；告警是建议性的（exit 0、确定性、字节稳定 JSON），坏 roster 仍是硬错误（exit 2）；内置 roster lint 干净
- **证据（evidence）** — 每个 commit 的 evidence 归因都等于审计桶（同一份代码）；命中的 trailer 指名命中的 roster 选择器（`match_detail` 与 `match` 一致）；JSON 字节稳定；未知 SHA exit 2（`sha-unknown`）；`--bundle` 可复现（同状态 + 同路径 ⇒ 同字节），其 `report.json` 等于审计报告、manifest sha256 与磁盘文件一致、写入已存在目录被拒绝（`bundle-exists`）
- **evidence 冻结** — 真实 `evidence --format json` 输出通过 `schema/evidence.schema.json` 校验；bundle 的 `evidence.json` 是同样的字节；删 key、加 key、伪造归因各被按名拒绝
- **Schema 冻结** — 真实 `render_json` 输出通过 `schema/report.schema.json` 校验；golden snapshot 字节稳定；删/加 key 被按名拒绝
- **SARIF 映射** — 字节稳定；findings → 带 rule descriptor 的 results；超限门禁是 `error` 级 result
- **Framing 契约** — git 输出解析器对着手工构造的流钉死，包括拒绝未 framing 的输出
- **Ref 作用域** — ref 把扫描限到那个分支/tag/commit；与 `--all` 组合被拒绝（`ref-conflict`）；路径不存在是 `not-a-repo`，绝不是服务端故障
- **双分支 diff** — `--base` 扫 git 的 `base..target` 区间；空区间是 `no-commits`（绝不悄悄空报告）；`--base` + `--all` 是 `ref-conflict`；`-` 开头的 base 是 `ref-invalid`
- **交叉表** — per-author 行、per-agent 列：多 agent commit 落进每个命中列（cap 不变量），行总数去重，每个格子展开成 commit 列表
- **Web 对等** — web 传输层提供与 CLI 逐字节相同的 JSON；请求级错误（坏窗口/门禁/roster）是 400，条目级错误（not-a-repo 404、no-commits 422）带文档化的码

一键跑整个矩阵（roster → 单测 → 3 个确定性 fixture 仓库 → CLI 退出码 →
真实 MCP `initialize`/`tools/list`/`tools/call` → 渲染 → 时间窗口 →
roster 目录合并 → SARIF → live web server 与 CLI 字节稳定 → 双分支
diff + 交叉表 → CI 资产 → schema 冻结（强制）→ 写路径：round-trip /
幂等 / 冲突 / 默认关闭 → hook：安装 / commit 时刻签名 / 幂等 / 冲突 /
卸载 + roster lint → evidence：审计一致性 / 字节稳定 / 可复现 bundle →
evidence 冻结（强制）），并打印跳过了什么、为什么：

```bash
python verify.py
```

## 许可

MIT — 见 [LICENSE](LICENSE)。

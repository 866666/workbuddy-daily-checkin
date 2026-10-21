---
name: totorosir-workbuddy-score
display_name: WorkBuddy积分助手
display_name_en: WorkBuddy Credit Assistant
description: "WorkBuddy积分助手（WorkBuddy积分查询、获取、播报 Skill，跨平台支持 Windows / macOS / Linux）积分余额、连续登录天数、当月活跃地图、本周进度、本期活动与连续奖励分档；只读展示成长计划任务/进度/当前 Buddy；只读展示派猫猫旅行状态（地点、倒计时、可领积分）；支持一键自动签到（幂等）与派猫猫旅行全自动闭环（先领后派，需显式 --travel-auto）。支持三通道授权（本机登录态 / 网页会话 / 长效令牌），跨平台 Windows/macOS/Linux。触发词：查积分、我的积分多少、积分余额、今天记录没、今天签到了吗、连续登录几天了、全勤能得多少分、还差几天到下一档、下一档奖励、活动入口、去哪领积分、记录催办、帮我签到、自动签到、每日签到、每日播报、桌面播报、积分情况、派猫猫旅行、派 Buddy 去旅行、猫猫旅行、旅行积分、领旅行奖励、Buddy 在旅行吗、推送积分、消息推送、发通知、推送到钉钉。"
description_zh: "WorkBuddy积分助手：读取本机登录态，接口直查 Buddy 加油站数据生成每日积分播报；只读展示成长计划与派猫猫旅行状态，支持一键幂等自动签到与旅行全自动闭环（先领后派，需显式 --travel-auto），并可把播报推送至自有渠道（12 渠道，复用消息推送技能配置，--no-push 可关）。提供三通道授权：A 本机登录态（零配置，典型 Windows）、B 一次性网页会话授权（macOS/Linux 等无法本地解密时）、C 长效令牌自续期（客户端可不开）。跨平台 Windows/macOS/Linux；以只读为主，写操作仅限签到与旅行领取/派出，不打印 token，不修改登录态。兼容 WorkBuddy 5.6.2+ 客户端：登录态 accessToken 为 AES-256-GCM 信封，自动识别并解密。"
description_en: "WorkBuddy Credit Assistant queries Buddy Station via API for a daily credit report (balance, streak, calendar, weekly progress, event, streak rewards), shows the read-only growth plan and Buddy travel status, supports one-click idempotent auto check-in and a fully automated travel loop (claim first, then depart; needs explicit --travel-auto), and pushes the report to 12 channels mirroring totorosir-push-message (disable with --no-push). Three channels are chosen automatically: a usable local login state (typically Windows) needs zero config; if it cannot be decrypted (typically macOS), a one-time ~30s web-session authorization applies; or save a long-lived refresh token once for self-renewal that works even with the client closed. Cross-platform Windows/macOS/Linux. Read-only by default; write calls limited to check-in and travel claim/depart; never prints the token. Compatible with WorkBuddy 5.6.2+: the accessToken became an AES-256-GCM envelope, auto-detected and decrypted."
category: automation
version: 3.1.7
author: totorosir
agent_created: true
visibility: public
license: MIT-0
metadata:
  openclaw:
    emoji: "💰"
    requires:
      bins:
        - python3
      env: []
allowed-tools: Bash
---

# WorkBuddy积分助手（每日积分播报 + 成长计划 + 派猫猫旅行 + 自动签到）

读取本机已登录 WorkBuddy 的登录态，用接口直查「Buddy 加油站」数据，汇总成每日播报；支持成长计划只读展示、派猫猫旅行状态展示与全自动闭环（先领后派），以及一键自动签到。执行后**默认弹系统通知（跨平台），按需生成桌面 HTML 报告（加 `--html` 才生成）**。

> **安全边界（自证清白）**：只读登录态文件，绝不打印 / 外传 `accessToken`。写操作仅限三个已验证接口：`POST /v2/billing/meter/daily-checkin`（幂等）、`POST /activity/growth/buddy/travel/claim`、`POST /activity/growth/buddy/travel/depart`；绝不调用兑换 / 抽奖等其他写接口。旅行写操作**仅在显式加 `--travel-auto` 时执行**，且派出前必先查询 `daily_limit_reached`，达上限绝不发写请求。除向桌面写入报告 HTML 与调用系统通知外，不修改或删除任何文件。

---

## 一、何时使用（触发条件）

当用户出现以下任意意图时，调用本技能（按意图选对应命令）：

- **查询 / 播报类**：查积分、我的积分多少、积分余额、今天多少分、今天记录没、今天签到了吗、连续登录几天了、全勤能得多少分、还差几天到下一档、下一档奖励、活动入口、去哪领积分、记录催办、活跃地图、连续奖励、加油站日报、每日加油站播报、桌面播报、积分情况。
- **签到类**：帮我签到、签到、自动签到、每日签到、打卡。
- **成长计划类**：成长计划、成长任务、我的 Buddy、好事喵（当前 Buddy 名称随官方变动）。
- **派猫猫旅行类**：派猫猫旅行、派 Buddy 去旅行、猫猫旅行、旅行积分、领旅行奖励、Buddy 在旅行吗、旅行到哪了、还有多久回来、自动派猫猫。

任一意图命中即触发；无需用户精确措辞。

---

## 二、前置条件

1. **用户已登录 WorkBuddy 客户端**（本机存在登录态文件，详见 `@references/api-spec.md` 的「登录态定位」）。找不到文件时停止并提示「请先在 WorkBuddy 客户端登录」，不伪造数据。
2. **运行环境**：Python 3.8+ 标准库（无需第三方依赖）。优先 `python3`；未安装可用 WorkBuddy 托管 Python：
   - Windows：`~/.workbuddy/binaries/python/versions/*/python.exe`
   - macOS / Linux：`~/.workbuddy/binaries/python/versions/*/python`
3. **三通道授权（自 3.1.7 起）**：脚本按 `--channel`（缺省 `auto` 自动择优）从下列通道取凭据，任一成功即可运行；默认值优先通道 A，无需任何配置。
   - **通道 A · 本机登录态（零配置，典型 Windows）**：直接读取并（必要时）解密 `workbuddy-desktop.info` 的 `accessToken`，无需联网授权。
   - **通道 B · 一次性网页会话授权（macOS / Linux 等无法本地解密时）**：运行 `python scripts/buddy_station.py --snippet` 取得授权片段后在浏览器一次性授权，凭据落盘 `~/.workbuddy/checkin-cred.json`（POSIX 0600）。授权码携带原始 User-Agent，不受网关会话绑定影响。
   - **通道 C · 长效令牌自续期（客户端可长期不开）**：运行 `python scripts/buddy_station.py --export-rt` 取得长效 `refresh_token` 并落盘 `~/.workbuddy/checkin-rt.json`；每次运行用其换新 `accessToken` 并回写自转，客户端关闭仍能签到、跑旅行闭环。
   - 探测可用性与推荐通道：`python scripts/buddy_station.py --probe`（输出 JSON `{channels, recommendation}`）。

---

## 三、执行流程

当用户请求积分播报 / 签到 / 派猫猫旅行时，按以下步骤执行：

1. **定位脚本**：技能安装目录 `<skill-dir>`（用户级 `~/.workbuddy/skills/totorosir-workbuddy-score/`，或 SkillHub 分发后缀 `totorosir-workbuddy-score__skillhub/`；以本机实际为准），脚本在 `<skill-dir>/scripts/buddy_station.py`。
2. **按意图选命令**（见下表），用 Bash 工具执行。
3. **获取输出**：脚本向 stdout 打印文字播报（或 `--json` 时打印 JSON），向 stderr 打印 `[desktop]` 提示；不打印任何 token。
4. **向用户呈现**：默认仅输出文字播报 + 弹一次系统通知（跨平台，失败静默降级）。
5. **桌面报告的时机**：默认**不**生成桌面 HTML；仅在用户于对话中明确要看桌面报告、或你主动询问后用户确认需要，才重跑带 `--html`，生成 `~/Desktop/WorkBuddy积分播报.html`（藏蓝科技风单文件，双击即开）。

---

## 四、命令与参数

| 意图 | 命令 |
|---|---|
| 查积分 / 每日播报（默认，弹通知、不写 HTML） | `python "<skill-dir>/scripts/buddy_station.py"` |
| 查积分并生成桌面报告 | `python "<skill-dir>/scripts/buddy_station.py" --html` |
| 自动签到（幂等） | `python "<skill-dir>/scripts/buddy_station.py" checkin` |
| 自动签到 + 桌面报告 | `python "<skill-dir>/scripts/buddy_station.py" checkin --html` |
| 派猫猫旅行·只查看状态（默认，只读） | `python "<skill-dir>/scripts/buddy_station.py"` |
| 派猫猫旅行·全自动闭环（先领后派，写操作） | `python "<skill-dir>/scripts/buddy_station.py" --travel-auto` |
| 全自动闭环 + 指定地点 | `python "<skill-dir>/scripts/buddy_station.py" --travel-auto --location 1` |
| 签到同时跑旅行闭环 | `python "<skill-dir>/scripts/buddy_station.py" checkin --travel-auto` |
| 机器可读 JSON | `python "<skill-dir>/scripts/buddy_station.py" --json` |
| 签到并取 JSON | `python "<skill-dir>/scripts/buddy_station.py" checkin --json` |
| 关闭系统通知 | 追加 `--no-toast` |
| 关闭远程推送 | 追加 `--no-push`（桌面通知仍弹） |
| 指定推送配置 | 追加 `--push-config <路径>`（默认复用消息推送技能 config.json） |

**授权通道（自 3.1.7 起，仅当通道 A 不可用或想切换时用到）**

| 意图 | 命令 |
|---|---|
| 探测三通道可用性与推荐通道 | `python "<skill-dir>/scripts/buddy_station.py" --probe` |
| 强制指定通道 | 追加 `--channel auto\|local\|web\|rt`（`auto` 为默认，自动择优） |
| 通道 B · 生成浏览器授权片段 | `python "<skill-dir>/scripts/buddy_station.py" --snippet` |
| 通道 B · 用授权码完成授权 | 追加 `--setup-code <WBAUTH1:...>` |
| 通道 B · 用 cURL/HAR/文件导入会话 | 追加 `--setup-curl <文件>` / `--setup-har <文件>` / `--setup-file <文件>` |
| 通道 B · 清除已存凭据 | 追加 `--forget` |
| 通道 C · 导出长效 refresh_token | `python "<skill-dir>/scripts/buddy_station.py" --export-rt` |
| 通道 C · 用 refresh_token 完成配置 | 追加 `--setup-rt <token>` |
| 通道 C · 用文件导入 refresh_token | 追加 `--setup-rt-file <文件>` |
| 通道 C · 清除已存 refresh_token | 追加 `--forget-rt` |

> 典型场景：① Windows 机已登录 → 默认通道 A，零配置；② macOS / Linux 无法本地解密 → 跑一次 `--snippet` + `--setup-code` 走通道 B（凭据落盘后默认即用）；③ 想让脚本在客户端长期关闭时也能跑 → 跑一次 `--export-rt` + `--setup-rt` 落盘通道 C，此后无需客户端在线。

**参数说明**
- 位置参数 `checkin` 或选项 `--checkin`：进入自动签到分支（先查后签，幂等）。
- `--travel-auto`：执行派猫猫旅行全自动闭环（先领后派）。**这是唯一会触发旅行写操作的开关**；不加则旅行部分纯只读展示。
- `--location N`：指定派遣地点，`1`=咖啡馆、`2`=商场店铺、`3`=健身房、`4`=古镇客栈；缺省时随机。四个地点的时长与积分区间完全相同（随机 1-4 小时、5-10 积分），收益无差异。
- `--html`：生成桌面 HTML 报告（默认不生成）。
- `--json`：输出机器可读 JSON（不触发通知、不写 HTML），便于自动化读取。
- `--no-toast`：关闭系统通知。
- `--no-push`：关闭远程消息推送（桌面系统通知不受影响）。
- `--push-config <路径>`：指定推送配置文件路径；缺省复用消息推送技能 config.json（或环境变量 `WORKBUDDY_SCORE_PUSH_CONFIG`）。
- `--channel <auto|local|web|rt>`：强制指定授权通道；缺省 `auto` 自动择优（A 本机登录态 → B 网页会话 → C 长效令牌）。仅在多通道冲突或调试时用。
- `--probe`：仅探测三通道可用性与推荐通道，打印 JSON 后退出（不取数、不签到）。
- `--snippet`：生成通道 B 的浏览器授权片段（`templates/auth-snippet.js` 输出 `WBAUTH1:` 授权码），供 `--setup-code` 使用。
- `--setup-code <WBAUTH1:...>`：用授权码完成通道 B 授权并落盘 `~/.workbuddy/checkin-cred.json`（POSIX 0600）。
- `--setup-curl <文件>` / `--setup-har <文件>` / `--setup-file <文件>`：从 cURL 脚本 / HAR / 会话文件导入通道 B 会话。
- `--forget`：清除已存的通道 B 凭据（`checkin-cred.json`）。
- `--export-rt`：进入通道 C 流程，输出长效 `refresh_token` 供 `--setup-rt` 使用，并落盘 `~/.workbuddy/checkin-rt.json`。
- `--setup-rt <token>` / `--setup-rt-file <文件>`：用长效 refresh_token（或直接传文件）完成通道 C 配置。
- `--forget-rt`：清除已存的通道 C refresh_token（`checkin-rt.json`）。

**派猫猫旅行闭环逻辑（先领后派）**
1. 查询旅行状态 `state`：`idle` 空闲 / `traveling` 旅行中 / `arrived` 已到达待领取。
2. 若 `arrived` → 调用领取接口拿积分；领取后重新查询状态。
3. 若此时为 `idle` 且 `daily_limit_reached` 为假 → 派出；若已达每日上限 → 跳过并说明。
4. 若 `traveling` → 不派遣，仅展示到达倒计时。
- **不会丢积分**：到达后状态一直保持 `arrived` 等待领取，下次运行自动补领。
- **不会超发**：派出前必查 `daily_limit_reached`，达上限绝不发写请求。

---

## 五、消息推送（Push）

把每日积分播报自动分发到你自己的消息渠道（**配置即自动推送**；`--no-push` 整体关闭）。实现参照 totorosir-push-message（消息推送助手）技能：统一接口、本地配置、结果聚合、失败隔离。

**配置（凭据只存本地，绝不进日志 / 对话）**
- 默认读取消息推送技能的配置：`~/.workbuddy/totorosir-push-message/config.json`（**复用你已填的凭据，零重复配置**）。
- 覆盖优先级：`--push-config <路径>` > 环境变量 `WORKBUDDY_SCORE_PUSH_CONFIG` > 默认路径。
- 也可独立生成模板：`python scripts/push_notify.py sample-config --write`，再 `python scripts/push_notify.py check` 校验就绪渠道。

**支持渠道（12 个）**
钉钉 `dingtalk`、飞书 `feishu`、企业微信 `wecom`、微信 `wechat`(PushPlus)、邮件 `email`(SMTP)、短信 `sms`(付费)、QQ `qq`、Slack `slack`、Telegram `telegram`、Bark `bark`、通用 Webhook `webhook`、系统通知 `system`(零配置，写本地收件箱 + 尽力弹窗)。

**触发与降级**
- 非 `--json` 模式、且未加 `--no-push` 时，报告构建后自动推送到所有「已配置」渠道；桌面系统通知仍照常弹。
- 某渠道未配置 → 跳过并标注缺失字段，不中断。
- 某渠道发送失败 → 单记失败，不影响其他渠道。
- 短信为付费渠道，默认跳过，需显式 `confirm_paid` 才发（脚本默认不带此开关，故永自发短信）。
- 每渠道独立超时（默认 10s），无内置自动重试；凭据绝不出现在任何输出。
- 接口定义、配置 schema、签名与发送细节见 **`@references/push-spec.md`**。

## 六、输出处理规范

- **默认（非 `--json`）**：把文字播报回显到对话，并尝试弹一次跨平台系统通知（Windows 调同目录 `toast.ps1`、macOS 调 `osascript`、Linux 调 `notify-send`，失败静默降级）。
- **桌面 HTML**：仅当显式带 `--html` 才生成，且失败静默降级（stderr 提示，不影响主结果）。
- **绝不向对话输出 token**；控制台仅出现播报文本与 `[desktop]` 提示。
- **`--json` 模式**：原样回传 JSON（含 `total_credits`、`streak_days`、`checkin_dates`、`week_progress`、`activity`、`streak_schedule`、`remind_checkin`、`growth`、`travel`；签到模式另含 `checkin_result`），保持机器输出干净。
- **`travel` 字段要点**：`available` 标识旅行数据是否可取；`state` / `state_text` 旅行状态；`remaining_text` 到达倒计时；`reward_credit` 可领积分；`daily_limit_reached` 今日派遣是否达上限；`locations` 可选地点列表；`auto_log` 为 `--travel-auto` 的执行日志。

---

## 七、核心能力清单

| 能力 | 对应数据 / 接口 |
|---|---|
| 积分余额 | `total_credits`（状态查询接口） |
| 今日是否记录 | `today_checked_in`（完成一次对话即记录 1 天） |
| 连续登录天数 | `streak_days` |
| 当月活跃地图 | 当月已记录日期列表、累计记录天数 |
| 本周进度 | 本周 7 天记录情况 + 满勤缺口 |
| 本期活动 | 名称 / 状态 / 周期 / 剩余天数 / 全勤预估 / 领取状态 / 活动入口 |
| 连续奖励分档 | 7 / 14 / 28 天节点（积分 / 能量 / 补登卡 / 抽奖），含下一档缺口 |
| 记录催办 | 今日未记录时主动提醒「完成一次对话即可记录登录 1 天」 |
| 自动签到 | `POST /v2/billing/meter/daily-checkin`，幂等安全 |
| 成长计划（只读 GET） | 成长任务 / 进度 / 当前 Buddy（`/v2/activity/growth/*`，无需 Turing Shield 设备指纹） |
| 派猫猫旅行·只读 | 旅行状态 / 地点 / 到达倒计时 / 可领积分 / 可选地点 / 能量（`GET /activity/growth/buddy/travel/*`） |
| 派猫猫旅行·全自动 | 先领后派闭环（`--travel-auto` 显式触发），受每日上限保护 |
| 桌面展示 | 系统通知默认 + 可选桌面 HTML 报告 |
| 消息推送 | 发往用户自有渠道（钉钉/飞书/企业微信/邮件等 12 渠道，复用消息推送技能配置）；`--no-push` 可关 |
| 三通道授权 | 通道 A 本机登录态（零配置）、通道 B 一次性网页会话授权（macOS/Linux 无法本地解密时）、通道 C 长效令牌自续期（客户端可不开）；默认 `auto` 自动择优，凭据落盘 `~/.workbuddy/checkin-cred.json`（B）/ `checkin-rt.json`（C），POSIX 0600 |

> 成长计划与派猫猫旅行均已通过只读 GET 接口展示；仅「积分明细（流水）/ 兑换抽奖条件」两项暂不支持（见第十节）。

---

## 八、接口与实现参考

完整接口明细、登录态定位路径、字段映射、连续奖励分档表、派猫猫旅行接口族、签到幂等与错误处理，见 **`@references/api-spec.md`**。摘要：

- **登录态**：`CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info`（明文 JSON），取 `auth.accessToken` 与 `auth.domain`（`www.codebuddy.cn`）；认证头 `Authorization: Bearer <token>`。此即「通道 A · 本机登录态」，零配置、典型 Windows。
- **其他授权通道（自 3.1.7）**：当通道 A 不可用（如 macOS / Linux 本地无法解密）或希望客户端长期不在线时，改用通道 B（一次性网页会话，`scripts/web_auth.py` + `templates/auth-snippet.js`，凭据 `~/.workbuddy/checkin-cred.json`）或通道 C（长效 `refresh_token` 自续期，`scripts/rt_auth.py`，凭据 `~/.workbuddy/checkin-rt.json`），均由 `buddy_station.py` 的 `_resolve_auth` 统一接入，默认 `auto` 自动择优。
- **状态查询（只读）**：`POST /v2/billing/meter/checkin-activity-status`。
- **成长计划（只读 GET，无需 Turing Shield 设备指纹）**：`GET /v2/activity/growth/tasks`、`GET /v2/activity/growth/profile`、`GET /v2/activity/growth/buddy/info`；任一失败静默降级，不影响主结果。
- **派猫猫旅行（`/activity/growth/...`，注意无 `/v2` 前缀）**：
  - 只读：`GET /activity/growth/buddy/travel/status`、`GET /activity/growth/buddy/travel/config`、`GET /activity/growth/energy`、`GET /activity/growth/buddy/quota`
  - 写：`POST /activity/growth/buddy/travel/depart`（body `{"location_id": N}`）、`POST /activity/growth/buddy/travel/claim`（body `{}`）
  - 全部仅需 Bearer Token，**无需 Turing Shield 设备指纹**（已实测请求头中无 `x-device-token` / `x-sign`）。
- **自动签到（写，已验证）**：`POST /v2/billing/meter/daily-checkin`；当日已签返回 `code 10001 / "今天已签到"`，`data` 为 null——幂等，不重复发奖。

---

## 九、输出样例

各模式（查询 / 签到 / `--travel-auto` / `--html` / `--json`）的真实输出片段见 **`@references/examples.md`**。要点：

- 查询模式：文字播报含积分余额、今日记录、活跃地图、本周进度、连续奖励分档（含下一档缺口）、本期活动、领取状态、活动入口、成长计划、派猫猫旅行、记录催办、暂不支持项。
- 签到模式：先报告签到结果（成功 / 今日已签跳过 / 失败），再附全量播报。
- `--travel-auto` 模式：旅行区块下多出「自动执行」日志，逐条列出领取 / 派遣 / 跳过原因。
- `--json` 模式：结构化字段，`growth.available` 标识成长计划是否可取，`travel.available` 标识旅行数据是否可取。

---

## 十、限制与安全约束

**两项暂不支持（已确认无可用只读接口，绝不编造）**
- 积分明细（流水）：针对性探测与暴力探测均无可用接口，无只读来源。
- 兑换 / 抽奖条件：同上，无可用只读接口。
- 成长计划任务 / 进度 / 当前 Buddy、派猫猫旅行均已通过只读 GET 接口展示，**不再属于缺口**。

**安全约束（硬规则）**
- 只读登录态文件，绝不修改 / 删除 / 外传 `accessToken` / `refreshToken`；任何输出不含真实 token。
- 通道 B 凭据 `~/.workbuddy/checkin-cred.json` 与通道 C 凭据 `~/.workbuddy/checkin-rt.json` 均按 POSIX 0600 落盘（仅当前用户可读写），与登录态 token 同源隔离；授权码 / refresh_token 绝不回显到 stdout、日志或对话，仅由脚本内部读取。
- 写操作**仅**允许三个已验证接口：`daily-checkin`、`travel/claim`、`travel/depart`；绝不调用兑换 / 抽奖等其他写接口。
- 旅行写操作**必须**显式加 `--travel-auto` 才执行；且派出前必查 `daily_limit_reached`，达上限跳过、不发写请求。
- 桌面展示仅向 `~/Desktop/WorkBuddy积分播报.html` 写入报告、调用系统通知；不修改 / 删除其他文件。
- 消息推送仅发往用户本地配置中自有渠道（钉钉/邮件等），凭据取自本地 config.json，与 WorkBuddy 登录态 token 完全隔离；推送内容不含登录态 token，任一渠道失败不影响主结果。
- 定时任务用 WorkBuddy 自带自动化（recurring），无需 crontab / launchd。

**停止条件**
- 登录态缺失或 token 过期：停止，告知「请先登录 WorkBuddy」，不伪造。
- 接口返回非 0 / 非 200：停止并报告（旅行只读接口除外，失败静默降级不影响主结果）。
- 涉及「积分明细 / 兑换抽奖条件」的查询：如实标注「暂不支持」，绝不编造。
- 除上述三个写接口外的任何写接口调用请求：一律拒绝。

---

## 十一、排错速查

| 现象 | 处理 |
|---|---|
| 未找到登录态 | 先登录 WorkBuddy 客户端 |
| HTTP 404 | 域名错，应改用登录态 `auth.domain`；旅行接口另需确认路径**不带 `/v2` 前缀** |
| 签到 `code 10001` | 正常幂等返回，非失败 |
| 接口 500 / code 10001 空响应 | 活动期切换后端偶发，脚本重试 3 次，稍后重试 |
| 通知不弹 | 跨平台均失败静默降级；以桌面 HTML 为准；加 `--no-toast` 可彻底关 |
| 桌面 HTML 没生成 | 默认不生成，需带 `--html`；仍无则检查桌面目录可写 |
| 旅行区块不显示 | 旅行只读接口失败会静默降级；加 `--json` 看 `travel.available` |
| 「今日派遣：已达上限」 | 正常，服务端限制每天派遣次数，次日自动恢复 |
| 旅行中想提前召回 | 官方无召回接口，只能等到达后自动领取 |
| 推送未送达 / 报错 | 各渠道独立隔离；检查对应渠道配置（webhook / token 是否失效），用 `python scripts/push_notify.py check` 看就绪渠道；失败仅该渠道标红，不影响其他渠道与主结果 |
| 想关掉远程推送 | 加 `--no-push`（桌面通知仍弹）；彻底不发任何外部请求 |
| 通道 A 取不到 token / macOS 解密失败 | 先跑 `--probe` 看推荐通道；macOS / Linux 本地无法解密时改用通道 B：`--snippet` 取片段 → 浏览器授权 → `--setup-code`；或走通道 C `--export-rt` + `--setup-rt`（客户端可不开） |
| 通道 B 授权后报无效 / 会话绑定 | 授权码须携带原始 User-Agent；重跑 `--snippet` 取新片段，并在同一浏览器会话内完成授权，再 `--setup-code` |
| 通道 C `refresh_token` 过期 | 重跑 `--export-rt` 导出新的长效令牌并 `--setup-rt` 覆盖；旧 `checkin-rt.json` 自动自转更新 |
| 想切换通道 / 清除凭据 | 通道 B 用 `--forget`、通道 C 用 `--forget-rt`；清除后回退到通道 A 或重新授权 |

更多高频问题见 `@references/api-spec.md` 的「错误处理」与脚本内注释。

---

## 十二、版本说明

- 当前版本号：**3.1.7**。版本号遵循语义化版本规则 MAJOR.MINOR.PATCH；后续迭代请递增对应位（新增能力升 MINOR、缺陷修复升 PATCH、不兼容变更升 MAJOR），并同步修改本文件顶部 `version` 字段。
- **3.1.7（2026-09-22 镜像签到助手 3.1.7：三通道授权 B/C）**：参考签到助手 3.1.7，补齐「通道 B 一次性网页会话授权」与「通道 C 长效令牌自续期」两条授权链路，使积分助手在 macOS/Linux（本地登录态无法解密时）与「客户端长期不在线」场景下也能稳定取数、签到、跑旅行闭环。实现方式：镜像签到助手多脚本结构，新增 `scripts/web_auth.py`（通道 B，交互打开一次性授权页、生成 `WBAUTH1:` 授权码并落盘 `~/.workbuddy/checkin-cred.json`，POSIX 0600）、`scripts/rt_auth.py`（通道 C，落盘 `~/.workbuddy/checkin-rt.json`，每次运行用 `refresh_token` 换新 `accessToken` 并回写自转，客户端关闭仍可用）、`scripts/auto_checkin.py`（三通道探测与自动派发）、`scripts/push_message.py`（12 渠道推送，原样复用）、`templates/auth-snippet.js`（授权片段生成工具）；并把三条通道统一接入 `buddy_station.py` 的 `_resolve_auth(channel)`（通道 A 本机登录态 / B 网页会话 / C 长效令牌，默认 `auto` 自动择优）。新增 CLI：`--probe`（探测三通道可用性与推荐）、`--snippet` / `--setup-code` / `--setup-curl` / `--setup-har` / `--setup-file`（通道 B 授权）、`--export-rt` / `--setup-rt` / `--setup-rt-file` / `--forget-rt`（通道 C）、`--channel auto|local|web|rt`（强制指定通道）、`--forget`（清除通道 B 凭据）。文档同步补齐三通道说明、凭据文件位置与排错项。版本号由 3.1.4 升至 3.1.7（新增能力 = MINOR，与签到助手 3.1.7 对齐）。
- **3.1.4（2026-09-24 跨平台密钥获取：macOS / Linux 签到支持）**：参考签到助手 3.3.1，为积分助手补齐非 Windows 平台的 `atRestSecretKey` 获取能力，使其在 macOS / Linux 上同样能完成签到与旅行闭环。新增：① 平台分发 `_list_workbuddy_pids`（Windows 走 Toolhelp32、macOS 走 libproc 按可执行路径匹配、Linux 走 ps）；② macOS 主途径「经客户端 Node 调试端口（CDP）让客户端自己交出密钥」（`--inspect` 独立实例 + WebSocket Runtime.evaluate，取得后落盘 `~/.workbuddy/scripts/atrest.key` 供自动化复用）；③ macOS Keychain 兜底、macOS 离线核心转储兜底（Hardened Runtime 下实测不可用，仅保留）；④ Linux `/proc/<pid>/mem` 在线扫描兜底；⑤ 按平台的 `_discover_at_rest_key` 编排（环境变量 → 密钥文件 → 客户端密钥文件扫描 → Keychain → 进程内存 / CDP）。Windows 既有路径（含已修正的 7 字段 `MEMORY_BASIC_INFORMATION` 进程内存扫描）完整保留。版本号由 3.1.2 升至 3.1.4（跨平台兼容，按用户明确要求升版）。
- **3.1.2（2026-09-23 兼容增强：进程内存密钥发现 + ctypes 结构体修正）**：参考签到助手 3.1.2，为 5.6.2 的 `atRestSecretKey` 解密补充「第 4 级：扫描运行中 WorkBuddy.exe 进程内存」发现路径，使密钥只驻留内存、磁盘无落盘 DPAPI 文件时也能可靠定位并解密（此前仅依赖环境变量 / 密钥文件 / DPAPI blob 扫描，内存态密钥会解密失败）。同时修正移植代码中的 `MEMORY_BASIC_INFORMATION` ctypes 结构体对齐错误：原 9 字段误加两个填充字段，导致 `RegionSize` 偏移错位、`ReadProcessMemory` 触发段错误；改为官方 7 字段布局并补齐 `OpenProcess` 的 restype/argtypes。版本号由 3.1.1 升至 3.1.2（兼容增强 = PATCH）。
- **3.1.1（2026-09-22 文档完善）**：文档补充「关于作者」署名段（SKILL.md / 分享包 README / 使用说明同步，署名 TOTORO（totorosir）与公众号 龙猫科技说）；并对 SKILL.md frontmatter 长文本值统一加双引号，规避 YAML 解析报错。脚本逻辑与接口无变化。版本号由 3.1.0 升至 3.1.1（文档完善 = PATCH）。
- **3.1.0（2026-09-22 兼容 WorkBuddy 5.6.2+ 客户端）**：修复 WorkBuddy 客户端被强制更新到 5.6.2 后无法签到的问题。根因：5.6.2 把 AtRestEncryption 的 buildMode 由 disabled 改为 required，登录态 `workbuddy-desktop.info` 中的 `auth.accessToken`（及 refreshToken 等）由明文 JWT 变为 AES-256-GCM 信封 `{"$wbEncrypted":1,"envelope":"<base64>"}`，旧逻辑把信封当明文 JWT 读取 → 鉴权失败。本版本移植签到助手 3.1.0 同款「读端兼容解密」模块（自包含、纯标准库，含 cryptography/PyCryptodome/纯 Python 三重 AES-256-GCM 后端与 Windows DPAPI 自动定位 `atRestSecretKey`），`load_credentials` 现自动识别「明文 / 信封」两种情况：明文原样返回（旧客户端），信封则在本机当前用户下解密，绝不回写、绝不外传登录态。同时新增 `_check_expiry` 过期友好提示。版本号由 3.0.0 升至 3.1.0（兼容性缺陷修复 = PATCH）。
- **3.0.0（2026-09-17 新增消息推送能力）**：新增「消息推送」模块 `scripts/push_notify.py`（纯标准库，无第三方依赖），参照 totorosir-push-message 技能设计，把每日积分播报分发至用户自有消息渠道。支持 12 个渠道：钉钉、飞书、企业微信、微信(PushPlus)、邮件(SMTP)、短信(付费)、QQ、Slack、Telegram、Bark、通用 Webhook、系统通知(零配置)。默认复用消息推送技能 config.json（`~/.workbuddy/totorosir-push-message/config.json`），可通过 `--push-config` 或环境变量 `WORKBUDDY_SCORE_PUSH_CONFIG` 覆盖；非 `--json` 模式、未加 `--no-push` 时配置即自动推送，桌面通知保留；降级与边界：未配置跳过、失败隔离、10s 独立超时、无自动重试、付费渠道(短信)默认跳过、凭据绝不进输出。版本号由 2.1.0 升至 3.0.0（新增能力 = MAJOR）。
- **2.1.0（2026-09-10 新增派猫猫旅行）**：新增「派猫猫旅行」能力——只读展示旅行状态 / 地点 / 到达倒计时 / 可领积分 / 可选地点 / 能量，并支持 `--travel-auto` 全自动闭环（先领后派）、`--location N` 指定地点。新增接口族 `GET /activity/growth/buddy/travel/{status,config}`、`GET /activity/growth/energy`、`GET /activity/growth/buddy/quota`、`POST /activity/growth/buddy/travel/{depart,claim}`（注意**不带 `/v2` 前缀**，仅需 Bearer Token，无需 Turing Shield 设备指纹）。安全设计：旅行写操作仅在显式加 `--travel-auto` 时执行，派出前必查 `daily_limit_reached`。版本号由 2.0.0 升至 2.1.0（新增能力 = MINOR）。
- **2.0.0（2026-09-03 规范重建 + 分类调整）**：技能包按 WorkBuddy 开放平台规范全面重建（frontmatter 补齐 `description_zh` / `description_en` / `author` / `category` / `visibility` / `license` / `metadata.openclaw`，正文改为「技能指令正文」11 节，接口明细与输出样例拆入 `references/`）；`category` 由 `productivity` 调整为 `automation`；版本号由 1.1.0 升至 2.0.0（结构性重建属 MAJOR）。
- **1.0.4（2026-09-03 文档优化）**：文档重构（仅改 SKILL.md，脚本逻辑不变）——结构重排、新增输出样例与 FAQ、显式自证安全边界。
- **1.0.3（2026-09-03 新增）**：新增桌面展示（跨平台系统通知 + 可选桌面 HTML 报告）。
- **1.0.2（2026-09-01 新增）**：新增自动签到（`POST /v2/billing/meter/daily-checkin`，幂等）。
- **1.0.1（2026-09-01 整合更新）**：解析接口已返回字段，新增本地衍生洞察；连续奖励分档与官方一致（14 天为脚本推断）。
- **1.0.0（2026-08-28 初始发布）**：积分余额、连续登录天数、活跃地图、本周进度、本期活动、是否可领取。

## 关于作者

本技能由 TOTORO（totorosir）开发维护；相关更新、用法答疑与实战笔记发布于公众号 龙猫科技说。

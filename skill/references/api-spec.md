# 接口与实现参考（WorkBuddy积分助手）

本文件为 `scripts/buddy_station.py` 的接口与字段约定，供 AI 执行技能时作为上下文。所有请求均基于本机登录态 `auth.domain` + `Bearer` Token，纯标准库 `urllib` 实现，无第三方依赖。

> **例外**：派猫猫旅行接口不走 `auth.domain`，详见第 6 节的「域名与路径」说明。

---

## 1. 登录态定位

登录态为明文 JSON 文件，脚本按操作系统自动探测（顺序候选）：

| 系统 | 路径 |
|---|---|
| Windows | `%LOCALAPPDATA%` / `%APPDATA%` 下 `CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| macOS | `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| Linux | `~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info` |
| 兜底 | `~/.workbuddy/auth/workbuddy-desktop.info` |

文件关键字段：
- `auth.accessToken`：JWT，作为 `Authorization: Bearer <token>` 头。
- `auth.domain`：API 域名，实测值 `www.codebuddy.cn`（**必须用登录态域名，不要硬编码**）。
- `auth.tokenType`：通常为 `Bearer`。

> 读登录态全程不打印 token；缺失 `accessToken` / `domain` 即抛错停止。

---

## 2. 认证方式

```
Authorization: Bearer <auth.accessToken>
Content-Type: application/json
```

除派猫猫旅行外，所有接口 base = `https://<auth.domain>`。

---

## 3. 接口清单

| 方法 | 路径 | 用途 | 关键返回字段 | 备注 |
|---|---|---|---|---|
| POST | `/v2/billing/meter/checkin-activity-status` | 状态查询（只读） | `total_credits`、`streak_days`、`today_checked_in`、`checkin_dates`、`week_progress` / `week_checkin_days`、`theme_name` / `season` / `activity_name` / `start_time` / `end_time`、`claim_button_text` / `action_button`、`daily_credit`、`today_credit`、`is_streak_day`、`next_streak_day`、`streak_bonus_days`、`streak_bonus_credit`、`active` | 主查询接口 |
| GET | `/v2/activity/growth/tasks` | 成长任务列表（只读） | `data.tasks[]`：`title` / `reward_credit` / `status` | 无需 Turing Shield 设备指纹 |
| GET | `/v2/activity/growth/profile` | 成长进度（只读） | `data`：`level` / `completed` / `total` | 无需 Turing Shield 设备指纹 |
| GET | `/v2/activity/growth/buddy/info` | 当前 Buddy（只读） | `data.buddy`：`name` / `rarity` | 无需 Turing Shield 设备指纹 |
| POST | `/v2/billing/meter/daily-checkin` | 自动签到（写） | `code 0` 成功 / `code 10001`「今天已签到」 | 幂等，已验证 |

派猫猫旅行接口族见**第 6 节**（域名与路径规则不同，单独列出）。

返回结构统一为 `{code:0, data:...}`（GET 成长接口）或 `{code, msg, data}`（状态 / 签到）。非 200 / 非 0 码按错误处理。

---

## 4. 状态查询字段映射

`fetch_status` → `build_report` 解析的字段：

- `total_credits`：积分余额（总积分）。
- `streak_days`：连续登录天数。
- `today_checked_in`：今日是否记录（完成一次对话即记 1 天登录）。
- `checkin_dates`：历史已记录日期列表（ISO 字符串）；脚本筛出本月部分作为活跃地图。
- `week_progress`：本周 7 天布尔数组；`week_checkin_days` 为补充。
- `theme_name` / `season` / `activity_name` / `start_time` / `end_time` / `active`：本期活动信息。
- `claim_button_text` / `action_button`：领取状态文案与活动入口（`action_button.show` / `text` / `action` URL）。
- `daily_credit` / `today_credit`：每日可得 / 今日可得积分。
- `is_streak_day` / `next_streak_day` / `streak_bonus_*`：连续奖励节点与累计奖励。

**本地衍生洞察**（脚本计算，非接口直接返回）：活动状态（未开始 / 进行中 / 已结束）、剩余天数、全勤预估、连续里程碑缺口、本周满勤缺口、下一档连续奖励缺口、记录催办（`remind_checkin = not today_checked_in`）。

---

## 5. 成长计划字段映射（`parse_growth`）

| 接口 | 原始字段 | 归一化字段 | 兜底 |
|---|---|---|---|
| `tasks` | `title` / `reward_credit` / `status` | `title` / `reward_credit` / `status` | `name` / `credit` / `state` |
| `profile` | `level` / `completed` / `total` | `level` / `progress` / `total` | `progress`/`current_step` / `total_step` |
| `buddy` | `name` / `rarity` | `name` / `tier` | `buddy_name` / `tier`/`level`/`grade` |

`available = True` 当 tasks / profile / buddy 任一非空。字段缺失填 `None`，**绝不编造**。

---

## 6. 派猫猫旅行（Buddy Travel）

### 6.1 域名与路径（关键差异）

- **域名**：统一走 `https://www.workbuddy.cn`（脚本常量 `TRAVEL_DOMAIN`），**不**使用登录态的 `auth.domain`。
- **路径无 `/v2` 前缀**：是 `/activity/growth/...` 而非 `/v2/activity/growth/...`。这是此前按 `/v2/` 前缀探测全部 404 的根本原因。
- **无需 Turing Shield**：全部接口仅需 `Authorization: Bearer <token>`。浏览器实测请求头中无 `x-device-token` / `x-sign` 等签名头（仅 cookie 会话），Bearer Token 实测同样返回 `code 0`。

### 6.2 接口族

| 方法 | 路径 | 用途 | 关键返回字段 |
|---|---|---|---|
| GET | `/activity/growth/buddy/travel/status` | 旅行状态（只读） | `state`、`location{id,code,name,duration_hours}`、`depart_at` / `arrive_at` / `server_now`、`reward_credit`、`daily_limit_reached`、`record_id`、`buddy_id` |
| GET | `/activity/growth/buddy/travel/config` | 地点配置（只读） | `locations[]{id,code,name,description,duration_hours_min,duration_hours_max,reward_credit_min,reward_credit_max,sort}` |
| GET | `/activity/growth/energy` | 能量余额（只读） | `balance` / `total_consumed` / `total_earned` |
| GET | `/activity/growth/buddy/quota` | Buddy 开启配额（只读） | `affordable` / `balance` / `cost_per_open` / `max_open_count` |
| GET | `/activity/growth/buddy/travel/records` | 旅行历史（只读） | `records[]`、`total`、`total_page`（实测可用，当前未启用） |
| POST | `/activity/growth/buddy/travel/depart` | **派出（写）** | `state` / `record_id` / `location` / `arrive_at` / `daily_limit_reached` |
| POST | `/activity/growth/buddy/travel/claim` | **领取（写）** | `state`（领取后为 `idle`）/ `reward_credit` / `letter` |

### 6.3 旅行状态机

`state` 取值与含义：

| state | 含义 | 可行动作 |
|---|---|---|
| `idle` | 空闲（当前无旅行） | 若 `daily_limit_reached` 为假 → 可派出 |
| `traveling` | 旅行中 | 无，等待到达（脚本展示倒计时） |
| `arrived` | 已到达，待领取 | 调用 `claim` 领取积分 |

- **不会丢积分**：到达后状态一直保持 `arrived` 等待领取，下次运行会先补领再派遣。
- **每日上限**：`daily_limit_reached` 为 `true` 时不可再派遣（实测约每天 1 次，次日自动恢复）。
- **能量与旅行无关**：`energy.balance` 与 `quota.cost_per_open`（=10）用于「开启 Buddy 盲盒」，**旅行派遣不消耗能量**。

### 6.4 地点配置（实测 4 个地点）

| id | code | 名称 | 时长 | 积分 |
|---|---|---|---|---|
| 1 | `coffee` | 咖啡馆 | 1-4 小时（随机） | 5-10（随机） |
| 2 | `mall` | 商场店铺 | 1-4 小时（随机） | 5-10（随机） |
| 3 | `gym` | 健身房 | 1-4 小时（随机） | 5-10（随机） |
| 4 | `ancient_town` | 古镇客栈 | 1-4 小时（随机） | 5-10（随机） |

**四个地点的时长与积分区间完全相同，收益无差异**——随机选择即可，无需策略择优。脚本通过 `--location N` 支持指定，缺省随机。

### 6.5 字段映射（`parse_travel`）

| 原始字段 | 归一化字段 | 说明 |
|---|---|---|
| `state` | `state` / `state_text` | 状态原文与中文映射（空闲 / 旅行中 / 已到达） |
| `location.name` | `location_name` | 当前地点名 |
| `arrive_at - server_now` | `remaining_seconds` / `remaining_text` | 仅 `traveling` 时计算到达倒计时 |
| `reward_credit` | `reward_credit` | 可领积分（`arrived` 时有值） |
| `daily_limit_reached` | `daily_limit_reached` | 今日派遣是否达上限 |
| `energy.balance` | `energy` | 能量余额 |
| `config.locations[]` | `locations[]` | 可选地点列表（id / code / name / 时长与积分上下限） |

`available = True` 当 status 或 locations 任一非空。字段缺失填 `None`，**绝不编造**。

### 6.6 全自动闭环逻辑（`travel_auto`，先领后派）

```
1. GET status  → 取 state 与 daily_limit_reached
2. 若 state == "arrived"  → POST claim {}（领取积分）→ 重新 GET status
3. 若此时 state == "idle"：
     - daily_limit_reached == true  → 跳过派遣（不发写请求），日志说明
     - 否则                          → POST depart {"location_id": N}（N 缺省随机）
4. 若 state == "traveling" → 不派遣，仅展示倒计时
```

**安全约束**
- 旅行写操作**仅在显式加 `--travel-auto` 时**执行；默认运行纯只读展示。
- 派出前必查 `daily_limit_reached`，达上限绝不发写请求。
- 领取是纯收益操作，但同样只在 `--travel-auto` 下触发。

### 6.7 报文示例（已脱敏，实测抓包）

派出：
```
POST /activity/growth/buddy/travel/depart
{"location_id": 1}

{"code":0,"data":{"state":"traveling","record_id":4275477,
 "location":{"id":1,"code":"coffee","name":"咖啡馆","duration_hours":4},
 "depart_at":1789005652,"arrive_at":1789020052,
 "daily_limit_reached":false,"reward_credit":0}}
```

领取：
```
POST /activity/growth/buddy/travel/claim
{}

{"code":0,"data":{"state":"idle","record_id":4245471,"reward_credit":9}}
```

---

## 7. 连续奖励分档（`STREAK_TIERS`，与官方页面节点一致）

| 节点 | 积分 | 能量 | 补登卡 | 抽奖 | 类型 | 来源 |
|---|---|---|---|---|---|---|
| 7 天 | +50 | +3 | +1 | +1 | 累计发放 | 官方页面 tooltip ✓ |
| 14 天 | +100 | +4 | +1 | +1 | 累计发放 | 脚本推断·待校准 ⚠️ |
| 28 天 | +150 | +5 | +1 | +1 | 主动兑换 | 官方页面 tooltip ✓ |

- 接口仅返回「下一次连续奖励节点天数」`next_streak_day` 与「连续奖励累计积分」`streak_bonus_credit`；分档表为脚本内置映射，用于本地化呈现各档奖励与下一档缺口。
- **14 天节点为脚本推断**（截图未截到 tooltip）。有官方数值发我，直接改 `STREAK_TIERS`（仅改数据，不动逻辑）。

---

## 8. 自动签到（幂等）

`do_checkin` 流程：
1. 先 `fetch_status` 查 `today_checked_in`；已签 → 返回 `skipped`（「今日已签」，跳过写请求）。
2. 未签 → `POST /v2/billing/meter/daily-checkin`。
   - 成功：`HTTP 200 / code 0`，当日记录 +1。
   - 当日已签：`HTTP 400 / code 10001 / "今天已签到，请明天再来"`，`data` 为 null——**幂等，不重复发奖**。
3. 失败返回 `failed`，脚本退出码 1；`skipped` / `checked_in` 视为成功（退出码 0）。

**绝不调用**兑换 / 抽奖等其他写接口。

---

## 9. 错误处理与重试

- 网络 / HTTP 异常：`api_call` 捕获并返回 `(status, body)`，不抛出未处理异常。
- 状态查询失败（非 200 / 非 0）：抛 `RuntimeError` 并停止。
- 活动期切换后端偶发空响应（HTTP 500 / code 10001）：脚本重试 3 次后仍失败则如实报错，不伪造数据。
- 成长计划任一 GET 失败 / 非 200 / 非 0 码：静默跳过（`fetch_growth` 内 `except pass`），不影响主结果。
- 派猫猫旅行任一**只读** GET 失败：静默降级为 `None`（`_travel_get` 内 `except` 返回 `None`），`travel.available` 为假，不影响主结果。
- 派猫猫旅行**写**操作失败：如实上报 `action: failed` 与服务端 `msg`，写入 `auto_log` 展示给用户，不做静默吞掉。

---

## 10. 安全约束（硬规则）

- 只读登录态文件，绝不修改 / 删除 / 外传 `accessToken` / `refreshToken`；任何输出不含真实 token。
- **写操作仅允许三个已验证接口**：
  1. `POST /v2/billing/meter/daily-checkin`（签到，幂等）
  2. `POST /activity/growth/buddy/travel/claim`（领取旅行奖励）
  3. `POST /activity/growth/buddy/travel/depart`（派出 Buddy 旅行）
- 旅行写操作**必须**显式加 `--travel-auto` 才执行；派出前必查 `daily_limit_reached`，达上限跳过。
- 绝不调用兑换 / 抽奖等其他写接口。
- 桌面展示仅写 `~/Desktop/WorkBuddy积分播报.html` 与弹系统通知；不修改 / 删除其他文件。
- 定时任务用 WorkBuddy 自带自动化（recurring），无需 crontab / launchd。

---

## 11. 运行环境

- 仅 Python 3.8+ 标准库（`urllib` / `json` / `os` / `sys` / `platform` / `random` / `shutil` / `subprocess`）。
- 优先 `python3`；未安装可用 WorkBuddy 托管 Python：
  - Windows：`~/.workbuddy/binaries/python/versions/*/python.exe`
  - macOS / Linux：`~/.workbuddy/binaries/python/versions/*/python`
- 退出码：成功 0 / 失败 1（签到失败才返回 1，已签属成功）。

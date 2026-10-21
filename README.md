# workbuddy-daily-checkin

自动领取 WorkBuddy「Buddy 加油站」每日积分的小工具 —— **走接口，不依赖界面**。

> 每天签到 100 分 + 派猫猫旅行 5-10 分，按日发放、**不补发**。
> 本工具在 07:00 领一次、07:30 再核验一次，没到账就自动修复重试。

## 适用版本

| 方式 | 支持的 WorkBuddy 版本 | 依据 |
|---|---|---|
| **接口方式**（本仓库当前实现） | **5.6.2 及以上** | 5.6.2 起 `accessToken` 改为 AES-256-GCM 信封，`skill/` 自带解密；**已在 5.8.3 实测通过** ✅ |
| 界面方式（`checkin_cdp.py`，仅本地保留） | **≤ 5.5.x** | 5.5.4 / 5.5.6 实测可用；**5.8.1 起签到面板改为轮播气泡，已确认失效** ❌ |

补充说明：

- **5.6.2 之前的客户端**登录态是明文 token，`skill/` 声称能自动识别明文，但本仓库**未实测**
- **5.6.2 ~ 5.8.0** 之间界面是否仍可点，**未验证**（本项目实测点只有 5.5.4 / 5.5.6 / 5.8.1 / 5.8.3）
- 查看本机版本：WorkBuddy 菜单「帮助 → 关于」，或读注册表
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\WorkBuddy` 的 `DisplayVersion`

> 判断准则：**能取到登录态就走接口，与界面长什么样无关**。
> 界面方式只在 WorkBuddy 5.5.x 及更早的旧客户端上有意义。

## 为什么不用界面点击了

早期版本通过 CDP（Chrome DevTools Protocol）驱动 WorkBuddy 界面点「立即领取」。
**WorkBuddy 5.8.1 起，签到面板改成了轮播气泡**，`section.fuel-card` 等固定 DOM 全部消失，
按钮既读不到状态、也点不到 —— 实测已完全失效（按钮找不到、状态识别失效、面板打不开）。

现在改为调 WorkBuddy 自己的接口，好处是：

- 不受界面改版影响
- **WorkBuddy 不需要带 `--remote-debugging-port` 启动**（普通方式启动即可）
- 判定更硬：接口回报 + 单调计数器双证据

> ⚠️ **但仍需要 WorkBuddy 客户端进程在运行**：5.6.2+ 的 `accessToken` 是 AES 信封，
> 解封密钥只随客户端进程存活。进程不在时，磁盘上的旧信封解不开，三个授权通道全部失效
> —— 报错是「无法取得任何可用凭据」，属**凭据未就绪，不是网络故障**。

## 工作方式

```
07:00  Windows 计划任务  →  checkin_guard.py          →  接口签到（含就绪等待）
07:30  WorkBuddy 定时任务 →  checkin_guard.py --final  →  核验/修复 + 领旅行积分
```

- **07:00** 跑在操作系统层（不依赖 WorkBuddy 的定时任务机制），但需要客户端进程在运行
- **07:00 那次会"等"**：唤醒后网络/代理/登录态往往还没就绪，脚本在同一任务内按
  **2 → 3 → 5 → 5 → 7 → 8 → 8 分钟**递增重试（间隔累计 38 分钟，窗口上限 40 分钟，
  最后一次尝试约在 07:38）。
  **窗口必须跨过 07:30** —— 实测客户端在 07:30:32 启动、07:30:36 重写登录态文件，
  等待窗口若停在 07:22 就注定白等。用 `--wait-minutes` 可调整，传 `0` 关闭
- **07:30** 由 WorkBuddy 的定时任务调度；`--final` 表示当天最后一次检查，**不等待**、
  失败立即升级告警（07:00 那次失败还有补救机会，不该惊动你）
- 两处都是**幂等**的：当天已确认到账就毫秒级退出

## 怎么判断"真的到账了"

不靠界面文案猜，用两条独立证据：

| 证据 | 内容 |
|---|---|
| 接口回报 | `daily-checkin` 返回「今日已签到 / 签到成功」（当日已签会返回 `code 10001`，属正常） |
| 计数增长 | 本期「累计领取积分」比历史基线增长（单调计数器，说明当天确实入账） |

两条都成立 → 置信度 `high`；只有计数增长 → `medium`；都没有 → 未到账，触发领取。

> 期数切换（如第 10 期 → 第 11 期）会让计数器归零，此时自动识别为 `period_reset`，
> **弃用计数证据、只认接口回报**，不会误判成丢分。

## 环境要求

- Windows 10/11
- Python 3.9+
- **无第三方依赖**（全部标准库）；只有「注册计划任务」需要 `pywin32`

  ```
  pip install pywin32
  ```

- 已登录过 WorkBuddy 桌面版（登录态文件需存在）

## 快速开始

```bat
:: 1. 注册每天 07:00 的签到任务（pywin32，支持电池供电 + 错过补跑）
python register_windows_task.py --at 07:00 --script checkin_guard.py

:: 2. 手动验证一次
python checkin_guard.py --force
```

## 命令行

```bat
python checkin_guard.py            :: 执行一次（幂等；已到账则秒退；默认含就绪等待）
python checkin_guard.py --status   :: 只读状态：今天是否到账、最近几天、实时面板
python checkin_guard.py --force    :: 忽略「今日已确认」，强制重新核验（不等待）
python checkin_guard.py --final    :: 当天最后一次检查，失败立即告警（不等待）
python checkin_guard.py --wait-minutes 40   :: 就绪等待窗口（分钟），传 0 关闭
python register_windows_task.py --at 07:00 --script checkin_guard.py
python register_windows_task.py --delete          :: 删除任务
```

## 目录结构

```
checkin_guard.py           签到 + 核验 + 告警（唯一主脚本）
register_windows_task.py   注册 Windows 计划任务
push_github.py             本仓库的推送工具（Git Data API，仅开发用）
skill/                     第三方技能「WorkBuddy积分助手」（MIT-0，见 skill/NOTICE.md）
state.json                 每日核验记录与基线（运行时生成，不入库）
logs/                      日志与告警（运行时生成，不入库）
```

## 网络说明（重要，实测 2026-10-12）

本机到 `www.codebuddy.cn` 的**直连 TLS 不稳定**，但**走本机代理反而更稳**：

| 模式 | 成功率 | 平均耗时 |
|---|---|---|
| 走代理 `127.0.0.1:6664` | **6/6 (100%)** | 10.7s |
| 直连（清空 `*_proxy`） | 4/6 (67%) | 24.5s |

所以脚本**沿用环境变量里的代理**，请**不要**为了"绕开代理"而清空 `*_proxy`。
另：`api.github.com` 两者无差异（各 4/4），推送工具不受影响。

脚本只对**网络类**报错重试（`HTTP 0` / `SSL` / `EOF` / `timeout` / `handshake`，
最多 3 次）；登录态失效、`403` 等**不重试**。

## 故障排查

| 现象 | 处理 |
|---|---|
| `RESULT: OK` / `ALREADY_VERIFIED` | 正常，当天已到账 |
| 报「无法取得任何可用凭据（通道 A/B/C 均不可用）」 | **凭据未就绪，不是网络故障**。多见于 07:30 之前或电脑刚唤醒。脚本会自动等到 07:38；若持续出现，确认 WorkBuddy 客户端是否在运行。**不要动登录凭据** |
| `RESULT: FAILED` + 网络类报错 | 外网/代理抖动。先探连通性（走代理），恢复后重跑；**不要动登录凭据** |
| 报「登录态失效」/ `403` | 打开 WorkBuddy 重新登录一次 |
| 技能报接口 404 / 字段缺失 | 官方接口变更，需更新 `skill/`（见 `skill/NOTICE.md`） |
| 连续多天没到账但脚本没报错 | 看 `logs/guard_YYYYMMDD.log` 里 `[API] 判定` 一行，再查 `state.json` |
| 想看完整积分播报 | `python skill/scripts/buddy_station.py --no-push` |

## 派猫猫旅行

每天可领 5-10 分（**派遣上限约 1 次/天**，多跑无收益）。到达后奖励不会过期，
所以每天 07:30 跑一次「领上一趟 + 派下一趟」即可：

```bat
python skill/scripts/buddy_station.py --travel-auto --no-push
```

`--no-push` 表示只领取、不向外部渠道推送播报。

## 免责声明

- 本工具仅用于**自己的账号**、自己的设备，请遵守目标应用的用户协议与平台规则
- `skill/` 为第三方作品（MIT-0），版权归原作者 TOTORO（totorosir）所有
- 使用本工具产生的一切后果由使用者自行承担

## 许可证

本项目代码：[MIT](./LICENSE)
收录的第三方技能：MIT-0（见 [skill/NOTICE.md](./skill/NOTICE.md)）

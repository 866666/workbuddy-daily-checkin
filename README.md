# workbuddy-daily-checkin

通过 **CDP（Chrome DevTools Protocol）** 自动完成 WorkBuddy 每日签到领积分的小工具。

> WorkBuddy 基于 Electron/Chromium。只要它以 `--remote-debugging-port=9222` 启动，
> 脚本就能通过本机调试端口在页面里找到签到按钮替你点掉——全程只连 `127.0.0.1`，
> 不把任何数据发给第三方。

## ✨ 快速开始（推荐）

只需运行一次配置向导，之后完全不用管：

```bat
pip install -r requirements.txt
python checkin_cdp.py --setup
```

向导会依次自动完成：

1. **启动 WorkBuddy** —— 自动定位安装位置，以调试端口模式拉起；若 WorkBuddy 已在运行但未带
   调试端口，会询问后自动重启（调试参数只在启动时生效）
2. **登录引导** —— 未登录时提示你在弹出的 WorkBuddy 窗口完成扫码/登录，脚本检测到登录后自动继续
3. **自动生成配置** —— 从页面自动提取你的昵称写入 `config.json`，无需手填；自动校验签到按钮文案
4. **注册每日计划任务** —— 确认后注册两个任务：主签到（默认每天 07:00 静默签到，
   **错过触发会自动补跑**，支持电池供电）+ 签到守卫（主签到之后核验并自动修复，见下文）

跑完这 5 步，每天自动签到。**默认不自动拉起/重启 WorkBuddy**——适合 WorkBuddy 常驻运行
的场景；若需要"发现没开就自动拉起"，把 `config.json` 的 `auto_launch` 设为 `true` 即可。

手动补签 / 自检：`python checkin_cdp.py`（今日已领时自动识别并正常退出）。
连不上调试服务时禁用自动拉起：`python checkin_cdp.py --no-auto-launch`。

## 特性

- 🖱 自动查找并点击签到按钮（穿透 shadow DOM），点击后自动校验是否生效；今日已领自动识别
- 🚀 **可选自动拉起**：默认关闭（适合 WorkBuddy 常驻场景）；`config.json` 的 `auto_launch: true`
  可开启"没运行就自动启动（带调试端口）"
- 🔌 端口自适应：固定端口被占时自动扫描本机 CDP 端口（仅接受目标应用）
- 🌙 跨午夜自动刷新页面，避免界面仍显示"昨日已领"
- ⚙️ 按钮文案 / 昵称 / 跳过端口全部可配置，应用改版时改 `config.json` 即可
- 📋 日志落盘 `logs/checkin_YYYYMMDD.log`，静默运行异常也会写 crash 日志
- 🗓 内置 Windows 计划任务注册，每日无人值守自动签到
- 🔍 **签到守卫**（`checkin_guard.py`）：主签到之后再核验一次，用「按钮状态 + 累计积分计数器」
  双证据确认当天真的到账；没到账就自动排查、修复、重试。签到失败不再静默丢失

## 环境要求

- Windows 10/11
- Python 3.9+（唯一必需依赖是 `websocket-client`；HTTP 探测/CDP 列表用标准库实现，
  不依赖 `requests`。自动拉起/进程管理建议安装 `psutil`、计划任务完整功能建议安装
  `pywin32`——缺省时均有降级路径）
- 已安装 WorkBuddy 桌面版

## 手动配置（可选）

`--setup` 会自动生成 `config.json`；如需手动调整，可复制模板：

```bat
copy config.example.json config.json
```

| 字段 | 说明 | 默认 |
|---|---|---|
| `debug_port` | 目标应用 CDP 调试端口 | `9222` |
| `claim_text` | 签到按钮文案（应用改版后改这里） | `立即领取` |
| `nickname` | 主界面左下角用户显示名；留空则跳过"点头像打开面板"的兜底步骤 | `""` |
| `skip_ports` | 自动扫描端口时要跳过的端口（本机有其他调试服务时） | `[]` |
| `workbuddy_exe` | WorkBuddy 可执行文件路径；`auto` 为自动定位 | `"auto"` |
| `auto_launch` | 连不上调试服务时是否自动拉起/重启 WorkBuddy（常驻场景建议 `false`） | `false` |

配置优先级：内置默认 < 同目录 `config.json` < `--config` 指定文件 < 命令行参数。
`config.json` 已在 `.gitignore` 中，含个人昵称也不会误提交。

## 命令行

```bat
python checkin_cdp.py --setup            :: 一键配置向导（首次）
python checkin_cdp.py                    :: 正常签到（默认不拉起；auto_launch=true 时连不上会自动拉起）
python checkin_cdp.py --dry              :: 只检测按钮，不点击
python checkin_cdp.py --info             :: 打印端口与页面列表（先自检用这个）
python checkin_cdp.py --no-auto-launch   :: 强制禁用自动拉起，连不上直接报错
python checkin_cdp.py --nickname 我的名字
python checkin_cdp.py --claim-text 签到有礼
```

### 让 WorkBuddy 带调试端口启动（若不用 --setup 的手动方式）

方法 A —— 修改快捷方式参数：右键 WorkBuddy 快捷方式 → 属性 → 目标末尾追加：

```
"C:\...\WorkBuddy.exe" --remote-debugging-port=9222
```

方法 B —— 用启动器（免改快捷方式，无窗口启动）。新建 `start_hidden.vbs`（保持纯 ASCII 保存）：

```vbs
Set ws = CreateObject("WScript.Shell")
ws.Run """C:\Program Files\WorkBuddy\WorkBuddy.exe"" --remote-debugging-port=9222", 0, False
```

之后用这个 vbs 启动 WorkBuddy。**注意：调试参数只在启动时生效，普通方式重启后端口会消失。**
（`--setup` / 自动拉起已内置此逻辑，手动方式仅在你想自己控制启动时使用。）

## 每日自动运行（Windows 计划任务）

```bat
python register_windows_task.py --at 07:00
```

- 优先走 pywin32 COM 注册，支持**电池供电运行**和**错过触发后开机补跑**（`StartWhenAvailable`）
- 未装 pywin32 自动退回 `schtasks`（仅基础每日触发）
- 通过 `pythonw.exe` 静默运行，无控制台窗口
- 删除任务：`python register_windows_task.py --delete`

> 笔记本用户注意：若电脑在触发时间处于睡眠/关机，`StartWhenAvailable` 会让任务在
> 下次开机时自动补跑（需 pywin32 注册方式）。普通 `schtasks` 错过了就错过了。

## 🔍 签到守卫：确保当天积分不漏领

主签到每天只出手一次，**一旦这次失败，当天积分就白丢**（积分按日发放、不补发）。
`checkin_guard.py` 负责在主签到之后再核验一次，未到账则自动修复重取。

```bat
python checkin_guard.py            :: 执行一次守卫（建议在主签到后 30 分钟）
python checkin_guard.py --status   :: 只读状态：今天是否到账、历史几天、实时面板
python checkin_guard.py --force    :: 忽略「今日已确认」，强制重新核验
```

**怎么判断"真的到账了"**——不靠背景文案猜，用两条独立证据：

| 证据 | 内容 |
|---|---|
| 状态证据 | 签到主按钮文案「今日已领」且 `disabled=true` |
| 计数证据 | 面板「累计领取 N 分」比历史基线增长（单调计数器，说明当天确实入账） |

两条都成立 → 置信度 `high`；只有一条 → `medium`（记录并留待复核）；
都没有 → 判定未到账，自动执行领取。期数切换导致计数器归零时会自动识别，
弃用计数证据、只认状态证据并标记 `period_reset`。

失败时的升级阶梯：

```
L1 依赖自检 → 坏了就 pip 自愈
L2 CDP 端口 → 失败则强制全端口重扫
L3 面板打不开 → 点头像卡片 → 「Buddy加油站」；仍不行 → 刷新页面后重试
L4a 轻量领取（不刷新页面，不打扰正在使用 WorkBuddy 的你）
L4b 仍不行 → 刷新后重试
仍失败 → 写 logs/GUARD_ALERT_<日期>.txt + Windows 通知
```

每日核验结果与基线记录在 `state.json`（已在 `.gitignore`）。

> 调度建议：本工具自身只提供"一次守卫"的入口，**用哪个调度器由你决定**——
> Windows 计划任务（见上节）、WorkBuddy 自身定时任务，或任何能定时跑命令的机制都行。
> 典型配置就是「主签到 07:00 + 守卫 07:30」。

### 注册守卫计划任务（可选）

```bat
python register_windows_task.py --guard                       :: 一天多个检查点
python register_windows_task.py --guard --times 08:00,20:00   :: 自定义检查点
python register_windows_task.py --delete-all                  :: 删除主签到与守卫任务
```

## 故障排查

| 现象 | 处理 |
|---|---|
| `RESULT: NO_CDP` | WorkBuddy 没带 `--remote-debugging-port` 启动。先跑 `--setup` 一键修复，或查 `--info` |
| `RESULT: NO_BUTTON` | 今日已领（正常）；或应用 UI 改版导致按钮文案变化——改 `config.json` 的 `claim_text` |
| `未找到「XX」头像元素` | `nickname` 填错，或该版本无需点头像（可留空） |
| `RESULT: DEP_FAIL` | `websocket-client` 被清理工具破坏（包目录残留、缺 `create_connection`）。脚本会自动重装；失败时手动 `pip install --force-reinstall websocket-client` |
| 脚本误报「端口无调试服务」 | 同上：第三方依赖损坏会被静默吞掉。现已改用标准库 + 启动依赖自检，日志里会打印「依赖自检」一行 |
| 向导等待登录时卡住 | 确认 WorkBuddy 窗口已弹出并完成登录；超时 5 分钟可重跑 `--setup` |
| 找不到 WorkBuddy 安装位置 | 在 `config.json` 的 `workbuddy_exe` 填完整路径 |
| 守卫报「无法读取签到面板」 | 面板结构可能改版。用 `python archive/probe_fuel_panel.py` / `probe_fuel_panel2.py` 重新探测真实 DOM，再更新 `checkin_cdp.py` 里的选择器 |
| 守卫报「累计积分下降 / period_reset」 | 加油站进入新一期、计数器归零，属正常；此时只认按钮状态证据 |
| 连续多天没领到但脚本没报错 | 典型的静默失败（依赖被清理工具破坏）。跑 `python checkin_guard.py --status` 看真实状态，日志里查「依赖自检」一行 |

## 免责声明

- 本工具仅用于**自己的账号**、自己的设备，请遵守目标应用的用户协议与平台规则
- 目标应用界面改版可能导致脚本失效，请及时更新 `claim_text` 等配置
- 脚本通过官方 CDP 调试协议驱动本地应用，不涉及破解、注入或外挂
- 使用本工具产生的一切后果由使用者自行承担

## 许可证

[MIT](./LICENSE)

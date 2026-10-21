# NOTICE — 第三方技能收录说明

本目录 `skill/` 是**第三方技能**的完整收录，不是本仓库原创代码。

| 项 | 内容 |
|---|---|
| 技能名称 | WorkBuddy积分助手（`totorosir-workbuddy-score`） |
| 作者 | TOTORO（totorosir），公众号「龙猫科技说」 |
| 版本 | 3.1.7 |
| 许可证 | **MIT-0**（MIT No Attribution） |
| 来源 | WorkBuddy 技能市场（`skill_2095638135306883072`） |

**为什么收录**：本仓库的签到链路已改为调用该技能的接口，不再依赖 CDP/界面。
把技能一并放进仓库，是为了让仓库本身可自包含运行，避免"换台机器就缺依赖"。

**收录范围**（刻意排除以下内容）：

- `_icon.png` —— 图标，非运行所需
- `__pycache__/`、`scripts/.toast_payload.json` —— 运行时产物
- `_skillhub_meta.json` —— 安装元信息，含本机绝对路径，不宜公开

**使用限制**：技能内含 `--travel-auto`（派猫猫旅行写操作）与消息推送（12 渠道）能力，
本仓库的自动化只使用其中两个接口：

- `POST /v2/billing/meter/daily-checkin`（签到，幂等）
- 旅行 `claim` / `depart`（仅在显式传 `--travel-auto` 时）

**不调用**兑换、抽奖等其他写接口。详见 `skill/SKILL.md` 的「安全约束」。

**升级方式**：技能市场更新后，重新执行本目录的收录流程即可
（复制 `SKILL.md`、`scripts/`、`references/`、`templates/`，排除上述清单）。

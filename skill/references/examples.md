# 输出样例（WorkBuddy积分助手）

本文件给出各运行模式的真实输出片段，供 AI 理解技能产出格式。数值为示例，随账号与活动期变化。

---

## 1. 查询模式（默认，弹通知、不写 HTML）

```
== WorkBuddy积分助手 · 每日播报 ==
积分余额（总积分）：300
今日记录：已记录（今日可得 100 分）
活跃地图（本月）：本月已记录 3 天，累计 3 天
  本月已记录日：2026-09-03、2026-09-02、2026-09-01
本周进度：已完成 3 / 7 天（还差 4 天满勤）
连续登录：今日为连续登录日；下一连续奖励节点 7 天，还差 4 天；连续奖励累计 0 分（0 天）
连续奖励分档（与官方页面一致）：7天+50积分+3能量+1补登卡+1抽奖（累计发放） / 14天+100积分+4能量+1补登卡+1抽奖（累计发放·脚本推断·待校准） / 28天+150积分+5能量+1补登卡+1抽奖（主动兑换）
  下一档：7 天（还差 4 天）
本期活动：开学季（Buddy加油站 / 第8期）[进行中]
  活动周期：2026-09-01 00:00:00 ~ 2026-09-15 23:59:59
  活动剩余：12 天；若全勤预计再得 1200 分
领取状态：立即领取
活动入口：认证领积分 -> https://www.workbuddy.cn/events/campus-freshman/

成长计划：
  成长等级：14
  成长进度：14 / 16
  当前 Buddy：好事喵（R 级）
  成长任务：
    · 完成新手引导（奖励 100 积分 · 已完成）
    · 连续活跃 7 天（奖励 300 积分 · 进行中）

派猫猫旅行：
  旅行状态：空闲（可派遣）
  能量余额：2
  可选地点：咖啡馆、商场店铺、健身房、古镇客栈
  （各地点时长 / 积分完全相同：随机 1-4 小时，5-10 积分）
  今日派遣：已达上限，明日再来

暂不支持：积分明细（流水）、兑换 / 抽奖条件——成长计划任务 / 进度 / 当前 Buddy 与派猫猫旅行均已通过只读 GET 接口展示；积分明细与兑换抽奖仍无可用只读接口，本助手只做已验证字段的只读查询，不编造上述数据。
```

> 今日未记录时，播报会在「记录催办」段提示：⏰ 今日尚未完成对话，去 Buddy 加油站完成一次对话即可记录登录 1 天。

> 旅行区块按状态变化：`traveling` 时多出「当前地点」与「到达倒计时」；`arrived` 时多出「当前地点」与「可领积分」。

---

## 2. 自动签到模式（`checkin`）

```
== WorkBuddy积分助手 · 自动签到 ==
签到结果：今日已签到，无需重复打卡 ✓

== WorkBuddy积分助手 · 每日播报 ==
（下方为同查询模式的全量播报，此处略）
```

签到成功示例：`签到结果：✅ 签到成功！`；失败：`签到结果：❌ 失败 - <原因>`。

---

## 3. `--travel-auto` 模式（派猫猫旅行全自动闭环）

在查询模式基础上，旅行区块多出「自动执行」日志，逐条列出领取 / 派遣 / 跳过原因。

**场景 A：已达每日上限（不会发任何写请求）**

```
派猫猫旅行：
  旅行状态：空闲（可派遣）
  能量余额：2
  可选地点：咖啡馆、商场店铺、健身房、古镇客栈
  （各地点时长 / 积分完全相同：随机 1-4 小时，5-10 积分）
  今日派遣：已达上限，明日再来
  自动执行：
    · 今日派遣次数已用完，跳过派遣
```

**场景 B：已到达 → 先领取 → 再派出（完整闭环）**

```
派猫猫旅行：
  旅行状态：旅行中
  当前地点：健身房
  到达倒计时：2 小时 15 分
  自动执行：
    · ✅ 已领取旅行奖励 +9 积分
    · 🚀 已派出 Buddy 前往【健身房】
```

**场景 C：旅行中（不派遣，仅展示倒计时）**

```
派猫猫旅行：
  旅行状态：旅行中
  当前地点：咖啡馆
  到达倒计时：3 小时 40 分
  能量余额：2
  自动执行：
    · Buddy 正在旅行中，无需派遣
```

> 可与签到组合：`checkin --travel-auto`（一次运行完成签到 + 旅行闭环）。

---

## 4. `--html` 模式（桌面报告）

- 终端文字播报照常输出；脚本在 `~/Desktop/WorkBuddy积分播报.html` 生成一份藏蓝科技风单文件（覆盖更新），并在 stderr 打印 `[desktop] 已生成桌面报告：<路径>`。
- 同时尝试弹一次系统通知（Windows / macOS / Linux 跨平台，失败静默降级）。
- HTML 结构（单文件，内联样式，无外部资源，双击即开）：
  - 顶部统计卡：积分余额 / 连续登录(天) / 今日记录
  - 卡片区：签到结果（若有）、活跃地图、本周进度、本期活动、成长计划、**派猫猫旅行**、记录催办、领取/活动入口
  - 连续奖励分档表（7 / 14 / 28 节点状态：已达成 ✓ / 还差 N 天）
  - 页脚：暂不支持项说明 + 版本号（2.1.0）+ 安全声明
- 派猫猫旅行卡片内容与文字播报一致；带 `--travel-auto` 时同样展示「自动执行」日志。

---

## 5. `--json` 模式（机器可读，不触发展示）

输出 JSON，含以下顶层字段（签到模式额外含 `checkin_result`）：

```json
{
  "total_credits": 300,
  "streak_days": 3,
  "today_checked_in": true,
  "checkin_dates_total": 3,
  "checkin_dates_this_month": ["2026-09-03", "2026-09-02", "2026-09-01"],
  "week_checkin_days": 3,
  "week_progress_done": 3,
  "week_progress_total": 7,
  "week_remaining": 4,
  "daily_credit": 100,
  "today_credit": 100,
  "is_streak_day": true,
  "next_streak_day": 7,
  "streak_gap": 4,
  "streak_bonus_days": 0,
  "streak_bonus_credit": 0,
  "streak_schedule": [
    {"streak_days": 7, "credit": 50, "energy": 3, "makeup_card": 1, "lottery": 1, "type": "累计发放", "remaining": 4},
    {"streak_days": 14, "credit": 100, "energy": 4, "makeup_card": 1, "lottery": 1, "type": "累计发放", "remaining": 11},
    {"streak_days": 28, "credit": 150, "energy": 5, "makeup_card": 1, "lottery": 1, "type": "主动兑换", "remaining": 25}
  ],
  "activity": {
    "active": true,
    "status": "进行中",
    "theme_name": "Buddy加油站",
    "season": "8",
    "activity_name": "开学季",
    "start_time": "2026-09-01 00:00:00",
    "end_time": "2026-09-15 23:59:59",
    "remaining_days": 12,
    "full_attendance_estimate": 1200
  },
  "claim": {
    "claim_button_text": "立即领取",
    "action_button": {"show": true, "text": "认证领积分", "action": "https://www.workbuddy.cn/events/campus-freshman/"}
  },
  "remind_checkin": false,
  "growth": {
    "available": true,
    "tasks": [
      {"title": "完成新手引导", "reward_credit": 100, "status": "已完成"},
      {"title": "连续活跃 7 天", "reward_credit": 300, "status": "进行中"}
    ],
    "profile": {"level": 14, "progress": 14, "total": 16},
    "buddy": {"name": "好事喵", "tier": "R"}
  },
  "travel": {
    "available": true,
    "state": "idle",
    "state_text": "空闲（可派遣）",
    "location_name": null,
    "reward_credit": 0,
    "remaining_seconds": null,
    "remaining_text": null,
    "daily_limit_reached": true,
    "record_id": 0,
    "energy": 2,
    "energy_cost_per_open": 10,
    "locations": [
      {"id": 1, "code": "coffee", "name": "咖啡馆", "duration_min": 1, "duration_max": 4, "reward_min": 5, "reward_max": 10},
      {"id": 2, "code": "mall", "name": "商场店铺", "duration_min": 1, "duration_max": 4, "reward_min": 5, "reward_max": 10},
      {"id": 3, "code": "gym", "name": "健身房", "duration_min": 1, "duration_max": 4, "reward_min": 5, "reward_max": 10},
      {"id": 4, "code": "ancient_town", "name": "古镇客栈", "duration_min": 1, "duration_max": 4, "reward_min": 5, "reward_max": 10}
    ]
  }
}
```

- 不弹通知、不写 HTML，保持机器输出干净。
- `growth.available` 标识成长计划是否可取；`False` 时 `tasks` / `profile` / `buddy` 为 `null`。
- `travel.available` 标识派猫猫旅行数据是否可取；`False` 时旅行只读接口全部失败（脚本静默降级，不影响主结果）。
- 带 `--travel-auto` 时，`travel` 内额外出现 `auto_log`（字符串数组，逐条记录领取 / 派遣 / 跳过原因）。

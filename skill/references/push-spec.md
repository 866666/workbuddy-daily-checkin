# 消息推送接口与实现参考（WorkBuddy积分助手）

本文件为 `scripts/push_notify.py` 的接口与配置约定，供 AI 执行技能或外部流程调用时作为上下文。
参照 totorosir-push-message（消息推送助手）技能设计：统一接口、本地配置、结果聚合、失败隔离。
**纯 Python 标准库，无第三方依赖。**

---

## 1. 接口契约（被调用）

```python
push_send(title, content, content_type="markdown",
          channels=None, config=None, confirm_paid=False, timeout=10)
# -> [{"channel": str,
#      "status": "ok" | "failed" | "unconfigured" | "skipped",
#      "http_code": int | None,
#      "detail": str}, ...]
```

| 参数 | 说明 |
|---|---|
| `title` | 推送标题，如 `WorkBuddy 积分播报` |
| `content` | 推送正文（markdown / text / html，默认 markdown） |
| `content_type` | `markdown` / `text` / `html`；各渠道按能力映射（见第 3 节） |
| `channels` | 渠道列表（逗号分隔字符串或列表）；`None` 表示「配置中所有渠道」 |
| `config` | 配置字典（由 `load_push_config` 返回）；`None` 时自动按优先级加载 |
| `confirm_paid` | 付费渠道(短信)开关；`False` 时短信标记 `skipped`，绝不自发 |
| `timeout` | 每渠道独立超时秒数（默认 10），无内置自动重试 |

返回结果列表：每渠道一项，`status` 含义——
- `ok`：HTTP 2xx（或 SMTP 投递成功 / 系统写本地成功）
- `failed`：网络错误 / 超时 / 服务端非 2xx
- `unconfigured`：配置缺失（列出缺失字段），不中断
- `skipped`：付费渠道未确认等主动跳过

---

## 2. 配置（凭据只存本地）

默认路径：`~/.workbuddy/totorosir-push-message/config.json`（**复用消息推送技能的配置**）。
覆盖优先级：`push_send` 的 `config` 参数 > 环境变量 `WORKBUDDY_SCORE_PUSH_CONFIG` > 默认路径。

生成模板：`python scripts/push_notify.py sample-config --write`
校验就绪：`python scripts/push_notify.py check [--config <路径>]`

### 配置 schema（channels 下各渠道字段）

```json
{
  "channels": {
    "dingtalk":  {"webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxx", "secret": "可选加签密钥"},
    "feishu":    {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx", "secret": "可选加签密钥"},
    "wecom":     {"webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx", "secret": "可选加签密钥"},
    "wechat":    {"pushplus_token": "微信 PushPlus 令牌"},
    "email":     {"smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_mode": "ssl",
                  "smtp_user": "a@qq.com", "smtp_pass": "授权码", "from": "a@qq.com", "to": "b@qq.com"},
    "sms":       {"url": "https://your-sms-api/send", "payload_template": "{\"msg\":\"{content}\"}"},
    "qq":        {"url": "https://qmsg.zendee.cn/send/xxx", "payload_template": "{\"msg\":\"{content}\"}"},
    "slack":     {"webhook_url": "https://hooks.slack.com/services/xxx"},
    "telegram":  {"bot_token": "xxxx:yyyy", "chat_id": "-100123"},
    "bark":      {"server": "https://api.day.app", "device_key": "设备密钥"},
    "webhook":   {"url": "https://your-endpoint/hook", "payload_template": "{\"title\":\"{title}\",\"content\":\"{content}\"}"},
    "system":    {}
  }
}
```

- `sms` / `qq` / `webhook` 支持 `payload_template`：含 `{title}` / `{content}` 占位符的 JSON 模板，发送前替换。
- `system` 零配置，无需任何字段。

---

## 3. 渠道路由表

| 渠道 | 标识 | 类型 | 配置关键字段 | 内容适配 |
|---|---|---|---|---|
| 钉钉 | dingtalk | HTTP | webhook, secret(可选加签) | markdown→markdown 卡片 / 其它→text |
| 飞书 | feishu | HTTP | webhook, secret(可选) | interactive 卡片（markdown 元素） |
| 企业微信 | wecom | HTTP | webhook, secret(可选) | markdown 消息 |
| 微信 | wechat | HTTP | pushplus_token | PushPlus（`markdown`/`html`/`txt`） |
| 邮件 | email | SMTP | smtp_host/user/pass/from/to | `plain` / `html` 子类型 |
| 短信 | sms | HTTP(付费) | url, payload_template | 模板占位符 |
| QQ | qq | HTTP | url, payload_template(可选) | 模板或 `{content}` |
| Slack | slack | HTTP | webhook_url | `text` |
| Telegram | telegram | HTTP | bot_token, chat_id | `text`（不强制 parse_mode，避免 400） |
| Bark | bark | HTTP | server, device_key | `title`+`body` |
| 通用 | webhook | HTTP | url, payload_template(可选) | 模板或 `{title,content}` |
| 系统通知 | system | 本地 | 无 | 写 `~/.workbuddy/totorosir-workbuddy-score/push_inbox.jsonl` + 尽力弹窗 |

### 加签算法（钉钉 / 飞书 / 企业微信）
- **钉钉**：`sign = urlencode(base64(hmac_sha256(secret, f"{timestamp}\n{secret}")))`，timestamp 为毫秒；拼到 webhook 的 `sign` / `timestamp` 参数。
- **飞书**：`sign = base64(hmac_sha256(secret, f"{timestamp}\n{secret}"))`，timestamp 为秒；拼到 `sign` / `timestamp` 参数（不 urlencode）。
- **企业微信**：`sign = base64(hmac_sha256(secret, f"{timestamp}\n{secret}"))`，timestamp 为秒；拼到 `sign` / `timestamp` 参数。

---

## 4. 降级与边界（与消息推送技能一致）

- **未配置**：`unconfigured`，列出缺失字段名，不中断其他渠道。
- **发送失败**：单记 `failed`，不影响其他渠道；结果聚合输出，便于调用方对失败项重发。
- **网络超时**：每渠道独立 `timeout`（默认 10s）；单次发送**无内置自动重试**。
- **付费渠道(短信)**：`confirm_paid=False` 时 `skipped`，绝不自发。
- **凭据安全**：配置只在本地读取与发送；任何 stdout / stderr / 日志 / 结果 `detail` 都不含明文 token / 密码 / webhook 密钥。
- **系统通知**：零配置、不过网，写入本地收件箱文件，并尽力弹窗（Windows 端桌面弹窗由主脚本 `notify()` 统一负责，不重复）。

---

## 5. 与主脚本的集成

- `buddy_station.py` 在非 `--json` 模式、未加 `--no-push` 时，报告构建后自动调用 `push_send`（传入 `render_human(report)` 作为正文）。
- 新参数：`--no-push`（关闭远程推送，桌面通知仍弹）、`--push-config <路径>`（指定配置）。
- 推送失败 / 模块缺失均静默降级（写 stderr `[push] ...`），**不影响主结果（查分 / 签到 / 旅行）**。
- `--json` 模式不触发推送（机器输出）。

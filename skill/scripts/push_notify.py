#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy积分助手 - 消息推送模块（纯标准库，无第三方依赖）。

参照 totorosir-push-message（消息推送助手）技能的设计：统一接口契约、本地配置、
结果聚合、失败隔离。用于把每日积分播报分发到用户自有的消息渠道。

完整 12 渠道：
    钉钉 dingtalk / 飞书 feishu / 企业微信 wecom / 微信 wechat(PushPlus) /
    邮件 email / 短信 sms(付费) / QQ qq / Slack slack /
    Telegram telegram / Bark bark / 通用 Webhook webhook / 系统通知 system

接口契约（被 buddy_station.py 或外部流程调用）：
    push_send(title, content, content_type="markdown",
              channels=None, config=None, confirm_paid=False, timeout=10)
    -> [{"channel": str,
         "status": "ok" | "failed" | "unconfigured" | "skipped",
         "http_code": int | None,
         "detail": str}, ...]

配置（凭据只存本地，绝不进日志 / 对话）：
    默认路径：~/.workbuddy/totorosir-push-message/config.json  （复用消息推送技能的配置）
    覆盖优先级：push_send 的 config 参数 > 环境变量 WORKBUDDY_SCORE_PUSH_CONFIG > 默认路径
    （调用方也可先读 --push-config 再把路径作为 config 参数传入）

降级与边界（与消息推送技能一致）：
    - 某渠道配置缺失          -> status=unconfigured，列出缺失字段，不中断
    - 某渠道发送失败          -> 单记 failed，不影响其他渠道
    - 网络超时                -> 每渠道独立 timeout（默认 10s），无内置自动重试
    - 付费渠道(短信)          -> 未 confirm_paid 则 skipped
    - 凭据 / 密钥             -> 任何输出都不含明文
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import shutil
import smtplib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText

# ===== 配置路径 =====
DEFAULT_PUSH_CONFIG = os.path.join(
    os.path.expanduser("~"), ".workbuddy", "totorosir-push-message", "config.json")
ENV_PUSH_CONFIG = "WORKBUDDY_SCORE_PUSH_CONFIG"

PUSH_CHANNELS = [
    "dingtalk", "feishu", "wecom", "wechat", "email", "sms",
    "qq", "slack", "telegram", "bark", "webhook", "system",
]

# 示例配置模板（占位符，用户填入真实凭据；不写进技能目录、不进对话）
SAMPLE_CONFIG = {
    "channels": {
        "dingtalk": {"webhook": "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN", "secret": "可选加签密钥"},
        "feishu": {"webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_HOOK", "secret": "可选加签密钥"},
        "wecom": {"webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY", "secret": "可选加签密钥"},
        "wechat": {"pushplus_token": "YOUR_PUSHPLUS_TOKEN"},
        "email": {"smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_mode": "ssl",
                  "smtp_user": "123@qq.com", "smtp_pass": "邮箱授权码", "from": "123@qq.com", "to": "to@qq.com"},
        "sms": {"url": "https://your-sms-api.example.com/send", "payload_template": "{\"phone\":\"\",\"msg\":\"{title} {content}\"}"},
        "qq": {"url": "https://qmsg.zendee.cn/send/YOUR_KEY", "payload_template": "{\"msg\":\"{content}\"}"},
        "slack": {"webhook_url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"},
        "telegram": {"bot_token": "YOUR_BOT_TOKEN", "chat_id": "YOUR_CHAT_ID"},
        "bark": {"server": "https://api.day.app", "device_key": "YOUR_DEVICE_KEY"},
        "webhook": {"url": "https://your-endpoint.example.com/hook", "payload_template": "{\"title\":\"{title}\",\"content\":\"{content}\"}"},
        "system": {},
    }
}


# ===== 配置加载 =====
def load_push_config(path=None):
    """读取推送配置。返回 {"_path": str, "channels": dict}。

    缺失 / 损坏时返回空 channels（不抛异常），由调用方降级处理。
    """
    if path is None:
        path = os.environ.get(ENV_PUSH_CONFIG) or DEFAULT_PUSH_CONFIG
    result = {"_path": path, "channels": {}}
    if not path or not os.path.isfile(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        channels = data.get("channels", {}) if isinstance(data, dict) else {}
        if isinstance(channels, dict):
            result["channels"] = channels
    except Exception:
        result["channels"] = {}
    return result


# ===== 渠道构建器 =====
# 每个 HTTP 渠道返回 (url, payload_dict, headers_dict)；SMTP/系统渠道由专用函数处理。
def _build_dingtalk(cfg, msg):
    webhook = cfg["webhook"]
    content = msg["content"]
    if msg.get("content_type") == "markdown":
        payload = {"msgtype": "markdown", "markdown": {"title": msg["title"], "text": content}}
    else:
        payload = {"msgtype": "text", "text": {"content": content}}
    url = _sign_dingtalk_feishu(webhook, cfg.get("secret"), kind="dingtalk")
    return url, payload, {"Content-Type": "application/json"}


def _build_feishu(cfg, msg):
    webhook = cfg["webhook"]
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": msg["title"]}},
            "elements": [{"tag": "markdown", "content": msg["content"]}],
        },
    }
    url = _sign_dingtalk_feishu(webhook, cfg.get("secret"), kind="feishu")
    return url, payload, {"Content-Type": "application/json"}


def _build_wecom(cfg, msg):
    webhook = cfg["webhook"]
    payload = {"msgtype": "markdown", "content": {"content": msg["content"]}}
    url = _sign_wecom(webhook, cfg.get("secret"))
    return url, payload, {"Content-Type": "application/json"}


def _build_wechat(cfg, msg):
    template = {"markdown": "markdown", "html": "html"}.get(msg.get("content_type"), "txt")
    payload = {"token": cfg["pushplus_token"], "title": msg["title"],
               "content": msg["content"], "template": template}
    return "http://www.pushplus.plus/send", payload, {"Content-Type": "application/json"}


def _build_slack(cfg, msg):
    payload = {"text": msg["content"], "username": msg["title"]}
    return cfg["webhook_url"], payload, {"Content-Type": "application/json"}


def _build_telegram(cfg, msg):
    url = "https://api.telegram.org/bot%s/sendMessage" % cfg["bot_token"]
    payload = {"chat_id": cfg["chat_id"], "text": msg["content"]}
    return url, payload, {"Content-Type": "application/json"}


def _build_bark(cfg, msg):
    server = (cfg.get("server") or "https://api.day.app").rstrip("/")
    payload = {"title": msg["title"], "body": msg["content"], "device_key": cfg["device_key"]}
    return server + "/push", payload, {"Content-Type": "application/json"}


def _build_webhook(cfg, msg):
    url = cfg["url"]
    if cfg.get("payload_template"):
        payload = _render_template(cfg["payload_template"], msg["title"], msg["content"])
    else:
        payload = {"title": msg["title"], "content": msg["content"]}
    return url, payload, {"Content-Type": "application/json"}


def _build_qq(cfg, msg):
    url = cfg["url"]
    if cfg.get("payload_template"):
        payload = _render_template(cfg["payload_template"], msg["title"], msg["content"])
    else:
        payload = {"content": msg["content"], "title": msg["title"]}
    return url, payload, {"Content-Type": "application/json"}


def _build_sms(cfg, msg):
    # 付费渠道：payload_template 为含 {title}/{content} 占位符的 JSON 模板
    payload = _render_template(cfg["payload_template"], msg["title"], msg["content"])
    return cfg["url"], payload, {"Content-Type": "application/json"}


# ===== 签名辅助 =====
def _sign_dingtalk_feishu(webhook, secret, kind):
    if not secret:
        return webhook
    timestamp = str(round(time.time() * 1000)) if kind == "dingtalk" else str(int(time.time()))
    string_to_sign = "%s\n%s" % (timestamp, secret)
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                         digestmod=hashlib.sha256).digest()
    if kind == "dingtalk":
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    else:
        sign = base64.b64encode(hmac_code).decode("utf-8")
    sep = "&" if "?" in webhook else "?"
    return "%s%ssign=%s&timestamp=%s" % (webhook, sep, sign, timestamp)


def _sign_wecom(webhook, secret):
    if not secret:
        return webhook
    timestamp = str(int(time.time()))
    string_to_sign = "%s\n%s" % (timestamp, secret)
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                         digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    sep = "&" if "?" in webhook else "?"
    return "%s%ssign=%s&timestamp=%s" % (webhook, sep, sign, timestamp)


def _render_template(template, title, content):
    text = template.replace("{title}", title).replace("{content}", content)
    return json.loads(text)


# ===== 发送辅助 =====
def _http_post(url, payload, headers, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            body = resp.read().decode("utf-8", "replace")
        return code, _truncate(body)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        return exc.code, _truncate(body)
    except Exception as exc:  # noqa: BLE001
        return None, _safe(str(exc))


def _send_smtp(cfg, msg, timeout):
    host = cfg["smtp_host"]
    port = int(cfg.get("smtp_port") or 465)
    mode = (cfg.get("smtp_mode") or "ssl").lower()
    user = cfg["smtp_user"]
    pw = cfg["smtp_pass"]
    frm = cfg["from"]
    to = cfg["to"]
    subtype = "html" if msg.get("content_type") == "html" else "plain"
    mime = MIMEText(msg["content"], subtype, "utf-8")
    mime["From"] = frm
    mime["To"] = to
    mime["Subject"] = Header(msg["title"], "utf-8")
    if mode == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
        if mode == "tls":
            server.starttls()
    try:
        server.login(user, pw)
        server.sendmail(frm, [t.strip() for t in to.split(",")], mime.as_bytes())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    return "已投递至 %s" % to


def _send_system(msg):
    inbox = os.path.join(os.path.expanduser("~"), ".workbuddy",
                         "totorosir-workbuddy-score", "push_inbox.jsonl")
    try:
        os.makedirs(os.path.dirname(inbox), exist_ok=True)
        with open(inbox, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"title": msg["title"], "content": msg["content"],
                 "ts": datetime.now().isoformat()}, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        return "本地收件箱写入失败：%s" % _safe(str(exc))
    _notify_local(msg["title"], msg["content"])
    return "已写入本地收件箱 %s" % inbox


def _notify_local(title, message):
    """系统通知渠道的尽力弹窗（零配置、不过网）；失败静默。"""
    system = platform.system()
    try:
        if system == "Darwin":
            esc_t = title.replace("\\", "\\\\").replace('"', '\\"')
            esc_m = message.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s"' % (esc_m, esc_t)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        elif system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        # Windows 端桌面弹窗由 buddy_station.notify() 统一负责，这里不重复
    except Exception:  # noqa: BLE001
        pass


def _truncate(text, limit=200):
    text = (text or "").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _safe(text):
    return _truncate(text, 300)


# ===== 渠道注册表 =====
CHANNELS = {
    "dingtalk": {"kind": "http", "required": ["webhook"], "build": _build_dingtalk},
    "feishu": {"kind": "http", "required": ["webhook"], "build": _build_feishu},
    "wecom": {"kind": "http", "required": ["webhook"], "build": _build_wecom},
    "wechat": {"kind": "http", "required": ["pushplus_token"], "build": _build_wechat},
    "email": {"kind": "smtp", "required": ["smtp_host", "smtp_user", "smtp_pass", "from", "to"], "build": None},
    "sms": {"kind": "http", "required": ["url", "payload_template"], "build": _build_sms, "paid": True},
    "qq": {"kind": "http", "required": ["url"], "build": _build_qq},
    "slack": {"kind": "http", "required": ["webhook_url"], "build": _build_slack},
    "telegram": {"kind": "http", "required": ["bot_token", "chat_id"], "build": _build_telegram},
    "bark": {"kind": "http", "required": ["device_key"], "build": _build_bark},
    "webhook": {"kind": "http", "required": ["url"], "build": _build_webhook},
    "system": {"kind": "system", "required": [], "build": None},
}


# ===== 核心发送接口 =====
def push_send(title, content, content_type="markdown", channels=None,
              config=None, confirm_paid=False, timeout=10):
    """分发一条消息到目标渠道，返回结果列表。

    - channels=None 时向「配置中所有渠道」发送（含显式配置的 system）。
    - 任一路失败 / 未配置均隔离，不中断其他渠道。
    - 付费渠道(短信) 未 confirm_paid 时标记为 skipped。
    """
    if config is None:
        config = load_push_config()
    chans = config.get("channels", {}) if isinstance(config, dict) else {}

    if channels:
        targets = [c.strip() for c in channels if c.strip()]
    else:
        targets = list(chans.keys())

    results = []
    seen = set()
    for ch in targets:
        if ch in seen:
            continue
        seen.add(ch)
        spec = CHANNELS.get(ch)
        if spec is None:
            results.append({"channel": ch, "status": "unconfigured",
                            "http_code": None, "detail": "未知渠道"})
            continue
        cfg = chans.get(ch) if isinstance(chans.get(ch), dict) else {}
        missing = [k for k in spec["required"] if not cfg.get(k)]
        if missing and spec["kind"] != "system":
            results.append({"channel": ch, "status": "unconfigured",
                            "http_code": None, "detail": "缺少配置: " + ",".join(missing)})
            continue
        if spec.get("paid") and not confirm_paid:
            results.append({"channel": ch, "status": "skipped",
                            "http_code": None, "detail": "付费渠道，需确认(confirm_paid)"})
            continue
        try:
            if spec["kind"] == "http":
                url, payload, headers = spec["build"](cfg, {"title": title, "content": content, "content_type": content_type})
                code, detail = _http_post(url, payload, headers, timeout)
                ok = code is not None and 200 <= code < 300
                results.append({"channel": ch, "status": "ok" if ok else "failed",
                                "http_code": code, "detail": detail})
            elif spec["kind"] == "smtp":
                detail = _send_smtp(cfg, {"title": title, "content": content, "content_type": content_type}, timeout)
                results.append({"channel": ch, "status": "ok", "http_code": None, "detail": detail})
            else:  # system
                detail = _send_system({"title": title, "content": content})
                results.append({"channel": ch, "status": "ok", "http_code": None, "detail": detail})
        except Exception as exc:  # noqa: BLE001
            results.append({"channel": ch, "status": "failed", "http_code": None, "detail": _safe(str(exc))})
    return results


def render_results(results):
    """把结果列表渲染为可读文本（用于 stderr / 日志）。"""
    icon = {"ok": "✅", "failed": "❌", "unconfigured": "⚠️", "skipped": "⏭️"}
    lines = []
    for r in results:
        lines.append("%s %s: %s" % (icon.get(r["status"], "•"), r["channel"], r["detail"]))
    return "\n".join(lines)


# ===== 轻量 CLI（与消息推送技能风格一致）=====
def main_cli():
    args = sys.argv[1:]
    if "sample-config" in args:
        print(json.dumps(SAMPLE_CONFIG, ensure_ascii=False, indent=2))
        if "--write" in args:
            path = DEFAULT_PUSH_CONFIG
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(SAMPLE_CONFIG, handle, ensure_ascii=False, indent=2)
            print("已写入示例配置: " + path)
        return
    if "check" in args or "check-config" in args:
        path = None
        if "--config" in args:
            idx = args.index("--config")
            if idx + 1 < len(args):
                path = args[idx + 1]
        cfg = load_push_config(path)
        chans = cfg.get("channels", {})
        print("配置路径: %s" % (cfg.get("_path") or "(未指定)"))
        print("已配置渠道: %s" % (", ".join(chans.keys()) if chans else "(无)"))
        for ch in PUSH_CHANNELS:
            if ch in chans:
                print("  ✅ %s" % ch)
            else:
                print("  ⚪ %s (未配置)" % ch)
        return
    # 直接发送：--title --content [--channels] [--push-config]
    title = None
    content = None
    channels = None
    pconf = None
    if "--title" in args:
        title = args[args.index("--title") + 1]
    if "--content" in args:
        content = args[args.index("--content") + 1]
    if "--channels" in args:
        channels = args[args.index("--channels") + 1]
    if "--push-config" in args:
        pconf = args[args.index("--push-config") + 1]
    if not title or not content:
        print("用法: push_notify.py --title 标题 --content 正文 [--channels dingtalk,email] [--push-config 路径]")
        return
    cfg = load_push_config(pconf) if pconf else None
    results = push_send(title, content, channels=channels, config=cfg)
    print(render_results(results))


if __name__ == "__main__":
    main_cli()

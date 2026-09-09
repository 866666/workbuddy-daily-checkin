# -*- coding: utf-8 -*-
"""workbuddy-daily-checkin —— 通过 CDP 自动完成 WorkBuddy 每日签到领积分

使用方式
--------
**一键配置（首次，交互式，建议在终端前台运行）：**

    python checkin_cdp.py --setup

向导会自动完成：定位并启动 WorkBuddy（带调试端口）→ 检测登录（未登录则提示你在
弹出的窗口扫码/登录）→ 从页面自动提取你的昵称并写入 config.json → 校验签到按钮文案
→ 询问是否注册每日计划任务。跑完这一次，之后就不用管了。

**日常签到（计划任务自动调用，也可手动）：**

    python checkin_cdp.py                # 正常签到；auto_launch=true 时连不上才会自动拉起
    python checkin_cdp.py --dry          # 只检测按钮，不点击
    python checkin_cdp.py --info         # 打印端口与页面列表
    python checkin_cdp.py --no-auto-launch   # 强制禁用"自动拉起目标应用"，连不上就直接报错

原理
----
WorkBuddy 是 Electron/Chromium 应用。以 ``--remote-debugging-port=9222`` 启动后，
本机即暴露 CDP 调试服务；脚本经 WebSocket 在页面内执行 JS，找到文案为「立即领取」
的按钮并点击，再校验是否生效。全程只连 ``127.0.0.1``，不向任何第三方发送数据。

配置优先级：内置默认 < 同目录 config.json < --config 指定文件 < 命令行参数。
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

# Windows 下 stdout 默认 GBK，中文日志会崩，强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests
import websocket

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    # 本机 CDP 调试端口（WorkBuddy 启动参数 --remote-debugging-port 的值）
    "debug_port": 9222,
    # 签到按钮文案（应用改版后只需改这里，不用动代码）
    "claim_text": "立即领取",
    # 主界面左下角的用户显示名。留空表示跳过「点头像打开签到面板」的兜底步骤。
    # 用 --setup 会自动从页面提取并写入，无需手填。
    "nickname": "",
    # 端口自动扫描时要跳过的端口（若本机还有其他服务占用调试端口会误连）
    "skip_ports": [],
    # WorkBuddy 可执行文件路径；留空/auto 时自动定位（App Paths → Program Files → PATH）
    "workbuddy_exe": "auto",
    # 连不上调试服务时是否自动拉起/重启 WorkBuddy。
    # 注意：WorkBuddy 已带调试端口常驻运行（如本机日常使用场景）建议设为 false，
    # 避免脚本在非交互（计划任务）环境下误杀正在使用的 WorkBuddy。
    "auto_launch": False,
}


def load_config(path=None):
    cfg = dict(DEFAULT_CONFIG)
    candidates = []
    if path:
        candidates.append(Path(path))
    else:
        candidates.append(Path(__file__).parent / "config.json")
    for p in candidates:
        if p.exists():
            try:
                user = json.loads(p.read_text(encoding="utf-8"))
                cfg.update({k: v for k, v in user.items() if k in cfg})
            except Exception as e:
                print(f"[warn] 读取配置 {p} 失败: {e}")
    return cfg


def save_config(cfg, path=None):
    """写回 config.json（保留全部字段）"""
    p = Path(path) if path else Path(__file__).parent / "config.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 目标应用定位 / 进程管理
# ---------------------------------------------------------------------------
def find_workbuddy_exe(cfg):
    """按优先级定位 WorkBuddy.exe：配置 > App Paths 注册表 > Program Files > PATH"""
    conf = (cfg or {}).get("workbuddy_exe", "auto")
    if conf and conf != "auto":
        p = Path(conf)
        if p.exists():
            return str(p)

    # 1) 运行中的进程路径（最可靠）
    try:
        import psutil
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                if (proc.info.get("name") or "").lower() == "workbuddy.exe":
                    exe = proc.info.get("exe")
                    if exe and Path(exe).exists():
                        return exe
            except Exception:
                continue
    except Exception:
        pass

    # 2) App Paths 注册表
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(
                    hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WorkBuddy.exe")
                val, _ = winreg.QueryValueEx(key, None)
                winreg.CloseKey(key)
                if val and Path(val).exists():
                    return val
            except OSError:
                continue
    except Exception:
        pass

    # 3) 常见安装位置
    for cand in (
        r"C:\Program Files\WorkBuddy\WorkBuddy.exe",
        r"C:\Program Files (x86)\WorkBuddy\WorkBuddy.exe",
        str(Path.home() / r"AppData\Local\Programs\WorkBuddy\WorkBuddy.exe"),
    ):
        if Path(cand).exists():
            return cand

    # 4) PATH
    found = shutil.which("WorkBuddy")
    if found:
        return found
    return None


def find_workbuddy_procs():
    """返回正在运行的 WorkBuddy.exe 进程列表（psutil 不可用时返回 None）"""
    try:
        import psutil
        out = []
        for p in psutil.process_iter(["name", "pid"]):
            try:
                if (p.info.get("name") or "").lower() == "workbuddy.exe":
                    out.append(p)
            except Exception:
                continue
        return out
    except Exception:
        return None


def kill_workbuddy(timeout=5):
    """结束所有 WorkBuddy 进程（主进程退出后子进程通常随之退出）"""
    procs = find_workbuddy_procs()
    if not procs:
        return True
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    # 等待退出，必要时强制结束
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [p for p in find_workbuddy_procs() if p.is_running()]
        if not alive:
            return True
        time.sleep(0.5)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    return True


def interactive():
    """是否可交互（有控制台）"""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def ask_yes_no(prompt, default=True):
    """交互确认；非交互时直接返回 default"""
    if not interactive():
        return default
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        ans = input(prompt + suffix).strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes")


def launch_workbuddy(exe, port):
    """启动 WorkBuddy（带调试端口）。返回 True 表示已尝试启动"""
    try:
        subprocess.Popen([exe, f"--remote-debugging-port={port}"],
                         cwd=str(Path(exe).parent))
        return True
    except Exception as e:
        print(f"[warn] 启动 WorkBuddy 失败: {e}")
        return False


# ---------------------------------------------------------------------------
# CDP 基础操作
# ---------------------------------------------------------------------------
def cdp_available(port, timeout=1.5):
    """端口上是否有可用的 CDP 服务"""
    try:
        resp = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
        return resp.status_code == 200 and "Browser" in resp.text
    except Exception:
        return False


def cdp_is_workbuddy(port, timeout=1.5):
    """该端口 CDP 是否属于 WorkBuddy"""
    try:
        resp = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
        return resp.status_code == 200 and "workbuddy" in resp.text.lower()
    except Exception:
        return False


def detect_debug_port(cfg):
    """探测目标应用实际 CDP 调试端口。
    若配置端口被占用，Chromium 会回退到其他端口。先试配置端口，
    失败则扫描本机 127.0.0.1 监听端口找 CDP 服务。"""
    port = cfg.get("debug_port", 9222)
    if cdp_is_workbuddy(port):
        return port
    candidates = [port]
    try:
        r2 = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                            encoding="gbk", errors="replace", timeout=15)
        for line in r2.stdout.splitlines():
            if "LISTENING" in line and "127.0.0.1:" in line:
                parts = line.split()
                try:
                    p = int(parts[1].split(":")[-1])
                    if p not in cfg.get("skip_ports", []):
                        candidates.append(p)
                except Exception:
                    continue
    except Exception:
        pass
    seen = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        try:
            resp = requests.get(f"http://127.0.0.1:{p}/json", timeout=0.5)
            data = resp.json()
            if isinstance(data, list) and data:
                # 只接受 WorkBuddy 的调试服务，避免误连其他浏览器
                if cdp_is_workbuddy(p):
                    return p
        except Exception:
            continue
    return port


def ensure_cdp(cfg):
    """确保带调试端口的 WorkBuddy 正在运行。

    已就绪 → 直接返回 True；
    没启动 → 自动定位 exe 启动；
    已在跑但没带调试端口 → 询问（可交互时）或自动重启后启动。
    """
    port = cfg.get("debug_port", 9222)
    if cdp_is_workbuddy(port):
        return True

    exe = find_workbuddy_exe(cfg)
    if not exe:
        print("❌ 找不到 WorkBuddy 可执行文件。可在 config.json 的 "
              "`workbuddy_exe` 字段填写完整路径。")
        return False
    print(f"  定位到 WorkBuddy: {exe}")

    procs = find_workbuddy_procs()
    if procs:
        print(f"  检测到 WorkBuddy 正在运行（{len(procs)} 个进程），但未带调试端口。")
        print("  调试参数只在启动时生效，需要重启 WorkBuddy 才能注入。")
        if not ask_yes_no("  是否自动关闭并重启？（当前未保存的窗口内容可能丢失）", default=True):
            print("  已取消。请手动关闭 WorkBuddy 后用带调试端口的方式启动，再运行本脚本。")
            return False
        print("  正在关闭 WorkBuddy 进程...")
        kill_workbuddy()
        time.sleep(1.5)

    print(f"  正在启动 WorkBuddy（--remote-debugging-port={port}）...")
    if not launch_workbuddy(exe, port):
        return False

    # 等待调试服务就绪（最多 60s）
    deadline = time.time() + 60
    while time.time() < deadline:
        if cdp_is_workbuddy(port):
            print(f"  ✅ 调试服务已就绪（端口 {port}）")
            return True
        time.sleep(1)
    print("  ⏱ 等待调试服务超时（60s）。请确认 WorkBuddy 能正常启动。")
    return False


def get_targets(port):
    resp = requests.get(f"http://127.0.0.1:{port}/json", timeout=3)
    resp.raise_for_status()
    return resp.json()


def cdp_evaluate(ws_url, js_expr, timeout=10):
    """连接 WebSocket 执行 JS，返回返回值"""
    # suppress_origin: Chromium 138+ 默认拒绝带 Origin 的调试连接
    ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    try:
        msg = json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": js_expr, "returnByValue": True}
        })
        ws.send(msg)
        while True:
            data = json.loads(ws.recv())
            if data.get("id") == 1:
                if "error" in data:
                    return {"_cdp_error": data["error"]}
                return data.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


def cdp_reload(ws_url, wait=7):
    """刷新页面。跨午夜后界面可能缓存昨日签到状态，刷新后才能看到今日按钮。"""
    ws = websocket.create_connection(ws_url, timeout=15, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Page.reload", "params": {}}))
        while True:
            data = json.loads(ws.recv())
            if data.get("id") == 1:
                break
    finally:
        ws.close()
    time.sleep(wait)


# ---------------------------------------------------------------------------
# 登录状态 / 账号信息（从 CDP target URL 的 accountSnapshot 参数解析）
# ---------------------------------------------------------------------------
def parse_account_snapshot(url):
    """从 target URL 解析 accountSnapshot → {uid, nickname, ...}；无则返回 None

    URL 形如 …?accountSnapshot=%257B%2522version%2522…（双重 URL 编码的 JSON）。
    """
    if not url or "accountSnapshot=" not in url:
        return None
    try:
        qs = urllib.parse.urlparse(url).query
        params = urllib.parse.parse_qs(qs)
        raw = params.get("accountSnapshot", [None])[0]
        if not raw:
            return None
        # parse_qs 已解一层，再解一层得到原始 JSON 文本
        s = urllib.parse.unquote(raw)
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_account_info(port):
    """扫描所有 target，返回第一个含有效 uid 的账号信息；未登录返回 None"""
    try:
        for t in get_targets(port):
            info = parse_account_snapshot(t.get("url", ""))
            if info and info.get("uid"):
                return info
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 注入到页面执行的 JS（__CLAIM_TEXT__ / __NICKNAME__ 为运行时占位符）
# ---------------------------------------------------------------------------
JS_FIND_BUTTON = r"""
(() => {
  const CLAIM = __CLAIM_TEXT__;
  const candidates = [];
  function walk(root) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      const t = (el.textContent || '').trim();
      if (t === CLAIM) candidates.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  }
  walk(document);
  if (candidates.length === 0) {
    return { found: false, detail: 'no button in dom or shadow dom' };
  }
  const el = candidates[0];
  let target = el, up = el;
  for (let i = 0; i < 12; i++) {
    if (!up.parentElement) break;
    up = up.parentElement;
    const tag = (up.tagName || '').toLowerCase();
    const role = up.getAttribute ? up.getAttribute('role') : null;
    if (tag === 'button' || tag === 'a' || role === 'button') { target = up; break; }
  }
  const r = target.getBoundingClientRect();
  return {
    found: true,
    targetTag: target.tagName,
    targetText: (target.textContent || '').trim().slice(0, 30),
    x: Math.round(r.x + r.width / 2),
    y: Math.round(r.y + r.height / 2),
    visible: r.width > 0 && r.height > 0,
    targetClass: target.className ? String(target.className).slice(0, 60) : ''
  };
})()
"""

JS_CLICK = r"""
(() => {
  const CLAIM = __CLAIM_TEXT__;
  const candidates = [];
  function walk(root) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      const t = (el.textContent || '').trim();
      if (t === CLAIM) candidates.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  }
  walk(document);
  if (candidates.length === 0) return { clicked: false, reason: 'no button' };
  const el = candidates[0];
  let target = el, up = el;
  for (let i = 0; i < 12; i++) {
    if (!up.parentElement) break;
    up = up.parentElement;
    const tag = (up.tagName || '').toLowerCase();
    const role = up.getAttribute ? up.getAttribute('role') : null;
    if (tag === 'button' || tag === 'a' || role === 'button') { target = up; break; }
  }
  let nativeOk = false;
  let nativeErr = '';
  try { target.click(); nativeOk = true; } catch (e) { nativeErr = String(e); }
  const r = target.getBoundingClientRect();
  return {
    clicked: nativeOk,
    method: nativeOk ? 'native click()' : 'click failed',
    err: nativeErr,
    tag: target.tagName,
    text: (target.textContent || '').trim().slice(0, 30),
    cx: Math.round(r.x + r.width / 2),
    cy: Math.round(r.y + r.height / 2)
  };
})()
"""

JS_VERIFY = r"""
(() => {
  const texts = [];
  function walk(root) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      if (el.childElementCount === 0 && el.textContent && el.textContent.trim()) {
        texts.push(el.textContent.trim());
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  }
  walk(document);
  const hasClaim = texts.includes(__CLAIM_TEXT__);
  const hasClaimed = texts.some(t => /已领取|已签到|今日已|已打卡/.test(t));
  const hasIntegral = texts.some(t => /积分/.test(t));
  return { hasClaim, hasClaimed, hasIntegral };
})()
"""

# 扫描页面所有像"按钮"的短文本（含 领取/签到/打卡/积分 关键词），用于 setup 学习按钮文案
JS_SCAN_CLAIM_CANDIDATES = r"""
(() => {
  const out = [];
  const kw = /领取|签到|打卡|积分|加油/;
  function walk(root) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      if (el.childElementCount === 0 && el.textContent) {
        const t = el.textContent.trim();
        if (t && t.length <= 20 && kw.test(t)) out.push(t);
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  }
  walk(document);
  return [...new Set(out)];
})()
"""


def build_js(template, claim_text):
    return template.replace("__CLAIM_TEXT__",
                            json.dumps(claim_text, ensure_ascii=False))


def open_user_panel(ws_url, nickname):
    """点击主界面左下角用户卡片（显示名），打开用户菜单（部分版本需此兜底）。

    优先点击新版 UI 的 `button.user-menu-trigger`（5.5.x 结构），
    找不到再按昵称文本匹配兜底。返回 {'opened': bool, 'via': str, 'target': str}。
    """
    js = r"""
    (() => {
      const NICK = __NICKNAME__;
      let via = 'nickname-fallback';
      // 1) 新版 UI：左下角用户卡片触发器（button）。直接点按钮，click() 才会触发菜单
      let btn = document.querySelector('button.user-menu-trigger') ||
                document.querySelector('.user-menu-trigger');
      if (btn) via = 'user-menu-trigger';
      if (!btn) {
        // 2) 兜底：匹配昵称文本开头的可见元素，优先 button，其次取最后一个
        const hits = [...document.querySelectorAll('*')].filter(e => {
          const t = (e.textContent || '').trim();
          return t.indexOf(NICK) === 0 && t.length < 20;
        }).filter(e => e.offsetParent !== null);
        btn = hits.find(e => (e.tagName || '').toLowerCase() === 'button') ||
              [...hits].pop();
      }
      if (!btn) return { opened: false, via, target: '' };
      const tag = (btn.tagName || '').toLowerCase();
      const cls = btn.className ? String(btn.className).slice(0, 60) : '';
      try { btn.click(); return { opened: true, via, target: `${tag}.${cls}` }; }
      catch (e) { return { opened: false, via, target: `${tag}.${cls} err=${e}` }; }
    })()
    """.replace("__NICKNAME__", json.dumps(nickname, ensure_ascii=False))
    res = cdp_evaluate(ws_url, js)
    if isinstance(res, dict):
        return res
    return {"opened": bool(res), "via": "unknown", "target": ""}


def click_fuel_menu_entry(ws_url):
    """新版 UI（5.5.3+）：打开用户菜单后，点击菜单里的「Buddy加油站」条目
    （div.fuel-menu-entry / label.fuel-menu-entry__label）才会展开签到面板。
    支持 shadow DOM；'fuel' 类名未来若变化，可回退到文本定位兜底。
    只接受视口内可见的元素（避免误点对话正文里的同名字符串）。"""
    js = r"""
    (() => {
      function inViewport(el) {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 &&
               r.top < (window.innerHeight || document.documentElement.clientHeight) &&
               r.left < (window.innerWidth || document.documentElement.clientWidth);
      }
      // 1) 首选 .fuel-menu-entry（主文档 + shadow DOM），要求视口内可见
      let entry = null;
      const roots = [document];
      while (roots.length) {
        const root = roots.pop();
        let els = [];
        try { els = root.querySelectorAll ? [...root.querySelectorAll('.fuel-menu-entry')] : []; } catch (e) {}
        for (const el of els) { if (inViewport(el)) { entry = el; break; } }
        if (entry) break;
        try { for (const el of root.querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot); } catch (e) {}
      }
      // 2) 文本兜底：找视口内「Buddy加油站」开头的元素，且能上溯到 fuel/按钮祖先
      if (!entry) {
        const all = [...document.querySelectorAll('*')];
        const hit = all.filter(e => {
          const t = (e.textContent || '').trim();
          return t.indexOf('Buddy加油站') === 0 && t.length < 30 && inViewport(e);
        }).pop();
        if (hit) {
          let up = hit;
          for (let i = 0; i < 8 && up; i++) {
            const tag = (up.tagName || '').toLowerCase();
            const role = up.getAttribute ? up.getAttribute('role') : null;
            if (tag === 'button' || tag === 'a' || role === 'button' ||
                /fuel/.test(String(up.className || ''))) { entry = up; break; }
            up = up.parentElement;
          }
        }
      }
      if (!entry) return 'no-entry';
      const r = entry.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return 'invisible';
      try {
        entry.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        return 'clicked';
      } catch (e) {
        try { entry.click(); return 'clicked(native)'; } catch (e2) { return 'click-failed'; }
      }
    })()
    """
    return cdp_evaluate(ws_url, js)


# ---------------------------------------------------------------------------
# 签到主流程
# ---------------------------------------------------------------------------
def log(msg, logfile=None):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    if logfile:
        logfile.write(line + "\n")
        logfile.flush()


def get_work_targets(port):
    targets = get_targets(port)
    all_targets = [t for t in targets if t.get("webSocketDebuggerUrl")]
    work_targets = [t for t in all_targets
                    if "workbuddy" in (t.get("title") or "").lower()
                    or "workbuddy" in (t.get("url") or "").lower()
                    or "codebuddy" in (t.get("url") or "").lower()]
    return work_targets or all_targets


def run(cfg, dry=False, info_only=False, auto_launch=None):
    if auto_launch is None:
        auto_launch = bool(cfg.get("auto_launch", False))
    log_file = open(LOG_DIR / f"checkin_{datetime.now().strftime('%Y%m%d')}.log",
                    "a", encoding="utf-8")
    try:
        # 探测端口；若不可用且允许自动拉起，则自动启动 WorkBuddy
        port = detect_debug_port(cfg)
        if not cdp_is_workbuddy(port):
            if auto_launch:
                log(f"端口 {port} 无调试服务，自动拉起 WorkBuddy...", log_file)
                if not ensure_cdp(cfg):
                    log("❌ 无法确保调试服务就绪", log_file)
                    return "NO_CDP"
                port = detect_debug_port(cfg)
            else:
                log(f"❌ 端口 {port} 无调试服务（已禁用自动拉起）", log_file)
                return "NO_CDP"

        log(f"使用 CDP 端口: {port}", log_file)
        if info_only:
            targets = get_targets(port)
            log(f"CDP 端口正常，共 {len(targets)} 个调试目标:", log_file)
            for t in targets:
                log(f"  - {t.get('type')} title={t.get('title')} "
                    f"url={t.get('url', '')[:80]} ws={bool(t.get('webSocketDebuggerUrl'))}", log_file)
            return "INFO_OK"

        work_targets = get_work_targets(port)
        log(f"候选目标: {len(work_targets)} 个", log_file)

        # 跨午夜后刷新页面，避免界面仍显示昨日签到状态
        if work_targets:
            log("刷新页面以获取最新签到状态...", log_file)
            cdp_reload(work_targets[0]["webSocketDebuggerUrl"])
            log("页面已刷新", log_file)
            work_targets = get_work_targets(port)
            log(f"刷新后候选目标: {len(work_targets)} 个", log_file)

        js_find = build_js(JS_FIND_BUTTON, cfg["claim_text"])
        js_click = build_js(JS_CLICK, cfg["claim_text"])
        js_verify = build_js(JS_VERIFY, cfg["claim_text"])

        def try_click_all():
            """遍历所有候选页面查找并点击签到按钮，返回 rc"""
            for t in work_targets:
                ws_url = t.get("webSocketDebuggerUrl")
                title = t.get("title")
                log(f"\n--- 页面: {title} ---", log_file)
                if not ws_url:
                    log("  无 WebSocket 地址，跳过", log_file)
                    continue

                find_res = cdp_evaluate(ws_url, js_find)
                log(f"查找按钮: {json.dumps(find_res, ensure_ascii=False)[:300]}", log_file)
                if not isinstance(find_res, dict) or not find_res.get("found"):
                    # 无「立即领取」按钮：先看是否已显示「已领」状态（今日已签到）
                    verify = cdp_evaluate(ws_url, js_verify)
                    if isinstance(verify, dict) and verify.get("hasClaimed"):
                        log("  ✅ 今日已领取，无需重复签到", log_file)
                        return "ALREADY"
                    log(f"  未找到「{cfg['claim_text']}」按钮，跳过", log_file)
                    continue

                if dry:
                    log("  DRY 模式：不点击", log_file)
                    return "DRY_OK"

                click_res = cdp_evaluate(ws_url, js_click)
                log(f"点击结果: {json.dumps(click_res, ensure_ascii=False)[:300]}", log_file)
                if isinstance(click_res, dict) and click_res.get("clicked"):
                    time.sleep(3)
                    verify = cdp_evaluate(ws_url, js_verify)
                    log(f"验证: {json.dumps(verify, ensure_ascii=False)[:300]}", log_file)
                    if isinstance(verify, dict) and not verify.get("hasClaim"):
                        log(f"✅ 签到成功（「{cfg['claim_text']}」按钮已消失/变化）", log_file)
                        return "OK"
                    elif isinstance(verify, dict) and verify.get("hasClaimed"):
                        log("✅ 签到成功（页面出现已领取状态）", log_file)
                        return "OK"
                    else:
                        log("⚠️ 已点击但未确认状态变化", log_file)
                        return "UNCERTAIN"
            return "NO_BUTTON"

        rc = try_click_all()
        # 未找到按钮：点击左下角用户卡片打开用户菜单，再点「Buddy加油站」条目
        # 展开签到面板（5.5.3+ UI），然后重试。菜单偶发打不开，最多循环 3 轮。
        if rc == "NO_BUTTON" and cfg.get("nickname"):
            main_page_ws = next((t.get("webSocketDebuggerUrl") for t in work_targets
                                 if "WorkBuddy" in (t.get("title") or "")), None)
            if main_page_ws:
                for round_no in range(1, 4):
                    if rc != "NO_BUTTON":
                        break
                    log(f"第 {round_no}/3 轮：点击左下角用户卡片（{cfg['nickname']}）打开用户菜单...", log_file)
                    opened = open_user_panel(main_page_ws, cfg["nickname"])
                    log(f"打开用户菜单: {json.dumps(opened, ensure_ascii=False)[:200]}", log_file)
                    if not opened.get("opened"):
                        log(f"打开用户菜单失败: {opened.get('via', '?')} -> {opened.get('target', '')}", log_file)
                        break
                    # 菜单渲染有延迟，轮询等待「Buddy加油站」条目出现再点击
                    fuel_click = "no-entry"
                    tries = 0
                    for tries in range(1, 9):
                        time.sleep(1.2)
                        fuel_click = click_fuel_menu_entry(main_page_ws)
                        if fuel_click in ("clicked", "clicked(native)"):
                            break
                    log(f"菜单项点击结果(尝试{tries}次): {fuel_click}", log_file)
                    if fuel_click not in ("clicked", "clicked(native)"):
                        continue  # 菜单可能没真正打开，下一轮重试
                    time.sleep(4)
                    log("签到面板应已展开，重新查找...", log_file)
                    rc = try_click_all()
            else:
                log("未找到应用主页面", log_file)

        if rc not in ("OK", "ALREADY", "DRY_OK", "UNCERTAIN"):
            log("❌ 所有页面均未找到可点击的签到按钮", log_file)
        return rc

    except requests.exceptions.ConnectionError:
        log(f"❌ 无法连接 CDP 端口 {cfg['debug_port']}，"
            "请确认目标应用已带 --remote-debugging-port 启动", log_file)
        return "NO_CDP"
    except Exception as e:
        log(f"❌ 异常: {e}", log_file)
        import traceback
        log(traceback.format_exc(), log_file)
        return "ERROR"
    finally:
        log_file.close()


# ---------------------------------------------------------------------------
# 一键配置向导
# ---------------------------------------------------------------------------
def cmd_setup(cfg, register_task=True):
    print("=" * 62)
    print("  WorkBuddy 每日签到 · 一键配置向导")
    print("=" * 62)
    port = cfg.get("debug_port", 9222)

    # [1] 确保带调试端口的 WorkBuddy 在运行
    print(f"\n[1/5] 检查 WorkBuddy 调试服务（端口 {port}）...")
    if cdp_is_workbuddy(port):
        print("  ✅ 调试服务已就绪")
    else:
        if not ensure_cdp(cfg):
            print("❌ 无法启动/连接 WorkBuddy，向导中止。")
            return 1

    # [2] 检查登录状态，未登录则等待用户登录
    print("\n[2/5] 检查登录状态...")
    account = get_account_info(port)
    if account and account.get("uid"):
        print(f"  ✅ 已登录：{account.get('nickname', '')} (uid={account.get('uid')[:8]}…)")
    else:
        print("  ⚠️ 未检测到登录。")
        print("   ➜ 请在弹出的 WorkBuddy 窗口中完成登录（扫码或账号密码）。")
        print("   （最多等待 5 分钟，完成后会自动继续；随时 Ctrl+C 可中止）")
        deadline = time.time() + 300
        while time.time() < deadline:
            account = get_account_info(port)
            if account and account.get("uid"):
                print(f"  ✅ 检测到登录：{account.get('nickname', '')} "
                      f"(uid={account.get('uid')[:8]}…)")
                break
            # 定期给个提示，避免用户以为卡死
            remain = int(deadline - time.time())
            if remain % 20 == 0 and remain > 0:
                print(f"     …仍在等待登录（剩余约 {remain // 60} 分 {remain % 60} 秒）…")
            time.sleep(2)
        else:
            print("  ⏱ 等待登录超时（5 分钟）。可重新运行 --setup 继续。")
            return 1

    # [3] 自动生成/更新配置
    print("\n[3/5] 生成配置 config.json ...")
    nickname = account.get("nickname", "") if account else ""
    if nickname:
        cfg["nickname"] = nickname
        print(f"  → nickname = {nickname}（自动提取）")
    else:
        print("  → nickname 留空（未取到显示名，不影响基础签到）")

    # 校验签到按钮文案：扫描页面，若能找到就确认，找不到列出候选
    claim = cfg.get("claim_text", "立即领取")
    work_targets = get_work_targets(port)
    found_now = False
    if work_targets:
        ws_url = work_targets[0]["webSocketDebuggerUrl"]
        r = cdp_evaluate(ws_url, build_js(JS_FIND_BUTTON, claim))
        if isinstance(r, dict) and r.get("found"):
            found_now = True
        else:
            # 刷新一次再试（登录后界面可能未加载完）
            cdp_reload(ws_url, wait=6)
            work_targets = get_work_targets(port)
            if work_targets:
                ws_url = work_targets[0]["webSocketDebuggerUrl"]
                r = cdp_evaluate(ws_url, build_js(JS_FIND_BUTTON, claim))
                found_now = isinstance(r, dict) and r.get("found")

    if found_now:
        print(f"  → claim_text = {claim}（页面可找到该按钮 ✓）")
    else:
        verify = None
        if work_targets:
            verify = cdp_evaluate(work_targets[0]["webSocketDebuggerUrl"],
                                  build_js(JS_VERIFY, claim))
        claimed = isinstance(verify, dict) and verify.get("hasClaimed")
        if claimed:
            print(f"  → claim_text = {claim}（当前显示「今日已领」，文案正确 ✓）")
        else:
            cands = []
            if work_targets:
                cands = cdp_evaluate(work_targets[0]["webSocketDebuggerUrl"],
                                     JS_SCAN_CLAIM_CANDIDATES) or []
            print(f"  ⚠️ 页面暂未找到「{claim}」按钮。扫描到以下候选文本：")
            for i, c in enumerate(cands[:15]):
                print(f"     [{i + 1}] {c}")
            if interactive() and cands:
                try:
                    pick = input("  输入编号改用候选文案，或直接回车保持默认: ").strip()
                except EOFError:
                    pick = ""
                if pick.isdigit() and 1 <= int(pick) <= len(cands):
                    claim = cands[int(pick) - 1]
                    cfg["claim_text"] = claim
                    print(f"  → claim_text = {claim}")

    save_config(cfg)
    print(f"  ✅ 配置已写入: {Path(__file__).parent / 'config.json'}")

    # [4] 冒烟签到验证（不点击：dry 探测一次）
    print("\n[4/5] 签到链路自检...")
    rc = run(cfg, dry=True, auto_launch=False)
    if rc in ("DRY_OK", "NO_BUTTON", "ALREADY"):
        print("  ✅ 链路正常（能连接并扫描页面）。")
        if rc in ("NO_BUTTON", "ALREADY"):
            print("     （当前无「立即领取」按钮——可能今日已领，属正常）")
    else:
        print(f"  ⚠️ 自检返回 {rc}——配置完成但链路待观察，可稍后运行 "
              "`python checkin_cdp.py --dry` 复查。")

    # [5] 注册每日计划任务
    print("\n[5/5] 注册每日自动签到...")
    if register_task and ask_yes_no("  是否注册 Windows 计划任务（默认每天 07:00 自动签到）?",
                                    default=True):
        try:
            import register_windows_task as rwt
            rwt.register(task_name=rwt.DEFAULT_NAME, at="07:00",
                         script=str(Path(__file__).resolve()))
        except Exception as e:
            print(f"  ⚠️ 注册计划任务失败: {e}")
            print("     可稍后手动运行: python register_windows_task.py")
    else:
        print("  跳过计划任务注册。手动签到：python checkin_cdp.py")

    print()
    print("=" * 62)
    print("  ✅ 配置完成！以后无需再操作：")
    print("     · 计划任务每天自动签到（错过会补跑）")
    print("     · WorkBuddy 常驻时无需任何操作；若需自动拉起，将 config.json 的 auto_launch 设为 true")
    print("     手动补签/自检：python checkin_cdp.py")
    print("=" * 62)
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="WorkBuddy 每日自动签到")
    ap.add_argument("--setup", action="store_true",
                    help="一键配置向导（首次使用：自动启动/登录引导/生成配置/注册计划任务）")
    ap.add_argument("--dry", action="store_true", help="只检测按钮，不点击")
    ap.add_argument("--info", action="store_true", help="打印端口状态和页面列表")
    ap.add_argument("--no-auto-launch", action="store_true", dest="no_auto_launch",
                    help="连不上调试服务时不自动拉起 WorkBuddy，直接报错")
    ap.add_argument("--config", default=None, help="配置文件路径（默认读同目录 config.json）")
    ap.add_argument("--nickname", default=None, help="主界面左下角用户显示名（覆盖配置）")
    ap.add_argument("--claim-text", default=None, dest="claim_text",
                    help="签到按钮文案（覆盖配置，默认「立即领取」）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.nickname is not None:
        cfg["nickname"] = args.nickname
    if args.claim_text is not None:
        cfg["claim_text"] = args.claim_text

    # 全局保护：pythonw 静默运行时任何未捕获异常都落盘，避免无痕失败
    try:
        if args.setup:
            sys.exit(cmd_setup(cfg))
        rc = run(cfg, dry=args.dry, info_only=args.info,
                 auto_launch=None if not args.no_auto_launch else False)
        print(f"\nRESULT: {rc}")
        sys.exit(0 if rc in ("OK", "ALREADY", "NO_BUTTON", "DRY_OK", "INFO_OK", "UNCERTAIN") else 1)
    except Exception:
        import traceback
        crash_log = LOG_DIR / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        print(f"❌ 未捕获异常，详情: {crash_log}")
        sys.exit(2)

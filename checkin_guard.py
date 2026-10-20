# -*- coding: utf-8 -*-
"""checkin_guard.py —— 签到守卫：确保每天的积分不漏领

为什么需要它
------------
主签到（checkin_cdp.py）每天只在 07:00 出手一次。一旦这次失败（依赖被清理、CDP 未就绪、
页面结构变化、WorkBuddy 正在升级……），当天积分就白丢——而且积分按日发放、**不补发**。
2026-09-12 ~ 09-14 的连续三天静默失败就是这么发生的。

本脚本作为「守卫」，由 **WorkBuddy 的定时任务**在每天 **07:30**（主签到 07:00 之后）调用一次：
核验当天积分是否到账，未到账就自动排查 → 修复 → 重试，仍失败则告警。

两条领取路径（2026-10-12 起）
---------------------------
**路径 A · API（优先）** —— 调 WorkBuddy 自己的接口，幂等、不依赖界面。
2026-10-12 起启用：WorkBuddy 升到 5.8.1 后，签到面板从固定 DOM 改成了**滚动气泡**，
`section.fuel-card` / `.fuel-stats` 抓不到，界面路径不再可靠。接口路径不受改版影响。

**路径 B · CDP 兜底** —— 本项目原有的界面点击方式（点头像卡片 → Buddy加油站 → 点「立即领取」）。
接口不可用（没装技能 / 外网不通）时接管；对不依赖技能的独立使用者仍可单独使用。

两条路径的"到账"判定共用同一套证据逻辑，且都会写进 state.json。

核心概念
--------
- **调度**：WorkBuddy 定时任务，每天 07:30 一次（不用 Windows 计划任务）。
- **幂等**：当天已确认到账时立即退出（毫秒级），所以误触发或手动重跑都无副作用。
- **双证据验证**（不靠猜界面文案）：
    - 路径 A：① 接口 `daily-checkin` 回报「今日已签到 / 签到成功」（权威）
             ② 本期累计积分比历史基线增长（单调计数器）
    - 路径 B：① 主签到按钮文案「今日已领」且 `disabled=true`
             ② 面板「累计领取 N 分」比基线增长
  两条都成立 → high；只有一条 → medium（记录并留待复核）
- **抗网络抖动**：本机到 `www.codebuddy.cn` 有间歇性 TLS 干扰。
  先探连通性再调用，且只对**网络类**报错重试（登录态/权限类不重试）。
- **状态文件**：state.json 记录每日核验结果与基线，跨日比对的基础。

运行模式
--------
::

    python checkin_guard.py            # 执行一次守卫（WorkBuddy 定时任务每天 07:30 调用）
    python checkin_guard.py --status   # 只读状态，不做任何操作
    python checkin_guard.py --force    # 忽略「今日已确认」，强制重新核验
    python checkin_guard.py --no-auto-launch   # 连不上时不自动拉起 WorkBuddy

退出码
------
    0  当天已确认到账（本次核验通过，或此前已确认）
    1  参数/环境异常
    3  核验未通过（已尽力修复仍失败，已触发告警）
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)
STATE_PATH = HERE / "state.json"
STATE_KEEP_DAYS = 120          # state.json 只保留最近 N 天，避免无限膨胀

# 07:00 主签到的「就绪等待」窗口。2026-10-13~10-20 连续 8 天 07:00 首次尝试失败
# （通道 A/B/C 全不可用，或 www.codebuddy.cn 探测不通），而 07:30 首试即成功 ——
# 根因是电脑 06:33 左右从睡眠唤醒后，网络/代理/客户端登录态要到 07:00 之后才就绪，
# 脚本却是「试一次就退出」，把机会全让给了 07:30 的第二层。改为在同一个任务内
# 按递增间隔重试，环境一就绪就立刻签完，07:30 退回纯兜底角色。
#
# 2026-10-21 再修正：窗口 22 分钟（到 07:22）**不够**。当天 6 次尝试全部报
# 「无法取得可用凭据（通道 A/B/C 均不可用）」，07:30 一次即成。取证：
#   `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info`
#   的 mtime 恰为 07:30:36（新 WorkBuddy 进程 07:30:32 启动后重写），之后通道 A 立即可用。
# 即 5.6.2+ 的 accessToken 信封密钥只随客户端进程存活，客户端写回登录态前，
# 磁盘上的旧信封解不开 → 通道 A 不可用。**决定性事件发生在 07:30 前后**，
# 因此窗口必须跨过 07:30，而不是停在 07:22。
DEFAULT_WAIT_MINUTES = 40                     # 07:00 + 40 分 = 07:40，跨过客户端写回登录态的时刻
RETRY_GAPS_MIN = (2, 3, 5, 5, 7, 8, 8)        # 逐次等待间隔（分钟），累计 38；第 7 次落在 07:30


def _load_cc():
    """按路径加载 checkin_cdp.py（不依赖 cwd，计划任务用绝对路径调用也能工作）"""
    spec = importlib.util.spec_from_file_location("checkin_cdp", HERE / "checkin_cdp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cc = _load_cc()


def log(msg, logfile=None):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    if logfile:
        logfile.write(line + "\n")
        logfile.flush()


# ---------------------------------------------------------------------------
# 状态文件
# ---------------------------------------------------------------------------
def load_state():
    if STATE_PATH.exists():
        try:
            st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(st, dict) and isinstance(st.get("days"), dict):
                return st
        except Exception as e:
            print(f"[warn] state.json 读取失败（将重建）: {e}")
    return {"version": 1, "days": {}}


def save_state(st):
    days = st.get("days", {})
    if len(days) > STATE_KEEP_DAYS:
        for k in sorted(days)[:-STATE_KEEP_DAYS]:
            days.pop(k, None)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def baseline_for(st, today):
    """当天之前的最近一条含累计积分的记录，作为计数证据的基线"""
    best = None
    for d in sorted(st.get("days", {})):
        if d < today:
            rec = st["days"][d]
            if rec.get("total_points") is not None:
                best = {"date": d, "total_points": rec.get("total_points"),
                        "total_days": rec.get("total_days")}
    return best


# ---------------------------------------------------------------------------
# 双证据判定
# ---------------------------------------------------------------------------
def judge(panel, baseline):
    """判定当天是否真的到账。

    返回 (verdict, confidence, evidence, flags)：
        verdict     'verified' | 'unverified'
        confidence  'high'（双证据）| 'medium'（单证据）| 'none'
        evidence    人类可读的证据列表
        flags       异常标记，如 ['period_reset']
    """
    ev, flags = [], []
    if not panel.get("open"):
        return "unverified", "none", [f"面板未展开：{panel.get('reason', '?')}"], flags

    btn_text = (panel.get("btnText") or "").strip()
    btn_disabled = panel.get("btnDisabled")
    btn_claimed = (btn_disabled is True) and bool(cc.CLAIMED_RE.search(btn_text))
    if btn_claimed:
        ev.append(f"按钮「{btn_text}」且 disabled=true")

    pts = panel.get("total_points")
    base_pts = (baseline or {}).get("total_points")
    grew = None
    if pts is not None and base_pts is not None:
        delta = pts - base_pts
        if delta > 0:
            grew = True
            ev.append(f"累计积分 {base_pts} → {pts}（+{delta}）")
        elif delta == 0:
            grew = False
            ev.append(f"累计积分未变化（{pts}，基线 {baseline['date']}）")
        else:
            flags.append("period_reset")
            ev.append(f"累计积分下降 {base_pts} → {pts}（疑似进入新一期，计数证据作废）")
    elif pts is None:
        ev.append("未能解析累计积分")
    else:
        ev.append(f"无历史基线（首次运行），本次累计积分 {pts}")

    if btn_claimed and grew is True:
        return "verified", "high", ev, flags
    if btn_claimed:
        return "verified", "medium", ev, flags
    if grew is True:
        return "verified", "medium", ev, flags
    return "unverified", "none", ev, flags


def record(st, today, panel, baseline, verdict, confidence, evidence, flags,
           attempts, source, rc=None):
    """把当天核验结果写入 state.json"""
    rec = st["days"].get(today, {})
    rec.update({
        "verified": verdict == "verified",
        "confidence": confidence,
        "status_text": panel.get("btnText"),
        "btn_disabled": panel.get("btnDisabled"),
        "total_days": panel.get("total_days"),
        "total_points": panel.get("total_points"),
        "daily_quota": panel.get("score"),
        "period": panel.get("period"),
        "baseline": baseline,
        "evidence": evidence,
        "flags": flags,
        "attempts": attempts,
        "last_check_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
    })
    if rc:
        rec["last_rc"] = rc
    if verdict == "verified":
        rec.setdefault("first_ok_at", datetime.now().isoformat(timespec="seconds"))
        rec["verified_at"] = datetime.now().isoformat(timespec="seconds")
    st["days"][today] = rec
    save_state(st)
    return rec


# ---------------------------------------------------------------------------
# 告警（本地优先，不外发）
# ---------------------------------------------------------------------------
def raise_alert(today, reason, detail_lines):
    """写醒目告警文件 + 尽力弹 Windows 通知。返回告警文件路径。"""
    path = LOG_DIR / f"GUARD_ALERT_{today.replace('-', '')}.txt"
    body = [f"WorkBuddy 每日签到告警  {today}",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"原因: {reason}", ""]
    body += detail_lines
    body += ["", "处理建议:",
             "  1. 确认 WorkBuddy 正在运行且带 --remote-debugging-port=9222",
             "  2. 手动运行: python checkin_cdp.py    （看完整日志）",
             "  3. 手动核验: python checkin_guard.py --status"]
    path.write_text("\n".join(body), encoding="utf-8")
    print(f"⚠️ 已写入告警文件: {path}")

    # Windows 通知（best-effort：失败不影响告警文件）
    try:
        ps = ("[reflection.assembly]::LoadWithPartialName('System.Windows.Forms')|Out-Null;"
              "[reflection.assembly]::LoadWithPartialName('System.Drawing')|Out-Null;"
              "$n=New-Object System.Windows.Forms.NotifyIcon;"
              "$n.Icon=[System.Drawing.SystemIcons]::Warning;$n.Visible=$true;"
              "$n.ShowBalloonTip(15000,'WorkBuddy 签到告警',"
              "'今日积分未确认到账，请查看 logs/GUARD_ALERT 文件',3);"
              "Start-Sleep -Seconds 8;$n.Dispose()")
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                          "-Command", ps],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"  （Windows 通知未送达，不影响告警文件: {e}）")
    return path


# ---------------------------------------------------------------------------
# 守卫主流程
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 路径 A：API 优先签到（不依赖界面，抗 UI 改版）
# ---------------------------------------------------------------------------
# 2026-10-12 起启用：WorkBuddy 升到 5.8.1 后签到面板从固定 DOM 变成滚动气泡，
# CDP 抓不到 → 界面路径不再可靠。改走 WorkBuddy 自己的接口（幂等），
# CDP 保留为兜底（本机专属脚本，发布版仍可用界面路径）。
SKILL_SCRIPT = (Path.home() / ".workbuddy" / "skills" / "totorosir-workbuddy-score"
                / "scripts" / "buddy_station.py")
API_HOME = "https://www.codebuddy.cn/"


def api_ping(timeout=10, retries=3):
    """探测 codebuddy.cn 连通性。

    **刻意不屏蔽代理**（实测 2026-10-12，A/B 交替各跑 6 次状态查询）：
        走本机代理 127.0.0.1:6664 → 成功 6/6，平均 10.7s
        直连（清空 *_proxy 变量）    → 成功 4/6，平均 24.5s
    本机宽带到该域名的**直连 TLS 不稳定**（与 GitHub 的 SNI/TLS 干扰同源），
    走本机代理反而更稳。技能内部用裸 urllib.request.urlopen，同样自动采用
    环境代理 —— 这里必须与之一致，否则探测结果不能代表真实调用。
    """
    opener = urllib.request.build_opener()      # 沿用环境变量里的代理
    for i in range(retries):
        try:
            with opener.open(API_HOME, timeout=timeout) as r:
                if r.status == 200:
                    return True
        except Exception:
            if i < retries - 1:
                time.sleep(2)
    return False


def run_skill(extra_args, timeout=120):
    """调用技能脚本，返回 (returncode, stdout, stderr)。脚本不会打印 token。"""
    cmd = [sys.executable, str(SKILL_SCRIPT), "--no-push"] + list(extra_args)
    # 子进程默认按系统 ANSI 代码页输出（中文 Windows = GBK），父进程按 UTF-8 解码
    # 会得到一串乱码 —— 2026-10-19 定位根因时就被这个坑过。强制子进程输出 UTF-8。
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=env)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "技能脚本超时"
    except Exception as e:
        return -1, "", f"{type(e).__name__}: {e}"


def _is_network_error(msg):
    """判断报错是否属于网络层间歇故障（值得重试），而非业务/凭据问题。

    本机到 www.codebuddy.cn 有间歇性 TLS 干扰（实测 SSL UNEXPECTED_EOF /
    handshake timed out / Remote end closed / HTTP 0），这类错误重试即可恢复；
    登录态失效、参数错误之类则重试无意义。
    """
    m = (msg or "").lower()
    return any(k in m for k in (
        "http 0", "ssl", "eof", "timeout", "timed out", "handshake",
        "urlopen", "remote end closed", "connection reset", "connection aborted",
        "target closed", "incomplete read"))


def _is_credential_error(msg):
    """判断报错是否属于「本机登录态未就绪」类（凭据侧，不是网络侧）。

    典型报文：`无法取得可用凭据：无法取得任何可用凭据（通道 A/B/C 均不可用）`。
    成因多为 5.6.2+ 的 accessToken 信封密钥只随客户端进程存活，客户端尚未写回
    登录态文件时旧信封解不开。**这类错误不要当网络故障处理，也绝不去动凭据**——
    客户端写回登录态（通常紧随客户端进程启动）后即可自动恢复。
    """
    m = (msg or "").lower()
    return any(k in m for k in (
        "凭据", "登录态", "accesstoken", "通道 a/b/c", "credential", "unauthorized"))


# 最近一次接口调用的失败定性，供 _fail 的「判定依据」使用（避免只写"接口不可用"）
_LAST_API_NOTE = ""


def try_api_checkin(baseline, logfile):
    """接口方式签到 + 核验。

    返回 dict（verdict/confidence/evidence/flags/credits/action）或
    None —— 表示本路径不可用，应交给 CDP 兜底。
    """
    global _LAST_API_NOTE
    _LAST_API_NOTE = ""
    if not SKILL_SCRIPT.exists():
        log(f"[API] 未找到技能脚本，跳过：{SKILL_SCRIPT}", logfile)
        return None
    if not api_ping():
        # 探测失败只作提示、不再直接放弃：电脑从睡眠唤醒后，网络/代理往往滞后于
        # 计划任务启动；而通道 A（本机登录态）本身不需要外网，探测失败不代表签到
        # 一定做不了。2026-10-20 07:00 就是被这句「不可达 → 跳过」误杀，连技能都
        # 没调。改成交给技能自己判定，真做不了再返回 None。
        log("[API] 连通性探测未通过（网络/代理可能未就绪），仍尝试调用技能...", logfile)

    log("[API] 调用接口签到（幂等，不依赖界面）...", logfile)
    data, degraded, err = None, False, ""
    for attempt in range(1, 4):
        rc, out, err = run_skill(["checkin", "--json"])
        try:
            data = json.loads(out)
        except Exception:
            data = None
            degraded = True
        msg = (((data or {}).get("checkin_result") or {}).get("message")) or err or ""
        if data is not None and not _is_network_error(msg):
            break
        if attempt < 3 and _is_network_error(msg):
            log(f"[API] 第 {attempt}/3 次遇网络干扰，3 秒后重试：{msg.strip()[:80]}", logfile)
            time.sleep(3)
            continue
        break

    if data is None:
        # 技能在「签到后状态查询」失败时不输出 JSON；再单独查一次状态，用累计计数判定
        log(f"[API] 签到未返回 JSON（rc={rc}）：{(err or '').strip()[:120]}", logfile)
        cred = _is_credential_error(err) or _is_credential_error(msg)
        if cred:
            log("[凭据] 本机登录态（通道 A）暂不可用：客户端尚未写回登录态文件，"
                "旧信封解不开。属客户端进程侧问题，**不是网络故障、不要动凭据**，"
                "等客户端写回（通常紧随其进程启动）即自动恢复。", logfile)
        rc2, out2, err2 = run_skill(["--json"])
        try:
            data = json.loads(out2)
        except Exception:
            log(f"[API] 状态查询同样失败 → 交给 CDP 兜底：{(err2 or '').strip()[:100]}", logfile)
            _LAST_API_NOTE = (
                "凭据未就绪：本机登录态（通道 A/B/C）不可用，等客户端写回登录态"
                if (cred or _is_credential_error(err2))
                else "外网/代理链路不通：" + ((err or err2 or "").strip()[:80] or "无输出"))
            return None
        degraded = True

    cr = data.get("checkin_result") or {}
    action = cr.get("action")
    credits = data.get("total_credits")
    total_days = data.get("checkin_dates_total")
    ev, flags = [], []

    base_pts = (baseline or {}).get("total_points")
    grew = None
    if credits is not None and base_pts is not None:
        d = credits - base_pts
        if d > 0:
            grew = True
            ev.append(f"累计积分 {base_pts} → {credits}（+{d}）")
        elif d == 0:
            grew = False
            ev.append(f"累计积分未变化（{credits}）")
        else:
            flags.append("period_reset")
            ev.append(f"累计积分下降 {base_pts} → {credits}（疑似新一期，计数证据作废）")
    elif credits is not None:
        ev.append(f"无历史基线（首次运行），本次累计积分 {credits}")

    if action == "skipped" and cr.get("already_checked_in"):
        ev.insert(0, "接口报告：今日已签到（daily-checkin 返回 code 10001）")
    elif action == "success":
        ev.insert(0, "接口报告：签到成功")
    elif action:
        ev.insert(0, f"接口报告：{action} {cr.get('message') or ''}".strip())

    api_ok = action in ("skipped", "success")
    if api_ok:
        verdict, conf = "verified", "high"          # 接口自身是权威判据
    elif grew is True:
        verdict, conf = "verified", "medium"        # 计数在长，接口只是没给出结论
    else:
        verdict, conf = "unverified", "none"
    if degraded:
        flags.append("api_degraded")

    log(f"[API] 判定: {verdict}/{conf} | " + " ; ".join(ev), logfile)
    return {"verdict": verdict, "confidence": conf, "evidence": ev, "flags": flags,
            "credits": credits, "total_days": total_days,
            "action": action, "degraded": degraded}


def check_environment(cfg, logfile):
    """L1 依赖 + L2 通道。返回 (ok, port, msg)。"""
    deps_ok, deps_msg = cc.check_deps(auto_repair=True)
    log(f"[L1 依赖] {deps_msg}", logfile)
    if not deps_ok:
        return False, None, "依赖不可用（自愈失败）"

    port = cc.detect_debug_port(cfg)
    if not cc.cdp_is_workbuddy(port):
        log(f"[L2 通道] 端口 {port} 未发现 WorkBuddy 调试服务，重新扫描...", logfile)
        cfg2 = dict(cfg)
        cfg2["debug_port"] = 0        # 强制走 full scan
        port = cc.detect_debug_port(cfg2)
    if not cc.cdp_is_workbuddy(port):
        return False, port, f"CDP 不可达（端口 {port}）"
    log(f"[L2 通道] CDP 就绪，端口 {port}", logfile)
    return True, port, "OK"


def read_state_via_panel(port, cfg, logfile):
    """打开面板并读取状态（含 L3 的刷新/重开阶梯）。返回 panel dict 或 None"""
    ws = cc.find_main_ws(port)
    if not ws:
        log("[L3 页面] 找不到 WorkBuddy 主页面", logfile)
        return None

    panel = cc.read_panel_state(ws)
    if panel.get("open"):
        return panel

    log("[L3 页面] 面板未展开 → 打开（点头像卡片 → Buddy加油站）", logfile)
    if cc.open_fuel_panel(ws, cfg.get("nickname") or "", logf=lambda m: log(m, logfile)) == "opened":
        return cc.read_panel_state(ws)

    log("[L3 页面] 打开失败 → 刷新页面后重试", logfile)
    try:
        cc.cdp_reload(ws, wait=6)
    except Exception as e:
        log(f"  刷新页面失败: {e}", logfile)
    ws = cc.find_main_ws(port) or ws
    if cc.open_fuel_panel(ws, cfg.get("nickname") or "", logf=lambda m: log(m, logfile)) == "opened":
        return cc.read_panel_state(ws)
    return cc.read_panel_state(ws)


def guard_once(cfg, force=False, auto_launch=None, use_cdp=False, final=False):
    """执行一次守卫。

    use_cdp=False（默认）：只走接口路径。WorkBuddy 5.8.1 起签到面板改为滚动气泡，
        界面路径已不可靠；接口更稳且不受改版影响。
    use_cdp=True：接口未确认到账时，再尝试界面点击（仅对旧版本 WorkBuddy 有意义）。
    """
    today = date.today().isoformat()
    logfile = open(LOG_DIR / f"guard_{today.replace('-', '')}.log", "a", encoding="utf-8")
    try:
        st = load_state()
        rec = st["days"].get(today, {})
        if rec.get("verified") and not force:
            log(f"✅ {today} 已确认到账"
                f"（{rec.get('total_points')} 分 / 置信度 {rec.get('confidence')} / "
                f"{rec.get('verified_at')}），无需处理 → 跳过", logfile)
            return "ALREADY_VERIFIED"

        attempts = int(rec.get("attempts", 0)) + 1
        baseline = baseline_for(st, today)
        log(f"===== 守卫检查 #{attempts}（{today}）=====", logfile)
        log(f"基线: {json.dumps(baseline, ensure_ascii=False)}", logfile)

        # ===== 路径 A：API 优先 =====
        # 5.8.1 起签到面板改成滚动气泡、CDP 抓不到，故先走接口（幂等、不依赖界面）。
        api = try_api_checkin(baseline, logfile)
        if api and api["verdict"] == "verified":
            panel = {"open": True, "btnText": f"(API {api['action']})",
                     "btnDisabled": None, "total_points": api.get("credits"),
                     "total_days": api.get("total_days"), "score": None, "reason": None}
            record(st, today, panel, baseline, "verified", api["confidence"],
                   api["evidence"], api["flags"], attempts, source="api")
            log(f"✅ 当天积分已确认到账（{api.get('credits')} 分，"
                f"置信度 {api['confidence']}，来源 API）", logfile)
            return "OK"

        # ===== 路径 B：CDP 兜底（默认不启用）=====
        # 2026-10-12 起默认只走 API：5.8.1 的签到面板已改成滚动气泡，界面路径
        # 实测既读不到状态、也点不到按钮。保留仅为 WorkBuddy 旧版本（≤5.5.x）备用。
        if not use_cdp:
            ev = (api or {}).get("evidence") or []
            return _fail(st, today, attempts,
                         "接口路径未确认到账（未启用界面兜底）",
                         "； ".join(ev) or _LAST_API_NOTE or "接口不可用", logfile, final=final)
        log("[CDP] 已启用界面兜底路径", logfile)
        ok, port, msg = check_environment(cfg, logfile)
        if not ok:
            return _fail(st, today, attempts, "环境不可用", msg, logfile, final=final)

        # L3 + 首次判定（可能主签到已领但没写状态，先白捡一次）
        panel = read_state_via_panel(port, cfg, logfile)
        if panel is None:
            return _fail(st, today, attempts, "无法读取签到面板",
                         "接口路径未到账，且找不到主页面", logfile, final=final)

        verdict, conf, ev, flags = judge(panel, baseline)
        log(f"核验: {verdict}/{conf} | " + " ; ".join(ev), logfile)
        if verdict == "verified":
            record(st, today, panel, baseline, verdict, conf, ev, flags,
                   attempts, source="guard")
            cc.close_fuel_panel(cc.find_main_ws(port) or "")
            log(f"✅ 当天积分已确认到账（{panel.get('total_points')} 分，置信度 {conf}）", logfile)
            return "OK"

        # L4 未到账 → 执行签到流程。分两级，尽量不打扰正在使用 WorkBuddy 的用户：
        #   L4a 不刷新页面（轻量，只是打开面板点一下）
        #   L4b 刷新页面后重试（较重，能清掉跨午夜/昨日的 UI 缓存）
        panel2, rc_used = panel, None
        ev2, flags2 = [], []
        for stage, do_reload in (("L4a", False), ("L4b", True)):
            log(f"[{stage} 领取] 尚未确认到账，执行签到流程"
                f"（reload={do_reload}）...", logfile)
            rc = cc.run(cfg, auto_launch=auto_launch, reload=do_reload)
            rc_used = rc
            log(f"[{stage} 领取] 签到返回: {rc}", logfile)

            time.sleep(2)
            panel2 = read_state_via_panel(port, cfg, logfile) or panel
            verdict2, conf2, ev2, flags2 = judge(panel2, baseline)
            log(f"[{stage} 复核] {verdict2}/{conf2} | " + " ; ".join(ev2), logfile)
            if verdict2 == "verified":
                record(st, today, panel2, baseline, verdict2, conf2, ev2, flags2,
                       attempts, source="guard", rc=rc)
                main_ws = cc.find_main_ws(port)
                if main_ws:
                    cc.close_fuel_panel(main_ws)
                log(f"✅ 当天积分已确认到账（{panel2.get('total_points')} 分，"
                    f"置信度 {conf2}）", logfile)
                return "OK"
            if rc in ("NO_CDP", "DEP_FAIL"):
                break     # 环境类故障，刷新也没用，直接交给下个检查点

        record(st, today, panel2, baseline, "unverified", "none", ev2, flags2,
               attempts, source="guard", rc=rc_used)
        if rc_used == "OK":
            # 领取动作本身成功了，只是界面读不到状态（5.8.1 起面板改为滚动气泡）。
            # 这类"假失败"必须说清楚，否则会被误判成丢分。
            reason = ("签到流程返回 OK，但界面核验未通过 —— 5.8.1 起签到面板改为滚动气泡、"
                      "已读不到状态。请以 API 路径的判定为准，勿据此认定丢分")
        else:
            reason = f"签到流程（含刷新重试）返回 {rc_used}，仍未确认到账"
        return _fail(st, today, attempts, reason, " ; ".join(ev2), logfile,
                     panel=panel2, final=final)

    except Exception as e:
        import traceback
        log(f"❌ 守卫异常: {e}\n{traceback.format_exc()}", logfile)
        return _fail(load_state(), today, 99, f"守卫异常: {e}", "", logfile, final=final)
    finally:
        logfile.close()


def _fail(st, today, attempts, reason, detail, logfile, panel=None, final=False):
    """核验失败：写状态。final=True 表示这是当天最后一次检查 → 立即升级告警。

    不再用「当前小时」判断最后窗口 —— 一天里现在有两次调用
    （07:00 的 Windows 计划任务、07:30 的 WorkBuddy 定时任务），
    只有 07:30 那次传 --final，早上的失败还有补救机会、不该惊动用户。
    """
    rec = st["days"].get(today, {})
    rec.setdefault("verified", False)
    rec["last_failure"] = reason
    rec["attempts"] = attempts
    rec["last_check_at"] = datetime.now().isoformat(timespec="seconds")
    st["days"][today] = rec
    save_state(st)

    lines = [f"检查次数: 第 {attempts} 次",
             f"失败说明: {reason}",
             f"判定依据: {detail or '（无）'}"]
    if panel:
        lines += [f"面板按钮: {panel.get('btnText')!r} disabled={panel.get('btnDisabled')}",
                  f"累计: {panel.get('total_days')} 天 / {panel.get('total_points')} 分",
                  f"面板文本: {panel.get('panelText', '')[:160]}"]
    lines.append(f"守卫日志: {LOG_DIR / ('guard_' + today.replace('-', '') + '.log')}")
    log("❌ " + " | ".join(lines[:4]), logfile)

    is_final = final
    if is_final:
        path = raise_alert(today, reason, lines)
        log(f"⛔ 已到最后检查窗口（--final），当日积分确认丢失。告警: {path}", logfile)
    else:
        log("ℹ️ 非最后检查窗口（未加 --final），状态已记入 state.json；"
            "若本任务内还有等待窗口，会自动重试", logfile)
    return "FAILED"


def log_wait(nap_sec, remain_sec):
    """把「环境未就绪、多久后再试」写进当天日志（与 guard_once 共用同一个文件）。"""
    today = date.today().isoformat()
    try:
        with open(LOG_DIR / f"guard_{today.replace('-', '')}.log", "a",
                  encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ⏳ 尚未到账 → "
                    f"{nap_sec // 60} 分 {nap_sec % 60:02d} 秒后重试"
                    f"（本任务剩余等待约 {max(0, int(round(remain_sec / 60)))} 分钟）\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 只读状态
# ---------------------------------------------------------------------------
def cmd_status(cfg):
    today = date.today().isoformat()
    st = load_state()
    print("=" * 62)
    print(f"  WorkBuddy 签到守卫 · 状态  {today}")
    print("=" * 62)
    rec = st["days"].get(today)
    if rec:
        print(f"  今天记录: 已确认={rec.get('verified')} 置信度={rec.get('confidence')} "
              f"检查次数={rec.get('attempts')}")
        print(f"           累计 {rec.get('total_days')} 天 / {rec.get('total_points')} 分 "
              f"（额度 {rec.get('daily_quota')}）")
        print(f"           证据: {' ; '.join(rec.get('evidence') or [])}")
        if rec.get("last_failure"):
            print(f"           最近失败: {rec['last_failure']}")
    else:
        print("  今天尚无记录。")
    bl = baseline_for(st, today)
    print(f"  基线: {json.dumps(bl, ensure_ascii=False) if bl else '（无）'}")

    days = sorted(st.get("days", {}))
    print(f"\n  最近 {min(7, len(days))} 天:")
    for d in days[-7:]:
        r = st["days"][d]
        mark = "✅" if r.get("verified") else "❌"
        print(f"    {mark} {d}  {r.get('total_points')} 分  "
              f"置信度={r.get('confidence')}  检查{r.get('attempts')}次")

    # 实时读一次页面（只读，不点击领取）
    print("\n  实时面板（只读）:")
    try:
        port = cc.detect_debug_port(cfg)
        if not cc.cdp_is_workbuddy(port):
            print(f"    ⚠️ 端口 {port} 无 WorkBuddy 调试服务")
        else:
            panel = read_state_via_panel(port, cfg, _NullLog())
            if panel and panel.get("open"):
                print(f"    按钮={panel.get('btnText')!r} disabled={panel.get('btnDisabled')}")
                print(f"    累计={panel.get('total_days')} 天 / {panel.get('total_points')} 分"
                      f"  期数={panel.get('period')}")
                verdict, conf, ev, flags = judge(panel, bl)
                print(f"    判定={verdict} 置信度={conf} :: {' ; '.join(ev)}")
                cc.close_fuel_panel(cc.find_main_ws(port) or "")
            else:
                print(f"    无法读取面板: {(panel or {}).get('reason', '?')}")
    except Exception as e:
        print(f"    （读取失败: {e}）")
    print("=" * 62)
    return 0


class _NullLog:
    """只读模式（--status）下的空日志对象。

    必须同时兼容两种用法，否则实时面板探测会中途抛异常：
      - logfile 用法：`log()` 会调用 .write()/.flush()
      - logf 用法：`open_fuel_panel(logf=...)` 会当函数调用
    """

    def __call__(self, msg):
        pass

    def write(self, s):
        return len(s) if isinstance(s, str) else 0

    def flush(self):
        pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="WorkBuddy 签到守卫（确保当天积分不漏领）")
    ap.add_argument("--status", action="store_true", help="只读状态，不做任何操作")
    ap.add_argument("--force", action="store_true", help="忽略「今日已确认」，强制重新核验")
    ap.add_argument("--no-auto-launch", action="store_true", dest="no_auto_launch",
                    help="连不上时不自动拉起 WorkBuddy")
    ap.add_argument("--use-cdp", action="store_true",
                    help="接口未确认到账时，再尝试界面点击兜底（仅旧版本 WorkBuddy 有意义）")
    ap.add_argument("--final", action="store_true",
                    help="标记这是当天最后一次检查（失败立即升级告警）。"
                         "早上的计划任务不加，07:30 的定时任务要加")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--nickname", default=None, help="主界面左下角用户显示名（覆盖配置）")
    ap.add_argument("--wait-minutes", type=int, default=None, dest="wait_minutes",
                    help=f"首次未到账时在同一任务内等待/重试的窗口（分钟）。"
                         f"默认：--final 时为 0，否则 {DEFAULT_WAIT_MINUTES}；传 0 关闭")
    args = ap.parse_args()

    cfg = cc.load_config(args.config)
    if args.nickname is not None:
        cfg["nickname"] = args.nickname

    if args.status:
        sys.exit(cmd_status(cfg))

    auto_launch = False if args.no_auto_launch else None
    # --final 是当天最后一次检查，等也没意义；其余场景默认给一个就绪等待窗口。
    wait_min = args.wait_minutes
    if wait_min is None:
        wait_min = 0 if args.final else DEFAULT_WAIT_MINUTES

    try:
        rc = "FAILED"
        deadline = time.monotonic() + max(0, wait_min) * 60
        max_tries = len(RETRY_GAPS_MIN) + 1
        for i in range(max_tries):
            rc = guard_once(cfg, force=args.force, auto_launch=auto_launch,
                            use_cdp=args.use_cdp, final=args.final)
            if rc in ("OK", "ALREADY_VERIFIED"):
                break
            # --force/--final 是收尾核验，不该再拖时间；最后一轮也直接收工
            if args.final or args.force or i == max_tries - 1:
                break
            remain = deadline - time.monotonic()
            if remain < 45:
                break
            nap = int(min(RETRY_GAPS_MIN[min(i, len(RETRY_GAPS_MIN) - 1)] * 60, remain))
            log_wait(nap, remain - nap)
            time.sleep(nap)

        print(f"\nRESULT: {rc}")
        sys.exit(0 if rc in ("OK", "ALREADY_VERIFIED") else 3)
    except Exception:
        import traceback
        crash = LOG_DIR / f"guard_crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        crash.write_text(traceback.format_exc(), encoding="utf-8")
        print(f"❌ 守卫未捕获异常，详情: {crash}")
        sys.exit(1)

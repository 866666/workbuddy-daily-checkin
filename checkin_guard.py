# -*- coding: utf-8 -*-
"""checkin_guard.py —— 签到守卫：确保每天的积分不漏领

为什么需要它
------------
主签到（checkin_cdp.py）每天只在 07:00 出手一次。一旦这次失败（依赖被清理、CDP 未就绪、
页面结构变化、WorkBuddy 正在升级……），当天积分就白丢——而且积分按日发放、**不补发**。
2026-09-12 ~ 09-14 的连续三天静默失败就是这么发生的。

本脚本作为「守卫」，由 **WorkBuddy 的定时任务**在每天 **07:30**（主签到 07:00 之后）调用一次：
核验当天积分是否到账，未到账就自动排查 → 修复 → 重试，仍失败则告警。

核心概念
--------
- **调度**：WorkBuddy 定时任务，每天 07:30 一次（不用 Windows 计划任务）。
- **幂等**：当天已确认到账时立即退出（毫秒级），所以误触发或手动重跑都无副作用。
- **双证据验证**（不靠猜界面文案）：
    1. 状态证据：`div.fuel-actions > button.fuel-btn` 文案匹配「今日已领」且 `disabled=true`
    2. 计数证据：`div.fuel-stats` 的「累计领取 N 分」是单调计数器，比基线大 = 当天确实入账
  两条都成立 → high；只有一条 → medium（记录并留待复核）
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
import subprocess
import sys
import time
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
FINAL_CHECK_HOUR = 7           # 当天唯一一次检查在 07:30（主签到 07:00 之后），
                               # 因此 07:00 以后的守卫运行都视为「最后窗口」：失败立即告警，
                               # 不留「下个检查点再试」的模糊结论。


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


def guard_once(cfg, force=False, auto_launch=None):
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
        log(f"===== 守卫检查 #{attempts}（{today}）=====", logfile)

        # L1/L2
        ok, port, msg = check_environment(cfg, logfile)
        if not ok:
            return _fail(st, today, attempts, "环境不可用", msg, logfile)

        baseline = baseline_for(st, today)
        log(f"基线: {json.dumps(baseline, ensure_ascii=False)}", logfile)

        # L3 + 首次判定（可能主签到已领但没写状态，先白捡一次）
        panel = read_state_via_panel(port, cfg, logfile)
        if panel is None:
            return _fail(st, today, attempts, "无法读取签到面板", "找不到主页面", logfile)

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
        return _fail(st, today, attempts,
                     f"签到流程（含刷新重试）返回 {rc_used}，仍未确认到账",
                     " ; ".join(ev2), logfile, panel=panel2)

    except Exception as e:
        import traceback
        log(f"❌ 守卫异常: {e}\n{traceback.format_exc()}", logfile)
        return _fail(load_state(), today, 99, f"守卫异常: {e}", "", logfile)
    finally:
        logfile.close()


def _fail(st, today, attempts, reason, detail, logfile, panel=None):
    """核验失败：写状态；若已到当日最后窗口则升级告警。"""
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

    is_final = datetime.now().hour >= FINAL_CHECK_HOUR
    if is_final:
        path = raise_alert(today, reason, lines)
        log(f"⛔ 已过最后窗口（{FINAL_CHECK_HOUR}:00），当日积分确认丢失。告警: {path}", logfile)
    else:
        log(f"ℹ️ 未到最后窗口，下个检查点继续重试（state.json 已记录）", logfile)
    return "FAILED"


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
                    help="连不上调试服务时不自动拉起 WorkBuddy")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--nickname", default=None, help="主界面左下角用户显示名（覆盖配置）")
    args = ap.parse_args()

    cfg = cc.load_config(args.config)
    if args.nickname is not None:
        cfg["nickname"] = args.nickname

    if args.status:
        sys.exit(cmd_status(cfg))

    auto_launch = False if args.no_auto_launch else None
    try:
        rc = guard_once(cfg, force=args.force, auto_launch=auto_launch)
        print(f"\nRESULT: {rc}")
        sys.exit(0 if rc in ("OK", "ALREADY_VERIFIED") else 3)
    except Exception:
        import traceback
        crash = LOG_DIR / f"guard_crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        crash.write_text(traceback.format_exc(), encoding="utf-8")
        print(f"❌ 守卫未捕获异常，详情: {crash}")
        sys.exit(1)

# -*- coding: utf-8 -*-
"""注册 Windows 计划任务：静默运行签到脚本（pythonw，无窗口）

两个任务
--------
1. **主签到** ``WorkBuddyDailyCheckin`` —— 每天 07:00 运行 checkin_cdp.py
2. **签到守卫** ``WorkBuddyCheckinGuard`` —— 一天多个检查点运行 checkin_guard.py
   （核验当天积分是否到账，未到账则自动排查/修复/重试；已到账毫秒级退出）

用法
----
::

    python register_windows_task.py                          # 主签到，每天 07:00
    python register_windows_task.py --at 08:30               # 自定义时间
    python register_windows_task.py --guard                  # 注册守卫（默认 8 个检查点）
    python register_windows_task.py --guard --times 08:00,12:00,18:00,22:00
    python register_windows_task.py --delete-all             # 删除两个任务

依赖
----
- 优先使用 pywin32（功能完整：支持电池供电运行、错过补跑、一天多检查点）
- 未安装 pywin32 时自动退回 schtasks.exe（仅基础单次每日触发）

注意
----
- 通过 pythonw.exe 运行（无控制台窗口），日志写入脚本同目录 logs/
- 目标应用（WorkBuddy）必须以 ``--remote-debugging-port=9222`` 启动，否则任务执行时连不上 CDP
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_NAME = "WorkBuddyDailyCheckin"
GUARD_NAME = "WorkBuddyCheckinGuard"

# 守卫检查点：07:00 主签到之后，每 2 小时核验一次直到 22:00。
# 每次核验是幂等的——当天已确认到账会在毫秒级退出，所以多跑几次几乎没有成本。
GUARD_TIMES = ["08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00"]


def find_pythonw():
    """优先用当前解释器同目录的 pythonw.exe，其次找 PATH 上的 pythonw"""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    if cand.exists():
        return str(cand)
    found = shutil.which("pythonw")
    if found:
        return found
    raise SystemExit("找不到 pythonw.exe，请用完整 Python 安装目录下的解释器运行本脚本")


def register_via_com(task_name, at, pythonw, script, extra_args=""):
    """pywin32 COM 注册：支持电池供电 + StartWhenAvailable（错过补跑）"""
    import win32com.client
    sched = win32com.client.Dispatch("Schedule.Service")
    sched.Connect()
    folder = sched.GetFolder("\\")

    td = sched.NewTask(0)
    td.RegistrationInfo.Description = "Daily check-in via CDP (workbuddy-daily-checkin)"

    trig = td.Triggers.Create(2)  # TASK_TRIGGER_DAILY
    from datetime import date
    trig.StartBoundary = f"{date.today().isoformat()}T{at}:00"
    trig.DaysInterval = 1
    trig.Enabled = True

    action = td.Actions.Create(0)  # TASK_ACTION_EXEC
    action.Path = pythonw
    action.Arguments = f'"{script}" {extra_args}'.strip()

    s = td.Settings
    s.DisallowStartIfOnBatteries = False   # 电池供电也运行
    s.StopIfGoingOnBatteries = False
    s.StartWhenAvailable = True            # 错过（睡眠/关机）后开机补跑
    s.WakeToRun = False
    s.ExecutionTimeLimit = "PT4H"
    s.MultipleInstances = 2                # TASK_INSTANCES_IGNORE_NEW（0=Parallel, 2=IgnoreNew）

    folder.RegisterTaskDefinition(task_name, td, 6, None, None, 3)  # 6=CREATE_OR_UPDATE
    print(f"✅ 任务已注册（COM）：{task_name}，每天 {at}，pythonw 静默运行")


def register_via_schtasks(task_name, at, pythonw, script, extra_args=""):
    """schtasks 兜底：仅基础每日触发"""
    cmd = ["schtasks", "/Create", "/TN", task_name, "/TR",
           f'"{pythonw}" "{script}" {extra_args}'.strip(), "/SC", "DAILY", "/ST", at, "/F"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        print(f"✅ 任务已注册（schtasks）：{task_name}，每天 {at}")
        print("   提示：schtasks 不支持「错过补跑/电池供电」，如需完整功能请 pip install pywin32")
    else:
        print("❌ 注册失败：", r.stderr or r.stdout)
        sys.exit(1)


def register_guard_via_com(task_name, times, pythonw, script, extra_args=""):
    """注册守卫任务：一天内多个检查点（多条每日触发器）"""
    import win32com.client
    from datetime import date
    sched = win32com.client.Dispatch("Schedule.Service")
    sched.Connect()
    folder = sched.GetFolder("\\")

    td = sched.NewTask(0)
    td.RegistrationInfo.Description = (
        "Checkin guard: verify daily points are credited, auto-repair if not "
        "(workbuddy-daily-checkin)")

    today = date.today().isoformat()
    for t in times:
        trig = td.Triggers.Create(2)  # TASK_TRIGGER_DAILY
        trig.StartBoundary = f"{today}T{t}:00"
        trig.DaysInterval = 1
        trig.Enabled = True

    action = td.Actions.Create(0)
    action.Path = pythonw
    action.Arguments = f'"{script}" {extra_args}'.strip()

    s = td.Settings
    s.DisallowStartIfOnBatteries = False
    s.StopIfGoingOnBatteries = False
    s.StartWhenAvailable = True
    s.WakeToRun = False
    s.ExecutionTimeLimit = "PT1H"
    s.MultipleInstances = 2        # IgnoreNew：防止检查点重叠互相抢 CDP 页面

    folder.RegisterTaskDefinition(task_name, td, 6, None, None, 3)
    print(f"✅ 守卫任务已注册（COM）：{task_name}")
    print(f"   检查点: {', '.join(times)}（共 {len(times)} 次/天），pythonw 静默运行")


def register_guard(task_name=GUARD_NAME, times=None, script=None, pythonw=None,
                   extra_args=""):
    """注册签到守卫任务（可被 checkin_cdp.py --setup 调用）

    times 默认 GUARD_TIMES；script 默认同目录 checkin_guard.py。
    """
    times = times or GUARD_TIMES
    pythonw = pythonw or find_pythonw()
    script = script or str(Path(__file__).parent / "checkin_guard.py")
    if not Path(script).exists():
        raise SystemExit(f"找不到脚本：{script}")

    print(f"pythonw: {pythonw}")
    print(f"script : {script}")
    if extra_args:
        print(f"args   : {extra_args}")

    try:
        register_guard_via_com(task_name, times, pythonw, script, extra_args)
    except ImportError:
        # schtasks 不支持一天多触发，退化为单次（仅提示）
        print("⚠️ 未安装 pywin32，schtasks 无法配置「一天多检查点」；"
              "请 pip install pywin32 后重新注册")
        register_via_schtasks(task_name, times[0], pythonw, script, extra_args)
    except Exception as e:
        print(f"⚠️ COM 注册失败（{e}）")
        sys.exit(1)
    return True


def delete_task(task_name):
    try:
        import win32com.client
        sched = win32com.client.Dispatch("Schedule.Service")
        sched.Connect()
        sched.GetFolder("\\").DeleteTask(task_name, 0)
        print(f"✅ 已删除任务：{task_name}")
        return
    except ImportError:
        pass
    except Exception as e:
        # pywin32 找不到任务时继续走 schtasks
        if "file not found" not in str(e).lower():
            pass
    r = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"✅ 已删除任务：{task_name}")
    else:
        print("❌ 删除失败：", r.stderr or r.stdout)


def register(task_name=DEFAULT_NAME, at="07:00", script=None, pythonw=None, extra_args=""):
    """注册每日自动签到任务（可被 checkin_cdp.py --setup 调用）

    script 默认取本文件同目录的 checkin_cdp.py；pythonw 默认自动定位。
    extra_args 为附加到脚本后的命令行参数（如 --no-auto-launch）。
    """
    pythonw = pythonw or find_pythonw()
    script = script or str(Path(__file__).parent / "checkin_cdp.py")
    if not Path(script).exists():
        raise SystemExit(f"找不到脚本：{script}")

    print(f"pythonw: {pythonw}")
    print(f"script : {script}")
    if extra_args:
        print(f"args   : {extra_args}")

    try:
        register_via_com(task_name, at, pythonw, script, extra_args)
    except ImportError:
        register_via_schtasks(task_name, at, pythonw, script, extra_args)
    except Exception as e:
        print(f"⚠️ COM 注册失败（{e}），尝试 schtasks 兜底...")
        register_via_schtasks(task_name, at, pythonw, script, extra_args)
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="注册 WorkBuddy 签到相关计划任务")
    ap.add_argument("--task-name", default=None,
                    help=f"任务名（主签到默认 {DEFAULT_NAME}，守卫默认 {GUARD_NAME}）")
    ap.add_argument("--at", default="07:00", help="主签到每天运行时间 HH:MM")
    ap.add_argument("--script", default=None,
                    help="脚本路径（默认按任务类型取同目录脚本）")
    ap.add_argument("--extra-args", default="",
                    help="附加到脚本后的命令行参数（如 --no-auto-launch）")
    ap.add_argument("--guard", action="store_true",
                    help="注册/更新「签到守卫」任务（一天多检查点核验+自愈）")
    ap.add_argument("--times", default=None,
                    help=f"守卫检查点，逗号分隔 HH:MM（默认 {','.join(GUARD_TIMES)}）")
    ap.add_argument("--delete", action="store_true", help="删除任务")
    ap.add_argument("--delete-all", action="store_true",
                    help="删除主签到与守卫两个任务")
    args = ap.parse_args()

    if args.delete_all:
        delete_task(DEFAULT_NAME)
        delete_task(GUARD_NAME)
        sys.exit(0)

    if args.delete:
        delete_task(args.task_name or DEFAULT_NAME)
        sys.exit(0)

    if args.guard:
        times = [t.strip() for t in args.times.split(",")] if args.times else None
        register_guard(task_name=args.task_name or GUARD_NAME, times=times,
                       script=args.script, extra_args=args.extra_args)
    else:
        register(task_name=args.task_name or DEFAULT_NAME, at=args.at,
                 script=args.script, extra_args=args.extra_args)

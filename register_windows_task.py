# -*- coding: utf-8 -*-
"""注册 Windows 计划任务：每天定时静默运行 checkin_cdp.py（无窗口）

用法
----
::

    python register_windows_task.py                          # 默认：每天 07:00
    python register_windows_task.py --at 08:30               # 自定义时间
    python register_windows_task.py --task-name MyCheckin    # 自定义任务名
    python register_windows_task.py --delete                 # 删除已注册任务

依赖
----
- 优先使用 pywin32（功能完整：支持电池供电运行、错过触发后补跑）
- 未安装 pywin32 时自动退回 schtasks.exe（仅基础每日触发）

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
    ap = argparse.ArgumentParser(description="注册 WorkBuddy 每日自动签到计划任务")
    ap.add_argument("--task-name", default=DEFAULT_NAME)
    ap.add_argument("--at", default="07:00", help="每天运行时间 HH:MM")
    ap.add_argument("--script", default=None,
                    help="checkin_cdp.py 路径（默认本文件同目录）")
    ap.add_argument("--extra-args", default="",
                    help="附加到脚本后的命令行参数（如 --no-auto-launch）")
    ap.add_argument("--delete", action="store_true", help="删除已注册任务")
    args = ap.parse_args()

    if args.delete:
        delete_task(args.task_name)
        sys.exit(0)

    register(task_name=args.task_name, at=args.at, script=args.script,
             extra_args=args.extra_args)

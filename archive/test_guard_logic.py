# -*- coding: utf-8 -*-
"""验证 checkin_guard 的判定矩阵与告警文件写入（不打扰用户：通知打桩）"""
import importlib.util
import subprocess
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("g", HERE.parent / "checkin_guard.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

OK = "\u2705"
NG = "\u274c"
fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {OK if ok else NG} {name}: {got!r}" + ("" if ok else f"  期望 {want!r}"))
    if not ok:
        fails.append(name)


def panel(btn="今日已领", dis=True, pts=1200, days=12):
    return {"open": True, "btnText": btn, "btnDisabled": dis,
            "total_points": pts, "total_days": days, "reason": None}


BASE = {"date": "2026-09-13", "total_points": 1100, "total_days": 11}

print("== 判定矩阵 ==")
# 双证据 → high
v, c, ev, fl = g.judge(panel(), BASE)
check("双证据(已领+积分+100)", (v, c), ("verified", "high"))
# 只有按钮证据（计数未变）→ medium
v, c, ev, fl = g.judge(panel(pts=1100), BASE)
check("仅按钮证据(计数未变)", (v, c), ("verified", "medium"))
# 只有计数证据（按钮未刷新）→ medium
v, c, ev, fl = g.judge(panel(btn="立即领取", dis=False, pts=1200), BASE)
check("仅计数证据(按钮待刷新)", (v, c), ("verified", "medium"))
# 无证据 → unverified
v, c, ev, fl = g.judge(panel(btn="立即领取", dis=False, pts=1100), BASE)
check("无证据(未领取)", (v, c), ("unverified", "none"))
# 无基线 → 按钮够用，但只能 medium
v, c, ev, fl = g.judge(panel(), None)
check("无基线(首次运行)", (v, c), ("verified", "medium"))
# 期数重置（积分下降）→ 计数证据作废，只认按钮
v, c, ev, fl = g.judge(panel(pts=100, days=1), BASE)
check("期数重置(积分下降)", (v, c, fl), ("verified", "medium", ["period_reset"]))
# 期数重置 + 按钮未领 → 不能凭计数上升误判
v, c, ev, fl = g.judge(panel(btn="立即领取", dis=False, pts=100), BASE)
check("重置且未领取", (v, c), ("unverified", "none"))
# 面板没打开
v, c, ev, fl = g.judge({"open": False, "reason": "panel closed"}, BASE)
check("面板未展开", (v, c), ("unverified", "none"))
# 解析异常（读不到计数）
v, c, ev, fl = g.judge({"open": True, "btnText": "今日已领", "btnDisabled": True,
                        "total_points": None}, BASE)
check("双证据但计数不可解析", (v, c), ("verified", "medium"))

print("\n== fuel-stats 解析 ==")
for text, want in [("已领 12 天 累计领取 1200 分", (12, 1200)),
                   ("已领 0 天 累计领取 0 分", (0, 0)),
                   ("已领 3 天 累计领取 300 分 额外", (3, 300)),
                   ("没有数字", (None, None)),
                   ("", (None, None))]:
    check(f"parse({text!r})", g.cc.parse_fuel_stats(text), want)

print("\n== 基线选取（跳过今天/取最近含计数的一天）==")
st = {"days": {
    "2026-09-10": {"total_points": 900},
    "2026-09-12": {"total_points": 1100},
    "2026-09-14": {"total_points": 1200},   # 今天，不能当基线
}}
bl = g.baseline_for(st, "2026-09-14")
check("baseline 取 09-12", bl["date"], "2026-09-12")
check("baseline 值", bl["total_points"], 1100)
check("无历史 → None", g.baseline_for({"days": {"2026-09-14": {}}}, "2026-09-14"), None)

print("\n== 告警文件写入（通知打桩）==")
real_popen = subprocess.Popen
subprocess.Popen = lambda *a, **k: type("P", (), {"pid": 0})()
try:
    p = g.raise_alert("2026-09-14", "自检测试（非真实故障）",
                      ["检查次数: 第 1 次", "失败说明: 自检测试（非真实故障）"])
    content = Path(p).read_text(encoding="utf-8")
    check("告警文件已生成", Path(p).exists(), True)
    check("含原因", "自检测试" in content, True)
    check("含处理建议", "python checkin_cdp.py" in content, True)
    Path(p).unlink()
    print("  (自检告警文件已清理)")
finally:
    subprocess.Popen = real_popen

print("\n" + "=" * 50)
print(f"结论: {'全部通过 ' + OK if not fails else NG + ' 失败项: ' + str(fails)}")

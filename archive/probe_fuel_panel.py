# -*- coding: utf-8 -*-
"""一次性诊断：点开 Buddy加油站 面板，dump 与积分/签到相关的可见文本，
用于设计「当天积分是否到账」的可靠判定（不要靠猜 DOM）。"""
import importlib.util
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cc", HERE.parent / "checkin_cdp.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

cfg = cc.load_config()
port = cc.detect_debug_port(cfg)
print("port:", port)

targets = cc.get_work_targets(port)
main_ws = next((t["webSocketDebuggerUrl"] for t in targets
                if "WorkBuddy" in (t.get("title") or "")), None)
print("main target:", bool(main_ws))

# 收集页面可见的叶子文本（含 shadow DOM）
JS_DUMP = r"""
(() => {
  const out = [];
  function walk(root) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      if (el.childElementCount === 0) {
        const t = (el.textContent || '').trim();
        if (t) {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.height > 0) {
            out.push({ t: t, cls: el.className ? String(el.className).slice(0, 50) : '' });
          }
        }
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  }
  walk(document);
  return out;
})()
"""


def dump(tag):
    res = cc.cdp_evaluate(main_ws, JS_DUMP)
    if not isinstance(res, list):
        print(tag, "dump 失败:", res)
        return []
    kw = ("积分", "领取", "签到", "加油", "成长", "连续", "天")
    hits = [(r["t"], r["cls"]) for r in res
            if any(k in r["t"] for k in kw) or r["t"].isdigit()]
    print(f"--- {tag}: 共 {len(res)} 个可见叶子节点，命中 {len(hits)}")
    for t, c in hits[:60]:
        print(f"    [{c}] {t}")
    return hits


print("\n===== 面板打开前 =====")
dump("before")

print("\n===== 打开用户菜单 + 加油站面板 =====")
opened = cc.open_user_panel(main_ws, cfg.get("nickname") or "")
print("open_user_panel:", json.dumps(opened, ensure_ascii=False))
fuel = "no-entry"
for i in range(1, 9):
    time.sleep(1.2)
    fuel = cc.click_fuel_menu_entry(main_ws)
    if fuel in ("clicked", "clicked(native)"):
        break
print(f"click_fuel_menu_entry: {fuel} (尝试 {i} 次)")
time.sleep(4)

print("\n===== 面板打开后 =====")
hits = dump("after")

# 额外：抓 fuel 容器的 innerText，便于看整体结构
JS_PANEL = r"""
(() => {
  const roots = [document];
  let best = null;
  while (roots.length) {
    const root = roots.pop();
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('[class*="fuel"]')] : []; } catch (e) {}
    for (const el of els) {
      const r = el.getBoundingClientRect();
      if (r.width > 100 && r.height > 100) {
        const txt = (el.innerText || '').trim();
        if (txt && (!best || txt.length > best.length)) best = txt;
      }
    }
    try { for (const el of root.querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot); } catch (e) {}
  }
  return best;
})()
"""
panel = cc.cdp_evaluate(main_ws, JS_PANEL)
print("\n===== fuel 容器 innerText =====")
print((panel or "")[:2500])

# 关闭面板（再点一次头像卡片）
print("\n===== 关闭面板 =====")
print(cc.open_user_panel(main_ws, cfg.get("nickname") or ""))

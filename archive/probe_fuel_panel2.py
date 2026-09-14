# -*- coding: utf-8 -*-
"""一次性诊断 2：转储加油站面板内所有 .fuel-* 元素（标签/类名/自身文本/可见性），
以及签到按钮的文案与 disabled 状态，用于设计精确的「今日已领」判定。"""
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
targets = cc.get_work_targets(port)
main_ws = next(t["webSocketDebuggerUrl"] for t in targets
               if "WorkBuddy" in (t.get("title") or ""))

opened = cc.open_user_panel(main_ws, cfg.get("nickname") or "")
fuel = "no-entry"
for i in range(1, 9):
    time.sleep(1.2)
    fuel = cc.click_fuel_menu_entry(main_ws)
    if fuel in ("clicked", "clicked(native)"):
        break
print("opened:", opened.get("opened"), "| fuel:", fuel)
time.sleep(4)

JS = r"""
(() => {
  const roots = [document], fuelEls = [];
  while (roots.length) {
    const root = roots.pop();
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      const cls = String(el.className || '');
      if (/fuel/.test(cls)) {
        const r = el.getBoundingClientRect();
        // 只取元素自身的直接文本（不含子节点）
        let own = '';
        for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
        fuelEls.push({
          tag: (el.tagName || '').toLowerCase(),
          cls: cls.slice(0, 70),
          own: own.trim().slice(0, 40),
          all: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80),
          disabled: el.disabled === true,
          vis: r.width > 0 && r.height > 0,
          x: Math.round(r.x), y: Math.round(r.y)
        });
      }
      if (el.shadowRoot) roots.push(el.shadowRoot);
    }
  }
  // 全页面找文字恰好是「今日已领/已领/立即领取」的叶子节点，看它挂在什么父级上
  const claimEls = [];
  function walk(root) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      if (el.childElementCount === 0) {
        const t = (el.textContent || '').trim();
        if (/^(今日已领|已领|立即领取|领取中)/.test(t)) {
          const p = el.parentElement;
          const pr = p ? p.getBoundingClientRect() : null;
          claimEls.push({
            text: t,
            tag: (el.tagName || '').toLowerCase(),
            cls: String(el.className || '').slice(0, 50),
            parentTag: p ? (p.tagName || '').toLowerCase() : '',
            parentCls: p ? String(p.className || '').slice(0, 70) : '',
            parentDisabled: p ? p.disabled === true : null,
            parentVis: pr ? (pr.width > 0 && pr.height > 0) : null
          });
        }
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  }
  walk(document);
  return { fuelEls, claimEls };
})()
"""

res = cc.cdp_evaluate(main_ws, JS)
print("\n===== .fuel-* 元素 =====")
for e in (res or {}).get("fuelEls", []):
    print(f"  <{e['tag']} class='{e['cls']}' disabled={e['disabled']} vis={e['vis']} @{e['x']},{e['y']}>")
    if e["own"]:
        print(f"      own={e['own']!r}")
    if e["all"]:
        print(f"      all={e['all']!r}")

print("\n===== 领取状态叶子节点及父级 =====")
for e in (res or {}).get("claimEls", []):
    print(f"  {e['text']!r} <{e['tag']} class='{e['cls']}'> "
          f"parent=<{e['parentTag']} class='{e['parentCls']}' disabled={e['parentDisabled']} vis={e['parentVis']}>")

print("\n===== 关闭面板 =====")
print(cc.open_user_panel(main_ws, cfg.get("nickname") or ""))

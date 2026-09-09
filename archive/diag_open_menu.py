# -*- coding: utf-8 -*-
"""诊断 3：点击左下角 user-menu-trigger 打开弹出菜单，dump 菜单内容（含 shadow DOM），
随后再点一次关闭。用于确认「Buddy加油站」条目在新版 UI 中的结构/class。"""
import json
import sys
import time
import requests
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 9222

JS_OPEN = r"""
(() => {
  const btn = document.querySelector('button.user-menu-trigger');
  if (!btn) return 'no-trigger';
  const r = btn.getBoundingClientRect();
  btn.click();
  return { clicked: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
})()
"""

JS_DUMP_MENU = r"""
(() => {
  const out = { roots: [], items: [] };
  function collect(root, name) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    const texts = [];
    for (const el of els) {
      const cls = String(el.className || '');
      const rect = el.getBoundingClientRect();
      // 弹出菜单/浮层特征：定位非 static、或 class 含 menu/dropdown/popover/panel，且可见
      let style = null;
      try { style = getComputedStyle(el); } catch (e) {}
      const pos = style ? style.position : '';
      const looksMenu = /menu|dropdown|popover|panel|overlay|popper/i.test(cls) && rect.width > 0 && rect.height > 0;
      if (looksMenu) {
        out.roots.push({ name, cls: cls.slice(0, 80), tag: el.tagName, pos,
                         rect: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)] });
      }
      if (el.childElementCount === 0 && (el.textContent||'').trim() && (el.textContent||'').trim().length < 30 && rect.width > 0 && rect.height > 0) {
        texts.push({ t: el.textContent.trim(), cls: cls.slice(0, 50), tag: el.tagName, pos, x: Math.round(rect.x), y: Math.round(rect.y) });
      }
      if (el.shadowRoot) collect(el.shadowRoot, name + '>shadow');
    }
    out.items.push(...texts);
  }
  collect(document, 'doc');
  // 高度疑似：文本含 加油站/签到/Buddy 的小于30字可见元素
  const fuelish = out.items.filter(i => /加油站|签到|积分|Buddy/.test(i.t));
  return { roots: out.roots.slice(0, 40), items: out.items.slice(0, 80), fuelish };
})()
"""

JS_CLOSE = r"""
(() => {
  const btn = document.querySelector('button.user-menu-trigger');
  if (btn) { btn.click(); return 're-clicked'; }
  return 'no-trigger';
})()
"""


def evaluate(ws_url, js):
    ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": js, "returnByValue": True}}))
        while True:
            data = json.loads(ws.recv())
            if data.get("id") == 1:
                res = data.get("result", {})
                if "exceptionDetails" in res:
                    return {"_exception": str(res["exceptionDetails"])[:600]}
                return res.get("result", {}).get("value")
    finally:
        ws.close()


def main():
    targets = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=3).json()
    t0 = next(x for x in targets if x.get("title") == "WorkBuddy")
    ws_url = t0["webSocketDebuggerUrl"]

    print("[1] 点击 user-menu-trigger 打开菜单 ...")
    print("   ", evaluate(ws_url, JS_OPEN))
    time.sleep(1.5)
    r = evaluate(ws_url, JS_DUMP_MENU)
    print("[2] 弹出层根元素:", len(r.get("roots", [])))
    for x in r.get("roots", [])[:30]:
        print(f"    <{x['tag']}> {x['name']} cls={x['cls']} pos={x['pos']} rect={x['rect']}")
    print("[3] 可见短文本(items):", len(r.get("items", [])))
    for x in r.get("items", [])[:60]:
        print(f"    '{x['t']}' <{x['tag']}> cls={x['cls']} pos={x['pos']} @({x['x']},{x['y']})")
    print("[4] 加油站/签到相关:", r.get("fuelish"))
    # 关闭菜单
    print("[5] 关闭菜单 ...")
    print("   ", evaluate(ws_url, JS_CLOSE))


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""只读诊断 2：dump 左下角 user-menu 内部结构 + 页面左下区域布局 + fuel 相关一切元素。"""
import json
import sys
import requests
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 9222

JS = r"""
(() => {
  const out = { menus: [], fuelAll: [], leftBottom: [], fuelEntryExact: null };
  function rectInfo(el) {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
             vis: r.width > 0 && r.height > 0 };
  }
  // 1) 所有 user-menu 类面板的完整文本与直接子项
  document.querySelectorAll('.user-menu,.userMenu,.user_menu,[class*="user-menu"],[class*="userMenu"]').forEach(m => {
    const kids = [];
    m.querySelectorAll('*').forEach(c => {
      if (c.childElementCount === 0 && (c.textContent||'').trim() && (c.textContent||'').trim().length < 40) {
        kids.push({ t: c.textContent.trim(), cls: String(c.className || '').slice(0,50), tag: c.tagName });
      }
    });
    out.menus.push({ cls: String(m.className).slice(0,80), rect: rectInfo(m), kids: kids.slice(0, 60) });
  });
  // 2) 全树（含 shadow）class/text 含 fuel/gas/加油站 的元素，可见或不可见都算
  const seen = new Set();
  function walk(root) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      const cls = String(el.className || '');
      const txt = (el.textContent || '');
      if (/fuel|gas|加油/i.test(cls) || txt.indexOf('加油站') >= 0 || txt.indexOf('Buddy加油站') >= 0) {
        if (!seen.has(el)) {
          seen.add(el);
          const ri = rectInfo(el);
          out.fuelAll.push({ cls: cls.slice(0,60), tag: el.tagName, t: txt.trim().slice(0, 40), rect: ri });
        }
      }
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  }
  walk(document);
  // 3) 左下区域（x<400, y>700）可见叶子元素
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.x < 400 && r.y > 700 && el.offsetParent !== null) {
      if (el.childElementCount === 0 || el.querySelectorAll('*').length < 30) {
        const t = (el.textContent||'').trim();
        if (t && t.length < 40) out.leftBottom.push({ t, tag: el.tagName, cls: String(el.className||'').slice(0,60), x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2) });
      }
    }
  });
  out.leftBottom = out.leftBottom.slice(0, 60);
  return out;
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
    r = evaluate(ws_url, JS)
    if not isinstance(r, dict):
        print("evaluate failed:", r); return
    if "_exception" in r:
        print("JS 异常:", r["_exception"]); return

    print(f"== user-menu 面板 {len(r.get('menus', []))} 个 ==")
    for m in r.get("menus", []):
        print(f"  {m['cls']} {m['rect']}")
        for k in m["kids"]:
            print(f"    - '{k['t']}' <{k['tag']}> cls={k['cls']}")

    print(f"\n== fuel/加油站 相关元素 {len(r.get('fuelAll', []))} 个 ==")
    for f in r.get("fuelAll", [])[:20]:
        print(f"  <{f['tag']}> cls={f['cls']} t='{f['t']}' rect={f['rect']}")

    print(f"\n== 左下区域可见元素 {len(r.get('leftBottom', []))} 个 ==")
    for x in r.get("leftBottom", []):
        print(f"  '{x['t']}' <{x['tag']}> cls={x['cls']} @({x['x']},{x['y']})")


if __name__ == "__main__":
    main()
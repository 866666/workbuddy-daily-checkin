# -*- coding: utf-8 -*-
"""只读诊断：枚举 9222 端口所有 target，遍历主文档 + shadow DOM，
dump 与签到相关（领取/签到/积分/加油/今日）的叶子文本及其可点击祖先，
以及 class 含 fuel 的元素、昵称「几米阳光」的出现情况。不刷新、不点击。"""
import json
import sys
import requests
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = 9222
KW = "领取|签到|积分|加油|今日已|Buddy"

JS_DUMP = r"""
(() => {
  const out = { texts: [], fuels: [], nick: false, panelLike: [] };
  const nick = "几米阳光";
  function leafInfo(el, rootName) {
    const t = (el.textContent || '').trim();
    if (!t) return null;
    // 找可点击祖先
    let up = el, depth = 0;
    while (up && depth < 8) {
      const tag = (up.tagName || '').toLowerCase();
      const role = up.getAttribute ? up.getAttribute('role') : null;
      if (tag === 'button' || tag === 'a' || role === 'button') break;
      up = up.parentElement; depth++;
    }
    const rect = el.getBoundingClientRect();
    return {
      text: t.slice(0, 60),
      tag: el.tagName,
      parent: up && up !== el ? `${(up.tagName||'').toLowerCase()}${up.className ? '.' + String(up.className).split(' ').join('.') : ''}` : '',
      visible: rect.width > 0 && rect.height > 0,
      x: Math.round(rect.x + rect.width / 2),
      y: Math.round(rect.y + rect.height / 2),
      in: rootName
    };
  }
  function walk(root, rootName) {
    let els = [];
    try { els = root.querySelectorAll ? [...root.querySelectorAll('*')] : []; } catch (e) {}
    for (const el of els) {
      const cls = el.className ? String(el.className) : '';
      if (cls && /fuel|gas|station|加油/i.test(cls)) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) out.fuels.push({ cls: cls.slice(0, 80), tag: el.tagName, t: (el.textContent||'').trim().slice(0, 50), x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2) });
      }
      if (el.childElementCount === 0 && el.textContent) {
        const t = el.textContent.trim();
        if (t && t.length <= 40 && /领取|签到|积分|加油|今日已|Buddy/.test(t)) {
          const info = leafInfo(el, rootName);
          if (info) out.texts.push(info);
        }
        if (t.indexOf(nick) >= 0) out.nick = true;
      }
      if (el.shadowRoot) walk(el.shadowRoot, rootName + '>shadow');
    }
  }
  walk(document, 'doc');
  // 顶部/侧边可见的弹出面板类元素
  document.querySelectorAll('.user-menu,.userMenu,.menu-panel,.ant-dropdown,.ant-popover').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) out.panelLike.push({ cls: String(el.className).slice(0,80), t: (el.textContent||'').trim().slice(0,60), x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2) });
  });
  out.htmlLen = document.documentElement ? document.documentElement.innerHTML.length : 0;
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
                    return {"_exception": str(res["exceptionDetails"])[:500]}
                return res.get("result", {}).get("value")
    finally:
        ws.close()


def main():
    targets = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=3).json()
    for i, t in enumerate(targets):
        ws_url = t.get("webSocketDebuggerUrl")
        print(f"\n{'='*70}\n[{i}] type={t.get('type')} title={t.get('title')}")
        print(f"    url={t.get('url', '')[:110]}")
        if not ws_url:
            print("    no ws, skip")
            continue
        r = evaluate(ws_url, JS_DUMP)
        if not isinstance(r, dict):
            print("    evaluate failed:", r)
            continue
        print(f"    htmlLen={r.get('htmlLen')}  nick出现在页面={r.get('nick')}")
        print(f"    [fuel 类元素 {len(r.get('fuels', []))} 个]")
        for f in r.get("fuels", [])[:15]:
            print(f"      {f['cls']} <{f['tag']}> text='{f['t']}' @({f['x']},{f['y']})")
        print(f"    [关键词文本 {len(r.get('texts', []))} 条]")
        for x in r.get("texts", [])[:30]:
            vis = 'V' if x['visible'] else 'H'
            print(f"      [{vis}] '{x['text']}' <{x['tag']}> parent={x['parent']} @({x['x']},{x['y']}) in {x['in']}")
        print(f"    [可见下拉/菜单面板 {len(r.get('panelLike', []))} 个]")
        for p in r.get("panelLike", [])[:10]:
            print(f"      {p['cls']} text='{p['t']}' @({p['x']},{p['y']})")


if __name__ == "__main__":
    main()
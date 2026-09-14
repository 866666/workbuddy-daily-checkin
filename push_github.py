# -*- coding: utf-8 -*-
"""push_github.py —— 用 GitHub REST API（Git Data API）把本地文件单 commit 推送到仓库

背景：本机 github.com 主站被墙（git push/SSH 均不通或被拒），api.github.com 畅通。
本工具经 ``urllib``（标准库）直连 api.github.com，并显式禁用代理
（等价于 requests 的 ``trust_env=False``），一次 update/create 多个文件为单个 commit。

为什么不用 requests：本项目曾因升级清理把 ``requests`` 包破坏成命名空间包，
``import requests`` 表面成功但 ``requests.get`` 不存在，导致静默失败。
推送属无人值守流程的一环，因此核心链路只用标准库。

用法：:

    python push_github.py --repo 866666/workbuddy-daily-checkin --branch main \\
        -m "commit message" file1 path2 dir3/...
    python push_github.py --token-env GITHUB_TOKEN ...   # 从环境变量读 token
                                                         # （默认读 ~/.workbuddy/credentials/github-pat.txt）

注意
----
- 远程文件请从本地完整内容覆盖；新增文件直接 PUT；无需提供 blob SHA。
- 目录参数会递归加入（例如传 ``archive``）；注意别把 logs/、config.json、state.json 传进来。
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAT_FALLBACK = Path.home() / ".workbuddy" / "credentials" / "github-pat.txt"
API = "https://api.github.com"

# 本机 git 默认全局代理可能指向被墙的 github.com；必须禁止走代理直连 api.github.com
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(method, url, token, payload=None):
    """调用 GitHub REST API，返回解析后的 JSON（无响应体返回 None）"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "workbuddy-daily-checkin-push",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code} {method} {url}\n{detail}") from None
    except Exception as e:
        raise RuntimeError(f"请求失败 {method} {url}: {type(e).__name__}: {e}") from None
    return json.loads(body) if body.strip() else None


def load_token(args):
    if args.token_env:
        tok = os.environ.get(args.token_env)
        if tok:
            return tok
    if PAT_FALLBACK.exists():
        return PAT_FALLBACK.read_text(encoding="utf-8").strip()
    raise SystemExit("找不到 token：用 --token-env 指定环境变量，或把 PAT 存入 "
                     "~/.workbuddy/credentials/github-pat.txt")


def main():
    ap = argparse.ArgumentParser(description="GitHub REST 单 commit 推送")
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--branch", default="main")
    ap.add_argument("-m", "--message", required=True, help="commit message")
    ap.add_argument("--token-env", default=None,
                    help="从环境变量读 token（默认读 ~/.workbuddy/credentials/github-pat.txt）")
    ap.add_argument("paths", nargs="+", help="要推送的本地文件/目录（目录递归）")
    args = ap.parse_args()

    token = load_token(args)
    owner, repo = args.repo.split("/")

    # 收集文件
    files = []
    seen = set()
    for p in args.paths:
        p = Path(p)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(Path.cwd()).as_posix()
                    if rel not in seen:
                        seen.add(rel)
                        files.append((rel, f.read_bytes()))
        elif p.is_file():
            rel = p.as_posix().lstrip("./")
            if rel not in seen:
                seen.add(rel)
                files.append((rel, p.read_bytes()))
    if not files:
        raise SystemExit("没有可推送的文件")

    # 1) 验证 token + 取当前分支 commit/tree
    me = api("GET", f"{API}/user", token)
    print(f"已认证: {me.get('login')}")
    ref = api("GET", f"{API}/repos/{owner}/{repo}/git/ref/heads/{args.branch}", token)
    base_sha = ref["object"]["sha"]
    base_commit = api("GET", f"{API}/repos/{owner}/{repo}/git/commits/{base_sha}", token)
    base_tree = base_commit["tree"]["sha"]
    print(f"远端当前: {args.branch} @ {base_sha[:10]} (tree {base_tree[:10]})")

    # 2) 逐个建 blob
    tree_items = []
    for rel, data in files:
        blob = api("POST", f"{API}/repos/{owner}/{repo}/git/blobs", token,
                   payload={"content": base64.b64encode(data).decode(), "encoding": "base64"})
        tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  blob {rel} -> {blob['sha'][:10]}")

    # 3) 新树（基于远端当前树，仅更新列出的路径）
    tree = api("POST", f"{API}/repos/{owner}/{repo}/git/trees", token,
               payload={"base_tree": base_tree, "tree": tree_items})
    print(f"新树: {tree['sha'][:10]}")

    # 4) 提交
    commit = api("POST", f"{API}/repos/{owner}/{repo}/git/commits", token,
                 payload={"message": args.message, "tree": tree["sha"], "parents": [base_sha]})
    print(f"提交: {commit['sha'][:10]}")

    # 5) 更新分支引用
    api("PATCH", f"{API}/repos/{owner}/{repo}/git/refs/heads/{args.branch}", token,
        payload={"sha": commit["sha"], "force": False})
    print(f"✅ 推送完成: {args.branch} -> {commit['sha'][:10]} ({len(files)} 个文件, 1 个 commit)")


if __name__ == "__main__":
    main()

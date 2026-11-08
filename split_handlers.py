# -*- coding: utf-8 -*-
"""P2-7：把 handler.py 的 do_GET/do_POST/do_DELETE 顶层路由分支按域拆到 server/handlers/*.py mixin。
AST 驱动：保留源码格式与注释，只转换"非嵌套 def 内的裸 return -> return True"。
用法：cd liuying_repo && python3 split_handlers.py（幂等：重跑会基于已拆分的文件再拆，勿重跑）"""
import ast
import os
import re

SRC = "server/handler.py"
OUT_DIR = "server/handlers"

# 域 -> 模块/类/方法名
DOMAINS = {
    "front":   ("front",   "FrontMixin",   "_h_get_front"),
    "social":  ("social",  "SocialMixin",  None),
    "collect": ("collect", "CollectMixin", None),
    "admin":   ("admin",   "AdminMixin",   None),
    "content": ("content", "ContentMixin", None),
    "auth":    ("auth",    "AuthMixin",    None),
}
DOMAIN_METHOD = {
    ("GET", "front"): "_h_get_front", ("GET", "social"): "_h_get_social",
    ("GET", "collect"): "_h_get_collect", ("GET", "admin"): "_h_get_admin",
    ("GET", "content"): "_h_get_content",
    ("POST", "auth"): "_h_post_auth", ("POST", "admin"): "_h_post_admin",
    ("POST", "collect"): "_h_post_collect", ("POST", "social"): "_h_post_social",
    ("POST", "content"): "_h_post_content",
    ("DELETE", "admin"): "_h_delete_admin", ("DELETE", "social"): "_h_delete_social",
}

HEADER = """# -*- coding: utf-8 -*-
\"\"\"P2-7：{domain} 域路由（mixin，由 server/handler.py 的 H 类继承）。
拆分自 handler.py 的 do_{method} 顶层分支，逻辑与路由条件保持原样，
仅把分支内"裸 return"改为 return True（表示已处理，返回给分发调用链）。\"\"\"
{imports}
from server.config import *
from server.database import db
from server.common import *
from server.ws import *
from server._priv import *
from server.recommend import *
from server.qishui import *
from server.media import *
from server.ai import *


class {cls}:
"""


def top_imports(src):
    """提取源码顶层全部 import 语句原文（含 handler.py 原有标准库/第三方 import）。"""
    tree = ast.parse(src)
    lines = []
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            seg = ast.get_source_segment(src, n)
            if seg:
                lines.append(seg)
    return "\n".join(lines)


def domain_for(method, ifnode):
    test = ast.unparse(ifnode.test)
    if "admin" in test or "admin.html" in test:
        return "admin"
    if "collect" in test or "collect.html" in test:
        return "collect"
    if method == "GET":
        if any(k in test for k in ("my-stats", "/api/user/", "my-tracks", "my-likes", "my-comments")):
            return "social"
    else:
        if any(k in test for k in ("register", "login", "change-password", "profile/update",
                                   "push-token", "device")):
            return "auth"
        if any(k in test for k in ("follow", "history", "playlists", "notifications",
                                   "official-messages", "play/", "like/", "share",
                                   "behavior", "ai-comment", "upload-comment-image")):
            return "social"
        if "comment" in test and ("comments/" in test and "like" in test):
            return "social"
        if "upload" in test and "comment" not in test:
            return "content"
        if "comment" in test or "comments/" in test:
            return "social"
    if method == "GET" and any(k in test for k in (
            "tracks", "track/", "cover", "lyric", "avatar", "background",
            "comment-image", "banner", "splash", "package", "comments/",
            "stream", "share-url", "/s/", "weather", "recommend", "search",
            "likes", "official-messages", "notifications")):
        return "content"
    if method == "GET":
        return "front"
    raise ValueError("POST/DELETE 分支未归类: %s" % test)


def walk_no_nested(node):
    """遍历节点子树，跳过嵌套 FunctionDef/ClassDef（其内部 return 不转换）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield child
        yield from walk_no_nested(child)


def top_units(fn):
    """方法体（try 块内）的顶层语句单元：If 节点 + 非 If 语句段（尾段跟随前一个 If 归属）。
    返回 [(stmt, is_if)]，按源码顺序。"""
    body = fn.body
    if body and isinstance(body[0], ast.Try):
        body = body[0].body
    units = []
    for stmt in body:
        if isinstance(stmt, ast.If):
            units.append((stmt, True))
        else:
            units.append((stmt, False))
    return units


def build_method(if_nodes, method_name, prelude=""):
    """把一组同域 if 节点逐个提取源码（按节点边界，杜绝中间其他域分支污染），
    拼接为方法体：节点首行原缩进 12 -> 方法体 8（dedent 4），
    只转换"非嵌套 def 内"的裸 return -> return True。"""
    with open(SRC, encoding="utf-8") as f:
        src = f.read()
    body_lines = []
    prelude_lines = []
    for n in if_nodes:
        # 非 If 的顶层赋值（如 ADMIN_TOKEN = _admin_token()）会被方法内后续分支引用，
        # 而赋值本身又使该名字成为"局部变量"（赋值前引用会 UnboundLocalError），
        # 因此提取到方法开头执行。
        if not isinstance(n, ast.If) and isinstance(n, ast.Assign):
            seg = ast.get_source_segment(src, n)
            for i, ln in enumerate(seg.splitlines(keepends=True)):
                if not ln.strip():
                    continue
                if i == 0:
                    ln = " " * n.col_offset + ln
                if ln.startswith("    "):
                    ln = ln[4:]
                if not ln.endswith("\n"):
                    ln += "\n"
                prelude_lines.append(ln)
            continue
        seg = ast.get_source_segment(src, n)
        seg_lines = seg.splitlines(keepends=True)
        # 节点内需要转换的裸 return 行（相对节点首行偏移）
        ret_local = set()
        for sub in walk_no_nested(n):
            if isinstance(sub, ast.Return) and sub.value is None:
                ret_local.add(sub.lineno - n.lineno)
        out = []
        for i, ln in enumerate(seg_lines):
            if i == 0:
                # get_source_segment 从 col_offset 开始，首行不含原缩进，补回
                ln = " " * n.col_offset + ln
            if i in ret_local:
                s = ln.strip()
                if s == "return":
                    indent = ln[:len(ln) - len(ln.lstrip())]
                    out.append(indent + "return True\n")
                    continue
                if re.search(r";\s*return$", s):
                    idx = ln.rfind("return")
                    out.append(ln[:idx] + "return True\n")
                    continue
            if not ln.endswith("\n"):
                # get_source_segment 结束于节点 end，尾行可能不含换行
                ln += "\n"
            out.append(ln)
        # dedent 4（非空行），使 if 从 12 -> 8 空格（方法体层级）
        body_lines.extend(l[4:] if l.strip() else l for l in out)
    body = "".join(body_lines)
    return ("    def %s(self, u):\n" % method_name) + prelude + "".join(prelude_lines) + body + "        return False\n"


def main():
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    with open(SRC, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)

    # 找 H 类与三个方法
    cls = next(c for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "H")
    fns = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}

    groups = {}  # (method, domain) -> [units]
    for method in ("GET", "POST", "DELETE"):
        fn = fns.get("do_" + method)
        if not fn:
            continue
        units = top_units(fn)
        if_domains = {}
        for i, (stmt, is_if) in enumerate(units):
            if is_if:
                if_domains[i] = domain_for(method, stmt)
        prev_d = None
        for i, (stmt, is_if) in enumerate(units):
            if is_if:
                prev_d = if_domains[i]
                groups.setdefault((method, prev_d), []).append(stmt)
            elif prev_d or any(j in if_domains for j in range(i + 1, len(units))):
                # 非 If 语句段归属：
                #  - 定义类（Assign/FunctionDef，如 ADMIN_TOKEN=_admin_token()、def _admin_auth）
                #    服务于后续分支 → 归后一个 If 的域
                #  - 执行类（Try/Expr，如上传主体、404 兜底）跟随前一个 If 的域
                if isinstance(stmt, (ast.Assign, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                    nxt = next((if_domains[j] for j in range(i + 1, len(units)) if j in if_domains), None)
                    d = nxt or prev_d
                else:
                    d = prev_d
                if d:
                    groups.setdefault((method, d), []).append(stmt)

    # 每域生成模块文件（一个模块一个 mixin 类；同一域多方法合并）
    modules = {}  # domain -> {method: [if_nodes]}
    for (method, d), nodes in sorted(groups.items(), key=lambda kv: (DOMAINS[kv[0][1]][0], kv[0][0])):
        modules.setdefault(d, {})[method] = nodes

    imports = top_imports(open(SRC, encoding="utf-8").read())
    for d, methods in modules.items():
        mod, cls_name, _ = DOMAINS[d]
        mname = DOMAIN_METHOD[(next(iter(methods)), d)]
        parts = [HEADER.format(domain=d, cls=cls_name, method=next(iter(methods)), imports=imports)]
        for method, nodes in sorted(methods.items()):
            mname = DOMAIN_METHOD[(method, d)]
            # POST/DELETE 分支依赖 do_* 头部的 length（Content-Length），补回方法首行
            prelude = "        length = int(self.headers.get(\"Content-Length\", 0))\n" if method in ("POST", "DELETE") else ""
            parts.append("\n" + build_method(nodes, mname, prelude))
        with open(os.path.join(OUT_DIR, mod + ".py"), "w", encoding="utf-8") as f:
            f.write("".join(parts))
        print("生成 %s.py (%s)" % (mod, ", ".join("%s:%d" % (m, len(ns)) for m, ns in methods.items())))

    # 打印 do_GET/do_POST/do_DELETE 的调用链（用于替换 handler.py 主体）
    print("\n===== 调用链 =====")
    for method in ("GET", "POST", "DELETE"):
        fn = fns.get("do_" + method)
        if not fn:
            continue
        print(method, "调用:", [DOMAIN_METHOD[(method, d)] for (m, d) in sorted(groups) if m == method])
    print("===== 未归类兜底检查 =====")
    total = sum(len(v) for v in groups.values())
    print("共归类分支:", total)


if __name__ == "__main__":
    main()

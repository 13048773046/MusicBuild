# -*- coding: utf-8 -*-
"""P2-7 第二步：把 handler.py 的 do_GET/do_POST/do_DELETE 主体替换为 mixin 调用链，
H 类继承 6 个域 mixin，并补 import。用法：python3 rewrite_handler.py"""
import ast

SRC = "server/handler.py"

GET_BODY = """    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            # WebSocket 实时通知推送：GET /api/ws?token=xxx（需 Upgrade 头）
            if u.path == "/api/ws":
                _ws_handshake(self, u)
                return

            if u.path in ("/", "/api/health"):
                self._json(200, {"ok": True, "service": "music",
                                 "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                return

            # 其余路由按域分发（P2-7 拆分：server/handlers/*.py mixin）
            if self._h_get_front(u):
                return
            if self._h_get_social(u):
                return
            if self._h_get_collect(u):
                return
            if self._h_get_admin(u):
                return
            if self._h_get_content(u):
                return

            self._err(404, "unknown")
        except (socket.timeout, TimeoutError):
            self._err(408, "request timeout")
        except Exception as e:
            self._internal_error(e)
"""

POST_BODY = """    def do_POST(self):
        try:
            u = urllib.parse.urlparse(self.path)

            # 路由按域分发（P2-7 拆分：server/handlers/*.py mixin）
            # 顺序说明：content（歌曲上传，含"非 upload 即 404"兜底）必须最后，
            # 否则会把 social 的点赞/评论/分享/播放等请求误吞为 404。
            if self._h_post_auth(u):
                return
            if self._h_post_admin(u):
                return
            if self._h_post_collect(u):
                return
            if self._h_post_social(u):
                return
            if self._h_post_content(u):
                return

            self._err(404, "unknown")
        except (socket.timeout, TimeoutError):
            self._err(408, "request timeout")
        except Exception as e:
            self._internal_error(e)
"""

DELETE_BODY = """    def do_DELETE(self):
        try:
            u = urllib.parse.urlparse(self.path)

            # 路由按域分发（P2-7 拆分：server/handlers/*.py mixin）
            if self._h_delete_admin(u):
                return
            if self._h_delete_social(u):
                return

            self._err(404, "unknown")
        except (socket.timeout, TimeoutError):
            self._err(408, "request timeout")
        except Exception as e:
            self._internal_error(e)
"""

MIXIN_IMPORTS = """from server.handlers.front import FrontMixin
from server.handlers.social import SocialMixin
from server.handlers.collect import CollectMixin
from server.handlers.admin import AdminMixin
from server.handlers.content import ContentMixin
from server.handlers.auth import AuthMixin
"""


def main():
    with open(SRC, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    cls = next(c for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "H")
    fns = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    # 替换三个方法主体（按行号，从后往前避免行号漂移）
    replacements = []
    for method, body in (("do_GET", GET_BODY), ("do_POST", POST_BODY), ("do_DELETE", DELETE_BODY)):
        fn = fns.get(method)
        if fn:
            replacements.append((fn.lineno - 1, fn.end_lineno, body))
    for start, end, body in sorted(replacements, reverse=True):
        lines[start:end] = [body]
    # H 类继承 mixin
    for i, ln in enumerate(lines):
        if ln.startswith("class H("):
            lines[i] = ln.replace("class H(BaseHTTPRequestHandler):",
                                  "class H(BaseHTTPRequestHandler, FrontMixin, SocialMixin, "
                                  "CollectMixin, AdminMixin, ContentMixin, AuthMixin):")
            break
    # 加 import（插到 __all__ 行后）
    for i, ln in enumerate(lines):
        if ln.strip() == "__all__ = ['H']":
            lines.insert(i + 1, "\n" + MIXIN_IMPORTS)
            break
    with open(SRC, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    print("handler.py 重写完成")


if __name__ == "__main__":
    main()

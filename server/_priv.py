# -*- coding: utf-8 -*-
"""P2-7：handler 私有辅助（从 handler.py 拆出，供 handler.py 与 handlers/*.py 共用，
避免 handlers 反向 import handler 造成循环依赖）。"""
import os
import re
import json
import threading

# 下划线前缀名字不会被 `import *` 默认导入，显式声明供 handler.py 与各 mixin 通配导入
__all__ = ["_TEMPLATE_CACHE", "_version_lt", "_geo_city", "_geo_city_async", "_render_template"]

from server.config import TEMPLATE_DIR, log
from server.database import db

_TEMPLATE_CACHE = {}


def _version_lt(a, b):
    """版本号逐段数字比较：a < b 返回 True。
    支持 1 / 1.0 / 1.0.0 等格式，段数不同按缺位补 0 比较（如 9.10 > 9.9.9）；
    段内非数字前缀按 0 处理（如 1.0.0-beta → 1.0.0），相等返回 False（不算更旧）。"""
    def parts(v):
        out = []
        for seg in re.split(r"[.\-_]", v.strip()):
            m = re.match(r"\d+", seg)
            out.append(int(m.group(0)) if m else 0)
        return out
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    for i in range(n):
        x = pa[i] if i < len(pa) else 0
        y = pb[i] if i < len(pb) else 0
        if x != y:
            return x < y
    return False


def _geo_city(ip):
    """按 IP 估算用户所在城市（大致地址，如"苏州市"）。
    走 ip-api.com 免费接口（中文），2 秒超时；失败/内网/无法识别返回空串。
    仅用于用户主页展示大致归属地，不做精确定位。"""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return ""
    if ip.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
                      "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                      "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                      "172.30.", "172.31.")):
        return ""
    try:
        import urllib.request as _ur
        url = "http://ip-api.com/json/%s?lang=zh-CN&fields=status,regionName,city" % ip
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=2) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        if d.get("status") == "success":
            city = (d.get("city") or "").strip()
            region = (d.get("regionName") or "").strip()
            if city and city != region:
                return city
            return region or city
    except Exception:
        pass
    return ""


def _geo_city_async(uid, ip):
    """后台定位城市并落库（P1：用户主页首次请求不再同步阻塞 2s）。

    daemon 线程执行：请求线程先返回空 city，定位完成写 users.city，下次命中缓存。
    失败静默（保持空 city，不影响功能）。
    """
    def _job():
        try:
            city = _geo_city(ip)
            if city:
                c = db()
                try:
                    c.execute("UPDATE users SET city=? WHERE id=?", (city, uid))
                    c.commit()
                finally:
                    c.close()
        except Exception as e:
            log.warning("geo city async failed: %s", e)

    threading.Thread(target=_job, daemon=True).start()


def _render_template(name):
    """读取 server/templates/<name>（带简单缓存）。"""
    t = _TEMPLATE_CACHE.get(name)
    if t is None:
        with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as f:
            t = f.read()
        _TEMPLATE_CACHE[name] = t
    return t

# -*- coding: utf-8 -*-
"""极光推送模块：APP 被杀后离线通知。"""
import json
import base64
import urllib.request
import urllib.error
import logging

log = logging.getLogger(__name__)

JPUSH_APPKEY = "57186564f29524aa55194dc8"
JPUSH_SECRET = "bb4207b43f5da8927389b6eb"
JPUSH_URL = "https://api.jpush.cn/v3/push"


def _auth_header():
    cred = f"{JPUSH_APPKEY}:{JPUSH_SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(cred).decode("ascii")


def push_to_alias(alias, title, alert, extras=None):
    """向指定 alias（用户 ID）推送离线通知。"""
    if not alias:
        return
    payload = {
        "platform": "android",
        "audience": {"alias": [str(alias)]},
        "notification": {
            "android": {
                "alert": alert,
                "title": title,
                "builder_id": 1,
                "extras": extras or {}
            }
        },
        "options": {"apns_production": True}
    }
    try:
        req = urllib.request.Request(
            JPUSH_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": _auth_header()
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("jpush ok alias=%s resp=%s", alias, resp.read().decode()[:200])
    except Exception as e:
        log.info("jpush fail alias=%s err=%s", alias, e)


def save_token(user_id, token, platform="android"):
    """保存用户极光 registrationId 并设置 alias=user_id。"""
    if not token:
        return
    import time as _time
    from server.database import db as _db
    _c = _db()
    try:
        _c.execute("""INSERT OR REPLACE INTO push_tokens(user_id, token, platform, created)
                     VALUES(?,?,?,?)""", (user_id, token, platform, _time.time()))
        _c.commit()
    finally:
        _c.close()
    # 设置 alias：极光 alias 就是 user_id，推送时按 alias 定向
    try:
        url = "https://api.jpush.cn/v3/devices/" + token
        payload = {"alias": str(user_id)}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": _auth_header()
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("jpush alias set uid=%s resp=%s", user_id, resp.read().decode()[:200])
    except Exception as e:
        log.info("jpush alias fail uid=%s err=%s", user_id, e)

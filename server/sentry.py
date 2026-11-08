# -*- coding: utf-8 -*-
"""P2：Sentry 错误监控（可选集成）。

- SENTRY_DSN 为空 → 不启用，零依赖零开销（sentry_sdk 未安装也不报错）。
- SENTRY_DSN 非空 → 初始化 sentry_sdk：未捕获异常/日志错误自动上报到 Sentry 后台。
- 服务启动时由 main.py 调用 init_sentry()。
"""
from server.config import SENTRY_DSN, log

__all__ = ['init_sentry']


def init_sentry():
    if not SENTRY_DSN:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception as e:
        log.warning("sentry_sdk 未安装，跳过 Sentry 初始化: %s", e)
        return False
    try:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=0.1,          # 采样 10% 性能追踪，控制配额
            send_default_pii=False,
            max_breadcrumbs=50,
            environment="production",
            integrations=[LoggingIntegration(level=None, event_level=None)],
        )
        log.info("Sentry 已启用 (DSN: %s...)", SENTRY_DSN[:24])
        return True
    except Exception as e:
        log.warning("Sentry 初始化失败: %s", e)
        return False

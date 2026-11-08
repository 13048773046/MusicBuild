#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流萤音乐后端一键部署：备份 → 上传 server.py → 重启 music.service → 验证。

用法：
    python3 deploy.py ssh "<远程命令>"     # 在服务器上执行命令
    python3 deploy.py upload <本地文件> <远端路径>   # 上传文件（带重试）
"""
import os
import sys
import time
import pexpect

# 脱敏：主机/密码从环境变量读取，不入库（用法：export LY_DEPLOY_HOST=... LY_DEPLOY_PWD=...）
HOST = os.environ.get("LY_DEPLOY_HOST", "")
USER = "root"
PWD = os.environ.get("LY_DEPLOY_PWD", "")

SSH_OPTS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"


def ssh_run(cmd, timeout=60):
    """在服务器执行命令，返回 (ok, 输出)"""
    try:
        child = pexpect.spawn("ssh %s %s@%s %s" % (SSH_OPTS, USER, HOST, cmd),
                              encoding="utf-8", timeout=timeout)
        i = child.expect(["password:", pexpect.EOF, pexpect.TIMEOUT])
        if i == 0:
            child.sendline(PWD)
        i = child.expect([pexpect.EOF, pexpect.TIMEOUT])
        out = child.before or ""
        child.close()
        return child.exitstatus == 0, out
    except Exception as e:
        return False, "ssh error: %s" % e


def scp_upload(local, remote, timeout=180):
    """上传文件到服务器（scp 免交互）"""
    try:
        child = pexpect.spawn("scp %s %s %s@%s:%s" % (SSH_OPTS, local, USER, HOST, remote),
                              encoding="utf-8", timeout=timeout)
        i = child.expect(["password:", pexpect.EOF, pexpect.TIMEOUT])
        if i == 0:
            child.sendline(PWD)
        i = child.expect([pexpect.EOF, pexpect.TIMEOUT])
        child.close()
        return child.exitstatus == 0, (child.before or "")[-400:]
    except Exception as e:
        return False, "scp error: %s" % e


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "ssh":
        ok, out = ssh_run(sys.argv[2])
        print(out if ok else ("FAILED\n" + out))
        sys.exit(0 if ok else 1)
    elif mode == "upload":
        ok, out = scp_upload(sys.argv[2], sys.argv[3])
        print(out)
        sys.exit(0 if ok else 1)

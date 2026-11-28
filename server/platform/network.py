"""网络状态与切换：只当系统层（NetworkManager）的薄壳。

本产品不实现无线协议栈，只做两件事：把「网络名 + 凭据」交给 `nmcli`，再把结果读回来。
凭据由 NetworkManager 自己保管，这里不复制一份。
"""

import logging
import shutil
import socket
import subprocess

logger = logging.getLogger(__name__)

TIMEOUT = 25


def _run(args):
    """跑一条命令，返回 (ok, stdout, stderr)。命令不存在时给明确原因而不是抛异常。"""
    if shutil.which(args[0]) is None:
        return False, "", f"没有 {args[0]}"
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "", "命令超时"
    except OSError as exc:
        return False, "", str(exc)
    return done.returncode == 0, done.stdout.strip(), done.stderr.strip()


def current_ssid():
    ok, out, _ = _run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
    if not ok:
        return ""
    for line in out.splitlines():
        active, _, ssid = line.partition(":")
        if active == "yes":
            return ssid
    return ""


def address():
    ok, out, _ = _run(["ip", "-br", "-4", "addr"])
    if not ok:
        return ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] == "lo" or parts[1] != "UP":
            continue
        return parts[2].split("/")[0]
    return ""


def hostname():
    return socket.gethostname()


def mdns_url(port=8080):
    return f"http://{hostname()}.local:{port}"


def support():
    return shutil.which("nmcli") is not None


def current():
    ssid = current_ssid()
    return {
        "supported": support(),
        "connected": bool(ssid),
        "ssid": ssid,
        "address": address(),
        "hostname": hostname(),
        "mdnsUrl": mdns_url(),
    }


def set_hostname(name):
    """改本机名字。`sudo -n` 依赖部署时写下的白名单，不给整个服务 root。

    avahi 的主机名只在启动时从 gethostname() 读一次，SIGHUP(reload) 只重读静态 hosts 与服务目录，
    不重读名字——必须 restart，否则局域网里仍用旧名应答。
    """
    ok, _, err = _run(["sudo", "-n", "hostnamectl", "set-hostname", name])
    if not ok:
        raise ValueError(f"改名字失败：{err or '没有权限（重跑 install-service 写入白名单）'}")
    ok, _, err = _run(["sudo", "-n", "systemctl", "restart", "avahi-daemon"])
    if not ok:
        logger.warning("名字已改但 mDNS 没重启，局域网里要等重启才用新名字：%s", err)
    logger.info("本机名字改成 %s", name)


def connect(ssid, password):
    """把「网络名 + 凭据」交给系统层。凭据会出现在命令行里，设备本地单用户可接受。"""
    ok, _, err = _run(["nmcli", "dev", "wifi", "connect", ssid, "password", password])
    if not ok:
        detail = (err or "连接失败").splitlines()[-1]
        raise ValueError(f"连不上 {ssid}：{detail}")
    logger.info("已切换到网络 %s", ssid)

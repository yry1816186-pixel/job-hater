"""出站 URL 安全闸：拒绝指向私网/环回/链路本地目标的 http(s) 抓取。

背景（见 SECURITY.md）：岗位链接来自三方输入（粘贴正文自动抽取、微信卡片导入、
收件箱表格），判活 Tier2 与公众号深挖会主动 GET 这些 URL——恶意输入可诱导本机
服务访问内网（如云元数据 169.254.169.254）。本模块在发请求前解析目标 IP 并分类拒绝。

已知残余：解析与请求之间的 DNS 变更（TOCTOU）可绕过 IP 分类；对本机单用户工具
属可接受残余，文档如实声明。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def _ips_of_host(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]  # 字面 IP（含 IPv6）
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return out


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # is_private 已覆盖 RFC1918/环回/链路本地/TEST-NET 等；再显式补齐多播/保留/未指定
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and (ip.is_site_local or ip.is_unique_local))
    )


def guard_url(url: str) -> str | None:
    """出站前检查。返回 None=放行；返回字符串=拒绝理由（调用方不发请求）。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "URL 无法解析"
    if parts.scheme not in ("http", "https"):
        return f"仅允许 http/https（收到 {parts.scheme or '空'}）"
    host = parts.hostname or ""
    if not host:
        return "URL 无主机名"
    ips = _ips_of_host(host)
    if not ips:
        return "主机名无法解析"
    for ip in ips:
        if _is_blocked_ip(ip):
            return f"{host} 解析到保留地址段（{ip}），已拒绝抓取"
    return None

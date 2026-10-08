#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优选 IP 推送器（yx.ccwu.cc 专用改造版）

与上游 dnscf.py 的区别：
1. IP 来源改为 090227 按运营商实测接口（默认移动 /cmcc），不是 GitHub 美国机房测的 ipTop.html
2. A 记录不存在时自动创建（上游只能更新已存在的记录）
3. 失败自动顺延备用源：/cmcc -> /ct -> /cu -> ip.164746.xyz/ipTop.html
"""

import os
import re
import sys
import requests

CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")

HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json",
}

IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# 备用源按顺序尝试，第一个成功的生效
SOURCES = [
    ("cmcc", "https://cf.090227.xyz/cmcc?ips=3"),
    ("ct",   "https://cf.090227.xyz/ct?ips=3"),
    ("cu",   "https://cf.090227.xyz/cu?ips=3"),
    ("top",  "https://ip.164746.xyz/ipTop.html"),
]

TTL = 300


def fetch_best_ip():
    for tag, url in SOURCES:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                print(f"[{tag}] HTTP {r.status_code}, 换下一个源")
                continue
            for line in r.text.splitlines():
                ip = line.split("#")[0].strip()
                if IPV4_RE.match(ip):
                    print(f"[{tag}] 取到优选 IP: {ip}")
                    return ip
            print(f"[{tag}] 返回里没有合法 IP: {r.text[:100]!r}")
        except Exception as e:
            print(f"[{tag}] 请求异常: {e}")
    return None


def api(method, path, **kwargs):
    r = requests.request(
        method,
        f"https://api.cloudflare.com/client/v4{path}",
        headers=HEADERS,
        timeout=30,
        **kwargs,
    )
    body = r.json()
    if not body.get("success"):
        raise RuntimeError(f"CF API {method} {path} 失败: {body.get('errors')}")
    return body


def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("错误: 缺少 CF_API_TOKEN / CF_ZONE_ID / CF_DNS_NAME")
        sys.exit(1)

    ip = fetch_best_ip()
    if not ip:
        print("错误: 所有源都拿不到优选 IP")
        sys.exit(1)

    records = api(
        "GET",
        f"/zones/{CF_ZONE_ID}/dns_records",
        params={"name": CF_DNS_NAME, "type": "A"},
    )["result"]

    payload = {
        "type": "A",
        "name": CF_DNS_NAME,
        "content": ip,
        "ttl": TTL,
        "proxied": False,  # 必须灰云：橙云会被 CF 自己的 anycast 覆盖，优选 IP 就失效了
    }

    if not records:
        api("POST", f"/zones/{CF_ZONE_ID}/dns_records", json=payload)
        print(f"创建 {CF_DNS_NAME} -> {ip} (ttl {TTL}, 灰云)")
    else:
        rec = records[0]
        if rec["content"] == ip:
            print(f"{CF_DNS_NAME} -> {ip} 已是最新，跳过")
        else:
            api(
                "PUT",
                f"/zones/{CF_ZONE_ID}/dns_records/{rec['id']}",
                json=payload,
            )
            print(f"更新 {CF_DNS_NAME}: {rec['content']} -> {ip}")

    # 多余的同名 A 记录清掉，避免轮询随机化
    for rec in records[1:]:
        api("DELETE", f"/zones/{CF_ZONE_ID}/dns_records/{rec['id']}")
        print(f"删除多余记录 {rec['id']} ({rec['content']})")


if __name__ == "__main__":
    main()

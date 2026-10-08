#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优选 IP 推送器（yx.ccwu.cc 专用改造版）

与上游 dnscf.py 的区别：
1. IP 来源改为 090227 按运营商实测接口（默认移动 /cmcc），不是 GitHub 美国机房测的 ipTop.html
2. A 记录不存在时自动创建（上游只能更新已存在的记录）
3. 失败自动顺延备用源：/cmcc -> /ct -> /cu -> ip.164746.xyz/ipTop.html
4. 2026-10-09：一次推**多条** A 记录（源给几个推几个，最多 3 条）。
   单条 A 记录时该 IP 一烂整条入口就超时（10-08 实测 5/10 timeout）；
   多条后 DNS 轮询分散拨号，单 IP 死掉只影响 1/N 连接，TTL 300 内自然换。
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

# 备用源按顺序尝试，第一个成功的生效；?ips=3 让 090227 返回前 3 个
SOURCES = [
    ("cmcc", "https://cf.090227.xyz/cmcc?ips=3"),
    ("ct",   "https://cf.090227.xyz/ct?ips=3"),
    ("cu",   "https://cf.090227.xyz/cu?ips=3"),
    ("top",  "https://ip.164746.xyz/ipTop.html"),
]

MAX_RECORDS = 3
TTL = 300


def fetch_best_ips():
    for tag, url in SOURCES:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                print(f"[{tag}] HTTP {r.status_code}, 换下一个源")
                continue
            ips = []
            for line in r.text.splitlines():
                ip = line.split("#")[0].strip()
                if IPV4_RE.match(ip) and ip not in ips:
                    ips.append(ip)
            if ips:
                print(f"[{tag}] 取到优选 IP {len(ips)} 个: {', '.join(ips)}")
                return ips
            print(f"[{tag}] 返回里没有合法 IP: {r.text[:100]!r}")
        except Exception as e:
            print(f"[{tag}] 请求异常: {e}")
    return []


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

    ips_env = os.environ.get("INPUT_IPS", "").strip()
    if ips_env:
        ips = [x.strip() for x in ips_env.split(",") if x.strip()][:MAX_RECORDS]
        print(f"使用本地实测预选 IP: {', '.join(ips)}")
    else:
        ips = fetch_best_ips()
        if not ips:
            print("错误: 所有源都拿不到优选 IP")
            sys.exit(1)
        ips = ips[:MAX_RECORDS]

    records = api(
        "GET",
        f"/zones/{CF_ZONE_ID}/dns_records",
        params={"name": CF_DNS_NAME, "type": "A"},
    )["result"]

    def make_payload(ip):
        return {
            "type": "A",
            "name": CF_DNS_NAME,
            "content": ip,
            "ttl": TTL,
            "proxied": False,  # 必须灰云：橙云会被 CF 自己的 anycast 覆盖，优选 IP 就失效了
        }

    # 目标状态：DNS 里恰好是 ips 这几条；多余的旧记录先就地改写复用，残余删除
    stale = [rec for rec in records if rec["content"] not in ips]
    for ip in ips:
        if any(rec["content"] == ip for rec in records):
            print(f"{CF_DNS_NAME} -> {ip} 已存在，跳过")
        elif stale:
            rec = stale.pop(0)
            api("PUT", f"/zones/{CF_ZONE_ID}/dns_records/{rec['id']}", json=make_payload(ip))
            print(f"改写 {rec['content']} -> {ip}")
        else:
            api("POST", f"/zones/{CF_ZONE_ID}/dns_records", json=make_payload(ip))
            print(f"创建 {CF_DNS_NAME} -> {ip} (ttl {TTL}, 灰云)")

    for rec in stale:
        api("DELETE", f"/zones/{CF_ZONE_ID}/dns_records/{rec['id']}")
        print(f"删除多余记录 {rec['content']}")


if __name__ == "__main__":
    main()

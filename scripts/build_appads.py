#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iOS App 专用广告拦截清单生成器

从 AdGuard 官方过滤器拉取数据，生成 Surge DOMAIN-SET 格式的 App 广告清单。

用法:
    python3 build_appads.py            # 生成到 ../rules/appads.domainset.txt
    python3 build_appads.py --check    # 只检查，不写文件；有差异时 exit 1
    python3 build_appads.py --dry-run  # 生成到临时目录并打印统计

数据源分层（按优先级）:
  1. AdGuard Mobile Ads filter (filters/11.txt)
     移动端第三方广告网络，全部收录，是本清单的基础。
  2. AdGuard Tracking Protection (filters/3.txt)
     只取与 App 相关的子集：隐私/归因 SDK + 常见广告交易平台。
     不整体引入（该文件有 10 万条，绝大多数与 App 无关）。
  3. AdGuard Chinese filter (filters/224.txt)
     国内广告地址。按优先级截取，补足到条目上限。

设计原则:
  1. 只接受 ||domain^ 与 ||domain^$third-party 两种形式，丢弃 URL/路径/通配规则。
  2. 丢弃带 $domain= 的站点限定规则：这类规则只在特定网站生效，
     当作全域拦截会误伤（例如 ||alicdn.com^$domain=... 会拦掉整个阿里 CDN）。
  3. 丢弃 IP 条目：DOMAIN-SET 只接受域名，混入 IP 会导致整份文件解析失败。
  4. 剔除 Apple 域名，以及已知会打断支付/登录的功能域名。
  5. 总条目数不超过 MAX_ENTRIES。

输出文件:
  rules/appads.domainset.txt    DOMAIN-SET，后缀匹配（含子域）
"""

import argparse
import ipaddress
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "rules"
TARGET = OUT / "appads.domainset.txt"

MAX_ENTRIES = 10000

SOURCES = {
    "mobile": "https://filters.adtidy.org/ios/filters/11.txt",
    "tracking": "https://filters.adtidy.org/ios/filters/3.txt",
    "chinese": "https://filters.adtidy.org/ios/filters/224.txt",
}

# 隐私 / 归因 / 统计 SDK：从 tracking filter 中挑出这些
SDK_RE = re.compile(
    r"(appsflyer|adjust\.|branch\.io|kochava|singular|tenjin|revenuecat|"
    r"applovin|unityads|ironsource|chartboost|adcolony|tapjoy|mintegral|pangle|"
    r"umeng|umengcloud|growingio|talkingdata|sensorsdata|bugly|jpush|jiguang|getui|"
    r"amplitude|mixpanel|braze|appboy|onesignal|leanplum|clevertap|moengage|"
    r"appmetrica|crashlytics|firebase|flurry|newrelic|smartlook|appcenter|"
    r"hockeyapp|datadog|sdk-api|\.sdk\.|attribution|skadnetwork|measurement|"
    r"pushwoosh|instabug|airship)",
    re.I,
)

# 常见广告交易平台：从 tracking filter 中挑出这些
COMMON_RE = re.compile(
    r"(doubleclick|googlesyndication|google-analytics|googletag|admob|"
    r"taboola|outbrain|adnxs|pubmatic|openx|rubiconproject|casalemedia|moatads|"
    r"bidswitch|demdex|everesttech|omtrdc|adsrvr|mathtag|agkn|bluekai|exelator|"
    r"crwdcntrl|turn\.com|tapad|adsafeprotected|moatpixel|triplelift|"
    r"indexexchange|sovrn|teads|spotxchange|2mdn|criteo|smartadserver|"
    r"yieldlab|1rx\.io)",
    re.I,
)

# 从 chinese filter 补足时，优先收这些国内平台标识
CN_PLATFORM_RE = re.compile(
    r"(gdtimg|dftoutiao|baidu|qq\.com|gdt|taobao|tmall|jd\.com|alipay|meituan|"
    r"dianping|xiaomi|huawei|oppo|vivo|samsung|bytedance|snssdk|toutiao|douyin|"
    r"weibo|sina|sohu|163\.com|netease|youku|iqiyi|bilibili|tencent|alibaba|"
    r"umeng|pangle)",
    re.I,
)

# 绝不可拦的整站（会打断系统功能或核心服务）
MUST_NOT_BLOCK = {
    "apple.com",
    "icloud.com",
    "mzstatic.com",
    "aaplimg.com",
    "alicdn.com",
    "baidu.com",
    "qq.com",
    "tencent.com",
    "taobao.com",
    "tmall.com",
    "jd.com",
    "alipay.com",
    "weixin.qq.com",
    "wechat.com",
    "unionpay.com",
    "meituan.com",
    "dianping.com",
    "amap.com",
    "autonavi.com",
    "ctrip.com",
    "xiaomi.com",
    "miui.com",
    "huawei.com",
    "hicloud.com",
}

# 虽为广告域但拦截后会影响支付/登录的个别条目
MUST_NOT_BLOCK_EXTRA = {
    "plugin.mobopay.baidu.com",  # 百度支付插件
}

# Apple 域名整域排除（含全部子域）。
# 与 MUST_NOT_BLOCK 的区别：那里是精确匹配，只排除整站本身，
# 因为像 pos.baidu.com 这种广告子域是应该拦的；
# 而 Apple 相关一律不动（用户明确要求），子域也要排除。
APPLE_SUFFIXES = (".apple.com", ".icloud.com", ".mzstatic.com")

def is_excluded(d):
    """判断域名是否必须排除。"""
    if d in MUST_NOT_BLOCK or d in MUST_NOT_BLOCK_EXTRA:
        return True
    if d in ("apple.com", "icloud.com", "mzstatic.com"):
        return True
    if d.endswith(APPLE_SUFFIXES):
        return True
    return False

DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
RULE_RE = re.compile(r"^\|\|([^/^$*]+)(\^|\$.*)?$")


def fetch(url, retries=3):
    """下载过滤器文本，失败重试。"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "surgeconf-build/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", errors="ignore")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            print(f"  重试 {attempt + 1}/{retries}: {e}", file=sys.stderr)
    raise RuntimeError(f"下载失败: {url}: {last}")


def parse(text):
    """从过滤器中解析出可用的域名集合。

    返回 (域名集合, 被丢弃的站点限定规则列表)。
    """
    good = set()
    scoped = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "!#;[":
            continue
        m = RULE_RE.match(s)
        if not m:
            continue
        # $domain= 规则只在特定站点生效，全域拦截会误伤
        if "domain=" in s:
            scoped.append(s)
            continue
        d = m.group(1).lower().strip(".")
        if not d or "*" in d:
            continue
        if not DOMAIN_RE.match(d):
            continue
        try:
            ipaddress.ip_address(d)
            continue  # DOMAIN-SET 不接受 IP
        except ValueError:
            pass
        if is_excluded(d):
            continue
        good.add(d)
    return good, scoped


def build():
    """拉取并合成清单，返回 (排序后的域名列表, 统计信息)。"""
    stats = {}
    texts = {}
    for name, url in SOURCES.items():
        print(f"拉取 {name}: {url}")
        texts[name] = fetch(url)
        print(f"  {len(texts[name].splitlines())} 行")

    print("解析 AdGuard Mobile Ads（基础层，全量）")
    mobile, scoped_m = parse(texts["mobile"])
    print(f"  {len(mobile)} 个域名，丢弃站点限定 {len(scoped_m)} 条")

    print("解析 Tracking Protection（只取 App 相关子集）")
    tracking, _ = parse(texts["tracking"])
    sdk = {d for d in tracking if SDK_RE.search(d)}
    common = {d for d in tracking if COMMON_RE.search(d)}
    print(f"  隐私/归因 SDK {len(sdk)} 个，常见广告平台 {len(common)} 个")

    print("解析 Chinese filter（国内广告地址）")
    chinese, scoped_c = parse(texts["chinese"])
    print(f"  {len(chinese)} 个域名，丢弃站点限定 {len(scoped_c)} 条")

    core = mobile | sdk | common
    print(f"核心层合计 {len(core)} 个域名")

    if len(core) > MAX_ENTRIES:
        # 核心层超上限时按优先级截断：mobile > sdk > common
        ordered = sorted(mobile) + sorted(sdk - mobile) + sorted(common - mobile - sdk)
        core = set(ordered[:MAX_ENTRIES])
        print(f"核心层超过上限 {MAX_ENTRIES}，已截断为 {len(core)}")

    remaining = MAX_ENTRIES - len(core)
    print(f"剩余名额 {remaining} 个，从 Chinese filter 补足")

    cn_only = chinese - core
    known = sorted(d for d in cn_only if CN_PLATFORM_RE.search(d))
    rest = sorted(d for d in cn_only if not CN_PLATFORM_RE.search(d))
    picked = known[:remaining]
    if len(picked) < remaining:
        picked += rest[: remaining - len(picked)]
    print(f"  国内平台标识 {len(known)} 个，其余 {len(rest)} 个，实际取用 {len(picked)} 个")

    final = sorted(core | set(picked))
    stats["mobile"] = len(mobile)
    stats["sdk"] = len(sdk)
    stats["common"] = len(common)
    stats["chinese"] = len(picked)
    stats["scoped_dropped"] = len(scoped_m) + len(scoped_c)
    return final, stats


def render(domains, stats, stamp):
    """生成文件内容，带来源说明与条目数。"""
    header = f"""# iOS App 专用广告拦截清单（DOMAIN-SET 格式，后缀匹配含子域）
# 本文件由 scripts/build_appads.py 自动生成，请勿手工编辑。
# 重新生成：python3 scripts/build_appads.py
# 生成时间：{stamp}
#
# 来源分层（按优先级）：
#   1. AdGuard Mobile Ads filter — filters/11.txt（全量收录，共 {stats['mobile']} 条）
#      移动端第三方广告网络，本清单的基础层
#   2. AdGuard Tracking Protection — filters/3.txt（只取子集，未整体引入）
#      隐私/归因 SDK {stats['sdk']} 条；常见广告交易平台 {stats['common']} 条
#   3. AdGuard Chinese filter — filters/224.txt（国内广告地址，取用 {stats['chinese']} 条补足上限）
#
# 处理规则：
#   - 只接受 ||domain^ 与 ||domain^$third-party 形式；丢弃 URL/路径/通配规则
#   - 丢弃带 $domain= 的站点限定规则（共 {stats['scoped_dropped']} 条），
#     这类规则仅在特定网站生效，当作全域拦截会误伤
#   - 丢弃 IP 条目（DOMAIN-SET 只接受域名，混入 IP 会导致整份文件解析失败）
#   - 剔除 Apple 域名与已知会打断支付/登录的功能域名
#   - 条目总数上限 {MAX_ENTRIES}
#
# 条目数：{len(domains)}
"""
    return header + "\n" + "\n".join(domains) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    ap.add_argument("--dry-run", action="store_true", help="生成到临时目录")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    domains, stats = build()
    content = render(domains, stats, stamp)

    print(f"\n生成条目数: {len(domains)}")
    print(f"  来源分布: mobile={stats['mobile']} sdk={stats['sdk']} "
          f"common={stats['common']} chinese={stats['chinese']}")

    if len(domains) > MAX_ENTRIES:
        print(f"错误：条目数 {len(domains)} 超过上限 {MAX_ENTRIES}", file=sys.stderr)
        return 1

    # 自检：无重复、无 IP、无通配
    assert len(domains) == len(set(domains)), "存在重复条目"
    for d in domains:
        assert DOMAIN_RE.match(d), f"格式非法: {d}"
    print("自检通过：无重复、无非法格式")

    if args.dry_run:
        tmp = Path("/tmp/appads.dryrun.txt")
        tmp.write_text(content, encoding="utf-8")
        print(f"试运行已写入 {tmp}")
        return 0

    if args.check:
        if not TARGET.exists():
            print(f"目标文件不存在: {TARGET}", file=sys.stderr)
            return 1
        old = TARGET.read_text(encoding="utf-8")
        # 只比数据行，忽略时间戳与注释差异
        old_d = [
            l.strip()
            for l in old.splitlines()
            if l.strip() and not l.lstrip().startswith("#")
        ]
        new_d = [
            l.strip()
            for l in content.splitlines()
            if l.strip() and not l.lstrip().startswith("#")
        ]
        if old_d == new_d:
            print("检查通过：数据行与上游一致，无需更新")
            return 0
        added = sorted(set(new_d) - set(old_d))
        removed = sorted(set(old_d) - set(new_d))
        print(f"检查发现差异：新增 {len(added)} 条，移除 {len(removed)} 条")
        for d in added[:10]:
            print(f"  + {d}")
        for d in removed[:10]:
            print(f"  - {d}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(content, encoding="utf-8")
    print(f"已写入 {TARGET}（{len(content.encode())} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

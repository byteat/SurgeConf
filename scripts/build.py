#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Surge 规则集合并生成器

从上游 ruleset.skk.moe 拉取规则，去重合并后生成 profile 直接引用的规则文件。

用法:
    python3 build.py              # 生成到 ../rules/
    python3 build.py --check      # 只校验现有文件与上游的差异，不写文件
    python3 build.py --dry-run    # 生成到临时目录并打印统计

设计原则:
  1. 只保留「最早命中」的条目。已快照的条目一旦被更宽的规则遮蔽，行为不变，删掉。
  2. 顺序严格遵循 profile [Rule] 的匹配优先级，不重排。
  3. 生成的每份文件都带分组注释与条目数，便于比对与追溯。
  4. 哨兵域名（skk.moe 标记）保留，用于验证规则是否生效。

输出文件:
  proxy.merged.conf          RULE-SET，代理域名
  direct-early.merged.conf   RULE-SET，直连域名（前段，必须在 Ai 之前）
  direct-late.merged.conf    RULE-SET，直连域名（后段，必须在 global 之后）
  ai.merged.conf             RULE-SET，AI 服务
  ip-telegram.ruleset.conf   RULE-SET，Telegram IP 段
  ip-domestic.ruleset.conf   RULE-SET，国内 IP 段
  local.merged.conf          RULE-SET，本地手工维护的拦截条目（源自 scripts/local.src.conf）
  local-reject.merged.conf   RULE-SET，本地单条规则 REJECT（源自 scripts/local-reject.src.conf）
  local-direct.merged.conf   RULE-SET，本地单条规则 DIRECT（源自 scripts/local-direct.src.conf）
  local-proxy.merged.conf    RULE-SET，本地单条规则 PROXY（源自 scripts/local-proxy.src.conf）
"""

import argparse
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://ruleset.skk.moe/List"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "rules"
CACHE = ROOT / ".cache"

SENTINEL = "7h15.ru1353t.1s.m4d3.by.5ukk4w.skk.moe"

# 上游来源 -> 输出目标。顺序即 profile [Rule] 中的匹配优先级。
REJECT_DOMAINSET = ["domainset/reject.conf", "domainset/reject_extra.conf"]
REJECT_EXTRA = ["non_ip/reject.conf"]          # 非域名型（KEYWORD/WILDCARD/AND/IP-CIDR）
REJECT_NO_DROP = ["non_ip/reject-no-drop.conf"]
PROXY_SRC = ["domainset/cdn.conf", "non_ip/cdn.conf", "non_ip/telegram.conf", "non_ip/global.conf"]

# DIRECT 组必须拆成两份文件，不能合并成一份。
#
# 原因：profile 原本的顺序是
#   apple_cdn(131) microsoft(133) apple_cn(137) WeChat(143)   ← 在 Ai 之前
#   Ai(139) cdn(143) telegram(147) global(151)
#   domestic(154) direct(157) lan(164)                        ← 在 global 之后
# 也就是说，「直连段」被中间的 Ai/cdn/global 分成了前后两组。
# 合成一份后，无论放哪个位置都会有一组跨越中间段，导致策略翻转：
#   放前面 → global 里 19 个域名（PT 站、blizzard.com 等）从 PROXY 变 DIRECT
#   放后面 → apple_cdn 的 160 条（mzstatic.com 等）从 DIRECT 变 PROXY
# 所以拆成 early（Ai 之前）和 late（global 之后）两份，顺序完全还原。
DIRECT_EARLY_SRC = ["domainset/apple_cdn.conf", "non_ip/microsoft_cdn.conf",
                    "non_ip/apple_cn.conf",
                    "Nobyda/Script/master/Surge/WeChat.list"]
DIRECT_LATE_SRC = ["non_ip/domestic.conf", "non_ip/direct.conf", "non_ip/lan.conf"]
AI_SRC = ["non_ip/ai.conf", "non_ip/apple_intelligence.conf"]
IP_TG_SRC = ["ip/telegram.conf"]
IP_CN_SRC = ["ip/domestic.conf"]

# 本地手工维护的源（不经上游 fetch，直接读 scripts/local.src.conf）
LOCAL_SRC = ROOT / "scripts" / "local.src.conf"
# 单条规则统一自托管：按策略分成三份，profile 用 RULE-SET 引用
LOCAL_SOURCES = {
    "local-reject.merged.conf": "scripts/local-reject.src.conf",
    "local-direct.merged.conf": "scripts/local-direct.src.conf",
    "local-proxy.merged.conf": "scripts/local-proxy.src.conf",
}

# wechat 走 NobyDa 的仓库，不在 ruleset.skk.moe 下
SIDE_URLS = {
    "nobyda/wechat": "https://raw.githubusercontent.com/NobyDa/Script/master/Surge/WeChat.list",
}

HDR = [
    "# 本文件由脚本从上游规则集合并去重生成，请勿手工编辑。",
    "# 生成脚本：scripts/build.py    重新生成：python3 scripts/build.py",
]

TYPE_MATCH = ("DOMAIN,", "DOMAIN-SUFFIX,", "DOMAIN-KEYWORD,", "DOMAIN-WILDCARD,",
              "IP-CIDR,", "IP-CIDR6,", "IP-ASN,", "GEOIP,", "USER-AGENT,",
              "PROCESS-NAME,", "URL-REGEX,", "AND,", "OR,", "NOT,")


def is_external(src: str) -> bool:
    return src.startswith("http") or "/" in src and not src.endswith(".conf")


def src_url(src: str) -> str:
    if src.startswith("http"):
        return src
    if src.startswith("Nobyda/"):
        return "https://raw.githubusercontent.com/" + src
    return f"{BASE}/{src}"


def fetch(path_or_url: str) -> list[str]:
    """取上游文件，优先用本地缓存（--refresh 可强制刷新）。"""
    url = src_url(path_or_url)
    name = url.replace("https://", "").replace("/", "_")[:120]
    cached = CACHE / name
    if cached.exists() and not ARGS.refresh:
        text = cached.read_text(encoding="utf-8")
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "surgeconf-build/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8")
        cached.write_text(text, encoding="utf-8")
    return text.splitlines()


def data_lines(lines: list[str]) -> list[str]:
    """去掉注释与空行，保留原始行（含行内注释）。"""
    out = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


DOMAINSET_SOURCES = {"domainset/cdn.conf", "domainset/apple_cdn.conf"}


def domainset_to_rules(path_src: str, lines: list[str]) -> list[str]:
    """domainset/*.conf 是裸域名列表，需转成 RULE-SET 语法。
    带 . 前缀 = 含子域 -> DOMAIN-SUFFIX；无前缀 = 精确 -> DOMAIN。"""
    out = []
    for d in data_lines(lines):
        d = d.split("//", 1)[0].strip()
        if not d:
            continue
        if d.startswith("."):
            out.append(f"DOMAIN-SUFFIX,{d[1:]}")
        else:
            out.append(f"DOMAIN,{d}")
    return out


def rule_key(line: str) -> str:
    """规则去重键：整条规则（去首尾空白）。
    注意：不能按 // 切分——URL-REGEX 里含 http:// 会被截断成 http:，
    导致不同的 URL 规则被误判为重复。"""
    return line.strip()


def rule_domain(line: str) -> str | None:
    """取规则行的域名部分；非域名型规则返回 None。"""
    parts = [x.strip() for x in line.split(",")]
    if len(parts) < 2:
        return None
    if parts[0].upper() in ("DOMAIN", "DOMAIN-SUFFIX"):
        return parts[1].lower().lstrip(".")
    return None


def domain_of(line: str):
    """若是域名型规则，返回 (类型, 域名)；否则 None。"""
    body = line.strip()
    for t in ("DOMAIN-SUFFIX,", "DOMAIN,", "DOMAIN-WILDCARD,", "DOMAIN-KEYWORD,"):
        if body.startswith(t):
            return t[:-1], body[len(t):].split(",")[0]
    return None


def dedup_domainset(domains: list[str]) -> tuple[list[str], int]:
    """DOMAIN-SET 去重：后缀条目被更短后缀覆盖则删除；精确条目被后缀覆盖则删除。"""
    suffix, exact = [], []
    for d in domains:
        (suffix if d.startswith(".") else exact).append(d)

    S = set(suffix)
    kept_suffix, dropped_sfx = [], 0
    for s in suffix:
        parts = s[1:].split(".")
        covered = False
        for i in range(1, len(parts)):
            if "." + ".".join(parts[i:]) in S:
                covered = True
                break
        if covered:
            dropped_sfx += 1
        else:
            kept_suffix.append(s)

    kept_exact, dropped_ex = [], 0
    for e in exact:
        parts = e.split(".")
        covered = any("." + ".".join(parts[i:]) in S for i in range(len(parts)))
        if covered:
            dropped_ex += 1
        else:
            kept_exact.append(e)
    return kept_suffix + kept_exact, dropped_sfx + dropped_ex


def build_reject_domainset() -> str:
    domains: list[str] = []
    per_src = []
    for src in REJECT_DOMAINSET:
        got = [d.strip() for d in data_lines(fetch(src))]
        per_src.append((src, len(got)))
        domains.extend(got)

    total_raw = len(domains)
    uniq = list(dict.fromkeys(domains))          # 保序去重
    dup = total_raw - len(uniq)
    merged, covered = dedup_domainset(uniq)

    suffix = [d for d in merged if d.startswith(".")]
    exact = ["." and d for d in merged if not d.startswith(".")]

    out = list(HDR)
    out += [
        "# 合并原则：按本地 [Rule] 的匹配优先级排列；重复条目只保留最先命中的那条。",
        "# 上游来源与条目数见各分组注释。",
        "# 格式：DOMAIN-SET 纯域名列表（. 前缀 = 含子域，无前缀 = 精确匹配）",
        f"# 上游：{' + '.join(s.replace('.conf','') for s in REJECT_DOMAINSET)}",
        f"# 去重：剔除完全重复 {dup} 条；剔除被更短后缀覆盖的条目 {covered} 条",
        f"# 保留：上游哨兵域名 {SENTINEL}",
        f"# 条目数：{len(merged)}",
        "",
    ]
    S = set(suffix)
    E = set(exact)
    all_exact = []
    emitted = set()          # 已写出过的条目，防止同一域名跨段重复
    for src, n in per_src:
        out.append(f"# ==== 来自 {src}（{n} 条）====")
        got = [d.strip() for d in data_lines(fetch(src))]
        kept_sfx = []
        for d in got:
            if d.startswith(".") and d in S and d not in emitted:
                kept_sfx.append(d)
                emitted.add(d)
        out.extend(kept_sfx)
        # 精确条目统一收进文件末尾的独立段（与 Surge 的匹配语义一致：后缀优先）
        for d in got:
            if not d.startswith(".") and d in E and d not in emitted:
                all_exact.append(d)
                emitted.add(d)
        out.append("")
    out.append("# ==== 精确域名 ====")
    out.extend(all_exact)
    return "\n".join(out).rstrip() + "\n"


NOTES_FILE = ROOT / "notes.json"


def load_notes() -> dict:
    """读人工维护的注释补充（不随重生成丢失）。"""
    import json
    if NOTES_FILE.exists():
        return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    return {}


def build_ruleset(sources: list[str], title: str,
                  keep_ip: bool, note: str = "",
                  exclude: set[str] | None = None) -> str:
    """通用 RULE-SET 生成：按来源顺序合并，跨来源去重（保留先出现的）。

    exclude：需要剔除的域名集合。用于解决「profile 里靠前的规则集能够命中、
    但合并后位置改变会导致策略翻转」的冲突——这些条目在合并文件里删掉，
    让它们自然落到 profile 中真正该命中的那条规则上。
    """
    seen = set()
    blocks = []
    total = 0
    for src in sources:
        raw = fetch(src)
        lines = domainset_to_rules(src, raw) if src in DOMAINSET_SOURCES else data_lines(raw)
        kept = []
        for ln in lines:
            k = rule_key(ln)
            if k in seen:
                continue
            if exclude:
                dom = rule_domain(ln)
                if dom and dom in exclude:
                    continue
            seen.add(k)
            kept.append(ln)
        total += len(kept)
        blocks.append((src, kept))
        # 未使用的参数保留接口
    if not keep_ip:
        total = sum(len(k) for _, k in blocks)

    out = list(HDR)
    out += [
        "# 合并原则：按本地 [Rule] 的匹配优先级自上而下排列；与前面分组重复的条目已删除（它本来就被遮蔽，行为不变）。",
        "# 每段开头注明上游来源，便于追溯与自动更新时比对。",
        f"# {title}（去重合并）",
        f"# 条目数：{total}",
    ]
    if note:
        out.append(f"# {note}")
    out += ["# ===== 上游来源 ====="]
    for src, _ in blocks:
        out.append(f"# {src}  <- {src_url(src)}")
    out.append("")
    notes = load_notes()
    for src, kept in blocks:
        label = src.split("/")[-1].replace(".conf", "")
        out.append(f"# ==== 分组：{label}（{len(kept)} 条）====")
        out.append(f"# {label}  ({len(kept)} 条)  <- {src_url(src)}")
        for note in notes.get(label, []):
            out.append(note)
        out.extend(kept)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build_local_file(rel_src: str = "scripts/local.src.conf", note: str = "") -> str:
    """把手工维护的本地条目套上统一头，产出一份 RULE-SET。

    rel_src：源文件的仓库相对路径。
    note：写入文件头的一行说明。
    源文件不存在时返回空内容，避免整个 build 崩掉。
    """
    src = ROOT / rel_src
    if not src.exists():
        return ""
    data = [l.rstrip() for l in src.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.lstrip().startswith("#")]
    out = [
        "# 本文件由脚本生成，请勿手工编辑；要改条目请改源文件 " + rel_src + "。",
        "# 生成脚本：scripts/build.py    重新生成：python3 scripts/build.py",
    ]
    if note:
        out.append("# " + note)
    out += [f"# 条目数：{len(data)}", ""]
    out.extend(data)
    return "\n".join(out).rstrip() + "\n"


def build_ip_file(sources: list[str], title: str, policy: str) -> str:
    out = [f"# {title}（RULE-SET 格式）"]
    for src in sources:
        out.append(f"# 上游：{src_url(src)}")
    out.append(f"# 引用时策略用 {policy}（RULE-SET 文件本身不含策略，策略写在 profile 的引用行上）")
    out.append("# 说明：全部带 no-resolve，对纯域名请求不触发 DNS 查询。")
    out.append(f"# 上游哨兵 DOMAIN,{SENTINEL} 属拦截测试标记，不收录。")
    out.append("")
    total = 0
    for src in sources:
        for ln in data_lines(fetch(src)):
            if ln.startswith(("IP-CIDR,", "IP-CIDR6,")):
                out.append(ln)
                total += 1
    return "\n".join(out).rstrip() + "\n"


def build_all() -> dict[str, str]:
    wechat = SIDE_URLS["nobyda/wechat"]
    files = {}
    files["proxy.merged.conf"] = build_ruleset(PROXY_SRC, "PROXY 组", True)
    files["direct-early.merged.conf"] = build_ruleset(
        DIRECT_EARLY_SRC, "DIRECT 组（前段：Ai 之前的直连规则）", True)
    files["direct-late.merged.conf"] = build_ruleset(
        DIRECT_LATE_SRC, "DIRECT 组（后段：global 之后的直连规则）", True)
    files["ai.merged.conf"] = build_ruleset(AI_SRC, "Ai 组", True)
    files["ip-telegram.ruleset.conf"] = build_ip_file(IP_TG_SRC, "Telegram IP 段", "PROXY")
    files["ip-domestic.ruleset.conf"] = build_ip_file(IP_CN_SRC, "国内 IP 段", "DIRECT")
    files["local.merged.conf"] = build_local_file(
        note="本文件承载无法放进 DOMAIN-SET 的非域名型规则（URL-REGEX 等）。")
    for out_name, src in LOCAL_SOURCES.items():
        files[out_name] = build_local_file(
            rel_src=src, note="单条规则统一自托管，策略列与文件名一致。")
    return files


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对上游与现有文件，不写")
    ap.add_argument("--dry-run", action="store_true", help="生成到临时目录")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，重新拉取上游")
    ARGS = ap.parse_args()

    files = build_all()
    target = Path("/tmp/surge-build") if ARGS.dry_run else OUT
    target.mkdir(parents=True, exist_ok=True)

    changed = []
    for name, content in files.items():
        p = target / name
        old = p.read_text(encoding="utf-8") if p.exists() else None
        if ARGS.check:
            same = old == content
            n = len([l for l in content.splitlines() if l and not l.startswith("#")])
            print(f"{'一致' if same else '有差异':6s}  {name:32s} {n:>7} 条")
            if not same:
                changed.append(name)
            continue
        p.write_text(content, encoding="utf-8")
        n = len([l for l in content.splitlines() if l and not l.startswith("#")])
        print(f"写入  {name:32s} {n:>7} 条")

    if ARGS.check and changed:
        print(f"\n{len(changed)} 份与上游不同：{', '.join(changed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

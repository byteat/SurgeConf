# SurgeConf

Surge 配置使用的**去重合并后远程规则集**，由上游 [ruleset.skk.moe](https://ruleset.skk.moe) 的多份规则合并而成。

## 文件

| 文件 | 数据行 | 用途 |
|---|---:|---|
| `rules/reject.domainset.txt` | 233,767 | DOMAIN-SET，广告/追踪拦截（4.8 MB，profile 中最大的远程资源） |
| `rules/proxy.merged.conf` | 6,022 | 代理 RULE-SET |
| `rules/direct.merged.conf` | 1,649 | 直连 RULE-SET |
| `rules/reject-extra.ruleset.conf` | 227 | 补充拦截 RULE-SET |
| `rules/reject-no-drop.ruleset.conf` | 38 | 不丢弃连接的拦截 RULE-SET |
| `rules/ai.merged.conf` | 61 | AI 服务 RULE-SET |

所有文件 `pre-matching` 均为 0，各保留 1 条上游哨兵域名用于验证规则是否生效。

## 拦截量口径

```
真遮蔽 129 = cdn 94 + domestic 19 + wechat 9 + global 7
  cdn 94 = domainset 91（reject 后缀 53 + reject_extra 后缀 35 + 精确段 3）
         + non_ip/reject 3（域名型 2：micro.rubiconproject.com、scorecardresearch.com
                          + KEYWORD 1：appsflyersdk.com）
  35     = reject 24 + reject_extra 9 + no-drop 2

单列不计入拦截：iadsdk.apple.com（实测命中 apple.com → DIRECT）
```

**脚注（两种口径，避免重复排查）**

`reject.domainset.txt` 保留三段边界（第 10、148,096、232,646 行），因此上表可按产物直接复算。差异仅在 1 条 `js.partnerstack.com`：

- 按**上游原文**：reject 后缀 53 + 精确 4 + reject_extra 34
- 按**合并产物**：reject 后缀 53 + 精确 3 + reject_extra 35

原因是上游把精确条目分散在各段内，产物把精确条目统一移到末尾的独立段；同时 `js.partnerstack.com`（上游 `domainset/reject` 精确条目）被 `.partnerstack.com` 后缀覆盖删除。**两种口径总和都是 91。**

## 使用约束（实测确认）

1. **放行任何域名必须加在 reject 段之前**（按 `match_rule` 的 `rule-source.line` 定位），加在后面无效。
2. **绝不可给 reject 段加 `pre-matching`**——它在 DNS 解析阶段就返回 No Record，会击穿 profile 里的本地域名覆盖。
3. 引用行号须标明基准：文件原始行号（含注释）与去除注释后的行号不同。
4. 切换 `reject.domainset.txt`（4.8 MB / 23.37 万条）前先测内存占用；采样须避开 Script Hub 转换活动，否则读数失真。
5. 用 `wc -l` 报行数。文件末尾存在连续换行，`split('\n')` 会多算 1 行。

## 已知虚警（不必再查）

- `latency-test.skk.moe`：实测走 PROXY，`http_probe` 返回 204，测速端点正常。**不要**加 `DOMAIN,latency-test.skk.moe,DIRECT`，那会让测速失去意义。
- `cloudflarestream.com`：不在拦截名单，既未命中 KEYWORD 也未命中后缀。

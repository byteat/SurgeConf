# Surge 规则集合并流程

## 为什么要合并

profile 原本引用约 12 份远程规则集。合并后的好处：

- 远程资源从 12 条降到 8 条，Surge 启动和规则匹配更快
- 去重后条目数减少（被更宽规则遮蔽的条目已删除，行为不变）
- 规则顺序固定在文件里，不受上游分段顺序变化影响

## 文件构成

| 文件 | 上游来源 | profile 引用方式 |
|---|---|---|
| `rules/reject.domainset.txt` | `domainset/reject` + `domainset/reject_extra` | `DOMAIN-SET` |
| `rules/reject-extra.ruleset.conf` | `non_ip/reject` | `RULE-SET` |
| `rules/reject-no-drop.ruleset.conf` | `non_ip/reject-no-drop` | `RULE-SET`（策略 `REJECT-NO-DROP`） |
| `rules/proxy.merged.conf` | `domainset/cdn` + `non_ip/cdn` + `non_ip/telegram` + `non_ip/global` | `RULE-SET` |
| `rules/direct.merged.conf` | `domainset/apple_cdn` + `non_ip/microsoft_cdn` + `non_ip/apple_cn` + NobyDa WeChat + `non_ip/domestic` + `non_ip/direct` + `non_ip/lan` | `RULE-SET` |
| `rules/ai.merged.conf` | `non_ip/ai` + `non_ip/apple_intelligence` | `RULE-SET` |
| `rules/ip-telegram.ruleset.conf` | `ip/telegram` | `RULE-SET`（策略 `PROXY`） |
| `rules/ip-domestic.ruleset.conf` | `ip/domestic` | `RULE-SET`（策略 `DIRECT`） |

`reject-extra.ruleset.conf` 由早期手工生成，脚本尚未覆盖它的「domainset 未覆盖域名」补集逻辑，重跑时不要覆盖它。

## 用法

```bash
python3 scripts/build.py              # 重新生成到 rules/
python3 scripts/build.py --dry-run    # 生成到 /tmp/surge-build，不动 rules/
python3 scripts/build.py --check      # 只比对，报告与现有文件的差异，不写
python3 scripts/build.py --refresh    # 忽略缓存，强制重新拉取上游
```

上游文件缓存在 `.cache/`，默认复用，避免每次重跑都去拉。

## 关键实现细节（踩过的坑）

**1. `domainset/*.conf` 是裸域名列表，必须转换**

上游 `domainset` 目录的文件不是 RULE-SET 语法，是纯域名列表：

```
.esm.sh    ← 带点前缀 = 含子域 → DOMAIN-SUFFIX,esm.sh
0x0.st     ← 无前缀   = 精确匹配 → DOMAIN,0x0.st
```

漏了这层转换，生成的 proxy/direct 会出现大量裸域名行，Surge 直接解析失败。

**2. 去重键绝对不能按 `//` 切分**

`URL-REGEX,^http://www\.microsoft\.com/pkiops/` 这样的规则含 `http://`，按 `//` 切分会变成 `URL-REGEX,^http:`，导致所有 URL-REGEX 规则被误判为同一条而互相删除。去重键必须用整行。

**3. DOMAIN-SET 的去重语义**

- 后缀条目（`.foo.com`）若被更短后缀（`.com`）覆盖，删除
- 精确条目（`bar.foo.com`）若被任何后缀覆盖，删除
- 精确条目统一收进文件末尾的独立段

**4. 精确段必须只生成一次**

早期脚本在每个上游来源后都插一个「精确域名」段，导致文件里出现两个同名段，条目被分散。正确做法是把所有精确条目集中到文件末尾。

**5. `ip/telegram` 和 `ip/domestic` 要单独成文件**

`DOMAIN-SET` 装不了 `IP-CIDR`。而 profile 原本就是把 IP 规则和域名规则分开引用的，拆成独立文件更贴合结构。

## 已知限制（文件补了也不生效）

`apple-relay.apple.com` 和 `gspe1-ssl.ls.apple.com` 虽然收录在 `ai.merged.conf`，但 profile 第 98 行有 `DOMAIN-SUFFIX,apple.com,DIRECT` 排在前面，会把它们抢先命中。**这跟合并无关，现状就是这样。** 要让它们走 Ai，得把 Ai 规则移到第 98 行之前，那会影响所有 `*.apple.com` 的走向，需单独决策。

## 更新流程

```bash
python3 scripts/build.py --refresh    # 拉最新上游并重新生成
git diff --stat                       # 看变了什么
python3 scripts/build.py --check      # 确认无差异
git add -A && git commit && git push
```

推送后 Surge 会在下次刷新规则时自动拉取新版本（`raw.githubusercontent.com` 命中 `proxy.merged.conf` 里的 `githubusercontent.com` 规则，走 PROXY）。

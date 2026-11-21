# Surge 规则集仓库

本仓库有两套独立内容：

1. **非拦截类合并规则**（`scripts/build.py` 生成）——proxy / direct / ai / 两条 IP 段，把上游多份规则集合并成固定顺序的文件，减少远程资源数量。
2. **App 专用广告拦截清单**（`scripts/build_appads.py` 生成，每日自动更新）——见文末「App 专用广告清单」一节。

## 为什么合并非拦截规则

profile 原本引用约 12 份远程规则集。合并后：

- 远程资源数量减少，Surge 启动和规则匹配更快
- 规则顺序固定在文件里，不受上游分段顺序变化影响

## 关于拦截（reject）规则

**本仓库不再维护 skk 的那套 reject 合并文件**，相关文件已删除：

- ~~`rules/reject.domainset.txt`~~（原 233,767 条，`domainset/reject` + `domainset/reject_extra` 合并）
- ~~`rules/reject-extra.ruleset.conf`~~（原 `non_ip/reject`）
- ~~`rules/reject-no-drop.ruleset.conf`~~（原 `non_ip/reject-no-drop`）

原因：那套是「大而全的网页拦截」，23 万条里能归到 App 广告 SDK 的不足 1%，实际命中极少（实测开机至今 `domainset/reject` 91 次、`reject_extra` 4 次、`non_ip/reject` 1 次）。现改用 App 专用清单 `rules/appads.domainset.txt`。

profile 里其余拦截仍直接引用上游地址（`non_ip/reject-no-drop.conf` 等），不经过本仓库。

## 文件构成

| 文件 | 上游来源 | profile 引用方式 |
|---|---|---|
| `rules/proxy.merged.conf` | `domainset/cdn` + `non_ip/cdn` + `non_ip/telegram` + `non_ip/global` | `RULE-SET` |
| `rules/direct.merged.conf` | `domainset/apple_cdn` + `non_ip/microsoft_cdn` + `non_ip/apple_cn` + NobyDa WeChat + `non_ip/domestic` + `non_ip/direct` + `non_ip/lan` | `RULE-SET` |
| `rules/ai.merged.conf` | `non_ip/ai` + `non_ip/apple_intelligence` | `RULE-SET` |
| `rules/ip-telegram.ruleset.conf` | `ip/telegram` | `RULE-SET`（策略 `PROXY`） |
| `rules/ip-domestic.ruleset.conf` | `ip/domestic` | `RULE-SET`（策略 `DIRECT`） |
| `rules/appads.domainset.txt` | AdGuard 三个过滤器（见文末） | `DOMAIN-SET`（策略 `REJECT`） |

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

---

## App 专用广告清单（`rules/appads.domainset.txt`）

由 `scripts/build_appads.py` 生成，与上面的 `build.py` 是两套独立流程。

### 来源分层

| 层 | 来源 | 收录方式 |
|---|---|---|
| 1 | AdGuard Mobile Ads filter（`filters/11.txt`） | 全量收录，清单基础 |
| 2 | AdGuard Tracking Protection（`filters/3.txt`） | 只取 App 相关子集，不整体引入 |
| 3 | AdGuard Chinese filter（`filters/224.txt`） | 国内广告地址，按优先级补足上限 |

第 2 层只挑两类：隐私/归因 SDK（appsflyer、adjust、branch、umeng、bugly、jpush…）与常见广告交易平台（doubleclick、googlesyndication、taboola、criteo…）。该文件有 10 万条，整体引入会远超上限且绝大多数与 App 无关。

条目总数上限由 `MAX_ENTRIES` 控制，当前 **10000**。核心层（前两层）永远全保留，剩余名额从第 3 层按「国内平台标识优先」补足。

### 处理时踩过的坑

**1. `$domain=` 站点限定规则必须丢弃**

`||alicdn.com^$domain=m.petdianping.com|m.6080w.com` 这种规则，只在括号里列出的网站生效。把 `alicdn.com` 剥出来当全域拦截，会**拦掉整个阿里 CDN**。同理还有 `||mzstatic.com^$image,domain=...`。脚本一律跳过含 `domain=` 的行。

**2. IP 条目必须丢弃**

DOMAIN-SET 只接受域名。混进一条 `23.224.199.74` 会让**整份文件解析失败**，不是只丢那一条。

**3. 带 `$third-party` 的行不能丢**

早期正则写成 `^\|\|([^/^$]+)\^$`，把 `||pagead46.l.doubleclick.net^$third-party` 这类当非法格式丢了，导致几个最大的广告域名缺席。正确做法是允许 `$` 后缀，只拦 `domain=`。

**4. Apple 域名要整域排除，不是精确匹配**

排除表若只写 `apple.com` 精确匹配，`iadsdk.apple.com` 会漏过。Apple 相关用后缀匹配（`endswith(".apple.com")`）；而百度、腾讯这类只排除整站本身——`pos.baidu.com` 是要拦的广告子域。

### 推送与生效顺序（重要）

`raw.githubusercontent.com` 的响应头是 `cache-control: max-age=300`，**CDN 缓存 5 分钟**。推送后立刻改 profile，Surge 会拉到缓存里的旧版本甚至 404：

- 第一次踩坑：Surge reload 时新规则集 `ready: false`、`error: "The file does not exist"`
- 表现是规则没加载，请求落到后面的 `domestic.conf` 被判 DIRECT

正确顺序：

1. 推送
2. **等 raw 返回新的大小**（轮询或等 5 分钟）
3. 匿名拉取验证字节数 / SHA256
4. 再改 profile，或 `update_external_resource` 强制刷新
5. `list_external_resources` 确认 `ready: true` 且 `updatedAt` 非零（`updatedAt: 0` 表示失败）

### 自动更新

`.github/workflows/update-rules.yml`，每天 UTC 18:30（北京 02:30）跑一次，也可手动 `workflow_dispatch`。

这个 workflow 同时覆盖两套内容：App 广告清单，以及 `build.py` 生成的 5 份合并规则（proxy/direct/ai/两条 IP 段）。

流程是「先 `--check` 比对 → 有变化才重新生成 → 校验 → 提交」，**无变化不产生空提交**。提交前的校验项：条目数与文件头声明一致、不超上限、无重复、无 IP 条目。

本地验证：

```bash
python3 scripts/build_appads.py --check     # 与上游比对，有差异 exit 1
python3 scripts/build_appads.py --dry-run   # 生成到 /tmp 看统计
python3 scripts/build_appads.py             # 直接重新生成
```

### 实测过的内存影响

| 状态 | memory-bytes |
|---|---|
| 原来 23 万条网页拦截 | 25,970,336 |
| 933 条（App-only 初版） | 24,643,232 |
| 7,176 条（当前，上限 1 万） | 25,167,520 |

**结论**：换这份清单省不下多少内存（约 0.8 MiB），收益在「拦截范围贴合 App」而不在性能。早先估算的「省 20 MB」不成立，`domainset` 索引之外的固定开销（virtual-ip、模块脚本）占大头。

### 已知边界（App 内广告拦不全）

以下广告不经过域名层，拦不住，需要模块脚本处理：

- 广告内容与正常接口同域（`/api/ad/xxx` 这类路径）
- 走 IP 直连、不带 SNI 的请求
- 已内置在 App 里的本地广告逻辑

国内 App 的广告拦截实际主要靠现有模块（如「QQ音乐去广告 · 抓包实证版」拦 `xs.gdt.qq.com`），这份清单是补充，不是替代。

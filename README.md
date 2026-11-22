# SurgeConf

Surge 自托管远程规则集仓库。

- 仓库：https://github.com/byteat/SurgeConf
- 规则集地址：`https://raw.githubusercontent.com/byteat/SurgeConf/main/rules/<文件名>`
- 公开仓库，无需 token，匿名可拉取。

## 规则文件

`rules/` 当前 11 份文件。数据行数 = 非注释、非空白行。

| 文件 | 数据行 | 类型 | 用途 |
|---|---:|---|---|
| `local-reject.merged.conf` | 3 | RULE-SET | 本地单条 REJECT 规则自托管 |
| `local-direct.merged.conf` | 12 | RULE-SET | 本地单条 DIRECT 规则自托管 |
| `local-proxy.merged.conf` | 3 | RULE-SET | 本地单条 PROXY 规则自托管 |
| `local.merged.conf` | 2 | RULE-SET | 本地自定义拦截（高德更新接口 + Deepal 开屏广告） |
| `appads.domainset.txt` | 7,176 | DOMAIN-SET | App 广告与隐私 SDK 域名拦截 |
| `direct-early.merged.conf` | 561 | RULE-SET | Apple / Microsoft CDN 直连 |
| `ai.merged.conf` | 95 | RULE-SET | AI 服务 |
| `proxy.merged.conf` | 6,026 | RULE-SET | CDN / Telegram / 全球流量代理 |
| `ip-telegram.ruleset.conf` | 14 | RULE-SET | Telegram IP 段代理 |
| `direct-late.merged.conf` | 1,094 | RULE-SET | 国内域名直连 |
| `ip-domestic.ruleset.conf` | 2 | RULE-SET | 国内 IP 段直连 |

规则源文件位于 `scripts/*.src.conf`，合并产物位于 `rules/`。

## 上游来源

- 非拦截规则、IP 段：`ruleset.skk.moe`
- AI 服务：`ruleset.skk.moe` + blackmatrix7/ios_rule_script（OpenAI / Claude / Anthropic / Gemini）
- App 广告清单：AdGuard Mobile Ads / Tracking Protection / Chinese filter

## 生成与更新

```bash
python3 scripts/build.py              # 合并规则
python3 scripts/build_appads.py       # App 广告清单
python3 scripts/build.py --check      # 比对，不写入
```

自动更新：`.github/workflows/update-rules.yml`，每天 UTC 18:30 运行，无变化时跳过提交。

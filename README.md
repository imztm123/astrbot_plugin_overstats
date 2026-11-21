# overstats · ZTM 适配优化版（v2.7.7）

> ## ⚠️ 声明
>
> **本项目是基于原版 [`astrbot_plugin_overstats`](https://github.com/Zzzzzzouhang/astrbot_plugin_overstats) 二次修改的版本**：在原版基础上，针对不断更新的 AstrBot 做适配性更新与优化（`@` 渲染变更、消息链兼容、插件市场接入等），并非原创项目。
>
> - 原作者：**[@Zzzzzzouhang](https://github.com/Zzzzzzouhang)**（插件本体）
> - 后端服务：[`Zzzzzzouhang/Overstats`](https://github.com/Zzzzzzouhang/Overstats) / [`AddOneSecondL/Overstats`](https://github.com/AddOneSecondL/Overstats)
> - 二次修改：**ZTM**（[`imztm123`](https://github.com/imztm123)）
> - 本仓库：`imztm123/astrbot_plugin_overstats`，版本 `v2.7.7`
> - 插件包名沿用 `astrbot_plugin_overstats`；市场身份为 `owner/name`（即 `imztm123/astrbot_plugin_overstats`），与原作者的 `Zzzzzzouhang/...` 天然隔离。市场卡片标题为「overstats（ZTM 优化版）」以示区分
> - 版权与许可沿用原仓库 [LICENSE](./LICENSE)
>

一款为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 打造的守望先锋数据查询插件，作为桥梁连接由网易大神接口驱动的 [Overstats](https://github.com/AddOneSecondL/Overstats) 后端服务，为群聊带来守望先锋战绩查询与资讯体验。

---

# 一、本版相对上游的改动

## 1) 修复「是区吗」无法使用（判定书始终出不来）

**现象**：`是区吗` 有响应、进度提示正常，但判定书图片永远发不出来，后端接口 500。

**根因**（两处代码版本错位）：

| 位置 | 问题 |
| --- | --- |
| 插件 `deploy/ops/config_writer.py` 的 `_SHIQU_CONFIG_TEMPLATE` | 生成的 `ShiquLLMConfig` 只有 `base_url / api_key / model / stream / timeout_seconds / retry`，**没有 `json_mode` 字段** |
| 后端 `src/modules/dashen_shiqu/service.py:1159` | 直接读 `if cfg.json_mode:` 决定是否加 `response_format={"type":"json_object"}` |

于是每次「是区吗」进入 `_call_llm()` 就抛：

```
AttributeError: 'ShiquLLMConfig' object has no attribute 'json_mode'
```

`/dashen-shiqu/image` 返回 500，图片自然拿不到。而「开庭」走自带 `json_schema`（`strict: False`），不读该字段，所以**只有「是区吗」单点失效**——这也解释了为什么其它指令都正常。

**修复**：

1. 模板补上两个字段（默认 `true`）：

```python
    strict: bool = True
    json_mode: bool = True
        ...
        strict=_parse_bool(_get("SHIQU_LLM_STRICT", "true"), True),
        json_mode=_parse_bool(_get("SHIQU_LLM_JSON_MODE", "true"), True),
```

2. 用插件模板**重新生成后端 `config/shiqu_config.py`**，并保留原有的 base_url / api_key / model：

```bash
python3 scripts/fix_shiqu_config.py \
  --plugin  /AstrBot/data/plugins/astrbot_plugin_overstats \
  --backend /AstrBot/data/plugin_data/astrbot_plugin_overstats/overstats_backend
```

脚本流程：`ast` 静态读取现有配置 → 三项任一为空即中止（绝不用空值覆盖把 Key 抹掉）→ `importlib` 加载插件 `config_writer.py` → 备份为 `shiqu_config.py.bak.<时间戳>` → `write_shiqu_config()` 重写（内含 `py_compile` 语法校验）→ 失败自动回滚 → 提示重启后端。只输出「正常 / 缺失」，不打印密钥。修复后执行 `/ow重启后端` 生效。

## 2) 适配 AstrBot v4.29.0：修复更新后消息里「@」变成乱码

**现象**：升级 AstrBot v4.29.0 后，图片消息里出现 `@` 加一串乱码 / 占位符，有时整条消息直接发送失败。

**根因**：v4.29.0 调整了消息组件与 QQ 官方适配层的序列化方式，`Comp.At` 不再稳定渲染成昵称文本，而是被降级成内部 ID 占位；插件把 `At` 拼进出站消息链时，这段内部标识就暴露给了用户。

**修复**：出站消息链不再写 `At`，改为纯文本 + 图片：

```python
# main.py → _build_image_chain()
# 改前
chain = [Comp.At(qq=user_id), Comp.Plain('\n' if not fallback_text else f'\n{fallback_text}\n'), image_comp]
# 改后
chain = [Comp.Plain(f'{fallback_text}\n'), image_comp] if fallback_text else [image_comp]

# main.py → _send_multiple_images_result()
# 改前
chain = [Comp.At(qq=user_id), Comp.Plain('\n')]
# 改后
chain = []
```

入站方向不受影响：「是否真实 @ 了机器人」仍由 `_is_real_at_bot()` 按 `event.get_self_id()` 比对 `Comp.At.qq`，并排除 QQ 官方占位 `self_id`（`qq_official` / `unknown_selfid`），不依赖文本里的 `@`。

## 3) 并发与账号轮询：默认保持上游，按需自助调优

**先说明一件事**：账号池轮询是 **Overstats 上游本身就有的机制**（`DASHEN_ACCOUNTS` 配多个账号即自动在池内分摊请求），本版没有改动这部分代码。

本版也**没有修改默认并发参数**，面板与后端示例配置一律保持上游默认值：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `dashen_account_max_requests_per_second` | `5` | 单账号每秒请求上限 |
| `dashen_max_concurrent_requests` | `2` | 进程内全局并发上限 |
| `DASHEN_MAX_ACCEPTED_REQUESTS` | `账号数 × 4` | 上游硬限制：超过（活跃+排队）直接返回 429 |

**为什么不做成"更高"的默认值**：上游接口对「账号数 × 4」以上的请求直接返回 429。单账号用户把并发调到 12/14 只会换来满屏限流，所以默认值必须按最小可用配置给。

**想要更快，按自己的账号数调**（在插件面板改，auto 模式会写进后端 `config.py`）：

| 账号数 | `max_requests_per_second` | `max_concurrent_requests` |
| --- | --- | --- |
| 1 个（默认） | `5` | `2` |
| 2 个（本机实测） | `8` | `14` |
| 3 个及以上 | `8` | `≤ 账号数 × 4` |

调完执行 `/ow重启后端` 生效。经验规则：**全局并发 ≤ 账号数 × 4**，越过这条线就是 429。

> 参考数据：2 账号 + `8/14` 的配置下，跨海查询战绩与竞技强度的等待时间明显下降；单账号维持默认即可，收益极小且容易触发限流。
## 4) 其它改动

- `metadata.yaml`：`version` → `v2.7.7`，`author` → `Zzzzzzouhang, ZTM`，`repo` → 本仓库；`logo.png` 换为自制图标（1:1，256×256）。
- `_conf_schema.json` 的 `backend_repo_url` 默认值、`deploy/ops/manager.py` 的兜底地址，全部指向本仓库，避免插件更新时拉回原版覆盖改动。
- `deploy/ops/config_writer.py`、`deploy/ops/manager.py` 增加 `[Shiqu诊断]` 日志（仅打印布尔值），便于排查是区吗 LLM 配置是否下发成功。
- ID 解析失败文案统一为「未查询到id或者id错误，id严格区分大小写（受大神接口维护影响，部分id不可用）」。
- 新增 `scripts/fix_shiqu_config.py`；两个仓库均补 `.gitattributes`（`* text=auto eol=lf`），避免 Windows 提交给 Linux 部署带来 CRLF 污染。

---

# 二、🌟 核心特性

- **极速绑定**：支持 `/绑定` 指令；群聊中直接发送符合规范的战网 ID（如 `Player#12345`）也会被自动截获并完成绑定。
- **全景数据大盘**
  - 战绩总结：今日 / 昨日 / 周度大数据总结卡片；今日无对局时自动回退拉取昨日总结。
  - 对局分析：最近对局列表；群聊中直发数字 `1~20` 可一键拉取单局多图超详细战绩。
  - 天梯评估：历史历届段位追溯、快速/竞技英雄云图（Treemap 矩形树图）、开黑同玩胜率、快速/竞技强度指数评估。
- **国服 / 外服资讯**：全服段位分布图、Ban/Pick 胜率排行、每周精选商店、实时赛事对阵图、赛季地图池、皮肤搜索、外服版本补丁说明。
- **专精排行榜**：按省份 / 位置查询天梯省榜（tank / dps / healer / open），以及单英雄专精绝活榜。
- **AI 趣味判定**：`是区吗`「判定书」与 `开庭`「判决书」，基于最近多局数据由 LLM 生成图片，支持分级冷却、白名单、每日限次。
- **平台动态适配**：原生支持 QQ 官方机器人；官方环境下自动启用动态 Markdown 按钮标签（`<qqbot-cmd-input>`）并做编码处理，其他平台自动剥离标签转纯文本。
- **全量消息适配**（按群独立配置）：`@昵称模式`（`@机器人昵称 指令`）与 `直接匹配模式`（直接发 `今日总结` 等即可），持久化到 JSON。
- **群组级别配置**：按群单独配置「首次提示后不再提示」「追加交互提示」「全量消息适配」，群管理员通过 `/群设置`、`/全量适配开|关` 管理。
- **内置 Web 监控面板**：运行总览、指令成功率、调用趋势、后端性能、限流统计、错误日志（SSE 实时推送）。

---

# 三、🛠️ 前置准备

本插件仅用于 AstrBot 与 Overstats 后端通讯，支持两种接入模式：

| 模式 | 说明 |
| --- | --- |
| **一键托管（auto）** | 插件自动 clone、建 venv、写配置、拉起并管理后端进程（推荐） |
| **独立链接（manual）** | 自行部署 Overstats 后端，插件按 API 地址接入（适合已有后端的用户） |

## auto 模式准备

1. 获取网易大神账号的 `role_id` 与 `token`（参考 Overstats 的 `Faststart.md`）。
2. 确保 AstrBot 所在环境已安装 `git` 且在 PATH 中可用。
3. 插件配置面板把「后端接入模式」`deploy_mode` 改为 `auto`。
4. 在「大神账号配置」（`dashen_accounts`）中至少添加一个账号；账号多时可用 `dashen_accounts_bulk` 直接粘贴 JSON 数组。
5. 群内执行 `/ow部署`，插件自动完成：代码获取 → 虚拟环境创建 → 依赖安装 → 配置生成 → 服务启动 → 健康检查。

> auto 模式下后端配置（端口、账号、AI 分析、并发参数等）改动后，需执行 `/ow重启后端` 或 `/ow部署` 重新生成 `config.py` 才生效。

## manual 模式准备

1. 自行部署 [Overstats](https://github.com/AddOneSecondL/Overstats) 后端（本仓库配套的后端在 [`imztm123/Overstats`](https://github.com/imztm123/Overstats)，配置模板与插件面板默认值保持一致）。
2. 确认后端 API 端口可被 AstrBot 所在服务器访问（后端默认 `18081`，manual 面板默认填 `18080`，按实际改）。
3. 需监听全部网卡时，改后端 `config/config.py` 的 `API_HOST = "0.0.0.0"`，并在服务器防火墙放行对应端口。

---

# 四、🚀 安装

```text
仓库地址：https://github.com/imztm123/astrbot_plugin_overstats
```

**方式一（推荐）**：AstrBot 管理面板 → 插件市场 → 通过 GitHub 仓库安装 → 粘贴上面的地址。

**方式二**：直接 clone 到 AstrBot 插件目录：

```bash
cd <AstrBot>/data/plugins
git clone https://github.com/imztm123/astrbot_plugin_overstats.git
```

装完重载插件，进入配置面板按下一节填写。

> 安装后插件目录名 = 包名 `astrbot_plugin_overstats`。注意：**不要与原版同时安装**——两者 `name` 相同，AstrBot 会视为同一插件并互相覆盖；如需切换版本，先卸载另一个。

---

# 五、⚙️ 插件配置

## 5.1 接入模式与基础

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `deploy_mode` | String | `manual` | `manual`=独立链接（自行部署后端），`auto`=一键托管（插件部署并管理后端）。切换后需重载插件 |
| `overstats_api_url` | String | `http://127.0.0.1:18080/api/v2` | Overstats API 地址（manual 使用；auto 忽略，自动用本地托管地址） |
| `direct_message_handling_enabled` | Bool | `true` | 直接消息处理：群聊中真实 @机器人 时识别战网 ID / 单局数字，私聊直接识别 |
| `unmatched_cmd_guide_enabled` | Bool | `false` | 收到未识别指令时返回快速指南（仅用户查询指令，管理/部署类不触发） |
| `full_adaptation_nickname` | String | 空 | 全量适配昵称兜底。@昵称模式下群内未指定昵称且自动探测失败时使用；留空需先真实 @一次机器人以自动获取 |
| `save_image_locally` | Bool | `false` | 关闭时图片直接转 base64 发送（省磁盘 I/O）；开启则先落盘临时文件再发 |

## 5.2 限流、白名单与冷却

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `cmd_rate_limit` | Text(JSON) | `{"enabled":false,"per_user_max":3}` | 指令并发限流（仅访问后端的业务指令计入）。`per_user_max`=单用户并发上限，`0` 不限制；改完热生效 |
| `group_whitelist` | List | 空 | 群白名单：维护模式下豁免拦截、是区吗免二次确认。QQ 官方机器人填**群 openid** |
| `user_whitelist` | List | 空 | 个人白名单：同上。QQ 官方机器人填**个人 openid** |
| `shiqu_cd_map` | Text(JSON) | `{"normal":14400,"whitelist":1800,"admin":0,"match_count":12,"normal_enabled":false}` | 是区吗分级配置：普通/白名单/管理员 CD（秒）、抓取场数、普通用户总开关。12 场约 1.9w 输入 token，30 场约 3.3w |
| `ai_review_limit_enabled` | Bool | `true` | 单局详细「AI 锐评」每日限次开关（管理员与白名单不受限） |
| `ai_review_daily_limit` | Int | `3` | 普通用户每日 AI 锐评次数上限 |
| `ai_review_reset_hour` | Int | `4` | 每日重置时刻（小时，默认凌晨 4 点） |

## 5.3 一键托管（auto 模式生效）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `backend_repo_url` | String | `https://github.com/imztm123/Overstats.git` | 后端仓库地址，用于 `git clone/pull`。**改成原仓库会导致更新时覆盖本地后端改动** |
| `backend_host` | String | `127.0.0.1` | 后端监听地址，需外网访问改 `0.0.0.0` |
| `backend_port` | Int | `18081` | 后端监听端口 |
| `auto_start_on_boot` | Bool | `false` | 插件加载时自动启动后端（首次部署仍需手动 `/ow部署`） |
| `github_proxy_enabled` | Bool | `false` | 开启后首次克隆即走加速；关闭时遇到 RPC/HTTP2/early EOF 也会自动尝试代理重试 |
| `github_proxy_custom` | String | 空 | 自定义加速前缀（如 `https://gh.llkk.cc/`） |
| `github_proxy_primary` / `github_proxy_fallback` | String | `https://gh.llkk.cc/` / `https://gh-proxy.com/` | 主 / 备加速地址，主失效自动切备 |

## 5.4 大神数据与赛季参数

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `dashen_accounts` | TemplateList | 空 | 大神账号（`name` / `role_id` / `token`），auto 模式必填 |
| `dashen_accounts_bulk` | Text | 空 | 批量账号（可直接粘贴 `DASHEN_ACCOUNTS` JSON 数组），与上表合并去重、批量优先 |
| `dashen_account_max_requests_per_second` | Int | **`8`**（上游 5） | 单账号每秒请求上限 |
| `dashen_account_rate_limit_window_seconds` | Float | `1.0` | 限流统计窗口（秒） |
| `dashen_account_failure_cooldown_seconds` | Int | `60` | 账号触发限流 / 失效后的冷却秒数 |
| `dashen_max_concurrent_requests` | Int | **`14`**（上游 2） | 全局并发请求上限。**经验值：≤ 账号数 × 4**，否则易 429 |
| `dashen_dts` / `dashen_server` | Int | `2026` / `1` | 大神接口 `dts` 与服务器编号（国服默认 1） |
| `dashen_current_season` | Int | `24` | 当前赛季编号，新赛季需手动更新 |
| `dashen_history_start_season` | Int | `15` | 历史段位追溯的起始赛季 |
| `ow_hero_leaderboard_cn_season` | Int | `4` | 国服英雄榜赛季编号 |
| `dashen_international_proxy` | String | 空 | 查询外服数据使用的代理，留空直连 |
| `ow_esports_api_key` | String | 空 | PandaScore 赛事 API Key（可选） |
| `ow_guess_asset_root` | String | `ow_guess_assets` | OW guess 外部素材目录（可选） |
| `patch_notes_use_international_proxy` / `patch_notes_international_proxy` | Bool / String | `false` / 空 | 补丁说明抓取是否走代理及代理地址 |

## 5.5 AI 锐评与「是区吗」LLM

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `analysis_base_url` | String | 空 | AI 锐评接口 Base URL（OpenAI 兼容，如 `https://api.deepseek.com/v1`） |
| `analysis_api_key` | String | 空 | AI 锐评 API Key |
| `analysis_model` | String | 空 | AI 锐评模型名 |
| `analysis_proxy` | String | 空 | AI 锐评请求代理 |
| `analysis_persona_prompt` | Text | 内置中立人格 | AI 锐评人设 / 语气（任务指令与 JSON Schema 在代码内维护） |
| `shiqu_llm_api_base` | String | 空 | 是区吗独立 LLM 的 Base URL（与 AI 锐评解耦，可单独换模型 / 限流 / 控预算） |
| `shiqu_llm_api_key` | String | 空 | 是区吗 LLM API Key |
| `shiqu_llm_model` | String | 空 | 是区吗 LLM 模型名 |
| `shiqu_llm_stream` | Bool | `true` | 是否 SSE 流式；网关不支持时关掉走普通 JSON |
| `shiqu_llm_timeout_seconds` | Int | `300` | 单次 LLM 请求超时（判定书 Prompt 较长） |
| `shiqu_llm_retry` | Int | `0` | LLM 失败重试次数，`0`=只调 1 次 |

## 5.6 数据与监控

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `sqlite_db_path` | String | 空 | 后端 `match_stats.sqlite3` 完整路径。填了才启用：同目录的 `shiqu_llm.sqlite3` 用于是区吗 / 开庭调用日志，`request_metrics.sqlite3` 用于面板后端性能 |
| `use_stream_response` | Bool | `true` | 后端是否以流式响应返回 |
| `enable_database_write` | Bool | `true` | 后端是否写入战绩数据库 |

---

# 六、📋 指令大全

群内发送 `/owhelp`（别称 `/ow菜单`、`/ow帮助`、`/OW帮助`、`/help`）查看可视化交互菜单；发送 `/所有指令`（别称 `/别称`）查看指令与别称全表；`/快捷指令`（`快速指南`）看精简速查；`/管理` 打开管理员菜单。

## 6.1 基础与绑定

| 指令 | 别称 | 说明 |
| --- | --- | --- |
| `/大神绑定 [战网ID]` | `/绑定` | 绑定战网账号（`/绑定 Player#12345`）；群内直发规范战网 ID 也会被自动识别绑定 |
| `/所有指令` | `/别称` | 展示全部指令及别称 |
| `/owhelp` | `/ow菜单`、`/ow帮助`、`/OW帮助`、`/help` | 快捷指令菜单 |
| `/快速指南` | `/快捷指令` | 精简速查页 |

## 6.2 数据与战绩

| 指令 | 别称 | 说明 |
| --- | --- | --- |
| `/今日总结` | `/今日`、`/今日数据` | 过去 24 小时对局大数据总结卡片 |
| `/昨日总结` | `/昨日`、`/昨日数据`、`/昨天数据` | 昨日战绩数据卡片 |
| `/周度总结` | `/本周总结`、`/本周数据`、`/本周` | 本周战绩总结（耗时约 30–60 秒） |
| `/大神数据 [战网ID/模式]` | `/详情卡片`、`/战绩查询`、`/数据` | 玩家详情卡片（模式：`快速/quick`、`竞技/competitive`） |
| `/大神对局 [战网ID]` | `/最近对局`、`/战绩`、`/对局` | 最近 20 局对局列表 |
| `/单局详细 [序号] [战网ID]` | `/单局`、`/单局详情` | 单局多图详细战绩；拉过对局后群内直发 `1~20` 同样触发 |
| `/同玩查询 [ID1] [ID2]` | `/开黑胜率` | 两位玩家开黑战绩与胜率分析 |
| `/历史段位` | `/历届段位` | 历史天梯段位追溯 |

## 6.3 AI 趣味判定

| 指令 | 别称 | 说明 |
| --- | --- | --- |
| `/ow是区吗 [战网ID] [局数1~25]` | `/是区吗` | AI 生成「判定书」。自定义局数仅白名单/管理员；白名单免二次确认；分级 CD：管理员 0 / 白名单 30 分钟 / 普通 4 小时（普通用户默认关闭，需 `shiqu_cd_map.normal_enabled` 开启） |
| `/ow是区吗结果` | `/是区吗结果` | 取回上次生成的判定书图片 |
| `/ow开庭 [战网ID]` | `/开庭` | AI 电竞法庭风格「判决书」，与是区吗共用白名单与并发守卫 |
| `/ow开庭结果` | `/开庭结果` | 取回上次开庭判决书 |
| `/owAI检测` | `/AI检测` | 测试是区吗 LLM 接口连通性 |

## 6.4 指数与图表

| 指令 | 别称 | 说明 |
| --- | --- | --- |
| `/快速强度` | `/快速强度指数` | 快速模式强度指数评估 |
| `/竞技强度` | `/竞技强度指数` | 竞技模式强度指数评估 |
| `/快速英雄云图` | `/快速云图` | 指定赛季快速英雄使用率 Treemap |
| `/竞技英雄云图` | `/竞技云图` | 指定赛季竞技英雄使用率 Treemap |

## 6.5 游戏生态与资讯

| 指令 | 说明 |
| --- | --- |
| `/威能 [英雄名]` | 英雄核心威能 / 机制数据图 |
| `/ow英雄 [英雄名]` | 英雄在当前天梯的 Pick 率走势图 |
| `/获取段位分布` | 全服大盘全英雄数据排行与天梯环境分布 |
| `/banpick`（`/全英雄排行`） | 本周天梯英雄选禁用排行 |
| `/mappick` | 最新补丁中当前赛季地图池与轮换出场情况 |
| `/商店`（`/ow商店`） | 今日精选商店在售皮肤 |
| `/皮肤搜索 [关键词]` | 检索包含关键词的上架皮肤卡片 |
| `/ow赛事`（`/赛事`） | 实时职业赛事对阵与赛程 |
| `/ow活动`（`/活动`） | 当前版本限时活动公告卡片 |
| `/ow更新 [latest\|small\|big]`（`/版本更新`） | 外服更新日志卡片 |

## 6.6 天梯排行榜

| 指令 | 别称 | 说明 |
| --- | --- | --- |
| `/省榜 [省份] [位置]` | `/排行` | 大神天梯省榜（位置：`tank` / `dps` / `healer` / `open`） |
| `/绝活榜 [省份] [英雄]` | `/英雄省榜` | 指定地区与英雄的专精绝活榜 |

## 6.7 群组管理

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `/群设置` | 所有人 | 查看本群功能配置状态 |
| `/群设置 提示 开\|关` | 群主/管理员 | 切换「首次提示后不再提示」 |
| `/群设置 追加提示 开\|关` | 群主/管理员 | 切换「追加交互提示」（默认开） |
| `/全量适配开 [昵称]` | 群主/管理员 | 开启本群全量适配 · @昵称模式（`@机器人昵称 指令`），昵称留空自动探测 |
| `/全量适配开完全匹配` | 群主/管理员 | 开启本群全量适配 · 直接匹配模式（直接发指令即可） |
| `/全量适配关` | 群主/管理员 | 关闭本群全量适配 |

> 全量适配需群主在「机器人设置 → 机器人可获取消息范围」中开启「获取群内全部消息」才会真正生效。

## 6.8 违规封禁与维护

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `/维护 [内容]` | AstrBot 管理员 | 开启维护模式：所有数据查询指令返回指定维护内容（基础指令不受影响） |
| `/维护 取消` | AstrBot 管理员 | 关闭维护模式 |
| `/ow违禁封禁 [用户ID] [指令名]` | AstrBot 管理员 | 封禁该用户该指令 12 小时（图片违规被平台拦截时自动触发，也可手动） |
| `/ow违禁解封 [用户ID] [指令名]` | AstrBot 管理员 | 解除封禁 |
| `/管理` | 管理员/群主 | 管理员指令统一入口菜单 |

## 6.9 运维工具（两种模式通用）

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `/ow连接测试` | AstrBot 管理员 | 测试后端连通性，返回 `/healthz` 原始响应 |
| `/ow部署状态` | AstrBot 管理员 | 接入模式、运行状态、监听地址、后端目录、git 版本、进程状态 |

## 6.10 一键部署（仅 auto 模式）

| 指令 | 说明 |
| --- | --- |
| `/ow部署` | 一键部署：代码获取 → venv → 依赖 → 配置生成 → 启动 → 健康检查（首次必执行） |
| `/ow更新后端` | `git pull` → 装依赖 → 重新生成配置 → 重启（**注意 `backend_repo_url` 必须是本仓库，否则会覆盖后端改动**） |
| `/ow停止后端` | 停止后端进程 |
| `/ow重启后端` | 重启后端（改配置后需执行） |
| `/ow部署日志` | 最近 50 行实时输出（内存缓冲） |
| `/ow后端日志` | 持久化日志最新 30 条（跨重启保留，上限 300 条） |

## 6.11 后端卸载（仅 auto 模式）

> 卸载严格按「先停进程 → 再删文件」执行，避免 SQLite 因占用损坏。**操作不可逆，数据库删除后无法恢复。**
> 与 AstrBot 面板的「卸载插件（delete_data=True）」不同：面板卸载会删掉整个插件数据目录；这里的卸载只针对后端代码 / venv / 数据库。

| 指令 | 说明 |
| --- | --- |
| `/ow卸载后端` | 预览影响（目录、磁盘占用、运行中进程、将删除的数据库） |
| `/ow卸载后端执行确认` | 删除后端代码（含数据库）+ 虚拟环境 |
| `/ow卸载后端执行仅代码` | 仅删代码目录（含数据库），保留 venv |
| `/ow卸载后端执行仅venv` | 仅删虚拟环境，保留代码 |
| `/ow卸载后端执行强制` | 进程无法停止时强制杀进程再删除 |

推荐顺序：`/ow卸载后端` 预览 → `/ow卸载后端执行确认` → 再到 AstrBot 面板卸载插件。

---

# 七、📊 Web 监控面板

插件内置监控页（基于 AstrBot Pages），在 AstrBot WebUI 的插件详情页打开：

- **运行总览**：运行时长、指令调用量、成功率、API 请求数
- **指令分析**：按分类筛选使用频率与成功率，支持搜索与折叠；失败原因面板带 `error_code`
- **调用趋势**：SVG 折线图，24 小时 / 三天 / 本周 / 本月 / 全部；点击节点按日期下钻时段分布
- **后端性能**：端点延迟排行、慢端点标记、队列等待时间
- **上游 API 统计**：上游接口调用量与成功率
- **部署与限流**：部署状态详情、指令限流触发统计
- **AI 调用日志**：是区吗 / 开庭的调用明细（读后端 `shiqu_llm.sqlite3`，需配 `sqlite_db_path`）
- **错误日志**：ERROR / WARNING 分级筛选，SSE 实时推送

`request_metrics.sqlite3` 路径自动探测：auto 模式从 `overstats_backend/src/db/` 读取，manual 模式从 `sqlite_db_path` 同级目录读取。

---

# 八、📁 仓库结构

本仓库只放**外层插件**，后端是独立仓库、由插件在 auto 模式下 clone。

```
astrbot_plugin_overstats/          ← 本仓库根目录 = AstrBot 插件本体（安装后目录名为包名）
├── main.py                        # 指令注册与事件分发（单体）
├── metadata.yaml                  # name/display_name/version/author/repo
├── _conf_schema.json              # 配置面板 schema
├── logo.png                       # 市场 / WebUI 图标（1:1，256×256）
├── LICENSE / CHANGELOG.md / README.md
├── scripts/
│   └── fix_shiqu_config.py        # 「是区吗」配置一键修复（见 一.1）
├── deploy/
│   ├── ops/                       # 部署运维：git / venv / 进程 / 配置写入 / 限流
│   └── ow/                        # OW 业务：是区吗 / 开庭 / 监控 / SQLite 读取
├── features/                      # 各指令业务入口
└── pages/dashboard/               # 内置监控面板前端
```

后端 `Overstats` 被 clone 到 AstrBot 数据目录：

```
<AstrBot>/data/plugin_data/astrbot_plugin_overstats/
├── overstats_backend/             # ← clone 自 https://github.com/imztm123/Overstats.git
│   ├── config/config.example.py   # 配置模板（复制成 config.py 后填写）
│   ├── config/shiqu_config.py     # 是区吗 LLM 配置（插件部署时自动覆盖）
│   ├── src/
│   └── run.py
├── overstats_backend_venv/
└── overstats_backend.log
```

`config/config.py`、`*.db`、`*.sqlite3`、`*.bak`、`__pycache__` 等均已进 `.gitignore`。

---

# 九、🔧 排障

| 症状 | 处理 |
| --- | --- |
| `是区吗` 不出图 / 接口 500，日志含 `no attribute 'json_mode'` | 跑 `scripts/fix_shiqu_config.py`，再 `/ow重启后端` |
| `是区吗` 提示「暂无可用的最近结果」 | 正常流程：5 分钟内再发一次 `是区吗` 即开启新查询 |
| `是区吗` 提示「功能暂未对普通用户开放」 | 在 `shiqu_cd_map` 里设 `"normal_enabled": true` |
| 图片消息里出现 `@` 乱码 | 确认跑的是本仓库版本（出站链已无 `Comp.At`） |
| 查询很慢 / 报 429 | `dashen_max_concurrent_requests` 过大。按「≤ 账号数 × 4」调小，或增加账号 |
| 插件更新后改动没了 | 检查安装来源是否为 `imztm123/astrbot_plugin_overstats` |
| `/ow更新后端` 后后端被重置 | 检查 `backend_repo_url` 是否指向原仓库 |
| 全量适配不生效 | 群主需在「机器人设置 → 机器人可获取消息范围」开启「获取群内全部消息」 |
| 面板监控全是空 | 配 `sqlite_db_path` 指向后端 `match_stats.sqlite3` |

---

# 十、🤝 鸣谢与许可

- 插件与后端代码版权归原作者所有，遵循原仓库 [LICENSE](./LICENSE)。
- 感谢 [@Zzzzzzouhang](https://github.com/Zzzzzzouhang) 的 [`astrbot_plugin_overstats`](https://github.com/Zzzzzzouhang/astrbot_plugin_overstats) 与 [`Overstats`](https://github.com/Zzzzzzouhang/Overstats)。
- 感谢 [@AddOneSecondL](https://github.com/AddOneSecondL) 的 [`Overstats`](https://github.com/AddOneSecondL/Overstats) 提供的后端服务支持。
- 感谢 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供的机器人开发生态。
- 数据来源：网易大神开放接口；外服数据参考 [TeKrop/overfast-api](https://github.com/TeKrop/overfast-api)，游戏资源参考 [overtools/OWLib](https://github.com/overtools/OWLib)。

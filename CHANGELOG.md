# Changelog

## v2.7.7（社区维护版）

### 🐛 修复
- **「是区吗」无法使用（判定书永远出不来）**：根因是两处代码错位——插件 `deploy/ops/config_writer.py` 的 `_SHIQU_CONFIG_TEMPLATE` 生成的 `ShiquLLMConfig` **没有 `json_mode` 字段**，而后端 `src/modules/dashen_shiqu/service.py:1159` 直接读 `if cfg.json_mode:` 来决定是否加 `response_format={"type":"json_object"}`。结果每次「是区吗」调用 `_call_llm()` 都抛 `AttributeError: 'ShiquLLMConfig' object has no attribute 'json_mode'`，`/dashen-shiqu/image` 返回 500。「开庭」走自带 `json_schema`（`strict: False`）不读该字段，所以只有「是区吗」单点失效。
  - 模板补 `strict: bool = True` / `json_mode: bool = True` 字段，以及 `strict=_parse_bool(_get("SHIQU_LLM_STRICT", "true"), True)` / `json_mode=_parse_bool(_get("SHIQU_LLM_JSON_MODE", "true"), True)` 两行解析；
  - 新增 `scripts/fix_shiqu_config.py`：`ast` 静态读取后端现有 `base_url / api_key / model` → 任一为空即中止（绝不用空值覆盖把 Key 抹掉）→ `importlib` 加载插件 `config_writer` → `write_shiqu_config()` 按新模板重生成（内含 `py_compile` 语法校验）→ 先备份 `shiqu_config.py.bak.<时间戳>`、失败自动回滚 → 提示重启后端。脚本只打印「正常/缺失」，不打印密钥内容。
- **AstrBot v4.29.0「@」乱码**：出站消息链不再拼 `Comp.At(qq=发送者)`，改为 `Plain` 文本 + 图片。`main.py` 的 `_build_image_chain()` 与 `_send_multiple_images_result()` 两处：前者由 `[Comp.At(...), Comp.Plain(...), image]` 改为 `[Comp.Plain(f'{fallback_text}\n'), image]` / `[image]`；后者起始项由 `[Comp.At(...), Comp.Plain('\n')]` 改为空链 `[]`。入站 `@` 判定仍由 `_is_real_at_bot()` 按 `self_id` 比对并排除 QQ 官方占位 `at`，触发不受影响。

### 🔧 优化
- **并发参数：默认保持上游不改**。账号池轮询是 Overstats 上游自带机制（`DASHEN_ACCOUNTS` 配多账号即在池内分摊），本版未改动该逻辑，也未改动默认并发值（`dashen_account_max_requests_per_second`=5、`dashen_max_concurrent_requests`=2），避免单账号用户命中上游「账号数 × 4」的 429 限制。多账号环境下的调优建议（2 账号可用 8/14）写在 README「一、3」与 `config.example.py` 注释里。
- **是区吗 LLM 配置下发**：模板增加 `strict` / `json_mode` 开关（默认 true，强制 JSON 输出），降低判定书解析失败概率。
- **诊断日志**：`config_writer.py` 与 `deploy/ops/manager.py` 增加 `[Shiqu诊断]` 输出，打印 `shiqu_llm_api_base` / `api_key` / `model` 是否已配置（仅布尔值）。
- **ID 解析失败文案**：统一为「未查询到id或者id错误，id严格区分大小写（受大神接口维护影响，部分id不可用）」。

### 🔒 仓库整理
- **插件市场身份**：插件市场 ID 为 `owner/name`（`imztm123/astrbot_plugin_overstats`），与原作者的 `Zzzzzzouhang/astrbot_plugin_overstats` 天然隔离，因此**包名保持 `astrbot_plugin_overstats` 不变**；`display_name` 改为「overstats（ZTM 优化版）」用于在市场列表中区分。
- `metadata.yaml`：`version` → `v2.7.7`，`author` → `Zzzzzzouhang 社区维护`，`repo` → 本仓库；`logo.png` 换为自制图标。
- `_conf_schema.json` 的 `backend_repo_url` 默认值、`deploy/ops/manager.py` 的兜底地址，全部改为本仓库地址，避免插件更新时拉回原版覆盖改动。
- 后端独立为 `Overstats` 仓库：`config/config.py` 改为 `config/config.example.py` 模板，真实 token / API Key 不入库；补齐上游 `res/` 素材与 `bg.png`。
## v2.7.5 (2026-09-03)

### 🐛 修复
- **是区吗/开庭接入维护模式管控**：`ow开庭`/`ow是区吗`/`ow开庭结果`/`ow是区吗结果` 此前未做维护模式检查，维护期间仍可执行并请求后端。现四者均在入口调用 `_check_maintenance` 拦截，维护期间普通用户收到维护文案即终止，AstrBot 管理员与白名单用户豁免（与其余数据查询指令一致）
  - `是区吗结果`/`开庭结果` 一并不再漏网：`last_result` 会向后端发起取图请求（`use_db=true`，timeout 900s），属真实上游调用，维护期间必须熔断

### 🔧 优化
- **ID 解析失败提示补充维护说明**：统一文案 `id_resolve_error_hint`（经 `_id_resolve_err` 一处改动覆盖 11 处调用：今日/昨日/周度总结、大神数据、大神对局、历史段位、同玩查询、快速/竞技强度、快速/竞技英雄云图）追加"受大神接口维护影响，部分id不可用"，降低大神接口波动时用户误判为自身 ID 输错的概率
- **是区吗/开庭 ID 解析失败不再透传英文报错**：后端返回 `Could not resolve customerToken` 时（`deploy/ow/shiqu.py`、`deploy/ow/court.py` 共 3 处）改用插件统一 ID 解析失败文案；其余错误类型仍原样透传后端信息，不影响问题排查
- **注册装饰器版本同步**：`main.py` 的 `@register` 版本号由长期漏同步的 `2.6.5` 更新至 `2.7.5`（该参数实际已由 `metadata.yaml` 取代，不影响运行）

## v2.7.3 (2026-08-15)

### 🐛 修复
- **指令并发限流误触发修复（`⏳ 同时执行的指令过多`）**：
  - 槽位生命周期上移至 `main.py` handler 层统一管理（`_run_with_cmd_slot`），try/finally 保证指令被中断/取消时槽位必定释放，杜绝此前"指令被中断后槽位泄漏导致全员误触发直至每日重置"的问题
  - `_release_cmd_slot` 与获取共用同一把异步锁，消除计数竞态
  - 删除会吞掉异常的旧 `_rate_limit_slot` context manager
  - 仅"需要访问后端"的业务指令计入限流（28 条）；绑定/解绑、帮助/别称、快速指南、AI 检测、管理/部署/测试类指令不再占用槽位
  - 配置严格布尔解析：`"false"/"0"/"off"/空` 一律视为关闭，修复 `bool("false")==True` 导致限流被意外开启的陷阱；配置内容变化自动热生效，无需重启
  - 限流拒绝时写入监控埋点（`record_rate_limit`），监控面板"限流统计"可查触发记录

### ✨ 调整
- **限流改为纯用户粒度**：删除全局并发上限（`max_concurrent` 废弃），同一用户同时最多 `per_user_max` 条（默认 3，`0`=不限制），不同用户互不影响；监控面板显示"单用户并发上限"
- **后端瞬时错误自动重试**：`_fetch_image` 请求后端遇到 5xx/internal_error（如 ConnectTimeout、超时类瞬时错误）时 5 秒后自动重试一次（共 2 次尝试），软业务错误（bnet_not_found/summary_empty）与上游限流（429）不重试；单局详细同样 5 秒后重试一次，降低"Internal server error. See details."直接报给用户的概率
  - **是区吗/开庭不重试**：这两个 AI 生成类功能调用 `_fetch_image` 时传 `retry=False`，失败直接提示用户重发，避免重复高成本生成
- **指令限流槽位 300 秒自动释放**：槽位记录获取时间戳，超过 300 秒未释放（指令卡死/中断）时，该用户下次发起指令前自动清理过期槽位恢复额度；与每日 4 点清空双保险
- **限流逻辑内聚重构**：提取独立类 `UserConcurrencyLimiter`（`deploy/ops/concurrency_limiter.py`），单结构 `dict[str, deque[float]]` 取代原"计数+时间戳列表"双结构；时间戳改用 `time.monotonic()`；FIFO 队首清理 O(1)；`main.py` 只保留 `_cmd_limiter` 与薄委托方法；提供 `clear()/expire_all()/stats()` 接口；拒绝文案统一为"你已有指令正在执行"，拒绝埋点增加异常防护

### 🐛 修复
- **监控指令成功率统计修正**：`ow是区吗`/`ow开庭`/`ow是区吗结果`/`ow开庭结果` 此前在指令入口无条件记录 `success=True`（成功率恒 100%）。现改为在真实执行结果点记录——后端取图成功/发送完成记成功，查询失败记失败并携带 `error_code`（软错误与上游限流仍计入失败原因面板）；`owAI检测` 按连通性结果记录；`绑定` 成功时补记成功调用

## v2.7.2 (2026-07-07)

### 🔧 优化
- **指令集中**：所有 `@filter.command` 指令注册与事件监听器统一集中在 `main.py` 单体（`OverstatsPlugin(Star)`），撤回此前的 mixin 拆分。辅助类保留在 `deploy/ow/`（OW 业务）与 `deploy/ops/`（部署运维）子包。功能与 v2.7.1 完全一致。

## v2.7.1 (2026-07-06)

### 🔧 优化
- **提示词结构重构**：是区吗与开庭提示词统一为 `[ROLE]/[CONTEXT]/[OBJECTIVE]/[CONSTRAINTS]/[WORKFLOW]/[OUTPUT FORMAT]/[INPUT DATA]` 标准化分节，角色设定合并语气/修辞要求，新增修辞比喻库（5 大分类）与英雄名称参考
- **数据白名单同步**：开庭模块同步是区吗的数据白名单（通用数据 + 按英雄分类的特色数据），`_hero_detail_text` 增加 `_stat_allowed_for_hero` + `should_skip_prompt_stat` 双重过滤
- **开庭提示词精简**：移除 `_EMOJI_MAP_TEXT`（完整数据已包含英雄位置信息），减少冗余 token

## v2.7.0 (2026-07-05)

### ✨ 新增
- **Web 监控面板**：新增基于 AstrBot Pages 的插件运行状态监控页面，涵盖：
  - 运行总览：运行时长、指令调用总量、成功率、API 请求数
  - 指令分析：按分类筛选（基础绑定/数据查询/总结/图表排行/游戏资讯/AI开庭/管理部署）、支持搜索、可折叠指令列表、成功率条形图
  - 调用趋势：24 小时/三天/本周/本月/全部时间范围的 SVG 折线图，支持点击节点按日期筛选时段分布
  - 时段分布：24 小时柱状图，支持按日期筛选
  - 后端 API 性能：端点延迟条形图、慢端点检测、队列等待统计
  - 上游 API 统计：上游接口调用量与成功率排行
  - 部署与限流：部署状态详情、指令/LLM 限流触发统计
  - 错误日志：ERROR/WARNING 分级筛选、SSE 实时推送
- **指令统计持久化**：新增 SQLite 统计采集器（`MonitorCollector`），记录每条指令的调用、成功率、耗时，支持 30 日自动清理
- **后端性能指标读取**：`BackendMetricsReader` 自动探测 `request_metrics.sqlite3` 路径（auto/manual 模式），查询端点延迟与上游调用统计
- **日志自动捕获**：`MonitorLogHandler` 挂载到 astrbot logger，零侵入自动捕获 ERROR 日志

### 🔧 优化
- 业务指令统一埋点：`_finalize_business_status_prompt` 增加 `cmd_name` 参数，覆盖 25+ 条业务指令
- `_fetch_image` 增加 API 调用耗时与成功率记录
- `_try_llm_rate_limit` 增加限流触发记录

## v2.6.5 (2026-07-01)

### ✨ 新增
- **是区吗支持自定义局数**：`/ow是区吗 Player#12345 20` 可指定有效对局数（1~25），不填则使用配置默认值。参数智能拆分，战网ID与局数可任意顺序
- **白名单二次确认优化**：白名单用户/群跳过 5 分钟二次确认，直接开启新查询（冷却 CD 仍保留）
- **发图失败兜底**：是区吗图片发送失败时，下次触发自动走确认流程提醒用户查看上次缓存图（白名单也不例外），确保用户不遗漏已生成的结果

### 🔧 优化
- `/ow是区吗` 命令支持 arg1/arg2/arg3 智能参数解析，参考单局详细模式

## v2.6.2 (2026-06-30)

### ✨ 新增
- **违规内容自动封禁**：所有图片查询指令发送时若被平台接口判定违规，自动封禁该用户对应指令 12h，提示联系管理员
- 新增 `/ow违禁封禁` / `/ow违禁解封` 管理员指令，支持按「用户ID+指令名」粒度手动管理封禁
- 封禁数据纯 JSON 文件存储（`violation_bans.json`），零 KV 依赖，过期自动清除

### 🔧 优化
- `_send_image_result` 重构为 async generator，25 处单图调用全部覆盖违规捕获

## v2.4.1 (2026-06-29)

### ✨ 新增
- **无空格指令识别**：支持 `/{命令}{战网ID}` 格式（如 `/今日总结Player#12345`），自动拆分并派发

### 🔧 优化
- **是区吗扩充 6v6 模式**：抓取范围从 `SportPreset/LeisurePreset` 扩展到 `Sport6v6/Leisure6v6`
- **队友英雄识别修复**：`_expand_player_segments` 改为优先使用 `_heroList.heroId`，避免从 statMap 推断错误
- **队友数据查询重构**：改用 `customer_token + match_id` 查同一局（对齐后端 `_build_all_player_details`），替代不可靠的 index 匹配
- **战网ID错误提示收敛**：`_bnet_err` 统一 10 处重复的错误文案

## v2.3.2 (2026-06-27)

### ✨ 新增
- **「ow是区吗」功能全量开放**：基于最近对局数据的 AI 阴阳怪气判定，支持分级 CD（管理员 0 / 白名单 30min / 普通 4h）
- **「ow是区吗结果」**：快速查看上次生成的判定书图片
- 新增 `shiqu_cd_map` 统一配置项（JSON）：`normal` / `whitelist` / `admin` CD 时长 + `match_count` 抓取场数 + `normal_enabled` 普通用户开关（默认关闭）
- 数据抓取优先 SportPreset / LeisurePreset 预设职责对局，不足时自动补充
- `owhelp` / README 新增是区吗功能入口

### 🔧 优化
- 是区吗判定分数区间重调：≥83 职业吗 → 82-75 暴力炸 → 74-68 化蛹成蝶 → 67-60 不是你区 → 59-52 可能是区 → 51-43 哦灭跌多 → <43 你个大区
- 移除提示词中所有 `(xxx样本)` 后缀
- 补全所有 `@filter.command` 方法的 docstring 描述
- `_ensure_full_adapt_map` 添加注释禁止高成本 AI 指令入表

## v2.3.1
- 历史版本（略）

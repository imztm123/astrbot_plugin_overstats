# astrbot_plugin_overstats（ZTM 自用优化版 v2.7.6）

> ## ⚠️ 重要声明
>
> **本项目是基于原版 [`astrbot_plugin_overstats`](https://github.com/Zzzzzzouhang/astrbot_plugin_overstats) 二次修改的自用优化版**，并非原创项目。
>
> - 原作者：**[@Zzzzzzouhang](https://github.com/Zzzzzzouhang)**（插件本体）
> - 后端服务：[`Zzzzzzouhang/Overstats`](https://github.com/Zzzzzzouhang/Overstats) / [`AddOneSecondL/Overstats`](https://github.com/AddOneSecondL/Overstats)
> - 二次修改：**ZTM**（`imztm123`），本仓库 `imztm123/astrbot_plugin_overstats`
> - 版本号：`v2.7.6`（在上游 v2.7.5 基础上修改；上游另有同名 v2.7.6，两者互不兼容，请以仓库地址区分）
> - 版权与许可沿用原仓库 [LICENSE](./LICENSE)
>
> 本仓库只针对自己实际遇到的问题做最小改动，不代表上游设计意图，也不保证跟进上游后续版本。
> 需要完整功能与长期维护请优先使用原仓库；若本仓库的改动对你有帮助，欢迎参考，但请把上游一并 Star 致谢。

一款为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 打造的守望先锋数据查询插件，作为桥梁连接由网易大神接口驱动的 [Overstats](https://github.com/AddOneSecondL/Overstats) 后端服务，在群聊中查询守望先锋战绩、竞技强度并渲染成图片。

---

## 本版核心改动

相对上游 `v2.7.5`，本版只有三条改动，全部围绕「指令能用、图片能发、查询更快」。

### 1) 修复「是区吗」无法使用（判定书始终出不来）

**现象**：`是区吗` 有响应、进度提示正常，但判定书图片永远发不出来，日志里能看到接口 500。

**根因**（两处代码错位叠加）：

| 位置 | 问题 |
| --- | --- |
| 插件 `deploy/ops/config_writer.py` 的 `_SHIQU_CONFIG_TEMPLATE` | 生成的 `ShiquLLMConfig` 只有 `base_url / api_key / model / stream / timeout_seconds / retry`，**没有 `json_mode` 字段** |
| 后端 `src/modules/dashen_shiqu/service.py:1159` | 直接读 `if cfg.json_mode:` 来加 `response_format={"type":"json_object"}` |

于是每次「是区吗」进入 `_call_llm()` 就抛：

```
AttributeError: 'ShiquLLMConfig' object has no attribute 'json_mode'
```

`/dashen-shiqu/image` 返回 500，图片自然拿不到。
「开庭」走的是自带 `json_schema`（`strict: False`），不读 `json_mode`，所以**只有「是区吗」单点失效**——这也解释了为什么其它指令都正常。

**修复**：
1. 模板补上两个字段（默认 `true`）：

```python
    strict: bool = True
    json_mode: bool = True
        ...
        strict=_parse_bool(_get("SHIQU_LLM_STRICT", "true"), True),
        json_mode=_parse_bool(_get("SHIQU_LLM_JSON_MODE", "true"), True),
```

2. 用插件模板**重新生成后端 `config/shiqu_config.py`**（保留原有的 base_url / api_key / model，绝不用空值覆盖）：

```bash
python3 scripts/fix_shiqu_config.py \
  --plugin  /AstrBot/data/plugins/astrbot_plugin_overstats \
  --backend /AstrBot/data/plugin_data/astrbot_plugin_overstats/overstats_backend
```

脚本流程：`ast` 静态读取现有配置 → 三项任一为空即中止 → `importlib` 加载插件 `config_writer.py` → 备份为 `shiqu_config.py.bak.<时间戳>` → `write_shiqu_config()` 重写（内部还会 `py_compile` 校验语法）→ 失败自动回滚 → 提示重启后端。
只输出「正常 / 缺失」，不打印密钥内容。修复后必须 `/ow重启后端` 才生效。

> 附带把 `config_writer.py` 与 `deploy/ops/manager.py` 各加了一段 `[Shiqu诊断]` 输出（只打印布尔值），方便下次一眼看出 `shiqu_llm_api_base / api_key / model` 有没有下发成功。

### 2) 适配 AstrBot v4.29.0：修复更新后消息里「@」变成乱码

**现象**：升级 AstrBot v4.29.0 后，机器人发出的图片消息里出现 `@` 加一串乱码 / 占位符，有时整条消息直接发送失败。

**根因**：v4.29.0 调整了消息组件与 QQ 官方（`qq_official` / `qq_official_webhook`）适配层的序列化方式，`Comp.At` 不再稳定渲染成昵称文本，而是被降级成内部 ID 占位；插件把 `At` 拼进出站消息链时，这段内部标识就直接暴露给了用户。

**修复**：出站消息链不再写 `At` 组件，改为纯文本 + 图片：

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

入站方向不受影响：「是否真实 @ 了机器人」仍由 `_is_real_at_bot()` 按 `event.get_self_id()` 比对 `Comp.At.qq`，并显式排除 QQ 官方占位 `self_id`（`qq_official` / `unknown_selfid`），不依赖文本里的 `@`。

### 3) 优化多账号轮询与请求并发，提升跨海战绩 / 竞技强度响应速度

改动全部在后端仓库 `Overstats`：

| 文件 | 上游 → 本版 |
| --- | --- |
| `config/config.py` | `DASHEN_ACCOUNTS` 1 个 → **2 个账号轮询** |
| `config/config.py` | `DASHEN_ACCOUNT_MAX_REQUESTS_PER_SECOND` `5` → **`8`** |
| `config/config.py` | `DASHEN_MAX_CONCURRENT_REQUESTS` `2` → **`14`** |
| `config/config.py` | `DASHEN_MAX_ACCEPTED_REQUESTS` `len*4`（4 → **8**，随账号数自动放大，减少 429） |
| `src/client/apiclient.py:194` | `DASHEN_MAX_CONCURRENT_REQUESTS` 读取兜底 `2` → **`12`** |
| `src/modules/dashen_quick_strength/engine.py:30` | `DEFAULT_MATCH_CONCURRENCY` `4` → **`12`** |
| `src/modules/dashen_competitive_strength/engine.py:23` | `DEFAULT_MATCH_CONCURRENCY` `4` → **`12`** |

后三处是「快速强度 / 竞技强度一次拉多局」的真实并行度，也是跨海查询变快的主因。
插件侧限流结构保持上游不变（单用户并发 `3`、槽位 300s 自动释放、仅访问后端的业务指令计入限流），所以后端提速不会放大群内刷屏压力。

---

## 仓库结构

本仓库只放**外层插件**；后端是独立仓库，由插件在 auto 模式下自动 clone。

```
astrbot_plugin_overstats/          ← 本仓库根目录 = AstrBot 插件本体
├── main.py                        # 指令注册与事件分发（单体）
├── metadata.yaml                  # 插件元信息（version/author/repo 已改）
├── _conf_schema.json              # 配置面板 schema（backend_repo_url 已指向本仓库）
├── logo.png                       # 市场 / WebUI 图标（安燃头像）
├── LICENSE / CHANGELOG.md / README.md
├── scripts/
│   └── fix_shiqu_config.py        # 「是区吗」配置一键修复（见上文改动 1）
├── deploy/
│   ├── ops/                       # 部署运维：git / venv / 进程 / 配置写入 / 限流
│   │   ├── manager.py             # auto 模式后端生命周期管理
│   │   ├── config_writer.py       # 把插件配置渲染成后端 config/*.py
│   │   ├── git_ops.py             # clone / pull / 代理重试
│   │   └── concurrency_limiter.py # 单用户指令并发限制器
│   └── ow/                        # OW 业务：是区吗 / 开庭 / 监控 / SQLite 读取
├── features/                      # 各指令业务入口（players / charts / summary / court ...）
└── pages/dashboard/               # 插件内置 WebUI 面板
```

后端 `Overstats` 单独一个仓库，被 clone 到 AstrBot 数据目录：

```
<AstrBot>/data/plugin_data/astrbot_plugin_overstats/
├── overstats_backend/             # ← clone 自 https://github.com/imztm123/Overstats.git
│   ├── config/
│   │   ├── config.example.py      # 配置模板（复制成 config.py 后填写）
│   │   └── shiqu_config.py        # 是区吗 LLM 配置（插件部署时自动覆盖）
│   ├── src/
│   └── run.py
├── overstats_backend_venv/
└── overstats_backend.log
```

`config/config.py`、`*.db`、`*.bak`、`__pycache__` 等均已进 `.gitignore`。

---

## 安装

1. AstrBot → 插件管理 → 从链接安装：
   ```
   https://github.com/imztm123/astrbot_plugin_overstats
   ```
2. 重载插件，在配置面板设置：
   - `deploy_mode`：`auto`（插件托管后端）或 `manual`（自建后端填地址）
   - `backend_repo_url`：默认已是 `https://github.com/imztm123/Overstats.git`，**保持不动**
   - 大神账号 token、`shiqu_llm_api_base` / `shiqu_llm_api_key` / `shiqu_llm_model` 按实际情况填
3. `auto` 模式首次执行 `/ow部署`（自动 clone 后端、建 venv、写配置、拉起进程）。
4. 群内发送 `owhelp` 查看全部指令。

### 防止被原版覆盖（关键）

插件重载 / 更新是否回拉原版，取决于三处地址，本仓库已全部改到自己仓库：

| 文件 | 作用 |
| --- | --- |
| `metadata.yaml` → `repo` | AstrBot 判断插件来源，`/plugin update` 与面板更新走这里 |
| `_conf_schema.json` → `backend_repo_url` 默认值 | auto 模式首次 clone 的后端仓库 |
| `deploy/ops/manager.py` → `repo_url()` 兜底值 | 配置项缺失时的回退地址 |

若手动把 `backend_repo_url` 填回原仓库，`/ow更新后端`（内部 `git pull --ff-only`，失败时 `git fetch + reset --hard`）会把后端重置回上游，本地改动（已跟踪文件）丢失。

---

## 排障

| 症状 | 处理 |
| --- | --- |
| `是区吗` 不出图 / 接口 500，日志含 `no attribute 'json_mode'` | 跑 `scripts/fix_shiqu_config.py`，再 `/ow重启后端` |
| `是区吗` 提示「暂无可用的最近结果」 | 正常流程：5 分钟内再发一次 `是区吗` 即开启新查询 |
| 图片消息里出现 `@` 乱码 | 确认跑的是本仓库版本（出站链已无 `At`）；老版本重装/覆盖即可 |
| 插件更新后改动没了 | 检查安装来源是否为 `imztm123/astrbot_plugin_overstats` |
| `/ow更新后端` 后后端被重置 | 检查 `backend_repo_url` 是否指向原仓库 |

---

## 许可与致谢

- 插件与后端代码版权归原作者所有，遵循原仓库 [LICENSE](./LICENSE)。
- 感谢 [@Zzzzzzouhang](https://github.com/Zzzzzzouhang) 的 [`astrbot_plugin_overstats`](https://github.com/Zzzzzzouhang/astrbot_plugin_overstats) 与 [`Overstats`](https://github.com/Zzzzzzouhang/Overstats)。
- 感谢 [@AddOneSecondL](https://github.com/AddOneSecondL) 的 [`Overstats`](https://github.com/AddOneSecondL/Overstats) 提供的后端服务支持。
- 感谢 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供的机器人开发生态。
- 数据来源：网易大神开放接口；外服数据参考 [TeKrop/overfast-api](https://github.com/TeKrop/overfast-api)，游戏资源参考 [overtools/OWLib](https://github.com/overtools/OWLib)。

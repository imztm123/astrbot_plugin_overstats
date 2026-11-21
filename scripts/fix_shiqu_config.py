#!/usr/bin/env python3
"""用插件模板重新生成后端 config/shiqu_config.py —— 修「是区吗」不可用。

病因
----
上游 v2.7.5 的 deploy/ops/config_writer.py 模板生成的 ShiquLLMConfig 只有
base_url / api_key / model / stream / timeout_seconds / retry，
**没有 json_mode 字段**；而 new 版后端 src/modules/dashen_shiqu/service.py 里是：

    if cfg.json_mode:
        payload["response_format"] = {"type": "json_object"}

两者叠加 → 每次「是区吗」调用 _call_llm 直接抛
    AttributeError: 'ShiquLLMConfig' object has no attribute 'json_mode'
→ /dashen-shiqu/image 返回 500 → 判定书图片出不来。
「开庭」用的是自带 json_schema（strict=False），不读 json_mode，所以只有
「是区吗」单点失效 —— 与本机现象一致。

修法
----
1. 用 ast 从后端现有 shiqu_config.py 里读出 base_url / api_key / model；
2. 三者任一为空就中止（绝不拿空值覆盖，否则等于把 Key 抹掉）；
3. 用插件的 config_writer.write_shiqu_config() 按新模板重写该文件
   （模板已含 strict / json_mode，默认 true）；
4. 先备份成 shiqu_config.py.bak.<时间戳>，写失败自动回滚；
5. 打印检查结果（只输出「正常/缺失」，不输出密钥内容），提示重启后端。

用法
----
    python3 fix_shiqu_config.py
    python3 fix_shiqu_config.py \
        --plugin  /AstrBot/data/plugins/astrbot_plugin_overstats_ztm \
        --backend /AstrBot/data/plugin_data/astrbot_plugin_overstats/overstats_backend

改完必须重启后端才生效：群内 /ow重启后端，或 systemctl restart 你的服务。
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import shutil
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_PLUGIN = "/AstrBot/data/plugins/astrbot_plugin_overstats"
# AstrBot 的实际数据目录名随版本/安装方式不同：常见是
#   data/plugin_data/astrbot_plugin_overstats/overstats_backend
# 老版本/文档里写作
#   data/plugin_data/overstats_full/overstats_backend
# 不给 --backend 时按候选列表自动探测。
DEFAULT_BACKEND_CANDIDATES = (
    "/AstrBot/data/plugin_data/astrbot_plugin_overstats/overstats_backend",
    "/AstrBot/data/plugin_data/astrbot_plugin_overstats_ztm/overstats_backend",
    "/AstrBot/data/plugin_data/overstats_full/overstats_backend",
)
CLASS_NAME = "ShiquLLMConfig"
REQUIRED = ("base_url", "api_key", "model")


def detect_backend() -> str:
    for candidate in DEFAULT_BACKEND_CANDIDATES:
        if (Path(candidate) / "config" / "shiqu_config.py").exists():
            return candidate
    return DEFAULT_BACKEND_CANDIDATES[0]


def read_existing_values(target: Path) -> dict:
    """只用 ast 静态读取，不 import（避免副作用、不打印内容）。"""
    tree = ast.parse(target.read_text(encoding="utf-8"))
    values: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == CLASS_NAME:
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    try:
                        values[item.target.id] = ast.literal_eval(item.value)
                    except Exception:
                        values[item.target.id] = ""
    return values


def load_writer(plugin_dir: Path):
    writer = plugin_dir / "deploy" / "ops" / "config_writer.py"
    if not writer.exists():
        raise SystemExit(f"失败：找不到插件配置生成器 {writer}")
    spec = importlib.util.spec_from_file_location("shiqu_config_writer", writer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # 只依赖标准库，可安全独立加载
    return module


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", default=DEFAULT_PLUGIN)
    ap.add_argument("--backend", default=detect_backend())
    args = ap.parse_args()

    backend = Path(args.backend)
    target = backend / "config" / "shiqu_config.py"

    print("=== Shiqu 一键修复检查 ===")
    if not target.exists():
        raise SystemExit(f"失败：后端配置不存在 {target}")

    values = read_existing_values(target)
    missing = [k for k in REQUIRED if not values.get(k)]
    if missing:
        raise SystemExit(
            "无法自动修复：后端配置缺少 " + ", ".join(missing) +
            "。请先在 AstrBot 插件面板填写 shiqu_llm_*，"
            "或直接带值跑 /ow部署 重新下发（不能用空值覆盖，会把 Key 抹掉）。"
        )

    module = load_writer(Path(args.plugin))
    config = {
        "shiqu_llm_api_base": values["base_url"],
        "shiqu_llm_api_key": values["api_key"],
        "shiqu_llm_model": values["model"],
    }

    backup = target.with_name(target.name + ".bak." + datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(target, backup)

    ok, message = module.write_shiqu_config(backend, config)
    if not ok:
        shutil.copy2(backup, target)
        raise SystemExit("修复失败，已恢复备份：" + message)

    print("配置重新生成：成功")
    print("API 地址：", "正常" if values["base_url"] else "缺失")
    print("API Key：", "正常" if values["api_key"] else "缺失")
    print("模型：", values["model"])
    print("json_mode：已由模板生成（默认 true）")
    print("备份：", backup)
    print("请重启后端，再测试「是区吗」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

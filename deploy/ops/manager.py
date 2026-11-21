"""部署管理器：编排 Overstats 后端一键部署全流程。

整合 git_ops / venv_ops / config_writer / process_runner，提供
deploy / start / stop / restart / update / status 等高层接口，
供 main.py 调用。所有逻辑独立于 main.py。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config_writer, git_ops, process_runner, uninstaller, venv_ops

from astrbot.api import logger

# 部署状态常量
STATE_IDLE = "idle"                # 未部署
STATE_DEPLOYING = "deploying"      # 部署中
STATE_RUNNING = "running"          # 后端运行中
STATE_STOPPED = "stopped"          # 已手动停止
STATE_FAILED = "failed"            # 部署或运行失败

# AstrBot 默认 PyPI 镜像源
_DEFAULT_PYPI_INDEX = "https://mirrors.aliyun.com/pypi/simple/"


@dataclass
class DeployResult:
    """部署/更新操作的结果。"""
    success: bool
    message: str
    logs: list[str] = field(default_factory=list)
    elapsed: float = 0.0


@dataclass
class DeployStatus:
    """当前部署状态快照。"""
    state: str = STATE_IDLE
    mode: str = "manual"
    backend_dir: str = ""
    venv_dir: str = ""
    backend_port: int = 18081
    backend_host: str = "127.0.0.1"
    git_commit: str = "unknown"
    git_origin: str = ""
    repo_expected: str = ""
    process_alive: bool = False
    process_pid: Optional[int] = None
    last_deploy_time: float = 0.0
    last_error: str = ""


class DeployManager:
    """Overstats 后端部署管理器。

    负责编排 git clone / venv 创建 / 依赖安装 / 配置生成 / 子进程启动 全流程，
    并提供生命周期管理与状态查询接口。
    """

    # KV 存储 key：持久化部署状态
    _KV_STATE_KEY = "overstats_deploy_state"

    def __init__(
        self,
        plugin_data_dir: str | Path,
        config: dict,
        context=None,
    ):
        """
        Args:
            plugin_data_dir: 插件数据目录（data/plugin_data/overstats_full/）
            config: AstrBot 插件配置字典
            context: AstrBot Context 对象（用于读取全局 pypi 镜像源配置与 KV 存储）
        """
        self._data_dir = Path(plugin_data_dir)
        self._config = config
        
        for key in ("shiqu_llm_api_base", "shiqu_llm_api_key", "shiqu_llm_model"):
            logger.warning(
                "[Shiqu诊断] %s configured=%s",
                key, bool(self._config.get(key, ""))
            )

        self._context = context

        # 部署目录：data/plugin_data/overstats_full/overstats_backend/
        self._backend_dir = self._data_dir / "overstats_backend"
        self._venv_dir = self._data_dir / "overstats_backend_venv"
        self._requirements_file = self._backend_dir / "requirements.txt"

        # 持久化日志文件路径：data/plugin_data/overstats_full/overstats_backend.log
        self._log_file = self._data_dir / "overstats_backend.log"

        # 子进程运行器（内存缓冲 300 行 + 持久化文件 300 行）
        self._runner = process_runner.ProcessRunner(
            max_log_lines=300,
            log_file_path=self._log_file,
            max_file_log_lines=300,
        )

        # 运行时状态（内存）
        self._state: str = STATE_IDLE
        self._last_deploy_time: float = 0.0
        self._last_error: str = ""
        self._git_commit: str = "unknown"
        self._deploy_lock = asyncio.Lock()
        self._auto_started = False

    # 配置访问

    @property
    def mode(self) -> str:
        """当前部署模式：auto（一键托管）或 manual（独立链接）。"""
        return str(self._config.get("deploy_mode", "manual")).lower()

    @property
    def is_auto_mode(self) -> bool:
        return self.mode == "auto"

    @property
    def backend_host(self) -> str:
        return str(self._config.get("backend_host", "127.0.0.1") or "127.0.0.1")

    @property
    def backend_port(self) -> int:
        try:
            return int(self._config.get("backend_port", 18081) or 18081)
        except (TypeError, ValueError):
            return 18081

    @property
    def backend_dir(self) -> Path:
        return self._backend_dir

    @property
    def repo_url(self) -> str:
        return str(self._config.get("backend_repo_url", "https://github.com/imztm123/Overstats.git"))

    def resolve_base_url(self) -> str:
        """根据部署模式返回插件应使用的 base_url。

        - auto 模式：指向本地托管的后端
        - manual 模式：使用用户配置的 overstats_api_url
        """
        if self.is_auto_mode:
            return f"http://{self.backend_host}:{self.backend_port}/api/v2"
        return str(self._config.get("overstats_api_url", "http://127.0.0.1:18080/api/v2"))

    def _get_pypi_index(self) -> str:
        """获取 PyPI 镜像源地址，优先使用 AstrBot 全局配置。"""
        try:
            if self._context and hasattr(self._context, "get_config"):
                cfg = self._context.get_config()
                url = getattr(cfg, "pypi_index_url", "") if cfg else ""
                if url:
                    return str(url)
        except Exception:
            pass
        return _DEFAULT_PYPI_INDEX

    def _get_pip_extra_args(self) -> str:
        """获取 AstrBot 配置的额外 pip 参数。"""
        try:
            if self._context and hasattr(self._context, "get_config"):
                cfg = self._context.get_config()
                arg = getattr(cfg, "pip_install_arg", "") if cfg else ""
                if arg:
                    return str(arg)
        except Exception:
            pass
        return ""

    def _get_proxy_config(self) -> dict:
        """获取 GitHub 加速代理配置字典，传递给 git_ops。"""
        return {
            "github_proxy_enabled": bool(self._config.get("github_proxy_enabled", False)),
            "github_proxy_primary": str(self._config.get("github_proxy_primary", git_ops.DEFAULT_PROXY_PRIMARY) or git_ops.DEFAULT_PROXY_PRIMARY),
            "github_proxy_fallback": str(self._config.get("github_proxy_fallback", git_ops.DEFAULT_PROXY_FALLBACK) or git_ops.DEFAULT_PROXY_FALLBACK),
            "github_proxy_custom": str(self._config.get("github_proxy_custom", "") or ""),
        }

    # 状态查询

    async def get_status(self) -> DeployStatus:
        """获取当前部署状态快照。"""
        # 尝试获取最新 commit hash
        commit = self._git_commit
        origin = ""
        if self._backend_dir.exists() and (self._backend_dir / ".git").exists():
            try:
                commit = await git_ops.get_commit_hash(self._backend_dir)
                self._git_commit = commit
            except Exception:
                pass
            try:
                origin = await git_ops.get_remote_url(self._backend_dir)
            except Exception:
                origin = ""

        return DeployStatus(
            state=self._state,
            mode=self.mode,
            backend_dir=str(self._backend_dir) if self._backend_dir.exists() else "",
            venv_dir=str(self._venv_dir) if venv_ops.exists(self._venv_dir) else "",
            backend_port=self.backend_port,
            backend_host=self.backend_host,
            git_commit=commit,
            git_origin=origin,
            repo_expected=self.repo_url,
            process_alive=self._runner.is_alive,
            process_pid=self._runner._process.pid if (self._runner._process and self._runner._process.returncode is None) else None,
            last_deploy_time=self._last_deploy_time,
            last_error=self._last_error,
        )

    def get_logs(self, lines: int = 50) -> list[str]:
        """获取最近的后端运行日志（内存缓冲）。"""
        return self._runner.get_recent_logs(lines)

    def get_backend_logs(self, lines: int = 10) -> list[str]:
        """从持久化日志文件中获取最新的 N 行后端日志。

        与 get_logs 不同，此方法读取专用日志文件，
        日志跨后端重启保留，容量上限 300 条。
        """
        return self._runner.get_file_logs(lines)

    @property
    def log_file_path(self) -> Path:
        """持久化日志文件路径。"""
        return self._log_file

    def _set_state(self, state: str, error: str = ""):
        self._state = state
        if error:
            self._last_error = error
        elif state in (STATE_RUNNING, STATE_IDLE):
            self._last_error = ""

    # 全流程部署

    async def _align_remote(self, logs: list[str]) -> None:
        """让已有克隆的 origin 与配置里的 backend_repo_url 保持一致。

        场景：插件与原版同包名，从原版升级过来的实例已有 origin=原作者的 Overstats，
        用户把 backend_repo_url 改成本版仓库后，直接 pull 仍然拉的是原仓库。
        此处检测到不一致时自动切换 origin，再执行后续 pull。
        """
        try:
            remote = await git_ops.get_remote_url(self._backend_dir)
        except Exception:
            return
        target = self.repo_url
        if not remote or not target:
            return
        if git_ops._normalize_repo_url(remote) == git_ops._normalize_repo_url(target):
            return
        ok, msg = await git_ops.set_remote_url(self._backend_dir, target)
        if ok:
            logs.append(f"[3/6] 后端仓库地址已变更，origin 切换：{remote} → {target}")
            logger.warning(f"[OverstatsDeploy] backend origin 已由 {remote} 切换为 {target}")
        else:
            logs.append(f"[3/6] origin 切换失败（继续尝试原仓库）：{msg}")

    async def deploy(self) -> DeployResult:
        """执行完整部署流程：git clone/pull → venv → install → config → start → health check。

        Returns:
            DeployResult
        """
        start_time = time.time()
        async with self._deploy_lock:
            self._set_state(STATE_DEPLOYING)
            logs: list[str] = []

            try:
                # 1. 校验账号配置
                ok, msg = config_writer.validate_accounts(self._config)
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)
                logs.append(f"[1/6] 账号校验: {msg}")

                # 2. 检测 git
                git_ok = await git_ops.check_git_available()
                if not git_ok:
                    msg = "未检测到 git 命令，请先安装 git 并确保在 PATH 中可用。"
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)
                logs.append("[2/6] git 可用性检测: 通过")

                # 3. clone 或 pull
                proxy_config = self._get_proxy_config()
                if self._backend_dir.exists() and (self._backend_dir / ".git").exists():
                    await self._align_remote(logs)
                    logs.append("[3/6] 检测到已有后端代码，执行 git pull 更新...")
                    ok, msg = await git_ops.pull(self._backend_dir, proxy_config=proxy_config)
                    if not ok:
                        # pull 失败尝试强制重置
                        logs.append(f"[3/6] git pull 失败: {msg}，尝试强制重置...")
                        ok, msg = await git_ops.fetch_and_reset(self._backend_dir, proxy_config=proxy_config)
                        if not ok:
                            self._set_state(STATE_FAILED, msg)
                            return DeployResult(False, f"代码更新失败: {msg}", logs, time.time() - start_time)
                    logs.append(f"[3/6] 代码更新: {msg}")
                else:
                    logs.append(f"[3/6] 首次部署，正在克隆仓库: {self.repo_url}")
                    ok, msg = await git_ops.clone(self.repo_url, self._backend_dir, proxy_config=proxy_config)
                    if not ok:
                        self._set_state(STATE_FAILED, msg)
                        return DeployResult(False, msg, logs, time.time() - start_time)
                    logs.append(f"[3/6] 代码克隆: {msg}")

                self._git_commit = await git_ops.get_commit_hash(self._backend_dir)
                logs.append(f"[3/6] 当前 commit: {self._git_commit}")

                # 4. 创建/复用 venv
                logs.append("[4/6] 准备虚拟环境...")
                ok, msg = await venv_ops.create(self._venv_dir)
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)
                logs.append(f"[4/6] 虚拟环境: {msg}")

                # 5. 安装依赖
                logs.append("[5/6] 安装依赖（可能需要 1-3 分钟，请耐心等待）...")
                index_url = self._get_pypi_index()
                extra_args = self._get_pip_extra_args()
                ok, msg = await venv_ops.install_requirements(
                    self._venv_dir,
                    self._requirements_file,
                    index_url=index_url,
                    extra_args=extra_args,
                )
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, f"依赖安装失败: {msg}", logs, time.time() - start_time)
                logs.append(f"[5/6] 依赖安装: {msg}")

                # 6. 生成配置并启动
                logs.append("[6/6] 生成配置文件并启动后端...")
                ok, msg = config_writer.write_config(self._backend_dir, self._config)
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)
                logs.append(f"[6/6] 配置生成: {msg}")

                # 启动进程
                venv_python = venv_ops.get_venv_python(self._venv_dir)
                ok, msg = await self._runner.start(self._backend_dir, venv_python, self.backend_host, self.backend_port)
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)
                logs.append(f"[6/6] 进程启动: {msg}")

                # 健康检查
                ok, msg = await self._runner.wait_for_health(self.backend_host, self.backend_port, timeout=40.0)
                # 无论健康检查结果如何，把运行日志带上
                logs.extend(self._runner.get_recent_logs(30))
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)

                self._last_deploy_time = time.time()
                self._set_state(STATE_RUNNING)
                logs.append(f"[完成] 部署成功，后端服务已就绪: http://{self.backend_host}:{self.backend_port}")
                return DeployResult(True, msg, logs, time.time() - start_time)

            except Exception as e:
                err = f"部署过程中发生未预期异常: {e}"
                logger.exception("[OverstatsDeploy] deploy 异常")
                self._set_state(STATE_FAILED, err)
                logs.append(f"[异常] {err}")
                return DeployResult(False, err, logs, time.time() - start_time)

    # 生命周期管理

    async def ensure_repo_alignment(self) -> tuple[bool, str]:
        """确保已有后端克隆的 origin 与配置里的 backend_repo_url 一致（只改 remote，不拉代码）。

        用于插件加载 / 重启后端时自动纠正：从原版插件覆盖升级过来的实例，
        配置被迁移成新仓库后，磁盘上的克隆目录 origin 仍指向原仓库，
        若用户此时直接 /ow更新后端 就会拉回原作者的代码。返回 (是否发生了切换, 说明)。
        """
        if self.mode != "auto":
            return False, "manual 模式无需对齐"
        if not (self._backend_dir.exists() and (self._backend_dir / ".git").exists()):
            return False, "后端未部署"
        try:
            remote = await git_ops.get_remote_url(self._backend_dir)
        except Exception as e:
            return False, f"读取 origin 失败: {e}"
        target = self.repo_url
        if not remote or not target:
            return False, "缺少 origin 或配置地址"
        if git_ops._normalize_repo_url(remote) == git_ops._normalize_repo_url(target):
            return False, "origin 已一致"
        ok, msg = await git_ops.set_remote_url(self._backend_dir, target)
        if ok:
            logger.warning(
                f"[OverstatsDeploy] 后端 origin 与配置不一致，已自动切换：{remote} → {target}。"
                f"当前磁盘上的代码仍可能是原仓库版本，建议执行一次 /ow更新后端 拉取配套后端。"
            )
            return True, f"{remote} → {target}"
        logger.error(f"[OverstatsDeploy] 自动切换后端 origin 失败: {msg}")
        return False, msg

    async def start(self) -> DeployResult:
        """启动已部署的后端（不重新部署）。"""
        start_time = time.time()
        async with self._deploy_lock:
            if self._runner.is_alive:
                return DeployResult(True, "后端进程已在运行", [], 0.0)

            if not (self._backend_dir / "run.py").exists():
                return DeployResult(False, "后端代码未部署，请先执行 /ow部署", [], 0.0)

            if not venv_ops.exists(self._venv_dir):
                return DeployResult(False, "虚拟环境未创建，请先执行 /ow部署", [], 0.0)

            # 确保配置文件是最新的
            ok, msg = config_writer.write_config(self._backend_dir, self._config)
            if not ok:
                return DeployResult(False, msg, [], time.time() - start_time)

            venv_python = venv_ops.get_venv_python(self._venv_dir)
            ok, msg = await self._runner.start(self._backend_dir, venv_python, self.backend_host, self.backend_port)
            if not ok:
                self._set_state(STATE_FAILED, msg)
                return DeployResult(False, msg, [], time.time() - start_time)

            ok, msg = await self._runner.wait_for_health(self.backend_host, self.backend_port, timeout=40.0)
            logs = self._runner.get_recent_logs(30)
            if not ok:
                self._set_state(STATE_FAILED, msg)
                return DeployResult(False, msg, logs, time.time() - start_time)

            self._set_state(STATE_RUNNING)
            return DeployResult(True, msg, logs, time.time() - start_time)

    async def stop(self) -> DeployResult:
        """停止后端进程。"""
        ok, msg = await self._runner.stop()
        if ok:
            self._set_state(STATE_STOPPED)
        return DeployResult(ok, msg, [], 0.0)

    async def restart(self) -> DeployResult:
        """重启后端：先停止再启动。"""
        await self.stop()
        # 重启前顺带对齐 origin，避免配置改了但克隆目录仍指向原仓库
        try:
            await self.ensure_repo_alignment()
        except Exception as e:
            logger.warning(f"[OverstatsDeploy] 重启前对齐 origin 失败: {e}")
        await asyncio.sleep(1.0)
        return await self.start()

    async def update(self) -> DeployResult:
        """更新后端：git pull + 重新安装依赖 + 重新生成配置 + 重启。"""
        start_time = time.time()
        async with self._deploy_lock:
            logs: list[str] = []

            if not (self._backend_dir / ".git").exists():
                return DeployResult(False, "后端代码未部署，无法更新，请先执行 /ow部署", logs, time.time() - start_time)

            self._set_state(STATE_DEPLOYING)
            try:
                # 1. 停止运行中的进程
                if self._runner.is_alive:
                    logs.append("[1/5] 停止运行中的后端进程...")
                    await self._runner.stop()
                    logs.append("[1/5] 已停止")

                # 2. git pull
                logs.append("[2/5] 拉取最新代码...")
                proxy_config = self._get_proxy_config()
                ok, msg = await git_ops.pull(self._backend_dir, proxy_config=proxy_config)
                if not ok:
                    logs.append(f"[2/5] git pull 失败: {msg}，尝试强制重置...")
                    ok, msg = await git_ops.fetch_and_reset(self._backend_dir, proxy_config=proxy_config)
                    if not ok:
                        self._set_state(STATE_FAILED, msg)
                        return DeployResult(False, f"代码更新失败: {msg}", logs, time.time() - start_time)
                logs.append(f"[2/5] {msg}")

                self._git_commit = await git_ops.get_commit_hash(self._backend_dir)
                logs.append(f"[2/5] 当前 commit: {self._git_commit}")

                # 3. 重新安装依赖（处理依赖变更）
                logs.append("[3/5] 检查并安装依赖...")
                index_url = self._get_pypi_index()
                extra_args = self._get_pip_extra_args()
                ok, msg = await venv_ops.install_requirements(
                    self._venv_dir,
                    self._requirements_file,
                    index_url=index_url,
                    extra_args=extra_args,
                )
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, f"依赖安装失败: {msg}", logs, time.time() - start_time)
                logs.append(f"[3/5] {msg}")

                # 4. 重新生成配置
                logs.append("[4/5] 重新生成配置文件...")
                ok, msg = config_writer.write_config(self._backend_dir, self._config)
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)
                logs.append(f"[4/5] {msg}")

                # 5. 启动
                logs.append("[5/5] 启动后端...")
                venv_python = venv_ops.get_venv_python(self._venv_dir)
                ok, msg = await self._runner.start(self._backend_dir, venv_python, self.backend_host, self.backend_port)
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)
                logs.append(f"[5/5] {msg}")

                ok, msg = await self._runner.wait_for_health(self.backend_host, self.backend_port, timeout=40.0)
                logs.extend(self._runner.get_recent_logs(30))
                if not ok:
                    self._set_state(STATE_FAILED, msg)
                    return DeployResult(False, msg, logs, time.time() - start_time)

                self._last_deploy_time = time.time()
                self._set_state(STATE_RUNNING)
                logs.append(f"[完成] 更新成功，commit={self._git_commit}")
                return DeployResult(True, msg, logs, time.time() - start_time)

            except Exception as e:
                err = f"更新过程中发生未预期异常: {e}"
                logger.exception("[OverstatsDeploy] update 异常")
                self._set_state(STATE_FAILED, err)
                logs.append(f"[异常] {err}")
                return DeployResult(False, err, logs, time.time() - start_time)

    # 自动启动 & 清理

    async def maybe_auto_start(self):
        """auto 模式下，若配置了 auto_start_on_boot 则在插件加载时启动后端。

        仅执行一次，避免重复启动。若后端代码与 venv 已就绪则直接 start，
        否则跳过（用户需手动执行 /ow部署 完成首次部署）。
        """
        if self._auto_started:
            return
        self._auto_started = True

        if not self.is_auto_mode:
            return
        if not bool(self._config.get("auto_start_on_boot", False)):
            return

        # 仅在后端代码与 venv 都已就绪时自动启动，否则首次部署必须手动触发
        if not (self._backend_dir / "run.py").exists():
            logger.info("[OverstatsDeploy] auto_start_on_boot 已开启，但后端代码未部署，跳过自动启动")
            return
        if not venv_ops.exists(self._venv_dir):
            logger.info("[OverstatsDeploy] auto_start_on_boot 已开启，但虚拟环境未创建，跳过自动启动")
            return

        logger.info("[OverstatsDeploy] auto_start_on_boot 已开启，自动启动后端...")
        try:
            result = await self.start()
            if result.success:
                logger.info(f"[OverstatsDeploy] 自动启动成功: {result.message}")
            else:
                logger.warning(f"[OverstatsDeploy] 自动启动失败: {result.message}（可手动执行 /ow部署）")
        except Exception as e:
            logger.warning(f"[OverstatsDeploy] 自动启动异常: {e}（可手动执行 /ow部署）")

    async def cleanup(self):
        """插件卸载/停用时调用，停止后端进程。"""
        try:
            await self._runner.cleanup()
        except Exception as e:
            logger.warning(f"[OverstatsDeploy] cleanup 异常: {e}")

    # 后端卸载（独立于插件卸载，实现安全隔离）

    def get_uninstaller(self) -> uninstaller.BackendUninstaller:
        """获取后端卸载器实例。"""
        return uninstaller.BackendUninstaller(
            backend_dir=self._backend_dir,
            venv_dir=self._venv_dir,
            process_runner=self._runner,
        )

    async def uninstall_backend(
        self,
        delete_code: bool = True,
        delete_venv: bool = True,
        force: bool = False,
    ) -> uninstaller.UninstallResult:
        """卸载后端资源（代码 + venv），与插件卸载逻辑隔离。

        严格按照「停止进程 → 删除文件」顺序，确保数据库安全。
        卸载后重置内部状态。

        Args:
            delete_code: 是否删除后端代码目录（含数据库）
            delete_venv: 是否删除虚拟环境
            force: 是否强制卸载（进程停止失败时仍继续删除文件）
        """
        uni = self.get_uninstaller()
        result = await uni.uninstall(
            delete_code=delete_code,
            delete_venv=delete_venv,
            force=force,
        )

        # 卸载后重置状态
        if result.success or force:
            self._set_state(STATE_IDLE)
            self._git_commit = "unknown"
            self._last_deploy_time = 0.0
            self._auto_started = False
            # 清空日志缓冲
            self._runner.clear_logs()

        return result

    def get_uninstall_preview(self) -> dict:
        """获取卸载预览信息（不实际执行删除）。"""
        return self.get_uninstaller().preview()

"""沙盒执行：所有不可信代码的统一入口。

用开源库 llm-sandbox（github.com/vndee/llm-sandbox，MIT）做隔离：
代码在 Docker 容器里跑，容器默认断网、丢弃全部 Linux capability、
禁止提权，宿主机碰不到、代码也碰不到宿主机。

为什么不自己撸 Docker 调用：
- 容器生命周期、镜像拉取、输出编码、依赖安装这些细节 llm-sandbox 已经踩过坑；
- 它有官方 Google ADK 集成示例，接进本项目的 SkillToolset 是同一条路；
- 换后端（Podman / Bubblewrap / NsJail）不用动业务代码，只换 runtime_configs。

「不可信代码」在本项目有三个来源：
- 技能脚本（技能包里的 scripts/，内容可能来自第三方导入包）
- 工作流代码节点（画布上的 Python 片段）
- 手动试跑（页面上的沙盒试跑）

三者的差别只有来源标识与上下文，执行路径完全一致，
所以只有这一个 run_code()：差异在调用方，不在执行器。

一条硬规矩：沙盒不可用时绝不退回宿主机执行。
退回执行等于把隔离承诺变成假的，比直接报错严重得多。
"""

from __future__ import annotations

import asyncio
import io
import logging
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.config import get_settings
from app.models_orm.sandbox import SandboxExecution
from app.schemas.workflow import PIP_NAME_PATTERN

logger = logging.getLogger(__name__)

# 落库时的正文上限：执行记录是给人回看的，不是完整存档，
# 超长正文只留开头并标注截断，避免一张表被单次输出撑爆。
CODE_LIMIT = 20000
OUTPUT_LIMIT = 20000

# 从容器里读回输出的字节上限。容器磁盘没有配额，代码可以一直打印，
# 整份读进内存会把 API 进程拖垮，所以读取阶段就要截断，不能等落库时再截。
OUTPUT_READ_LIMIT = 256 * 1024

# 单次执行允许安装的依赖数量上限，与画布上的校验口径一致。
MAX_LIBRARIES = 20

# 容器内执行超时之外的总墙钟余量（秒）。
# 容器里的死循环由 llm-sandbox 的 execution_timeout 负责；
# 这里再兜一层，是因为 Docker 守护进程卡住时执行线程根本不会返回。
HARD_TIMEOUT_GRACE_SECONDS = 30

# 沙盒支持的来源
SOURCE_SKILL = "skill"
SOURCE_WORKFLOW = "workflow"
SOURCE_MANUAL = "manual"

# 执行结果状态
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"

# 单次执行的最长墙钟时间（秒）。超时的代码会被容器销毁，
# 但这里仍要自己兜一层：容器里跑死循环时未必能及时返回。
DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600

# 容器内收集输出的文件名后缀。代码的输出先落进这些文件，再从容器里读回来。
OUTPUT_FILE_STDOUT = ".stdout"
OUTPUT_FILE_STDERR = ".stderr"
OUTPUT_FILE_EXIT = ".exit"

# 容器内的工作目录：代码文件与输出文件都放这里。
CONTAINER_WORKDIR = "/tmp"

# 容器内的硬限制：内存、进程数。防止代码把宿主机拖垮。
MEMORY_LIMIT = "512m"
PIDS_LIMIT = 256

# 容器主进程的常驻时长。容器一起来主进程就退出的话，
# 还没执行的代码会被连带杀掉，输出就成了空的。
CONTAINER_IDLE_SECONDS = 600


class SandboxUnavailable(RuntimeError):
    """沙盒环境不可用（Docker 不可达、镜像缺失等）。

    与「代码自己写错了」区分开：前者是环境问题，后者是用户代码问题。
    """


@dataclass
class SandboxResult:
    """一次沙盒执行的结果。"""

    execution_id: str
    stdout: str
    stderr: str
    exit_code: int | None
    status: str
    error: str
    duration_ms: int


@dataclass
class SandboxContext:
    """一次执行的来源上下文；只用于记录，不影响执行行为。"""

    source: str
    source_ref: str = ""
    session_id: str = ""
    workflow_run_id: str = ""


def _truncate(text: str, limit: int) -> str:
    """正文截断；超长时标注原长度，让看的人知道这不是全部。"""

    if len(text) <= limit:
        return text
    return text[:limit] + f"…（已截断，原文 {len(text)} 字符）"


def _validate_libraries(libraries: list[str]) -> list[str]:
    """校验 pip 包名。

    包名最终会进容器里的 pip 命令，必须先挡掉 shell 元字符与路径穿越写法。
    画布上的代码节点已在 schema 层校验过，手动试跑走的是这条，
    两处共用同一套规则，避免一个入口宽松一个入口严格。
    """

    names: list[str] = []
    for raw in libraries:
        name = raw.strip()
        if name == "":
            raise ValueError("libraries 不能包含空字符串")
        if not PIP_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"非法的 pip 包名: {raw}")
        names.append(name)
    if len(names) > MAX_LIBRARIES:
        raise ValueError(f"libraries 最多 {MAX_LIBRARIES} 个")
    return names


def _sandbox_settings() -> tuple[str, bool, int]:
    """读沙盒配置：镜像、是否联网、超时。缺项显式失败。"""

    settings = get_settings()
    image = settings.sandbox_image
    if image == "":
        raise SandboxUnavailable("缺少沙盒镜像配置 sandbox_image")
    timeout = settings.sandbox_timeout_seconds
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise SandboxUnavailable(
            f"沙盒超时配置非法: {timeout}（范围 1..{MAX_TIMEOUT_SECONDS}）"
        )
    return image, settings.sandbox_network_enabled, timeout


def _runtime_configs(network_enabled: bool) -> dict:
    """容器加固参数。

    断网是默认姿态：沙盒里的代码没有理由访问外网，开了反而是数据外泄的口子。
    需要联网时由配置显式打开。
    """

    configs: dict = {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "mem_limit": MEMORY_LIMIT,
        "pids_limit": PIDS_LIMIT,
        # 主进程必须常驻：python:3.12-slim 自带的 CMD 是 python3，
        # 容器一起来主进程就退出，exec 还没来得及跑就被连带杀掉，
        # 表现为「stdout 偶发为空」。这里用 sleep 顶住主进程，
        # 代码由 exec 单独执行，与主进程无关。
        "command": ["sleep", str(CONTAINER_IDLE_SECONDS)],
    }
    configs["network_mode"] = "bridge" if network_enabled else "none"
    return configs


def _docker_client() -> object:
    """建 Docker 客户端；连不上显式失败，不静默退化成宿主机执行。"""

    try:
        import docker
    except ImportError as exc:
        # 依赖没装是环境问题，不是用户代码问题：必须归到 SandboxUnavailable，
        # 否则会被上层记成「执行失败」，让人以为是自己的代码写错了。
        raise SandboxUnavailable(
            f"缺少沙盒依赖 docker: {exc}；请执行 uv sync"
        ) from exc

    host = get_settings().sandbox_docker_host
    try:
        if host == "":
            client = docker.from_env()
        else:
            client = docker.DockerClient(base_url=host)
        client.ping()
    except Exception as exc:  # noqa: BLE001 — 任何连接异常都归为环境不可用
        raise SandboxUnavailable(f"沙盒 Docker 不可用: {type(exc).__name__}: {exc}") from exc
    return client


@lru_cache(maxsize=1)
def _record_engine() -> AsyncEngine:
    """执行记录专用的引擎。

    用 NullPool：每次落库开一条新连接，不用池化连接，
    这样无论在哪个事件循环里跑（技能执行器会自己开循环）都不会跨循环复用连接。
    """

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    return create_async_engine(get_settings().database_url, poolclass=NullPool)


def _record_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """执行记录专用的会话工厂。"""

    return async_sessionmaker(_record_engine(), expire_on_commit=False)



@lru_cache(maxsize=1)
def _teardown_session_class() -> type:
    """构造「快速收尾」的会话类（只构造一次）。

    库原本的收尾是先 stop() 再 remove()：docker 默认给 stop 10 秒优雅退出，
    每次执行光等收尾就要 10 秒以上，交互体验没法接受。
    容器是一次性的、代码已经跑完、输出已经取回，没有需要善后的状态，
    直接 kill 掉是安全的。
    """

    try:
        from llm_sandbox.docker import SandboxDockerSession
    except ImportError as exc:
        # 同 docker 客户端：依赖缺失属于环境不可用，不能记成用户代码失败。
        raise SandboxUnavailable(
            f"缺少沙盒依赖 llm-sandbox: {exc}；请执行 uv sync"
        ) from exc

    class _FastTeardownSession(SandboxDockerSession):
        """收尾时直接杀容器，跳过 stop 的优雅退出等待。"""

        def close(self) -> None:
            if self.container:
                try:
                    self.container.kill()
                    self.container.remove(force=True)
                except Exception:  # noqa: BLE001 — 清理失败不该掩盖执行结果
                    logger.warning("沙盒容器清理失败 container=%s", self.container.id)
                self.container = None

    return _FastTeardownSession


async def run_code(
    *,
    code: str,
    ctx: SandboxContext,
    libraries: list[str] | None = None,
    timeout_seconds: int | None = None,
) -> SandboxResult:
    """在沙盒里跑一段 Python 代码，并记录这次执行。

    代码在容器内执行，宿主机只拿到 stdout / stderr / 退出码。
    执行记录无论成功失败都写一条：失败的那次恰恰最需要留痕。
    """

    if code.strip() == "":
        raise ValueError("沙盒代码不能为空")

    packages = _validate_libraries(libraries or [])
    image, network_enabled, default_timeout = _sandbox_settings()
    if packages and not network_enabled:
        # pip 需要联网。断网是默认姿态，与其让 pip 在容器里报一堆网络错误，
        # 不如在这里明确告诉调用方：要么换预装好依赖的镜像，要么显式打开联网。
        raise ValueError(
            "沙盒默认断网，无法安装依赖；请改用预装好依赖的镜像，"
            "或打开 SANDBOX_NETWORK_ENABLED 后重试"
        )
    timeout = default_timeout if timeout_seconds is None else timeout_seconds
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"沙盒超时非法: {timeout}（范围 1..{MAX_TIMEOUT_SECONDS}）")

    execution_id = str(uuid4())
    # 记录的开始时间取自真正开始执行的那一刻；落库时才取会让起止时间相同。
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    status = STATUS_FAILED
    error = ""

    try:
        # 外层再套一层总墙钟：容器里的超时由 llm-sandbox 管，
        # 但 Docker 守护进程卡住时执行线程不会返回，没有这层请求会一直挂着。
        # 取消只能让调用方脱身，线程本身无法回收。
        stdout, stderr, exit_code = await asyncio.wait_for(
            asyncio.to_thread(
                _execute_in_container,
                image=image,
                code=code,
                libraries=packages,
                network_enabled=network_enabled,
                timeout=timeout,
            ),
            timeout=timeout + HARD_TIMEOUT_GRACE_SECONDS,
        )
        status = STATUS_SUCCEEDED if exit_code == 0 else STATUS_FAILED
        if status == STATUS_FAILED and stderr == "":
            error = f"沙盒执行失败，退出码 {exit_code}"
    except TimeoutError as exc:
        status = STATUS_TIMEOUT
        error = f"沙盒执行超时（{timeout} 秒）: {exc}"
    except SandboxUnavailable:
        # 环境不可用：这条记录也要写，否则「为什么没结果」无从查起
        await _record(
            execution_id=execution_id,
            ctx=ctx,
            image=image,
            network_enabled=network_enabled,
            code=code,
            stdout="",
            stderr="",
            exit_code=None,
            status=STATUS_FAILED,
            error="沙盒环境不可用",
            duration_ms=int((time.perf_counter() - started) * 1000),
            started_at=started_at,
        )
        raise
    except Exception as exc:  # noqa: BLE001 — 兜住容器层的各种异常
        status = STATUS_FAILED
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("沙盒执行异常 execution_id=%s", execution_id)

    duration_ms = int((time.perf_counter() - started) * 1000)
    await _record(
        execution_id=execution_id,
        ctx=ctx,
        image=image,
        network_enabled=network_enabled,
        code=code,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        status=status,
        error=error,
        duration_ms=duration_ms,
        started_at=started_at,
    )
    logger.info(
        "沙盒执行完成 execution_id=%s source=%s ref=%s status=%s exit=%s duration_ms=%s",
        execution_id,
        ctx.source,
        ctx.source_ref,
        status,
        exit_code,
        duration_ms,
    )
    return SandboxResult(
        execution_id=execution_id,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        status=status,
        error=error,
        duration_ms=duration_ms,
    )


def _execute_in_container(
    *,
    image: str,
    code: str,
    libraries: list[str],
    network_enabled: bool,
    timeout: int,
) -> tuple[str, str, int | None]:
    """在容器里执行代码（同步；由 asyncio.to_thread 调度）。

    用 llm-sandbox 的会话而不是裸 docker 调用：镜像准备、镜像拉取、
    依赖安装这些细节它已经处理好了；容器加固参数通过 runtime_configs 传进去。
    每次执行开一个新会话、一个新容器，跑完即弃，不留可被复用的状态。

    输出不走 exec 的 socket，而是先落进容器内的文件再读回来：
    docker 的 exec 偶发在拿到任何数据帧之前就返回 EOF，表现为
    「代码跑成功了但 stdout 是空的」（实测 3~7% 的执行会中招），
    而重试会让有副作用的代码跑两遍。读文件走的是另一条通道，绕开了这个问题。
    """

    client = _docker_client()
    timeout_seconds = float(timeout)
    with _teardown_session_class()(
        client=client,
        lang="python",
        image=image,
        # workdir 用 /tmp 而不是 /sandbox：llm-sandbox 默认的 /sandbox 目录
        # 只有在它自己建虚拟环境时才会存在，这里跳过环境准备，用系统 python。
        workdir=CONTAINER_WORKDIR,
        skip_environment_setup=True,
        runtime_configs=_runtime_configs(network_enabled),
        default_timeout=timeout_seconds,
        execution_timeout=timeout_seconds,
        verbose=False,
    ) as session:
        _assert_network_policy(session, network_enabled)
        if libraries:
            _install_libraries(session, libraries)
        return _run_collecting_output(session, code=code)


def _install_libraries(session: object, libraries: list[str]) -> None:
    """用容器里的 pip 安装依赖。

    不走 llm-sandbox 的 session.install：它要求 skip_environment_setup=False
    （即由它自己建虚拟环境），而这里跑的是镜像自带的系统 python，
    会话是以 skip_environment_setup=True 开的，调它会直接抛
    「Library installation is not supported」。

    包名已按 pip 合法字符集校验，且以参数数组传给 exec_run、不经过 shell。
    """

    container = getattr(session, "container", None)
    if container is None:
        raise SandboxUnavailable("沙盒容器未就绪，无法安装依赖")
    result = container.exec_run(
        [
            "python",
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--disable-pip-version-check",
            *libraries,
        ],
        workdir=CONTAINER_WORKDIR,
    )
    exit_code = getattr(result, "exit_code", None)
    if exit_code != 0:
        raise ValueError(
            f"沙盒依赖安装失败（退出码 {exit_code}）: {', '.join(libraries)}"
        )


def _run_collecting_output(
    session: object,
    *,
    code: str,
) -> tuple[str, str, int | None]:
    """跑代码，把输出收进容器内的文件，再读回来。

    退出码也写进文件：exec 自己的退出码在超时等异常情况下不可靠，
    而文件里的退出码是代码进程自己写的。
    """

    container = getattr(session, "container", None)
    if container is None:
        raise SandboxUnavailable("沙盒容器未就绪，无法执行代码")

    name = uuid4().hex
    code_path = f"{CONTAINER_WORKDIR}/{name}.py"
    stdout_path = f"{code_path}{OUTPUT_FILE_STDOUT}"
    stderr_path = f"{code_path}{OUTPUT_FILE_STDERR}"
    exit_path = f"{code_path}{OUTPUT_FILE_EXIT}"

    _write_file_into_container(container, f"{name}.py", code)
    # 用 sh 包一层：要把 stdout / stderr / 退出码分别写进三个文件。
    # sh -c 里的转义只涉及我们自己生成的文件名，不含用户代码。
    command = (
        f"cd {CONTAINER_WORKDIR} && "
        f"python {name}.py > {name}.py{OUTPUT_FILE_STDOUT} "
        f"2> {name}.py{OUTPUT_FILE_STDERR}; "
        f"echo $? > {name}.py{OUTPUT_FILE_EXIT}"
    )
    exec_result = container.exec_run(
        cmd=["sh", "-c", command],
        workdir=CONTAINER_WORKDIR,
        demux=True,
    )
    exec_exit = getattr(exec_result, "exit_code", None)

    stdout = _read_file_from_container(container, stdout_path)
    stderr = _read_file_from_container(container, stderr_path)
    exit_text = _read_file_from_container(container, exit_path).strip()

    if exit_text != "":
        try:
            exit_code: int | None = int(exit_text)
        except ValueError:
            exit_code = exec_exit
    else:
        # 没拿到退出码文件：脚本可能没跑到写退出码那步（比如超时被杀）。
        exit_code = exec_exit
    return stdout, stderr, exit_code


def _write_file_into_container(container: Any, name: str, content: str) -> None:
    """把代码文件写进容器。

    用 put_archive 而不是 exec 写：exec 的输出会丢帧，写文件不能依赖它。
    """

    payload = content.encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    buffer.seek(0)
    container.put_archive(CONTAINER_WORKDIR, buffer.read())


def _read_file_from_container(container: Any, path: str) -> str:
    """从容器里读一个文件的内容；读不到就返回空串。

    只按 tar 头里的长度读，并截到输出上限：容器磁盘没有配额，
    代码可以一直打印，整份读进内存会把这边的进程拖垮。
    """

    try:
        stream, _stat = container.get_archive(path)
    except Exception:  # noqa: BLE001 — 文件不存在是正常情况（代码没跑到那步）
        return ""

    try:
        reader = _TarReader(stream)
        header = reader.read(512)
        if len(header) < 512:
            return ""
        size = _tar_member_size(header)
        if size < 0:
            return ""
        keep = min(size, OUTPUT_READ_LIMIT)
        content = reader.read(keep)
    except Exception:  # noqa: BLE001
        logger.warning("沙盒输出文件读取中断 path=%s", path)
        return ""

    text = content.decode("utf-8", errors="replace")
    if size > keep:
        return text + f"…（已截断，原文 {size} 字节）"
    return text


class _TarReader:
    """把 docker 的 tar 流包成可按字节读的读器。

    一个 chunk 里常常同时含头和内容，按需切分时必须保留多余字节，
    否则头之后的正文会被整段丢掉，表现为「执行成功但输出为空」。
    """

    def __init__(self, stream: Any) -> None:
        self._chunks = iter(stream)
        self._buffer = b""

    def read(self, limit: int) -> bytes:
        while len(self._buffer) < limit:
            try:
                self._buffer += bytes(next(self._chunks))
            except StopIteration:
                break
        taken, self._buffer = self._buffer[:limit], self._buffer[limit:]
        return taken


def _tar_member_size(header: bytes) -> int:
    """解析 tar 头里的成员长度；解析不出返回 -1。"""

    raw = header[124:136].split(b"\0")[0].strip()
    if raw == b"":
        return 0
    try:
        return int(raw, 8)
    except ValueError:
        return -1


def _assert_network_policy(session: object, network_enabled: bool) -> None:
    """核对容器的实际网络状态与配置是否一致。

    docker 的网络参数在部分版本 / 驱动上会被忽略；
    与其相信参数，不如看一眼容器真实的网络模式：配置说断网而实际能联网，
    就是隔离承诺失效，必须当场失败而不是照常跑完。
    """

    container = getattr(session, "container", None)
    if container is None:
        raise SandboxUnavailable("沙盒容器未就绪，无法核对网络隔离")
    network_mode = container.attrs.get("HostConfig", {}).get("NetworkMode")
    if network_enabled:
        if network_mode == "none":
            raise SandboxUnavailable("沙盒配置要求联网，但容器网络被设为 none")
        return
    if network_mode != "none":
        raise SandboxUnavailable(
            f"沙盒配置要求断网，但容器网络模式为 {network_mode!r}，隔离失效"
        )


async def _record(
    *,
    execution_id: str,
    ctx: SandboxContext,
    image: str,
    network_enabled: bool,
    code: str,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    status: str,
    error: str,
    duration_ms: int,
    started_at: datetime,
) -> None:
    """写执行记录。

    用独立会话落库：沙盒执行常发生在工作流 / 对话的事务里，
    复用同一个 session 会把「执行记录」和「业务事务」的成败绑在一起。
    """

    row = SandboxExecution(
        id=execution_id,
        source=ctx.source,
        source_ref=ctx.source_ref,
        session_id=ctx.session_id,
        workflow_run_id=ctx.workflow_run_id,
        language="python",
        image=image,
        network_enabled=network_enabled,
        code=_truncate(code, CODE_LIMIT),
        stdout=_truncate(stdout, OUTPUT_LIMIT),
        stderr=_truncate(stderr, OUTPUT_LIMIT),
        exit_code=exit_code,
        status=status,
        error=error,
        duration_ms=duration_ms,
        # 开始时间由调用方在执行前取好：这里才取会让起止时间落在同一刻，
        # 详情页看不出耗时，按开始时间筛选也会整体偏移。
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
    )
    # 独立引擎 + 独立会话落库：
    # - 不绑业务事务：沙盒执行常发生在工作流 / 对话的事务里，
    #   复用同一个 session 会把「执行记录」和「业务事务」的成败绑在一起；
    # - 不复用模块级 engine 的连接池：技能脚本的执行器每次都在自己的事件循环里跑，
    #   池化连接跨循环复用会报 Event loop is closed。
    async with _record_sessionmaker()() as record_db:
        record_db.add(row)
        await record_db.commit()


def execution_to_wire(row: SandboxExecution) -> dict:
    """执行记录的对外字段。"""

    return {
        "id": row.id,
        "source": row.source,
        "source_ref": row.source_ref,
        "session_id": row.session_id,
        "workflow_run_id": row.workflow_run_id,
        "language": row.language,
        "image": row.image,
        "network_enabled": row.network_enabled,
        "code": row.code,
        "stdout": row.stdout,
        "stderr": row.stderr,
        "exit_code": row.exit_code,
        "status": row.status,
        "error": row.error,
        "duration_ms": row.duration_ms,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }

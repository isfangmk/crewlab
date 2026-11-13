"""沙盒入参校验：不依赖 Docker，纯规则校验。

这些用例覆盖「提交前就能判定」的错误：包名非法、断网下要求装依赖。
它们必须在起容器之前就失败，否则用户会拿到一个看着像自己代码写错的执行记录。
"""

from __future__ import annotations

import pytest

from app.services import sandbox_service


def _ctx() -> sandbox_service.SandboxContext:
    return sandbox_service.SandboxContext(
        source=sandbox_service.SOURCE_MANUAL, source_ref="test"
    )


async def test_rejects_untrusted_package_names() -> None:
    """包名会进容器里的 pip 命令，shell 元字符与路径穿越必须先挡掉。"""

    for bad in ("requests; rm -rf /", "../../etc/passwd", "a b", "pkg|x"):
        with pytest.raises(ValueError, match="非法的 pip 包名"):
            await sandbox_service.run_code(
                code="print(1)", ctx=_ctx(), libraries=[bad]
            )


async def test_rejects_blank_package_name() -> None:
    with pytest.raises(ValueError, match="不能包含空字符串"):
        await sandbox_service.run_code(code="print(1)", ctx=_ctx(), libraries=["  "])


async def test_rejects_too_many_packages() -> None:
    packages = [f"pkg{i}" for i in range(sandbox_service.MAX_LIBRARIES + 1)]
    with pytest.raises(ValueError, match="最多"):
        await sandbox_service.run_code(code="print(1)", ctx=_ctx(), libraries=packages)


async def test_accepts_pinned_version() -> None:
    """校验通过后不应在参数校验这一步失败（断网拦截是下一步）。"""

    with pytest.raises(ValueError, match="断网"):
        await sandbox_service.run_code(
            code="print(1)", ctx=_ctx(), libraries=["requests==2.31.0"]
        )


async def test_rejects_libraries_when_network_disabled() -> None:
    """断网是默认姿态，pip 装不了依赖：要在起容器前说清楚，而不是让 pip 报网络错。"""

    from app.config import get_settings

    if get_settings().sandbox_network_enabled:
        pytest.skip("本机开了沙盒联网，这条规则不适用")
    with pytest.raises(ValueError, match="断网"):
        await sandbox_service.run_code(
            code="print(1)", ctx=_ctx(), libraries=["requests"]
        )


async def test_rejects_empty_code() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        await sandbox_service.run_code(code="   ", ctx=_ctx())

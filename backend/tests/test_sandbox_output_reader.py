"""沙盒输出读取：不依赖 Docker，用构造的 tar 流验证。

这里守的是一条真实踩过的坑：docker 的 tar 流一个 chunk 里常常同时含头与正文，
按「读 512 字节头 → 再读正文」切分时，如果丢弃同 chunk 里剩余的字节，
正文就会整段消失，表现为「执行成功但输出为空」。
"""

from __future__ import annotations

import io
import tarfile
from typing import Any

import pytest

from app.services import sandbox_service


def _tar_bytes(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class _FakeStream:
    """把整份 tar 当成单个 chunk 返回——正是会触发丢正文的形态。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __iter__(self):
        yield self._payload


class _FakeContainer:
    def __init__(self, payload: bytes | None) -> None:
        self._payload = payload

    def get_archive(self, path: str) -> Any:
        if self._payload is None:
            raise FileNotFoundError(path)
        return _FakeStream(self._payload), {}


def test_reads_content_when_header_and_body_share_one_chunk() -> None:
    payload = _tar_bytes("x.py.stdout", b"42\ndone\n")
    assert sandbox_service._read_file_from_container(_FakeContainer(payload), "/tmp/x.py.stdout") == "42\ndone\n"


def test_reads_content_split_across_chunks() -> None:
    payload = _tar_bytes("x.py.stdout", b"hello")

    class _Chunked:
        def __iter__(self):
            for i in range(0, len(payload), 7):
                yield payload[i : i + 7]

    class _Container:
        def get_archive(self, path: str) -> Any:
            return _Chunked(), {}

    assert sandbox_service._read_file_from_container(_Container(), "/tmp/x") == "hello"


def test_truncates_at_read_limit_and_marks_it() -> None:
    """超长输出必须在读取阶段截断，不能整份读进内存。"""

    total = sandbox_service.OUTPUT_READ_LIMIT + 5000
    payload = _tar_bytes("x.py.stdout", b"x" * total)

    text = sandbox_service._read_file_from_container(_FakeContainer(payload), "/tmp/x")

    assert len(text) < total
    assert text.startswith("x" * 100)
    assert "已截断" in text
    assert str(total) in text


def test_missing_file_returns_empty() -> None:
    """文件不存在是正常情况（代码没跑到写输出那一步）。"""

    assert sandbox_service._read_file_from_container(_FakeContainer(None), "/tmp/none") == ""


def test_empty_file_returns_empty() -> None:
    payload = _tar_bytes("x.py.stdout", b"")
    assert sandbox_service._read_file_from_container(_FakeContainer(payload), "/tmp/x") == ""


def test_tar_member_size_reads_octal_field() -> None:
    header = bytearray(512)
    header[124:136] = b"00000000123\0"
    assert sandbox_service._tar_member_size(bytes(header)) == 0o123


def test_tar_member_size_tolerates_garbage() -> None:
    header = bytearray(512)
    header[124:136] = b"not-octal!!!"
    assert sandbox_service._tar_member_size(bytes(header)) == -1


def test_invalid_archive_returns_empty() -> None:
    """不是 tar（比如 docker 返回了别的东西）时不该抛，返回空串。"""

    assert sandbox_service._read_file_from_container(_FakeContainer(b"not a tar"), "/tmp/x") == ""

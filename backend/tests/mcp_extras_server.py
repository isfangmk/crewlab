"""测试用本地 MCP Server：一个工具、静态+模板资源、一个带参提示词。

给 test_mcp_resources_prompts.py / test_mcp_oauth.py 当子进程起（SSE）；
文件名不以 test_ 开头，pytest 不会收集。
端口从命令行第一个参数读；第二个参数（可选）是 OAuth 授权服务器地址，
用于 /.well-known/oauth-protected-resource 发现（test_mcp_oauth 用）。
"""

from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

server = MCPServer(name="extras-demo", description="资源/提示词探测测试用")

_AS_URL = sys.argv[2] if len(sys.argv) > 2 else ""


@server.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def protected_resource(_request: Request) -> JSONResponse:
    """RFC 9728 资源元数据：把发现指到测试授权服务器。"""

    return JSONResponse({"authorization_servers": [_AS_URL] if _AS_URL else []})


@server.tool()
def echo(text: str) -> str:
    """原样返回输入文本。"""
    return text


@server.resource(
    "demo://greeting",
    name="greeting",
    description="问候语资源",
    mime_type="text/plain",
)
def greeting() -> str:
    return "你好，这是测试资源内容"


@server.resource(
    "demo://notes/{note_id}",
    name="note",
    description="按 id 取笔记",
    mime_type="text/plain",
)
def note(note_id: str) -> str:
    return f"笔记内容 {note_id}"


@server.prompt(name="summarize", description="生成主题摘要")
def summarize(topic: str) -> str:
    return f"请总结主题：{topic}"


if __name__ == "__main__":
    server.run(transport="sse", host="127.0.0.1", port=int(sys.argv[1]))

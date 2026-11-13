"""本地 MCP 演示服务（SSE）。

用途：给「MCP 服务」页面提供可探测的真实 Server。
三个工具刻意覆盖不同参数形态：必填数值、无参、必填字符串。
"""

from datetime import datetime

from mcp.server.mcpserver import MCPServer

server = MCPServer(
    name="demo-tools",
    title="演示工具集",
    description="本地演示：加法 / 当前时间 / 回显",
)


@server.tool()
def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


@server.tool()
def current_time() -> str:
    """返回服务器本地时间（ISO 格式，精确到秒）。"""
    return datetime.now().isoformat(timespec="seconds")


@server.tool()
def echo(text: str) -> str:
    """原样返回输入文本，用于连通性验证。"""
    return text


if __name__ == "__main__":
    server.run(transport="sse", host="127.0.0.1", port=18099)

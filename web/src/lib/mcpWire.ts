// MCP 工具 wire 名的前端实现，与后端 `mcp_service.mcp_tool_wire_name` 同源规则：
//   mcp__{数据库原始 server 名}__{规范化 tool 名}
// server 段保留原值（可含连字符与大小写），tool 段只留小写字母与数字。
// 助理白名单、技能声明的工具、审批名单都用这一个函数生成，三处不能各写一套。

export function mcpToolWireName(server: string, tool: string): string {
  if (server === '') throw new Error('MCP server 名不能为空');
  if (server.includes('__')) throw new Error(`MCP server 名不能包含 '__': ${server}`);
  if (tool === '') throw new Error('MCP tool 名不能为空');
  const toolSeg = sanitizeMcpToolSegment(tool);
  if (toolSeg === '') throw new Error(`MCP tool 名规范化后为空: ${tool}`);
  return `mcp__${server}__${toolSeg}`;
}

export function sanitizeMcpToolSegment(value: string): string {
  let out = '';
  for (const char of value.trim()) {
    if (/^[a-z0-9]$/.test(char)) out += char;
    else if (/^[A-Z]$/.test(char)) out += char.toLowerCase();
    else out += '_';
  }
  return out;
}

/** wire 名的 tool 段（展示用）；不是合法 wire 名时原样返回。 */
export function mcpToolDisplayName(wire: string): string {
  const parts = wire.split('__');
  if (parts.length !== 3 || parts[0] !== 'mcp' || parts[1] === '' || parts[2] === '') return wire;
  return parts[2];
}

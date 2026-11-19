// capabilityPreview — 助理「能力绑定」的实际可见口径。
//
// 口径必须与后端运行时一致，否则预览就是骗人的：
//   - MCP 挂载：`agent_service.filter_mcp_servers_by_tools`
//       白名单为空 = 不挂任何 MCP；非空 = 只挂载出现过的 mcp__<server>__* 所属 Server
//   - 可用性：`mcp_service.build_searchable_mcp_toolsets` 会跳过未探测成功（status !== 'ok'）的 Server，
//       白名单非空时 Server 内未勾选的工具不进检索目录（既搜不到也调不了）
//   - MCP 工具一律不进初始上下文：装配的是 search_tools / load_tool，按需检索加载
//   - 内置工具按助理勾选装配（selectedBuiltinTools），默认全选；web_search 仍由对话开关控制
//   - 技能：勾选的技能才装配（`skills_service.resolve_skills`）；空数组 = 不装配任何技能
//   - 禁用名单：`adk_runtime.tool_approval.before_tool_callback` 在执行前直接拒绝，
//     只影响可执行性，不会从目录里消失

import type { ToolRisk } from '@/api/tools';

export type BuiltinToolCapability = {
  name: string;
  description: string;
  risk: ToolRisk;
  mount: 'always' | 'per_turn';
};

export type SkillCapability = {
  key: string;
  description: string;
};

export type McpToolCapability = {
  /** 与后端 mcp_tool_wire_name 一致：mcp__<server>__<tool> */
  wire: string;
  description: string;
  risk: ToolRisk;
};

export type McpServerCapability = {
  name: string;
  enabled: boolean;
  status: string;
  tools: McpToolCapability[];
};

export type CapabilityCatalog = {
  builtin: BuiltinToolCapability[];
  skills: SkillCapability[];
  mcpServers: McpServerCapability[];
};

export type CapabilityPreview = {
  /** 进初始上下文的工具数（内置 always；MCP 工具只进检索目录，不算在内） */
  contextTools: number;
  /** 已装配进检索目录的 MCP 工具数（经 search_tools 发现后 load_tool 调用） */
  searchableMcpTools: number;
  builtinTools: number;
  /** 按对话开关挂载的工具数（如 web_search） */
  perTurnTools: number;
  /** 实际装配的技能数（勾选集合） */
  skills: number;
  /** 勾选了但目录里已不存在的技能 */
  unknownSkills: string[];
  blockedTools: number;
  whitelistHasMcp: boolean;
  mountedServers: string[];
  unmountedServers: string[];
  /** 白名单只选了 Server 内部分工具（未勾选的那部分不会进检索目录） */
  partialServers: { server: string; selected: number; total: number }[];
  /** 白名单里当前目录已不存在的工具（MCP 工具缓存变更后容易残留） */
  unknownWhitelist: string[];
};

/** 与后端 mcp_server_name_from_wire_name 同一解析规则：必须是 mcp__server__tool。 */
export function mcpServerNameFromWire(wire: string): string {
  const parts = wire.split('__');
  if (parts.length !== 3 || parts[0] !== 'mcp' || parts[1] === '' || parts[2] === '') {
    throw new Error(`非法 MCP 工具名: ${wire}`);
  }
  return parts[1];
}

export function previewCapabilities(
  catalog: CapabilityCatalog,
  allowedTools: string[],
  disallowedTools: string[],
  selectedSkills: string[],
  selectedBuiltinTools: string[] | null = null,
): CapabilityPreview {
  // 勾选的技能才装配；空数组 = 不装配任何技能。
  const mountedSkills = catalog.skills.filter((skill) => selectedSkills.includes(skill.key));
  const unknownSkills = selectedSkills.filter((key) => !catalog.skills.some((skill) => skill.key === key));

  // 白名单里显式勾选的 MCP Server
  const whitelistServers = new Set<string>();
  let whitelistHasMcp = false;
  for (const key of allowedTools) {
    if (!key.startsWith('mcp__')) continue;
    whitelistHasMcp = true;
    whitelistServers.add(mcpServerNameFromWire(key));
  }

  const alwaysTools = catalog.builtin.filter((tool) => tool.mount === 'always');
  // selectedBuiltinTools 为 null（目录未加载）时按全部勾选展示
  const chosen = selectedBuiltinTools ?? alwaysTools.map((tool) => tool.name);
  const chosenSet = new Set(chosen);
  const builtinAlways = alwaysTools.filter((tool) => chosenSet.has(tool.name));
  const perTurn = catalog.builtin.filter((tool) => tool.mount === 'per_turn');

  // 装配口径与后端 build_searchable_mcp_toolsets 对齐：Server 启用 + 探测成功 + 有工具缓存，
  // 且白名单里至少还有一个属于它的工具；否则整台不装配。
  const mountedServers: string[] = [];
  const unmounted: string[] = [];
  // 屏蔽数只按 MCP 工具名统计（内置工具走勾选装配，不在屏蔽名单内）
  const usableNames = new Set<string>();
  let searchableMcpTools = 0;
  const partialServers: { server: string; selected: number; total: number }[] = [];
  for (const server of catalog.mcpServers) {
    // 没有工具缓存的 Server 无法进入白名单（挂载以 mcp__<server>__<tool> 为载体）
    if (!server.enabled || server.status !== 'ok' || server.tools.length === 0) {
      unmounted.push(server.name);
      continue;
    }
    // 白名单里没出现的 Server 不装配；Server 内没勾选的工具也不进检索目录
    if (!whitelistServers.has(server.name)) {
      unmounted.push(server.name);
      continue;
    }
    const permitted = server.tools.filter((tool) => allowedTools.includes(tool.wire));
    if (permitted.length === 0) {
      // 白名单里的工具都已失效（改名 / 删除）：后端不装配这台 Server，预览也不该算挂载
      unmounted.push(server.name);
      continue;
    }
    mountedServers.push(server.name);
    searchableMcpTools += permitted.length;
    for (const tool of permitted) usableNames.add(tool.wire);
    if (permitted.length < server.tools.length) {
      partialServers.push({
        server: server.name,
        selected: permitted.length,
        total: server.tools.length,
      });
    }
  }

  const blockedTools = disallowedTools.filter((key) => usableNames.has(key)).length;
  const unknownWhitelist = allowedTools.filter((key) => {
        if (key.startsWith('mcp__')) {
          const server = mcpServerNameFromWire(key);
          const mountedServer = catalog.mcpServers.find((s) => s.name === server);
          return !mountedServer || !mountedServer.tools.some((t) => t.wire === key);
        }
        return !catalog.builtin.some((tool) => tool.name === key);
      });

  return {
    contextTools: builtinAlways.length,
    searchableMcpTools,
    builtinTools: builtinAlways.length,
    perTurnTools: perTurn.length,
    skills: mountedSkills.length,
    unknownSkills,
    blockedTools,
    whitelistHasMcp,
    mountedServers,
    unmountedServers: unmounted,
    partialServers,
    unknownWhitelist,
  };
}

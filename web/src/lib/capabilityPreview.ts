// capabilityPreview — 助理「能力绑定」的实际可见口径。
//
// 口径必须与后端运行时一致，否则预览就是骗人的：
//   - MCP 挂载：`agent_service.filter_mcp_servers_for_agent`
//       白名单为空 = 继承全部；非空 = 只挂载出现过的 mcp__<server>__* 所属 Server
//   - 可用性：`mcp_service.build_searchable_mcp_toolsets` 会跳过未探测成功（status !== 'ok'）的 Server，
//       白名单非空时 Server 内未勾选的工具不进检索目录（既搜不到也调不了）
//   - MCP 工具一律不进初始上下文：装配的是 search_tools / load_tool，按需检索加载
//   - 内置工具：`mount === 'always'` 随 SkillToolset 始终装配；`per_turn` 由对话开关决定
//   - 技能：勾选的技能才装配（`skills_service.resolve_skills`）；`null` = 继承全部技能
//   - 技能声明的工具：随技能自动并入白名单，且不允许屏蔽
//     （`agent_service.validate_skill_binding`：被屏蔽时整单 400）
//   - 禁用名单：`adk_runtime.tool_approval.before_tool_callback` 在执行前直接拒绝，
//     只影响可执行性，不会从目录里消失

export type BuiltinToolCapability = {
  name: string;
  description: string;
  mount: 'always' | 'per_turn';
};

export type SkillCapability = {
  key: string;
  description: string;
  tools: string[];
};

export type McpToolCapability = {
  /** 与后端 mcp_tool_wire_name 一致：mcp__<server>__<tool> */
  wire: string;
  description: string;
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
  /** 实际装配的技能数（勾选集合或继承全部） */
  skills: number;
  /** 勾选技能声明的工具数（去重） */
  skillTools: number;
  /** 由技能声明、因此不可单独屏蔽的工具 */
  lockedTools: string[];
  /** 勾选了但目录里已不存在的技能 */
  unknownSkills: string[];
  blockedTools: number;
  whitelistActive: boolean;
  whitelistHasMcp: boolean;
  mountedServers: string[];
  unmountedServers: string[];
  /** 白名单只选了 Server 内部分工具（未勾选的那部分不会进检索目录） */
  partialServers: { server: string; selected: number; total: number }[];
  /** 白名单里当前目录已不存在的工具（MCP 工具缓存变更后容易残留） */
  unknownWhitelist: string[];
  /** 因技能声明的 MCP 工具而额外挂载的 Server（运行时自动并入白名单） */
  skillDeclaredServers: string[];
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
  selectedSkills: string[] | null = null,
): CapabilityPreview {
  const whitelistActive = allowedTools.length > 0;

  const mountedSkills =
    selectedSkills === null
      ? catalog.skills
      : catalog.skills.filter((skill) => selectedSkills.includes(skill.key));
  const unknownSkills =
    selectedSkills === null
      ? []
      : selectedSkills.filter((key) => !catalog.skills.some((skill) => skill.key === key));
  // 技能声明的工具：随技能自动并入白名单，且不可屏蔽（后端同口径）。
  // 声明里可能是内置工具，也可能是 MCP wire 名（mcp__server__tool）。
  const lockedTools = [...new Set(mountedSkills.flatMap((skill) => skill.tools))];
  const skillDeclaredMcp = lockedTools.filter((wire) => wire.startsWith('mcp__'));

  // 白名单里显式勾选的 MCP Server
  const whitelistServers = new Set<string>();
  let whitelistHasMcp = false;
  for (const key of allowedTools) {
    if (!key.startsWith('mcp__')) continue;
    whitelistHasMcp = true;
    whitelistServers.add(mcpServerNameFromWire(key));
  }
  // 技能声明的 MCP 工具在运行时同样会并入白名单并挂载 Server
  // （agent_service.effective_allowed_tools），预览必须算进来。
  const skillDeclaredServers = new Set<string>();
  for (const wire of skillDeclaredMcp) {
    const server = mcpServerNameFromWire(wire);
    skillDeclaredServers.add(server);
    whitelistServers.add(server);
    whitelistHasMcp = true;
  }

  const builtinAlways = catalog.builtin.filter((tool) => tool.mount === 'always');
  const perTurn = catalog.builtin.filter((tool) => tool.mount === 'per_turn');

  // 装配口径与后端 build_searchable_mcp_toolsets 对齐：Server 启用 + 探测成功 + 有工具缓存，
  // 且白名单（含技能声明）里至少还有一个属于它的工具；否则整台不装配。
  const mountedServers: string[] = [];
  const unmounted: string[] = [];
  // 「可执行 / 可屏蔽」的口径仍按工具名收集：MCP 工具虽不进上下文，但能检索加载后调用
  const usableNames = new Set<string>(builtinAlways.map((tool) => tool.name));
  for (const tool of perTurn) usableNames.add(tool.name);
  let searchableMcpTools = 0;
  const partialServers: { server: string; selected: number; total: number }[] = [];
  for (const server of catalog.mcpServers) {
    // 没有工具缓存的 Server 无法进入白名单（挂载以 mcp__<server>__<tool> 为载体）
    if (!server.enabled || server.status !== 'ok' || server.tools.length === 0) {
      unmounted.push(server.name);
      continue;
    }
    if (whitelistActive && !whitelistServers.has(server.name)) {
      unmounted.push(server.name);
      continue;
    }
    // 白名单启用时，Server 内没勾选的工具不进检索目录
    // （后端 build_searchable_mcp_toolsets 按 allowed_tools 收口），不能算进「可检索」。
    const permitted = whitelistActive
      ? server.tools.filter(
          (tool) => allowedTools.includes(tool.wire) || skillDeclaredMcp.includes(tool.wire),
        )
      : server.tools;
    if (permitted.length === 0) {
      // 白名单里的工具都已失效（改名 / 删除）：后端不装配这台 Server，预览也不该算挂载
      unmounted.push(server.name);
      continue;
    }
    mountedServers.push(server.name);
    searchableMcpTools += permitted.length;
    for (const tool of permitted) usableNames.add(tool.wire);
    // 白名单未启用时整台继承，不存在「只选了部分」的问题
    if (whitelistActive && permitted.length < server.tools.length) {
      partialServers.push({
        server: server.name,
        selected: permitted.length,
        total: server.tools.length,
      });
    }
  }

  const blockedTools = disallowedTools.filter(
    (key) => usableNames.has(key) && !lockedTools.includes(key),
  ).length;
  const unknownWhitelist = whitelistActive
    ? allowedTools.filter((key) => {
        if (key.startsWith('mcp__')) {
          const server = mcpServerNameFromWire(key);
          const mountedServer = catalog.mcpServers.find((s) => s.name === server);
          return !mountedServer || !mountedServer.tools.some((t) => t.wire === key);
        }
        return !catalog.builtin.some((tool) => tool.name === key);
    })
    : [];

  return {
    contextTools: builtinAlways.length,
    searchableMcpTools,
    builtinTools: builtinAlways.length,
    perTurnTools: perTurn.length,
    skills: mountedSkills.length,
    skillTools: lockedTools.length,
    lockedTools,
    unknownSkills,
    blockedTools,
    whitelistActive,
    whitelistHasMcp,
    mountedServers,
    unmountedServers: unmounted,
    partialServers,
    unknownWhitelist,
    skillDeclaredServers: [...skillDeclaredServers],
  };
}

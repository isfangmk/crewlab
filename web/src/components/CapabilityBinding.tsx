// CapabilityBinding — 助理「能力绑定可视化」。
//
// 三组能力同屏：内置工具 / 技能 / MCP Server。勾选的是运行时真正生效的对象：
//   - MCP 按 Server 筛选挂载，Server 内只把勾选的工具放进检索目录（后端
//     filter_mcp_servers_for_agent + build_searchable_mcp_toolsets 的口径）；
//     未勾选的工具搜不到也调不了
//   - MCP 工具一律不进初始上下文：模型先用 search_tools 检索、再 load_tool 调用
//   - 技能：勾选的才装配；技能声明的工具随技能自动并入白名单且不可屏蔽
//   - 内置工具随 SkillToolset 始终装配，只能进「屏蔽」名单
// 左侧是能力目录，右侧是按运行时口径算出的装配预览；勾选与装配数不一致时逐条说明。
import { useMemo, useState, type ReactNode } from 'react';
import {
  ChevronDown, ChevronRight, Lock, Server, Sparkles, Wrench,
  type LucideIcon,
} from 'lucide-react';

import {
  ErrorBanner, Checkbox, SearchInput,
} from '@/components/ui';
import { Button, Chip } from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';
import {
  previewCapabilities,
  type CapabilityCatalog,
  type McpServerCapability,
  type SkillCapability,
} from '@/lib/capabilityPreview';

type Props = {
  catalog: CapabilityCatalog | null;
  catalogError: string | null;
  allowedTools: string[];
  disallowedTools: string[];
  onAllowedToolsChange(next: string[]): void;
  onDisallowedToolsChange(next: string[]): void;
  /** null = 继承全部技能；数组 = 只装配这些 */
  selectedSkills: string[] | null;
  onSelectedSkillsChange(next: string[] | null): void;
};

type CapabilityGroupKey = 'builtin' | 'skills' | 'mcp';

export function CapabilityBinding({
  catalog,
  catalogError,
  allowedTools,
  disallowedTools,
  onAllowedToolsChange,
  onDisallowedToolsChange,
  selectedSkills,
  onSelectedSkillsChange,
}: Props) {
  const { tr } = useI18n();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showTools, setShowTools] = useState<Record<string, boolean>>({});
  // 内置工具是主任务，默认展开；技能与 MCP 先给摘要，需要时再展开。
  const [openGroups, setOpenGroups] = useState<Record<CapabilityGroupKey, boolean>>({
    builtin: true,
    skills: false,
    mcp: false,
  });
  // 工具、技能、MCP 一次性搜索；输入后只保留匹配项并自动展开所在分组。
  const [query, setQuery] = useState('');

  const preview = useMemo(
    () =>
      catalog === null
        ? null
        : previewCapabilities(catalog, allowedTools, disallowedTools, selectedSkills),
    [catalog, allowedTools, disallowedTools, selectedSkills],
  );

  if (catalogError !== null) {
    return (
      <ErrorBanner>
        {tr('能力目录加载失败：', 'Failed to load capability catalog: ')}
        {catalogError}
      </ErrorBanner>
    );
  }
  if (catalog === null || preview === null) {
    return <div className="text-[11px] text-zinc-500">{tr('加载能力目录中…', 'Loading capability catalog…')}</div>;
  }

  // 白名单为空表示继承全部，交互时先展开成显式列表，语义等价
  const materialize = (): string[] => {
    if (allowedTools.length > 0) return allowedTools;
    return catalog.mcpServers
      .filter((server) => server.enabled && server.status === 'ok')
      .flatMap((server) => server.tools.map((tool) => tool.wire));
  };

  const toggleServer = (server: McpServerCapability, checked: boolean) => {
    const wires = server.tools.map((tool) => tool.wire);
    const base = materialize();
    if (checked) {
      const merged = [...base];
      for (const wire of wires) if (!merged.includes(wire)) merged.push(wire);
      onAllowedToolsChange(merged);
      return;
    }
    onAllowedToolsChange(base.filter((key) => !wires.includes(key)));
  };

  // 屏蔽开关：技能声明的工具不允许屏蔽，这里直接短路（后端同口径会 400）
  const toggleBlocked = (key: string) => {
    if (preview.lockedTools.includes(key)) return;
    onDisallowedToolsChange(
      disallowedTools.includes(key)
        ? disallowedTools.filter((item) => item !== key)
        : [...disallowedTools, key],
    );
  };

  const boundServers = new Set(preview.mountedServers);
  // 技能声明的工具来源：MCP wire 名与内置工具名的集合，用于标注「目录外」
  const knownMcpWires = new Set(catalog.mcpServers.flatMap((server) => server.tools.map((tool) => tool.wire)));
  const knownBuiltin = new Set(catalog.builtin.map((tool) => tool.name));
  const skillToolBinding = (wire: string) => {
    if (wire.startsWith('mcp__')) {
      return knownMcpWires.has(wire) ? (
        <Chip dense tone="accent">mcp</Chip>
      ) : (
        <Chip dense tone="warning">{tr('目录外', 'gone')}</Chip>
      );
    }
    return knownBuiltin.has(wire) ? undefined : <Chip dense tone="warning">{tr('目录外', 'gone')}</Chip>;
  };

  // 白名单为空 = 继承全部 MCP，所以「取消最后一台已挂载的 Server」无法用空数组表达
  const canUnmount = preview.mountedServers.length > 1;

  const safeQuery = query.trim().toLowerCase();
  const includes = (value: string | undefined) =>
    safeQuery === '' || (value ?? '').toLowerCase().includes(safeQuery);
  const builtinMatches = catalog.builtin.filter(
    (tool) => includes(tool.name) || includes(tool.description),
  );
  const skillMatches = catalog.skills.filter((skill) => {
    if (safeQuery === '') return true;
    return (
      includes(skill.key) ||
      includes(skill.description) ||
      skill.tools.some((wire) => includes(wire))
    );
  });
  const mcpMatches = catalog.mcpServers
    .map((server) => {
      const serverMatch = safeQuery === '' || includes(server.name);
      const toolMatches =
        safeQuery === ''
          ? server.tools
          : server.tools.filter((tool) => includes(tool.wire) || includes(tool.description));
      return {
        server,
        tools: serverMatch ? server.tools : toolMatches,
        matched: safeQuery === '' || serverMatch || toolMatches.length > 0,
      };
    })
    .filter((item) => item.matched);
  const matchCount = builtinMatches.length + skillMatches.length + mcpMatches.length;
  const searching = safeQuery !== '';

  const skillChecked = (key: string) => selectedSkills === null || selectedSkills.includes(key);

  // 勾选技能时把其声明的工具并入白名单并解除屏蔽：
  // 技能要靠这些工具工作，「勾了技能但不给工具」必然是坏配置。
  const toggleSkill = (skill: SkillCapability, checked: boolean) => {
    const next = selectedSkills === null
      ? catalog.skills.map((item) => item.key)
      : [...selectedSkills];
    const trimmed = checked
      ? [...next.filter((key) => key !== skill.key), skill.key]
      : next.filter((key) => key !== skill.key);
    onSelectedSkillsChange(trimmed);

    const otherTools = new Set(
      catalog.skills
        .filter((item) => item.key !== skill.key && trimmed.includes(item.key))
        .flatMap((item) => item.tools),
    );
    if (checked && allowedTools.length > 0) {
      const merged = [...allowedTools];
      for (const wire of skill.tools) if (!merged.includes(wire)) merged.push(wire);
      onAllowedToolsChange(merged);
    }
    if (!checked) {
      const dropped = skill.tools.filter((wire) => !otherTools.has(wire));
      if (dropped.length > 0 && allowedTools.length > 0) {
        onAllowedToolsChange(allowedTools.filter((key) => !dropped.includes(key)));
      }
    }
    // 新勾选的技能工具若在屏蔽名单里，直接解除（不可屏蔽）
    if (checked) {
      const conflicts = disallowedTools.filter((key) => skill.tools.includes(key));
      if (conflicts.length > 0) {
        onDisallowedToolsChange(disallowedTools.filter((key) => !conflicts.includes(key)));
      }
    }
  };

  // 预览区的异常与口径说明不能藏起来：白名单、部分挂载、目录外工具都在这里逐条列出。
  const warningItems: ReactNode[] = [];
  if (preview.whitelistActive && !preview.whitelistHasMcp && catalog.mcpServers.length > 0) {
    warningItems.push(
      <li key="no-mcp">{tr('白名单不含任何 MCP 工具，所有 MCP Server 都不会挂载', 'The whitelist has no MCP tool, so no MCP server is mounted')}</li>,
    );
  }
  if (preview.mountedServers.length === 0 && catalog.mcpServers.length === 0) {
    warningItems.push(
      <li key="no-server" className="text-zinc-500">{tr('还没有启用且探测成功的 MCP Server', 'No enabled, verified MCP server yet')}</li>,
    );
  }
  for (const item of preview.partialServers) {
    warningItems.push(
      <li key={`partial-${item.server}`}>
        {tr(
          `${item.server} 勾选 ${item.selected}/${item.total} 个工具（含技能声明）；未勾选的 ${item.total - item.selected} 个不进检索目录，搜不到也调不了`,
          `${item.server} selects ${item.selected}/${item.total} tools (including skill-declared); the other ${item.total - item.selected} are not searchable`,
        )}
      </li>,
    );
  }
  if (preview.skillDeclaredServers.length > 0) {
    warningItems.push(
      <li key="skill-mcp">
        {tr(
          `技能声明的 MCP 工具随技能进入检索目录（仍需 search/load 调用）：${preview.skillDeclaredServers.join('、')}`,
          `Skill-declared MCP tools are searchable with the skill (still need search/load): ${preview.skillDeclaredServers.join(', ')}`,
        )}
      </li>,
    );
  }
  if (preview.unknownSkills.length > 0) {
    warningItems.push(
      <li key="unknown-skills">
        {tr(
          `勾选的技能已不存在：${preview.unknownSkills.join('、')}`,
          `Selected skills no longer exist: ${preview.unknownSkills.join(', ')}`,
        )}
      </li>,
    );
  }
  if (preview.unknownWhitelist.length > 0) {
    warningItems.push(
      <li key="unknown-tools">
        {tr(
          `白名单里 ${preview.unknownWhitelist.length} 个工具已不在目录：${preview.unknownWhitelist.join('、')}`,
          `${preview.unknownWhitelist.length} whitelisted tools are gone from the catalog: ${preview.unknownWhitelist.join(', ')}`,
        )}
      </li>,
    );
  }

  const groupOpen = (key: CapabilityGroupKey) => searching || openGroups[key];
  const toggleGroup = (key: CapabilityGroupKey) => {
    if (searching) return;
    setOpenGroups((cur) => ({ ...cur, [key]: !cur[key] }));
  };

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_17.5rem]">
      <section className="flex h-[26rem] min-h-0 flex-col overflow-hidden rounded-xl border border-zinc-800/60 bg-zinc-900/30">
        <header className="flex flex-wrap items-center gap-2 border-b border-zinc-800/60 px-3.5 py-2.5">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-[12px] font-semibold text-zinc-100">
                {tr('能力目录', 'Capability catalog')}
              </span>
              <span className="text-[10px] text-zinc-500">
                {tr(
                  `${catalog.builtin.length} 内置 · ${catalog.skills.length} 技能 · ${catalog.mcpServers.length} MCP`,
                  `${catalog.builtin.length} built-in · ${catalog.skills.length} skills · ${catalog.mcpServers.length} MCP`,
                )}
              </span>
            </div>
            <p className="mt-0.5 text-[10px] text-zinc-500">
              {tr('勾选后立即生效；搜索只保留匹配项', 'Changes apply immediately; search keeps matches only')}
            </p>
          </div>
          <div className="w-full shrink-0 sm:w-[14.5rem]">
            <SearchInput
              value={query}
              onChange={setQuery}
              placeholder={tr('搜索工具 / 技能 / MCP', 'Search tools / skills / MCP')}
              ariaLabel={tr('搜索能力', 'Search capabilities')}
            />
          </div>
        </header>

        {searching && (
          <div className="border-b border-zinc-800/50 px-3.5 py-1.5 text-[10px] text-zinc-500">
            <span>
              {tr(`匹配 ${matchCount} 项`, `${matchCount} matches`)}
            </span>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
          {searching && matchCount === 0 ? (
            <div className="rounded-lg border border-zinc-800/60 px-3 py-8 text-center text-[11px] text-zinc-500">
              {tr('没有匹配的工具、技能或 MCP Server。', 'No matching tools, skills or MCP servers.')}
            </div>
          ) : (
            <>
              {/* 内置工具 */}
              {(!searching || builtinMatches.length > 0) && (
                <CapabilityGroup
                  title={tr('内置工具', 'Built-in tools')}
                  icon={Wrench}
                  count={builtinMatches.length}
                  status={
                    preview.blockedTools > 0
                      ? tr(`已屏蔽 ${preview.blockedTools}`, `${preview.blockedTools} blocked`)
                      : tr('全部可用', 'all available')
                  }
                  statusTone={preview.blockedTools > 0 ? 'warn' : 'muted'}
                  hint={tr('随技能目录始终装配；联网类工具按对话开关挂载', 'Mounted with the skill catalog; per-turn tools follow the chat toggle')}
                  open={groupOpen('builtin')}
                  lockedOpen={searching}
                  onToggle={() => toggleGroup('builtin')}
                >
                  {builtinMatches.map((tool) => (
                    <ToolRow
                      key={tool.name}
                      name={tool.name}
                      description={tool.description}
                      blocked={disallowedTools.includes(tool.name)}
                      locked={preview.lockedTools.includes(tool.name)}
                      onToggleBlocked={() => toggleBlocked(tool.name)}
                      binding={
                        <span className="shrink-0 text-[9.5px] text-zinc-500">
                          {tool.mount === 'always' ? tr('始终可用', 'always on') : tr('随开关', 'per turn')}
                        </span>
                      }
                    />
                  ))}
                </CapabilityGroup>
              )}

              {/* 技能 */}
              {(!searching || skillMatches.length > 0) && (
                <CapabilityGroup
                  title={tr('技能', 'Skills')}
                  icon={Sparkles}
                  count={skillMatches.length}
                  status={
                    selectedSkills === null
                      ? tr('继承全部', 'inherited')
                      : tr(`已选 ${preview.skills}`, `${preview.skills} selected`)
                  }
                  statusTone={selectedSkills === null ? 'muted' : 'accent'}
                  hint={tr('勾选的技能才装配；技能内的工具随技能自动开启', 'Only checked skills are mounted; their tools open with them')}
                  open={groupOpen('skills')}
                  lockedOpen={searching}
                  onToggle={() => toggleGroup('skills')}
                >
                  {catalog.skills.length === 0 ? (
                    <div className="px-2 py-2 text-[11px] text-zinc-500">
                      {tr('还没有技能，可到「技能」页新建或导入 zip', 'No skills yet — create one or import a zip on the Skills page')}
                    </div>
                  ) : (
                    <>
                      {selectedSkills !== null && (
                        <div className="flex justify-end px-2 pb-1">
                          <button
                            type="button"
                            className="text-[9.5px] text-zinc-500 transition-colors hover:text-zinc-200"
                            onClick={() => onSelectedSkillsChange(null)}
                          >
                            {tr('恢复全部技能', 'Restore all skills')}
                          </button>
                        </div>
                      )}
                      {skillMatches.map((skill) => (
                        <div key={skill.key} className="rounded-md hover:bg-zinc-900/60">
                          <div className="flex items-center gap-2 px-2 py-1.5">
                            <Checkbox
                              checked={skillChecked(skill.key)}
                              onCheckedChange={() => toggleSkill(skill, !skillChecked(skill.key))}
                              className="h-3 w-3 shrink-0"
                              aria-label={tr(`装配技能 ${skill.key}`, `Mount skill ${skill.key}`)}
                            />
                            <span
                              className="w-[10.5rem] shrink-0 truncate text-[11px] text-zinc-200"
                              title={skill.key}
                            >
                              {skill.key}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-500" title={skill.description}>
                              {skill.description}
                            </span>
                            <span className="shrink-0 text-[9.5px] text-zinc-500">
                              {skillChecked(skill.key)
                                ? selectedSkills === null
                                  ? tr('继承全部', 'inherited')
                                  : tr('已装配', 'mounted')
                                : tr('未装配', 'not mounted')}
                            </span>
                            {skill.tools.length > 0 && (
                              <button
                                type="button"
                                className="flex shrink-0 items-center gap-0.5 text-[9.5px] text-zinc-500 hover:text-zinc-300"
                                aria-expanded={showTools[skill.key] === true}
                                onClick={() => setShowTools((cur) => ({ ...cur, [skill.key]: cur[skill.key] !== true }))}
                              >
                                {showTools[skill.key] === true ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                                {tr(`${skill.tools.length} 个工具`, `${skill.tools.length} tools`)}
                              </button>
                            )}
                          </div>
                          {showTools[skill.key] === true && (
                            <div className="mb-1 ml-2 space-y-0.5 border-l border-zinc-800/60 pl-2">
                              {skill.tools.map((wire) => (
                                <ToolRow
                                  key={wire}
                                  name={wire}
                                  blocked={disallowedTools.includes(wire)}
                                  locked={skillChecked(skill.key)}
                                  onToggleBlocked={() => toggleBlocked(wire)}
                                  binding={skillToolBinding(wire)}
                                />
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </>
                  )}
                </CapabilityGroup>
              )}

              {/* MCP Server */}
              {(!searching || mcpMatches.length > 0) && (
                <CapabilityGroup
                  title={tr('MCP Server', 'MCP servers')}
                  icon={Server}
                  count={mcpMatches.length}
                  status={tr(`已挂载 ${preview.mountedServers.length}`, `${preview.mountedServers.length} mounted`)}
                  statusTone={preview.mountedServers.length > 0 ? 'accent' : 'muted'}
                  hint={tr('勾选即整台挂载；工具清单按需展开', 'Check to mount a whole server; expand for tools')}
                  open={groupOpen('mcp')}
                  lockedOpen={searching}
                  onToggle={() => toggleGroup('mcp')}
                >
                  {catalog.mcpServers.length === 0 ? (
                    <div className="px-2 py-2 text-[11px] text-zinc-500">{tr('尚未配置 MCP Server', 'No MCP server configured yet')}</div>
                  ) : (
                    mcpMatches.map(({ server, tools: visibleTools }) => {
                      const ready = server.enabled && server.status === 'ok' && server.tools.length > 0;
                      const bound = ready && boundServers.has(server.name);
                      const lockedBound = bound && !canUnmount;
                      return (
                        <div key={server.name} className="rounded-md hover:bg-zinc-900/60">
                          <div className="flex items-center gap-2 px-2 py-1.5">
                            <Hint
                              content={
                                lockedBound
                                  ? tr('白名单为空等于继承全部 MCP，最后一台已挂载的 Server 不能单独取消', 'An empty whitelist inherits every MCP server, so the last mounted server cannot be unmounted alone')
                                  : tr('勾选即把整台 Server 加入白名单', 'Checking adds the whole server to the whitelist')
                              }
                            >
                              <Checkbox
                                checked={bound}
                                disabled={!ready || lockedBound}
                                onCheckedChange={() => toggleServer(server, !bound)}
                                className="h-3 w-3 shrink-0"
                                aria-label={tr(`挂载 MCP Server ${server.name}`, `Mount MCP server ${server.name}`)}
                              />
                            </Hint>
                            <span className="w-[10.5rem] shrink-0 truncate text-[11px] text-zinc-200" title={`mcp · ${server.name}`}>
                              mcp · {server.name}
                            </span>
                            <Chip dense tone={bound ? 'accent' : 'default'}>
                              {bound ? tr('已挂载', 'mounted') : tr('未挂载', 'not mounted')}
                            </Chip>
                            {!ready && (
                              <Chip dense tone="warning">
                                {server.enabled ? tr('未探测成功', 'not verified') : tr('已停用', 'disabled')}
                              </Chip>
                            )}
                            <span className="ml-auto shrink-0 text-[9.5px] text-zinc-500">
                              {searching
                                ? tr(`匹配 ${visibleTools.length} 个工具`, `${visibleTools.length} matched tools`)
                                : null}
                            </span>
                            {!searching && (
                              <button
                                type="button"
                                className="flex shrink-0 items-center gap-0.5 text-[9.5px] text-zinc-500 hover:text-zinc-300"
                                aria-expanded={expanded[server.name] === true}
                                onClick={() => setExpanded((cur) => ({ ...cur, [server.name]: cur[server.name] !== true }))}
                              >
                                {expanded[server.name] === true ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                                {tr(`${server.tools.length} 个工具`, `${server.tools.length} tools`)}
                              </button>
                            )}
                          </div>
                          {(searching || expanded[server.name] === true) && (
                            <div className="mb-1 ml-2 space-y-0.5 border-l border-zinc-800/60 pl-2">
                              {visibleTools.length === 0 ? (
                                <div className="px-2 py-1 text-[11px] text-zinc-500">{tr('工具缓存为空，请先「测试连接」', 'Empty tool cache — run "Test connection" first')}</div>
                              ) : (
                                visibleTools.map((tool) => (
                                  <ToolRow
                                    key={tool.wire}
                                    name={tool.wire}
                                    description={tool.description}
                                    blocked={disallowedTools.includes(tool.wire)}
                                    locked={preview.lockedTools.includes(tool.wire)}
                                    onToggleBlocked={() => toggleBlocked(tool.wire)}
                                  />
                                ))
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </CapabilityGroup>
              )}
            </>
          )}
        </div>
      </section>

      <aside className="flex h-[26rem] min-h-0 flex-col gap-3 overflow-y-auto rounded-xl border border-zinc-800/60 bg-zinc-900/30 p-3.5">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[12px] font-semibold text-zinc-100">{tr('装配预览', 'Mounted preview')}</span>
          <Chip dense tone="accent">{tr('实时', 'live')}</Chip>
        </div>

        <div>
          <div className="text-[10px] text-zinc-500">{tr('进入初始上下文', 'In initial context')}</div>
          <div className="mt-0.5 flex items-baseline gap-1">
            <span className="text-[24px] font-semibold leading-none tracking-tight text-zinc-100">
              {preview.contextTools}
            </span>
            <span className="text-[10px] text-zinc-500">{tr('个工具', 'tools')}</span>
          </div>
        </div>

        <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 px-3 py-2.5">
          <div className="text-[10px] text-zinc-500">{tr('工具来源', 'Tool sources')}</div>
          <ul className="mt-1.5 space-y-1 text-[10.5px] text-zinc-400">
            <li className="flex items-center gap-1.5">
              <span className="h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
              <span>内置 {preview.builtinTools}</span>
            </li>
            <li className="flex items-center gap-1.5">
              <span className="h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
              <span>技能工具 {preview.skillTools}</span>
            </li>
            <li className="flex items-center gap-1.5">
              <span className="h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
              <span>MCP 可检索 {preview.searchableMcpTools}</span>
            </li>
            {preview.perTurnTools > 0 && (
              <li className="flex items-center gap-1.5">
                <span className="h-1 w-1 shrink-0 rounded-full bg-sky-400" />
                <span>随开关 + {preview.perTurnTools}</span>
              </li>
            )}
            {preview.blockedTools > 0 && (
              <li className="flex items-center gap-1.5">
                <span className="h-1 w-1 shrink-0 rounded-full bg-warn" />
                <span className="text-warn">已屏蔽 {preview.blockedTools}</span>
              </li>
            )}
          </ul>
        </div>

        <div className="min-h-0">
          <div className="text-[10px] text-zinc-500">{tr('已选能力', 'Selected')}</div>
          <div className="mt-1 space-y-0.5">
            <AssignedRow
              label={tr(`内置工具 · ${preview.builtinTools} 个`, `Built-in · ${preview.builtinTools}`)}
              detail={tr('始终装配', 'always mounted')}
            />
            <AssignedRow
              label={tr(`技能 · ${preview.skills} 个`, `Skills · ${preview.skills}`)}
              detail={selectedSkills === null ? tr('继承全部', 'inherited') : tr('指定装配', 'selected')}
            />
            <AssignedRow
              label={
                preview.mountedServers.length > 0
                  ? tr(`MCP · ${preview.mountedServers.join('、')}`, `MCP · ${preview.mountedServers.join(', ')}`)
                  : tr('MCP · 无挂载', 'MCP · none')
              }
              detail={tr('可检索', 'searchable')}
            />
          </div>
        </div>

        {warningItems.length > 0 && (
          <ul className="space-y-1 rounded-lg border border-warn/25 bg-warn/[0.06] px-2.5 py-2 text-[9.5px] leading-4 text-warn">
            {warningItems}
          </ul>
        )}

        <p className="mt-auto text-[9.5px] leading-4 text-zinc-500">
          {tr(
            'MCP 工具不进入初始上下文，模型按需检索；技能工具随技能自动装配。',
            'MCP tools are not in the initial context; the model searches on demand. Skill tools mount with their skill.',
          )}
        </p>

        {preview.whitelistActive && (
          <Button
            variant="ghost"
            size="sm"
            type="button"
            className="justify-start px-2 py-1 text-[10px] text-zinc-500"
            onClick={() => onAllowedToolsChange([])}
          >
            {tr('恢复继承全部 MCP', 'Inherit all MCP servers')}
          </Button>
        )}
      </aside>
    </div>
  );
}

function AssignedRow({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="flex items-center gap-1.5 rounded-md px-1 py-1 text-[10px]">
      <span className="h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
      <span className="min-w-0 flex-1 truncate text-zinc-300" title={label}>{label}</span>
      <span className="shrink-0 text-[9px] text-zinc-600">{detail}</span>
    </div>
  );
}

function CapabilityGroup({
  title,
  icon: Icon,
  count,
  status,
  statusTone,
  hint,
  open,
  lockedOpen,
  onToggle,
  children,
}: {
  title: string;
  icon: LucideIcon;
  count: number;
  status: string;
  statusTone: 'muted' | 'accent' | 'warn';
  hint: string;
  open: boolean;
  lockedOpen: boolean;
  onToggle(): void;
  children: ReactNode;
}) {
  return (
    <section className="border-b border-zinc-800/50 last:border-b-0">
      <div className="flex items-center gap-2 px-2 py-2">
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          disabled={lockedOpen}
          className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-default"
        >
          <ChevronRight
            size={11}
            className={cn('shrink-0 text-zinc-500 transition-transform', open && 'rotate-90')}
          />
          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-indigo-500/10 text-indigo-300">
            <Icon size={12} />
          </span>
          <span className="shrink-0 text-[11.5px] font-medium text-zinc-200">{title}</span>
          <span className="shrink-0 text-[10px] text-zinc-500">{count}</span>
          <span
            className={cn(
              'shrink-0 text-[9.5px]',
              statusTone === 'accent' && 'text-indigo-300',
              statusTone === 'warn' && 'text-warn',
              statusTone === 'muted' && 'text-zinc-500',
            )}
          >
            {status}
          </span>
        </button>
        <span className="ml-auto hidden max-w-[15rem] truncate text-[9.5px] text-zinc-600 lg:block" title={hint}>
          {hint}
        </span>
      </div>
      {open && <div className="pb-1.5 pl-7 pr-1">{children}</div>}
    </section>
  );
}

function ToolRow({
  name,
  description,
  blocked,
  locked,
  binding,
  onToggleBlocked,
}: {
  name: string;
  description?: string;
  blocked: boolean;
  // 技能声明：不可屏蔽
  locked: boolean;
  binding?: ReactNode;
  onToggleBlocked(): void;
}) {
  const { tr } = useI18n();
  const blockButton = (
    <Button
      variant={blocked ? 'dangerGhost' : 'ghost'}
      size="sm"
      type="button"
      disabled={locked}
      className={cn('shrink-0 px-1.5 py-0 text-[9.5px]', !blocked && 'text-zinc-500')}
      aria-pressed={blocked}
      aria-label={
        locked
          ? tr(`${name} 由技能声明，不可屏蔽`, `${name} is declared by a skill and cannot be blocked`)
          : blocked
            ? tr(`取消屏蔽 ${name}`, `Unblock ${name}`)
            : tr(`屏蔽 ${name}`, `Block ${name}`)
      }
      onClick={onToggleBlocked}
    >
      {locked ? (
        <span className="flex items-center gap-0.5">
          <Lock size={9} />
          {tr('随技能', 'with skill')}
        </span>
      ) : blocked ? (
        tr('已屏蔽', 'blocked')
      ) : (
        tr('屏蔽', 'block')
      )}
    </Button>
  );
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1 hover:bg-zinc-900/60">
      <span
        className="w-[10.5rem] shrink-0 truncate text-[11px] text-zinc-200"
        title={name}
      >
        {name}
      </span>
      {description !== undefined && description !== '' ? (
        <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-500" title={description}>
          {description}
        </span>
      ) : (
        <span className="min-w-0 flex-1" />
      )}
      {binding}
      {locked ? (
        <Hint content={tr('技能要靠声明的工具工作，取消勾选技能才能去掉', 'Skills need their declared tools; uncheck the skill to remove them')}>
          {blockButton}
        </Hint>
      ) : (
        blockButton
      )}
    </div>
  );
}

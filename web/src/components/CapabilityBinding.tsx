// CapabilityBinding — 助理「能力绑定可视化」。
//
// 三组可配置能力同屏：内置工具 / 技能 / MCP Server。全部按勾选装配，默认全选：
//   - MCP 按 Server 筛选挂载，Server 内只把勾选的工具放进检索目录（后端
//     filter_mcp_servers_by_tools + build_searchable_mcp_toolsets 的口径）；
//     未勾选的工具搜不到也调不了
//   - MCP 工具一律不进初始上下文：模型先用 search_tools 检索、再 load_tool 调用
//   - 技能：勾选的才装配（后端 skills_service.resolve_skills），能力由正文描述
// 左侧是能力目录，右侧是按运行时口径算出的装配预览；勾选与装配数不一致时逐条说明。
import { useMemo, useState, type ReactNode } from 'react';
import {
  Bot, ChevronDown, ChevronRight, Server, Sparkles,
  type LucideIcon,
} from 'lucide-react';

import {
  ErrorBanner, Checkbox, SearchInput,
} from '@/components/ui';
import { Button, Chip } from '@/components/ui';
import { RiskBadge } from '@/components/RiskBadge';
import type { ToolRisk } from '@/api/tools';
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
  /** 勾选装配的技能 key；空数组 = 不装配任何技能 */
  selectedSkills: string[];
  onSelectedSkillsChange(next: string[]): void;
  /** 勾选的内置工具名；null = 待目录加载初始化（默认全选） */
  selectedBuiltinTools: string[] | null;
  onSelectedBuiltinToolsChange(next: string[]): void;
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
  selectedBuiltinTools,
  onSelectedBuiltinToolsChange,
}: Props) {
  const { tr } = useI18n();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [openGroups, setOpenGroups] = useState<Record<CapabilityGroupKey, boolean>>({
    builtin: false,
    skills: false,
    mcp: false,
  });
  // 工具、技能、MCP 一次性搜索；输入后只保留匹配项并自动展开所在分组。
  const [query, setQuery] = useState('');

  const preview = useMemo(
    () =>
      catalog === null
        ? null
        : previewCapabilities(catalog, allowedTools, disallowedTools, selectedSkills, selectedBuiltinTools),
    [catalog, allowedTools, disallowedTools, selectedSkills, selectedBuiltinTools],
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

  const toggleServer = (server: McpServerCapability, checked: boolean) => {
    const wires = server.tools.map((tool) => tool.wire);
    const base = allowedTools;
    if (checked) {
      const merged = [...base];
      for (const wire of wires) if (!merged.includes(wire)) merged.push(wire);
      onAllowedToolsChange(merged);
      return;
    }
    onAllowedToolsChange(base.filter((key) => !wires.includes(key)));
  };

  const toggleBlocked = (key: string) => {
    onDisallowedToolsChange(
      disallowedTools.includes(key)
        ? disallowedTools.filter((item) => item !== key)
        : [...disallowedTools, key],
    );
  };

  const boundServers = new Set(preview.mountedServers);

  const safeQuery = query.trim().toLowerCase();
  const includes = (value: string | undefined) =>
    safeQuery === '' || (value ?? '').toLowerCase().includes(safeQuery);
  const skillMatches = catalog.skills.filter((skill) => {
    if (safeQuery === '') return true;
    return includes(skill.key) || includes(skill.description);
  });
  const selectableBuiltin = catalog.builtin.filter((tool) => tool.mount === 'always');
  const builtinMatches = selectableBuiltin.filter((tool) =>
    safeQuery === '' || includes(tool.name) || includes(tool.description),
  );
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

  const skillChecked = (key: string) => selectedSkills.includes(key);

  const toggleSkill = (skill: SkillCapability, checked: boolean) => {
    const trimmed = checked
      ? [...selectedSkills.filter((key) => key !== skill.key), skill.key]
      : selectedSkills.filter((key) => key !== skill.key);
    onSelectedSkillsChange(trimmed);
  };

  const chosenBuiltin = selectedBuiltinTools ?? selectableBuiltin.map((tool) => tool.name);
  const builtinChecked = (name: string) => chosenBuiltin.includes(name);
  const toggleBuiltin = (name: string, checked: boolean) => {
    onSelectedBuiltinToolsChange(
      checked
        ? [...chosenBuiltin.filter((item) => item !== name), name]
        : chosenBuiltin.filter((item) => item !== name),
    );
  };

  // 预览区的异常与口径说明不能藏起来：部分挂载、目录外工具都在这里逐条列出。
  const warningItems: ReactNode[] = [];
  if (preview.mountedServers.length === 0 && catalog.mcpServers.length === 0) {
    warningItems.push(
      <li key="no-server" className="text-zinc-500">{tr('还没有启用且探测成功的 MCP Server', 'No enabled, verified MCP server yet')}</li>,
    );
  }
  for (const item of preview.partialServers) {
    warningItems.push(
      <li key={`partial-${item.server}`}>
        {tr(
          `${item.server} 勾选 ${item.selected}/${item.total} 个工具；未勾选的 ${item.total - item.selected} 个不进检索目录，搜不到也调不了`,
          `${item.server} selects ${item.selected}/${item.total} tools; the other ${item.total - item.selected} are not searchable`,
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
                  `${selectableBuiltin.length} 内置 · ${catalog.skills.length} 技能 · ${catalog.mcpServers.length} MCP`,
                  `${selectableBuiltin.length} built-in · ${catalog.skills.length} skills · ${catalog.mcpServers.length} MCP`,
                )}
              </span>
            </div>
            <p className="mt-0.5 text-[10px] text-zinc-500">
              {tr('内置工具默认全选；勾选后保存立即生效', 'Built-in tools are all selected by default; changes apply after saving')}
            </p>
          </div>
          <div className="w-full shrink-0 sm:w-[14.5rem]">
            <SearchInput
              fluid
              value={query}
              onChange={setQuery}
              placeholder={tr('搜索技能 / MCP', 'Search skills / MCP')}
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
              {tr('没有匹配的技能或 MCP Server。', 'No matching skills or MCP servers.')}
            </div>
          ) : (
            <>
              {/* 内置工具 */}
              {(!searching || builtinMatches.length > 0) && (
                <CapabilityGroup
                  title={tr('内置工具', 'Built-in tools')}
                  icon={Bot}
                  count={builtinMatches.length}
                  status={tr(`已选 ${preview.builtinTools}`, `${preview.builtinTools} selected`)}
                  statusTone={preview.builtinTools > 0 ? 'accent' : 'muted'}
                  hint={tr('勾选的内置工具才装配；默认全部勾选', 'Only checked built-in tools are mounted; all selected by default')}
                  open={groupOpen('builtin')}
                  lockedOpen={searching}
                  onToggle={() => toggleGroup('builtin')}
                >
                  {builtinMatches.length === 0 ? (
                    <div className="px-2 py-2 text-[11px] text-zinc-500">
                      {tr('没有匹配的内置工具。', 'No matching built-in tools.')}
                    </div>
                  ) : (
                    builtinMatches.map((tool) => (
                      <div key={tool.name} className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-zinc-900/60">
                        <Checkbox
                          checked={builtinChecked(tool.name)}
                          onCheckedChange={() => toggleBuiltin(tool.name, !builtinChecked(tool.name))}
                          className="h-3 w-3 shrink-0"
                          aria-label={tr(`装配内置工具 ${tool.name}`, `Mount built-in tool ${tool.name}`)}
                        />
                        <span
                          className="w-[10.5rem] shrink-0 truncate text-[11px] text-zinc-200"
                          title={tool.name}
                        >
                          {tool.name}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[10px] text-zinc-500" title={tool.description}>
                          {tool.description}
                        </span>
                        <RiskBadge value={tool.risk} />
                      </div>
                    ))
                  )}
                </CapabilityGroup>
              )}

              {/* 技能 */}
              {(!searching || skillMatches.length > 0) && (
                <CapabilityGroup
                  title={tr('技能', 'Skills')}
                  icon={Sparkles}
                  count={skillMatches.length}
                  status={tr(`已选 ${preview.skills}`, `${preview.skills} selected`)}
                  statusTone={preview.skills > 0 ? 'accent' : 'muted'}
                  hint={tr('勾选的技能才装配；技能自身的能力由正文描述', 'Only checked skills are mounted; their capability is described in the skill body')}
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
                      <div className="flex justify-end px-2 pb-1">
                        <button
                          type="button"
                          className="text-[9.5px] text-zinc-500 transition-colors hover:text-zinc-200"
                          onClick={() => onSelectedSkillsChange(catalog.skills.map((skill) => skill.key))}
                        >
                          {tr('恢复全部技能', 'Restore all skills')}
                        </button>
                      </div>
                      {skillMatches.map((skill) => (
                        <div key={skill.key} className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-zinc-900/60">
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
                            {skillChecked(skill.key) ? tr('已装配', 'mounted') : tr('未装配', 'not mounted')}
                          </span>
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
                      return (
                        <div key={server.name} className="rounded-md hover:bg-zinc-900/60">
                          <div className="flex items-center gap-2 px-2 py-1.5">
                            <Hint content={tr('勾选即把整台 Server 加入白名单', 'Checking adds the whole server to the whitelist')}>
                              <Checkbox
                                checked={bound}
                                disabled={!ready}
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
                                    risk={tool.risk}
                                    blocked={disallowedTools.includes(tool.wire)}
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
              <span>{tr('内置工具', 'Built-in')} {preview.builtinTools}</span>
            </li>
            <li className="flex items-center gap-1.5">
              <span className="h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
              <span>MCP {tr('可检索', 'searchable')} {preview.searchableMcpTools}</span>
            </li>
            {preview.perTurnTools > 0 && (
              <li className="flex items-center gap-1.5">
                <span className="h-1 w-1 shrink-0 rounded-full bg-sky-400" />
                <span>{tr('随开关', 'per turn')} + {preview.perTurnTools}</span>
              </li>
            )}
            {preview.blockedTools > 0 && (
              <li className="flex items-center gap-1.5">
                <span className="h-1 w-1 shrink-0 rounded-full bg-warn" />
                <span className="text-warn">{tr('已屏蔽', 'blocked')} {preview.blockedTools}</span>
              </li>
            )}
          </ul>
        </div>

        <div className="min-h-0">
          <div className="text-[10px] text-zinc-500">{tr('已选能力', 'Selected')}</div>
          <div className="mt-1 space-y-0.5">
            <AssignedRow
              label={tr(`内置工具 · ${preview.builtinTools} 个`, `Built-in · ${preview.builtinTools}`)}
              detail={tr('勾选装配', 'selected')}
            />
            <AssignedRow
              label={tr(`技能 · ${preview.skills} 个`, `Skills · ${preview.skills}`)}
              detail={tr('指定装配', 'selected')}
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
            '内置工具按勾选装配；MCP 工具不进入初始上下文，模型按需检索。',
            'Built-in tools are mounted per selection; MCP tools are not in the initial context and are searched on demand.',
          )}
        </p>

        {preview.mountedServers.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            type="button"
            className="justify-start px-2 py-1 text-[10px] text-zinc-500"
            onClick={() => onAllowedToolsChange([])}
          >
            {tr('清空 MCP 挂载', 'Clear MCP mounts')}
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
  risk,
  blocked,
  onToggleBlocked,
}: {
  name: string;
  description?: string;
  risk?: ToolRisk;
  blocked: boolean;
  onToggleBlocked(): void;
}) {
  const { tr } = useI18n();
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
      {risk !== undefined && <RiskBadge value={risk} />}
      <Button
        variant={blocked ? 'dangerGhost' : 'ghost'}
        size="sm"
        type="button"
        className={cn('shrink-0 px-1.5 py-0 text-[9.5px]', !blocked && 'text-zinc-500')}
        aria-pressed={blocked}
        aria-label={blocked ? tr(`取消屏蔽 ${name}`, `Unblock ${name}`) : tr(`屏蔽 ${name}`, `Block ${name}`)}
        onClick={onToggleBlocked}
      >
        {blocked ? tr('已屏蔽', 'blocked') : tr('屏蔽', 'block')}
      </Button>
    </div>
  );
}

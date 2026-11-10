// CapabilityBinding — 助理「能力绑定可视化」。
//
// 三组能力同屏：内置工具 / 技能 / MCP Server。勾选的是运行时真正生效的对象：
//   - MCP 按 Server 筛选挂载，Server 内只把勾选的工具放进检索目录（后端
//     filter_mcp_servers_for_agent + build_searchable_mcp_toolsets 的口径）；
//     未勾选的工具搜不到也调不了
//   - MCP 工具一律不进初始上下文：模型先用 search_tools 检索、再 load_tool 调用
//   - 技能：勾选的才装配；技能声明的工具随技能自动并入白名单且不可屏蔽
//   - 内置工具随 SkillToolset 始终装配，只能进「屏蔽」名单
// 顶部的预览由 capabilityPreview 按运行时口径算出，勾选与装配数不一致时逐条说明。
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Lock } from 'lucide-react';

import { Checkbox } from '@/components/ui';
import { Button, Chip, Label } from '@/components/ui';
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

  const preview = useMemo(
    () =>
      catalog === null
        ? null
        : previewCapabilities(catalog, allowedTools, disallowedTools, selectedSkills),
    [catalog, allowedTools, disallowedTools, selectedSkills],
  );

  if (catalogError !== null) {
    return (
      <div className="rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-[11px] text-red-300">
        {tr('能力目录加载失败：', 'Failed to load capability catalog: ')}
        {catalogError}
      </div>
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

  return (
    <div className="space-y-3">
      {preview.whitelistActive && (
        <div className="flex items-center justify-end">
          <Button
            variant="ghost"
            size="sm"
            type="button"
            className="px-2 py-0.5 text-[11px]"
            onClick={() => onAllowedToolsChange([])}
          >
            {tr('恢复继承全部', 'Inherit all')}
          </Button>
        </div>
      )}

      {/* 装配预览：MCP 工具只进检索目录，不进上下文 */}
      <div className="rounded-md border border-indigo-500/30 bg-indigo-500/5 px-3 py-2">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-[12px] text-zinc-300">{tr('进上下文', 'In context')}</span>
          <span className="text-[15px] font-semibold text-indigo-200">{preview.contextTools}</span>
          <span className="text-[12px] text-zinc-300">{tr('个工具', 'tools')}</span>
          <span className="ml-auto flex flex-wrap items-center gap-1">
            <Hint
              content={tr(
                'MCP 工具不进初始上下文：模型用 search_tools 检索、load_tool 加载后调用',
                'MCP tools are not injected: the model finds them with search_tools and calls load_tool',
              )}
            >
              <Chip dense>{tr(`MCP 可检索 ${preview.searchableMcpTools}`, `MCP searchable ${preview.searchableMcpTools}`)}</Chip>
            </Hint>
            <Chip dense>{tr(`内置 ${preview.builtinTools}`, `builtin ${preview.builtinTools}`)}</Chip>
            <Chip dense>{tr(`技能 ${preview.skills}`, `skills ${preview.skills}`)}</Chip>
            {preview.skillTools > 0 && (
              <Hint
                content={tr(
                  '技能声明的工具随技能装配，且不可单独屏蔽',
                  'Tools declared by skills are mounted with the skill and cannot be blocked',
                )}
              >
                <Chip dense tone="accent">{tr(`技能工具 ${preview.skillTools}`, `skill tools ${preview.skillTools}`)}</Chip>
              </Hint>
            )}
            {preview.perTurnTools > 0 && (
              <Hint content={tr('由对话开关（如联网）在本回合临时挂载', 'Mounted per turn when the chat toggle is on')}>
                <Chip dense tone="info">{tr(`随开关 +${preview.perTurnTools}`, `per-turn +${preview.perTurnTools}`)}</Chip>
              </Hint>
            )}
            {preview.blockedTools > 0 && (
              <Chip dense tone="warning">{tr(`已屏蔽 ${preview.blockedTools}`, `blocked ${preview.blockedTools}`)}</Chip>
            )}
          </span>
        </div>
        <ul className="mt-1.5 space-y-0.5 text-[11px] leading-5 text-amber-300">
          {preview.whitelistActive && !preview.whitelistHasMcp && catalog.mcpServers.length > 0 && (
            <li>{tr('白名单不含任何 MCP 工具，所有 MCP Server 都不会挂载', 'The whitelist has no MCP tool, so no MCP server is mounted')}</li>
          )}
          {preview.mountedServers.length === 0 && catalog.mcpServers.length === 0 && (
            <li className="text-zinc-500">{tr('还没有启用且探测成功的 MCP Server', 'No enabled, verified MCP server yet')}</li>
          )}
          {preview.partialServers.map((item) => (
            <li key={item.server}>
              {tr(
                `${item.server} 勾选 ${item.selected}/${item.total} 个工具（含技能声明）；未勾选的 ${item.total - item.selected} 个不进检索目录，搜不到也调不了`,
                `${item.server} selects ${item.selected}/${item.total} tools (including skill-declared); the other ${item.total - item.selected} are not searchable`,
              )}
            </li>
          ))}
          {preview.skillDeclaredServers.length > 0 && (
            <li>
              {tr(
                `技能声明的 MCP 工具随技能进入检索目录（仍需 search/load 调用）：${preview.skillDeclaredServers.join('、')}`,
                `Skill-declared MCP tools are searchable with the skill (still need search/load): ${preview.skillDeclaredServers.join(', ')}`,
              )}
            </li>
          )}
          {preview.unknownSkills.length > 0 && (
            <li>
              {tr(
                `勾选的技能已不存在：${preview.unknownSkills.join('、')}`,
                `Selected skills no longer exist: ${preview.unknownSkills.join(', ')}`,
              )}
            </li>
          )}
          {preview.unknownWhitelist.length > 0 && (
            <li>
              {tr(
                `白名单里 ${preview.unknownWhitelist.length} 个工具已不在目录：${preview.unknownWhitelist.join('、')}`,
                `${preview.unknownWhitelist.length} whitelisted tools are gone from the catalog: ${preview.unknownWhitelist.join(', ')}`,
              )}
            </li>
          )}
        </ul>
      </div>

      {/* 内置工具 */}
      <CapabilityGroup
        title={tr('内置工具', 'Built-in tools')}
        count={catalog.builtin.length}
        hint={tr('随技能目录始终装配；联网类工具按对话开关挂载', 'Always mounted with the skill catalog; per-turn tools follow the chat toggle')}
      >
        {catalog.builtin.map((tool) => (
          <ToolRow
            key={tool.name}
            name={tool.name}
            description={tool.description}
            blocked={disallowedTools.includes(tool.name)}
            locked={preview.lockedTools.includes(tool.name)}
            onToggleBlocked={() => toggleBlocked(tool.name)}
            binding={
              <span className="shrink-0 text-[10px] text-zinc-500">
                {tool.mount === 'always' ? tr('始终可用', 'always on') : tr('随开关', 'per turn')}
              </span>
            }
          />
        ))}
      </CapabilityGroup>

      {/* 技能 */}
      <CapabilityGroup
        title={tr('技能', 'Skills')}
        count={catalog.skills.length}
        hint={tr('勾选的技能才装配；技能内的工具随技能自动开启', 'Only checked skills are mounted; their tools open with them')}
        actions={
          <span className="flex items-center gap-1" role="group" aria-label={tr('技能装配范围', 'Skill scope')}>
            <Button
              size="sm"
              variant="ghost"
              aria-pressed={selectedSkills === null}
              aria-label={tr('装配全部技能', 'Mount every skill')}
              className={cn('h-6 px-2 text-[10px]', selectedSkills === null && 'text-zinc-100')}
              onClick={() => onSelectedSkillsChange(null)}
            >
              {tr('全部', 'All')}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              aria-pressed={selectedSkills !== null}
              aria-label={tr('只装配勾选的技能', 'Mount only checked skills')}
              className={cn('h-6 px-2 text-[10px]', selectedSkills !== null && 'text-zinc-100')}
              onClick={() => onSelectedSkillsChange([])}
            >
              {tr('指定', 'Selected')}
            </Button>
          </span>
        }
      >
        {catalog.skills.length === 0 && (
          <div className="px-1.5 py-1 text-[11px] text-zinc-500">
            {tr('还没有技能，可到「技能」页新建或导入 zip', 'No skills yet — create one or import a zip on the Skills page')}
          </div>
        )}
        {catalog.skills.map((skill) => (
          <div key={skill.key} className="rounded px-1.5 py-1 hover:bg-zinc-900/60">
            <div className="flex items-center gap-1.5">
              {selectedSkills !== null && (
                <Checkbox
                  checked={skillChecked(skill.key)}
                  onCheckedChange={() => toggleSkill(skill, !skillChecked(skill.key))}
                  className="h-3 w-3"
                  aria-label={tr(`装配技能 ${skill.key}`, `Mount skill ${skill.key}`)}
                />
              )}
              <span className="truncate font-mono text-[11px] text-zinc-200">{skill.key}</span>
              <span className="shrink-0 text-[10px] text-zinc-500">
                {selectedSkills === null
                  ? tr('继承全部', 'inherited')
                  : skillChecked(skill.key)
                    ? tr('已装配', 'mounted')
                    : tr('未装配', 'not mounted')}
              </span>
              {skill.tools.length > 0 && (
                <button
                  type="button"
                  className="ml-auto flex items-center gap-0.5 text-[10px] text-zinc-500 hover:text-zinc-300"
                  aria-expanded={showTools[skill.key] === true}
                  onClick={() => setShowTools((cur) => ({ ...cur, [skill.key]: cur[skill.key] !== true }))}
                >
                  {showTools[skill.key] === true ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                  {tr(`${skill.tools.length} 个工具`, `${skill.tools.length} tools`)}
                </button>
              )}
            </div>
            {showTools[skill.key] === true && (
              <div className="mt-1 space-y-1 border-l border-zinc-800/60 pl-2">
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
      </CapabilityGroup>

      {/* MCP Server */}
      {catalog.mcpServers.map((server) => {
        const ready = server.enabled && server.status === 'ok' && server.tools.length > 0;
        const bound = ready && boundServers.has(server.name);
        const lockedBound = bound && !canUnmount;
        return (
          <section key={server.name} className="rounded-md border border-zinc-800 bg-zinc-950/40">
            <div className="flex items-center gap-2 px-2 py-1.5">
              <Label className="flex min-w-0 items-center gap-1.5">
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
                    className="h-3 w-3"
                    aria-label={tr(`挂载 MCP Server ${server.name}`, `Mount MCP server ${server.name}`)}
                  />
                </Hint>
                <span className="truncate font-mono text-[11px] text-zinc-200">mcp · {server.name}</span>
              </Label>
              <Chip dense tone={bound ? 'accent' : 'default'}>
                {bound ? tr('已挂载', 'mounted') : tr('未挂载', 'not mounted')}
              </Chip>
              {!ready && (
                <Chip dense tone="warning">
                  {server.enabled ? tr('未探测成功', 'not verified') : tr('已停用', 'disabled')}
                </Chip>
              )}
              <button
                type="button"
                className="ml-auto flex items-center gap-0.5 text-[10px] text-zinc-500 hover:text-zinc-300"
                aria-expanded={expanded[server.name] === true}
                onClick={() => setExpanded((cur) => ({ ...cur, [server.name]: cur[server.name] !== true }))}
              >
                {expanded[server.name] === true ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                {tr(`${server.tools.length} 个工具`, `${server.tools.length} tools`)}
              </button>
            </div>
            {expanded[server.name] === true && (
              <div className="max-h-56 space-y-0.5 overflow-y-auto border-t border-zinc-800/60 px-2 py-1.5">
                {server.tools.length === 0 ? (
                  <div className="text-[11px] text-zinc-500">{tr('工具缓存为空，请先「测试连接」', 'Empty tool cache — run "Test connection" first')}</div>
                ) : (
                  server.tools.map((tool) => (
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
          </section>
        );
      })}

      {catalog.mcpServers.length === 0 && (
        <div className="text-[11px] text-zinc-500">{tr('尚未配置 MCP Server', 'No MCP server configured yet')}</div>
      )}
    </div>
  );
}

function CapabilityGroup({
  title,
  count,
  hint,
  actions,
  children,
}: {
  title: string;
  count: number;
  hint: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-md border border-zinc-800 bg-zinc-950/40">
      <div className="flex items-baseline gap-1.5 border-b border-zinc-800/60 px-2 py-1.5">
        <span className="text-[11px] font-medium text-zinc-300">{title}</span>
        <span className="text-[10px] text-zinc-600">({count})</span>
        {actions}
        <span className="ml-auto truncate pl-2 text-[10px] text-zinc-600">{hint}</span>
      </div>
      <div className="space-y-0.5 px-1 py-1.5">{children}</div>
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
  binding?: React.ReactNode;
  onToggleBlocked(): void;
}) {
  const { tr } = useI18n();
  const label = (
    <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-300">{name}</span>
  );
  const blockButton = (
    <Button
      variant={blocked ? 'dangerGhost' : 'ghost'}
      size="sm"
      type="button"
      disabled={locked}
      className={cn('shrink-0 px-1.5 py-0 text-[10px]', !blocked && 'text-zinc-500')}
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
    <div className="flex items-center gap-1.5 rounded px-1.5 py-1 hover:bg-zinc-900/60">
      {description !== undefined && description !== '' ? <Hint content={description}>{label}</Hint> : label}
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

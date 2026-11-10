// capabilityPreview 测试 — 助理能力绑定的「实际可见」必须与后端运行时口径一致。
import { describe, expect, it } from 'vitest';

import {
  mcpServerNameFromWire,
  previewCapabilities,
  type CapabilityCatalog,
} from './capabilityPreview';

function catalog(): CapabilityCatalog {
  return {
    builtin: [
      { name: 'query_knowledge', description: '检索知识库', mount: 'always' },
      { name: 'web_search', description: '联网搜索', mount: 'per_turn' },
    ],
    skills: [{ key: 'knowledge-search', description: '知识检索', tools: ['query_knowledge'] }],
    mcpServers: [
      {
        name: 'ai-engine',
        enabled: true,
        status: 'ok',
        tools: [
          { wire: 'mcp__ai-engine__ask', description: 'ask' },
          { wire: 'mcp__ai-engine__rerank', description: 'rerank' },
        ],
      },
      {
        name: 'observability',
        enabled: true,
        status: 'ok',
        tools: [{ wire: 'mcp__observability__query_metrics', description: 'query' }],
      },
      {
        name: 'never-probed',
        enabled: true,
        status: 'error',
        tools: [{ wire: 'mcp__never-probed__ping', description: 'ping' }],
      },
      {
        name: 'disabled-one',
        enabled: false,
        status: 'ok',
        tools: [{ wire: 'mcp__disabled-one__run', description: 'run' }],
      },
    ],
  };
}

describe('mcpServerNameFromWire', () => {
  it('按后端口径解析 server 段', () => {
    expect(mcpServerNameFromWire('mcp__ai-engine__ask')).toBe('ai-engine');
  });

  it('非法工具名显式失败', () => {
    expect(() => mcpServerNameFromWire('query_knowledge')).toThrow('非法 MCP 工具名');
    expect(() => mcpServerNameFromWire('mcp__ai-engine__')).toThrow('非法 MCP 工具名');
    expect(() => mcpServerNameFromWire('mcp__a__b__c')).toThrow('非法 MCP 工具名');
  });
});

describe('previewCapabilities', () => {
  it('白名单为空时继承全部可用 Server，未探测成功/停用的不计入', () => {
    const preview = previewCapabilities(catalog(), [], []);

    expect(preview.whitelistActive).toBe(false);
    expect(preview.mountedServers).toEqual(['ai-engine', 'observability']);
    expect(preview.unmountedServers).toEqual(['never-probed', 'disabled-one']);
    expect(preview.mcpTools).toBe(3);
    // 始终装配的内置工具只有 query_knowledge；web_search 由对话开关决定
    expect(preview.builtinTools).toBe(1);
    expect(preview.perTurnTools).toBe(1);
    expect(preview.visibleTools).toBe(4);
    expect(preview.skills).toBe(1);
    expect(preview.partialServers).toEqual([]);
  });

  it('白名单只认出现过的 MCP Server', () => {
    const preview = previewCapabilities(
      catalog(),
      ['mcp__observability__query_metrics'],
      [],
    );

    expect(preview.whitelistActive).toBe(true);
    expect(preview.whitelistHasMcp).toBe(true);
    expect(preview.mountedServers).toEqual(['observability']);
    expect(preview.mcpTools).toBe(1);
    expect(preview.visibleTools).toBe(2);
    // 只勾了 server 内 1 个工具时整台挂载，提示口径差异
    expect(preview.partialServers).toEqual([]);
  });

  it('白名单只选部分工具时提示整台挂载', () => {
    const preview = previewCapabilities(catalog(), ['mcp__ai-engine__ask'], []);
    expect(preview.partialServers).toEqual([{ server: 'ai-engine', selected: 1, total: 2 }]);
    expect(preview.mcpTools).toBe(2);
  });

  it('白名单不含 MCP 工具时所有 Server 都不挂载', () => {
    const preview = previewCapabilities(catalog(), ['query_knowledge'], []);

    expect(preview.whitelistHasMcp).toBe(false);
    expect(preview.mountedServers).toEqual([]);
    expect(preview.mcpTools).toBe(0);
    expect(preview.visibleTools).toBe(1);
    expect(preview.partialServers).toEqual([]);
  });

  it('禁用名单只统计当前可见、且不是技能声明的工具', () => {
    const preview = previewCapabilities(
      catalog(),
      [],
      ['mcp__ai-engine__ask', 'mcp__observability__query_metrics', 'query_knowledge', 'mcp__never-probed__ping'],
    );

    // never-probed 未挂载，不计入已屏蔽；
    // query_knowledge 由 knowledge-search 技能声明，随技能装配且不可屏蔽，也不计入
    expect(preview.blockedTools).toBe(2);
    expect(preview.lockedTools).toEqual(['query_knowledge']);
  });

  it('技能勾选：null 继承全部，数组只装配勾选的技能', () => {
    const all = previewCapabilities(catalog(), [], [], null);
    expect(all.skills).toBe(1);
    expect(all.skillTools).toBe(1);

    const none = previewCapabilities(catalog(), [], [], []);
    expect(none.skills).toBe(0);
    expect(none.skillTools).toBe(0);
    // 没有装配技能时，技能工具不再锁定，可以正常屏蔽
    expect(none.lockedTools).toEqual([]);
    expect(previewCapabilities(catalog(), [], ['query_knowledge'], []).blockedTools).toBe(1);
  });

  it('勾选了目录里已不存在的技能时单独暴露', () => {
    const preview = previewCapabilities(catalog(), [], [], ['knowledge-search', 'gone-skill']);
    expect(preview.skills).toBe(1);
    expect(preview.unknownSkills).toEqual(['gone-skill']);
  });

  it('技能声明的 MCP 工具会额外挂载对应 Server', () => {
    const withSkill: CapabilityCatalog = {
      ...catalog(),
      // 技能除了内置工具还声明了一个 MCP 工具
      skills: [
        {
          key: 'metrics-skill',
          description: '指标排查',
          tools: ['query_knowledge', 'mcp__observability__query_metrics'],
        },
      ],
    };

    // 白名单只有内置工具：按 Server 筛选会把 observability 排除，
    // 但技能声明了它的工具，运行时会自动并入白名单，预览必须算进来。
    const preview = previewCapabilities(withSkill, ['query_knowledge'], []);

    expect(preview.whitelistHasMcp).toBe(true);
    expect(preview.mountedServers).toEqual(['observability']);
    expect(preview.skillDeclaredServers).toEqual(['observability']);
    expect(preview.lockedTools).toContain('mcp__observability__query_metrics');
    // 该 Server 只有一个工具且已由技能声明，不存在「只选了部分」的口径差异
    expect(preview.partialServers).toEqual([]);
    expect(preview.mcpTools).toBe(1);
  });

  it('未装配技能时，声明带来的 Server 不再挂载', () => {
    const withSkill: CapabilityCatalog = {
      ...catalog(),
      skills: [{ key: 'metrics-skill', description: '指标排查', tools: ['mcp__observability__query_metrics'] }],
    };

    const preview = previewCapabilities(withSkill, ['query_knowledge'], [], []);
    expect(preview.mountedServers).toEqual([]);
    expect(preview.skillDeclaredServers).toEqual([]);
    expect(preview.lockedTools).toEqual([]);
  });

  it('白名单里残留的失效工具单独暴露', () => {
    const preview = previewCapabilities(
      catalog(),
      ['mcp__ai-engine__removed', 'mcp__gone__ask', 'no_such_tool'],
      [],
    );

    expect(preview.unknownWhitelist).toEqual([
      'mcp__ai-engine__removed',
      'mcp__gone__ask',
      'no_such_tool',
    ]);
    // 失效工具仍按 Server 段挂载整台 ai-engine
    expect(preview.mountedServers).toEqual(['ai-engine']);
  });
});

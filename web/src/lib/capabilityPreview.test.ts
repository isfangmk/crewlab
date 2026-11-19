// capabilityPreview 测试 — 助理能力绑定的「实际可见」必须与后端运行时口径一致。
import { describe, expect, it } from 'vitest';

import {
  mcpServerNameFromWire,
  previewCapabilities,
} from './capabilityPreview';

function catalog() {
  return {
    builtin: [
      { name: 'query_knowledge', description: '检索知识库', risk: 'safe' as const, mount: 'always' as const },
      { name: 'web_search', description: '联网搜索', risk: 'safe' as const, mount: 'per_turn' as const },
    ],
    skills: [{ key: 'knowledge-search', description: '知识检索' }],
    mcpServers: [
      {
        name: 'ai-engine',
        enabled: true,
        status: 'ok',
        tools: [
          { wire: 'mcp__ai-engine__ask', description: 'ask', risk: 'safe' as const },
          { wire: 'mcp__ai-engine__rerank', description: 'rerank', risk: 'safe' as const },
        ],
      },
      {
        name: 'observability',
        enabled: true,
        status: 'ok',
        tools: [{ wire: 'mcp__observability__query_metrics', description: 'query', risk: 'safe' as const }],
      },
      {
        name: 'never-probed',
        enabled: true,
        status: 'error',
        tools: [{ wire: 'mcp__never-probed__ping', description: 'ping', risk: 'mutating' as const }],
      },
      {
        name: 'disabled-one',
        enabled: false,
        status: 'ok',
        tools: [{ wire: 'mcp__disabled-one__run', description: 'run', risk: 'mutating' as const }],
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
  it('白名单为空时不挂任何 Server', () => {
    const preview = previewCapabilities(catalog(), [], []);

    expect(preview.mountedServers).toEqual([]);
    expect(preview.unmountedServers).toEqual(['ai-engine', 'observability', 'never-probed', 'disabled-one']);
    expect(preview.searchableMcpTools).toBe(0);
    expect(preview.builtinTools).toBe(1);
    expect(preview.perTurnTools).toBe(1);
    expect(preview.contextTools).toBe(1);
    expect(preview.skills).toBe(1);
    expect(preview.partialServers).toEqual([]);
  });

  it('白名单只认出现过的 MCP Server', () => {
    const preview = previewCapabilities(
      catalog(),
      ['mcp__observability__query_metrics'],
      [],
    );

    expect(preview.whitelistHasMcp).toBe(true);
    expect(preview.mountedServers).toEqual(['observability']);
    expect(preview.searchableMcpTools).toBe(1);
    expect(preview.contextTools).toBe(1);
    // 该 Server 只有一个工具且已勾选，不存在「只选了部分」的口径差异
    expect(preview.partialServers).toEqual([]);
  });

  it('白名单只选部分工具时：未勾选的工具不进检索目录', () => {
    const preview = previewCapabilities(catalog(), ['mcp__ai-engine__ask'], []);
    expect(preview.partialServers).toEqual([{ server: 'ai-engine', selected: 1, total: 2 }]);
    // 未勾选的 rerank 搜不到；勾选的 ask 也仍要靠 search/load
    expect(preview.searchableMcpTools).toBe(1);
    expect(preview.contextTools).toBe(1);
  });

  it('白名单不含 MCP 工具时所有 Server 都不挂载', () => {
    const preview = previewCapabilities(catalog(), ['query_knowledge'], []);

    expect(preview.whitelistHasMcp).toBe(false);
    expect(preview.mountedServers).toEqual([]);
    expect(preview.searchableMcpTools).toBe(0);
    expect(preview.contextTools).toBe(1);
    expect(preview.partialServers).toEqual([]);
  });

  it('禁用名单只统计当前挂载的工具', () => {
    // 空白名单不挂任何 MCP；勾选一台后只有它的工具计入已屏蔽
    const preview = previewCapabilities(
      catalog(),
      ['mcp__ai-engine__ask'],
      ['mcp__ai-engine__ask', 'mcp__observability__query_metrics', 'query_knowledge', 'mcp__never-probed__ping'],
    );

    // 只有挂载的 ai-engine 工具计入；其余未挂载或内置常驻的不计
    expect(preview.blockedTools).toBe(1);
  });

  it('技能勾选：null 继承全部，数组只装配勾选的技能', () => {
    const all = previewCapabilities(catalog(), [], [], null);
    expect(all.skills).toBe(1);

    const none = previewCapabilities(catalog(), [], [], []);
    expect(none.skills).toBe(0);
  });

  it('勾选了目录里已不存在的技能时单独暴露', () => {
    const preview = previewCapabilities(catalog(), [], [], ['knowledge-search', 'gone-skill']);
    expect(preview.skills).toBe(1);
    expect(preview.unknownSkills).toEqual(['gone-skill']);
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
    // 白名单里的工具全部失效：后端不装配这台 Server，预览也不该算挂载
    expect(preview.mountedServers).toEqual([]);
    expect(preview.searchableMcpTools).toBe(0);
  });
});

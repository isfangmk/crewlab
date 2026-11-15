import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MessageBubble, type ConfigDraftResult } from './MessageBubble';
import type { ChatMessage, ToolCallSummary } from '@/api/chat';
import { getApproval, rejectApproval, streamApproveApproval, type Approval } from '@/api/approvals';
import { setLocale } from '@/i18n/locale';
import * as chatApi from '@/api/chat';

vi.mock('@/api/approvals', () => ({
  getApproval: vi.fn(),
  approveApproval: vi.fn(),
  streamApproveApproval: vi.fn(),
  rejectApproval: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  setLocale('zh-CN');
});

describe('MessageBubble image preview', () => {
  it('opens a protected message image on click', async () => {
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(chatApi, 'loadChatAttachment').mockResolvedValue('blob:protected-image');
    render(<MessageBubble sessionId="session-1" message={{
      id: 'message-1',
      role: 'user',
      content: '',
      attachments: [{ id: 'attachment-1', name: 'diagram.png', mime_type: 'image/png', size: 123 }],
    }} />);

    const thumbnail = await screen.findByAltText('diagram.png');
    fireEvent.click(thumbnail);
    expect(await screen.findByAltText('Preview diagram.png')).toBeInTheDocument();
  });
});

describe('MessageBubble shared approval card', () => {
  it.each([
    ['zh-CN', '需要你确认才能执行 capture_pcap'],
    ['en-US', 'Needs your approval to run capture_pcap'],
  ] as const)('renders a generic tool proposal in %s', async (locale, heading) => {
    setLocale(locale);
    vi.mocked(getApproval).mockResolvedValue({
      id: 'approval-1',
      kind: 'agent_tool',
      title: 'capture_pcap confirmation',
      summary: 'capture_pcap {"device_id":1,"interface":"eth0"}',
      payload: JSON.stringify({
        tool_name: 'capture_pcap',
        summary: 'capture_pcap {"device_id":1,"interface":"eth0"}',
        arguments: { device_id: 1, interface: 'eth0' },
      }),
      source: 'agent',
      session_id: 'session-1',
      status: 'pending',
      proposed_by: 1,
      created_at: new Date().toISOString(),
    });

    render(<MessageBubble message={{
      id: 'tool-card-approval',
      role: 'tool',
      kind: 'tool_card',
      tool_call: {
        id: 'call-1',
        name: 'capture_pcap',
        status: 'pending',
        result: {
          status: 'pending_approval',
          approval_id: 'approval-1',
          kind: 'agent_tool',
          tool_name: 'capture_pcap',
          command: 'capture_pcap {"device_id":1,"interface":"eth0"}',
        },
      },
    }} />);

    expect(await screen.findByText(heading)).toBeInTheDocument();
    // 卡片正文展示结构化实参（tool + arguments），不是 ADK 的 hint 原文
    expect(screen.getAllByText(/capture_pcap/).length).toBeGreaterThan(0);
    expect(screen.getByText(/"device_id": 1/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: locale === 'zh-CN' ? '批准并执行' : 'Approve & run' })).toBeInTheDocument();
  });

  it.each([
    ['批准并在本会话中信任此工具', 'tool'],
    ['批准并在本会话中信任所有工具', 'all'],
  ] as const)('批准下拉「%s」随确认帧提交免审范围', async (itemLabel, trustScope) => {
    setLocale('zh-CN');
    const approval = {
      id: 'approval-1',
      kind: 'agent_tool',
      title: 'capture_pcap confirmation',
      summary: 'capture_pcap {"device_id":1,"interface":"eth0"}',
      payload: JSON.stringify({
        tool_name: 'capture_pcap',
        summary: 'capture_pcap {"device_id":1,"interface":"eth0"}',
        arguments: { device_id: 1, interface: 'eth0' },
      }),
      source: 'agent',
      session_id: 'session-1',
      status: 'pending',
      proposed_by: 1,
      created_at: new Date().toISOString(),
    } as unknown as Approval;
    vi.mocked(getApproval).mockResolvedValue(approval);
    vi.mocked(streamApproveApproval).mockResolvedValue({ ...approval, status: 'executed' });

    render(<MessageBubble message={{
      id: 'tool-card-approval',
      role: 'tool',
      kind: 'tool_card',
      tool_call: {
        id: 'call-1',
        name: 'capture_pcap',
        status: 'pending',
        result: {
          status: 'pending_approval',
          approval_id: 'approval-1',
          kind: 'agent_tool',
          tool_name: 'capture_pcap',
          command: 'capture_pcap {"device_id":1,"interface":"eth0"}',
        },
      },
    }} />);

    await userEvent.click(await screen.findByRole('button', { name: '批准选项' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: itemLabel }));

    await waitFor(() =>
      expect(streamApproveApproval).toHaveBeenCalledWith(
        'approval-1',
        '',
        expect.any(Object),
        { trustScope },
      ),
    );
  });

  it('主按钮「批准并执行」仅本次批准，不携带免审范围', async () => {
    setLocale('zh-CN');
    const approval = {
      id: 'approval-1',
      kind: 'agent_tool',
      title: 'capture_pcap confirmation',
      summary: 'capture_pcap {"device_id":1,"interface":"eth0"}',
      payload: JSON.stringify({
        tool_name: 'capture_pcap',
        summary: 'capture_pcap {"device_id":1,"interface":"eth0"}',
        arguments: { device_id: 1, interface: 'eth0' },
      }),
      source: 'agent',
      session_id: 'session-1',
      status: 'pending',
      proposed_by: 1,
      created_at: new Date().toISOString(),
    } as unknown as Approval;
    vi.mocked(getApproval).mockResolvedValue(approval);
    vi.mocked(streamApproveApproval).mockResolvedValue({ ...approval, status: 'executed' });

    render(<MessageBubble message={{
      id: 'tool-card-approval',
      role: 'tool',
      kind: 'tool_card',
      tool_call: {
        id: 'call-1',
        name: 'capture_pcap',
        status: 'pending',
        result: {
          status: 'pending_approval',
          approval_id: 'approval-1',
          kind: 'agent_tool',
          tool_name: 'capture_pcap',
          command: 'capture_pcap {"device_id":1,"interface":"eth0"}',
        },
      },
    }} />);

    await userEvent.click(await screen.findByRole('button', { name: '批准并执行' }));

    await waitFor(() =>
      expect(streamApproveApproval).toHaveBeenCalledWith(
        'approval-1',
        '',
        expect.any(Object),
        {},
      ),
    );
    expect(streamApproveApproval).not.toHaveBeenCalledWith(
      'approval-1',
      '',
      expect.any(Object),
      expect.objectContaining({ trustScope: expect.anything() }),
    );
  });
});

const supportedKinds = [
  'metric_threshold',
  'metric_raw',
  'metric_anomaly',
  'metric_forecast',
  'metric_burn_rate',
  'log_match',
  'log_volume',
  'trace_latency',
  'trace_error_rate',
];

function draftFor(kind: string): ConfigDraftResult {
  return {
    kind: 'config_draft',
    domain: 'alert_rule',
    action: 'create',
    summary: `Create ${kind} rule`,
    payload: {
      action: 'create',
      rule: {
        rule_key: `${kind}_natural_language`,
        kind,
        name: `${kind} from natural language`,
        severity: 'warning',
        spec: specFor(kind),
      },
    },
    preview: {
      fire_count: 2,
      samples: [{ summary: `${kind} sample` }],
    },
    warnings: [`${kind} preview warning`],
    scope: {
      type: 'host',
      label: '主机级',
      reason: '命中后会关联到具体设备。',
      change_hint: '如果要改成全局汇总，可以回复“改成全局”。',
    },
    confirmation_prompt:
      '当前告警范围：主机级。命中后会关联到具体设备。如果要改成全局汇总，可以回复“改成全局”。确认无误后可点击确认应用或回复“ok”。',
    rollback: 'Disable or edit the rule from Alerts.',
    apply_tool: 'apply_config_change',
    draft_hash: `sha256:${kind}`,
  };
}

function specFor(kind: string): Record<string, unknown> {
  switch (kind) {
    case 'metric_raw':
      return {
        expr: '(100 * max(redis_memory_used_bytes) / clamp_min(max(redis_memory_max_bytes), 1)) > 80',
      };
    case 'metric_anomaly':
      return { metric: 'cpu_pct', method: 'zscore', baseline_window: '1h', deviation: 3 };
    case 'metric_forecast':
      return { metric: 'disk_avail_bytes', predict_seconds: 21600, operator: '<=', threshold: 0 };
    case 'metric_burn_rate':
      return {
        sli: 'sum(rate(http_requests_total{code!~"5.."}[$window])) / sum(rate(http_requests_total[$window]))',
        slo: 99.9,
        burns: [{ window: '1h', multiplier: 14.4 }],
      };
    case 'log_match':
      return { stream_selector: '{ongrid_source=~"journald:.+"}', line_filter: '(?i)error|panic' };
    case 'log_volume':
      return { stream_selector: '{ongrid_source=~".+"}', ratio_op: '>=', ratio_threshold: 2 };
    case 'trace_latency':
      return { service: 'checkout', quantile: 'p95', threshold_ms: 500 };
    case 'trace_error_rate':
      return { service: 'checkout', operator: '>=', threshold_pct: 1 };
    default:
      return {};
  }
}

function toolCardMessage(draft: ConfigDraftResult): ChatMessage {
  return {
    id: `tool-card-${draft.summary}`,
    role: 'tool',
    kind: 'tool_card',
    tool_call: {
      id: `call-${draft.summary}`,
      name: 'draft_config_change',
      status: 'success',
      result: draft,
    },
  };
}

describe('MessageBubble config draft card', () => {
  it('compacts persisted config confirmation user payloads', () => {
    const longConfirmation = [
      '确认应用这个配置草案。',
      'domain: alert_rule',
      'action: create',
      'draft_hash: sha256:test',
      'apply_tool: apply_config_change',
      '请调用 apply_config_change，传 confirmed=true、domain=alert_rule、action=create、上方 draft_hash 和下方原始 payload，创建这条告警规则；不要改写 payload。',
      'payload:',
      '```json',
      JSON.stringify({
        action: 'create',
        rule: {
          rule_key: 'system_disk_pressure_v2',
          kind: 'metric_raw',
        },
      }, null, 2),
      '```',
    ].join('\n');

    render(<MessageBubble message={{ id: 'user-confirmation', role: 'user', content: longConfirmation }} />);

    expect(screen.getByText('确认创建这条告警规则')).toBeInTheDocument();
    expect(screen.queryByText(/draft_hash/)).not.toBeInTheDocument();
    expect(screen.queryByText(/system_disk_pressure_v2/)).not.toBeInTheDocument();
  });

  it('keeps ordinary user messages unchanged', () => {
    render(<MessageBubble message={{ id: 'user-normal', role: 'user', content: '创建一个 CPU 告警' }} />);

    expect(screen.getByText('创建一个 CPU 告警')).toBeInTheDocument();
  });

  it.each(supportedKinds)('renders and confirms %s drafts', async (kind) => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const draft = draftFor(kind);

    render(<MessageBubble message={toolCardMessage(draft)} onConfirmConfigDraft={onConfirm} />);

    expect(screen.getByText(`Create ${kind} rule`)).toBeInTheDocument();
    expect(screen.getByText('范围：主机级')).toBeInTheDocument();
    expect(screen.getByText(/当前告警范围：主机级/)).toBeInTheDocument();
    expect(screen.getByText(
      `action: create · rule_key: ${kind}_natural_language · kind: ${kind} · name: ${kind} from natural language · severity: warning`,
    )).toBeInTheDocument();
    expect(screen.getByText('Preview fire_count=2')).toBeInTheDocument();
    expect(screen.getByText(`${kind} preview warning`)).toBeInTheDocument();
    expect(screen.getByText('Disable or edit the rule from Alerts.')).toBeInTheDocument();

    await act(async () => {
      await user.click(screen.getByRole('button', { name: /确认应用|Apply/ }));
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith(draft);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /已确认|Confirmed/ })).toBeDisabled();
    });
  });

  it('cancels without calling confirm', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    render(<MessageBubble message={toolCardMessage(draftFor('metric_raw'))} onConfirmConfigDraft={onConfirm} />);
    await act(async () => {
      await user.click(screen.getByRole('button', { name: /取消|Cancel/ }));
    });

    expect(onConfirm).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /已取消|Cancelled/ })).toBeDisabled();
    });
  });

  it('allows retry when confirm fails', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockResolvedValue(false);

    render(<MessageBubble message={toolCardMessage(draftFor('metric_raw'))} onConfirmConfigDraft={onConfirm} />);
    await act(async () => {
      await user.click(screen.getByRole('button', { name: /确认应用|Apply/ }));
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /确认应用|Apply/ })).toBeEnabled();
    });
    expect(screen.queryByRole('button', { name: /已确认|Confirmed/ })).not.toBeInTheDocument();
  });

  it('does not render a config draft card for unsupported config domains', () => {
    const draft = {
      ...draftFor('metric_raw'),
      domain: 'notification_channel',
      summary: 'Create notification channel',
    } as ConfigDraftResult;

    render(<MessageBubble message={toolCardMessage(draft)} onConfirmConfigDraft={vi.fn()} />);

    expect(screen.queryByText('Create notification channel')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认应用|Apply/ })).not.toBeInTheDocument();
  });

  it('renders a persisted tool result string as a draft card', () => {
    const draft = draftFor('metric_raw');
    const message: ChatMessage = {
      id: 'persisted-tool-result',
      role: 'tool',
      tool_name: 'draft_config_change',
      content: JSON.stringify(draft),
    };

    render(<MessageBubble message={message} onConfirmConfigDraft={vi.fn()} />);

    expect(screen.getByText('Create metric_raw rule')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认应用|Apply/ })).toBeInTheDocument();
  });

  it('renders the generic proposal envelope on a persisted draft', () => {
    const draft = {
      ...draftFor('metric_raw'),
      proposal: {
        kind: 'proposal' as const,
        type: 'config_change',
        state: 'pending_confirmation',
        title: 'Create metric_raw rule',
        actions: [
          { kind: 'confirm', label: 'Confirm', enabled: true },
          { kind: 'cancel', label: 'Cancel', enabled: true },
        ],
      },
    };

    render(<MessageBubble message={toolCardMessage(draft)} onConfirmConfigDraft={vi.fn()} />);

    expect(screen.getByText(/提案|Proposal/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认应用|Apply/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /取消|Cancel/ })).toBeInTheDocument();
  });
});

describe('MessageBubble 引用来源', () => {
  const knowledgeCall: ToolCallSummary = {
    id: 'call-knowledge',
    name: 'query_knowledge',
    status: 'success',
    result: {
      items: [
        {
          doc_id: 'doc-1',
          title: '雷达校准手册',
          path: '运维/手册',
          chunk_index: 0,
          score: 0.031,
          excerpt: '校准前先做零点漂移检查。',
        },
      ],
      total: 1,
    },
  };

  it('检索结果在卡外独立区，卡片只留折叠的输入/输出', () => {
    render(<MessageBubble message={{
      id: 'tool-card-knowledge',
      role: 'tool',
      kind: 'tool_card',
      tool_call: knowledgeCall,
    }} />);

    // 检索结果是卡片外的独立区块，无需展开工具卡
    expect(screen.getByRole('region', { name: '搜索结果' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '雷达校准手册' })).toHaveAttribute('href', '/knowledge?doc=doc-1');
    expect(screen.getByText('校准前先做零点漂移检查。')).toBeInTheDocument();
    // 折叠区未展开，不渲染原始结果 JSON
    expect(screen.queryByText('结果')).not.toBeInTheDocument();
  });

  it('助手气泡把多次检索的结果收口成一块', () => {
    render(<MessageBubble message={{
      id: 'assistant-with-sources',
      role: 'assistant',
      content: '按手册第 1 条执行 [1]。',
      tool_calls: [
        knowledgeCall,
        {
          id: 'call-web',
          name: 'web_search',
          status: 'success',
          result: { results: [{ title: '官方文档', url: 'https://example.com/doc', snippet: '接口说明' }] },
        },
      ],
    }} />);

    expect(screen.getAllByRole('region', { name: '搜索结果' })).toHaveLength(1);
    expect(screen.getByRole('link', { name: '雷达校准手册' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '官方文档' })).toBeInTheDocument();
  });
});

describe('MessageBubble message actions', () => {
  it('copies a user message and reports success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });

    render(<MessageBubble message={{ id: 'user-1', role: 'user', content: '用户正文' }} />);

    fireEvent.click(screen.getByRole('button', { name: '复制' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('用户正文'));
    expect(await screen.findByRole('status')).toHaveTextContent('已复制');
  });

  it('does not render a copy button under assistant replies', () => {
    render(<MessageBubble message={{ id: 'assistant-1', role: 'assistant', content: '回答正文' }} />);

    expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument();
  });

  it('edits a user message and hands the new text to the caller', () => {
    const onEditResend = vi.fn();
    const message: ChatMessage = {
      id: 'user-1',
      role: 'user',
      content: '旧问题',
      attachments: [{ id: 'att-1', name: 'a.png', mime_type: 'image/png', size: 1 }],
    };
    render(<MessageBubble message={message} onEditResend={onEditResend} />);

    fireEvent.click(screen.getByRole('button', { name: '编辑并重发' }));
    const box = screen.getByRole('textbox', { name: '编辑消息' });
    fireEvent.change(box, { target: { value: '新问题' } });
    fireEvent.click(screen.getByRole('button', { name: '保存并重发' }));

    expect(onEditResend).toHaveBeenCalledTimes(1);
    expect(onEditResend).toHaveBeenCalledWith(message, '新问题');
  });

  it('regenerates and forks from a user message', () => {
    const onRegenerate = vi.fn();
    const onFork = vi.fn();
    const message: ChatMessage = { id: 'user-1', role: 'user', content: '用户提问' };
    render(<MessageBubble message={message} onRegenerate={onRegenerate} onFork={onFork} />);

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
    fireEvent.click(screen.getByRole('button', { name: '从这条消息分叉' }));

    expect(onRegenerate).toHaveBeenCalledWith(message);
    expect(onFork).toHaveBeenCalledWith(message);
  });

  it('hides regenerate/fork while a turn is in flight', () => {
    render(
      <MessageBubble
        message={{ id: 'user-1', role: 'user', content: '用户提问' }}
        actionsDisabled
        onRegenerate={vi.fn()}
        onFork={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '从这条消息分叉' })).toBeDisabled();
  });
});

function askCardMessage(result: Record<string, unknown>): ChatMessage {
  return {
    id: 'tool-card-ask',
    role: 'tool',
    kind: 'tool_card',
    tool_call: {
      id: 'call-ask',
      name: 'ask_user',
      status: 'pending',
      arguments: { question: '你想预订周几？', options: ['周一', '周三', '周五'] },
      result,
    },
  };
}

function pendingAskApproval(payloadExtra?: Record<string, unknown>): Approval {
  return {
    id: 'approval-ask',
    kind: 'user_input',
    title: '需要你的输入',
    summary: '需要你的输入',
    payload: JSON.stringify({
      tool_name: 'ask_user',
      arguments: { question: '你想预订周几？', options: ['周一', '周三', '周五'] },
      ...payloadExtra,
    }),
    source: 'chat',
    session_id: 'session-1',
    status: 'pending',
    proposed_by: 1,
    created_at: new Date().toISOString(),
  };
}

describe('MessageBubble 用户澄清卡', () => {
  it('渲染提问与候选，提交时把回答交给审批续跑', async () => {
    vi.mocked(getApproval).mockResolvedValue(pendingAskApproval());
    vi.mocked(streamApproveApproval).mockResolvedValue({
      ...pendingAskApproval({ answer: { selected: '周三', values: {}, text: '' } }),
      status: 'executed',
      result: '{"selected":"周三","values":{},"text":""}',
    });

    render(<MessageBubble message={askCardMessage({
      status: 'pending_input',
      approval_id: 'approval-ask',
      kind: 'user_input',
      tool_name: 'ask_user',
      question: '你想预订周几？',
      options: ['周一', '周三', '周五'],
      fields: [],
    })} />);

    expect(await screen.findByText('你想预订周几？')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '周三' }));
    await userEvent.click(screen.getByRole('button', { name: '提交' }));

    expect(streamApproveApproval).toHaveBeenCalledWith(
      'approval-ask',
      '',
      expect.any(Object),
      { answer: { selected: '周三', values: {}, text: '' } },
    );
    expect(await screen.findByText('你的回答')).toBeInTheDocument();
    expect(screen.getByText('选择：周三')).toBeInTheDocument();
  });

  it('未选候选、必填字段为空时不提交', async () => {
    vi.mocked(getApproval).mockResolvedValue({
      ...pendingAskApproval(),
      payload: JSON.stringify({
        tool_name: 'ask_user',
        arguments: {
          question: '填写会议信息',
          fields: [{ key: 'title', label: '会议主题', required: true }],
        },
      }),
    });

    render(<MessageBubble message={askCardMessage({
      status: 'pending_input',
      approval_id: 'approval-ask',
      kind: 'user_input',
      tool_name: 'ask_user',
      question: '填写会议信息',
      options: [],
      fields: [{ key: 'title', label: '会议主题', required: true }],
    })} />);

    await userEvent.click(await screen.findByRole('button', { name: '提交' }));
    expect(await screen.findByText('请填写「会议主题」')).toBeInTheDocument();
    expect(streamApproveApproval).not.toHaveBeenCalled();
  });

  it('纯文本模式必填且回传 text', async () => {
    vi.mocked(getApproval).mockResolvedValue({
      ...pendingAskApproval(),
      payload: JSON.stringify({
        tool_name: 'ask_user',
        arguments: { question: '还有什么要补充？' },
      }),
    });
    vi.mocked(streamApproveApproval).mockResolvedValue({
      ...pendingAskApproval({ answer: { selected: null, values: {}, text: '加一份沙拉' } }),
      status: 'executed',
    });

    render(<MessageBubble message={askCardMessage({
      status: 'pending_input',
      approval_id: 'approval-ask',
      kind: 'user_input',
      tool_name: 'ask_user',
      question: '还有什么要补充？',
      options: [],
      fields: [],
    })} />);

    const box = await screen.findByPlaceholderText('输入你的回答…');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(await screen.findByText('请填写回答')).toBeInTheDocument();

    await userEvent.type(box, '加一份沙拉');
    await userEvent.click(screen.getByRole('button', { name: '提交' }));
    expect(streamApproveApproval).toHaveBeenCalledWith(
      'approval-ask',
      '',
      expect.any(Object),
      { answer: { selected: null, values: {}, text: '加一份沙拉' } },
    );
  });

  it('跳过走拒绝通道，已答态回显历史回答', async () => {
    vi.mocked(getApproval).mockResolvedValue({
      ...pendingAskApproval({ answer: { selected: '周一', values: {}, text: '' } }),
      status: 'executed',
      result: '{"selected":"周一","values":{},"text":""}',
    });

    render(<MessageBubble message={askCardMessage({
      status: 'pending_input',
      approval_id: 'approval-ask',
      kind: 'user_input',
      tool_name: 'ask_user',
      question: '你想预订周几？',
      options: ['周一', '周三', '周五'],
      fields: [],
    })} />);

    // 已答态：只读回显，不再有提交按钮
    expect(await screen.findByText('你的回答')).toBeInTheDocument();
    expect(screen.getByText('选择：周一')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交' })).not.toBeInTheDocument();

    vi.mocked(getApproval).mockResolvedValue(pendingAskApproval());
    vi.mocked(rejectApproval).mockResolvedValue({ ok: true });
    const { unmount } = render(<MessageBubble message={askCardMessage({
      status: 'pending_input',
      approval_id: 'approval-ask',
      kind: 'user_input',
      tool_name: 'ask_user',
      question: '你想预订周几？',
      options: ['周一', '周三', '周五'],
      fields: [],
    })} />);
    await userEvent.click(await screen.findByRole('button', { name: '跳过' }));
    await waitFor(() => expect(rejectApproval).toHaveBeenCalledWith('approval-ask', '用户跳过该问题'));
    unmount();
  });
});

describe('MessageBubble 正文围栏', () => {
  it('echarts 围栏不做图表特判，按普通代码块渲染（图表只走产出物通道）', () => {
    render(<MessageBubble message={{
      id: 'm-fence',
      role: 'assistant',
      content: '```echarts\n{"series": []}\n```',
    }} />);
    // 原文可见，且不会出现图表占位
    expect(screen.getByText(/"series"/)).toBeInTheDocument();
    expect(screen.queryByText('图表生成中…')).not.toBeInTheDocument();
  });
});

describe('MessageBubble 建议问题与回答统计', () => {
  const contentWithFollowups = '这是回答。\n\n```suggestions\n查上周的工时\n导出为表格\n```';

  it('剥离建议块正文并渲染可点建议组', async () => {
    const onUseSuggestion = vi.fn();
    render(<MessageBubble
      message={{ id: 'a1', role: 'assistant', content: contentWithFollowups }}
      followupsActive
      onUseSuggestion={onUseSuggestion}
    />);
    expect(screen.getByText('这是回答。')).toBeInTheDocument();
    // 建议块是元数据：不进正文
    expect(screen.queryByText(/```suggestions/)).not.toBeInTheDocument();
    expect(screen.getByText('猜你想问')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '查上周的工时' }));
    expect(onUseSuggestion).toHaveBeenCalledWith('查上周的工时');
  });

  it('点中的选项高亮「已提问」，其余整组失效；历史回答整组禁用', async () => {
    const onUseSuggestion = vi.fn();
    const { rerender } = render(<MessageBubble
      message={{ id: 'a1', role: 'assistant', content: contentWithFollowups }}
      followupsActive
      onUseSuggestion={onUseSuggestion}
    />);
    await userEvent.click(screen.getByRole('button', { name: '查上周的工时' }));
    // 被点的那条可见「已提问」标记，一眼看出选了哪个
    expect(screen.getByText('已提问')).toBeInTheDocument();
    // 点过的与未点过的都不能再点
    expect(screen.getByRole('button', { name: /查上周的工时/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: '导出为表格' })).toBeDisabled();
    // 历史回答（会话已推进）：整组禁用
    rerender(<MessageBubble
      message={{ id: 'a1', role: 'assistant', content: contentWithFollowups }}
      followupsActive={false}
      onUseSuggestion={onUseSuggestion}
    />);
    expect(screen.getByRole('button', { name: /查上周的工时/ })).toBeDisabled();
  });

  it('流式中建议随写随出（半行不闪现），回合收口前禁点', () => {
    const onUseSuggestion = vi.fn();
    const base = { id: 'a1', role: 'assistant' as const };
    const { rerender } = render(<MessageBubble
      message={{ ...base, content: '这是回答。\n\n```suggestions\n查上周的工时\n', pending: true }}
      followupsActive
      onUseSuggestion={onUseSuggestion}
    />);
    // 第一行写完即出第一条；回合未收口，禁点
    expect(screen.getByRole('button', { name: '查上周的工时' })).toBeDisabled();
    // 正在写的半行不闪现
    rerender(<MessageBubble
      message={{ ...base, content: '这是回答。\n\n```suggestions\n查上周的工时\n导出为表', pending: true }}
      followupsActive
      onUseSuggestion={onUseSuggestion}
    />);
    expect(screen.queryByText('导出为表')).not.toBeInTheDocument();
    expect(screen.getAllByText('查上周的工时')).toHaveLength(1);
    // 围栏闭合、回合收口：两条都出，且可点
    rerender(<MessageBubble
      message={{ ...base, content: '这是回答。\n\n```suggestions\n查上周的工时\n导出为表格\n```' }}
      followupsActive
      onUseSuggestion={onUseSuggestion}
    />);
    expect(screen.getByRole('button', { name: '查上周的工时' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '导出为表格' })).toBeInTheDocument();
  });

  it('右下角渲染耗时与 token 输入/输出拆分', () => {
    render(<MessageBubble message={{
      id: 'a2',
      role: 'assistant',
      content: '好',
      stats: { duration_ms: 3210, prompt_tokens: 900, completion_tokens: 334, total_tokens: 1234 },
    }} />);
    expect(screen.getByText(/耗时 3\.2s/)).toBeInTheDocument();
    expect(screen.getByText(/输入 900 \/ 输出 334 tokens/)).toBeInTheDocument();
    // 总量放悬浮提示
    expect(screen.getByTitle('总计 1,234 tokens')).toBeInTheDocument();
  });

  it('无统计不渲染统计行（禁止假 0）', () => {
    render(<MessageBubble message={{ id: 'a3', role: 'assistant', content: '好' }} />);
    expect(screen.queryByText(/tokens/)).not.toBeInTheDocument();
    expect(screen.queryByText(/耗时/)).not.toBeInTheDocument();
  });
});

describe('MessageBubble 失败工具原始错误', () => {
  it('折叠态露出错误预览，展开后「原始错误」独立成区且与结果分开', async () => {
    render(<MessageBubble message={{
      id: 'tool-card-fail',
      role: 'tool',
      kind: 'tool_card',
      tool_call: {
        id: 'call-fail',
        name: 'query_metric',
        status: 'error',
        error: 'query_metric 调用失败',
        duration_ms: 80,
        arguments: { q: 'cpu' },
        result: {
          ok: false,
          error: 'connection refused',
          traceback: 'Traceback (most recent call last):\n  File "x.py", line 1',
          rows: 3,
        },
      },
    }} />);

    // 折叠态就有错误预览，不用先展开才知道为什么失败
    expect(screen.getByText(/query_metric 调用失败/)).toBeInTheDocument();
    expect(screen.getByText('80ms')).toBeInTheDocument();
    expect(screen.queryByText('原始错误')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '工具调用 query_metric' }));

    expect(screen.getByText('原始错误')).toBeInTheDocument();
    // call.error 与 result 的 error/traceback 合并进原始错误区（预览条 + 展开区两处；
    // 结果块里 JSON 转义的 \"x.py\" 不匹配带真实引号的片段）
    const rawBlocks = screen.getAllByText(/File "x\.py", line 1/);
    expect(rawBlocks).toHaveLength(2);
    for (const block of rawBlocks) {
      expect(block.textContent).toContain('query_metric 调用失败');
      expect(block.textContent).toContain('connection refused');
      expect(block.textContent).toContain('Traceback (most recent call last)');
    }
    // 结果块独立存在，不与原始错误混排
    expect(screen.getByText('结果')).toBeInTheDocument();
    expect(screen.getByText(/"rows": 3/)).toBeInTheDocument();
  });

  it('只有 call.error（结果是字符串）也进原始错误区', async () => {
    render(<MessageBubble message={{
      id: 'tool-card-fail-2',
      role: 'tool',
      kind: 'tool_card',
      tool_call: {
        id: 'call-fail-2',
        name: 'run_cmd',
        status: 'error',
        error: 'exit 1',
        result: 'command output',
      },
    }} />);

    expect(screen.getByText('exit 1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '工具调用 run_cmd' }));
    expect(screen.getByText('原始错误')).toBeInTheDocument();
    expect(screen.getByText('command output')).toBeInTheDocument();
  });

  it('成功工具没有错误预览与原始错误区', () => {
    render(<MessageBubble message={{
      id: 'tool-card-ok',
      role: 'tool',
      kind: 'tool_card',
      tool_call: {
        id: 'call-ok',
        name: 'query_metric',
        status: 'success',
        result: { ok: true },
      },
    }} />);

    expect(screen.queryByText('原始错误')).not.toBeInTheDocument();
    expect(screen.queryByText(/connection refused/)).not.toBeInTheDocument();
  });

  it('历史降级行从结果记录恢复错误与耗时', () => {
    render(<MessageBubble message={{
      id: 'legacy-tool',
      role: 'tool',
      tool_name: 'run_cmd',
      content: JSON.stringify({ ok: false, error: 'exit 1', duration_ms: 42 }),
    }} />);

    expect(screen.getByText('run_cmd')).toBeInTheDocument();
    expect(screen.getByText('42ms')).toBeInTheDocument();
    expect(screen.getByText(/exit 1/)).toBeInTheDocument();
  });
});

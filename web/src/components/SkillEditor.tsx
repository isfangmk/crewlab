// SkillEditor — 技能包在线编辑（SKILL.md 全套资源）。
//
// 对齐 ADK 的目录结构：SKILL.md（frontmatter + 正文）+ references/ assets/ scripts/。
// 编辑保存走整包覆盖：资源按 [{path, text|base64}] 全量提交，避免删改不同步。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FilePlus2, FileUp, Plus, Trash2 } from 'lucide-react';
import { errorText } from '@/lib/errors';

import { Modal } from '@/components/Modal';
import {
  Button,
  Chip,
  DetailSection,
  Input,
  Label,
  Select,
  Textarea,
} from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { ApiError } from '@/api/client';
import { listMcpServers, parseToolsCache } from '@/api/mcp';
import { listBuiltinTools } from '@/api/tools';
import { mcpToolWireName } from '@/lib/mcpWire';
import {
  SKILL_CLASSES,
  createSkill,
  getSkill,
  importSkillZip,
  parseSkillZip,
  updateSkill,
  type SkillClass,
  type SkillPayloadInput,
  type SkillSummary,
  type SkillZipPreview,
} from '@/api/skills';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';

/** 资源目录类型：三类目录的语义不同，编辑器要分开呈现。 */
type ResourceKind = 'references' | 'assets' | 'scripts';

type FileDraft = {
  path: string;
  text: string;
  base64: string;
};

/**
 * 可声明的工具选项：内置工具与 MCP 工具同列。
 * group 用 `builtin` / `mcp:<server>`，groupLabel 只用于分组标题；
 * available=false 表示 MCP Server 停用或探测失败（声明仍可保存）。
 */
type ToolOption = {
  name: string;
  description: string;
  group: string;
  groupLabel: string;
  available: boolean;
};

const RESOURCE_SECTIONS: {
  kind: ResourceKind;
  title: { zh: string; en: string };
  hint: { zh: string; en: string };
  binary: boolean;
}[] = [
  {
    kind: 'references',
    title: { zh: '参考资料 references/', en: 'References references/' },
    hint: {
      zh: '补充文档、示例、SOP；模型用 load_skill_resource 按需读取',
      en: 'Supporting docs, examples, SOPs; read on demand via load_skill_resource',
    },
    binary: false,
  },
  {
    kind: 'assets',
    title: { zh: '素材 assets/', en: 'Assets assets/' },
    hint: {
      zh: '模板、图片等数据文件；二进制会以 base64 存储',
      en: 'Templates, images and other data files; binaries are stored as base64',
    },
    binary: true,
  },
  {
    kind: 'scripts',
    title: { zh: '脚本 scripts/', en: 'Scripts scripts/' },
    hint: {
      zh: '必须是 UTF-8 文本；当前平台未接沙箱，只能被读取，不能执行',
      en: 'Must be UTF-8 text; no sandbox yet, so scripts can be read but not executed',
    },
    binary: false,
  },
];

function emptyDraft(): FileDraft {
  return { path: '', text: '', base64: '' };
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      const comma = result.indexOf(',');
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(new Error('读取文件失败'));
    reader.readAsDataURL(file);
  });
}

export function SkillEditor({
  mode,
  existingKey,
  onClose,
  onSaved,
}: {
  mode: 'create' | 'edit';
  existingKey?: string;
  onClose: () => void;
  onSaved: (skill: SkillSummary, mode: 'create' | 'edit') => void;
}) {
  const { tr } = useI18n();
  const [key, setKey] = useState('');
  const [description, setDescription] = useState('');
  const [risk, setRisk] = useState<SkillClass>('safe');
  const [category, setCategory] = useState('');
  const [license, setLicense] = useState('');
  const [compatibility, setCompatibility] = useState('');
  const [instructions, setInstructions] = useState('');
  const [tools, setTools] = useState<string[]>([]);
  const [injectState, setInjectState] = useState(false);
  const [files, setFiles] = useState<Record<ResourceKind, FileDraft[]>>({
    references: [],
    assets: [],
    scripts: [],
  });
  const [knownTools, setKnownTools] = useState<ToolOption[]>([]);
  const [toolQuery, setToolQuery] = useState('');
  const [loading, setLoading] = useState(mode === 'edit');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const assetInputRef = useRef<HTMLInputElement>(null);
  const assetTarget = useRef<ResourceKind>('assets');

  // 可声明的工具 = 内置工具 + 各 MCP Server 探测缓存里的工具（wire 名）。
  // 未探测成功的 Server 不参与（没有工具缓存），其已声明项在渲染时单列「目录外」。
  useEffect(() => {
    void Promise.all([listBuiltinTools(), listMcpServers()])
      .then(([builtins, servers]) => {
        const options: ToolOption[] = builtins.items.map((tool) => ({
          name: tool.name,
          description: tool.description,
          group: 'builtin',
          groupLabel: '内置工具',
          available: true,
        }));
        for (const server of servers) {
          if (server.tools_cache_json === '') continue;
          const available = server.enabled && server.status === 'ok';
          for (const tool of parseToolsCache(server.tools_cache_json)) {
            options.push({
              name: mcpToolWireName(server.name, tool.name),
              description: tool.description,
              group: `mcp:${server.name}`,
              groupLabel: `mcp · ${server.name}`,
              available,
            });
          }
        }
        setKnownTools(options);
      })
      .catch((e: Error) => setErr(e.message));
  }, []);

  useEffect(() => {
    if (mode !== 'edit') return;
    if (existingKey === undefined || existingKey === '') {
      setErr('编辑模式缺少技能 key');
      setLoading(false);
      return;
    }
    let cancelled = false;
    void getSkill(existingKey)
      .then((skill) => {
        if (cancelled) return;
        setKey(skill.key);
        setDescription(skill.description);
        setRisk(skill.class);
        setCategory(skill.category ?? '');
        setLicense(skill.license ?? '');
        setCompatibility(skill.compatibility ?? '');
        setInstructions(skill.instructions ?? '');
        setTools(skill.tools ?? []);
        setInjectState(skill.inject_state === true);
        setFiles({
          references: (skill.references ?? []).map((f) => ({ path: f.path, text: f.text ?? '', base64: '' })),
          assets: (skill.assets ?? []).map((f) => ({ path: f.path, text: f.text ?? '', base64: f.base64 ?? '' })),
          scripts: (skill.scripts ?? []).map((f) => ({ path: f.path, text: f.text ?? '', base64: '' })),
        });
      })
      .catch((e: Error) => setErr(e instanceof ApiError ? e.message : e.message))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, existingKey]);

  const updateFile = useCallback((kind: ResourceKind, index: number, patch: Partial<FileDraft>) => {
    setFiles((cur) => {
      const next = cur[kind].map((item, i) => (i === index ? { ...item, ...patch } : item));
      return { ...cur, [kind]: next };
    });
  }, []);

  const addFile = useCallback((kind: ResourceKind) => {
    setFiles((cur) => ({ ...cur, [kind]: [...cur[kind], emptyDraft()] }));
  }, []);

  const removeFile = useCallback((kind: ResourceKind, index: number) => {
    setFiles((cur) => ({ ...cur, [kind]: cur[kind].filter((_, i) => i !== index) }));
  }, []);

  const pickAssetFiles = useCallback(async (picked: FileList | null, kind: ResourceKind) => {
    if (picked === null || picked.length === 0) return;
    const drafts: FileDraft[] = [];
    for (const file of Array.from(picked)) {
      const isText = file.type === '' || file.type.startsWith('text/') || file.type === 'application/json';
      if (isText) {
        const text = await file.text();
        drafts.push({ path: file.name, text, base64: '' });
      } else {
        drafts.push({ path: file.name, text: '', base64: await readAsBase64(file) });
      }
    }
    setFiles((cur) => ({ ...cur, [kind]: [...cur[kind], ...drafts] }));
  }, []);

  const payload = useMemo((): SkillPayloadInput => {
    const toWire = (kind: ResourceKind) =>
      files[kind].map((item) => {
        if (item.path.trim() === '') throw new Error(`${kind} 里有文件缺少路径`);
        return item.base64 !== ''
          ? { path: item.path.trim(), base64: item.base64 }
          : { path: item.path.trim(), text: item.text };
      });
    return {
      key: key.trim(),
      description: description.trim(),
      instructions,
      license: license.trim(),
      compatibility: compatibility.trim(),
      preapproved_tools: '',
      risk,
      category: category.trim(),
      tools,
      inject_state: injectState,
      references: toWire('references'),
      assets: toWire('assets'),
      scripts: toWire('scripts'),
    };
  }, [key, description, instructions, license, compatibility, risk, category, tools, injectState, files]);

  const submit = async () => {
    setErr(null);
    setSubmitting(true);
    try {
      const input = payload;
      if (mode === 'create') {
        onSaved(await createSkill(input), 'create');
      } else {
        onSaved(await updateSkill(existingKey ?? input.key, input), 'edit');
      }
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSubmitting(false);
    }
  };

  const toggleTool = (name: string) => {
    setTools((cur) => (cur.includes(name) ? cur.filter((item) => item !== name) : [...cur, name]));
  };

  // 工具分组：内置工具一组，每个 MCP Server 一组；已声明但目录里已不存在的
  // 单独成组（可取消勾选，否则改名/删 Server 后就没法去掉旧声明）。
  const toolGroups = useMemo(() => {
    const query = toolQuery.trim().toLowerCase();
    const matches = (name: string, description: string) =>
      query === '' || `${name} ${description}`.toLowerCase().includes(query);

    const groups: { key: string; label: string; items: ToolOption[] }[] = [];
    for (const tool of knownTools) {
      if (!matches(tool.name, tool.description)) continue;
      const hit = groups.find((group) => group.key === tool.group);
      if (hit === undefined) {
        groups.push({ key: tool.group, label: tool.groupLabel, items: [tool] });
      } else {
        hit.items.push(tool);
      }
    }
    const missing = tools
      .filter((name) => !knownTools.some((tool) => tool.name === name))
      .filter((name) => matches(name, ''))
      .map((name) => ({
        name,
        description: '',
        group: 'missing',
        groupLabel: tr('目录外（Server 未探测或已删除）', 'Outside catalog (server not verified or removed)'),
        available: false,
      }));
    if (missing.length > 0) {
      groups.push({ key: 'missing', label: missing[0].groupLabel, items: missing });
    }
    return groups;
  }, [knownTools, tools, toolQuery, tr]);

  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      resizable
      title={mode === 'create' ? tr('新建技能', 'New skill') : tr(`编辑技能 · ${existingKey ?? ''}`, `Edit skill · ${existingKey ?? ''}`)}
      footer={
        <>
          <span className="mr-auto text-[11px] text-zinc-500">
            {tr('技能包 = SKILL.md + references/ + assets/ + scripts/', 'A skill pack = SKILL.md + references/ + assets/ + scripts/')}
          </span>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button onClick={() => void submit()} disabled={submitting || loading}>
            {submitting ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-xs text-zinc-300">
        {err && (
          <div className="rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-red-300">{err}</div>
        )}
        {loading ? (
          <div className="py-10 text-center text-[12px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Field label={tr('技能名 key', 'Skill key')} required>
                <Input
                  type="text"
                  value={key}
                  disabled={mode === 'edit'}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder="knowledge-search / my-runbook"
                  className="w-full font-mono"
                  maxLength={64}
                />
                <div className="mt-1 text-[11px] text-zinc-500">
                  {tr('等于 SKILL.md 的 name，必须与目录名一致；创建后不能改。', 'Equals SKILL.md name and its directory name; cannot change later.')}
                </div>
              </Field>

              <Field label={tr('描述 description', 'Description')} required>
                <Input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={tr('一句话说明何时该加载这个技能', 'One sentence on when this skill should be loaded')}
                  className="w-full"
                  maxLength={1024}
                />
              </Field>
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
              <Field label={tr('风险等级', 'Risk')} required>
                <Select
                  label={tr('风险等级', 'Risk')}
                  value={risk}
                  onValueChange={(v) => setRisk(v as SkillClass)}
                  className="w-full"
                >
                  {SKILL_CLASSES.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={tr('分类', 'Category')} required>
                <Input
                  type="text"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  placeholder={tr('知识 / 主机 / 网络…', 'Knowledge / Host / Network…')}
                  className="w-full"
                  maxLength={64}
                />
              </Field>
              <Field label={tr('许可 license', 'License')}>
                <Input
                  type="text"
                  value={license}
                  onChange={(e) => setLicense(e.target.value)}
                  placeholder="MIT"
                  className="w-full"
                  maxLength={128}
                />
              </Field>
              <Field label={tr('兼容性 compatibility', 'Compatibility')}>
                <Input
                  type="text"
                  value={compatibility}
                  onChange={(e) => setCompatibility(e.target.value)}
                  placeholder={tr('需要 python3', 'Requires python3')}
                  className="w-full"
                  maxLength={512}
                />
              </Field>
            </div>

            <DetailSection title={tr('可调用工具 metadata.adk_additional_tools', 'Declared tools (metadata.adk_additional_tools)')}>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Input
                    type="text"
                    value={toolQuery}
                    onChange={(e) => setToolQuery(e.target.value)}
                    placeholder={tr('搜索工具名或说明（内置 + MCP）', 'Search tool name or description (built-in + MCP)')}
                    className="h-7 w-full text-[11px]"
                    aria-label={tr('搜索可声明工具', 'Search declarable tools')}
                  />
                  <span className="shrink-0 text-[10px] text-zinc-500">
                    {tr(`已选 ${tools.length}`, `${tools.length} selected`)}
                  </span>
                </div>
                {knownTools.length === 0 ? (
                  <div className="text-[11px] text-zinc-500">{tr('加载工具目录中…', 'Loading tool catalog…')}</div>
                ) : toolGroups.length === 0 ? (
                  <div className="text-[11px] text-zinc-500">{tr('没有匹配的工具', 'No matching tool')}</div>
                ) : (
                  toolGroups.map((group) => (
                    <div key={group.key} className="overflow-hidden rounded-md border border-zinc-800/60">
                      <div className="flex items-center gap-1.5 border-b border-zinc-800/60 bg-zinc-950/40 px-1.5 py-1">
                        <span className="truncate font-mono text-[10px] text-zinc-400">{group.label}</span>
                        <span className="shrink-0 text-[10px] text-zinc-600">{group.items.length}</span>
                        <span className="ml-auto shrink-0 text-[10px] text-zinc-500">
                          {tr(
                            `已选 ${group.items.filter((item) => tools.includes(item.name)).length}`,
                            `${group.items.filter((item) => tools.includes(item.name)).length} selected`,
                          )}
                        </span>
                      </div>
                      <div className="max-h-44 space-y-0.5 overflow-y-auto px-1 py-1">
                        {group.items.map((tool) => {
                          const checked = tools.includes(tool.name);
                          return (
                            <Label
                              key={tool.name}
                              className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 hover:bg-zinc-900/60"
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleTool(tool.name)}
                                className="h-3.5 w-3.5 shrink-0 accent-indigo-500"
                                aria-label={tr(`声明工具 ${tool.name}`, `Declare tool ${tool.name}`)}
                              />
                              <span className="shrink-0 font-mono text-[11px] text-zinc-200">{tool.name}</span>
                              {!tool.available && (
                                <Chip dense tone="warning">
                                  {tr('不可用', 'unavailable')}
                                </Chip>
                              )}
                              <span className="truncate text-[10px] text-zinc-500">{tool.description}</span>
                            </Label>
                          );
                        })}
                      </div>
                    </div>
                  ))
                )}
                <div className="px-1.5 text-[11px] text-zinc-500">
                  {tr(
                    '勾选的工具会随技能一起可用，并在助理里自动勾选、不可屏蔽；MCP 工具进检索目录，模型按需 search/load 调用，同 Server 中未勾选的工具搜不到也调不了。不勾选 = 纯说明型技能。',
                    'Checked tools open with the skill, are auto-selected in assistants and cannot be blocked; MCP tools join the search index and are loaded on demand, while unchecked tools of the same server stay unsearchable. None = an instruction-only skill.',
                  )}
                </div>
              </div>
            </DetailSection>

            <Field label={tr('SKILL.md 正文（指令）', 'SKILL.md body (instructions)')} required>
              <Textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder={tr('# 什么时候用\n\n1. 先读 references/guide.md\n2. 按步骤执行', '# When to use\n\n1. Read references/guide.md\n2. Follow the steps')}
                className="h-48 w-full resize-y font-mono text-[12px]"
              />
              <div className="mt-1 text-[11px] text-zinc-500">
                {tr('frontmatter 由上方字段生成，这里只写 Markdown 正文。', 'Frontmatter is generated from the fields above; write only the Markdown body here.')}
              </div>
            </Field>

            <Label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={injectState}
                onChange={(e) => setInjectState(e.target.checked)}
                className="h-3.5 w-3.5 accent-indigo-500"
                aria-label={tr('在正文里注入会话状态变量', 'Inject session state variables into the body')}
              />
              <span className="text-[12px] text-zinc-300">metadata.adk_inject_state</span>
              <span className="text-[11px] text-zinc-500">
                {tr('开启后正文里的 {变量} 会被会话状态替换', 'When on, {vars} in the body are replaced from session state')}
              </span>
            </Label>

            {RESOURCE_SECTIONS.map((section) => (
              <DetailSection key={section.kind} title={tr(section.title.zh, section.title.en)}>
                <div className="mb-2 flex items-center gap-2">
                  <Hint content={tr(section.hint.zh, section.hint.en)}>
                    <Chip dense>{files[section.kind].length}</Chip>
                  </Hint>
                  <span className="text-[11px] text-zinc-500">{tr(section.hint.zh, section.hint.en)}</span>
                  <span className="ml-auto flex gap-1">
                    {section.binary ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        type="button"
                        className="px-2 py-0.5 text-[11px]"
                        onClick={() => {
                          assetTarget.current = section.kind;
                          assetInputRef.current?.click();
                        }}
                      >
                        <FileUp size={11} />
                        {tr('上传文件', 'Upload')}
                      </Button>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="sm"
                      type="button"
                      className="px-2 py-0.5 text-[11px]"
                      onClick={() => addFile(section.kind)}
                    >
                      <Plus size={11} />
                      {tr('添加文本', 'Add text')}
                    </Button>
                  </span>
                </div>
                {files[section.kind].length === 0 ? (
                  <div className="rounded-lg border border-dashed border-zinc-800 px-3 py-3 text-[11px] text-zinc-500">
                    {tr('还没有文件（可选）', 'No files yet (optional)')}
                  </div>
                ) : (
                  <div className="space-y-2">
                    {files[section.kind].map((file, index) => (
                      <div key={`${section.kind}-${index}`} className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-2">
                        <div className="flex items-center gap-2">
                          <Input
                            type="text"
                            value={file.path}
                            onChange={(e) => updateFile(section.kind, index, { path: e.target.value })}
                            placeholder={tr('相对路径，例如 guide.md', 'Relative path, e.g. guide.md')}
                            className="w-full font-mono text-[11px]"
                          />
                          <Button
                            variant="dangerGhost"
                            size="sm"
                            type="button"
                            className="shrink-0 px-1.5 py-0"
                            aria-label={tr('删除文件', 'Remove file')}
                            onClick={() => removeFile(section.kind, index)}
                          >
                            <Trash2 size={11} />
                          </Button>
                        </div>
                        {file.base64 !== '' ? (
                          <div className="mt-1 text-[11px] text-zinc-500">
                            {tr(`二进制文件，已内嵌（${file.base64.length} 字符 base64）`, `Binary file embedded (${file.base64.length} base64 chars)`)}
                          </div>
                        ) : (
                          <Textarea
                            value={file.text}
                            onChange={(e) => updateFile(section.kind, index, { text: e.target.value })}
                            placeholder={tr('文件内容', 'File content')}
                            className={cn('mt-1 w-full resize-y font-mono text-[11px]', section.kind === 'scripts' ? 'h-24' : 'h-20')}
                          />
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </DetailSection>
            ))}

            <input
              ref={assetInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                void pickAssetFiles(e.target.files, assetTarget.current);
                e.target.value = '';
              }}
            />
          </>
        )}
      </div>
    </Modal>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1 text-[11px] text-zinc-400">
        {label}
        {required && <span className="text-red-400">*</span>}
      </span>
      {children}
    </label>
  );
}

/** 导入弹窗：先解析预览，再补 risk/category 落库。 */
export function SkillImportDialog({ onClose, onImported }: { onClose(): void; onImported(): void }) {
  const { tr } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [risk, setRisk] = useState<SkillClass>('safe');
  const [category, setCategory] = useState('');
  const [preview, setPreview] = useState<SkillZipPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const onPick = async (picked: File | null) => {
    if (picked === null) return;
    setErr(null);
    setFile(picked);
    setBusy(true);
    try {
      const p = await parseSkillZip(picked);
      setPreview(p);
      if (p.risk !== null) setRisk(p.risk);
      if (p.category !== null) setCategory(p.category);
    } catch (e) {
      setPreview(null);
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const doImport = async () => {
    if (file === null) return;
    setBusy(true);
    setErr(null);
    try {
      await importSkillZip(file, {
        risk,
        ...(category.trim() !== '' ? { category: category.trim() } : {}),
      });
      onImported();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      resizable
      title={tr('导入技能包（ZIP）', 'Import skill pack (ZIP)')}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button onClick={() => void doImport()} disabled={busy || preview === null}>
            {tr('导入', 'Import')}
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-xs text-zinc-300">
        <div className="rounded-md border border-indigo-500/30 bg-indigo-500/5 px-3 py-2 text-[11px] leading-5 text-zinc-400">
          {tr(
            'ZIP 内需有 SKILL.md（可整体套一层目录），可选 references/ assets/ scripts/；单包上限 2000 个文件、解压后 32MB。',
            'The ZIP must contain SKILL.md (a single wrapping folder is fine), plus optional references/ assets/ scripts/; limits are 2000 files and 32MB uncompressed.',
          )}
        </div>
        {err && <div className="rounded-md border border-red-500/40 bg-red-500/5 px-3 py-2 text-red-300">{err}</div>}

        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" type="button" onClick={() => fileRef.current?.click()} disabled={busy}>
            <FilePlus2 size={12} />
            {tr('选择 ZIP', 'Choose ZIP')}
          </Button>
          <span className="truncate text-[11px] text-zinc-500">{file?.name ?? tr('未选择文件', 'No file selected')}</span>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".zip,application/zip"
          className="hidden"
          aria-label={tr('选择技能包 ZIP 文件', 'Choose a skill pack ZIP file')}
          onChange={(e) => {
            void onPick(e.target.files?.[0] ?? null);
            e.target.value = '';
          }}
        />

        {preview !== null && (
          <div className="space-y-3">
            <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[12px] text-zinc-200">{preview.key}</span>
                <Chip dense tone="accent">{tr('已解析', 'parsed')}</Chip>
                <span className="ml-auto text-[11px] text-zinc-500">
                  {tr(
                    `references ${preview.references.length} · assets ${preview.assets.length} · scripts ${preview.scripts.length}`,
                    `references ${preview.references.length} · assets ${preview.assets.length} · scripts ${preview.scripts.length}`,
                  )}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-zinc-400">{preview.description}</div>
              {preview.tools.length > 0 && (
                <div className="mt-1 font-mono text-[10px] text-zinc-500">tools: {preview.tools.join(', ')}</div>
              )}
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label={tr('风险等级', 'Risk')} required>
                <Select
                  label={tr('风险等级', 'Risk')}
                  value={risk}
                  onValueChange={(v) => setRisk(v as SkillClass)}
                  className="w-full"
                >
                  {SKILL_CLASSES.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={tr('分类', 'Category')} required>
                <Input
                  type="text"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  placeholder={tr('知识 / 主机 / 网络…', 'Knowledge / Host / Network…')}
                  className="w-full"
                  maxLength={64}
                />
              </Field>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

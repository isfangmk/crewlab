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
  ErrorBanner,
  Input,
  Label,
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
} from '@/components/ui';
import { Hint } from '@/components/ui/Tooltip';
import { ApiError } from '@/api/client';
import {
  SKILL_CLASSES,
  createSkill,
  getSkill,
  importSkillZip,
  listSkillGroups,
  parseSkillZip,
  updateSkill,
  type SkillClass,
  type SkillGroup,
  type SkillGroupMeta,
  type SkillPayloadInput,
  type SkillSummary,
  type SkillZipPreview,
} from '@/api/skills';
import { useI18n } from '@/i18n/locale';
import { cn } from '@/lib/cn';

/** 资源目录类型：三类目录的语义不同，编辑器要分开呈现。 */
type ResourceKind = 'references' | 'assets' | 'scripts';

/** 编辑器的并列配置分区：基本信息 / 指令正文 / 资源文件。 */
type EditorSection = 'basic' | 'instructions' | 'resources';

const EDITOR_SECTIONS: { key: EditorSection; label: { zh: string; en: string } }[] = [
  { key: 'basic', label: { zh: '基本信息', en: 'Basics' } },
  { key: 'instructions', label: { zh: '指令正文', en: 'Instructions' } },
  { key: 'resources', label: { zh: '资源文件', en: 'Resources' } },
];

type FileDraft = {
  path: string;
  text: string;
  base64: string;
};

// 风险等级的中文含义（与 RiskBadge 同一口径）
const RISK_LABELS: Record<SkillClass, { zh: string; en: string }> = {
  safe: { zh: '安全', en: 'Safe' },
  mutating: { zh: '变更', en: 'Mutating' },
  dangerous: { zh: '危险', en: 'Dangerous' },
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
  const [activeTab, setActiveTab] = useState<EditorSection>('basic');
  const [key, setKey] = useState('');
  const [description, setDescription] = useState('');
  const [risk, setRisk] = useState<SkillClass>('safe');
  const [group, setGroup] = useState<SkillGroup>('other');
  const [groupOptions, setGroupOptions] = useState<SkillGroupMeta[]>([]);
  const [instructions, setInstructions] = useState('');
  const [injectState, setInjectState] = useState(false);
  const [files, setFiles] = useState<Record<ResourceKind, FileDraft[]>>({
    references: [],
    assets: [],
    scripts: [],
  });
  const [loading, setLoading] = useState(mode === 'edit');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const assetInputRef = useRef<HTMLInputElement>(null);
  const assetTarget = useRef<ResourceKind>('assets');

  // 分组选项来自后端枚举，编辑器的下拉与目录页筛选用同一份数据
  useEffect(() => {
    void listSkillGroups()
      .then((res) => setGroupOptions(res.groups ?? []))
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
        setGroup(skill.group ?? 'other');
        setInstructions(skill.instructions ?? '');
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
      preapproved_tools: '',
      risk,
      group,
      inject_state: injectState,
      references: toWire('references'),
      assets: toWire('assets'),
      scripts: toWire('scripts'),
    };
  }, [key, description, instructions, risk, group, injectState, files]);

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

  // 必填项分散在多个分区：缺失时给 tab 标状态点，底部给可定位提示，并禁用保存。
  const missingRequired: string[] = [];
  if (mode === 'create' && key.trim() === '') missingRequired.push(tr('技能名 key', 'Skill key'));
  if (description.trim() === '') missingRequired.push(tr('描述 description', 'Description'));
  if (instructions.trim() === '') missingRequired.push(tr('指令正文', 'Instructions'));
  const saveBlockedHint =
    missingRequired.length > 0
      ? tr(`还需填写：${missingRequired.join('、')}`, `Still required: ${missingRequired.join(', ')}`)
      : '';
  const basicIncomplete =
    (mode === 'create' && key.trim() === '') || description.trim() === '';
  const instructionsIncomplete = instructions.trim() === '';
  const saveDisabled = submitting || loading || missingRequired.length > 0;

  const tabLabel = (label: string, incomplete: boolean) => (
    <>
      {label}
      {incomplete && (
        <span
          aria-hidden
          title={tr('有必填项未完成', 'Required fields incomplete')}
          className="h-1.5 w-1.5 rounded-full bg-warn"
        />
      )}
    </>
  );

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
            {saveBlockedHint !== ''
              ? saveBlockedHint
              : tr('技能包 = SKILL.md + references/ + assets/ + scripts/', 'A skill pack = SKILL.md + references/ + assets/ + scripts/')}
          </span>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            {tr('取消', 'Cancel')}
          </Button>
          <Button onClick={() => void submit()} disabled={saveDisabled}>
            {submitting ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-xs text-zinc-300">
        {err && <ErrorBanner>{err}</ErrorBanner>}
        {loading ? (
          <div className="py-10 text-center text-[12px] text-zinc-500">{tr('加载中…', 'Loading…')}</div>
        ) : (
          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as EditorSection)}
            className="space-y-3"
          >
            <TabsList aria-label={tr('技能配置分区', 'Skill configuration sections')}>
              {EDITOR_SECTIONS.map((item) => (
                <TabsTrigger key={item.key} value={item.key}>
                  {tabLabel(
                    tr(item.label.zh, item.label.en),
                    (item.key === 'basic' && basicIncomplete) ||
                      (item.key === 'instructions' && instructionsIncomplete),
                  )}
                </TabsTrigger>
              ))}
            </TabsList>

            <TabsContent value="basic" keepMounted className="min-h-[16rem] space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <Field label={tr('技能名 key', 'Skill key')} required>
                  <Input
                    type="text"
                    value={key}
                    disabled={mode === 'edit'}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder="knowledge-search / my-runbook"
                    className="w-full font-sans"
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
                        {tr(RISK_LABELS[item].zh, RISK_LABELS[item].en)}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label={tr('分组', 'Group')} required>
                  <Select
                    label={tr('分组', 'Group')}
                    value={group}
                    onValueChange={(v) => setGroup(v as SkillGroup)}
                    className="w-full"
                  >
                    {groupOptions.map((g) => (
                      <option key={g.key} value={g.key}>
                        {tr(g.title, g.title_en)}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>

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
            </TabsContent>

            <TabsContent value="instructions" keepMounted className="min-h-[16rem] space-y-4">
              <Field label={tr('SKILL.md 正文（指令）', 'SKILL.md body (instructions)')} required>
                <Textarea
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  placeholder={tr('# 什么时候用\n\n1. 先读 references/guide.md\n2. 按步骤执行', '# When to use\n\n1. Read references/guide.md\n2. Follow the steps')}
                  className="h-80 w-full resize-y font-sans text-[12px]"
                />
                <div className="mt-1 text-[11px] text-zinc-500">
                  {tr('frontmatter 由「基本信息」分区的字段生成，这里只写 Markdown 正文。', 'Frontmatter is generated from the Basics section; write only the Markdown body here.')}
                </div>
              </Field>
            </TabsContent>

            <TabsContent value="resources" keepMounted className="min-h-[16rem] space-y-4">
              {RESOURCE_SECTIONS.map((resource) => (
                <DetailSection key={resource.kind} title={tr(resource.title.zh, resource.title.en)}>
                  <div className="mb-2 flex items-center gap-2">
                    <Hint content={tr(resource.hint.zh, resource.hint.en)}>
                      <Chip dense>{files[resource.kind].length}</Chip>
                    </Hint>
                    <span className="text-[11px] text-zinc-500">{tr(resource.hint.zh, resource.hint.en)}</span>
                    <span className="ml-auto flex gap-1">
                      {resource.binary ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          type="button"
                          className="px-2 py-0.5 text-[11px]"
                          onClick={() => {
                            assetTarget.current = resource.kind;
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
                        onClick={() => addFile(resource.kind)}
                      >
                        <Plus size={11} />
                        {tr('添加文本', 'Add text')}
                      </Button>
                    </span>
                  </div>
                  {files[resource.kind].length === 0 ? (
                    <div className="rounded-lg border border-dashed border-zinc-800 px-3 py-3 text-[11px] text-zinc-500">
                      {tr('还没有文件（可选）', 'No files yet (optional)')}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {files[resource.kind].map((file, index) => (
                        <div key={`${resource.kind}-${index}`} className="rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-2">
                          <div className="flex items-center gap-2">
                            <Input
                              type="text"
                              value={file.path}
                              onChange={(e) => updateFile(resource.kind, index, { path: e.target.value })}
                              placeholder={tr('相对路径，例如 guide.md', 'Relative path, e.g. guide.md')}
                              className="w-full font-sans text-[11px]"
                            />
                            <Button
                              variant="dangerGhost"
                              size="sm"
                              type="button"
                              className="shrink-0 px-1.5 py-0"
                              aria-label={tr('删除文件', 'Remove file')}
                              onClick={() => removeFile(resource.kind, index)}
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
                              onChange={(e) => updateFile(resource.kind, index, { text: e.target.value })}
                              placeholder={tr('文件内容', 'File content')}
                              className={cn('mt-1 w-full resize-y font-sans text-[11px]', resource.kind === 'scripts' ? 'h-24' : 'h-20')}
                            />
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </DetailSection>
              ))}
            </TabsContent>

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
          </Tabs>
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
        {required && <span className="text-danger">*</span>}
      </span>
      {children}
    </label>
  );
}

/** 导入弹窗：先解析预览，再补 risk/group 落库。 */
export function SkillImportDialog({ onClose, onImported }: { onClose(): void; onImported(): void }) {
  const { tr } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [risk, setRisk] = useState<SkillClass>('safe');
  const [group, setGroup] = useState<SkillGroup>('other');
  const [groupOptions, setGroupOptions] = useState<SkillGroupMeta[]>([]);
  const [preview, setPreview] = useState<SkillZipPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // 分组选项来自后端枚举，与编辑器共用同一份数据
  useEffect(() => {
    void listSkillGroups()
      .then((res) => setGroupOptions(res.groups ?? []))
      .catch((e: Error) => setErr(e.message));
  }, []);

  const onPick = async (picked: File | null) => {
    if (picked === null) return;
    setErr(null);
    setFile(picked);
    setBusy(true);
    try {
      const p = await parseSkillZip(picked);
      setPreview(p);
      if (p.risk !== null) setRisk(p.risk);
      if (p.group !== null) setGroup(p.group);
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
      await importSkillZip(file, { risk, group });
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
        {err && <ErrorBanner>{err}</ErrorBanner>}

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
                <span className="font-sans text-[12px] text-zinc-200">{preview.key}</span>
                <Chip dense tone="accent">{tr('已解析', 'parsed')}</Chip>
                <span className="ml-auto text-[11px] text-zinc-500">
                  {tr(
                    `references ${preview.references.length} · assets ${preview.assets.length} · scripts ${preview.scripts.length}`,
                    `references ${preview.references.length} · assets ${preview.assets.length} · scripts ${preview.scripts.length}`,
                  )}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-zinc-400">{preview.description}</div>
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
                      {tr(RISK_LABELS[item].zh, RISK_LABELS[item].en)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={tr('分组', 'Group')} required>
                <Select
                  label={tr('分组', 'Group')}
                  value={group}
                  onValueChange={(v) => setGroup(v as SkillGroup)}
                  className="w-full"
                >
                  {groupOptions.map((g) => (
                    <option key={g.key} value={g.key}>
                      {tr(g.title, g.title_en)}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

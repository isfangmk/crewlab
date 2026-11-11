import { useCallback, useEffect, useRef, useState } from 'react';
import { Database, Loader2, Save } from 'lucide-react';
import { Button, Input, Label, SectionCard } from '@/components/ui';
import { reindexKnowledge } from '@/api/knowledge';
import { listSettings, setSetting } from '@/api/settings';
import { useI18n } from '@/i18n/locale';

const CATEGORY = 'knowledge';

// 知识库检索设置：切块参数改动会触发全量重建，阈值保存即生效。
export default function SettingsKnowledge() {
  const { tr } = useI18n();
  const [size, setSize] = useState('');
  const [overlap, setOverlap] = useState('');
  const [minScore, setMinScore] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedText, setSavedText] = useState<string | null>(null);
  // 打开时读到的切块参数：只有它变了才需要重建索引
  const loaded = useRef<{ size: string; overlap: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listSettings(CATEGORY);
      const read = (key: string) => {
        const found = res.items.find((i) => i.key === key);
        // 三项都是知识库运行必需配置，缺一项显式报错而不是填默认值
        if (!found) throw new Error(tr(`缺少配置项 ${key}`, `Missing setting ${key}`));
        return found.value;
      };
      const sizeValue = read('chunk_size');
      const overlapValue = read('chunk_overlap');
      loaded.current = { size: sizeValue, overlap: overlapValue };
      setSize(sizeValue);
      setOverlap(overlapValue);
      setMinScore(read('rrf_min_score'));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [tr]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    const sizeNext = size.trim();
    const overlapNext = overlap.trim();
    const minScoreNext = minScore.trim();
    if (!/^\d+$/.test(sizeNext) || Number(sizeNext) <= 0) {
      setErr(tr('切块大小必须是正整数', 'Chunk size must be a positive integer'));
      return;
    }
    if (!/^\d+$/.test(overlapNext)) {
      setErr(tr('切块重叠必须是非负整数', 'Chunk overlap must be a non-negative integer'));
      return;
    }
    if (Number(overlapNext) >= Number(sizeNext)) {
      setErr(tr('切块重叠必须小于切块大小', 'Chunk overlap must be smaller than chunk size'));
      return;
    }
    const minScoreNum = Number(minScoreNext);
    if (minScoreNext === '' || !Number.isFinite(minScoreNum) || minScoreNum < 0) {
      setErr(tr('最低 RRF 分必须是非负数字', 'Minimum RRF score must be a non-negative number'));
      return;
    }

    setSaving(true);
    setErr(null);
    setSavedText(null);
    try {
      await setSetting(CATEGORY, 'chunk_size', sizeNext);
      await setSetting(CATEGORY, 'chunk_overlap', overlapNext);
      await setSetting(CATEGORY, 'rrf_min_score', minScoreNext);
      // 切块参数变了就必须按新参数重新切块；阈值改动即时生效，无需重建
      const chunkChanged =
        loaded.current === null ||
        loaded.current.size !== sizeNext ||
        loaded.current.overlap !== overlapNext;
      if (chunkChanged) {
        const res = await reindexKnowledge();
        setSavedText(
          tr(
            `已保存并重建索引（切块数 ${res.chunks}）`,
            `Saved; reindexed with the new params (${res.chunks} chunks)`,
          ),
        );
        loaded.current = { size: sizeNext, overlap: overlapNext };
      } else {
        setSavedText(tr('设置已保存', 'Settings saved'));
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const edit = (setValue: (value: string) => void) => (value: string) => {
    setValue(value);
    setSavedText(null);
  };

  return (
    <SectionCard
      title={
        <span className="inline-flex items-center gap-2">
          <Database size={13} className="text-zinc-400" />
          {tr('知识库检索', 'Knowledge retrieval')}
        </span>
      }
      description={tr(
        '切块参数改动会重建全部文档索引，可能耗时数十秒；最低 RRF 分保存即生效。',
        'Changing chunk params reindexes every document and can take a while; the minimum RRF score applies immediately.',
      )}
      footer={
        !loading ? (
          <>
            {savedText ? (
              <span role="status" className="mr-auto text-[11px] text-emerald-400">
                {savedText}
              </span>
            ) : null}
            <Button variant="primary" size="sm" disabled={saving} onClick={() => void submit()}>
              <Save size={12} />
              {saving ? tr('保存中…', 'Saving…') : tr('保存', 'Save')}
            </Button>
          </>
        ) : undefined
      }
    >
      {err && (
        <div role="alert" className="mb-3 text-[11px] text-red-400">
          {err}
        </div>
      )}
      {loading ? (
        <div className="flex h-7 items-center text-[11px] text-zinc-500">
          <Loader2 size={12} className="mr-2 animate-spin" /> {tr('加载中…', 'Loading…')}
        </div>
      ) : (
        <div className="space-y-5">
          <section className="space-y-3">
            <div>
              <h3 className="text-[11.5px] font-medium text-zinc-200">{tr('切块参数', 'Chunking')}</h3>
              <p className="mt-0.5 text-[10.5px] leading-4 text-zinc-500">
                {tr('改动后保存即自动重建索引', 'Changed values trigger a reindex on save')}
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label className="mb-1 block text-[11px] text-zinc-400" htmlFor="settings-chunk-size">
                  {tr('切块大小（字符）', 'Chunk size (chars)')}
                </Label>
                <Input
                  id="settings-chunk-size"
                  inputMode="numeric"
                  value={size}
                  onChange={(e) => edit(setSize)(e.target.value)}
                  className="font-mono text-[12px]"
                />
              </div>
              <div>
                <Label className="mb-1 block text-[11px] text-zinc-400" htmlFor="settings-chunk-overlap">
                  {tr('切块重叠（字符）', 'Chunk overlap (chars)')}
                </Label>
                <Input
                  id="settings-chunk-overlap"
                  inputMode="numeric"
                  value={overlap}
                  onChange={(e) => edit(setOverlap)(e.target.value)}
                  className="font-mono text-[12px]"
                />
              </div>
            </div>
            <p className="text-[10.5px] leading-4 text-zinc-500">
              {tr('重叠必须小于切块大小。', 'Overlap must be smaller than the chunk size.')}
            </p>
          </section>

          <section className="space-y-3 border-t border-zinc-800/60 pt-4">
            <div>
              <h3 className="text-[11.5px] font-medium text-zinc-200">{tr('检索阈值', 'Retrieval threshold')}</h3>
              <p className="mt-0.5 text-[10.5px] leading-4 text-zinc-500">
                {tr('保存后立即生效', 'Applies immediately')}
              </p>
            </div>
            <div className="max-w-sm">
              <Label className="mb-1 block text-[11px] text-zinc-400" htmlFor="settings-rrf-min-score">
                {tr('最低 RRF 分（0 = 不过滤）', 'Minimum RRF score (0 = no filter)')}
              </Label>
              <Input
                id="settings-rrf-min-score"
                inputMode="decimal"
                value={minScore}
                onChange={(e) => edit(setMinScore)(e.target.value)}
                className="font-mono text-[12px]"
              />
            </div>
            <p className="text-[10.5px] leading-4 text-zinc-500">
              {tr('低于该分数的命中直接丢弃。', 'Hits below this score are dropped.')}
            </p>
          </section>
        </div>
      )}
    </SectionCard>
  );
}

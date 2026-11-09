import { useAssistantPage } from '@/lib/assistantPage';
// Settings → About. Product identity: version (live from the manager), the
// public GitHub repo, and the license. Lives here because the brand mark in the
// sidebar no longer shows the version.
import { useEffect, useState } from 'react';
import { Github, ExternalLink, Scale, Tag } from 'lucide-react';
import { useI18n } from '@/i18n/locale';
import { getManagerVersion } from '@/api/version';
import { OngridLogo } from '@/components/OngridLogo';
import { SectionCard } from '@/components/ui';

const GITHUB_URL = 'https://github.com/ongridio/ongrid';
const LICENSE = 'AGPL-3.0-only';
const LICENSE_URL = 'https://github.com/ongridio/ongrid/blob/main/LICENSE';

export default function About() {
  const { tr } = useI18n();
  const [version, setVersion] = useState('');

  useEffect(() => {
    let cancelled = false;
    void getManagerVersion()
      .then((r) => {
        if (!cancelled) setVersion((r.manager_version || '').trim());
      })
      .catch(() => {
        if (!cancelled) setVersion('');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useAssistantPage('关于 Ongrid', 'About Ongrid', {
    version: version || "loading",
  });

  return (
    <SectionCard
      title={<span className="inline-flex items-center gap-2"><OngridLogo size={16} className="shrink-0" />Ongrid</span>}
      description={tr('AIOps 平台', 'AIOps platform')}
    >
      <dl className="divide-y divide-zinc-800/60">
        <Row icon={<Tag size={12} />} label={tr('版本', 'Version')}>
          <span className="font-mono text-zinc-200">{version || '—'}</span>
        </Row>
        <Row icon={<Github size={12} />} label={tr('源码', 'Source')}>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-indigo-300 hover:underline"
          >
            github.com/ongridio/ongrid <ExternalLink size={11} />
          </a>
        </Row>
        <Row icon={<Scale size={12} />} label={tr('许可证', 'License')}>
          <a
            href={LICENSE_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-indigo-300 hover:underline"
          >
            {LICENSE} <ExternalLink size={11} />
          </a>
        </Row>
      </dl>

      <p className="mt-3 text-[11px] leading-relaxed text-zinc-500">
        {tr(
          '以对话为中心的 AI 工作台：智能体、工具、技能、MCP、知识库与模型配置。',
          'Conversation-centric AI workbench: agents, tools, skills, MCP, knowledge, and model config.',
        )}
      </p>
    </SectionCard>
  );
}

function Row({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 text-[12px] text-zinc-200">
      <span className="flex items-center gap-2 text-[11px] text-zinc-500">
        {icon} {label}
      </span>
      <span className="min-w-0 truncate">{children}</span>
    </div>
  );
}

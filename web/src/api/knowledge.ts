import { request } from './client';
import { getToken } from '@/store/auth';
import { tr as trInline } from '@/i18n/locale';

// 知识库文档 API。id 为 UUID 字符串。
// source_type：后端创建只接受 manual / upload / vault。
export type DocSource = 'manual' | 'repo' | 'url' | 'vault' | 'upload';

export type KnowledgeDoc = {
  id: string;
  source_type: DocSource;
  repo_id?: string | null;
  url?: string;
  title: string;
  // 英文标题；en-US 优先展示，缺省用 title。
  title_en?: string;
  content?: string;
  path?: string; // 以 / 分段的目录路径
  tags?: string[];
  created_at: string;
  updated_at: string;
};

export type SearchHit = { doc: KnowledgeDoc; score: number };

export type PathRow = { path: string; count: number };

export function listDocs(params?: {
  source_type?: 'manual' | 'repo';
  repo_id?: number;
  path?: string;
  path_prefix?: string;
  tag?: string;
}) {
  const q = new URLSearchParams();
  if (params?.source_type) q.set('source_type', params.source_type);
  if (params?.repo_id != null) q.set('repo_id', String(params.repo_id));
  if (params?.path) q.set('path', params.path);
  if (params?.path_prefix) q.set('path_prefix', params.path_prefix);
  if (params?.tag) q.set('tag', params.tag);
  const qs = q.toString();
  return request<{ items: KnowledgeDoc[]; total: number }>(
    'GET',
    `/knowledge/docs${qs ? `?${qs}` : ''}`,
  );
}

export function getDoc(id: string) {
  return request<KnowledgeDoc>('GET', `/knowledge/docs/${id}`);
}

export function createDoc(input: {
  title: string;
  title_en?: string;
  content: string;
  url?: string;
  path?: string;
  tags?: string[];
}) {
  return request<KnowledgeDoc>('POST', '/knowledge/docs', input);
}

export function updateDoc(
  id: string,
  input: { title: string; title_en?: string; content: string; path?: string; tags?: string[] },
) {
  return request<KnowledgeDoc>('PATCH', `/knowledge/docs/${id}`, input);
}

export function deleteDoc(id: string) {
  return request<void>('DELETE', `/knowledge/docs/${id}`);
}

// 移动组织文档（manual/upload）到新目录；path 为空表示根目录。
export function moveDoc(id: string, path: string) {
  return request<KnowledgeDoc>('PATCH', `/knowledge/docs/${id}/move`, { path });
}

export function searchKnowledge(
  q: string,
  opts?: {
    limit?: number;
    path?: string;
    pathPrefix?: string;
    tags?: string[];
    scope?: { paths?: string[]; doc_ids?: string[] };
  },
) {
  const params = new URLSearchParams();
  params.set('q', q);
  params.set('limit', String(opts?.limit ?? 10));
  if (opts?.path) params.set('path', opts.path);
  if (opts?.pathPrefix) params.set('path_prefix', opts.pathPrefix);
  for (const t of opts?.tags ?? []) params.append('tag', t);
  for (const p of opts?.scope?.paths ?? []) params.append('scope_path', p);
  for (const d of opts?.scope?.doc_ids ?? []) params.append('scope_doc', d);
  return request<{ items: SearchHit[]; total: number }>(
    'GET',
    `/knowledge/search?${params.toString()}`,
  );
}

/** 助理编辑器预览知识范围：命中多少篇、哪些目录/文档已不存在。 */
export type ScopePreview = {
  doc_count: number;
  matched_paths: { path: string; count: number }[];
  unknown_paths: string[];
  unknown_doc_ids: string[];
};

export function scopePreview(input: { paths: string[]; doc_ids: string[] }) {
  return request<ScopePreview>('POST', '/knowledge/scope-preview', input);
}

export function listPaths() {
  return request<{ items: PathRow[]; total: number }>('GET', '/knowledge/paths');
}

export function reindexKnowledge() {
  return request<{ chunks: number }>('POST', '/knowledge/reindex');
}

// 上传文件入库（source_type=upload）；仅 .md / .txt / .pdf / .docx。
// request() 会强制 JSON，因此这里直接用 FormData + fetch。
export async function uploadDoc(
  file: File,
  opts?: { title?: string; path?: string; tags?: string[] },
): Promise<KnowledgeDoc> {
  const fd = new FormData();
  fd.append('file', file);
  if (opts?.title) fd.append('title', opts.title);
  if (opts?.path) fd.append('path', opts.path);
  if (opts?.tags && opts.tags.length) fd.append('tags', opts.tags.join(','));
  const token = getToken();
  const res = await fetch('/api/v1/knowledge/upload', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  if (!res.ok) {
    let msg = `upload failed (${res.status})`;
    try {
      const j = await res.json();
      msg = j.message || j.error || msg;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(msg);
  }
  return res.json();
}

// 内置知识库标题 / 路径的中英文对照。
const KNOWLEDGE_PATH_SEGMENTS: Record<string, { zh: string; en: string }> = {
  // 英文目录名
  concepts: { zh: '概念', en: 'Concepts' },
  diagnostics: { zh: '诊断 playbook', en: 'Diagnostics' },
  systems: { zh: '系统', en: 'Systems' },
  alerts: { zh: '告警', en: 'Alerts' },
  reference: { zh: '参考', en: 'Reference' },
  external: { zh: '外部资料', en: 'External' },
  index: { zh: '阅读索引', en: 'Index' },
  network: { zh: '网络', en: 'Network' },
  observability: { zh: '可观测', en: 'Observability' },
  'observability-stack': { zh: '可观测栈', en: 'Observability stack' },
  kernel: { zh: '内核', en: 'Kernel' },
  compute: { zh: '计算 / 内存', en: 'Compute' },
  kubernetes: { zh: 'Kubernetes', en: 'Kubernetes' },
  container: { zh: '容器', en: 'Container' },
  disk: { zh: '磁盘', en: 'Disk' },
  ebpf: { zh: 'eBPF', en: 'eBPF' },
  database: { zh: '数据库', en: 'Database' },
  dns: { zh: 'DNS', en: 'DNS' },
  systemd: { zh: 'systemd', en: 'systemd' },
  methodology: { zh: '方法论', en: 'Methodology' },
  storage: { zh: '存储', en: 'Storage' },
  tls: { zh: 'TLS', en: 'TLS' },
  shell: { zh: 'Shell', en: 'Shell' },
  http: { zh: 'HTTP', en: 'HTTP' },
  tracing: { zh: '追踪', en: 'Tracing' },
  performance: { zh: '性能', en: 'Performance' },
  scheduling: { zh: '调度', en: 'Scheduling' },
  linux: { zh: 'Linux', en: 'Linux' },
  // 中文目录名
  存储: { zh: '存储', en: 'Storage' },
  磁盘: { zh: '磁盘', en: 'Disk' },
  调度: { zh: '调度', en: 'Scheduling' },
  时区: { zh: '时区', en: 'Timezone' },
  容器: { zh: '容器', en: 'Container' },
  网络: { zh: '网络', en: 'Network' },
  连通性: { zh: '连通性', en: 'Connectivity' },
  网卡: { zh: '网卡', en: 'NIC' },
  控制面: { zh: '控制面', en: 'Control plane' },
  Linux系统: { zh: 'Linux 系统', en: 'Linux' },
  进程: { zh: '进程', en: 'Processes' },
  资源: { zh: '资源', en: 'Resources' },
};

// 标题对照：键为文档中文标题。
// Operators/users who add new docs will simply pass through.
const KNOWLEDGE_TITLES: Record<string, string> = {
  'XFS vs ext4 选型与常见坑 playbook': 'XFS vs ext4 — selection & common pitfalls (playbook)',
  'Redis 内存淘汰与 maxmemory 排查 playbook': 'Redis eviction & maxmemory diagnosis (playbook)',
  'K8s Pod CrashLoopBackOff 排查 playbook': 'K8s Pod CrashLoopBackOff diagnosis (playbook)',
  'MySQL 慢查询排查 playbook': 'MySQL slow-query diagnosis (playbook)',
  '容器 cgroup 限制与 systemd-cgls 排查 playbook': 'Container cgroup limits & systemd-cgls diagnosis (playbook)',
  'K8s PVC 绑定失败排查 playbook': 'K8s PVC binding failure diagnosis (playbook)',
  'conntrack 表满 + NAT 排查': 'conntrack table full + NAT diagnosis',
  'eBPF 网络丢包排查': 'eBPF network packet-loss diagnosis',
  'ping / traceroute / mtr 三件套': 'ping / traceroute / mtr — the three-piece set',
  'K8s Pod OOMKilled 排查 playbook': 'K8s Pod OOMKilled diagnosis (playbook)',
  'Linux OOM Killer 排查 playbook': 'Linux OOM Killer diagnosis (playbook)',
  'Kubernetes etcd 性能撞墙排查 playbook': 'Kubernetes etcd performance-wall diagnosis (playbook)',
  'Linux 进程 D state 排查 playbook': 'Linux D-state process diagnosis (playbook)',
  'K8s Pod 卡 Pending 排查 playbook': 'K8s Pod stuck Pending diagnosis (playbook)',
  'Linux 服务器时区错乱排查 playbook': 'Linux server timezone misconfiguration diagnosis (playbook)',
  'Linux load 高但 CPU 空闲排查 playbook': 'Linux high-load with idle-CPU diagnosis (playbook)',
  'Linux dmesg ringbuffer 用法与坑 playbook': 'Linux dmesg ring buffer — usage & pitfalls (playbook)',
  'MySQL 连接数打满排查 playbook': 'MySQL max-connections exhaustion diagnosis (playbook)',
  'MTU / MSS / PMTUD 黑洞排查': 'MTU / MSS / PMTUD black-hole diagnosis',
  'K8s pod 网络连不通 - netshoot 排查': 'K8s pod network unreachable — netshoot diagnosis',
  '磁盘 inode 占满排查 playbook': 'Disk inode exhaustion diagnosis (playbook)',
  'systemd timer 入门与替代 cron playbook': 'systemd timer primer & cron replacement (playbook)',
  'strace 入门排查 playbook': 'strace primer for diagnostics (playbook)',
  'K8s ImagePullBackOff 排查 playbook': 'K8s ImagePullBackOff diagnosis (playbook)',
  'TCP RST 排查 playbook': 'TCP RST diagnosis (playbook)',
  'iostat 解读与磁盘 IO 瓶颈定位 playbook': 'iostat interpretation & disk IO bottleneck (playbook)',
  'Docker overlay2 占满磁盘排查 playbook': 'Docker overlay2 disk exhaustion diagnosis (playbook)',
  'cron 没跑 / 不执行排查 playbook': 'cron not running / not executing diagnosis (playbook)',
  'Docker 启动慢 / hang 排查 playbook': 'Docker slow startup / hang diagnosis (playbook)',
  'Linux 网络命名空间排查思路': 'Linux network namespace diagnosis approach',
  'Linux fork bomb 防护与排查 playbook': 'Linux fork-bomb protection & diagnosis (playbook)',
  'Linux 网卡丢包 / 性能瓶颈排查': 'Linux NIC packet-loss / performance bottleneck diagnosis',
  'Linux DNS 解析故障排查': 'Linux DNS resolution failure diagnosis',
  'MongoDB Oplog 与复制集延迟排查 playbook': 'MongoDB Oplog & replica-set lag diagnosis (playbook)',
  'Linux ulimit 与文件句柄耗尽排查 playbook': 'Linux ulimit & fd exhaustion diagnosis (playbook)',
  'TLS 握手失败诊断（openssl s_client）': 'TLS handshake failure diagnosis (openssl s_client)',
  'lsof 找文件占用与已删除大文件 playbook': 'lsof — finding file usage & deleted big files (playbook)',
  'PostgreSQL VACUUM 与膨胀排查 playbook': 'PostgreSQL VACUUM & bloat diagnosis (playbook)',
  // First-party diagnostic playbooks shipped Chinese-only (diagnostics/*-cn.md);
  // map their titles so en-US mode shows English instead of the raw Chinese.
  '集群异常 / 告警风暴怎么排查': 'Cluster anomaly / alert-storm diagnosis',
  '磁盘满了怎么排查': 'Disk full diagnosis',
  '内存高 / OOM 怎么排查': 'High memory / OOM diagnosis',
  '网络慢 / 连不通怎么排查': 'Network slow / unreachable diagnosis',
  '服务挂了 / systemd 失败怎么排查': 'Service down / systemd failure diagnosis',
  '权威技术文档参考索引': 'Authoritative technical docs index',
};

/** 文档展示标题：en-US 优先 title_en，再查静态表，最后用 title；zh-CN 用 title。 */
export function localizedDocTitle(input: KnowledgeDoc | string): string {
  if (typeof input === 'string') {
    const en = KNOWLEDGE_TITLES[input];
    return en ? trInline(input, en) : input;
  }
  if (input.title_en && input.title_en.trim() !== '') {
    return trInline(input.title, input.title_en);
  }
  const en = KNOWLEDGE_TITLES[input.title];
  return en ? trInline(input.title, en) : input.title;
}

/** 按当前语言翻译路径各段。 */
export function localizedPath(path: string | null | undefined): string {
  if (!path) return path ?? '';
  return path.split('/').map(localizedPathSegment).join('/');
}

/** 翻译单个路径段（无斜杠）。 */
export function localizedPathSegment(seg: string): string {
  const m = KNOWLEDGE_PATH_SEGMENTS[seg];
  return m ? trInline(m.zh, m.en) : seg;
}

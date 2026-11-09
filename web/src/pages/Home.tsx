import { useAssistantPage } from '@/lib/assistantPage';



import { useEffect, useMemo, useState } from 'react';



import { useNavigate } from 'react-router-dom';



import {



  Activity,



  AlertTriangle,



  Bell,



  Database,



  FileSearch,



  Flame,



  Gauge,



  HardDrive,



  History,



  ListTree,



  Network,



  Search,



  ShieldAlert,



  TrendingUp,



} from 'lucide-react';



import { ChatInput, type ModelSelection } from '@/components/ChatInput';



import { useModelSelection } from '@/store/modelSelection';

import { useChatPrefs } from '@/store/chatPrefs';



import { PromptCard } from '@/components/PromptCard';
import { createSession, listModels, type LLMProvider } from '@/api/chat';
import { listAgents, type AgentSummary } from '@/api/agents';





import { useI18n } from '@/i18n/locale';







// 首页问候语：助理向用户报到的语气；mount 时抽一条，re-render 不重抽。



type Greeting = { zh: string; en: string };



const GREETINGS: Greeting[] = [



  { zh: '听候差遣', en: 'At your service.' },



  { zh: '随时待命', en: 'Ready when you are.' },



  { zh: '今天能为你做些什么？', en: 'What can I help with today?' },



  { zh: '说出你的目标，剩下交给我', en: "Tell me the goal — I'll handle the rest." },



  { zh: '想从哪儿开始？', en: 'Where do you want to start?' },



  { zh: '需要诊断、对比，还是只想瞅瞅？', en: 'Diagnose, compare, or just browse?' },



  { zh: '让我们看看现在最值得关心的事', en: "Let's tackle what matters most." },



  { zh: '让我们从最严重的那条开始', en: 'Start with the most critical one.' },



  { zh: '让我们一起把这班守好', en: "Let's work the shift together." },



  { zh: '让告警先过我这一关', en: 'I can triage the alerts first.' },



  { zh: '让我先帮你看一眼集群', en: 'Let me check the cluster.' },



  { zh: '让我帮你把头绪理一理', en: 'Let me help you untangle this.' },



  { zh: '让我们一起把它解决掉', en: "Let's solve this." },



  { zh: '想看看哪条线索？', en: 'Which lead first?' },



  { zh: '需要我先做些什么？', en: 'Where should I start?' },



  { zh: '随便问，我会给你一个起点', en: "Ask anything — I'll find a starting point." },



];







// PROMPT_POOL is the full set of starter prompts; sample 4 each render



// so users see variety.



type PromptPreset = {



  titleZh: string; titleEn: string;



  descZh: string; descEn: string;



  promptZh: string; promptEn: string;



  icon: typeof Flame;



};



const PROMPT_POOL: PromptPreset[] = [



  { titleZh: '找出资源最紧张的 3 台设备', titleEn: 'Top 3 busiest devices',



    descZh: 'CPU / 内存 / 负载综合排序', descEn: 'Ranked by CPU, memory, and load',



    promptZh: '找出当前 CPU、内存或负载最紧张的 3 台设备，给出关键指标和判断依据。',



    promptEn: 'Find the 3 busiest devices by CPU, memory, or load. Show key metrics and why.', icon: Flame },



  { titleZh: '看一眼整个集群健康状况', titleEn: 'Cluster health at a glance',



    descZh: '一句话掌握全局', descEn: 'One-line fleet summary',



    promptZh: '帮我总览所有设备的健康状况：在线 / 离线分布、平均 CPU / 内存 / 磁盘占用，以及任何明显异常。',



    promptEn: 'Summarise all devices: online vs offline, average CPU, memory, disk, and any obvious anomalies.', icon: Activity },



  { titleZh: '列出离线超过 24 小时的设备', titleEn: 'List devices offline > 24h',



    descZh: '找掉线节点', descEn: 'Find disconnected nodes',



    promptZh: '列出离线超过 24 小时的设备，按最后在线时间倒序展示。',



    promptEn: 'List devices offline for over 24h, sorted by last-seen descending.', icon: AlertTriangle },



  { titleZh: '对比本周和上周的整体负载', titleEn: 'Compare load: this week vs. last',



    descZh: '看趋势变化', descEn: 'See the trend',



    promptZh: '对比本周和上周所有设备的整体 CPU、内存、负载趋势，指出变化最大的节点。',



    promptEn: "Compare this week's vs. last week's CPU / memory / load trend across all devices; highlight the nodes that changed most.", icon: TrendingUp },



  { titleZh: '当前最严重的活跃告警', titleEn: 'Top active alerts',



    descZh: '哪些事在烧', descEn: "What's on fire",



    promptZh: '列出当前所有未解决的告警，按 severity 排序，给我每条告警的目标设备 + 触发原因摘要。',



    promptEn: 'List unresolved alerts by severity; for each, the target device and a one-line reason.', icon: ShieldAlert },



  { titleZh: '过去 24 小时新增告警有哪些', titleEn: 'New alerts in the last 24h',



    descZh: '看夜班发生了什么', descEn: 'What the night shift saw',



    promptZh: '过去 24 小时内首次触发的告警有哪些？按时间倒序列，每条给规则名 / 设备 / severity。',



    promptEn: 'Which alerts fired for the first time in the last 24h? Newest first, with rule, device, and severity.', icon: Bell },



  { titleZh: '诊断最近一条 critical 告警', titleEn: 'Diagnose the latest critical alert',



    descZh: '关联 metric / log / trace', descEn: 'Correlate metrics, logs, traces',



    promptZh: '查最近一条 critical 级别的告警，做完整的根因关联分析（metric + 日志 + trace），给出 3 段输出：现象 / 关联信号 / 假设。',



    promptEn: 'Take the latest critical alert and run a full root-cause correlation across metrics, logs, and traces. Output three sections: symptoms, correlated signals, hypotheses.', icon: AlertTriangle },



  { titleZh: '哪些设备磁盘快满了', titleEn: 'Which devices are running out of disk',



    descZh: '提前发现容量问题', descEn: 'Catch capacity issues early',



    promptZh: '列出磁盘使用率超过 80% 的设备，按使用率降序排，对前 3 台给出 top 占用目录。',



    promptEn: 'Devices over 80% disk usage, sorted descending. For the top 3, also list the largest directories.', icon: HardDrive },



  { titleZh: '某台设备的 top 大文件', titleEn: 'Top large files on a device',



    descZh: '帮我找空间被谁吃掉了', descEn: 'Find what is eating the space',



    promptZh: '在使用率最高的那台设备上，列出 / 目录下 top 20 大文件，按 size 降序。',



    promptEn: 'On the device with the highest disk usage, list the 20 largest files under /, biggest first.', icon: FileSearch },



  { titleZh: '哪台设备的某个进程占资源最多', titleEn: 'Find the heaviest processes',



    descZh: '定位异常进程', descEn: 'Pinpoint runaway processes',



    promptZh: '看一下当前所有设备里 top CPU + top 内存 进程，列出前 5 个，标出对应设备。',



    promptEn: 'Across all devices, the top 5 processes by CPU and the top 5 by memory, tagged with the device.', icon: ListTree },



  { titleZh: '检测异常波动的设备', titleEn: 'Spot anomalous devices',



    descZh: 'z-score 离群点', descEn: 'z-score outliers',



    promptZh: '在所有设备里，找最近 2 小时 CPU 或内存 z-score 偏离基线最远的设备（异常 outlier）。',



    promptEn: 'Find the devices whose CPU or memory z-score has drifted farthest from baseline in the last 2 hours.', icon: Gauge },



  { titleZh: '搜索最近的错误日志', titleEn: 'Search recent error logs',



    descZh: '快速定位 error / panic', descEn: 'Find error, panic, OOM fast',



    promptZh: '查所有设备最近 1 小时内的 error / panic / OOM 日志，按设备和频次聚合。',



    promptEn: 'Search error / panic / OOM logs in the last hour across all devices, grouped by device and frequency.', icon: Search },



  { titleZh: '看一台设备的实时负载', titleEn: 'Live view: one device',



    descZh: '即时 cpu/mem/load', descEn: 'Real-time CPU, memory, load',



    promptZh: '随便挑一台在线设备，给我看它现在的 CPU / 内存 / 负载（实时）+ 最近 24h 趋势对比。',



    promptEn: 'Pick an online device and show its current CPU / memory / load alongside the 24h trend.', icon: Activity },



  { titleZh: '对比设备之间的网络流量', titleEn: 'Compare network traffic',



    descZh: 'rx/tx 排序', descEn: 'Rank by rx/tx',



    promptZh: '比较所有设备最近 1 小时入站 / 出站流量，找最高的 3 台并解读对比。',



    promptEn: 'Compare rx/tx traffic over the last hour; surface the top 3 devices and explain the gap.', icon: Network },



  { titleZh: '看这条规则最近的触发频次', titleEn: 'Rule firing frequency',



    descZh: '规则疲劳分析', descEn: 'Alert-fatigue analysis',



    promptZh: '列出过去 7 天触发频次最高的 5 条告警规则，对每条给触发分布 + 代表设备。',



    promptEn: 'List the top 5 noisiest alert rules in the past week, with firing distribution and a sample device.', icon: History },



  { titleZh: '集群整体存储增长', titleEn: 'Cluster-wide storage growth',



    descZh: '容量规划视角', descEn: 'Capacity-planning view',



    promptZh: '所有设备过去 7 天磁盘使用增长趋势（百分点 / 天），找出增长最快的 3 台并预估到 90% 的剩余天数。',



    promptEn: 'Disk usage growth (pp/day) over the past week. Surface the 3 fastest-growing and estimate days until 90% full.', icon: Database },



  { titleZh: '分析数据库状态', titleEn: 'Analyze database status',



    descZh: 'MySQL / PostgreSQL / Redis / MongoDB', descEn: 'MySQL / PostgreSQL / Redis / MongoDB',



    promptZh: '分析当前数据库状态，覆盖 MySQL、PostgreSQL、Redis、MongoDB；按异常优先输出总体结论、每个数据库的关键指标、风险和证据。',



    promptEn: 'Analyze current database status across MySQL, PostgreSQL, Redis, and MongoDB. Prioritize anomalies and include the overall conclusion, key metrics, risks, and evidence for each database.', icon: Database },



  { titleZh: '配置一条 CPU 告警', titleEn: 'Configure a CPU alert',



    descZh: '先草案试算，确认后应用', descEn: 'Draft and preview before applying',



    promptZh: '配置一条 CPU 超过 90% 且持续 5 分钟的 warning 告警，先生成草案并试算，不要直接应用。',



    promptEn: 'Configure a warning alert for CPU over 90% for 5 minutes. Generate a draft with preview first; do not apply it yet.', icon: Bell },



];







function samplePrompts(n: number): typeof PROMPT_POOL {



  const pool = [...PROMPT_POOL];



  for (let i = pool.length - 1; i > 0; i--) {



    const j = Math.floor(Math.random() * (i + 1));



    [pool[i], pool[j]] = [pool[j], pool[i]];



  }



  return pool.slice(0, n);



}







export default function HomePage() {



  const { tr } = useI18n();



  const navigate = useNavigate();



  const [draft, setDraft] = useState('');



  const [submitting, setSubmitting] = useState(false);



  const [error, setError] = useState<string | null>(null);





  const [providers, setProviders] = useState<LLMProvider[]>([]);



  // Model selection lives in a persisted store (shared with ChatThread), so a



  // pick survives navigation + reload and the launched session inherits it.



  // selected==null → fall back to the live catalog default.



  const storeModel = useModelSelection((s) => s.selected);



  const setStoreModel = useModelSelection((s) => s.setSelected);



  const [catalogDefault, setCatalogDefault] = useState<ModelSelection | null>(null);



  const selectedModel = storeModel ?? catalogDefault;



  // SearXNG ships zero-key zero-quota in our compose stack — leave on by default.



  const webSearchEnabled = useChatPrefs((s) => s.webSearchEnabled);
  const setWebSearchEnabled = useChatPrefs((s) => s.setWebSearchEnabled);
  const thinkingEnabled = useChatPrefs((s) => s.thinkingEnabled);
  const setThinkingEnabled = useChatPrefs((s) => s.setThinkingEnabled);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState('default');







  // 进首页时随机一条问候 + 4 张 prompt 卡；mount 期间不变。



  const greetingPair = useMemo(() => GREETINGS[Math.floor(Math.random() * GREETINGS.length)], []);



  const greeting = tr(greetingPair.zh, greetingPair.en);



  // Pin the webpage-generator card first, then 3 random suggestions.



  const prompts = useMemo(() => samplePrompts(4), []);

  useEffect(() => {
    let cancelled = false;
    listAgents()
      .then((r) => {
        if (cancelled) return;
        if (!Array.isArray(r.items)) {
          throw new Error('助理列表缺少 items');
        }
        if (r.items.length === 0) {
          throw new Error('助理列表为空');
        }
        setAgents(r.items);
        setSelectedAgentId((cur) =>
          r.items.some((a) => a.name === cur) ? cur : r.items[0].name,
        );
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);








  useEffect(() => {



    let cancelled = false;



    listModels()



      .then((cat) => {



        if (cancelled) return;



        if (!cat.providers) {



          throw new Error('模型目录缺少 providers');



        }



        if (!cat.default?.provider || !cat.default?.model) {



          throw new Error('模型目录缺少 default.provider/model');



        }



        setProviders(cat.providers);



        const d: ModelSelection = { provider: cat.default.provider, model: cat.default.model };



        setCatalogDefault(d);
        // 只写目录默认；用户上次选择在 store 里，不能被目录加载冲掉
        const stored = useModelSelection.getState().selected;
        if (stored !== null) {
          const provider = cat.providers.find((p) => p.id === stored.provider);
          const models = provider?.models;
          if (
            provider === undefined ||
            !Array.isArray(models) ||
            !models.includes(stored.model)
          ) {
            setStoreModel(null);
          }
        }
      })



      .catch((e: Error) => {



        if (!cancelled) setError(e.message);



      });



    return () => {



      cancelled = true;



    };



  }, [setStoreModel]);

  async function handleModelChange(sel: ModelSelection | null) {



    if (!sel?.provider || !sel?.model) {



      throw new Error('模型选择缺少 provider/model');



    }



    setStoreModel(sel);



  }







  async function startSession(content: string, attachments: File[] = []) {







    if ((!content.trim() && attachments.length === 0) || submitting) return;







    if (!selectedModel?.provider || !selectedModel?.model) {







      setError(tr('请先在配置-模型中选择可用模型', 'Select a model from Settings → LLM first'));







      return;







    }







    setError(null);



    setSubmitting(true);



    try {



      const title = content.trim().slice(0, 30) || attachments[0]?.name || tr('图片会话', 'Image conversation');



      const session = await createSession({



        title,



        agent_id: selectedAgentId,



        provider: selectedModel.provider,



        model: selectedModel.model,



      });



      // Don't post here — ChatThread takes the initialPrompt and runs it



      // through the SSE streamMessage path so the user sees tool cards and



      // the assistant reply incrementally. The picked model is already stored



      // on the new session, so every browser inherits the same route.



      navigate(`/chat/${session.id}`, { state: { initialPrompt: content, initialAttachments: attachments, thinkingEnabled, webSearchEnabled } });



    } catch (err) {



      setError((err as Error).message || tr('创建会话失败', 'Failed to create session'));



      setSubmitting(false);



    }



  }














  useAssistantPage('首页', 'Home', {





    model: selectedModel?.model,



    web_search: webSearchEnabled,



  });







  return (



    <main className="flex min-w-0 flex-1 flex-col overflow-hidden">



      <div className="flex flex-1 flex-col overflow-y-auto px-0 py-8 sm:pb-[10vh] sm:pt-8">



        <div className="mx-auto my-auto flex w-full max-w-4xl shrink-0 flex-col items-stretch px-6">



          <h1 className="mb-8 mt-8 text-center text-3xl font-semibold tracking-tight text-zinc-100">



            {greeting}



          </h1>







          <ChatInput



            value={draft}



            onChange={setDraft}



            onSubmit={(p) => {



              setDraft('');



              void startSession(p.text, p.attachments);



            }}



            allowAttachments



            disabled={submitting}



            autoFocus



            providers={providers}



            selectedModel={selectedModel}



            onModelChange={handleModelChange}



            webSearchEnabled={webSearchEnabled}
            onWebSearchToggle={setWebSearchEnabled}
            thinkingEnabled={thinkingEnabled}
            onThinkingToggle={setThinkingEnabled}
            agents={agents}
            selectedAgentId={selectedAgentId}
            onAgentChange={setSelectedAgentId}



          />







          {error && (



            <div



              role="alert"



              className="mt-3 rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-[11px] text-red-300"



            >



              {error}



            </div>



          )}







          <div className="mt-10">



            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              {prompts.map((p) => (
                <PromptCard
                  key={p.titleEn}
                  title={tr(p.titleZh, p.titleEn)}
                  description={tr(p.descZh, p.descEn)}
                  icon={p.icon}
                  onClick={() => void startSession(tr(p.promptZh, p.promptEn))}
                />
              ))}
            </div>



          </div>



        </div>



      </div>



    </main>



  );



}




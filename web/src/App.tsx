import { AgentSidePanel } from '@/components/AgentSidePanel';
import { AssistantRoute } from '@/lib/assistantPage';
import { Suspense, lazy, type ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { safeLoginReturn } from '@/lib/loginReturn';
import { Layout } from '@/components/Layout';
import { AppToaster } from '@/components/ui/Toast';
import { ChatLayout } from '@/components/ChatLayout';
import { useAuth } from '@/store/auth';

const LoginPage = lazy(() => import('@/pages/Login'));
const HomePage = lazy(() => import('@/pages/Home'));
const ChatThreadPage = lazy(() => import('@/pages/ChatThread'));
const SkillsPage = lazy(() => import('@/pages/Skills'));
const ToolsPage = lazy(() => import('@/pages/Tools'));
const ApprovalsPage = lazy(() => import('@/pages/Approvals'));
const ApprovalDetailPage = lazy(() => import('@/pages/ApprovalDetail'));
const AgentsPage = lazy(() => import('@/pages/Agents'));
const McpPage = lazy(() => import('@/pages/Mcp'));
const WorkflowsPage = lazy(() => import('@/pages/Workflows'));
const TriggersPage = lazy(() => import('@/pages/Triggers'));
const MemoryPage = lazy(() => import('@/pages/Memory'));
const PublishPage = lazy(() => import('@/pages/Publish'));
const EvalsPage = lazy(() => import('@/pages/Evals'));
const EvalDatasetPage = lazy(() => import('@/pages/EvalDataset'));
const WorkflowEditorPage = lazy(() => import('@/pages/WorkflowEditor'));
const WorkflowRunPage = lazy(() => import('@/pages/WorkflowRun'));
const SandboxPage = lazy(() => import('@/pages/Sandbox'));
const KnowledgePage = lazy(() => import('@/pages/Knowledge'));
const KnowledgeSourcesPage = lazy(() => import('@/pages/KnowledgeSources'));
const SessionsPage = lazy(() => import('@/pages/Sessions'));
const ObservabilityPage = lazy(() => import('@/pages/Observability'));
const AuditPage = lazy(() => import('@/pages/Audit'));
const UsersPage = lazy(() => import('@/pages/Users'));
const ShareViewPage = lazy(() => import('@/pages/ShareView'));
const EvalShareViewPage = lazy(() => import('@/pages/EvalShareView'));
const SettingsLayout = lazy(() => import('@/pages/SettingsLayout'));
const SettingsLLM = lazy(() => import('@/pages/settings/LLM'));
const SettingsPreferences = lazy(() => import('@/pages/settings/Preferences'));
const SettingsAgent = lazy(() => import('@/pages/settings/Agent'));
const SettingsKnowledge = lazy(() => import('@/pages/settings/Knowledge'));
const SettingsAbout = lazy(() => import('@/pages/settings/About'));
const SettingsSecrets = lazy(() => import('@/pages/settings/Secrets'));

function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuth((s) => s.token);
  const location = useLocation();
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search + location.hash }} />;
  }
  return (
    <AssistantRoute.Provider value={location.key}>
      {children}
      <AgentSidePanel />
    </AssistantRoute.Provider>
  );
}

function PublicOnly({ children }: { children: ReactNode }) {
  const token = useAuth((s) => s.token);
  const location = useLocation();
  if (token) return <Navigate to={safeLoginReturn(location.state?.from)} replace />;
  return <>{children}</>;
}

export default function App() {
  const location = useLocation();
  return (
    <>
      {/* 轻提示全局挂载一次（sonner） */}
      <AppToaster />
      {/* 公开路由（登录/分享页）在 Layout 之外，lazy 分块必须有 Suspense 兜底，
      否则首次加载分块挂起时整页白屏 */}
      <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-[11px] text-zinc-500">
          加载中…
        </div>
      }
    >
    <Routes>
      <Route
        path="/login"
        element={
          <PublicOnly>
            <LoginPage />
          </PublicOnly>
        }
      />
      <Route path="/share/:token" element={<ShareViewPage />} />
      <Route path="/eval-share/:token" element={<EvalShareViewPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route element={<ChatLayout />}>
          <Route path="/" element={<HomePage key={location.key} />} />
          <Route path="/chat/:sessionId" element={<ChatThreadPage />} />
        </Route>
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="/skills" element={<SkillsPage />} />
        <Route path="/tools" element={<ToolsPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/approvals/:id" element={<ApprovalDetailPage key={location.pathname} />} />
        <Route path="/observability" element={<ObservabilityPage />} />
        <Route path="/sandbox" element={<SandboxPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/mcp" element={<McpPage />} />
        <Route path='/workflows' element={<WorkflowsPage />} />
        <Route path='/triggers' element={<TriggersPage />} />
        <Route path='/memory' element={<MemoryPage />} />
        <Route path='/publish' element={<PublishPage />} />
        <Route path='/evals' element={<EvalsPage />} />
        <Route path='/evals/:datasetId' element={<EvalDatasetPage key={location.pathname} />} />
        <Route path="/workflows/runs/:runUid" element={<WorkflowRunPage />} />
        <Route path="/workflows/:id" element={<WorkflowEditorPage key={location.pathname} />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/knowledge/sources" element={<KnowledgeSourcesPage />} />
        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<Navigate to="llm" replace />} />
          <Route path="llm" element={<SettingsLLM />} />
          <Route path="secrets" element={<SettingsSecrets />} />
          <Route path="agent" element={<SettingsAgent />} />
          <Route path="knowledge" element={<SettingsKnowledge />} />
          <Route path="preferences" element={<SettingsPreferences />} />
          <Route path="about" element={<SettingsAbout />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
      </Routes>
      </Suspense>
    </>
  );
}

import { AgentSidePanel } from '@/components/AgentSidePanel';
import { AssistantRoute } from '@/lib/assistantPage';
import { lazy, type ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { safeLoginReturn } from '@/lib/loginReturn';
import { Layout } from '@/components/Layout';
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
const WorkflowEditorPage = lazy(() => import('@/pages/WorkflowEditor'));
const WorkflowRunPage = lazy(() => import('@/pages/WorkflowRun'));
const SandboxPage = lazy(() => import('@/pages/Sandbox'));
const KnowledgePage = lazy(() => import('@/pages/Knowledge'));
const SessionsPage = lazy(() => import('@/pages/Sessions'));
const ObservabilityPage = lazy(() => import('@/pages/Observability'));
const AuditPage = lazy(() => import('@/pages/Audit'));
const UsersPage = lazy(() => import('@/pages/Users'));
const ShareViewPage = lazy(() => import('@/pages/ShareView'));
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
        <Route path="/workflows" element={<WorkflowsPage />} />
        <Route path="/workflows/runs/:runUid" element={<WorkflowRunPage />} />
        <Route path="/workflows/:id" element={<WorkflowEditorPage key={location.pathname} />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
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
  );
}

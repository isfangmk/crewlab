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
const SkillRunPage = lazy(() => import('@/pages/SkillRun'));
const AgentsPage = lazy(() => import('@/pages/Agents'));
const McpPage = lazy(() => import('@/pages/Mcp'));
const KnowledgePage = lazy(() => import('@/pages/Knowledge'));
const SettingsLayout = lazy(() => import('@/pages/SettingsLayout'));
const SettingsLLM = lazy(() => import('@/pages/settings/LLM'));
const SettingsPreferences = lazy(() => import('@/pages/settings/Preferences'));
const SettingsAgent = lazy(() => import('@/pages/settings/Agent'));
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
        <Route path="/skills" element={<SkillsPage />} />
        <Route path="/tools" element={<ToolsPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/approvals/:id" element={<ApprovalDetailPage key={location.pathname} />} />
        <Route path="/skills/:key" element={<SkillRunPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/mcp" element={<McpPage />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<Navigate to="llm" replace />} />
          <Route path="llm" element={<SettingsLLM />} />
          <Route path="secrets" element={<SettingsSecrets />} />
          <Route path="agent" element={<SettingsAgent />} />
          <Route path="preferences" element={<SettingsPreferences />} />
          <Route path="about" element={<SettingsAbout />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

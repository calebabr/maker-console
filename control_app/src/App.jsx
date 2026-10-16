import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext.jsx';
import { DialogProvider } from './context/DialogContext.jsx';
import { DisplayProvider } from './context/DisplayContext.jsx';
import { StatusProvider } from './context/StatusContext.jsx';
import Header from './components/Header.jsx';
import NavBar from './components/NavBar.jsx';
import WakingBanner from './components/WakingBanner.jsx';
import WhatsNew from './components/WhatsNew.jsx';
import UpdateBanner from './components/UpdateBanner.jsx';
import PageTitle from './components/PageTitle.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import Login from './pages/Login.jsx';
import Overview from './pages/Overview.jsx';
import Markets from './pages/Markets.jsx';
import Activity from './pages/Activity.jsx';
import Health from './pages/Health.jsx';
import Settings from './pages/Settings.jsx';
import Mode from './pages/Mode.jsx';
import Server from './pages/Server.jsx';
import Logs from './pages/Logs.jsx';
import Arbitrage from './pages/Arbitrage.jsx';
import Terminology from './pages/Terminology.jsx';
import Analytics from './pages/Analytics.jsx';
import More from './pages/More.jsx';
import Account from './pages/Account.jsx';
import Alerts from './pages/Alerts.jsx';

function SignedIn() {
  const location = useLocation();
  return (
    <StatusProvider>
      <div className="shell">
        <Header />
        <div className="shell-notices">
          <WakingBanner />
          <UpdateBanner />
          <WhatsNew />
        </div>
        <main className="content">
          {/* Sticky page name under the header; outside the boundary so a page crash keeps it. */}
          <PageTitle />
          {/* Page errors stay inside <main>; Header (Stop) and NavBar keep working. */}
          <ErrorBoundary resetKey={`${location.pathname}${location.hash}`}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/markets" element={<Markets />} />
              <Route path="/activity" element={<Activity />} />
              <Route path="/health" element={<Health />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/mode" element={<Mode />} />
              <Route path="/server" element={<Server />} />
              <Route path="/logs" element={<Logs />} />
              <Route path="/arb" element={<Arbitrage />} />
              <Route path="/terminology" element={<Terminology />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/more" element={<More />} />
              <Route path="/account" element={<Account />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route path="/login" element={<Navigate to="/" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </ErrorBoundary>
        </main>
        <NavBar />
      </div>
    </StatusProvider>
  );
}

function Gate() {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  return <SignedIn />;
}

// Everything except the router, so tests can wrap it in a MemoryRouter.
export function AppShell() {
  return (
    <DisplayProvider>
      <AuthProvider>
        <DialogProvider>
          <Gate />
        </DialogProvider>
      </AuthProvider>
    </DisplayProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}

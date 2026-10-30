import { Link } from 'react-router-dom';
import StatusBadge from './StatusBadge.jsx';
import StopButton from './StopButton.jsx';
import PriorLogo from './PriorLogo.jsx';
import { useStatus } from '../context/StatusContext.jsx';
import { isMockMode } from '../services/api.js';
import { APP_NAME } from '../utils/routes.js';

// Sticky header on every signed-in page: Prior lockup + status badge + compact STOP (always visible).
export default function Header() {
  const { status } = useStatus();
  return (
    <header className="appbar">
      <Link to="/" className="appbar-title" aria-label={`${APP_NAME} home`}>
        <PriorLogo size={24} />
        <span className="appbar-name">{APP_NAME}</span>
        {isMockMode() && <span className="mock-tag">MOCK</span>}
      </Link>
      <div className="appbar-right">
        <StatusBadge status={status} compact />
        <StopButton compact />
      </div>
    </header>
  );
}

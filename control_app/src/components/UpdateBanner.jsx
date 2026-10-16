import { useStatus } from '../context/StatusContext.jsx';
import { APP_VERSION, isOlder } from '../utils/version.js';

// Version handshake: the server reports the oldest app it supports (min_app_version on /api/status).
// Informational only. It never hides data or blocks an action, and Stop stays in the header.
export default function UpdateBanner({ appVersion = APP_VERSION }) {
  const { status } = useStatus();
  const min = status && status.min_app_version;
  if (!isOlder(appVersion, min)) return null;
  return (
    <div className="banner banner-update" role="status">
      <span>Update available, reopen the app. You can keep using it meanwhile; Stop still works.</span>
    </div>
  );
}

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, clearToken, getToken, onUnauthorized, setToken } from '../services/api.js';

// Login flow: password -> mfa_token (5 min) -> 6-digit code -> access token.
// The access token is kept in memory (api.js) and sessionStorage, never localStorage.

const AuthContext = createContext(null);
const USER_KEY = 'km.user';

function readUser() {
  try {
    return sessionStorage.getItem(USER_KEY);
  } catch {
    return null;
  }
}
function writeUser(u) {
  try {
    if (u) sessionStorage.setItem(USER_KEY, u);
    else sessionStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export function AuthProvider({ children }) {
  const navigate = useNavigate();
  const [token, setTok] = useState(() => getToken());
  const [username, setUsername] = useState(() => (getToken() ? readUser() : null));
  const [mfa, setMfa] = useState(null); // { token, expiresAt, username }
  const [expired, setExpired] = useState(false);

  useEffect(
    () =>
      onUnauthorized((detail) => {
        setTok(null);
        setUsername(null);
        writeUser(null);
        setMfa(null);
        setExpired(detail === 'session_expired' ? 'session_expired' : true);
        navigate('/login', { replace: true });
      }),
    [navigate],
  );

  const login = useCallback(async (user, password) => {
    const r = await api.login(user, password);
    setExpired(false);
    setMfa({
      token: r.mfa_token,
      expiresAt: Date.now() + (r.expires_in || 300) * 1000,
      username: user,
    });
  }, []);

  const verify = useCallback(
    async (code) => {
      if (!mfa) throw new Error('Start Again: Enter your username and password.');
      if (Date.now() > mfa.expiresAt) {
        setMfa(null);
        throw new Error('The sign-in step expired (5 min). Enter your password again.');
      }
      const r = await api.verify2fa(mfa.token, code);
      setToken(r.access_token);
      setTok(r.access_token);
      setUsername(mfa.username);
      writeUser(mfa.username);
      setMfa(null);
      navigate('/', { replace: true });
    },
    [mfa, navigate],
  );

  const cancelMfa = useCallback(() => setMfa(null), []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* token may already be invalid; clear locally anyway */
    }
    clearToken();
    writeUser(null);
    setTok(null);
    setUsername(null);
    setExpired(false);
    navigate('/login', { replace: true });
  }, [navigate]);

  // Local-only sign-out after POST /api/auth/logout-all (the token is already revoked).
  const endSession = useCallback(
    (reason = 'logged_out_all') => {
      clearToken();
      writeUser(null);
      setTok(null);
      setUsername(null);
      setMfa(null);
      setExpired(reason);
      navigate('/login', { replace: true });
    },
    [navigate],
  );

  const value = useMemo(
    () => ({
      isAuthenticated: !!token,
      username,
      mfaPending: !!mfa,
      mfaUsername: mfa ? mfa.username : null,
      expired,
      login,
      verify,
      cancelMfa,
      logout,
      endSession,
    }),
    [token, username, mfa, expired, login, verify, cancelMfa, logout, endSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

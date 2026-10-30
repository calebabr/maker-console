import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { applyTextSize, applyTheme, readTextSize, readTheme } from '../utils/theme.js';

/**
 * Per-device display preferences: colour theme and text size.
 *   const { theme, setTheme, textSize, setTextSize } = useDisplayPrefs();
 * Stored in localStorage (prior.theme / prior.textSize), applied to :root immediately. Not a bot setting,
 * so there is no 2FA and nothing goes to the server.
 */
const DisplayContext = createContext(null);

export function DisplayProvider({ children }) {
  const [theme, setThemeState] = useState(readTheme);
  const [textSize, setTextSizeState] = useState(readTextSize);

  const setTheme = useCallback((id) => setThemeState(applyTheme(id, { persist: true })), []);
  const setTextSize = useCallback((size) => setTextSizeState(applyTextSize(size, { persist: true })), []);

  const value = useMemo(() => ({ theme, setTheme, textSize, setTextSize }), [theme, setTheme, textSize, setTextSize]);
  return <DisplayContext.Provider value={value}>{children}</DisplayContext.Provider>;
}

// Outside a provider (a page rendered on its own in a test) it still works: it reads the saved values and
// the setters apply and save them, they just don't re-render anything.
const FALLBACK = {
  get theme() {
    return readTheme();
  },
  get textSize() {
    return readTextSize();
  },
  setTheme: (id) => applyTheme(id, { persist: true }),
  setTextSize: (size) => applyTextSize(size, { persist: true }),
};

export function useDisplayPrefs() {
  return useContext(DisplayContext) || FALLBACK;
}

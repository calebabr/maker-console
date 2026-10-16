import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fs from 'node:fs';
import path from 'node:path';
import KeelLogo from '../components/KeelLogo.jsx';
import { DisplayProvider, useDisplayPrefs } from '../context/DisplayContext.jsx';
import {
  DEFAULT_TEXT_SIZE, DEFAULT_THEME, TEXT_SIZE_KEY, TEXT_SIZES, THEME_COLOR, THEME_IDS, THEME_KEY, THEMES, applyTextSize,
  applyTheme, initTheme, readTextSize, readTheme,
} from '../utils/theme.js';

// WCAG 2.x contrast ratio between two #rrggbb colours.
function lum(hex) {
  const n = parseInt(hex.slice(1), 16);
  const f = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f((n >> 16) & 255) + 0.7152 * f((n >> 8) & 255) + 0.0722 * f(n & 255);
}
function contrast(a, b) {
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
// `fg` at `alpha` over `bg`.
function over(fg, bg, alpha) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [f, b] = [p(fg), p(bg)];
  return '#' + f.map((c, i) => Math.round(c * alpha + b[i] * (1 - alpha)).toString(16).padStart(2, '0')).join('');
}

const VARS = ['bg', 'surface', 'surface2', 'border', 'text', 'muted', 'accent', 'accentText', 'ok', 'warn', 'danger', 'chart2', 'overlay'];

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('style');
  document.documentElement.removeAttribute('data-theme');
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('palettes', () => {
  it('has exactly the three themes, in order, with the hex values from the plan', () => {
    expect(THEME_IDS).toEqual(['midnight', 'teal', 'graphite']);
    expect(DEFAULT_THEME).toBe('midnight');
    expect(THEMES.midnight.colors).toMatchObject({
      bg: '#0f141b', surface: '#18202a', border: '#263241', text: '#e6edf3', muted: '#8b98a8', accent: '#4ea1ff',
      ok: '#3fb97a', warn: '#e3b341', danger: '#f06a6a', chart2: '#a78bfa',
    });
    expect(THEMES.teal.colors).toMatchObject({
      bg: '#0b1416', surface: '#12201f', border: '#1f3533', text: '#e3f1ee', muted: '#86a39d', accent: '#2dd4bf',
      ok: '#5ad08a', warn: '#f2b84b', danger: '#ef6b6b', chart2: '#f59e8b',
    });
    expect(THEMES.graphite.colors).toMatchObject({
      bg: '#121212', surface: '#1c1c1e', border: '#2c2c2e', text: '#ececec', muted: '#9a9a9e', accent: '#f5a524',
      ok: '#4cc38a', warn: '#ff8a3d', danger: '#ff5c5c', chart2: '#64b5f6',
    });
    expect(THEME_COLOR).toBe(THEMES.midnight.colors.bg);
  });

  it.each(THEME_IDS)('%s defines every CSS variable the contract lists', (id) => {
    for (const k of VARS) expect(THEMES[id].colors[k], `${id}.${k}`).toBeTruthy();
  });

  // Text must be readable (WCAG AA 4.5:1) wherever the app puts it; non-text marks need 3:1.
  describe.each(THEME_IDS)('WCAG contrast: %s', (id) => {
    const c = THEMES[id].colors;
    const grounds = { bg: c.bg, surface: c.surface, surface2: c.surface2 };
    for (const [gname, g] of Object.entries(grounds)) {
      for (const k of ['text', 'muted', 'accent', 'ok', 'warn', 'danger', 'chart2']) {
        it(`${k} on ${gname} >= 4.5`, () => {
          expect(contrast(c[k], g)).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
    it('button text >= 4.5 on the accent and danger fills (Stop is the danger fill)', () => {
      expect(contrast(c.accentText, c.accent)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c.dangerText, c.danger)).toBeGreaterThanOrEqual(4.5);
    });
    it('verdict / mode chips (tone text over its 12% tint on the surface) >= 4.5', () => {
      for (const k of ['ok', 'warn', 'danger', 'accent']) {
        expect(contrast(c[k], over(c[k], c.surface, 0.12))).toBeGreaterThanOrEqual(4.5);
      }
    });
    it('page-title text on the accent-tinted band >= 4.5 and the accent mark >= 3', () => {
      const band = over(c.accent, c.surface, 0.09);
      expect(contrast(c.text, band)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c.accent, band)).toBeGreaterThanOrEqual(3);
    });
    it('status dot grey (off) is visible on the page background (>= 3)', () => {
      expect(contrast(c.off, c.bg)).toBeGreaterThanOrEqual(3);
    });
    it('red, green and amber are three distinct colours (meanings never swap)', () => {
      expect(new Set([c.danger, c.ok, c.warn]).size).toBe(3);
    });
  });

  it('index.css fallback :root block equals Midnight Slate (no flash before JS)', () => {
    const css = fs.readFileSync(path.resolve(process.cwd(), 'src/index.css'), 'utf8');
    const block = css.match(/:root\s*\{([^}]*)\}/)[1];
    const get = (name) => (block.match(new RegExp(`--${name}:\\s*([^;]+);`)) || [])[1]?.trim();
    const m = THEMES.midnight.colors;
    expect(get('bg')).toBe(m.bg);
    expect(get('surface')).toBe(m.surface);
    expect(get('surface2')).toBe(m.surface2);
    expect(get('border')).toBe(m.border);
    expect(get('text')).toBe(m.text);
    expect(get('muted')).toBe(m.muted);
    expect(get('accent')).toBe(m.accent);
    expect(get('accent-text')).toBe(m.accentText);
    expect(get('ok')).toBe(m.ok);
    expect(get('warn')).toBe(m.warn);
    expect(get('danger')).toBe(m.danger);
    expect(get('chart2')).toBe(m.chart2);
  });
});

describe('applyTheme / applyTextSize', () => {
  it('writes the variables to :root and sets data-theme', () => {
    applyTheme('teal');
    const root = document.documentElement;
    expect(root.dataset.theme).toBe('teal');
    expect(root.style.getPropertyValue('--bg')).toBe('#0b1416');
    expect(root.style.getPropertyValue('--accent')).toBe('#2dd4bf');
    expect(root.style.getPropertyValue('--accent-text')).toBe(THEMES.teal.colors.accentText);
    expect(root.style.getPropertyValue('--chart2')).toBe('#f59e8b');
    expect(root.style.colorScheme).toBe('dark');
  });

  it('switching themes changes the variables each time', () => {
    for (const id of THEME_IDS) {
      applyTheme(id);
      expect(document.documentElement.style.getPropertyValue('--surface')).toBe(THEMES[id].colors.surface);
    }
  });

  it('unknown theme falls back to the default', () => {
    expect(applyTheme('hot-pink')).toBe('midnight');
    expect(document.documentElement.dataset.theme).toBe('midnight');
  });

  it('updates the theme-color meta to the theme background', () => {
    document.head.insertAdjacentHTML('beforeend', '<meta name="theme-color" content="#000000">');
    applyTheme('graphite');
    expect(document.querySelector('meta[name="theme-color"]').getAttribute('content')).toBe('#121212');
    document.querySelector('meta[name="theme-color"]').remove();
  });

  it('persists only when asked, under keel.theme / keel.textSize', () => {
    applyTheme('teal');
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    applyTheme('teal', { persist: true });
    applyTextSize('large', { persist: true });
    expect(localStorage.getItem('keel.theme')).toBe('teal');
    expect(localStorage.getItem('keel.textSize')).toBe('large');
    expect(readTheme()).toBe('teal');
    expect(readTextSize()).toBe('large');
  });

  it('text size sets the root font-size to 14 / 16 / 18 px', () => {
    expect(TEXT_SIZES).toEqual({ small: 14, normal: 16, large: 18 });
    for (const [k, px] of Object.entries(TEXT_SIZES)) {
      applyTextSize(k);
      expect(document.documentElement.style.fontSize).toBe(`${px}px`);
    }
    expect(applyTextSize('huge')).toBe(DEFAULT_TEXT_SIZE);
  });

  it('initTheme applies the saved preferences', () => {
    localStorage.setItem(TEXT_SIZE_KEY, 'small');
    localStorage.setItem(THEME_KEY, 'graphite');
    initTheme();
    expect(document.documentElement.dataset.theme).toBe('graphite');
    expect(document.documentElement.style.fontSize).toBe('14px');
  });

  it('blocked or throwing localStorage never breaks anything', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(readTheme()).toBe('midnight');
    expect(readTextSize()).toBe('normal');
    expect(() => applyTheme('teal', { persist: true })).not.toThrow();
    expect(() => applyTextSize('large', { persist: true })).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe('teal');
    expect(() => initTheme()).not.toThrow();
  });

  it('garbage in localStorage falls back to the defaults', () => {
    localStorage.setItem(THEME_KEY, '<script>');
    localStorage.setItem(TEXT_SIZE_KEY, '999');
    expect(readTheme()).toBe('midnight');
    expect(readTextSize()).toBe('normal');
  });
});

describe('useDisplayPrefs', () => {
  function Probe() {
    const { theme, setTheme, textSize, setTextSize } = useDisplayPrefs();
    return (
      <div>
        <span data-testid="state">{`${theme}/${textSize}`}</span>
        <button onClick={() => setTheme('teal')}>teal</button>
        <button onClick={() => setTextSize('large')}>large</button>
      </div>
    );
  }

  it('reads saved values, updates state, applies to :root and saves', async () => {
    const user = userEvent.setup();
    localStorage.setItem(THEME_KEY, 'graphite');
    render(
      <DisplayProvider>
        <Probe />
      </DisplayProvider>,
    );
    expect(screen.getByTestId('state')).toHaveTextContent('graphite/normal');
    await user.click(screen.getByText('teal'));
    await user.click(screen.getByText('large'));
    expect(screen.getByTestId('state')).toHaveTextContent('teal/large');
    expect(document.documentElement.dataset.theme).toBe('teal');
    expect(document.documentElement.style.fontSize).toBe('18px');
    expect(localStorage.getItem(THEME_KEY)).toBe('teal');
    expect(localStorage.getItem(TEXT_SIZE_KEY)).toBe('large');
  });

  it('works (read + set) outside a provider too', () => {
    let prefs;
    function Bare() {
      prefs = useDisplayPrefs();
      return null;
    }
    render(<Bare />);
    expect(prefs.theme).toBe('midnight');
    act(() => prefs.setTheme('teal'));
    expect(localStorage.getItem(THEME_KEY)).toBe('teal');
    expect(document.documentElement.dataset.theme).toBe('teal');
  });
});

describe('KeelLogo', () => {
  it('renders the four parts from theme variables, in every theme', () => {
    for (const id of THEME_IDS) {
      applyTheme(id);
      const { container, unmount } = render(<KeelLogo size={32} />);
      const svg = container.querySelector('svg');
      expect(svg).toHaveAttribute('viewBox', '0 0 48 48');
      expect(svg).toHaveAttribute('width', '32');
      expect(container.querySelectorAll('circle')).toHaveLength(2); // ring + dot
      expect(container.querySelector('path[d="M9 24 H39"]')).toHaveAttribute('stroke', 'var(--accent)');
      expect(container.querySelector('path[d="M18 24 L24 38 L30 24 Z"]')).toHaveAttribute('fill', 'var(--ok)');
      // Every colour is a theme variable: no hard-coded hex in the markup.
      expect(svg.outerHTML).not.toMatch(/#[0-9a-f]{3,6}/i);
      expect(document.documentElement.style.getPropertyValue('--accent')).toBe(THEMES[id].colors.accent);
      unmount();
    }
  });
  it('is decorative unless given a title', () => {
    const { container, rerender } = render(<KeelLogo />);
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    rerender(<KeelLogo title="Keel" />);
    expect(screen.getByRole('img', { name: 'Keel' })).toBeInTheDocument();
  });
});

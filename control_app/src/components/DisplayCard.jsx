import Card from './Card.jsx';
import { useDisplayPrefs } from '../context/DisplayContext.jsx';
import { TEXT_SIZE_IDS, THEMES, THEME_IDS } from '../utils/theme.js';

const SIZE_LABELS = { small: 'Small', normal: 'Normal', large: 'Large' };

/** Per-device display choices: colour theme and text size. Saved on this phone only (no 2FA, nothing sent). */
export default function DisplayCard() {
  const { theme, setTheme, textSize, setTextSize } = useDisplayPrefs();
  return (
    <Card title="Display" subtitle="Saved on this device only.">
      <div className="stack">
        <fieldset className="field">
          <legend>
            <span className="field-label">Theme</span>
          </legend>
          <div className="stack" role="group" aria-label="Theme">
            {THEME_IDS.map((id) => {
              const c = THEMES[id].colors;
              return (
                <button
                  key={id}
                  type="button"
                  className="seg-btn"
                  style={{ textAlign: 'left' }}
                  aria-pressed={theme === id}
                  onClick={() => setTheme(id)}
                >
                  <span className="swatches" aria-hidden="true">
                    {[c.bg, c.surface2, c.accent, c.ok].map((col, i) => (
                      <i key={i} style={{ background: col }} />
                    ))}
                  </span>
                  {THEMES[id].label}
                </button>
              );
            })}
          </div>
        </fieldset>
        <fieldset className="field">
          <legend>
            <span className="field-label">Text Size</span>
          </legend>
          <div className="seg" role="group" aria-label="Text size">
            {TEXT_SIZE_IDS.map((id) => (
              <button key={id} type="button" className="seg-btn" aria-pressed={textSize === id} onClick={() => setTextSize(id)}>
                {SIZE_LABELS[id] || id}
              </button>
            ))}
          </div>
        </fieldset>
      </div>
    </Card>
  );
}

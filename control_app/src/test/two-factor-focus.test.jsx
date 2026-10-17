import { describe, expect, it } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { DialogProvider, useDialogs } from '../context/DialogContext.jsx';

let api;
function Grab() {
  api = useDialogs();
  return null;
}
function setup() {
  render(
    <DialogProvider>
      <Grab />
    </DialogProvider>,
  );
}

describe('2FA keyboard handoff and scroll lock', () => {
  it('focuses the hidden anchor synchronously, then the real code input after mount', async () => {
    setup();
    const anchor = screen.getByTestId('kb-anchor');
    expect(anchor).toHaveAttribute('aria-hidden', 'true');
    expect(anchor).toHaveAttribute('inputmode', 'numeric');
    let seen;
    act(() => {
      api.twoFactor({ title: 'Start', action: async (c) => c });
      seen = document.activeElement; // same call stack as the tap handler
    });
    // focused the anchor before React re-rendered the dialog
    expect(seen === anchor || seen === screen.getByLabelText('6-digit code')).toBe(true);
    const input = screen.getByLabelText('6-digit code');
    expect(document.activeElement).toBe(input);
    expect(document.activeElement).not.toBe(anchor);
  });

  it('records anchor focus before any render happens', () => {
    setup();
    const anchor = screen.getByTestId('kb-anchor');
    let during;
    const orig = anchor.focus.bind(anchor);
    anchor.focus = (o) => {
      during = { called: true, opts: o };
      orig(o);
    };
    act(() => {
      api.twoFactor({ title: 'Go', action: async (c) => c });
    });
    expect(during).toEqual({ called: true, opts: { preventScroll: true } });
  });

  it('locks body scroll while the dialog is open and restores it on close', async () => {
    setup();
    document.body.style.overflow = 'auto';
    act(() => {
      api.twoFactor({ title: 'Go', action: async (c) => c });
    });
    expect(document.body.style.position).toBe('fixed');
    expect(document.body.style.overflow).toBe('hidden');
    await act(async () => {
      screen.getByRole('button', { name: 'Cancel' }).click();
    });
    expect(document.body.style.position).toBe('');
    expect(document.body.style.overflow).toBe('auto');
  });
});

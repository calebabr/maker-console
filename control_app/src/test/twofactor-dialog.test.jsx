import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import TwoFactorPrompt from '../components/TwoFactorPrompt.jsx';

describe('TwoFactorPrompt dialog', () => {
  it('is a centered modal dialog with a focused one-time-code input', () => {
    const done = vi.fn();
    const { container } = render(<TwoFactorPrompt title="Start" action={async () => 1} done={done} fail={vi.fn()} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(container.querySelector('.modal-backdrop')).toBeTruthy();
    const input = screen.getByLabelText('6-digit code');
    expect(input.getAttribute('inputmode')).toBe('numeric');
    expect(input.getAttribute('autocomplete')).toBe('one-time-code');
    expect(document.activeElement).toBe(input);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(done).toHaveBeenCalledWith(null);
  });

  it('traps Tab inside the dialog', () => {
    render(<TwoFactorPrompt action={async () => 1} done={vi.fn()} fail={vi.fn()} />);
    const confirm = screen.getByRole('button', { name: 'Confirm' });
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    cancel.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByLabelText('6-digit code'));
    expect(confirm.disabled).toBe(true);
  });
});

import { Component } from 'react';

// Catches a render error in page content so the rest of the shell (Header with its Stop
// button, NavBar) keeps working. Wraps <Routes> only, never the Header/Stop (S-29).
// `resetKey` (the current location) clears the error when the user navigates elsewhere.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Message only: no props/state that could carry data.
    console.error('Page render error:', error && error.message, info && info.componentStack ? '(see stack)' : '');
  }

  componentDidUpdate(prev) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page">
        <div className="notice notice-error" role="alert">
          <strong>This page could not be shown.</strong>
          <p className="hint">The Stop button at the top still works. Pick another page below, or reload the app.</p>
          <div className="btn-row">
            <a className="btn" href="/">
              Reload
            </a>
            <button type="button" className="btn" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }
}

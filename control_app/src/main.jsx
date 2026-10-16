import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import { initTheme } from './utils/theme.js';
import './index.css';

// The service worker is registered automatically by vite-plugin-pwa
// (injectRegister: 'auto', registerType: 'autoUpdate') in production builds.

initTheme();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

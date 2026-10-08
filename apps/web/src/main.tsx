import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import OfflinePOS from './OfflinePOS';
if (import.meta.env.PROD && 'serviceWorker' in navigator) void navigator.serviceWorker.register('/sw.js').catch(() => { /* preparation reports cache unavailability */ });
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>{window.location.pathname === '/offline-pos' ? <OfflinePOS /> : <App />}</StrictMode>,
);

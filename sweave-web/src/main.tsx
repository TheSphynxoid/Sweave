import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { applyFontScale, loadFontScaleId } from './lib/theme/fontScale'

// Apply the persisted font scale before the first render. The no-FOUC
// inline script in index.html already set this pre-paint; this covers
// the script-blocked edge so a reload never drops the user's scale.
// (Settings owns changes afterwards via useFontScale.)
applyFontScale(loadFontScaleId());

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
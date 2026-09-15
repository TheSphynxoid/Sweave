import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { RouteErrorBoundary } from './components/RouteErrorBoundary'
import { applyFontScale, loadFontScaleId } from './lib/theme/fontScale'
// Bundled variable fonts (Fontsource, OFL): Inter for UI, JetBrains Mono
// for code. font-display: swap + unicode-range subsets, so the system
// stacks in globals.css paint first and the webfonts enhance in place.
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'

// Apply the persisted font scale before the first render. The no-FOUC
// inline script in index.html already set this pre-paint; this covers
// the script-blocked edge so a reload never drops the user's scale.
// (Settings owns changes afterwards via useFontScale.)
applyFontScale(loadFontScaleId());

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* Last-resort guard: provider-tree throws render here instead of
        leaving a blank page with no recovery affordance. */}
    <RouteErrorBoundary label="Sweave">
      <App />
    </RouteErrorBoundary>
  </StrictMode>,
)
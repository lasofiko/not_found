import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import './index.css'
import App from './App.tsx'

// Legacy links work in both Vite and FastAPI without opening a second page.
if (['/capacity', '/capacity/', '/capacity.html'].includes(window.location.pathname)) {
  window.history.replaceState(null, '', '/#capacity')
} else if (window.location.pathname === '/dashboard' || window.location.pathname.startsWith('/dashboard/')) {
  window.history.replaceState(null, '', '/' + window.location.hash)
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

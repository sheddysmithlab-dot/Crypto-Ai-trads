import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import LoginPage from './components/LoginPage.jsx'
import { AuthProvider, useAuth } from './hooks/useAuth.jsx'

function AppGate() {
  const { status, login } = useAuth();

  if (status === 'loading') {
    return (
      <div className="min-h-screen bg-[#0b0e11] flex items-center justify-center text-gray-500 text-sm">
        Verifying session...
      </div>
    );
  }

  if (status === 'guest') {
    return <LoginPage onLogin={login} />;
  }

  return <App />;
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <AppGate />
    </AuthProvider>
  </StrictMode>,
)

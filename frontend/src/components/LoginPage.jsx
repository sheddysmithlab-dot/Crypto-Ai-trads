import { useState } from 'react';

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      await onLogin(username.trim(), password);
    } catch (err) {
      setError(err.message || 'Sign in failed. Check your credentials.');
    } finally {
      setPassword('');
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#0b0e11] flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-gray-800 bg-[#161A1E] shadow-2xl p-8">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-blue-500/10 border border-blue-500/30 mb-4">
            <i className="fas fa-shield-halved text-blue-400 text-xl" aria-hidden="true" />
          </div>
          <h1 className="text-xl font-bold text-white tracking-tight">Secure Access</h1>
          <p className="text-sm text-gray-500 mt-2">Sign in to open the trading dashboard.</p>
        </div>

        <form onSubmit={handleSubmit} autoComplete="off" noValidate>
          <div className="space-y-4">
            <div>
              <label htmlFor="login-user" className="block text-xs font-semibold text-gray-400 mb-1.5 uppercase tracking-wider">
                Username
              </label>
              <input
                id="login-user"
                name="login-user"
                type="text"
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="off"
                spellCheck={false}
                data-lpignore="true"
                data-form-type="other"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-[#0b0e11] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
                required
              />
            </div>
            <div>
              <label htmlFor="login-pass" className="block text-xs font-semibold text-gray-400 mb-1.5 uppercase tracking-wider">
                Password
              </label>
              <input
                id="login-pass"
                name="login-pass"
                type="password"
                autoComplete="new-password"
                data-lpignore="true"
                data-form-type="other"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-[#0b0e11] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
                required
              />
            </div>
          </div>

          {error ? (
            <p className="mt-4 text-sm text-red-400 bg-red-900/20 border border-red-800/40 rounded-lg px-3 py-2" role="alert">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={busy || !username.trim() || !password}
            className="mt-6 w-full py-3 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold text-sm transition"
          >
            {busy ? 'Signing in...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}

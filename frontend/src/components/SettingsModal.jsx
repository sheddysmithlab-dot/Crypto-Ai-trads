import { useEffect, useState } from 'react';
import { API_BASE } from '../config/api';

const TONE_CLASSES = {
  neutral: 'bg-emerald-900/20 border-emerald-700/40 text-emerald-300',
  success: 'bg-emerald-900/30 border-emerald-600/50 text-emerald-300',
  error: 'bg-red-900/30 border-red-600/50 text-red-300',
  info: 'bg-blue-900/30 border-blue-600/50 text-blue-300',
};

export default function SettingsModal({ open, onClose, onLiveTradingConnected }) {
  const [bybitKey, setBybitKey] = useState('');
  const [bybitSecret, setBybitSecret] = useState('');
  const [bybitEnv, setBybitEnv] = useState('mainnet');
  const [aiProvider, setAiProvider] = useState('z-ai');
  const [aiKey, setAiKey] = useState('');
  const [aiModel, setAiModel] = useState('glm-4.5-flash');
  const [aiBaseUrl, setAiBaseUrl] = useState('https://api.z.ai/api/paas/v4');
  const [banner, setBanner] = useState({ tone: 'neutral', message: 'Loading settings status...' });
  const [busy, setBusy] = useState({ save: false, testBybit: false, testAi: false, reset: false });

  async function refreshStatus() {
    try {
      const res = await fetch(`${API_BASE}/settings/status`);
      const data = await res.json();

      const bybitLabel = data.bybit_configured ? `Bybit: configured (${data.bybit_environment})` : 'Bybit: not configured';
      const aiLabel =
        data.ai_configured
          ? `AI: ${data.ai_provider} (${data.ai_model || 'glm-4.5-flash'})`
          : data.ai_provider === 'z-ai'
            ? 'AI: Z.ai GLM-4.5-Flash (add ZAI_API_KEY in backend/.env or paste key below)'
            : 'AI: not configured';

      setBanner({ tone: 'neutral', message: `${bybitLabel} | ${aiLabel}. Keys stored locally; values never shown in the form.` });

      setBybitEnv(data.bybit_environment || 'mainnet');
      setAiProvider(data.ai_provider || 'z-ai');
      setAiModel(data.ai_model || 'glm-4.5-flash');
      setAiBaseUrl(data.ai_base_url || 'https://api.z.ai/api/paas/v4');
    } catch {
      setBanner({ tone: 'error', message: 'Could not reach backend to load settings status.' });
    }
  }

  useEffect(() => {
    if (open) refreshStatus();
    else {
      setBybitKey('');
      setBybitSecret('');
      setAiKey('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  async function handleSave() {
    setBusy((b) => ({ ...b, save: true }));
    try {
      const payload = {
        bybit_api_key: bybitKey.trim(),
        bybit_api_secret: bybitSecret.trim(),
        bybit_environment: bybitEnv,
        ai_provider: aiProvider,
        ai_api_key: aiKey.trim(),
        ai_model: aiModel.trim(),
        ai_base_url: aiBaseUrl.trim(),
      };
      const res = await fetch(`${API_BASE}/settings/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (res.ok) {
        setBanner({ tone: 'success', message: data.message });
        setBybitKey('');
        setBybitSecret('');
        setAiKey('');
        await refreshStatus();
      } else {
        setBanner({ tone: 'error', message: data.message || 'Failed to save settings.' });
      }
    } catch {
      setBanner({ tone: 'error', message: 'Connection to backend failed while saving settings.' });
    } finally {
      setBusy((b) => ({ ...b, save: false }));
    }
  }

  async function handleTestBybit() {
    setBusy((b) => ({ ...b, testBybit: true }));
    try {
      const res = await fetch(`${API_BASE}/settings/test-bybit`, { method: 'POST' });
      const data = await res.json();
      setBanner({ tone: data.success ? 'success' : 'error', message: data.message });

      if (data.success) {
        const connectRes = await fetch(`${API_BASE}/connect-bybit`, { method: 'POST' });
        const connectData = await connectRes.json();
        setBanner({ tone: 'success', message: `${data.message} ${connectData.message}` });
        onLiveTradingConnected?.();
      }
    } catch {
      setBanner({ tone: 'error', message: 'Connection to backend failed while testing Bybit.' });
    } finally {
      setBusy((b) => ({ ...b, testBybit: false }));
    }
  }

  async function handleTestAi() {
    setBusy((b) => ({ ...b, testAi: true }));
    try {
      const res = await fetch(`${API_BASE}/settings/test-ai`, { method: 'POST' });
      const data = await res.json();
      setBanner({ tone: data.success ? 'success' : 'error', message: data.message });
    } catch {
      setBanner({ tone: 'error', message: 'Connection to backend failed while testing AI provider.' });
    } finally {
      setBusy((b) => ({ ...b, testAi: false }));
    }
  }

  async function handleReset() {
    if (!confirm('Reset all stored API settings? This cannot be undone.')) return;
    setBusy((b) => ({ ...b, reset: true }));
    try {
      const res = await fetch(`${API_BASE}/settings/reset`, { method: 'POST' });
      const data = await res.json();
      setBybitKey('');
      setBybitSecret('');
      setBybitEnv('mainnet');
      setAiProvider('z-ai');
      setAiKey('');
      setAiModel('glm-4.5-flash');
      setAiBaseUrl('https://api.z.ai/api/paas/v4');
      setBanner({ tone: 'info', message: data.message });
    } catch {
      setBanner({ tone: 'error', message: 'Connection to backend failed while resetting settings.' });
    } finally {
      setBusy((b) => ({ ...b, reset: false }));
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-70 z-[110] flex items-center justify-center backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#0B0E11] border border-gray-800 rounded-2xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="flex justify-between items-start px-6 pt-6">
          <div>
            <div className="text-xs font-bold text-blue-400 uppercase tracking-widest mb-1">Integration</div>
            <h2 className="text-xl font-bold text-white">Set API — Bybit & AI</h2>
          </div>
          <button
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white flex items-center justify-center"
            onClick={onClose}
          >
            <i className="fas fa-times"></i>
          </button>
        </div>

        <div className="px-6 py-5 space-y-6">
          {/* BYBIT API */}
          <div>
            <div className="text-xs font-bold text-blue-400 uppercase tracking-widest mb-3">Bybit API</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-gray-300 mb-1.5">API Key</label>
                <input
                  type="password"
                  autoComplete="off"
                  placeholder="Bybit API key"
                  value={bybitKey}
                  onChange={(e) => setBybitKey(e.target.value)}
                  className="w-full bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-300 mb-1.5">API Secret</label>
                <input
                  type="password"
                  autoComplete="off"
                  placeholder="API secret"
                  value={bybitSecret}
                  onChange={(e) => setBybitSecret(e.target.value)}
                  className="w-full bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            <div className="mt-4">
              <label className="block text-xs font-semibold text-gray-300 mb-1.5">Environment</label>
              <select
                value={bybitEnv}
                onChange={(e) => setBybitEnv(e.target.value)}
                className="w-full sm:w-1/2 bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-blue-500"
              >
                <option value="mainnet">Mainnet (real funds)</option>
                <option value="testnet">Testnet (paper funds)</option>
              </select>
            </div>
          </div>

          {/* AI API INTEGRATION */}
          <div>
            <div className="text-xs font-bold text-blue-400 uppercase tracking-widest mb-3">AI API Integration</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-gray-300 mb-1.5">AI provider</label>
                <select
                  value={aiProvider}
                  onChange={(e) => {
                    const next = e.target.value;
                    setAiProvider(next);
                    if (next === 'z-ai') {
                      if (!aiModel) setAiModel('glm-4.5-flash');
                      if (!aiBaseUrl) setAiBaseUrl('https://api.z.ai/api/paas/v4');
                    }
                  }}
                  className="w-full bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-blue-500"
                >
                  <option value="z-ai">Z.ai — GLM-4.5-Flash (default)</option>
                  <option value="none">None (disable AI)</option>
                  <option value="openai">OpenAI</option>
                  <option value="azure-openai">Azure OpenAI</option>
                  <option value="zhipu-glm">Zhipu GLM (China endpoint)</option>
                  <option value="custom">Custom / Other</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-300 mb-1.5">AI API key</label>
                <input
                  type="password"
                  autoComplete="off"
                  placeholder="Paste Z.ai API key (or set ZAI_API_KEY in backend/.env)"
                  value={aiKey}
                  onChange={(e) => setAiKey(e.target.value)}
                  className="w-full bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-4">
              <div>
                <label className="block text-xs font-semibold text-gray-300 mb-1.5">Model</label>
                <input
                  type="text"
                  placeholder="glm-4.5-flash"
                  value={aiModel}
                  onChange={(e) => setAiModel(e.target.value)}
                  className="w-full bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-300 mb-1.5">Base URL (optional)</label>
                <input
                  type="text"
                  placeholder="https://api.z.ai/api/paas/v4"
                  value={aiBaseUrl}
                  onChange={(e) => setAiBaseUrl(e.target.value)}
                  className="w-full bg-[#161A1E] border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          </div>

          {/* Status Banner */}
          <div className={`text-xs rounded-lg px-4 py-3 border ${TONE_CLASSES[banner.tone]}`}>{banner.message}</div>

          {/* Action Buttons */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <button
              className="bg-emerald-500 hover:bg-emerald-600 text-black font-bold py-2.5 rounded-lg text-sm transition-colors col-span-2 sm:col-span-1 disabled:opacity-60"
              onClick={handleSave}
              disabled={busy.save}
            >
              {busy.save ? 'Saving...' : 'Save'}
            </button>
            <button
              className="bg-[#161A1E] hover:bg-gray-800 border border-gray-700 text-white font-bold py-2.5 rounded-lg text-sm transition-colors disabled:opacity-60"
              onClick={handleTestBybit}
              disabled={busy.testBybit}
            >
              {busy.testBybit ? 'Testing...' : 'Test Bybit'}
            </button>
            <button
              className="bg-[#161A1E] hover:bg-gray-800 border border-gray-700 text-white font-bold py-2.5 rounded-lg text-sm transition-colors disabled:opacity-60"
              onClick={handleTestAi}
              disabled={busy.testAi}
            >
              {busy.testAi ? 'Testing...' : 'Test AI'}
            </button>
            <button
              className="bg-[#161A1E] hover:bg-gray-800 border border-gray-700 text-white font-bold py-2.5 rounded-lg text-sm transition-colors disabled:opacity-60"
              onClick={handleReset}
              disabled={busy.reset}
            >
              Reset
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

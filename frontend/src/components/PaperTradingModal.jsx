import { useEffect, useState } from 'react';
import { authFetch } from '../config/api';

const PRESETS = [
  { amount: 1000, label: '$1K' },
  { amount: 10000, label: '$10K' },
  { amount: 50000, label: '$50K' },
  { amount: 142560.88, label: '$142.5K' },
];

function fmtCurrency(num) {
  return `$${Number(num).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function PaperTradingModal({ open, onClose, currentCapital, onCapitalSet, isLive }) {
  const [amount, setAmount] = useState('');
  const [status, setStatus] = useState({ tone: 'yellow', message: `Currently simulating with ${fmtCurrency(currentCapital)}.` });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setAmount('');
      setStatus({ tone: 'yellow', message: `Currently simulating with ${fmtCurrency(currentCapital)}.` });
    }
  }, [open, currentCapital]);

  if (!open) return null;

  if (isLive) {
    return (
      <div className="fixed inset-0 bg-black bg-opacity-70 z-[110] flex items-center justify-center backdrop-blur-sm p-4">
        <div className="bg-[#0B0E11] border border-gray-800 rounded-2xl shadow-2xl max-w-md w-full p-6 text-center">
          <p className="text-emerald-400 font-semibold mb-2">Live Trading Active</p>
          <p className="text-sm text-gray-400 mb-4">Paper trading is paused while Bybit is connected. Capital and P&amp;L come from your live account.</p>
          <button className="px-4 py-2 rounded-lg bg-gray-800 text-white text-sm" onClick={onClose}>Close</button>
        </div>
      </div>
    );
  }

  const toneClasses = {
    yellow: 'bg-yellow-900/20 border-yellow-700/40 text-yellow-300',
    red: 'bg-red-900/30 border-red-600/50 text-red-300',
    emerald: 'bg-emerald-900/30 border-emerald-600/50 text-emerald-300',
  };

  async function handleSave() {
    const value = parseFloat(amount);
    if (!value || value < 100) {
      setStatus({ tone: 'red', message: 'Please enter a valid amount of at least $100.' });
      return;
    }

    setSaving(true);
    try {
      const res = await authFetch('/paper-trading/set-capital', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: value }),
      });
      const data = await res.json();
      const isSuccess = res.ok && data.status === 'success';
      setStatus({ tone: isSuccess ? 'emerald' : 'red', message: data.message });

      if (isSuccess) {
        onCapitalSet(data.capital);
        setTimeout(onClose, 1200);
      }
    } catch (err) {
      console.error('Failed to set paper trading capital:', err);
      setStatus({ tone: 'red', message: 'Connection to backend failed. Please try again.' });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-70 z-[110] flex items-center justify-center backdrop-blur-sm p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-[#0B0E11] border border-gray-800 rounded-2xl shadow-2xl max-w-md w-full">
        <div className="flex justify-between items-start px-6 pt-6">
          <div>
            <div className="text-xs font-bold text-yellow-400 uppercase tracking-widest mb-1">Simulation</div>
            <h2 className="text-xl font-bold text-white">Paper Trading Setup</h2>
          </div>
          <button
            className="w-8 h-8 rounded-lg bg-[#161A1E] border border-gray-700 text-gray-400 hover:text-white flex items-center justify-center"
            onClick={onClose}
          >
            <i className="fas fa-times"></i>
          </button>
        </div>

        <div className="px-6 py-5 space-y-4">
          <p className="text-sm text-gray-400">
            Kitni virtual (paper) money se bot ko trade karna chahiye? Yeh amount real funds ko touch nahi karega -
            sirf simulation ke liye hai.
          </p>

          <div>
            <label className="block text-xs font-semibold text-gray-300 mb-1.5">Starting Paper Capital (USD)</label>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 text-sm">$</span>
              <input
                type="number"
                min="100"
                step="100"
                placeholder="e.g. 10000"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="w-full bg-[#161A1E] border border-gray-700 rounded-lg pl-7 pr-3 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-yellow-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-4 gap-2">
            {PRESETS.map((preset) => (
              <button
                key={preset.amount}
                className="bg-[#161A1E] hover:bg-gray-800 border border-gray-700 text-gray-300 text-xs font-bold py-2 rounded-lg"
                onClick={() => setAmount(String(preset.amount))}
              >
                {preset.label}
              </button>
            ))}
          </div>

          <div className={`text-xs rounded-lg px-4 py-3 border ${toneClasses[status.tone]}`}>{status.message}</div>

          <button
            className="w-full bg-yellow-500 hover:bg-yellow-400 text-black font-bold py-2.5 rounded-lg text-sm transition-colors disabled:opacity-60"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? 'Starting...' : 'Start Paper Trading With This Amount'}
          </button>
        </div>
      </div>
    </div>
  );
}

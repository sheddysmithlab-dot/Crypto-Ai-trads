import { useEffect, useState } from 'react';

const TERMS_PARAGRAPHS = [
  'This AI trading bot is an automated assistance tool only. It does not provide financial, investment, legal, or tax advice, and it does not guarantee any profit, return, or successful trading outcome.',
  'Cryptocurrency and leveraged trading involve a high risk of loss, including the possible loss of all capital. Markets move quickly; prices can gap; and past performance does not predict future results. All profit and loss depend entirely on market conditions and your own decisions.',
  'We make no warranty that the software, signals, patterns, or automation will be accurate, uninterrupted, error-free, or suitable for any purpose. Bugs, connectivity issues, exchange outages, slippage, fees, and liquidations may occur without notice.',
  'Bybit (or any connected exchange) fees, funding, and execution rules are outside our control. You are solely responsible for understanding exchange terms, leverage, and account risk limits.',
  'This bot does not guarantee compliance with any law, regulation, or “legal activity” standard in your jurisdiction. You must ensure that using automated trading tools is lawful where you live and trade. You remain fully responsible for your account, funds, taxes, and regulatory obligations.',
  'To the maximum extent permitted by law, the operators, developers, and affiliates of this service are not liable for any direct, indirect, incidental, or consequential losses arising from use of this bot — including trading losses, missed profits, data loss, or exchange actions.',
  'By checking the box and continuing, you confirm that you are of legal age, that you understand these risks, that you accept full responsibility for all trades placed while the engine is running, and that you will not hold us responsible for any outcome.',
];

/**
 * Final gate before AI Engine START: user must tick agree, then Continue.
 * Shown after Final Safety Check; Cancel aborts start.
 */
export default function TermsConditionsModal({ open, onContinue, onCancel }) {
  const [agreed, setAgreed] = useState(false);

  useEffect(() => {
    if (open) setAgreed(false);
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/80 z-[114] flex items-center justify-center backdrop-blur-sm p-4">
      <div className="modal-enter bg-[#0B0E11] p-6 sm:p-8 rounded-2xl shadow-2xl max-w-lg w-full border border-amber-500/50 text-left max-h-[90vh] flex flex-col">
        <div className="text-center mb-4 shrink-0">
          <i className="fas fa-file-contract text-4xl text-amber-400 mb-3"></i>
          <h2 className="text-xl font-black text-white uppercase tracking-wide">
            Terms &amp; Conditions
          </h2>
          <p className="text-xs text-gray-500 mt-1">
            Please read carefully before starting the AI Engine.
          </p>
        </div>

        <div className="overflow-y-auto flex-1 min-h-0 rounded-lg border border-gray-700 bg-[#161A1E] px-4 py-3 space-y-3 mb-4">
          {TERMS_PARAGRAPHS.map((text, i) => (
            <p key={i} className="text-xs sm:text-sm text-gray-300 leading-relaxed">
              {text}
            </p>
          ))}
        </div>

        <label className="flex items-start gap-3 cursor-pointer mb-5 shrink-0 select-none">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-1 h-4 w-4 rounded border-gray-600 bg-[#161A1E] text-emerald-500 focus:ring-emerald-500 focus:ring-offset-0"
          />
          <span className="text-sm text-gray-200">
            I have read and agree to these Terms &amp; Conditions. I understand trading involves
            risk of loss and that no profit or outcome is guaranteed.
          </span>
        </label>

        <div className="grid grid-cols-2 gap-3 shrink-0">
          <button
            type="button"
            className="bg-gray-700 hover:bg-gray-600 text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs sm:text-sm"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!agreed}
            className={`font-bold py-3 rounded-lg uppercase tracking-wide text-xs sm:text-sm ${
              agreed
                ? 'bg-emerald-600 hover:bg-emerald-500 text-white'
                : 'bg-gray-800 text-gray-500 cursor-not-allowed'
            }`}
            onClick={() => agreed && onContinue?.()}
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}

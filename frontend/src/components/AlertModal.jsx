export default function AlertModal({ open, onClose }) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-70 z-[100] flex items-center justify-center backdrop-blur-sm">
      <div className="modal-enter bg-lightCard dark:bg-darkCard p-8 rounded-2xl shadow-2xl max-w-md w-full border border-red-500 text-center transform scale-95 transition-transform duration-300">
        <i className="fas fa-exclamation-triangle text-6xl text-red-500 mb-4 animate-bounce"></i>
        <h2 className="text-2xl font-black text-gray-900 dark:text-white mb-2">EMERGENCY EXIT TRIGGERED</h2>
        <p className="text-gray-600 dark:text-gray-300 mb-6">
          2.5% Loss limit reached or Manual Kill Switch activated. All active trades have been successfully closed at
          market price.
        </p>
        <button
          className="bg-gray-800 text-white dark:bg-white dark:text-black px-6 py-3 rounded-lg font-bold w-full uppercase tracking-wider hover:opacity-90"
          onClick={onClose}
        >
          Acknowledge
        </button>
      </div>
    </div>
  );
}

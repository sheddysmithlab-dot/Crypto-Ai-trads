// High-security confirmation before any voluntary trade exit action.
export default function TradeExitConfirmModal({
  open,
  title,
  message,
  detail,
  confirmLabel = 'Confirm Exit',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/80 z-[114] flex items-center justify-center backdrop-blur-sm p-4">
      <div className="modal-enter bg-[#0B0E11] p-6 sm:p-8 rounded-2xl shadow-2xl max-w-md w-full border-2 border-amber-500 text-center">
        <i className="fas fa-shield-alt text-5xl text-amber-400 mb-4"></i>
        <h2 className="text-lg sm:text-xl font-black text-white mb-2 uppercase tracking-wide">{title}</h2>
        <p className="text-sm text-gray-300 mb-3">{message}</p>
        {detail ? <p className="text-xs text-gray-500 mb-5">{detail}</p> : <div className="mb-5" />}

        <div className="grid grid-cols-2 gap-3">
          <button
            type="button"
            className="bg-gray-700 hover:bg-gray-600 text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs sm:text-sm"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className="bg-red-600 hover:bg-red-500 text-white font-bold py-3 rounded-lg uppercase tracking-wide text-xs sm:text-sm"
            onClick={onConfirm}
          >
            <i className="fas fa-exclamation-triangle mr-1.5"></i>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

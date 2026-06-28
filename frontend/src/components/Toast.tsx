import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { AlertCircle, CheckCircle, AlertTriangle, Info, X } from 'lucide-react';

export interface Toast {
  id: string;
  message: string;
  type: 'success' | 'error' | 'warning' | 'info';
  duration?: number;
}

interface ToastContextType {
  toast: (message: string, type: Toast['type'], duration?: number) => void;
  success: (message: string, duration?: number) => void;
  error: (message: string, duration?: number) => void;
  warning: (message: string, duration?: number) => void;
  info: (message: string, duration?: number) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}

interface ToastProviderProps {
  children: React.ReactNode;
}

export function ToastProvider({ children }: ToastProviderProps) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback((message: string, type: Toast['type'], duration = 5000) => {
    const id = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { id, message, type, duration }]);
  }, []);

  const success = useCallback((message: string, duration?: number) => toast(message, 'success', duration), [toast]);
  const error = useCallback((message: string, duration?: number) => toast(message, 'error', duration), [toast]);
  const warning = useCallback((message: string, duration?: number) => toast(message, 'warning', duration), [toast]);
  const info = useCallback((message: string, duration?: number) => toast(message, 'info', duration), [toast]);

  return (
    <ToastContext.Provider value={{ toast, success, error, warning, info }}>
      {children}
      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </ToastContext.Provider>
  );
}

interface ToastContainerProps {
  toasts: Toast[];
  onDismiss: (id: string) => void;
}

function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2.5 pointer-events-none max-w-sm w-full px-4 sm:px-0">
      {toasts.map((t) => (
        <ToastCard key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

interface ToastCardProps {
  toast: Toast;
  onDismiss: (id: string) => void;
}

function ToastCard({ toast, onDismiss }: ToastCardProps) {
  const { id, message, type, duration = 5000 } = toast;

  useEffect(() => {
    const timer = setTimeout(() => {
      onDismiss(id);
    }, duration);

    return () => clearTimeout(timer);
  }, [id, duration, onDismiss]);

  const getStyles = () => {
    switch (type) {
      case 'success':
        return {
          borderClass: 'border-l-4 border-emerald-500',
          bgClass: 'bg-emerald-50/95',
          icon: <CheckCircle className="w-4 h-4 text-emerald-600 shrink-0" />,
          progressClass: 'bg-emerald-500/30',
        };
      case 'error':
        return {
          borderClass: 'border-l-4 border-rose-500',
          bgClass: 'bg-rose-50/95',
          icon: <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />,
          progressClass: 'bg-rose-500/30',
        };
      case 'warning':
        return {
          borderClass: 'border-l-4 border-amber-500',
          bgClass: 'bg-amber-50/95',
          icon: <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />,
          progressClass: 'bg-amber-500/30',
        };
      case 'info':
      default:
        return {
          borderClass: 'border-l-4 border-blue-500',
          bgClass: 'bg-blue-50/95',
          icon: <Info className="w-4 h-4 text-blue-600 shrink-0" />,
          progressClass: 'bg-blue-500/30',
        };
    }
  };

  const styles = getStyles();

  return (
    <div
      className={`pointer-events-auto relative flex items-start gap-3 p-4 shadow-md rounded-none border border-slate-200/50 backdrop-blur-md overflow-hidden transition-all duration-300 animate-slide-in ${styles.borderClass} ${styles.bgClass}`}
      role="alert"
    >
      <div className="mt-0.5">{styles.icon}</div>
      <div className="flex-1 pr-4">
        <p className="text-xs font-semibold text-slate-800 leading-normal select-text">
          {message}
        </p>
      </div>
      <button
        onClick={() => onDismiss(id)}
        className="text-slate-400 hover:text-slate-600 transition-colors p-0.5 cursor-pointer focus:outline-none shrink-0"
        aria-label="Close notification"
      >
        <X className="w-3.5 h-3.5" />
      </button>

      {/* Countdown progress bar */}
      <div className="absolute bottom-0 left-0 w-full h-[3px] bg-slate-100/50">
        <div
          className={`h-full animate-shrink-width ${styles.progressClass}`}
          style={{
            animationDuration: `${duration}ms`,
            animationTimingFunction: 'linear',
            animationFillMode: 'forwards',
          }}
        />
      </div>
    </div>
  );
}

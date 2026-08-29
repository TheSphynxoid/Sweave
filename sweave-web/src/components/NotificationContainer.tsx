import { useApp } from '../context/AppProvider';
import { X, CheckCircle, AlertCircle, AlertTriangle, Info } from 'lucide-react';
import { cn } from '../utils/cn';

const getIcon = (type: string) => {
  switch (type) {
    case 'success': return <CheckCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />;
    case 'error': return <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />;
    case 'warning': return <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" />;
    case 'info': return <Info className="w-5 h-5 flex-shrink-0 mt-0.5" />;
    default: return <Info className="w-5 h-5 flex-shrink-0 mt-0.5" />;
  }
};

const colors = {
  success: 'bg-green-500/10 text-green-500 border-green-500/20',
  error: 'bg-red-500/10 text-red-500 border-red-500/20',
  warning: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  info: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
};

export function NotificationContainer() {
  const { notifications, removeNotification } = useApp();

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80 max-w-full">
      {notifications.map((notification) => (
        <div
          key={notification.id}
          className={cn(
            'flex items-start gap-3 p-4 rounded-lg border shadow-lg animate-slide-in',
            colors[notification.type]
          )}
        >
          {getIcon(notification.type)}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium">{notification.message}</p>
          </div>
          <button
            onClick={() => removeNotification(notification.id)}
            className="p-1 hover:bg-black/10 rounded transition-colors flex-shrink-0"
            aria-label="Dismiss"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      ))}
    </div>
  );
}
// Test if JS modules load correctly
import { API } from './sweave/web/static/js/api.js';
import { ThemeManager } from './sweave/web/static/js/theme.js';
import { WebSocketManager } from './sweave/web/static/js/websocket.js';
import { ModalManager } from './sweave/web/static/js/modal.js';
import { NotificationManager } from './sweave/web/static/js/notification.js';

console.log('All modules loaded successfully');
console.log('API:', typeof API);
console.log('ThemeManager:', typeof ThemeManager);
console.log('WebSocketManager:', typeof WebSocketManager);
console.log('ModalManager:', typeof ModalManager);
console.log('NotificationManager:', typeof NotificationManager);
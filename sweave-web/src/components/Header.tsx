import { useApp } from '../context/AppProvider';
import { 
  Menu, Sun, Moon, Bell, User, LogOut, ChevronDown,
  LayoutDashboard, FolderGit2, MessageSquare, Bot, Settings
} from 'lucide-react';
import { useState } from 'react';
import { cn } from '../utils/cn';

export function Header() {
  const { sidebarOpen, setSidebarOpen, activeView, setActiveView, addNotification } = useApp();
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);

  const toggleTheme = () => {
    document.documentElement.classList.toggle('dark');
    localStorage.setItem('theme', document.documentElement.classList.contains('dark') ? 'dark' : 'light');
  };

  return (
    <header className="sticky top-0 z-30 h-16 bg-background/80 backdrop-blur-sm border-b border-border flex items-center justify-between px-6">
      <div className="flex items-center gap-4">
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="p-2 rounded-lg hover:bg-muted transition-colors lg:hidden"
          aria-label="Toggle sidebar"
        >
          <Menu className="w-5 h-5" />
        </button>
        
        <nav className="hidden md:flex items-center gap-1" aria-label="Main navigation">
          {[
            { id: 'dashboard', label: 'Dashboard', icon: () => <LayoutDashboard className="w-4 h-4" /> },
            { id: 'projects', label: 'Projects', icon: () => <FolderGit2 className="w-4 h-4" /> },
            { id: 'sessions', label: 'Sessions', icon: () => <MessageSquare className="w-4 h-4" /> },
            { id: 'agents', label: 'Agents', icon: () => <Bot className="w-4 h-4" /> },
            { id: 'settings', label: 'Settings', icon: () => <Settings className="w-4 h-4" /> },
          ].map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveView(item.id)}
              className={cn(
                'flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                activeView === item.id
                  ? 'bg-primary/10 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              )}
            >
              {item.icon()}
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
      </div>

      <div className="flex items-center gap-4">
        {/* Notifications */}
        <div className="relative">
          <button
            onClick={() => setNotificationsOpen(!notificationsOpen)}
            className="p-2 rounded-lg hover:bg-muted transition-colors relative"
            aria-label="Notifications"
          >
            <Bell className="w-5 h-5" />
            <span className="absolute -top-1 -right-1 w-4 h-4 bg-destructive text-destructive-foreground text-xs rounded-full flex items-center justify-center">
              3
            </span>
          </button>
          
          {notificationsOpen && (
            <div className="absolute right-0 top-full mt-2 w-80 bg-card border border-border rounded-lg shadow-lg p-4 z-50">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold">Notifications</h3>
                <button onClick={() => setNotificationsOpen(false)} className="p-1 hover:bg-muted rounded">
                  <ChevronDown className="w-4 h-4" />
                </button>
              </div>
              <div className="space-y-3 max-h-60 overflow-y-auto">
                <div className="p-3 bg-muted rounded-lg">
                  <p className="text-sm font-medium">Task completed</p>
                  <p className="text-xs text-muted-foreground">Backend agent finished API implementation</p>
                  <p className="text-xs text-muted-foreground mt-1">2 minutes ago</p>
                </div>
                <div className="p-3 bg-muted rounded-lg">
                  <p className="text-sm font-medium">New session created</p>
                  <p className="text-xs text-muted-foreground">Session "feature-auth" started</p>
                  <p className="text-xs text-muted-foreground mt-1">10 minutes ago</p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Theme Toggle */}
        <button
          onClick={toggleTheme}
          className="p-2 rounded-lg hover:bg-muted transition-colors"
          aria-label="Toggle theme"
        >
          <Sun className="w-5 h-5 dark:hidden" />
          <Moon className="w-5 h-5 hidden dark:block" />
        </button>

        {/* User Menu */}
        <div className="relative">
          <button
            onClick={() => setUserMenuOpen(!userMenuOpen)}
            className="flex items-center gap-2 p-2 rounded-lg hover:bg-muted transition-colors"
          >
            <div className="w-8 h-8 bg-primary rounded-full flex items-center justify-center">
              <span className="text-primary-foreground text-sm font-medium">U</span>
            </div>
            <span className="hidden md:block text-sm font-medium">User</span>
            <ChevronDown className="w-4 h-4" />
          </button>

          {userMenuOpen && (
            <div className="absolute right-0 top-full mt-2 w-48 bg-card border border-border rounded-lg shadow-lg py-1 z-50">
              <div className="px-3 py-2 border-b border-border">
                <p className="text-sm font-medium">User</p>
                <p className="text-xs text-muted-foreground">user@example.com</p>
              </div>
              <button className="w-full px-3 py-2 text-left hover:bg-muted transition-colors flex items-center gap-2">
                <User className="w-4 h-4" />
                Profile
              </button>
              <button className="w-full px-3 py-2 text-left hover:bg-muted transition-colors flex items-center gap-2">
                <Settings className="w-4 h-4" />
                Settings
              </button>
              <hr className="my-1 border-border" />
              <button className="w-full px-3 py-2 text-left hover:bg-muted transition-colors flex items-center gap-2 text-destructive">
                <LogOut className="w-4 h-4" />
                Logout
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
import { useApp } from '../context/AppProvider';
import { Sidebar } from './Sidebar';
import { Header } from './Header';
import { NotificationContainer } from './NotificationContainer';
import { Outlet } from 'react-router-dom';

export function Layout() {
  const { sidebarOpen, activeView } = useApp();

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar />
      <div className={`flex-1 flex flex-col transition-all duration-200 ${sidebarOpen ? 'ml-64' : 'ml-20'}`}>
        <Header />
        <main className="flex-1 p-6 overflow-auto">
          <Outlet />
        </main>
      </div>
      <NotificationContainer />
    </div>
  );
}
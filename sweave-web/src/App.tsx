import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryProvider } from './context/QueryProvider';
import { AppProvider } from './context/AppProvider';
import { Layout } from './components/Layout';
import { Dashboard } from './pages/Dashboard';
import { Projects } from './pages/Projects';
import { Sessions } from './pages/Sessions';
import { Tasks } from './pages/Tasks';
import { Agents } from './pages/Agents';
import { Memory } from './pages/Memory';
import { Settings } from './pages/Settings';
import './styles/globals.css';

function App() {
  return (
    <QueryProvider>
      <AppProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="projects" element={<Projects />} />
              <Route path="sessions" element={<Sessions />} />
              <Route path="tasks" element={<Tasks />} />
              <Route path="agents" element={<Agents />} />
              <Route path="memory" element={<Memory />} />
              <Route path="settings" element={<Settings />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AppProvider>
    </QueryProvider>
  );
}

export default App;
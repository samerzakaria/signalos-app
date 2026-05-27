
import './styles.css';

import { Titlebar } from './components/Titlebar';
import { Onboarding } from './components/Onboarding';
import { Sidebar } from './components/Sidebar';
import { Toolbar } from './components/Toolbar';

import { DashboardView } from './components/views/DashboardView';
import { BuildView } from './components/views/BuildView';
import { TerminalView } from './components/views/TerminalView';
import { PreviewView } from './components/views/PreviewView';
import { VaultView } from './components/views/VaultView';
import { SettingsView } from './components/views/SettingsView';
import { HistoryView } from './components/views/HistoryView';
import { BrainView } from './components/views/BrainView';
import { HelpView } from './components/views/HelpView';
import { DeliverView } from './components/views/DeliverView';

import { AddSecretModal } from './components/AddSecretModal';
import { NewProjectModal } from './components/NewProjectModal';
import { OverrideModal } from './components/OverrideModal';
import { ExitModal } from './components/ExitModal';
import { stageClass } from './components/viewShell';

export function App() {
  return (
    <div className="window">
      <Titlebar />
      <div className="window-body">
        <Onboarding />
        <div id="app" className={stageClass('app')}>
          <Sidebar />
          <section className="main">
            <Toolbar />
            <div className="views">
              <DashboardView />
              <BuildView />
              <TerminalView />
              <PreviewView />
              <VaultView />
              <SettingsView />
              <HistoryView />
              <BrainView />
              <HelpView />
              <DeliverView />
            </div>
          </section>
        </div>
      </div>
      <AddSecretModal />
      <NewProjectModal />
      <OverrideModal />
      <ExitModal />
    </div>
  );
}

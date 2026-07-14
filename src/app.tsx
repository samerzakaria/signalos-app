
import './styles.css';

import { Titlebar } from './components/Titlebar';
import { Onboarding } from './components/Onboarding';
import { Sidebar } from './components/Sidebar';
import { Toolbar } from './components/Toolbar';

import { DashboardView } from './components/views/DashboardView';
import { BuildView } from './components/views/BuildView';
import { PreviewView } from './components/views/PreviewView';
import { VaultView } from './components/views/VaultView';
import { WarRoomView } from './components/views/WarRoomView';
import { SettingsView } from './components/views/SettingsView';
import { HistoryView } from './components/views/HistoryView';
import { BrainView } from './components/views/BrainView';
import { HelpView } from './components/views/HelpView';

import { AddSecretModal } from './components/AddSecretModal';
import { NewProjectModal } from './components/NewProjectModal';
import { OverrideModal } from './components/OverrideModal';
import { ExitModal } from './components/ExitModal';
import { stageClass } from './components/viewShell';
import { mobileNavOpen } from './state';

export function App() {
  return (
    <div className="window">
      <Titlebar />
      <div className="window-body">
        <Onboarding />
        <div id="app" className={stageClass('app')}>
          <Sidebar />
          {mobileNavOpen.value ? <div className="mobile-nav-backdrop" onClick={() => { mobileNavOpen.value = false; }}></div> : null}
          <section className="main">
            <Toolbar />
            <div className="views">
              <DashboardView />
              <BuildView />
              <PreviewView />
              <VaultView />
              <WarRoomView />
              <SettingsView />
              <HistoryView />
              <BrainView />
              <HelpView />
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

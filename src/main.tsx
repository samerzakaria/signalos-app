import { render } from 'preact';
import { App } from './app';
import './assets/icons/tabler-icons.min.css';
import './js/app-v2.js';
import './services/providerModels';
import './services/terminal';
import './services/chat';
import './services/workspace';
import './services/approvePlan';
import './services/orchestratorEvents';
import './services/fileTree';
import './services/preview';
import './services/protocolContext';

render(<App />, document.getElementById('root') as HTMLElement);

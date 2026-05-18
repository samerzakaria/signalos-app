import { render } from 'preact';
import { App } from './app';
import './js/app-v2.js';
import './services/providerModels';

render(<App />, document.getElementById('app') as HTMLElement);

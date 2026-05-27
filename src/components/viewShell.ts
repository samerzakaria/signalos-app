import { appVisible, onboardingVisible, sbTab, tab } from '../state';

export const stageClass = (stage: 'app' | 'onboarding') => {
  const active = stage === 'app' ? appVisible.value : onboardingVisible.value;
  return active ? 'stage active' : 'stage';
};

export const viewClass = (id: string) => {
  return tab.value === id ? 'view active' : 'view';
};

export const topTabClass = (id: string) => {
  return tab.value === id ? 'seg-i active' : 'seg-i';
};

export const sidebarTabClass = (id: string) => {
  return sbTab.value === id ? 'sb-tab active' : 'sb-tab';
};

export const sidebarPanelClass = (id: string) => {
  return sbTab.value === id ? 'sb-panel active' : 'sb-panel';
};

export const sidebarNavClass = (id: string) => {
  return tab.value === id ? 'nav active' : 'nav';
};

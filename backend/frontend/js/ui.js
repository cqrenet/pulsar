// Theme, collapsible panels, and small display helpers.
(window.PulsarParts ??= {}).ui = () => ({
  activeTab: 'events',
  showThemeMenu: false,
  theme: 'auto',
  panelState: { sourceHealth: true, alerts: true, rules: true, filters: true, events: true },
  appVersion: '',
  repoUrl: 'https://github.com/cqrenet/pulsar',
  docsUrl: 'https://github.com/cqrenet/pulsar/blob/main/README.md',

  initTheme() {
    let saved = null;
    try { saved = localStorage.getItem('pulsar_theme'); } catch {}
    this.theme = ['light', 'dark', 'auto'].includes(saved) ? saved : 'auto';
    this.applyTheme();
    // Follow OS changes in auto mode
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: light)').addEventListener?.('change', () => {
        if (this.theme === 'auto') this.applyTheme();
      });
    }
  },

  setTheme(mode) {
    this.theme = mode;
    try { localStorage.setItem('pulsar_theme', mode); } catch {}
    this.applyTheme();
    this.showThemeMenu = false;
  },

  applyTheme() {
    document.documentElement.setAttribute('data-theme', this.theme);
  },

  loadPanelState() {
    try {
      const parsed = JSON.parse(localStorage.getItem('pulsar_panels') || '{}');
      Object.keys(parsed).forEach((k) => { if (this.panelState[k] !== undefined) this.panelState[k] = parsed[k]; });
    } catch {}
  },

  togglePanel(key) {
    this.panelState[key] = !this.panelState[key];
    try { localStorage.setItem('pulsar_panels', JSON.stringify(this.panelState)); } catch {}
  },

  openTab(tab) {
    this.activeTab = tab;
    if (tab === 'alerts') return this.loadAlerts();
    if (tab === 'rules') return this.loadRules();
  },

  relativeTime(ts) {
    if (!ts) return '—';
    const mins = Math.floor((Date.now() - new Date(ts).getTime()) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d ago`;
    return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  },

  formatFullTime(ts) {
    if (!ts) return '—';
    return new Date(ts).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
  },

  formatEventTime(ts) {
    if (!ts) return '—';
    return new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  },

  sourceTitle(src) {
    const when = src.last_fetch_time ? ' · ' + new Date(src.last_fetch_time).toLocaleString() : '';
    return `${src.source}: ${src.status}${when}`;
  },

  isSuccess(result) {
    return ['success', 'succeeded', 'ok', 'passed', 'true', '1'].includes((result || '').toLowerCase());
  },

  isFailure(result) {
    return ['failure', 'failed', 'error', 'fail', 'false', '0'].includes((result || '').toLowerCase());
  },

  serviceColor(service) {
    const map = {
      Directory: '#58a6ff', UserManagement: '#58a6ff', GroupManagement: '#58a6ff',
      RoleManagement: '#db6d28', Policy: '#e09b53', Device: '#e09b53',
      Intune: '#e09b53', DeviceManagement: '#e09b53',
      Exchange: '#a371f7', ApplicationManagement: '#a371f7',
      SharePoint: '#3fb950',
      Teams: '#1f6feb', MicrosoftTeams: '#1f6feb',
    };
    return map[service] || '#656d76';
  },
});

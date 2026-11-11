// PULSAR UI. The component is assembled from the parts in /js (api, auth, ui, events, alerts, timeline);
// each part adds its own state and methods to the one Alpine component the templates bind to.
function pulsarApp() {
  const parts = Object.values(window.PulsarParts).map((make) => make());
  return Object.assign({}, ...parts, {
    async initApp() {
      this.initTheme();
      await this.loadVersion();
      await this.initAuth();
      this.loadSavedFilters();
      this.loadPanelState();
      if (!this.authEnabled() || this.accessToken) {
        await this.loadFilterOptions();
        await this.loadSavedSearches();
        await this.loadSourceHealth();
        await this.loadAlertSummary();
        await this.loadAlerts();
        await this.loadRules();
        await this.loadEvents();
      }
    },
  });
}

// The CSP build only accepts components registered by name (x-data="pulsarApp").
document.addEventListener('alpine:init', () => Alpine.data('pulsarApp', pulsarApp));

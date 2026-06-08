function pulsarApp() {
  return {
    events: [],
    sourceHealth: [],
    statusText: '',
    countText: '',
    cursorStack: [],
    nextCursor: null,
    currentCursor: null,
    modalOpen: false,
    modalBody: '',
    modalEventId: '',

    // UI state
    activeTab: 'events',
    expandedEventId: null,
    showAdvancedFilters: false,
    showServiceDropdown: false,
    showExportMenu: false,

    // Timeline / correlation pivot view
    timelineMode: false,
    timelineType: '', // 'actor' | 'correlation'
    timelineTitle: '',
    timelineEvents: [],
    timelineLoading: false,
    timelineNextCursor: null,
    timelineExpandedId: null,

    authBtnText: 'Login',
    authConfig: null,
    msalInstance: null,
    account: null,
    accessToken: null,
    authScopes: [],
    filters: {
      actor: '', selectedServices: [], search: '', operation: '', result: '', start: '', end: '', limit: 50, includeTags: '', excludeTags: '',
    },
    panelState: { sourceHealth: true, alerts: true, rules: true, filters: true, events: true },
    options: { actors: [], services: [], operations: [], results: [] },
    savedSearches: [],
    appVersion: '',
    repoUrl: 'https://github.com/cqrenet/pulsar',
    docsUrl: 'https://github.com/cqrenet/pulsar/blob/main/README.md',
    alertSummary: { total_open: 0, high: 0, medium: 0, low: 0 },
    alerts: [],
    alertsTotal: 0,
    alertsPage: 1,
    alertsFilter: { status: 'open', severity: '' },
    rules: [],
    ruleModalOpen: false,
    ruleEditId: null,
    ruleEdit: { name: '', enabled: true, severity: 'medium', message: '', conditions: [] },

    // Theme
    theme: 'auto',
    showThemeMenu: false,


    async initApp() {
      this.initTheme();
      await this.loadVersion();
      await this.initAuth();
      this.loadSavedFilters();
      this.loadPanelState();
      if (!this.authConfig?.auth_enabled || this.accessToken) {
        await this.loadFilterOptions();
        await this.loadSavedSearches();
        await this.loadSourceHealth();
        await this.loadAlertSummary();
        await this.loadAlerts();
        await this.loadRules();
        await this.loadEvents();
      }
    },

    loadSavedFilters() {
      try {
        const saved = localStorage.getItem('pulsar_filters');
        if (!saved) return;
        const parsed = JSON.parse(saved);
        const fields = ['actor', 'selectedServices', 'search', 'operation', 'result', 'start', 'end', 'limit', 'includeTags', 'excludeTags'];
        fields.forEach((f) => {
          if (parsed[f] !== undefined) this.filters[f] = parsed[f];
        });
      } catch {}
    },

    saveFilters() {
      try {
        localStorage.setItem('pulsar_filters', JSON.stringify(this.filters));
      } catch {}
    },

    loadPanelState() {
      try {
        const saved = localStorage.getItem('pulsar_panels');
        if (saved) {
          const parsed = JSON.parse(saved);
          Object.keys(parsed).forEach((k) => { if (this.panelState[k] !== undefined) this.panelState[k] = parsed[k]; });
        }
      } catch {}
    },

    savePanelState() {
      try {
        localStorage.setItem('pulsar_panels', JSON.stringify(this.panelState));
      } catch {}
    },

    togglePanel(key) {
      this.panelState[key] = !this.panelState[key];
      this.savePanelState();
    },

    initTheme() {
      const saved = localStorage.getItem('pulsar_theme');
      this.theme = ['light', 'dark', 'auto'].includes(saved) ? saved : 'auto';
      this.applyTheme();
      // Listen for OS changes in auto mode
      if (window.matchMedia) {
        const mql = window.matchMedia('(prefers-color-scheme: light)');
        mql.addEventListener?.('change', () => { if (this.theme === 'auto') this.applyTheme(); });
      }
    },

    setTheme(mode) {
      this.theme = mode;
      localStorage.setItem('pulsar_theme', mode);
      this.applyTheme();
      this.showThemeMenu = false;
    },

    applyTheme() {
      document.documentElement.setAttribute('data-theme', this.theme);
    },

    themeIcon() {
      if (this.theme === 'light') {
        return '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="3.5"/><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3.05 3.05l1.06 1.06M11.89 11.89l1.06 1.06M3.05 12.95l1.06-1.06M11.89 4.11l1.06-1.06"/></svg>';
      }
      if (this.theme === 'dark') {
        return '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M13.5 9.5a5.5 5.5 0 11-7-7 4.5 4.5 0 007 7z"/></svg>';
      }
      return '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="2.5"/><path d="M8 3V1M8 15v-2M3 8H1m14 0h-2M4.34 4.34L2.93 2.93m10.14 10.14l-1.41-1.41M4.34 11.66l-1.41 1.41m10.14-10.14l-1.41 1.41"/></svg>';
    },

    async loadVersion() {
      try {
        const res = await this.apiFetch('/api/version');
        if (res.ok) {
          const body = await res.json();
          this.appVersion = (body.version || '').replace(/^v/, '');
        }
      } catch {}
    },

    authHeader() {
      return this.accessToken ? { Authorization: `Bearer ${this.accessToken}` } : {};
    },

    async apiFetch(url, options = {}) {
      const res = await window.fetch(url, options);
      if (res.status === 401 && this.authConfig?.auth_enabled && this.account) {
        try {
          const scopes = this.authScopes?.length ? this.authScopes : ['openid', 'profile', 'email'];
          const msal = await this.msalInstance.acquireTokenSilent({ scopes, account: this.account });
          const newToken = this.pickToken(msal);
          if (newToken) {
            this.accessToken = newToken;
            return window.fetch(url, { ...options, headers: { ...options.headers, Authorization: `Bearer ${newToken}` } });
          }
        } catch {}
        this.accessToken = null;
        this.account = null;
        this.updateAuthButtons();
        this.statusText = 'Your session has expired. Please sign in again.';
      }
      return res;
    },

    pickToken(res) {
      if (!res) return null;
      const clientId = this.authConfig?.client_id;
      // If accessToken is present and its audience matches our API, use it.
      if (res.accessToken && clientId) {
        try {
          const base64 = res.accessToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
          const padded = base64.padEnd(base64.length + (4 - base64.length % 4) % 4, '=');
          const payload = JSON.parse(atob(padded));
          if (payload.aud === clientId) {
            return res.accessToken;
          }
        } catch {}
      }
      // Fall back to idToken (always aud=clientId) or accessToken
      return res.idToken || res.accessToken || null;
    },

    async initAuth() {
      try {
        const res = await this.apiFetch('/api/config/auth');
        if (!res.ok) {
          console.error('Auth config fetch failed:', res.status, res.statusText);
          this.authConfig = { auth_enabled: false, _error: res.status };
        } else {
          this.authConfig = await res.json();
        }
      } catch (err) {
        console.error('Auth config fetch error:', err);
        this.authConfig = { auth_enabled: false, _error: 'network' };
      }

      if (!this.authConfig?.auth_enabled) {
        this.authBtnText = 'Auth: OFF';
        console.warn('PULSAR auth is disabled. Set AUTH_ENABLED=true in .env to enable login.');
        return;
      }

      const tenantId = this.authConfig.tenant_id;
      const clientId = this.authConfig.client_id;
      if (!clientId || !tenantId) {
        this.authBtnText = 'Auth: misconfigured';
        this.statusText = 'Auth is enabled but client_id or tenant_id is missing. Check .env configuration.';
        console.error('PULSAR auth misconfigured: missing client_id or tenant_id in /api/config/auth');
        return;
      }

      if (typeof msal === 'undefined' || !msal.PublicClientApplication) {
        this.statusText = 'Login library failed to load. Please check network or CDN.';
        return;
      }

      const baseScope = this.authConfig.scope || "";
      this.authScopes = Array.from(new Set(['openid', 'profile', 'email', ...baseScope.split(/[ ,]+/).filter(Boolean)]));
      const authority = `https://login.microsoftonline.com/${tenantId}`;
      const redirectUri = window.location.origin;

      this.msalInstance = new msal.PublicClientApplication({
        auth: { clientId, authority, redirectUri },
        cache: { cacheLocation: 'sessionStorage' },
      });

      const redirectResult = await this.msalInstance.handleRedirectPromise().catch(() => null);
      if (redirectResult) {
        this.account = redirectResult.account;
        this.msalInstance.setActiveAccount(this.account);
        this.accessToken = this.pickToken(redirectResult);
      } else {
        const accounts = this.msalInstance.getAllAccounts();
        if (accounts.length) {
          this.account = accounts[0];
          this.msalInstance.setActiveAccount(this.account);
          this.accessToken = await this.acquireToken(this.authScopes);
        }
      }

      this.updateAuthButtons();
    },

    async acquireToken(scopes) {
      if (!this.msalInstance || !this.account) return null;
      const request = { scopes: scopes && scopes.length ? scopes : ['openid', 'profile', 'email'], account: this.account };
      try {
        const res = await this.msalInstance.acquireTokenSilent(request);
        return this.pickToken(res);
      } catch {
        const res = await this.msalInstance.acquireTokenPopup(request);
        return this.pickToken(res);
      }
    },

    updateAuthButtons() {
      const loggedIn = !!this.account;
      if (this.authConfig?.auth_enabled) {
        this.authBtnText = loggedIn ? 'Logout' : 'Login';
      }
      if (loggedIn) {
        this.acquireToken(this.authScopes).then((t) => { if (t) this.accessToken = t; }).catch(() => {});
        this.statusText = '';
      } else if (this.authConfig?.auth_enabled) {
        this.statusText = 'Please log in to view events.';
      }
    },

    async toggleAuth() {
      if (!this.authConfig?.auth_enabled || !this.msalInstance) return;
      if (this.account) {
        const acc = this.msalInstance.getActiveAccount();
        this.accessToken = null;
        this.account = null;
        this.updateAuthButtons();
        if (acc) await this.msalInstance.logoutPopup({ account: acc });
        return;
      }
      const scopes = this.authScopes && this.authScopes.length ? this.authScopes : ['openid', 'profile', 'email'];
      this.statusText = 'Redirecting to sign in...';
      this.msalInstance.loginRedirect({ scopes });
    },

    async loadEvents(cursor) {
      this.currentCursor = cursor || null;
      const params = new URLSearchParams();
      ['actor', 'operation', 'result', 'search'].forEach((key) => {
        const val = this.filters[key];
        if (val) params.append(key, val);
      });
      if (this.filters.selectedServices && this.filters.selectedServices.length) {
        this.filters.selectedServices.forEach((s) => params.append('services', s));
      }
      if (this.filters.includeTags) {
        this.filters.includeTags.split(/[,;]+/).map((t) => t.trim()).filter(Boolean).forEach((t) => params.append('include_tags', t));
      }
      if (this.filters.excludeTags) {
        this.filters.excludeTags.split(/[,;]+/).map((t) => t.trim()).filter(Boolean).forEach((t) => params.append('exclude_tags', t));
      }
      if (this.filters.start) {
        const d = new Date(this.filters.start);
        if (!isNaN(d.getTime())) params.append('start', d.toISOString());
      }
      if (this.filters.end) {
        const d = new Date(this.filters.end);
        if (!isNaN(d.getTime())) params.append('end', d.toISOString());
      }
      params.append('page_size', String(this.filters.limit || 50));
      if (cursor) params.append('cursor', cursor);

      this.statusText = 'Loading events…';
      this.countText = '';

      if (this.authConfig?.auth_enabled && !this.accessToken) {
        this.statusText = 'Please sign in to load events.';
        return;
      }

      try {
        const res = await this.apiFetch(`/api/events?${params.toString()}`, { headers: { Accept: 'application/json', ...this.authHeader() } });
        if (!res.ok) throw new Error(`Request failed: ${res.status} ${await res.text()}`);
        const body = await res.json();
        this.events = body.items || [];
        this.nextCursor = body.next_cursor || null;
        this.countText = body.total >= 0 ? `${body.total} event${body.total === 1 ? '' : 's'}` : '';
        this.statusText = this.events.length ? '' : 'No events found for these filters.';
        this.saveFilters();
      } catch (err) {
        this.statusText = err.message || 'Failed to load events.';
      }
    },

    async fetchLogs() {
      this.statusText = 'Fetching latest audit logs…';
      if (this.authConfig?.auth_enabled && !this.accessToken) {
        this.statusText = 'Please sign in first.';
        return;
      }
      try {
        const res = await this.apiFetch('/api/fetch-audit-logs', { headers: this.authHeader() });
        if (!res.ok) throw new Error(`Fetch failed: ${res.status} ${await res.text()}`);
        const body = await res.json();
        const errs = Array.isArray(body.errors) && body.errors.length ? `Warnings: ${body.errors.join(' | ')}` : '';
        this.statusText = `Fetched and stored ${body.stored_events || 0} events.${errs ? ' ' + errs : ''} Refreshing list…`;
        this.resetPagination();
        await this.loadEvents();
        await this.loadSourceHealth();
      } catch (err) {
        this.statusText = err.message || 'Failed to fetch audit logs.';
      }
    },

    async loadFilterOptions() {
      if (this.authConfig?.auth_enabled && !this.accessToken) return;
      try {
        const res = await this.apiFetch('/api/filter-options', { headers: this.authHeader() });
        if (!res.ok) return;
        const opts = await res.json();
        this.options.actors = (opts.actors || []).slice(0, 200);
        this.options.services = (opts.services || []).slice(0, 200);
        this.options.operations = (opts.operations || []).slice(0, 200);
        this.options.results = (opts.results || []).slice(0, 200);

        const saved = localStorage.getItem('pulsar_filters');
        if (!saved && this.options.services.length) {
          // Default: show all services (privacy controls handle exclusions server-side)
          this.filters.selectedServices = [...this.options.services];
        } else if (saved) {
          try {
            const parsed = JSON.parse(saved);
            if (parsed.selectedServices) {
              this.filters.selectedServices = parsed.selectedServices.filter((s) => this.options.services.includes(s));
            }
          } catch {}
        }
      } catch {}
    },

    async loadSourceHealth() {
      try {
        const res = await this.apiFetch('/api/source-health', { headers: this.authHeader() });
        if (!res.ok) return;
        this.sourceHealth = await res.json();
      } catch {}
    },

    async loadSavedSearches() {
      try {
        const res = await this.apiFetch('/api/saved-searches', { headers: this.authHeader() });
        if (!res.ok) return;
        this.savedSearches = await res.json();
      } catch {}
    },

    async saveCurrentFilters() {
      const name = prompt('Name this saved filter:');
      if (!name || !name.trim()) return;
      try {
        const res = await this.apiFetch('/api/saved-searches', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
          body: JSON.stringify({ name: name.trim(), filters: { ...this.filters } }),
        });
        if (!res.ok) throw new Error(await res.text());
        const created = await res.json();
        this.savedSearches.unshift(created);
        this.statusText = 'Filters saved.';
        setTimeout(() => { if (this.statusText === 'Filters saved.') this.statusText = ''; }, 2000);
      } catch (err) {
        this.statusText = err.message || 'Failed to save filters.';
      }
    },

    applySavedSearch(ss) {
      if (!ss || !ss.filters) return;
      const fields = ['actor', 'selectedServices', 'search', 'operation', 'result', 'start', 'end', 'limit', 'includeTags', 'excludeTags'];
      fields.forEach((f) => {
        if (ss.filters[f] !== undefined) this.filters[f] = ss.filters[f];
      });
      // Validate selectedServices against current options
      this.filters.selectedServices = this.filters.selectedServices.filter((s) => this.options.services.includes(s));
      this.resetPagination();
      this.loadEvents();
    },

    async deleteSavedSearch(id) {
      if (!confirm('Delete this saved search?')) return;
      try {
        const res = await this.apiFetch(`/api/saved-searches/${id}`, {
          method: 'DELETE',
          headers: this.authHeader(),
        });
        if (!res.ok) throw new Error(await res.text());
        this.savedSearches = this.savedSearches.filter((s) => s.id !== id);
      } catch (err) {
        this.statusText = err.message || 'Failed to delete saved search.';
      }
    },

    resetPagination() {
      this.cursorStack = [];
      this.nextCursor = null;
      this.currentCursor = null;
    },

    goPrev() {
      if (this.cursorStack.length) {
        const prevCursor = this.cursorStack.pop();
        this.loadEvents(prevCursor);
      }
    },

    goNext() {
      if (this.nextCursor) {
        this.cursorStack.push(this.currentCursor);
        this.loadEvents(this.nextCursor);
      }
    },

    clearFilters() {
      this.filters = { actor: '', selectedServices: [...this.options.services], search: '', operation: '', result: '', start: '', end: '', limit: 24, includeTags: '', excludeTags: '' };
      this.saveFilters();
      this.resetPagination();
      this.loadEvents();
    },

    filterByService(service) {
      if (!service) return;
      this.filters.selectedServices = [service];
      this.saveFilters();
      this.resetPagination();
      this.loadEvents();
    },

    filterByResult(result) {
      if (!result) return;
      this.filters.result = this.filters.result === result ? '' : result;
      this.saveFilters();
      this.resetPagination();
      this.loadEvents();
    },

    async loadAlertSummary() {
      try {
        const res = await this.apiFetch('/api/alerts/summary', { headers: this.authHeader() });
        if (!res.ok) return;
        const body = await res.json();
        this.alertSummary.total_open = body.total_open || 0;
        const sev = body.by_status_severity || [];
        this.alertSummary.high = sev.filter((s) => s._id.severity === 'high' && s._id.status === 'open').reduce((a, b) => a + b.count, 0);
        this.alertSummary.medium = sev.filter((s) => s._id.severity === 'medium' && s._id.status === 'open').reduce((a, b) => a + b.count, 0);
        this.alertSummary.low = sev.filter((s) => s._id.severity === 'low' && s._id.status === 'open').reduce((a, b) => a + b.count, 0);
      } catch {}
    },

    async loadAlerts() {
      try {
        const params = new URLSearchParams();
        params.append('page_size', '20');
        params.append('page', String(this.alertsPage));
        if (this.alertsFilter.status) params.append('status', this.alertsFilter.status);
        if (this.alertsFilter.severity) params.append('severity', this.alertsFilter.severity);
        const res = await this.apiFetch(`/api/alerts?${params.toString()}`, { headers: this.authHeader() });
        if (!res.ok) return;
        const body = await res.json();
        this.alerts = body.items || [];
        this.alertsTotal = body.total || 0;
      } catch {}
    },

    async updateAlertStatus(alertId, status) {
      try {
        const res = await this.apiFetch(`/api/alerts/${alertId}/status`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
          body: JSON.stringify({ status }),
        });
        if (res.ok) {
          await this.loadAlerts();
          await this.loadAlertSummary();
        }
      } catch {}
    },

    async loadRules() {
      try {
        const res = await this.apiFetch('/api/rules', { headers: this.authHeader() });
        if (!res.ok) return;
        this.rules = await res.json();
      } catch {}
    },

    openRuleEditor(rule) {
      if (rule) {
        this.ruleEditId = rule.id;
        this.ruleEdit = {
          name: rule.name,
          enabled: rule.enabled,
          severity: rule.severity,
          message: rule.message,
          conditions: JSON.parse(JSON.stringify(rule.conditions)),
        };
      } else {
        this.ruleEditId = null;
        this.ruleEdit = { name: '', enabled: true, severity: 'medium', message: '', conditions: [] };
      }
      this.ruleModalOpen = true;
    },

    async saveRule() {
      const payload = { ...this.ruleEdit };
      try {
        const url = this.ruleEditId ? `/api/rules/${this.ruleEditId}` : '/api/rules';
        const method = this.ruleEditId ? 'PUT' : 'POST';
        const res = await this.apiFetch(url, {
          method,
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(await res.text());
        this.ruleModalOpen = false;
        await this.loadRules();
      } catch (err) {
        alert('Failed to save rule: ' + err.message);
      }
    },

    async toggleRule(ruleId, enabled) {
      try {
        const rule = this.rules.find((r) => r.id === ruleId);
        if (!rule) return;
        const res = await this.apiFetch(`/api/rules/${ruleId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
          body: JSON.stringify({ ...rule, enabled }),
        });
        if (res.ok) await this.loadRules();
      } catch {}
    },

    async deleteRule(ruleId) {
      if (!confirm('Delete this rule?')) return;
      try {
        const res = await this.apiFetch(`/api/rules/${ruleId}`, {
          method: 'DELETE',
          headers: this.authHeader(),
        });
        if (res.ok) await this.loadRules();
      } catch {}
    },


    hasActiveFilters() {
      return this.filters.actor || this.filters.operation || this.filters.result ||
        this.filters.start || this.filters.end || this.filters.includeTags ||
        this.filters.excludeTags ||
        (this.filters.selectedServices && this.filters.selectedServices.length &&
         this.filters.selectedServices.length < this.options.services.length);
    },

    activeFilterSummary() {
      const parts = [];
      if (this.filters.actor) parts.push('actor');
      if (this.filters.operation) parts.push('action');
      if (this.filters.result) parts.push('result');
      if (this.filters.start || this.filters.end) parts.push('time');
      if (this.filters.includeTags || this.filters.excludeTags) parts.push('tags');
      const svcCount = this.filters.selectedServices?.length || 0;
      const allCount = this.options.services?.length || 0;
      if (svcCount && svcCount < allCount) parts.push(`${svcCount} service${svcCount === 1 ? '' : 's'}`);
      return parts.join(', ') || 'none';
    },

    async bulkTagMatching() {
      const tag = prompt('Enter tag to apply to all matching events:');
      if (!tag || !tag.trim()) return;
      const mode = confirm('Click OK to REPLACE existing tags.\nClick Cancel to APPEND the new tag.') ? 'replace' : 'append';
      const params = new URLSearchParams();
      ['actor', 'operation', 'result', 'search'].forEach((key) => {
        const val = this.filters[key];
        if (val) params.append(key, val);
      });
      if (this.filters.selectedServices && this.filters.selectedServices.length) {
        this.filters.selectedServices.forEach((s) => params.append('services', s));
      }
      if (this.filters.includeTags) {
        this.filters.includeTags.split(/[,;]+/).map((t) => t.trim()).filter(Boolean).forEach((t) => params.append('include_tags', t));
      }
      if (this.filters.excludeTags) {
        this.filters.excludeTags.split(/[,;]+/).map((t) => t.trim()).filter(Boolean).forEach((t) => params.append('exclude_tags', t));
      }
      if (this.filters.start) {
        const d = new Date(this.filters.start);
        if (!isNaN(d.getTime())) params.append('start', d.toISOString());
      }
      if (this.filters.end) {
        const d = new Date(this.filters.end);
        if (!isNaN(d.getTime())) params.append('end', d.toISOString());
      }
      this.statusText = 'Applying bulk tag…';
      try {
        const res = await this.apiFetch(`/api/events/bulk-tags?${params.toString()}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
          body: JSON.stringify({ tags: [tag.trim()], mode }),
        });
        if (!res.ok) throw new Error(await res.text());
        const body = await res.json();
        this.statusText = `Tagged ${body.matched} events (${body.modified} modified).`;
        await this.loadEvents();
      } catch (err) {
        this.statusText = err.message || 'Failed to apply bulk tag.';
      }
    },

    displayActor(e) {
      const app = e.actor?.application || e.actor?.app;
      if (app?.displayName) return app.displayName;
      return e.actor_display ||
        (e.actor_resolved?.name) ||
        (e.actor?.user?.displayName && e.actor?.user?.userPrincipalName && e.actor?.user?.displayName !== e.actor?.user?.userPrincipalName
          ? `${e.actor.user.displayName} (${e.actor.user.userPrincipalName})`
          : (e.actor?.user?.displayName || e.actor?.user?.userPrincipalName)) ||
        e.actor?.servicePrincipal?.displayName ||
        'Unknown actor';
    },

    displayTargets(e) {
      if (Array.isArray(e.target_displays) && e.target_displays.length) return e.target_displays.join(', ');
      if (Array.isArray(e.targets) && e.targets.length) return e.targets[0].displayName || e.targets[0].id || '—';
      return '—';
    },

    openModal(e) {
      const seen = new WeakSet();
      try {
        this.modalBody = JSON.stringify(e.raw || e, (key, value) => {
          if (typeof value === 'object' && value !== null) {
            if (seen.has(value)) return '[Circular]';
            seen.add(value);
          }
          return value;
        }, 2);
      } catch (err) {
        this.modalBody = `Error serializing event:\n${err.message}\n\nEvent ID: ${e.id || 'N/A'}`;
      }
      this.modalEventId = e.id || '';
      this.modalOpen = true;
    },

    async copyRawEvent() {
      if (!this.modalBody) return;
      try {
        await navigator.clipboard.writeText(this.modalBody);
        this.statusText = 'Raw event copied to clipboard.';
        setTimeout(() => { if (this.statusText === 'Raw event copied to clipboard.') this.statusText = ''; }, 2000);
      } catch (err) {
        this.statusText = 'Failed to copy to clipboard.';
      }
    },


    async addTag(e, tag) {
      if (!tag.trim()) return;
      const tags = [...(e.tags || []), tag.trim()];
      try {
        const res = await this.apiFetch(`/api/events/${e.id}/tags`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
          body: JSON.stringify({ tags }),
        });
        if (res.ok) e.tags = tags;
      } catch {}
    },

    async addComment(e, text) {
      if (!text.trim()) return;
      try {
        const res = await this.apiFetch(`/api/events/${e.id}/comments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
          body: JSON.stringify({ text: text.trim() }),
        });
        if (res.ok) {
          const c = await res.json();
          e.comments = [...(e.comments || []), c];
        }
      } catch {}
    },

    exportJSON() {
      const blob = new Blob([JSON.stringify(this.events, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pulsar-events-${new Date().toISOString().slice(0,10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    },

    // ── Row expansion ──────────────────────────────────────────
    toggleRow(id) {
      this.expandedEventId = this.expandedEventId === id ? null : id;
    },

    // ── Formatting helpers ──────────────────────────────────────
    relativeTime(ts) {
      if (!ts) return '—';
      const diff = Date.now() - new Date(ts).getTime();
      const mins = Math.floor(diff / 60000);
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

    // ── Timeline / correlation pivot ─────────────────────────
    async openTimeline(entity) {
      if (!entity || entity === '—' || entity === 'Unknown actor') return;
      this.timelineType = 'actor';
      this.timelineTitle = entity;
      this.timelineMode = true;
      this.timelineEvents = [];
      this.timelineNextCursor = null;
      this.timelineExpandedId = null;
      await this.loadTimelineEvents();
    },

    async openCorrelation(id) {
      if (!id || id === '—') return;
      this.timelineType = 'correlation';
      this.timelineTitle = id;
      this.timelineMode = true;
      this.timelineEvents = [];
      this.timelineNextCursor = null;
      this.timelineExpandedId = null;
      await this.loadTimelineEvents();
    },

    closeTimeline() {
      this.timelineMode = false;
      this.timelineType = '';
      this.timelineTitle = '';
      this.timelineEvents = [];
      this.timelineExpandedId = null;
    },

    async loadTimelineEvents(cursor) {
      if (this.authConfig?.auth_enabled && !this.accessToken) return;
      this.timelineLoading = true;
      const params = new URLSearchParams();
      params.append('page_size', '100');
      if (this.timelineType === 'actor') {
        params.append('search', this.timelineTitle);
      } else if (this.timelineType === 'correlation') {
        params.append('correlation_id', this.timelineTitle);
      }
      if (cursor) params.append('cursor', cursor);
      try {
        const res = await this.apiFetch(`/api/events?${params.toString()}`, {
          headers: { Accept: 'application/json', ...this.authHeader() },
        });
        if (!res.ok) throw new Error(`${res.status}`);
        const body = await res.json();
        this.timelineEvents = cursor
          ? [...this.timelineEvents, ...(body.items || [])]
          : (body.items || []);
        this.timelineNextCursor = body.next_cursor || null;
      } catch {}
      finally { this.timelineLoading = false; }
    },

    timelineGrouped() {
      const today = new Date(); today.setHours(0, 0, 0, 0);
      const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
      const groups = [];
      let current = null;
      for (const evt of this.timelineEvents) {
        if (!evt.timestamp) continue;
        const d = new Date(evt.timestamp); d.setHours(0, 0, 0, 0);
        const key = d.toISOString();
        let label;
        if (d.getTime() === today.getTime())     label = 'Today';
        else if (d.getTime() === yesterday.getTime()) label = 'Yesterday';
        else label = d.toLocaleDateString(undefined, {
          weekday: 'long', month: 'long', day: 'numeric',
          ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}),
        });
        if (!current || current.key !== key) {
          current = { key, label, events: [] };
          groups.push(current);
        }
        current.events.push(evt);
      }
      return groups;
    },

    formatEventTime(ts) {
      if (!ts) return '—';
      return new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    },

    toggleTimelineRow(id) {
      this.timelineExpandedId = this.timelineExpandedId === id ? null : id;
    },

    async copyEventId(evt) {
      if (!evt.id) return;
      try {
        await navigator.clipboard.writeText(evt.id);
        const prev = this.statusText;
        this.statusText = 'Event ID copied.';
        setTimeout(() => { if (this.statusText === 'Event ID copied.') this.statusText = prev; }, 1500);
      } catch {}
    },

    exportCSV() {
      if (!this.events.length) return;
      const headers = ['timestamp', 'service', 'operation', 'result', 'actor_display', 'target_displays', 'display_summary'];
      const rows = this.events.map((e) => [
        e.timestamp || '',
        e.service || '',
        e.operation || '',
        e.result || '',
        (e.actor_display || '').replace(/"/g, '""'),
        (Array.isArray(e.target_displays) ? e.target_displays.join('; ') : '').replace(/"/g, '""'),
        (e.display_summary || '').replace(/"/g, '""'),
      ]);
      const csv = [headers.join(','), ...rows.map((r) => r.map((c) => `"${c}"`).join(','))].join('\n');
      const blob = new Blob([csv], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pulsar-events-${new Date().toISOString().slice(0,10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    },
  };
}

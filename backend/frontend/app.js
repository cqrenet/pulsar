function aocApp() {
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
    modalExplanation: '',
    modalExplainLoading: false,
    modalExplainError: '',
    authBtnText: 'Login',
    authConfig: null,
    msalInstance: null,
    account: null,
    accessToken: null,
    authScopes: [],
    filters: {
      actor: '', selectedServices: [], search: '', operation: '', result: '', start: '', end: '', limit: 24, includeTags: '', excludeTags: '',
    },
    panelState: { sourceHealth: true, alerts: true, rules: true, filters: true, ask: true, events: true },
    options: { actors: [], services: [], operations: [], results: [] },
    savedSearches: [],
    appVersion: '',
    repoUrl: 'https://github.com/cqrenet/pulsar',
    docsUrl: 'https://github.com/cqrenet/pulsar/blob/main/README.md',
    aiFeaturesEnabled: true,
    alertSummary: { total_open: 0, high: 0, medium: 0, low: 0 },
    alerts: [],
    alertsTotal: 0,
    alertsPage: 1,
    alertsFilter: { status: 'open', severity: '' },
    rules: [],
    ruleModalOpen: false,
    ruleEditId: null,
    ruleEdit: { name: '', enabled: true, severity: 'medium', message: '', conditions: [] },
    askQuestionText: '',
    askLoading: false,
    askAnswer: '',
    askAnswerHtml: '',
    askEvents: [],
    askLlmUsed: false,
    askLlmError: '',

    async initApp() {
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
        const saved = localStorage.getItem('aoc_filters');
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
        localStorage.setItem('aoc_filters', JSON.stringify(this.filters));
      } catch {}
    },

    loadPanelState() {
      try {
        const saved = localStorage.getItem('aoc_panels');
        if (saved) {
          const parsed = JSON.parse(saved);
          Object.keys(parsed).forEach((k) => { if (this.panelState[k] !== undefined) this.panelState[k] = parsed[k]; });
        }
      } catch {}
    },

    savePanelState() {
      try {
        localStorage.setItem('aoc_panels', JSON.stringify(this.panelState));
      } catch {}
    },

    togglePanel(key) {
      this.panelState[key] = !this.panelState[key];
      this.savePanelState();
    },

    async loadVersion() {
      try {
        const res = await fetch('/api/version');
        if (res.ok) {
          const body = await res.json();
          this.appVersion = (body.version || '').replace(/^v/, '');
        }
      } catch {}
    },

    authHeader() {
      return this.accessToken ? { Authorization: `Bearer ${this.accessToken}` } : {};
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
        const res = await fetch('/api/config/auth');
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

      try {
        const featRes = await fetch('/api/config/features');
        if (featRes.ok) {
          const featBody = await featRes.json();
          this.aiFeaturesEnabled = featBody.ai_features_enabled !== false;
          if (featBody.default_page_size) {
            this.filters.limit = featBody.default_page_size;
          } else {
            this.filters.limit = 24;
          }
        } else {
          this.aiFeaturesEnabled = true;
        }
      } catch {
        this.aiFeaturesEnabled = true;
      }

      if (!this.authConfig?.auth_enabled) {
        this.authBtnText = 'Auth: OFF';
        console.warn('AOC auth is disabled. Set AUTH_ENABLED=true in .env to enable login.');
        return;
      }

      const tenantId = this.authConfig.tenant_id;
      const clientId = this.authConfig.client_id;
      if (!clientId || !tenantId) {
        this.authBtnText = 'Auth: misconfigured';
        this.statusText = 'Auth is enabled but client_id or tenant_id is missing. Check .env configuration.';
        console.error('AOC auth misconfigured: missing client_id or tenant_id in /api/config/auth');
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
        const res = await fetch(`/api/events?${params.toString()}`, { headers: { Accept: 'application/json', ...this.authHeader() } });
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
        const res = await fetch('/api/fetch-audit-logs', { headers: this.authHeader() });
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
        const res = await fetch('/api/filter-options', { headers: this.authHeader() });
        if (!res.ok) return;
        const opts = await res.json();
        this.options.actors = (opts.actors || []).slice(0, 200);
        this.options.services = (opts.services || []).slice(0, 200);
        this.options.operations = (opts.operations || []).slice(0, 200);
        this.options.results = (opts.results || []).slice(0, 200);

        const saved = localStorage.getItem('aoc_filters');
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
        const res = await fetch('/api/source-health', { headers: this.authHeader() });
        if (!res.ok) return;
        this.sourceHealth = await res.json();
      } catch {}
    },

    async loadSavedSearches() {
      try {
        const res = await fetch('/api/saved-searches', { headers: this.authHeader() });
        if (!res.ok) return;
        this.savedSearches = await res.json();
      } catch {}
    },

    async saveCurrentFilters() {
      const name = prompt('Name this saved filter:');
      if (!name || !name.trim()) return;
      try {
        const res = await fetch('/api/saved-searches', {
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
        const res = await fetch(`/api/saved-searches/${id}`, {
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
        const res = await fetch('/api/alerts/summary', { headers: this.authHeader() });
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
        const res = await fetch(`/api/alerts?${params.toString()}`, { headers: this.authHeader() });
        if (!res.ok) return;
        const body = await res.json();
        this.alerts = body.items || [];
        this.alertsTotal = body.total || 0;
      } catch {}
    },

    async updateAlertStatus(alertId, status) {
      try {
        const res = await fetch(`/api/alerts/${alertId}/status`, {
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
        const res = await fetch('/api/rules', { headers: this.authHeader() });
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
        const res = await fetch(url, {
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
        const res = await fetch(`/api/rules/${ruleId}`, {
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
        const res = await fetch(`/api/rules/${ruleId}`, {
          method: 'DELETE',
          headers: this.authHeader(),
        });
        if (res.ok) await this.loadRules();
      } catch {}
    },

    async askQuestion() {
      const q = this.askQuestionText.trim();
      if (!q) return;
      this.askLoading = true;
      this.askAnswer = '';
      this.askAnswerHtml = '';
      this.askEvents = [];
      this.askLlmError = '';

      const payload = { question: q };
      if (this.filters.selectedServices && this.filters.selectedServices.length) {
        payload.services = this.filters.selectedServices;
      }
      if (this.filters.actor) payload.actor = this.filters.actor;
      if (this.filters.operation) payload.operation = this.filters.operation;
      if (this.filters.result) payload.result = this.filters.result;
      if (this.filters.start) payload.start = new Date(this.filters.start).toISOString();
      if (this.filters.end) payload.end = new Date(this.filters.end).toISOString();
      if (this.filters.includeTags) {
        payload.include_tags = this.filters.includeTags.split(/[,;]+/).map(t => t.trim()).filter(Boolean);
      }
      if (this.filters.excludeTags) {
        payload.exclude_tags = this.filters.excludeTags.split(/[,;]+/).map(t => t.trim()).filter(Boolean);
      }

      try {
        const res = await fetch('/api/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(await res.text());
        const body = await res.json();
        this.askAnswer = body.answer;
        this.askAnswerHtml = this._mdToHtml(body.answer);
        this.askEvents = body.events || [];
        this.askLlmUsed = body.llm_used;
        this.askLlmError = body.llm_error || '';
      } catch (err) {
        this.askAnswer = 'Sorry, something went wrong: ' + (err.message || 'Unknown error');
        this.askAnswerHtml = this.askAnswer;
      } finally {
        this.askLoading = false;
      }
    },

    clearAsk() {
      this.askQuestionText = '';
      this.askAnswer = '';
      this.askAnswerHtml = '';
      this.askEvents = [];
      this.askLlmUsed = false;
      this.askLlmError = '';
    },

    _mdToHtml(text) {
      // Very lightweight markdown-to-HTML for LLM answers
      return text
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/Event #(\d+)/g, '<strong>Event #$1</strong>')
        .replace(/\n/g, '<br>');
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
        const res = await fetch(`/api/events/bulk-tags?${params.toString()}`, {
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
      this.modalExplanation = '';
      this.modalExplainError = '';
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

    async explainEvent() {
      if (!this.modalEventId) return;
      this.modalExplainLoading = true;
      this.modalExplanation = '';
      this.modalExplainError = '';
      try {
        const res = await fetch(`/api/events/${this.modalEventId}/explain`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...this.authHeader() },
        });
        if (!res.ok) throw new Error(await res.text());
        const body = await res.json();
        this.modalExplanation = body.explanation;
        this.modalExplainError = body.llm_error || '';
      } catch (err) {
        this.modalExplainError = err.message || 'Failed to explain event.';
      } finally {
        this.modalExplainLoading = false;
      }
    },

    async addTag(e, tag) {
      if (!tag.trim()) return;
      const tags = [...(e.tags || []), tag.trim()];
      try {
        const res = await fetch(`/api/events/${e.id}/tags`, {
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
        const res = await fetch(`/api/events/${e.id}/comments`, {
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
      a.download = `aoc-events-${new Date().toISOString().slice(0,10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
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
      a.download = `aoc-events-${new Date().toISOString().slice(0,10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    },
  };
}

// HTTP helpers: authenticated fetch with one silent token refresh, JSON requests, status messages.
(window.PulsarParts ??= {}).api = () => ({
  statusText: '',

  authEnabled() {
    return !!(this.authConfig && this.authConfig.auth_enabled);
  },

  // True when the API needs a login that has not happened yet.
  needsLogin() {
    return this.authEnabled() && !this.accessToken;
  },

  authHeader() {
    return this.accessToken ? { Authorization: `Bearer ${this.accessToken}` } : {};
  },

  // fetch() that always sends the bearer token and retries once with a refreshed token on 401.
  async apiFetch(url, options = {}) {
    const withAuth = (extra) => ({ ...options, headers: { ...this.authHeader(), ...options.headers, ...extra } });
    const res = await window.fetch(url, withAuth());
    if (res.status === 401 && this.authEnabled() && this.account) {
      try {
        const scopes = this.authScopes?.length ? this.authScopes : ['openid', 'profile', 'email'];
        const msal = await this.msalInstance.acquireTokenSilent({ scopes, account: this.account });
        const newToken = this.pickToken(msal);
        if (newToken) {
          this.accessToken = newToken;
          return window.fetch(url, withAuth());
        }
      } catch {}
      this.accessToken = null;
      this.account = null;
      this.updateAuthButtons();
      this.statusText = 'Your session has expired. Please sign in again.';
    }
    return res;
  },

  // Send a JSON body (POST/PUT/PATCH) or no body (DELETE).
  sendJson(url, method, body) {
    return this.apiFetch(url, {
      method,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  },

  // GET a JSON resource; on failure report it in the status line and return null.
  async getJson(url, what) {
    try {
      const res = await this.apiFetch(url, { headers: { Accept: 'application/json' } });
      if (res.ok) return await res.json();
      this.reportError(what, `${res.status}`);
    } catch (err) {
      this.reportError(what, err);
    }
    return null;
  },

  reportError(what, err) {
    console.error(`PULSAR: failed to load ${what}`, err);
    if (!this.needsLogin()) this.statusText = `Failed to load ${what}.`;
  },

  // Show a message in the status line, then clear it unless something else replaced it.
  flash(message, ms = 2000) {
    this.statusText = message;
    setTimeout(() => { if (this.statusText === message) this.statusText = ''; }, ms);
  },

  async loadVersion() {
    try {
      const res = await window.fetch('/api/version');
      if (res.ok) this.appVersion = ((await res.json()).version || '').replace(/^v/, '');
    } catch {}
  },
});

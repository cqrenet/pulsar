// Microsoft Entra sign-in (MSAL).
const DEFAULT_SCOPES = ['openid', 'profile', 'email'];

(window.PulsarParts ??= {}).auth = () => ({
  authBtnText: 'Login',
  authConfig: null,
  msalInstance: null,
  account: null,
  accessToken: null,
  authScopes: [],

  pickToken(res) {
    if (!res) return null;
    const clientId = (this.authConfig?.client_id || '').replace(/^api:\/\//, '');
    // Only an access token issued for this API is accepted by the backend (ID tokens are rejected).
    if (res.accessToken && clientId) {
      try {
        const base64 = res.accessToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        const padded = base64.padEnd(base64.length + (4 - base64.length % 4) % 4, '=');
        const payload = JSON.parse(atob(padded));
        if (payload.aud === clientId || payload.aud === `api://${clientId}`) return res.accessToken;
      } catch {}
    }
    this.statusText = 'Sign-in returned no access token for PULSAR. Set AUTH_SCOPE to api://<client-id>/user_impersonation.';
    return null;
  },

  async initAuth() {
    try {
      const res = await window.fetch('/api/config/auth');
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

    if (!this.authEnabled()) {
      this.authBtnText = 'Auth: OFF';
      console.warn('PULSAR auth is disabled. Set AUTH_ENABLED=true in .env to enable login.');
      return;
    }

    const { tenant_id: tenantId, client_id: clientId } = this.authConfig;
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

    const baseScope = this.authConfig.scope || '';
    this.authScopes = Array.from(new Set([...DEFAULT_SCOPES, ...baseScope.split(/[ ,]+/).filter(Boolean)]));
    this.msalInstance = new msal.PublicClientApplication({
      auth: { clientId, authority: `https://login.microsoftonline.com/${tenantId}`, redirectUri: window.location.origin },
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
    const request = { scopes: scopes && scopes.length ? scopes : DEFAULT_SCOPES, account: this.account };
    try {
      return this.pickToken(await this.msalInstance.acquireTokenSilent(request));
    } catch {
      return this.pickToken(await this.msalInstance.acquireTokenPopup(request));
    }
  },

  updateAuthButtons() {
    const loggedIn = !!this.account;
    if (this.authEnabled()) this.authBtnText = loggedIn ? 'Logout' : 'Login';
    if (loggedIn) {
      this.acquireToken(this.authScopes).then((t) => { if (t) this.accessToken = t; }).catch(() => {});
      this.statusText = '';
    } else if (this.authEnabled()) {
      this.statusText = 'Please log in to view events.';
    }
  },

  async toggleAuth() {
    if (!this.authEnabled() || !this.msalInstance) return;
    if (this.account) {
      const acc = this.msalInstance.getActiveAccount();
      this.accessToken = null;
      this.account = null;
      this.updateAuthButtons();
      if (acc) await this.msalInstance.logoutPopup({ account: acc });
      return;
    }
    this.statusText = 'Redirecting to sign in...';
    this.msalInstance.loginRedirect({ scopes: this.authScopes.length ? this.authScopes : DEFAULT_SCOPES });
  },
});

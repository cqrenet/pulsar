// Alerts and alert rules.
function emptyRule() {
  return { name: '', enabled: true, severity: 'medium', message: '', conditions: [] };
}

(window.PulsarParts ??= {}).alerts = () => ({
  alertSummary: { total_open: 0, high: 0, medium: 0, low: 0 },
  alerts: [],
  alertsTotal: 0,
  alertsPage: 1,
  alertsFilter: { status: 'open', severity: '' },
  rules: [],
  ruleModalOpen: false,
  ruleEditId: null,
  ruleEdit: emptyRule(),

  async loadAlertSummary() {
    const body = await this.getJson('/api/alerts/summary', 'alert summary');
    if (!body) return;
    const open = (severity) => (body.by_status_severity || [])
      .filter((s) => s._id.severity === severity && s._id.status === 'open')
      .reduce((total, s) => total + s.count, 0);
    this.alertSummary = { total_open: body.total_open || 0, high: open('high'), medium: open('medium'), low: open('low') };
  },

  async loadAlerts() {
    const params = new URLSearchParams({ page_size: '20', page: String(this.alertsPage) });
    if (this.alertsFilter.status) params.append('status', this.alertsFilter.status);
    if (this.alertsFilter.severity) params.append('severity', this.alertsFilter.severity);
    const body = await this.getJson(`/api/alerts?${params}`, 'alerts');
    if (!body) return;
    this.alerts = body.items || [];
    this.alertsTotal = body.total || 0;
  },

  alertsGoTo(page) {
    this.alertsPage = page;
    return this.loadAlerts();
  },

  async updateAlertStatus(alertId, status) {
    try {
      const res = await this.sendJson(`/api/alerts/${alertId}/status`, 'PATCH', { status });
      if (res.ok) {
        await this.loadAlerts();
        await this.loadAlertSummary();
      }
    } catch (err) {
      this.reportError('alert status', err);
    }
  },

  async loadRules() {
    this.rules = (await this.getJson('/api/rules', 'rules')) || this.rules;
  },

  openRuleEditor(rule) {
    this.ruleEditId = rule ? rule.id : null;
    this.ruleEdit = rule
      ? { name: rule.name, enabled: rule.enabled, severity: rule.severity, message: rule.message, conditions: JSON.parse(JSON.stringify(rule.conditions)) }
      : emptyRule();
    this.ruleModalOpen = true;
  },

  async saveRule() {
    try {
      const res = this.ruleEditId
        ? await this.sendJson(`/api/rules/${this.ruleEditId}`, 'PUT', this.ruleEdit)
        : await this.sendJson('/api/rules', 'POST', this.ruleEdit);
      if (!res.ok) throw new Error(await res.text());
      this.ruleModalOpen = false;
      await this.loadRules();
    } catch (err) {
      alert('Failed to save rule: ' + err.message);
    }
  },

  async toggleRule(ruleId, enabled) {
    const rule = this.rules.find((r) => r.id === ruleId);
    if (!rule) return;
    try {
      const res = await this.sendJson(`/api/rules/${ruleId}`, 'PUT', { ...rule, enabled });
      if (res.ok) await this.loadRules();
    } catch (err) {
      this.reportError('rule', err);
    }
  },

  async deleteRule(ruleId) {
    if (!confirm('Delete this rule?')) return;
    try {
      const res = await this.sendJson(`/api/rules/${ruleId}`, 'DELETE');
      if (res.ok) await this.loadRules();
    } catch (err) {
      this.reportError('rule', err);
    }
  },
});

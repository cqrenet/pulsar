// Event list: filters, paging, saved searches, bulk tagging, row details, export.
const FILTER_FIELDS = ['actor', 'selectedServices', 'search', 'operation', 'result', 'start', 'end', 'limit', 'includeTags', 'excludeTags'];
const FILTER_STORAGE_KEY = 'pulsar_filters';

function defaultFilters(services = []) {
  return {
    actor: '', selectedServices: [...services], search: '', operation: '', result: '',
    start: '', end: '', limit: 50, includeTags: '', excludeTags: '',
  };
}

function splitTags(text) {
  return (text || '').split(/[,;]+/).map((t) => t.trim()).filter(Boolean);
}

function downloadFile(content, type, name) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

(window.PulsarParts ??= {}).events = () => ({
  events: [],
  sourceHealth: [],
  countText: '',
  cursorStack: [],
  nextCursor: null,
  currentCursor: null,
  modalOpen: false,
  modalBody: '',
  modalEventId: '',
  expandedEventId: null,
  showAdvancedFilters: false,
  showServiceDropdown: false,
  showExportMenu: false,
  filters: defaultFilters(),
  filtersRestored: false,
  options: { actors: [], services: [], operations: [], results: [] },
  savedSearches: [],

  // ── Filters ──────────────────────────────────────────────
  loadSavedFilters() {
    try {
      const parsed = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || 'null');
      if (!parsed) return;
      FILTER_FIELDS.forEach((f) => { if (parsed[f] !== undefined) this.filters[f] = parsed[f]; });
      this.filtersRestored = true;
    } catch {}
  },

  saveFilters() {
    try { localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(this.filters)); } catch {}
  },

  // Query-string for /api/events and /api/events/bulk-tags from the current filter form.
  buildEventParams() {
    const f = this.filters;
    const params = new URLSearchParams();
    ['actor', 'operation', 'result', 'search'].forEach((key) => { if (f[key]) params.append(key, f[key]); });
    (f.selectedServices || []).forEach((s) => params.append('services', s));
    splitTags(f.includeTags).forEach((t) => params.append('include_tags', t));
    splitTags(f.excludeTags).forEach((t) => params.append('exclude_tags', t));
    ['start', 'end'].forEach((key) => {
      const d = new Date(f[key]);
      if (f[key] && !isNaN(d.getTime())) params.append(key, d.toISOString());
    });
    return params;
  },

  applyFilters() {
    this.resetPagination();
    return this.loadEvents();
  },

  // Persist the filters and reload from the first page.
  refilter() {
    this.saveFilters();
    return this.applyFilters();
  },

  clearFilters() {
    this.filters = defaultFilters(this.options.services);
    return this.refilter();
  },

  selectAllServices() { this.filters.selectedServices = [...this.options.services]; },
  clearServices() { this.filters.selectedServices = []; },

  filterByService(service) {
    if (!service) return;
    this.filters.selectedServices = [service];
    return this.refilter();
  },

  filterByResult(result) {
    if (!result) return;
    this.filters.result = this.filters.result === result ? '' : result;
    return this.refilter();
  },

  hasActiveFilters() {
    const f = this.filters;
    return f.actor || f.operation || f.result || f.start || f.end || f.includeTags || f.excludeTags ||
      (f.selectedServices && f.selectedServices.length && f.selectedServices.length < this.options.services.length);
  },

  activeFilterSummary() {
    const f = this.filters;
    const parts = [];
    if (f.actor) parts.push('actor');
    if (f.operation) parts.push('action');
    if (f.result) parts.push('result');
    if (f.start || f.end) parts.push('time');
    if (f.includeTags || f.excludeTags) parts.push('tags');
    const svcCount = f.selectedServices?.length || 0;
    if (svcCount && svcCount < (this.options.services?.length || 0)) parts.push(`${svcCount} service${svcCount === 1 ? '' : 's'}`);
    return parts.join(', ') || 'none';
  },

  async loadFilterOptions() {
    if (this.needsLogin()) return;
    const opts = await this.getJson('/api/filter-options', 'filter options');
    if (!opts) return;
    ['actors', 'services', 'operations', 'results'].forEach((k) => { this.options[k] = (opts[k] || []).slice(0, 200); });
    if (!this.filtersRestored && this.options.services.length) {
      // Default: show all services (privacy controls handle exclusions server-side)
      this.filters.selectedServices = [...this.options.services];
    } else {
      this.filters.selectedServices = (this.filters.selectedServices || []).filter((s) => this.options.services.includes(s));
    }
  },

  async loadSourceHealth() {
    this.sourceHealth = (await this.getJson('/api/source-health', 'source health')) || this.sourceHealth;
  },

  // ── Event list ───────────────────────────────────────────
  async loadEvents(cursor) {
    this.currentCursor = cursor || null;
    this.statusText = 'Loading events…';
    this.countText = '';
    if (this.needsLogin()) {
      this.statusText = 'Please sign in to load events.';
      return;
    }
    const params = this.buildEventParams();
    params.append('page_size', String(this.filters.limit || 50));
    if (cursor) params.append('cursor', cursor);

    try {
      const res = await this.apiFetch(`/api/events?${params}`, { headers: { Accept: 'application/json' } });
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
    if (this.needsLogin()) {
      this.statusText = 'Please sign in first.';
      return;
    }
    this.statusText = 'Fetching latest audit logs…';
    try {
      const res = await this.apiFetch('/api/fetch-audit-logs');
      if (!res.ok) throw new Error(`Fetch failed: ${res.status} ${await res.text()}`);
      const body = await res.json();
      const warnings = body.errors?.length ? ` Warnings: ${body.errors.join(' | ')}` : '';
      this.statusText = `Fetched and stored ${body.stored_events || 0} events.${warnings} Refreshing list…`;
      await this.applyFilters();
      await this.loadSourceHealth();
    } catch (err) {
      this.statusText = err.message || 'Failed to fetch audit logs.';
    }
  },

  resetPagination() {
    this.cursorStack = [];
    this.nextCursor = null;
    this.currentCursor = null;
  },

  goPrev() {
    if (this.cursorStack.length) this.loadEvents(this.cursorStack.pop());
  },

  goNext() {
    if (!this.nextCursor) return;
    this.cursorStack.push(this.currentCursor);
    this.loadEvents(this.nextCursor);
  },

  // ── Saved searches ───────────────────────────────────────
  async loadSavedSearches() {
    this.savedSearches = (await this.getJson('/api/saved-searches', 'saved searches')) || this.savedSearches;
  },

  async saveCurrentFilters() {
    const name = prompt('Name this saved filter:');
    if (!name || !name.trim()) return;
    try {
      const res = await this.sendJson('/api/saved-searches', 'POST', { name: name.trim(), filters: { ...this.filters } });
      if (!res.ok) throw new Error(await res.text());
      this.savedSearches.unshift(await res.json());
      this.flash('Filters saved.');
    } catch (err) {
      this.statusText = err.message || 'Failed to save filters.';
    }
  },

  applySavedSearch(ss) {
    if (!ss || !ss.filters) return;
    FILTER_FIELDS.forEach((f) => { if (ss.filters[f] !== undefined) this.filters[f] = ss.filters[f]; });
    this.filters.selectedServices = this.filters.selectedServices.filter((s) => this.options.services.includes(s));
    this.applyFilters();
  },

  async deleteSavedSearch(id) {
    if (!confirm('Delete this saved search?')) return;
    try {
      const res = await this.sendJson(`/api/saved-searches/${id}`, 'DELETE');
      if (!res.ok) throw new Error(await res.text());
      this.savedSearches = this.savedSearches.filter((s) => s.id !== id);
    } catch (err) {
      this.statusText = err.message || 'Failed to delete saved search.';
    }
  },

  // ── Bulk tagging ─────────────────────────────────────────
  async bulkTagMatching() {
    const tag = prompt('Enter tag to apply to all matching events:');
    if (!tag || !tag.trim()) return;
    const answer = prompt('Type "append" to add the tag, or "replace" to overwrite the existing tags:', 'append');
    const mode = (answer || '').trim().toLowerCase();
    if (mode !== 'append' && mode !== 'replace') return;
    this.statusText = 'Applying bulk tag…';
    try {
      const res = await this.sendJson(`/api/events/bulk-tags?${this.buildEventParams()}`, 'POST', { tags: [tag.trim()], mode });
      if (!res.ok) throw new Error(await res.text());
      const body = await res.json();
      this.statusText = `Tagged ${body.matched} events (${body.modified} modified).`;
      await this.loadEvents();
    } catch (err) {
      this.statusText = err.message || 'Failed to apply bulk tag.';
    }
  },

  async addTag(e, input) {
    const tag = input.value.trim();
    input.value = '';
    if (!tag) return;
    const tags = [...(e.tags || []), tag];
    try {
      const res = await this.sendJson(`/api/events/${e.id}/tags`, 'PATCH', { tags });
      if (res.ok) e.tags = tags;
    } catch (err) {
      this.reportError('tags', err);
    }
  },

  async addComment(e, input) {
    const text = input.value.trim();
    input.value = '';
    if (!text) return;
    try {
      const res = await this.sendJson(`/api/events/${e.id}/comments`, 'POST', { text });
      if (res.ok) e.comments = [...(e.comments || []), await res.json()];
    } catch (err) {
      this.reportError('comments', err);
    }
  },

  // ── Row display ──────────────────────────────────────────
  toggleRow(id) {
    this.expandedEventId = this.expandedEventId === id ? null : id;
  },

  // The server resolves the best actor label (actor_display); the UI only supplies a fallback.
  displayActor(e) {
    return e.actor_display || 'Unknown actor';
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

  async copyText(text, doneMessage, failMessage) {
    try {
      await navigator.clipboard.writeText(text);
      this.flash(doneMessage, 1500);
    } catch {
      this.statusText = failMessage;
    }
  },

  copyRawEvent() {
    if (this.modalBody) return this.copyText(this.modalBody, 'Raw event copied to clipboard.', 'Failed to copy to clipboard.');
  },

  copyEventId(evt) {
    if (evt.id) return this.copyText(evt.id, 'Event ID copied.', 'Failed to copy event ID.');
  },

  // ── Export ───────────────────────────────────────────────
  exportJSON() {
    this.showExportMenu = false;
    downloadFile(JSON.stringify(this.events, null, 2), 'application/json', `pulsar-events-${new Date().toISOString().slice(0, 10)}.json`);
  },

  exportCSV() {
    this.showExportMenu = false;
    if (!this.events.length) return;
    const headers = ['timestamp', 'service', 'operation', 'result', 'actor_display', 'target_displays', 'display_summary'];
    const rows = this.events.map((e) => [
      e.timestamp, e.service, e.operation, e.result, e.actor_display,
      Array.isArray(e.target_displays) ? e.target_displays.join('; ') : '',
      e.display_summary,
    ]);
    // Names come from the tenant and can be attacker-chosen: neutralise spreadsheet formulas.
    const cell = (v) => {
      let s = String(v ?? '');
      if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
      return `"${s.replace(/"/g, '""')}"`;
    };
    const csv = [headers.join(','), ...rows.map((r) => r.map(cell).join(','))].join('\n');
    downloadFile(csv, 'text/csv', `pulsar-events-${new Date().toISOString().slice(0, 10)}.csv`);
  },
});

// Timeline / correlation pivot view.
(window.PulsarParts ??= {}).timeline = () => ({
  timelineMode: false,
  timelineType: '', // 'actor' | 'correlation'
  timelineTitle: '',
  timelineEvents: [],
  timelineLoading: false,
  timelineNextCursor: null,
  timelineExpandedId: null,

  openTimeline(entity) {
    if (entity && entity !== '—' && entity !== 'Unknown actor') return this.startTimeline('actor', entity);
  },

  openCorrelation(id) {
    if (id && id !== '—') return this.startTimeline('correlation', id);
  },

  startTimeline(type, title) {
    this.timelineType = type;
    this.timelineTitle = title;
    this.timelineMode = true;
    this.timelineEvents = [];
    this.timelineNextCursor = null;
    this.timelineExpandedId = null;
    return this.loadTimelineEvents();
  },

  closeTimeline() {
    this.timelineMode = false;
    this.timelineType = '';
    this.timelineTitle = '';
    this.timelineEvents = [];
    this.timelineExpandedId = null;
  },

  async loadTimelineEvents(cursor) {
    if (this.needsLogin()) return;
    this.timelineLoading = true;
    const params = new URLSearchParams({ page_size: '100' });
    params.append(this.timelineType === 'correlation' ? 'correlation_id' : 'search', this.timelineTitle);
    if (cursor) params.append('cursor', cursor);
    try {
      const body = await this.getJson(`/api/events?${params}`, 'timeline');
      if (!body) return;
      this.timelineEvents = cursor ? [...this.timelineEvents, ...(body.items || [])] : (body.items || []);
      this.timelineNextCursor = body.next_cursor || null;
    } finally {
      this.timelineLoading = false;
    }
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
      if (!current || current.key !== key) {
        let label;
        if (d.getTime() === today.getTime()) label = 'Today';
        else if (d.getTime() === yesterday.getTime()) label = 'Yesterday';
        else label = d.toLocaleDateString(undefined, {
          weekday: 'long', month: 'long', day: 'numeric',
          ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}),
        });
        current = { key, label, events: [] };
        groups.push(current);
      }
      current.events.push(evt);
    }
    return groups;
  },

  toggleTimelineRow(id) {
    this.timelineExpandedId = this.timelineExpandedId === id ? null : id;
  },
});

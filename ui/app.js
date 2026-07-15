function myHealthApp() {
  return {
    apiBase: '',
    patientId: '7c787a96-de6d-4a9d-88cc-94a15dc93aee',
    authChecked: false,
    authenticated: false,
    password: '',
    loginBusy: false,
    loginError: '',
    patientLoading: false,
    patientError: '',
    journeyLoading: false,
    journeyError: '',
    journeyFilter: 'all',
    view: 'overview',
    draftQuestion: '',
    chatBusy: false,
    chatSessionId: null,
    messages: [],
    chatContextResourceIds: [],
    expanded: { conditions: false, medications: false, recent_observations: false, encounters: false },
    patientData: { patient_id: '', patient: {}, conditions: [], medications: [], recent_observations: [], encounters: [] },
    journeyData: { group_id: '', memory: {}, current_state: { active_conditions: [], current_medications: [], allergies: [], recent_results: [], recent_visits: [] }, episodes: [], total_resources: 0, dated_resources: 0, undated_resources: 0, generated_at: '', source: '' },
    episodeDetail: { open: false, episode: null },
    detail: { open: false, loading: false, error: '', resourceType: '', resourceId: '', title: '', relationship: '', tab: 'details', resource: null, related: [], history: [] },

    get patientName() { return 'John Doe'; },
    get viewEyebrow() { return this.view === 'chat' ? 'Assistant' : this.view === 'journey' ? 'Longitudinal record' : 'Patient record'; },
    get viewTitle() { return this.view === 'chat' ? 'Ask MyHealth' : this.view === 'journey' ? 'Patient journey' : 'Patient overview'; },
    get patientAge() {
      const birthDate = this.patientData.patient?.birthDate;
      if (!birthDate) return '';
      const birthday = new Date(`${birthDate}T00:00:00`);
      if (Number.isNaN(birthday.getTime())) return '';
      const today = new Date();
      let age = today.getFullYear() - birthday.getFullYear();
      const hasHadBirthday = today.getMonth() > birthday.getMonth() || (today.getMonth() === birthday.getMonth() && today.getDate() >= birthday.getDate());
      return hasHadBirthday ? age : age - 1;
    },

    async init() {
      this.refreshIcons();
      try {
        const response = await fetch(`${this.apiBase}/api/auth/status`, { credentials: 'same-origin' });
        const status = await response.json();
        if (!status.configured) this.loginError = 'Prototype authentication is not configured on the server.';
        this.authenticated = Boolean(status.authenticated);
        this.authChecked = true;
        if (this.authenticated) await Promise.all([this.loadPatientDetails(), this.loadPatientJourney()]);
      } catch (error) {
        this.authChecked = true;
        this.loginError = `Unable to check authentication: ${error.message}`;
      }
      this.refreshIcons();
    },

    async apiFetch(path, options = {}) {
      const response = await fetch(`${this.apiBase}${path}`, { ...options, credentials: 'same-origin' });
      if (response.status === 401) {
        this.authenticated = false;
        this.loginError = 'Your session expired. Sign in again.';
        throw new Error('Authentication required');
      }
      if (!response.ok) {
        let message = `Request failed (${response.status})`;
        try { message = (await response.json()).detail || message; } catch (_) { /* Keep status message. */ }
        throw new Error(message);
      }
      return response;
    },

    async login() {
      this.loginBusy = true;
      this.loginError = '';
      try {
        const response = await fetch(`${this.apiBase}/api/auth/login`, {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: this.password }),
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || 'Sign in failed');
        }
        this.password = '';
        this.authenticated = true;
        await Promise.all([this.loadPatientDetails(), this.loadPatientJourney()]);
      } catch (error) {
        this.loginError = error.message;
      } finally {
        this.loginBusy = false;
        this.refreshIcons();
      }
    },

    async logout() {
      try { await fetch(`${this.apiBase}/api/auth/logout`, { method: 'POST', credentials: 'same-origin' }); } finally {
        this.authenticated = false;
        this.password = '';
        this.messages = [];
        this.chatSessionId = null;
        this.chatContextResourceIds = [];
        this.closeEpisode();
        this.closeDetail();
        this.refreshIcons();
      }
    },

    async loadPatientDetails() {
      this.patientLoading = true;
      this.patientError = '';
      try {
        const response = await this.apiFetch(`/api/patient/${this.patientId}/details`);
        const data = await response.json();
        this.patientData = {
          patient_id: data.patient_id || '', patient: data.patient || {}, conditions: data.conditions || [], medications: data.medications || [], recent_observations: data.recent_observations || [], encounters: data.encounters || [],
        };
      } catch (error) {
        if (error.message !== 'Authentication required') this.patientError = `Patient record could not be loaded: ${error.message}`;
      } finally {
        this.patientLoading = false;
        this.$nextTick(() => this.refreshIcons());
      }
    },

    async loadPatientJourney() {
      this.journeyLoading = true;
      this.journeyError = '';
      try {
        const response = await this.apiFetch(`/api/patient/${this.patientId}/journey`);
        const data = await response.json();
        this.journeyData = {
          group_id: data.group_id || '',
          memory: data.memory || {},
          current_state: {
            active_conditions: data.current_state?.active_conditions || [],
            current_medications: data.current_state?.current_medications || [],
            allergies: data.current_state?.allergies || [],
            recent_results: data.current_state?.recent_results || [],
            recent_visits: data.current_state?.recent_visits || [],
          },
          episodes: data.episodes || [],
          total_resources: data.total_resources || 0,
          dated_resources: data.dated_resources || 0,
          undated_resources: data.undated_resources || 0,
          generated_at: data.generated_at || '',
          source: data.source || '',
        };
      } catch (error) {
        if (error.message !== 'Authentication required') this.journeyError = `Patient journey could not be loaded: ${error.message}`;
      } finally {
        this.journeyLoading = false;
        this.$nextTick(() => this.refreshIcons());
      }
    },

    setView(view) {
      this.view = view;
      if (view === 'journey' && !this.journeyLoading && !this.journeyData.episodes.length && !this.journeyError) this.loadPatientJourney();
      this.$nextTick(() => {
        this.refreshIcons();
        if (view === 'chat') this.$refs.questionInput?.focus();
      });
    },

    visibleItems(key) {
      const items = this.patientData[key] || [];
      return this.expanded[key] ? items : items.slice(0, 4);
    },
    hasMore(key) { return (this.patientData[key] || []).length > 4; },
    toggleList(key) { this.expanded[key] = !this.expanded[key]; this.$nextTick(() => this.refreshIcons()); },

    askAbout(kind, name) {
      this.chatContextResourceIds = [];
      this.draftQuestion = `Tell me about the ${kind}${name ? ` ${name}` : ''} in this patient’s record.`;
      this.setView('chat');
    },
    openChatWithDraft() { this.setView('chat'); },
    useSuggestion(question) { this.draftQuestion = question; this.sendQuestion(); },
    newConversation() { this.messages = []; this.chatSessionId = null; this.chatContextResourceIds = []; this.draftQuestion = ''; this.$nextTick(() => this.$refs.questionInput?.focus()); },

    journeyFilterOptions() {
      return [
        { value: 'all', label: 'All', icon: 'list-filter' },
        { value: 'encounter', label: 'Visits', icon: 'calendar-days' },
        { value: 'result', label: 'Results', icon: 'flask-conical' },
        { value: 'medication', label: 'Medications', icon: 'pill' },
        { value: 'condition', label: 'Conditions', icon: 'stethoscope' },
        { value: 'procedure', label: 'Procedures', icon: 'clipboard-check' },
      ];
    },
    episodeMatchesFilter(episode, type) {
      return type === 'all' || episode.type === type || Number(episode.category_counts?.[type] || 0) > 0;
    },
    journeyEpisodeCount(type) { return this.journeyData.episodes.filter((episode) => this.episodeMatchesFilter(episode, type)).length; },
    filteredJourneyEpisodes() { return this.journeyData.episodes.filter((episode) => this.episodeMatchesFilter(episode, this.journeyFilter)); },
    journeyGroups() {
      const groups = [];
      for (const episode of this.filteredJourneyEpisodes()) {
        const label = episode.date ? new Date(episode.date).toLocaleDateString(undefined, { year: 'numeric', month: 'long' }) : 'Date unavailable';
        const current = groups[groups.length - 1];
        if (!current || current.label !== label) groups.push({ label, episodes: [episode] });
        else current.episodes.push(episode);
      }
      return groups;
    },
    episodeIcon(type) {
      return { encounter: 'calendar-days', result: 'flask-conical', medication: 'pill', condition: 'stethoscope', procedure: 'clipboard-check', allergy: 'shield-alert', immunization: 'syringe', care_plan: 'list-checks', document: 'file-text', other: 'activity' }[type] || 'activity';
    },
    episodeItemLabel(category, count) {
      const label = { visit: 'visit', condition: 'condition', medication: 'medication', result: 'result', procedure: 'procedure', allergy: 'allergy', immunization: 'immunization', care_plan: 'care plan', document: 'document', other: 'record' }[category] || category;
      return `${count} ${label}${count === 1 ? '' : 's'}`;
    },
    openEpisode(episode) { this.episodeDetail = { open: true, episode }; this.$nextTick(() => this.refreshIcons()); },
    closeEpisode() { this.episodeDetail = { open: false, episode: null }; },
    closeActiveDrawer() { if (this.episodeDetail.open) this.closeEpisode(); else this.closeDetail(); },
    askAboutEpisode(episode) {
      this.chatContextResourceIds = (episode.citations || []).map((citation) => citation.reference).filter(Boolean).slice(0, 20);
      this.draftQuestion = `Explain what happened and what changed during the ${episode.title} episode on ${this.formatDate(episode.date)}.`;
      this.closeEpisode();
      this.setView('chat');
    },
    openJourneySource(source) {
      if (source?.resource_type && source?.resource_id) {
        this.closeEpisode();
        this.openDetail(source.resource_type, source.resource_id, 'Source for patient journey');
      }
    },

    async sendQuestion() {
      const question = this.draftQuestion.trim();
      if (!question || this.chatBusy) return;
      this.messages.push({ id: crypto.randomUUID(), role: 'user', content: question, error: false, citations: [] });
      this.draftQuestion = '';
      this.chatBusy = true;
      this.$nextTick(() => this.scrollChat());
      try {
        const response = await this.apiFetch('/api/agent/ask', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question, patient_id: this.patientId, session_id: this.chatSessionId, episode_resource_ids: this.chatContextResourceIds }),
        });
        const data = await response.json();
        this.chatSessionId = data.session_id || this.chatSessionId;
        this.messages.push({
          id: crypto.randomUUID(),
          role: 'assistant',
          content: data.answer || 'No response from the agent.',
          error: false,
          citations: data.citations || [],
          grounded: Boolean(data.grounded),
          sourcesExpanded: false,
        });
      } catch (error) {
        if (error.message !== 'Authentication required') this.messages.push({ id: crypto.randomUUID(), role: 'assistant', content: error.message, error: true, citations: [] });
      } finally {
        this.chatContextResourceIds = [];
        this.chatBusy = false;
        this.$nextTick(() => { this.scrollChat(); this.refreshIcons(); this.$refs.questionInput?.focus(); });
      }
    },

    renderMessage(content) {
      const source = String(content || '');
      if (window.marked && window.DOMPurify) return window.DOMPurify.sanitize(window.marked.parse(source));
      return this.escapeHtml(source).replace(/\n/g, '<br>');
    },
    escapeHtml(value) { const node = document.createElement('div'); node.textContent = value; return node.innerHTML; },
    scrollChat() { if (this.$refs.chatMessages) this.$refs.chatMessages.scrollTop = this.$refs.chatMessages.scrollHeight; },

    visibleCitations(message) {
      const citations = message.citations || [];
      return message.sourcesExpanded ? citations : citations.slice(0, 5);
    },
    citationMeta(source) {
      const parts = [source.publisher];
      if (source.version) parts.push(`Version ${source.version}`);
      else if (source.date) parts.push(this.formatDate(source.date));
      else if (source.status) parts.push(source.status);
      else if (source.code) parts.push(`Code ${source.code}`);
      else if (source.severity && source.severity !== 'none') parts.push(`${source.severity} severity`);
      return parts.filter(Boolean).join(' | ');
    },
    citationActionable(source) {
      return Boolean(source?.url || (source?.type === 'patient_record' && source.resource_type && source.resource_id));
    },
    openCitation(source) {
      if (source?.type === 'patient_record' && source.resource_type && source.resource_id) {
        this.openDetail(source.resource_type, source.resource_id, 'Cited in answer');
        return;
      }
      if (source?.url) window.open(source.url, '_blank', 'noopener,noreferrer');
    },
    toggleSources(message) {
      message.sourcesExpanded = !message.sourcesExpanded;
      this.$nextTick(() => this.refreshIcons());
    },

    async openDetail(resourceType, resourceId, relationship = '', sourceType = '', resetHistory = true) {
      if (!resourceType || !resourceId) return;
      if (resetHistory) this.detail.history = [];
      this.detail.history.push({ resourceType, resourceId, relationship, sourceType, title: '' });
      this.detail.open = true;
      await this.loadDetail(resourceType, resourceId);
    },
    async openRelated(item) {
      if (!item.resource) return;
      await this.openDetail(item.resource.resourceType, item.resource.id, item.reference.path, this.detail.resourceType, false);
    },
    async loadDetail(resourceType, resourceId) {
      this.detail.loading = true;
      this.detail.error = '';
      this.detail.resourceType = resourceType;
      this.detail.resourceId = resourceId;
      this.detail.title = `${resourceType}/${resourceId}`;
      this.detail.tab = 'details';
      this.detail.resource = null;
      this.detail.related = [];
      const current = this.detail.history[this.detail.history.length - 1];
      this.detail.relationship = current?.relationship ? this.relationshipLabel(current.relationship) : '';
      try {
        const [resourceResponse, relatedResponse] = await Promise.all([
          this.apiFetch(`/api/fhir/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}`),
          this.apiFetch(`/api/fhir/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}/related`),
        ]);
        const resource = await resourceResponse.json();
        const relatedData = await relatedResponse.json();
        this.detail.resource = resource;
        this.detail.related = relatedData.related || [];
        this.detail.title = this.resourceTitle(resource, resourceType);
        if (current) current.title = this.detail.title;
      } catch (error) {
        this.detail.error = `Resource could not be loaded: ${error.message}`;
      } finally {
        this.detail.loading = false;
        this.$nextTick(() => this.refreshIcons());
      }
    },
    async detailBack() {
      if (this.detail.history.length < 2) return;
      this.detail.history.pop();
      const previous = this.detail.history[this.detail.history.length - 1];
      await this.loadDetail(previous.resourceType, previous.resourceId);
    },
    closeDetail() { this.detail.open = false; this.detail.history = []; this.detail.error = ''; },

    detailFields() {
      const r = this.detail.resource;
      if (!r) return [];
      const coding = (value) => value?.coding?.[0] ? `${value.coding[0].code || ''}${value.coding[0].system ? ` (${value.coding[0].system})` : ''}` : '';
      if (r.resourceType === 'Condition') return [
        { label: 'Condition', value: r.code?.text }, { label: 'Code', value: coding(r.code) }, { label: 'Clinical status', value: r.clinicalStatus?.coding?.[0]?.code || r.clinicalStatus }, { label: 'Onset date', value: this.formatDate(r.onsetDateTime) }, { label: 'Recorded date', value: this.formatDate(r.recordedDate) }, { label: 'Subject', value: r.subject?.reference },
      ];
      if (r.resourceType === 'MedicationRequest') return [
        { label: 'Medication', value: r.medicationCodeableConcept?.text }, { label: 'RxNorm code', value: coding(r.medicationCodeableConcept) }, { label: 'Status', value: r.status }, { label: 'Intent', value: r.intent }, { label: 'Authored on', value: this.formatDate(r.authoredOn) }, { label: 'Dosage', value: r.dosageInstruction?.[0]?.text }, { label: 'Subject', value: r.subject?.reference },
      ];
      if (r.resourceType === 'Observation') return [
        { label: 'Observation', value: r.code?.text }, { label: 'LOINC code', value: coding(r.code) }, { label: 'Status', value: r.status }, { label: 'Effective date', value: this.formatDate(r.effectiveDateTime) }, { label: 'Value', value: this.formatValue(r.valueQuantity?.value || r.valueString, r.valueQuantity?.unit) }, { label: 'Reference range', value: this.referenceRange(r) }, { label: 'Subject', value: r.subject?.reference },
      ];
      if (r.resourceType === 'Encounter') return [
        { label: 'Visit type', value: r.class?.display || r.class?.code }, { label: 'Status', value: r.status }, { label: 'Start', value: this.formatDate(r.period?.start) }, { label: 'End', value: this.formatDate(r.period?.end) }, { label: 'Reason', value: r.reasonCode?.[0]?.text }, { label: 'Participants', value: r.participant?.map((p) => p.individual?.reference).filter(Boolean).join(', ') }, { label: 'Service provider', value: r.serviceProvider?.reference },
      ];
      if (r.resourceType === 'Practitioner') {
        const name = r.name?.[0];
        return [{ label: 'Name', value: this.personName(name) }, { label: 'Qualification', value: r.qualification?.map((q) => q.code?.text).filter(Boolean).join(', ') }, { label: 'Contact', value: r.telecom?.map((t) => `${t.system}: ${t.value}`).join(', ') }];
      }
      if (r.resourceType === 'Organization') return [{ label: 'Name', value: r.name }, { label: 'Type', value: r.type?.map((t) => t.coding?.[0]?.display || t.coding?.[0]?.code).filter(Boolean).join(', ') }];
      return [{ label: 'Resource type', value: r.resourceType }, { label: 'ID', value: r.id }, { label: 'Status', value: r.status }, { label: 'Subject', value: r.subject?.reference }];
    },
    resourceTitle(resource, fallback) {
      if (!resource) return fallback;
      if (resource.resourceType === 'Condition') return resource.code?.text || fallback;
      if (resource.resourceType === 'MedicationRequest') return resource.medicationCodeableConcept?.text || fallback;
      if (resource.resourceType === 'Observation') return resource.code?.text || fallback;
      if (resource.resourceType === 'Encounter') return resource.class?.display || resource.class?.code || fallback;
      if (resource.resourceType === 'Practitioner') return this.personName(resource.name?.[0]) || fallback;
      if (resource.resourceType === 'Organization') return resource.name || fallback;
      return `${resource.resourceType || fallback}/${resource.id || ''}`;
    },
    relatedTitle(resource, reference) { return resource ? this.resourceTitle(resource, `${reference.type}/${reference.id}`) : `${reference.type}/${reference.id}`; },
    relatedSubtitle(resource, error) { if (error) return error; if (!resource) return ''; return resource.resourceType === 'Encounter' ? this.formatDate(resource.period?.start) : resource.resourceType; },
    relationshipLabel(path) {
      const key = String(path || '').toLowerCase().replace(/\[\d+\]/g, '').replace(/\.\w+$/, '');
      const labels = { subject: 'About patient', patient: 'About patient', encounter: 'From visit', requester: 'Prescribed by', recorder: 'Recorded by', performer: 'Performed by', 'participant.individual': 'Provider at visit', participant: 'Provider at visit', serviceprovider: 'Facility', location: 'Location', author: 'Author', asserter: 'Asserted by', context: 'Context' };
      return labels[key] || (path ? `Linked via ${path}` : 'Related resource');
    },
    personName(name) { return name ? `${name.prefix?.[0] ? `${name.prefix[0]} ` : ''}${name.given?.join(' ') || ''} ${name.family || ''}`.trim() : ''; },
    referenceRange(resource) { const range = resource.referenceRange?.[0]; return range ? `${range.low?.value ?? ''} - ${range.high?.value ?? ''} ${range.low?.unit || range.high?.unit || ''}`.trim() : ''; },
    formatJson(value) { return value ? JSON.stringify(value, null, 2) : ''; },
    formatDate(value) { if (!value) return ''; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); },
    formatValue(value, unit) { return value === undefined || value === null || value === '' ? 'Value unavailable' : `${value}${unit ? ` ${unit}` : ''}`; },
    titleCase(value) { return value ? String(value).replace(/\b\w/g, (letter) => letter.toUpperCase()) : ''; },
    refreshIcons() { requestAnimationFrame(() => window.lucide?.createIcons()); },
  };
}

(() => {
  'use strict';

  const STORAGE_KEY = 'flowstate-assistant-history';
  const MAX_MESSAGES = 80;
  const welcome = "Hello! I'm your Traffic AI Assistant. How can I help you manage traffic today?";
  const panel = document.getElementById('trafficAssistant');
  const launcher = document.getElementById('assistantLauncher');
  const closeButton = document.getElementById('assistantClose');
  const messages = document.getElementById('assistantMessages');
  const form = document.getElementById('assistantForm');
  const input = document.getElementById('assistantInput');
  const sendButton = document.getElementById('assistantSend');
  const typing = document.getElementById('assistantTyping');
  const actions = [...document.querySelectorAll('[data-assistant-action]')];
  let pending = false;
  let history = loadHistory();

  function loadHistory() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      if (Array.isArray(saved)) {
        const valid = saved.filter((item) =>
          item && (item.role === 'assistant' || item.role === 'user')
          && typeof item.text === 'string'
          && item.text.length <= 2000
          && typeof item.createdAt === 'string'
        );
        const normalized = valid.filter((item, index) => !(
          item.role === 'assistant'
          && item.text === welcome
          && index > 0
          && valid[index - 1].role === 'assistant'
          && valid[index - 1].text === welcome
        ));
        if (normalized.length) return normalized.slice(-MAX_MESSAGES);
      }
    } catch {
      // A blocked or malformed browser preference should not prevent chat.
    }
    return [{ role: 'assistant', text: welcome, createdAt: new Date().toISOString() }];
  }

  function saveHistory() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-MAX_MESSAGES)));
    } catch {
      // Keep the conversation usable if this browser disables local storage.
    }
  }

  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? ''
      : date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }

  function addMessage(role, text, createdAt = new Date().toISOString(), save = true) {
    const item = { role, text, createdAt };
    if (save) {
      history.push(item);
      history = history.slice(-MAX_MESSAGES);
    }

    const article = document.createElement('article');
    article.className = `assistant-message ${role === 'user' ? 'from-user' : 'from-assistant'}`;
    const bubble = document.createElement('p');
    bubble.className = 'assistant-bubble';
    bubble.textContent = text;
    const time = document.createElement('time');
    time.className = 'assistant-time';
    time.dateTime = createdAt;
    time.textContent = formatTime(createdAt);
    article.append(bubble, time);
    messages.append(article);
    messages.scrollTop = messages.scrollHeight;
    if (save) saveHistory();
  }

  function renderHistory() {
    for (const item of history) addMessage(item.role, item.text, item.createdAt, false);
  }

  function setPending(value) {
    pending = value;
    sendButton.disabled = value;
    input.disabled = value;
    for (const button of actions) button.disabled = value;
    typing.hidden = !value;
    if (value) messages.scrollTop = messages.scrollHeight;
  }

  function openPanel() {
    panel.classList.add('is-open');
    panel.setAttribute('aria-hidden', 'false');
    panel.inert = false;
    launcher.setAttribute('aria-expanded', 'true');
    input.focus();
  }

  function closePanel() {
    panel.classList.remove('is-open');
    panel.setAttribute('aria-hidden', 'true');
    panel.inert = true;
    launcher.setAttribute('aria-expanded', 'false');
    launcher.focus();
  }

  async function ask(message, action = null) {
    if (pending) return;
    const text = message.trim();
    if (!text || text.length > 1000) return;
    addMessage('user', text);
    input.value = '';
    setPending(true);

    try {
      const response = await fetch('/api/traffic-assistant', {
        method: 'POST',
        cache: 'no-store',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, ...(action ? { action } : {}) }),
      });
      let body;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      if (!response.ok) {
        const detail = typeof body?.detail === 'string'
          ? body.detail
          : 'I could not process that request. Please try again.';
        throw new Error(detail);
      }
      if (typeof body?.response !== 'string' || !body.response.trim()) {
        throw new Error('The assistant returned an invalid response. Please try again.');
      }
      addMessage('assistant', body.response);
    } catch (error) {
      addMessage('assistant', `I couldn't reach the traffic assistant: ${error.message}`);
    } finally {
      setPending(false);
      input.focus();
    }
  }

  renderHistory();
  launcher.addEventListener('click', () => {
    if (panel.classList.contains('is-open')) closePanel();
    else openPanel();
  });
  closeButton.addEventListener('click', closePanel);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && panel.classList.contains('is-open')) closePanel();
    if (event.key === 'Tab' && panel.classList.contains('is-open')) {
      const focusable = [...panel.querySelectorAll('button:not(:disabled), textarea:not(:disabled)')];
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    ask(input.value);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  actions.forEach((button) => button.addEventListener('click', () => {
    const label = button.textContent.trim();
    ask(label, button.dataset.assistantAction);
  }));
})();

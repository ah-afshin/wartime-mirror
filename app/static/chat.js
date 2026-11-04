(() => {
  "use strict";

  const app = document.getElementById("chat-app");
  if (!app) return;

  const apiRoot = app.dataset.conversationsUrl;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const elements = {
    sidebar: document.getElementById("chat-sidebar"),
    backdrop: document.getElementById("sidebar-backdrop"),
    sidebarOpen: document.getElementById("sidebar-open"),
    sidebarClose: document.getElementById("sidebar-close"),
    newChat: document.getElementById("new-chat"),
    sidebarStatus: document.getElementById("sidebar-status"),
    conversationList: document.getElementById("conversation-list"),
    title: document.getElementById("chat-title"),
    modelSelect: document.getElementById("model-select"),
    modelStatus: document.getElementById("model-status"),
    themeToggle: document.getElementById("theme-toggle"),
    error: document.getElementById("chat-error"),
    scroll: document.getElementById("conversation-scroll"),
    welcome: document.getElementById("welcome"),
    messages: document.getElementById("messages"),
    generationStatus: document.getElementById("generation-status"),
    composer: document.getElementById("composer"),
    input: document.getElementById("message-input"),
    send: document.getElementById("send-button"),
  };
  const state = {
    conversations: [],
    activeId: null,
    activeConversation: null,
    draftModel: "",
    messages: [],
    models: [],
    providers: [],
    modelState: "loading",
    sending: false,
    selection: 0,
  };

  function setError(message) {
    elements.error.textContent = message || "";
    elements.error.hidden = !message;
  }

  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body) headers.set("Content-Type", "application/json");
    if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", csrfToken);
    const response = await fetch(url, {
      ...options,
      headers,
      credentials: "same-origin",
    });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const error = data?.error;
      const message = typeof error === "string" ? error : error?.message;
      throw new Error(message || data?.message || `Request failed (${response.status})`);
    }
    return data;
  }

  function selectedModel() {
    return state.activeConversation?.model || state.draftModel;
  }

  function providerOf(model) {
    const separator = model.indexOf("/");
    return separator > 0 ? model.slice(0, separator) : "Other";
  }

  function renderModelSelector() {
    const select = elements.modelSelect;
    const savedValue = selectedModel();
    select.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = state.modelState === "loading" ? "Loading models…" : "Select a model…";
    select.append(placeholder);

    const groups = new Map();
    for (const model of state.models) {
      const provider = providerOf(model.id);
      if (!groups.has(provider)) {
        const group = document.createElement("optgroup");
        group.label = provider;
        groups.set(provider, group);
        select.append(group);
      }
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.id;
      groups.get(provider).append(option);
    }

    if (savedValue && !state.models.some((model) => model.id === savedValue)) {
      const provider = providerOf(savedValue);
      let group = groups.get(provider);
      if (!group) {
        group = document.createElement("optgroup");
        group.label = provider;
        groups.set(provider, group);
        select.append(group);
      }
      const option = document.createElement("option");
      option.value = savedValue;
      option.textContent = `${savedValue} (saved; not in current discovery)`;
      group.append(option);
    }
    select.value = savedValue || "";
    select.disabled = state.modelState === "loading" || state.sending;

    if (state.modelState === "loading") {
      elements.modelStatus.textContent = "Discovering available models…";
    } else if (state.modelState === "error") {
      elements.modelStatus.textContent = state.models.length
        ? "Some model discovery failed. Existing saved models are unchanged."
        : "Model discovery failed. Retry by reloading this page.";
    } else if (state.modelState === "partial") {
      const failures = state.providers
        .filter((provider) => provider.configured && provider.error)
        .map((provider) => `${provider.id} discovery failed (check ${provider.id.toUpperCase()}_API_KEY and provider access)`);
      const unconfigured = state.providers
        .filter((provider) => !provider.configured)
        .map((provider) => `${provider.id.toUpperCase()}_API_KEY`);
      const details = [];
      if (failures.length) details.push(`${failures.join(", ")}.`);
      if (unconfigured.length) details.push(`Set ${unconfigured.join(", ")} on the server to discover those providers.`);
      elements.modelStatus.textContent = details.join(" ") || "Some providers could not return models.";
    } else if (!state.models.length) {
      const unconfigured = state.providers
        .filter((provider) => !provider.configured)
        .map((provider) => `${provider.id.toUpperCase()}_API_KEY`);
      if (unconfigured.length) {
        elements.modelStatus.textContent = `No models are available. Configure provider keys on the server, such as ${unconfigured.slice(0, 3).join(", ")}.`;
      } else {
        elements.modelStatus.textContent = savedValue
          ? "No models were returned. The conversation’s saved model is unchanged."
          : "No models are currently available.";
      }
    } else if (state.providers.some((provider) => !provider.configured)) {
      const unconfigured = state.providers
        .filter((provider) => !provider.configured)
        .map((provider) => `${provider.id.toUpperCase()}_API_KEY`);
      elements.modelStatus.textContent = `Only configured providers are listed. Set ${unconfigured.join(", ")} on the server to discover more models.`;
    } else {
      elements.modelStatus.textContent = "";
    }
    renderComposerState();
  }

  function renderComposerState() {
    const hasModel = Boolean(selectedModel());
    elements.input.disabled = state.sending;
    elements.send.disabled = state.sending || !hasModel || !elements.input.value.trim();
    elements.send.setAttribute("aria-label", state.sending ? "Waiting for response" : "Send message");
    elements.generationStatus.hidden = !state.sending;
  }

  function formatTimestamp(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  }

  function safeLinkTarget(value) {
    const target = value.trim();
    if (!target || /[\u0000-\u001f\u007f\\]/.test(target) || target.startsWith("//")) return null;
    if (target.startsWith("#") || target.startsWith("/")) return target;
    try {
      const url = new URL(target);
      return ["http:", "https:", "mailto:"].includes(url.protocol) ? target : null;
    } catch {
      return null;
    }
  }

  function appendInline(parent, text) {
    let remaining = text;
    while (remaining) {
      let match = remaining.match(/^\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/);
      if (match) {
        const target = safeLinkTarget(match[2]);
        if (target) {
          const link = document.createElement("a");
          link.href = target;
          link.rel = "noopener noreferrer";
          appendInline(link, match[1]);
          parent.append(link);
        } else {
          parent.append(document.createTextNode(match[0]));
        }
        remaining = remaining.slice(match[0].length);
        continue;
      }
      match = remaining.match(/^(`+)([\s\S]*?)\1/);
      if (match) {
        const code = document.createElement("code");
        code.textContent = match[2].replace(/\n/g, " ");
        parent.append(code);
        remaining = remaining.slice(match[0].length);
        continue;
      }
      match = remaining.match(/^(\*\*|__)(.+?)\1/);
      if (match) {
        const strong = document.createElement("strong");
        appendInline(strong, match[2]);
        parent.append(strong);
        remaining = remaining.slice(match[0].length);
        continue;
      }
      match = remaining.match(/^(\*|_)(\S(?:.*?\S)?)\1/);
      if (match) {
        const emphasis = document.createElement("em");
        appendInline(emphasis, match[2]);
        parent.append(emphasis);
        remaining = remaining.slice(match[0].length);
        continue;
      }

      const next = remaining.slice(1).search(/[\[`*_]/);
      const length = next < 0 ? remaining.length : next + 1;
      parent.append(document.createTextNode(remaining.slice(0, length)));
      remaining = remaining.slice(length);
    }
  }

  function splitTableRow(line) {
    let value = line.trim();
    if (value.startsWith("|")) value = value.slice(1);
    if (value.endsWith("|") && !value.endsWith("\\|")) value = value.slice(0, -1);
    const cells = [];
    let cell = "";
    let escaped = false;
    for (const character of value) {
      if (character === "|" && !escaped) {
        cells.push(cell.trim());
        cell = "";
      } else {
        cell += character;
      }
      if (character === "\\" && !escaped) escaped = true;
      else escaped = false;
    }
    cells.push(cell.trim());
    return cells;
  }

  function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  }

  function isBlockStart(lines, index) {
    const line = lines[index];
    return /^\s{0,3}(?:#{1,6}\s|```|~~~|>\s?|[-*+]\s|\d+\.\s)/.test(line)
      || /^\s{0,3}(?:---+|\*\*\*+|___+)\s*$/.test(line)
      || (index + 1 < lines.length && line.includes("|") && isTableSeparator(lines[index + 1]));
  }

  function appendCodeBlock(parent, source, language) {
    const wrapper = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = source;
    if (/^[A-Za-z0-9_-]{1,30}$/.test(language)) code.className = `language-${language}`;
    wrapper.append(code);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "code-copy";
    copy.textContent = "Copy code";
    copy.addEventListener("click", () => copyText(source, copy));
    wrapper.append(copy);
    parent.append(wrapper);
  }

  function appendMarkdown(parent, source) {
    const lines = source.split(/\r?\n/);
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }

      const fence = line.match(/^\s{0,3}(`{3,}|~{3,})(.*)$/);
      if (fence) {
        const marker = fence[1][0];
        const minimum = fence[1].length;
        const language = fence[2].trim().split(/\s+/, 1)[0] || "";
        const content = [];
        index += 1;
        while (index < lines.length && !new RegExp(`^\\s{0,3}${marker}{${minimum},}\\s*$`).test(lines[index])) {
          content.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        appendCodeBlock(parent, content.join("\n"), language);
        continue;
      }

      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) {
        const element = document.createElement(`h${heading[1].length}`);
        appendInline(element, heading[2]);
        parent.append(element);
        index += 1;
        continue;
      }

      if (/^\s{0,3}(?:---+|\*\*\*+|___+)\s*$/.test(line)) {
        parent.append(document.createElement("hr"));
        index += 1;
        continue;
      }

      if (/^\s{0,3}>\s?/.test(line)) {
        const quoteLines = [];
        while (index < lines.length && /^\s{0,3}>\s?/.test(lines[index])) {
          quoteLines.push(lines[index].replace(/^\s{0,3}>\s?/, ""));
          index += 1;
        }
        const quote = document.createElement("blockquote");
        appendMarkdown(quote, quoteLines.join("\n"));
        parent.append(quote);
        continue;
      }

      if (index + 1 < lines.length && line.includes("|") && isTableSeparator(lines[index + 1])) {
        const headers = splitTableRow(line);
        index += 2;
        const rows = [];
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        const wrapper = document.createElement("div");
        wrapper.className = "table-wrap";
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const headerRow = document.createElement("tr");
        for (const value of headers) {
          const cell = document.createElement("th");
          appendInline(cell, value);
          headerRow.append(cell);
        }
        thead.append(headerRow);
        table.append(thead);
        const tbody = document.createElement("tbody");
        for (const row of rows) {
          const tr = document.createElement("tr");
          headers.forEach((_, cellIndex) => {
            const cell = document.createElement("td");
            appendInline(cell, row[cellIndex] || "");
            tr.append(cell);
          });
          tbody.append(tr);
        }
        table.append(tbody);
        wrapper.append(table);
        parent.append(wrapper);
        continue;
      }

      const listMatch = line.match(/^\s{0,3}([-*+]|\d+\.)\s+(.*)$/);
      if (listMatch) {
        const ordered = /^\d/.test(listMatch[1]);
        const list = document.createElement(ordered ? "ol" : "ul");
        while (index < lines.length) {
          const item = lines[index].match(/^\s{0,3}([-*+]|\d+\.)\s+(.*)$/);
          if (!item || /^\d/.test(item[1]) !== ordered) break;
          const li = document.createElement("li");
          appendInline(li, item[2]);
          list.append(li);
          index += 1;
        }
        parent.append(list);
        continue;
      }

      const paragraph = [];
      while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
        paragraph.push(lines[index]);
        index += 1;
      }
      if (!paragraph.length) {
        paragraph.push(lines[index]);
        index += 1;
      }
      const element = document.createElement("p");
      appendInline(element, paragraph.join("\n"));
      parent.append(element);
    }
  }

  async function copyText(value, button) {
    try {
      await navigator.clipboard.writeText(value);
      const original = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(() => { button.textContent = original; }, 1200);
    } catch {
      setError("Could not copy to the clipboard. Check browser clipboard permissions.");
    }
  }

  function renderMessages(forceScroll = false) {
    const shouldScroll = forceScroll || elements.scroll.scrollHeight - elements.scroll.scrollTop
      - elements.scroll.clientHeight < 120;
    elements.messages.replaceChildren();
    const hasMessages = state.messages.length > 0;
    elements.welcome.hidden = hasMessages;
    for (const message of state.messages) {
      const article = document.createElement("article");
      article.className = `message message-${message.role}`;
      const avatar = document.createElement("div");
      avatar.className = "message-avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = message.role === "assistant" ? "M" : "You";
      const main = document.createElement("div");
      main.className = "message-main";
      const heading = document.createElement("div");
      heading.className = "message-heading";
      const author = document.createElement("span");
      author.className = "message-author";
      author.textContent = message.role === "assistant" ? "Mirror" : "You";
      heading.append(author);
      const time = document.createElement("time");
      time.className = "message-time";
      time.dateTime = message.created_at || "";
      time.textContent = formatTimestamp(message.created_at);
      heading.append(time);
      if (message.role === "assistant") {
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "message-copy";
        copy.textContent = "Copy";
        copy.addEventListener("click", () => copyText(message.content || "", copy));
        heading.append(copy);
      }
      const body = document.createElement("div");
      body.className = "message-body";
      appendMarkdown(body, message.content || "");
      main.append(heading, body);
      article.append(avatar, main);
      elements.messages.append(article);
    }
    if (shouldScroll) {
      requestAnimationFrame(() => { elements.scroll.scrollTop = elements.scroll.scrollHeight; });
    }
  }

  function renderTitle() {
    elements.title.textContent = state.activeConversation?.title || "New chat";
  }

  function setActiveConversationUrl(id) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("conversation", id);
    else url.searchParams.delete("conversation");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }

  function renderConversations() {
    const sorted = [...state.conversations].sort((a, b) =>
      new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    elements.conversationList.replaceChildren();
    for (const conversation of sorted) {
      const row = document.createElement("li");
      row.className = `conversation-row${conversation.id === state.activeId ? " active" : ""}`;
      const select = document.createElement("button");
      select.type = "button";
      select.className = "conversation-select";
      select.textContent = conversation.title;
      select.title = conversation.title;
      select.setAttribute("aria-current", conversation.id === state.activeId ? "page" : "false");
      select.addEventListener("click", () => activateConversation(conversation.id));
      const actions = document.createElement("div");
      actions.className = "conversation-actions";
      const rename = document.createElement("button");
      rename.type = "button";
      rename.textContent = "Rename";
      rename.title = `Rename ${conversation.title}`;
      rename.setAttribute("aria-label", `Rename ${conversation.title}`);
      rename.addEventListener("click", () => renameConversation(conversation));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Delete";
      remove.title = `Delete ${conversation.title}`;
      remove.setAttribute("aria-label", `Delete ${conversation.title}`);
      remove.addEventListener("click", () => deleteConversation(conversation));
      actions.append(rename, remove);
      row.append(select, actions);
      elements.conversationList.append(row);
    }
    if (!state.conversations.length && elements.sidebarStatus.textContent === "") {
      elements.sidebarStatus.textContent = "No conversations yet.";
    }
  }

  async function loadConversations() {
    elements.sidebarStatus.textContent = "Loading conversations…";
    try {
      const data = await request(apiRoot);
      state.conversations = Array.isArray(data?.conversations) ? data.conversations : [];
      state.conversations.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
      elements.sidebarStatus.textContent = state.conversations.length ? "" : "No conversations yet.";
      renderConversations();
      const requestedId = new URLSearchParams(window.location.search).get("conversation");
      if (!state.activeId && requestedId) {
        if (state.conversations.some((conversation) => conversation.id === requestedId)) {
          await activateConversation(requestedId);
        } else {
          setActiveConversationUrl(null);
        }
      }
    } catch (error) {
      elements.sidebarStatus.textContent = `Could not load conversations: ${error.message}`;
    }
  }

  async function loadModels() {
    try {
      const data = await request(`${app.dataset.modelsUrl}`);
      if (!Array.isArray(data?.data)) throw new Error("The model list response was invalid.");
      const rows = Array.isArray(data?.data) ? data.data : [];
      state.models = rows.filter((item) => item && typeof item.id === "string" && item.id)
        .map((item) => ({ id: item.id }))
        .sort((a, b) => a.id.localeCompare(b.id));
      const providers = Array.isArray(data?.providers) ? data.providers : [];
      state.providers = providers.filter((provider) => provider && typeof provider.id === "string");
      state.modelState = state.providers.some((provider) => provider.error) ? "partial" : "ready";
      renderModelSelector();
    } catch (error) {
      state.modelState = "error";
      state.models = [];
      state.providers = [];
      renderModelSelector();
      elements.modelStatus.textContent = `Could not discover models: ${error.message}`;
    }
  }

  async function loadConversation(id, forceScroll = false) {
    const selection = state.selection;
    const data = await request(`${apiRoot}/${encodeURIComponent(id)}`);
    if (selection !== state.selection || id !== state.activeId) return;
    state.activeConversation = data.conversation;
    state.messages = Array.isArray(data.conversation.messages) ? data.conversation.messages : [];
    renderTitle();
    renderModelSelector();
    renderConversations();
    renderMessages(forceScroll);
  }

  async function activateConversation(id) {
    if (id === state.activeId && state.activeConversation) return;
    state.selection += 1;
    state.activeId = id;
    state.activeConversation = null;
    state.draftModel = "";
    state.messages = [];
    setActiveConversationUrl(id);
    setError("");
    renderTitle();
    renderConversations();
    renderModelSelector();
    renderMessages();
    closeSidebar();
    elements.messages.textContent = "Loading conversation…";
    try {
      await loadConversation(id, true);
    } catch (error) {
      elements.messages.replaceChildren();
      setError(`Could not load this conversation: ${error.message}`);
    }
  }

  function startNewChat() {
    state.selection += 1;
    state.activeId = null;
    state.activeConversation = null;
    state.draftModel = "";
    state.messages = [];
    setActiveConversationUrl(null);
    setError("");
    renderTitle();
    renderConversations();
    renderModelSelector();
    renderMessages(true);
    elements.input.focus();
    closeSidebar();
  }

  async function changeModel(model) {
    if (!model || state.sending) {
      renderModelSelector();
      return;
    }
    if (!state.activeId) {
      state.draftModel = model;
      renderModelSelector();
      renderComposerState();
      return;
    }
    const originalModel = selectedModel();
    if (model === originalModel) return;
    elements.modelSelect.disabled = true;
    setError("");
    try {
      const data = await request(`${apiRoot}/${encodeURIComponent(state.activeId)}`, {
        method: "PATCH",
        body: JSON.stringify({ model }),
      });
      if (data?.conversation?.id === state.activeId) {
        state.activeConversation = data.conversation;
        renderModelSelector();
        await loadConversations();
      }
    } catch (error) {
      setError(`Could not change the conversation model: ${error.message}`);
      renderModelSelector();
    }
  }

  async function renameConversation(conversation) {
    const title = window.prompt("Rename conversation", conversation.title);
    if (title === null || !title.trim() || title.trim() === conversation.title) return;
    try {
      const data = await request(`${apiRoot}/${encodeURIComponent(conversation.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ title: title.trim() }),
      });
      state.conversations = state.conversations.map((item) =>
        item.id === conversation.id ? data.conversation : item);
      if (state.activeId === conversation.id) {
        state.activeConversation = data.conversation;
        renderTitle();
      }
      elements.sidebarStatus.textContent = "";
      renderConversations();
      await loadConversations();
    } catch (error) {
      setError(`Could not rename this conversation: ${error.message}`);
    }
  }

  async function deleteConversation(conversation) {
    if (!window.confirm(`Delete “${conversation.title}”? This cannot be undone.`)) return;
    try {
      await request(`${apiRoot}/${encodeURIComponent(conversation.id)}`, { method: "DELETE" });
      state.conversations = state.conversations.filter((item) => item.id !== conversation.id);
      if (state.activeId === conversation.id) startNewChat();
      elements.sidebarStatus.textContent = state.conversations.length ? "" : "No conversations yet.";
      renderConversations();
      await loadConversations();
    } catch (error) {
      setError(`Could not delete this conversation: ${error.message}`);
    }
  }

  async function submitMessage(event) {
    event.preventDefault();
    const content = elements.input.value;
    if (!content.trim() || state.sending) return;
    if (!selectedModel()) {
      setError("Choose an available model before sending a message.");
      return;
    }
    setError("");
    state.sending = true;
    const selection = state.selection;
    let conversationId = state.activeId;
    renderComposerState();

    try {
      if (!conversationId) {
        const created = await request(apiRoot, {
          method: "POST",
          body: JSON.stringify({ model: state.draftModel }),
        });
        conversationId = created.conversation.id;
        if (selection === state.selection) {
          state.activeId = conversationId;
          state.activeConversation = created.conversation;
          state.draftModel = "";
          state.messages = [];
          renderTitle();
          renderConversations();
          renderModelSelector();
        }
      }

      const optimistic = {
        id: `pending-${Date.now()}`,
        role: "user",
        content,
        created_at: new Date().toISOString(),
      };
      if (selection === state.selection && conversationId === state.activeId) {
        state.messages.push(optimistic);
        renderMessages(true);
      }
      await loadConversations();
      await request(`${apiRoot}/${encodeURIComponent(conversationId)}/messages`, {
        method: "POST",
        body: JSON.stringify({ content }),
      });
      elements.input.value = "";
      resizeInput();
      if (selection === state.selection && conversationId === state.activeId) {
        await loadConversation(conversationId);
      }
      await loadConversations();
    } catch (error) {
      setError(`Message was not completed: ${error.message}. Your text is still available in the composer.`);
      if (conversationId && selection === state.selection && conversationId === state.activeId) {
        try {
          await loadConversation(conversationId);
        } catch {
          renderMessages();
        }
      }
      await loadConversations();
    } finally {
      state.sending = false;
      renderModelSelector();
      renderComposerState();
      elements.input.focus();
    }
  }

  function resizeInput() {
    elements.input.style.height = "auto";
    elements.input.style.height = `${Math.min(elements.input.scrollHeight, 200)}px`;
    renderComposerState();
  }

  function setSidebarOpen(open) {
    elements.sidebar.classList.toggle("open", open);
    elements.backdrop.classList.toggle("visible", open);
    elements.backdrop.hidden = !open;
    elements.sidebarOpen.setAttribute("aria-expanded", String(open));
  }

  function closeSidebar() {
    setSidebarOpen(false);
  }

  function initializeTheme() {
    const saved = window.localStorage.getItem("mirror-chat-theme");
    const theme = saved === "light" || saved === "dark"
      ? saved
      : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
    elements.themeToggle.textContent = theme === "dark" ? "Light theme" : "Dark theme";
    elements.themeToggle.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`);
  }

  elements.newChat.addEventListener("click", startNewChat);
  elements.sidebarOpen.addEventListener("click", () => setSidebarOpen(true));
  elements.sidebarClose.addEventListener("click", closeSidebar);
  elements.backdrop.addEventListener("click", closeSidebar);
  elements.modelSelect.addEventListener("change", (event) => changeModel(event.target.value));
  elements.composer.addEventListener("submit", submitMessage);
  elements.input.addEventListener("input", resizeInput);
  elements.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      elements.composer.requestSubmit();
    }
  });
  elements.themeToggle.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("mirror-chat-theme", theme);
    initializeTheme();
  });

  initializeTheme();
  renderComposerState();
  Promise.all([loadConversations(), loadModels()]);
})();

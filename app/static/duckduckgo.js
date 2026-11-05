(() => {
  "use strict";

  const app = document.getElementById("duckduckgo");
  if (!app) return;

  const form = document.getElementById("duckduckgo-search-form");
  const query = document.getElementById("duckduckgo-query");
  const region = document.getElementById("duckduckgo-region");
  const submit = document.getElementById("duckduckgo-search-submit");
  const status = document.getElementById("duckduckgo-status");
  const error = document.getElementById("duckduckgo-error");
  const results = document.getElementById("duckduckgo-results");
  const heading = document.getElementById("duckduckgo-results-heading");
  const list = document.getElementById("duckduckgo-result-list");

  function showError(message) {
    error.textContent = message || "";
    error.hidden = !message;
  }

  function validResultUrl(value) {
    try {
      const url = new URL(value);
      return (url.protocol === "https:" || url.protocol === "http:")
        && !url.username && !url.password;
    } catch {
      return false;
    }
  }

  function renderResults(data) {
    list.replaceChildren();
    heading.textContent = `Results (${data.count})`;
    if (!data.results.length) {
      const empty = document.createElement("li");
      empty.className = "duckduckgo-result";
      empty.textContent = "No results found.";
      list.append(empty);
      results.hidden = false;
      return;
    }

    for (const result of data.results) {
      if (!result || typeof result.title !== "string" || typeof result.url !== "string"
          || !validResultUrl(result.url)) continue;
      const item = document.createElement("li");
      item.className = "duckduckgo-result";

      const link = document.createElement("a");
      link.href = result.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = result.title;
      item.append(link);

      const destination = document.createElement("div");
      destination.className = "duckduckgo-result-url";
      destination.textContent = result.url;
      item.append(destination);

      const snippet = document.createElement("p");
      snippet.textContent = typeof result.snippet === "string" ? result.snippet : "";
      item.append(snippet);

      const source = document.createElement("div");
      source.className = "duckduckgo-result-source";
      source.textContent = typeof result.source === "string" ? result.source : data.source;
      item.append(source);

      list.append(item);
    }
    results.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("");
    results.hidden = true;
    list.replaceChildren();
    status.textContent = "Searching DuckDuckGo…";
    submit.disabled = true;

    const formData = new FormData(form);
    const params = new URLSearchParams({
      q: String(formData.get("q") || ""),
      limit: String(formData.get("limit") || "5"),
      region: region.value,
      safesearch: String(formData.get("safesearch") || "moderate"),
    });
    try {
      const response = await fetch(`${app.dataset.searchUrl}?${params}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(
          typeof data?.error === "string" ? data.error : `Search failed (${response.status})`,
        );
      }
      if (!data || !Array.isArray(data.results) || typeof data.count !== "number") {
        throw new Error("DuckDuckGo returned an invalid response.");
      }
      renderResults(data);
      status.textContent = data.count === 1 ? "1 result" : `${data.count} results`;
    } catch (requestError) {
      showError(requestError instanceof Error ? requestError.message : "Search failed.");
      status.textContent = "";
    } finally {
      submit.disabled = false;
    }
  });

  query.focus();
})();

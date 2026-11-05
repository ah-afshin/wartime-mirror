(() => {
  "use strict";

  const app = document.getElementById("google-search");
  if (!app) return;

  const form = document.getElementById("google-search-form");
  const query = document.getElementById("google-search-query");
  const submit = document.getElementById("google-search-submit");
  const status = document.getElementById("google-search-status");
  const error = document.getElementById("google-search-error");
  const results = document.getElementById("google-search-results");
  const heading = document.getElementById("google-search-results-heading");
  const list = document.getElementById("google-search-result-list");

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
      empty.className = "google-search-result";
      empty.textContent = "No results found.";
      list.append(empty);
      results.hidden = false;
      return;
    }

    for (const result of data.results) {
      if (!validResultUrl(result.url)) continue;
      const item = document.createElement("li");
      item.className = "google-search-result";

      const link = document.createElement("a");
      link.href = result.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = result.title;
      item.append(link);

      const destination = document.createElement("div");
      destination.className = "google-search-result-url";
      destination.textContent = result.url;
      item.append(destination);

      const snippet = document.createElement("p");
      snippet.textContent = result.snippet;
      item.append(snippet);

      list.append(item);
    }
    results.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("");
    results.hidden = true;
    list.replaceChildren();
    status.textContent = "Searching Google…";
    submit.disabled = true;

    const params = new URLSearchParams(new FormData(form));
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
        throw new Error("Google Search returned an invalid response.");
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

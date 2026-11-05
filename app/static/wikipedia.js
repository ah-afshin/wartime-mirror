(() => {
  "use strict";

  const app = document.getElementById("wikipedia");
  if (!app) return;

  const form = document.getElementById("wikipedia-search-form");
  const query = document.getElementById("wikipedia-query");
  const language = document.getElementById("wikipedia-language");
  const submit = document.getElementById("wikipedia-search-submit");
  const status = document.getElementById("wikipedia-search-status");
  const error = document.getElementById("wikipedia-search-error");
  const results = document.getElementById("wikipedia-search-results");
  const resultsHeading = document.getElementById("wikipedia-results-heading");
  const resultList = document.getElementById("wikipedia-result-list");
  const article = document.getElementById("wikipedia-article");
  const pageStatus = document.getElementById("wikipedia-page-status");
  const pageError = document.getElementById("wikipedia-page-error");
  const pageTitle = document.getElementById("wikipedia-page-title");
  const pageMetadata = document.getElementById("wikipedia-page-metadata");
  const canonicalLink = document.getElementById("wikipedia-canonical-link");
  const extract = document.getElementById("wikipedia-extract");

  function showError(element, message) {
    element.textContent = message || "";
    element.hidden = !message;
  }

  async function getJson(url) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(
        typeof data?.error === "string" ? data.error : `Request failed (${response.status})`,
      );
    }
    if (!data || typeof data !== "object") {
      throw new Error("Wikipedia returned an invalid response.");
    }
    return data;
  }

  function validWikipediaUrl(value, lang) {
    try {
      const url = new URL(value);
      return url.protocol === "https:"
        && url.hostname === `${lang}.wikipedia.org`
        && url.pathname.startsWith("/wiki/");
    } catch {
      return false;
    }
  }

  async function loadArticle(title, lang) {
    article.hidden = false;
    article.dir = lang === "fa" ? "rtl" : "ltr";
    pageTitle.textContent = "";
    pageMetadata.textContent = "";
    extract.textContent = "";
    canonicalLink.hidden = true;
    showError(pageError, "");
    pageStatus.textContent = "Loading article…";

    const params = new URLSearchParams({ title, lang });
    try {
      const data = await getJson(`${app.dataset.pageUrl}?${params}`);
      const page = data.page;
      if (!page || typeof page.title !== "string" || typeof page.extract !== "string"
          || typeof page.url !== "string" || !validWikipediaUrl(page.url, lang)) {
        throw new Error("Wikipedia returned an invalid article.");
      }

      pageTitle.textContent = page.title;
      extract.textContent = page.extract || "This article has no text extract.";
      const details = [];
      if (typeof page.last_modified === "string") {
        details.push(`Updated ${page.last_modified}`);
      }
      if (typeof page.size === "number") details.push(`${page.size.toLocaleString()} bytes`);
      if (typeof page.content_model === "string") details.push(page.content_model);
      pageMetadata.textContent = details.join(" · ");
      canonicalLink.href = page.url;
      canonicalLink.hidden = false;
      pageStatus.textContent = "";
    } catch (requestError) {
      showError(
        pageError,
        requestError instanceof Error ? requestError.message : "Could not load article.",
      );
      pageStatus.textContent = "";
    }
  }

  function renderSearchResults(data, lang) {
    resultList.replaceChildren();
    resultsHeading.textContent = `Articles (${data.count})`;
    if (!data.results.length) {
      const empty = document.createElement("li");
      empty.className = "wikipedia-result";
      empty.textContent = "No articles found.";
      resultList.append(empty);
      results.hidden = false;
      return;
    }

    for (const result of data.results) {
      if (!result || typeof result.title !== "string" || typeof result.url !== "string"
          || !validWikipediaUrl(result.url, lang)) continue;
      const item = document.createElement("li");
      item.className = "wikipedia-result";

      const select = document.createElement("button");
      select.type = "button";
      select.textContent = result.title;
      select.addEventListener("click", () => loadArticle(result.title, lang));
      item.append(select);

      const snippet = document.createElement("p");
      snippet.textContent = typeof result.snippet === "string" ? result.snippet : "";
      item.append(snippet);

      const metadata = [];
      if (typeof result.wordcount === "number") {
        metadata.push(`${result.wordcount.toLocaleString()} words`);
      }
      if (typeof result.timestamp === "string") metadata.push(result.timestamp);
      if (metadata.length) {
        const details = document.createElement("div");
        details.className = "wikipedia-result-meta";
        details.textContent = metadata.join(" · ");
        item.append(details);
      }
      resultList.append(item);
    }
    results.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError(error, "");
    showError(pageError, "");
    article.hidden = true;
    results.hidden = true;
    resultList.replaceChildren();
    status.textContent = "Searching Wikipedia…";
    submit.disabled = true;

    const params = new URLSearchParams(new FormData(form));
    const lang = language.value;
    try {
      const data = await getJson(`${app.dataset.searchUrl}?${params}`);
      if (!Array.isArray(data.results) || typeof data.count !== "number") {
        throw new Error("Wikipedia returned an invalid search response.");
      }
      renderSearchResults(data, lang);
      status.textContent = data.count === 1 ? "1 article found." : `${data.count} articles found.`;
    } catch (requestError) {
      showError(error, requestError instanceof Error ? requestError.message : "Search failed.");
      status.textContent = "";
    } finally {
      submit.disabled = false;
    }
  });

  query.focus();
})();

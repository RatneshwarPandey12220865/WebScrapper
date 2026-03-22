const siteListNode = document.getElementById("siteList");
const selectedSummaryNode = document.getElementById("selectedSummary");
const catalogSummaryNode = document.getElementById("catalogSummary");
const crawlButton = document.getElementById("crawlButton");
const useCacheToggle = document.getElementById("useCacheToggle");
const siteSearchInput = document.getElementById("siteSearchInput");
const selectSupportedButton = document.getElementById("selectSupportedButton");
const clearSelectionButton = document.getElementById("clearSelectionButton");

const keywordSearch = document.getElementById("keywordSearch");
const websiteFilter = document.getElementById("websiteFilter");
const categoryFilter = document.getElementById("categoryFilter");
const dateFromFilter = document.getElementById("dateFromFilter");
const dateToFilter = document.getElementById("dateToFilter");

const metricsNode = document.getElementById("metrics");
const statusNode = document.getElementById("statusText");
const statusListNode = document.getElementById("statusList");
const resultSummaryNode = document.getElementById("resultSummary");
const resultsBodyNode = document.getElementById("resultsBody");
const emptyStateNode = document.getElementById("emptyState");
const activeFilterChipsNode = document.getElementById("activeFilterChips");

const exportJsonButton = document.getElementById("exportJsonButton");
const exportCsvButton = document.getElementById("exportCsvButton");
const exportExcelButton = document.getElementById("exportExcelButton");

let siteCatalog = [];
let crawlResults = [];
let siteStatuses = [];
const selectedSites = new Set();

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return response.json();
}

function formatDate(value) {
  if (!value) {
    return "Not available";
  }
  return new Date(value).toLocaleString();
}

function normalize(text) {
  return (text || "").toLowerCase().trim();
}

function catalogByKey() {
  return Object.fromEntries(siteCatalog.map((site) => [site.site_key, site]));
}

function selectedSiteArray() {
  return [...selectedSites];
}

function supportedSites() {
  return siteCatalog.filter((site) => site.supported);
}

function renderCatalogSummary() {
  const supported = supportedSites().length;
  catalogSummaryNode.textContent = `${supported} supported of ${siteCatalog.length} known sites from the register.`;
}

function renderSelectionSummary() {
  selectedSummaryNode.textContent = `${selectedSites.size} sites selected`;
}

function renderSiteList() {
  const query = normalize(siteSearchInput.value);
  const filtered = siteCatalog.filter((site) => {
    const haystack = `${site.name} ${site.site_key} ${site.crawl_url || ""}`;
    return normalize(haystack).includes(query);
  });

  siteListNode.innerHTML = "";
  filtered.forEach((site) => {
    const card = document.createElement("label");
    card.className = `site-card${site.supported ? "" : " site-card--disabled"}`;

    const checked = selectedSites.has(site.site_key) ? "checked" : "";
    const disabled = site.supported ? "" : "disabled";
    const statusLabel = site.supported ? "Supported" : "Planned";

    card.innerHTML = `
      <input type="checkbox" data-site-key="${site.site_key}" ${checked} ${disabled}>
      <div class="site-card__body">
        <div class="site-card__top">
          <strong>${site.name}</strong>
          <span class="badge">${statusLabel}</span>
        </div>
        <p>${site.crawl_url || site.preferred_url || "No crawl URL available"}</p>
        <div class="site-card__meta">
          <span>${site.parser || "No parser yet"}</span>
          <span>${site.status.replace("_", " ")}</span>
        </div>
      </div>
    `;

    siteListNode.appendChild(card);
  });

  siteListNode.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
    checkbox.addEventListener("change", (event) => {
      const siteKey = event.target.dataset.siteKey;
      if (event.target.checked) {
        selectedSites.add(siteKey);
      } else {
        selectedSites.delete(siteKey);
      }
      renderSelectionSummary();
    });
  });
}

function renderMetrics() {
  const total = crawlResults.length;
  const pdfs = crawlResults.filter((item) => item.pdf_url).length;
  const newItems = crawlResults.filter((item) => item.is_new).length;
  const sites = new Set(crawlResults.map((item) => item.site_key)).size;

  const cards = [
    { label: "Selected sites", value: selectedSites.size },
    { label: "Crawled sites", value: sites },
    { label: "Returned items", value: total },
    { label: "New links", value: newItems },
    { label: "PDF items", value: pdfs },
    { label: "Failures", value: siteStatuses.filter((status) => status.state === "error").length }
  ];

  metricsNode.innerHTML = cards
    .map(
      (card) => `
        <article class="metric-card">
          <span>${card.label}</span>
          <strong>${card.value}</strong>
        </article>
      `
    )
    .join("");
}

function renderStatuses() {
  if (!siteStatuses.length) {
    statusListNode.innerHTML = `<div class="status-item"><strong>Idle</strong><span>No crawl has been started yet.</span></div>`;
    return;
  }

  statusListNode.innerHTML = "";
  siteStatuses.forEach((status) => {
    const item = document.createElement("article");
    item.className = "status-item";
    item.dataset.state = status.state;
    item.innerHTML = `
      <div>
        <strong>${status.site_name}</strong>
        <span>${status.message}</span>
      </div>
      <div class="status-meta">
        <span>${status.state}</span>
        <span>${status.item_count || 0} items</span>
        <span>${status.new_count || 0} new</span>
      </div>
    `;
    statusListNode.appendChild(item);
  });
}

function syncWebsiteFilter() {
  const current = websiteFilter.value;
  const names = [...new Set(crawlResults.map((item) => item.source_website))].sort();
  websiteFilter.innerHTML = ['<option value="">All websites</option>']
    .concat(names.map((name) => `<option value="${name}">${name}</option>`))
    .join("");
  websiteFilter.value = names.includes(current) ? current : "";
}

function activeFilterChips() {
  const chips = [];
  if (keywordSearch.value) {
    chips.push(`Keyword: ${keywordSearch.value}`);
  }
  if (websiteFilter.value) {
    chips.push(`Website: ${websiteFilter.value}`);
  }
  if (categoryFilter.value) {
    chips.push(`Category: ${categoryFilter.value}`);
  }
  if (dateFromFilter.value) {
    chips.push(`From: ${dateFromFilter.value}`);
  }
  if (dateToFilter.value) {
    chips.push(`To: ${dateToFilter.value}`);
  }
  return chips;
}

function renderActiveFilterChips() {
  const chips = activeFilterChips();
  activeFilterChipsNode.innerHTML = chips.map((chip) => `<span class="count-chip">${chip}</span>`).join("");
}

function filteredResults() {
  const keyword = normalize(keywordSearch.value);
  const website = websiteFilter.value;
  const category = categoryFilter.value;
  const fromDate = dateFromFilter.value;
  const toDate = dateToFilter.value;

  return crawlResults.filter((item) => {
    const haystack = normalize(`${item.title} ${item.description || ""}`);
    const publishDate = item.publish_date ? item.publish_date.slice(0, 10) : "";

    if (keyword && !haystack.includes(keyword)) {
      return false;
    }
    if (website && item.source_website !== website) {
      return false;
    }
    if (category && item.category !== category) {
      return false;
    }
    if (fromDate && (!publishDate || publishDate < fromDate)) {
      return false;
    }
    if (toDate && (!publishDate || publishDate > toDate)) {
      return false;
    }
    return true;
  });
}

function actionLinks(item) {
  const links = [];
  if (item.pdf_url) {
    links.push(`<a class="item-link" href="${item.pdf_url}" target="_blank" rel="noreferrer">Download PDF</a>`);
  }
  if (item.external_link) {
    links.push(`<a class="item-link" href="${item.external_link}" target="_blank" rel="noreferrer">Visit Link</a>`);
  }
  return links.join(" ");
}

function renderResults() {
  const results = filteredResults();
  renderActiveFilterChips();
  resultSummaryNode.textContent = `${results.length} items after frontend filtering.`;

  if (!results.length) {
    resultsBodyNode.innerHTML = "";
    emptyStateNode.style.display = "block";
    emptyStateNode.textContent = crawlResults.length
      ? "No items match the active filters."
      : "Run a crawl to populate results.";
    return;
  }

  emptyStateNode.style.display = "none";
  resultsBodyNode.innerHTML = results
    .map(
      (item) => `
        <tr class="${item.is_new ? "result-row--new" : ""}">
          <td>
            <div class="table-website">
              <strong>${item.source_website}</strong>
              ${item.section_label ? `<span>${item.section_label}</span>` : ""}
              <span>${item.from_cache ? "cache" : "live"}</span>
            </div>
          </td>
          <td>
            <div class="table-title">
              <strong>${item.title}</strong>
              ${item.is_new ? '<span class="badge badge--new">New</span>' : ""}
            </div>
          </td>
          <td><span class="badge">${item.category}</span></td>
          <td>${formatDate(item.publish_date)}</td>
          <td>${item.description || "No description extracted."}</td>
          <td class="table-links">${actionLinks(item)}</td>
        </tr>
      `
    )
    .join("");
}

function rerender() {
  renderMetrics();
  renderStatuses();
  syncWebsiteFilter();
  renderResults();
}

async function loadCatalog() {
  statusNode.textContent = "Loading site catalog...";
  const payload = await fetchJson("/api/sites");
  siteCatalog = payload.sites;
  renderCatalogSummary();
  renderSelectionSummary();
  renderSiteList();
  renderMetrics();
  renderStatuses();
  statusNode.textContent = "Select supported websites and start crawling.";
}

function queueStatuses() {
  const map = catalogByKey();
  siteStatuses = selectedSiteArray().map((siteKey) => ({
    site_key: siteKey,
    site_name: map[siteKey]?.name || siteKey,
    state: "queued",
    message: "Queued for crawl.",
    item_count: 0,
    new_count: 0,
    from_cache: false
  }));
  renderStatuses();
}

async function crawlSelectedSites() {
  const siteKeys = selectedSiteArray();
  if (!siteKeys.length) {
    statusNode.textContent = "Select at least one supported site before crawling.";
    return;
  }

  crawlButton.disabled = true;
  statusNode.textContent = `Crawling ${siteKeys.length} site(s)...`;
  queueStatuses();

  try {
    const payload = await fetchJson("/api/crawl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_keys: siteKeys,
        use_cache: useCacheToggle.checked
      })
    });

    crawlResults = payload.items;
    siteStatuses = payload.site_statuses;
    statusNode.textContent = `Crawl finished at ${formatDate(payload.crawl_time)}. ${payload.meta.returned_items} items returned.`;
    rerender();
  } catch (error) {
    statusNode.textContent = `Crawl failed: ${error.message}`;
  } finally {
    crawlButton.disabled = false;
  }
}

function downloadBlob(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function csvValue(value) {
  const text = `${value ?? ""}`.replace(/"/g, '""');
  return `"${text}"`;
}

function exportJson() {
  downloadBlob("crawl-results.json", JSON.stringify(filteredResults(), null, 2), "application/json");
}

function exportCsv() {
  const rows = filteredResults();
  const headers = ["source_website", "section_label", "title", "category", "publish_date", "description", "pdf_url", "external_link", "crawl_time", "is_new"];
  const content = [
    headers.join(","),
    ...rows.map((row) => headers.map((header) => csvValue(row[header])).join(","))
  ].join("\n");
  downloadBlob("crawl-results.csv", content, "text/csv;charset=utf-8");
}

function exportExcel() {
  const rows = filteredResults();
  const html = `
    <table>
      <thead>
        <tr>
          <th>Website</th>
          <th>Section</th>
          <th>Title</th>
          <th>Category</th>
          <th>Published</th>
          <th>Description</th>
          <th>PDF URL</th>
          <th>External Link</th>
          <th>Crawl Time</th>
          <th>New</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (row) => `
              <tr>
                <td>${row.source_website || ""}</td>
                <td>${row.section_label || ""}</td>
                <td>${row.title || ""}</td>
                <td>${row.category || ""}</td>
                <td>${row.publish_date || ""}</td>
                <td>${row.description || ""}</td>
                <td>${row.pdf_url || ""}</td>
                <td>${row.external_link || ""}</td>
                <td>${row.crawl_time || ""}</td>
                <td>${row.is_new ? "Yes" : "No"}</td>
              </tr>
            `
          )
          .join("")}
      </tbody>
    </table>
  `;
  downloadBlob("crawl-results.xls", html, "application/vnd.ms-excel");
}

siteSearchInput.addEventListener("input", renderSiteList);
selectSupportedButton.addEventListener("click", () => {
  supportedSites().forEach((site) => selectedSites.add(site.site_key));
  renderSiteList();
  renderSelectionSummary();
});
clearSelectionButton.addEventListener("click", () => {
  selectedSites.clear();
  renderSiteList();
  renderSelectionSummary();
});
crawlButton.addEventListener("click", crawlSelectedSites);

[keywordSearch, websiteFilter, categoryFilter, dateFromFilter, dateToFilter].forEach((node) => {
  node.addEventListener("input", renderResults);
  node.addEventListener("change", renderResults);
});

exportJsonButton.addEventListener("click", exportJson);
exportCsvButton.addEventListener("click", exportCsv);
exportExcelButton.addEventListener("click", exportExcel);

loadCatalog().catch((error) => {
  statusNode.textContent = `Failed to load site catalog: ${error.message}`;
});

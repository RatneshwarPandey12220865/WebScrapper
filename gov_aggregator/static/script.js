const siteListNode = document.getElementById("siteList");
const selectedSummaryNode = document.getElementById("selectedSummary");
const catalogSummaryNode = document.getElementById("catalogSummary");
const crawlButton = document.getElementById("crawlButton");
const crawlSpinner = document.getElementById("crawlSpinner");
const useCacheToggle = document.getElementById("useCacheToggle");
const siteSearchInput = document.getElementById("siteSearchInput");
const selectSupportedButton = document.getElementById("selectSupportedButton");
const clearSelectionButton = document.getElementById("clearSelectionButton");
const clearFiltersBtn = document.getElementById("clearFiltersBtn");
const toastContainer = document.getElementById("toastContainer");

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
let globalMinDate = null;
const selectedSites = new Set();

// ── Toast notifications ────────────────────────────────────────────────────
function showToast(message, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  toast.textContent = message;
  toastContainer.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ── Utilities ──────────────────────────────────────────────────────────────
async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return response.json();
}

function formatDate(value) {
  if (!value) return "Not available";
  return new Date(value).toLocaleString();
}

function formatDateRange(item) {
  if (!item.publish_date) return "Not available";
  const start = new Date(item.publish_date).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  if (!item.end_date) return start;
  const end = new Date(item.end_date).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  return `<span class="date-range">${start}<span class="date-range__arrow">→</span>${end}</span>`;
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

function catBadgeClass(category) {
  const known = ["notification", "news", "circular", "tender", "recruitment"];
  const slug = normalize(category);
  return known.includes(slug) ? `cat-badge cat-badge--${slug}` : "cat-badge cat-badge--default";
}

// ── Catalog summary ────────────────────────────────────────────────────────
function renderCatalogSummary() {
  const supported = supportedSites().length;
  catalogSummaryNode.textContent = `${supported} supported · ${siteCatalog.length} known`;
}

function renderSelectionSummary() {
  selectedSummaryNode.textContent = `${selectedSites.size} selected`;
}

// ── Site list ──────────────────────────────────────────────────────────────
function renderSiteList() {
  const query = normalize(siteSearchInput.value);
  const filtered = siteCatalog.filter((site) => {
    const haystack = `${site.name} ${site.site_key} ${site.crawl_url || ""}`;
    return normalize(haystack).includes(query);
  });

  siteListNode.innerHTML = "";
  filtered.forEach((site) => {
    const isSelected = selectedSites.has(site.site_key);
    const card = document.createElement("label");
    card.className = [
      "site-card",
      !site.supported ? "site-card--disabled" : "",
      isSelected ? "site-card--selected" : "",
    ].filter(Boolean).join(" ");

    const checked = isSelected ? "checked" : "";
    const disabled = site.supported ? "" : "disabled";
    const statusLabel = site.supported ? "Supported" : "Planned";

    const rawUrl = site.registry_url || site.preferred_url || site.crawl_url || "";
    const baseUrl = rawUrl
      ? (() => { try { return new URL(rawUrl).origin; } catch { return rawUrl; } })()
      : "No URL available";

    card.innerHTML = `
      <input type="checkbox" class="site-card__check" data-site-key="${site.site_key}" ${checked} ${disabled}>
      <div class="site-card__body">
        <div class="site-card__top">
          <strong>${site.name}</strong>
          <span class="badge${site.supported ? " badge--supported" : ""}">${statusLabel}</span>
        </div>
        <p>${baseUrl}</p>
      </div>
    `;

    card.querySelector("input").addEventListener("change", (e) => {
      const key = e.target.dataset.siteKey;
      if (e.target.checked) {
        selectedSites.add(key);
        card.classList.add("site-card--selected");
      } else {
        selectedSites.delete(key);
        card.classList.remove("site-card--selected");
      }
      renderSelectionSummary();
    });

    siteListNode.appendChild(card);
  });
}

// ── Metrics ────────────────────────────────────────────────────────────────
function renderMetrics() {
  const total = crawlResults.length;
  const pdfs = crawlResults.filter((item) => item.pdf_url).length;
  const newItems = crawlResults.filter((item) => item.is_new).length;
  const sites = new Set(crawlResults.map((item) => item.site_key)).size;

  const cards = [
    { label: "Selected", value: selectedSites.size },
    { label: "Crawled", value: sites },
    { label: "Total items", value: total },
    { label: "New", value: newItems },
    { label: "PDFs", value: pdfs },
    { label: "Failures", value: siteStatuses.filter((s) => s.state === "error").length },
  ];

  metricsNode.innerHTML = cards
    .map(
      (c) => `
        <article class="metric-card">
          <span class="metric-card__label">${c.label}</span>
          <strong class="metric-card__value">${c.value}</strong>
        </article>
      `
    )
    .join("");
}

// ── Statuses ───────────────────────────────────────────────────────────────
function renderStatuses() {
  if (!siteStatuses.length) {
    statusListNode.innerHTML = `
      <div class="status-item" data-state="idle">
        <span class="status-dot"></span>
        <div class="status-item__left">
          <strong>Idle</strong>
          <span>No crawl has been started yet.</span>
        </div>
      </div>`;
    return;
  }

  statusListNode.innerHTML = siteStatuses
    .map(
      (s) => {
        const dateSinceLabel = s.data_since
          ? `<span class="status-date-since">From ${formatDataSince(s.data_since)}</span>`
          : (s.state === "completed" || s.state === "cached")
            ? `<span class="status-date-since status-date-since--none">No date filter</span>`
            : "";
        return `
        <div class="status-item" data-state="${s.state}">
          <span class="status-dot"></span>
          <div class="status-item__left">
            <strong>${s.site_name}</strong>
            <span>${s.message}</span>
          </div>
          <div class="status-item__right">
            ${dateSinceLabel}
            <span class="status-state-label">${s.state}</span>
            <span>${s.item_count || 0} items</span>
            <span>${s.new_count || 0} new</span>
          </div>
        </div>
      `;}
    )
    .join("");
}

// ── Website filter sync ────────────────────────────────────────────────────
function syncWebsiteFilter() {
  const current = websiteFilter.value;
  const names = [...new Set(crawlResults.map((item) => item.source_website))].sort();
  websiteFilter.innerHTML = ['<option value="">All websites</option>']
    .concat(names.map((n) => `<option value="${n}">${n}</option>`))
    .join("");
  websiteFilter.value = names.includes(current) ? current : "";
}

// ── Filter chips ───────────────────────────────────────────────────────────
function getActiveFilters() {
  const chips = [];
  if (keywordSearch.value) chips.push(`Keyword: ${keywordSearch.value}`);
  if (websiteFilter.value) chips.push(`Website: ${websiteFilter.value}`);
  if (categoryFilter.value) chips.push(`Category: ${categoryFilter.value}`);
  if (dateFromFilter.value) chips.push(`From: ${dateFromFilter.value}`);
  if (dateToFilter.value) chips.push(`To: ${dateToFilter.value}`);
  return chips;
}

function renderActiveFilterChips() {
  const chips = getActiveFilters();
  activeFilterChipsNode.innerHTML = chips
    .map((chip) => `<span class="filter-chip">${chip}</span>`)
    .join("");
}

// ── Filtered results ───────────────────────────────────────────────────────
function filteredResults() {
  const keyword = normalize(keywordSearch.value);
  const website = websiteFilter.value;
  const category = categoryFilter.value;
  const fromDate = dateFromFilter.value;
  const toDate = dateToFilter.value;

  return crawlResults.filter((item) => {
    const haystack = normalize(`${item.title} ${item.description || ""}`);
    const publishDate = item.publish_date ? item.publish_date.slice(0, 10) : "";

    if (keyword && !haystack.includes(keyword)) return false;
    if (website && item.source_website !== website) return false;
    if (category && item.category !== category) return false;
    if (fromDate && (!publishDate || publishDate < fromDate)) return false;
    if (toDate && (!publishDate || publishDate > toDate)) return false;
    return true;
  });
}

// ── Action links ───────────────────────────────────────────────────────────
function actionLinks(item) {
  const links = [];
  if (item.pdf_url) {
    links.push(`<a class="link-btn link-btn--pdf" href="${item.pdf_url}" target="_blank" rel="noreferrer">PDF</a>`);
  }
  if (item.external_link) {
    links.push(`<a class="link-btn link-btn--ext" href="${item.external_link}" target="_blank" rel="noreferrer">Link</a>`);
  }
  return links.join("");
}

// ── Results table ──────────────────────────────────────────────────────────
function renderResults() {
  const results = filteredResults();
  renderActiveFilterChips();
  resultSummaryNode.textContent = `${results.length} item${results.length !== 1 ? "s" : ""} shown`;

  if (!results.length) {
    resultsBodyNode.innerHTML = "";
    emptyStateNode.style.display = "flex";
    emptyStateNode.querySelector("p").textContent = crawlResults.length
      ? "No items match the active filters."
      : "Run a crawl to populate results.";
    return;
  }

  emptyStateNode.style.display = "none";

  // Mark consecutive rows with same title+site as part of a multi-PDF group
  results.forEach((item, i) => {
    const prev = results[i - 1];
    const next = results[i + 1];
    item._groupFirst = !prev || prev.title !== item.title || prev.site_key !== item.site_key;
    item._groupLast  = !next || next.title !== item.title || next.site_key !== item.site_key;
    item._inGroup    = !item._groupFirst || !item._groupLast;
  });

  resultsBodyNode.innerHTML = results
    .map(
      (item) => {
        const groupClass = item._inGroup
          ? (item._groupFirst ? " row--group-first" : item._groupLast ? " row--group-last" : " row--group-mid")
          : "";
        const showMeta = item._groupFirst;
        return `
        <tr class="${item.is_new ? "row--new" : ""}${groupClass}">
          <td>
            ${showMeta ? `<div class="cell-website">
              <strong>${item.source_website}</strong>
              ${item.section_label ? `<span>${item.section_label}</span>` : ""}
              ${item.from_cache ? `<span class="cache-tag">cache</span>` : ""}
            </div>` : `<div class="cell-website cell-website--cont">
              <span class="multi-pdf-tag">same item</span>
            </div>`}
          </td>
          <td class="cell-title">
            ${showMeta ? `<strong>${item.title}</strong>
            ${item.is_new ? '<span class="badge badge--new">New</span>' : ""}` : `<span class="cell-title__cont">↳ additional PDF</span>`}
          </td>
          <td>${showMeta ? `<span class="${catBadgeClass(item.category)}">${item.category || "—"}</span>` : ""}</td>
          <td>${showMeta ? formatDateRange(item) : ""}</td>
          <td>${actionLinks(item)}</td>
        </tr>
      `;}
    )
    .join("");
}

function rerender() {
  renderMetrics();
  renderStatuses();
  syncWebsiteFilter();
  renderResults();
}

// ── Catalog load ───────────────────────────────────────────────────────────
function formatDataSince(dateSince) {
  if (!dateSince) return "No date filter";
  const d = new Date(dateSince + "T00:00:00Z");
  return d.toLocaleDateString("en-IN", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

function renderDateFilterBanner() {
  const banner = document.getElementById("dateFilterBanner");
  const text = document.getElementById("dateFilterBannerText");

  if (!siteStatuses.length) {
    // Before any crawl — show the global default
    if (!globalMinDate) { banner.style.display = "none"; return; }
    const d = new Date(globalMinDate + "T00:00:00Z");
    const formatted = d.toLocaleDateString("en-IN", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
    text.textContent = `Default filter: items from ${formatted} onwards`;
    banner.style.display = "flex";
    return;
  }

  // After a crawl — show the range across crawled sites
  const activeSinces = siteStatuses
    .filter(s => s.state === "completed" || s.state === "cached")
    .map(s => s.data_since);
  const noFilter = activeSinces.some(d => !d);
  const unique = [...new Set(activeSinces.filter(Boolean))].sort();

  if (!unique.length && noFilter) {
    text.textContent = "No date filter applied";
  } else if (unique.length === 1 && !noFilter) {
    text.textContent = `Showing items from ${formatDataSince(unique[0])} onwards`;
  } else {
    const parts = unique.map(formatDataSince);
    if (noFilter) parts.push("some with no filter");
    text.textContent = `Date filters: ${parts.join(" · ")}`;
  }
  banner.style.display = "flex";
}

async function loadCatalog() {
  statusNode.textContent = "Loading site catalog…";
  try {
    const payload = await fetchJson("/api/sites");
    siteCatalog = payload.sites;
    globalMinDate = payload.meta?.global_min_date ?? null;
    renderCatalogSummary();
    renderSelectionSummary();
    renderSiteList();
    renderMetrics();
    renderStatuses();
    renderDateFilterBanner();
    statusNode.textContent = "Select supported websites and start crawling.";
  } catch (err) {
    statusNode.textContent = `Failed to load catalog: ${err.message}`;
    showToast(`Catalog load failed: ${err.message}`, "error");
  }
}

// ── Crawl ──────────────────────────────────────────────────────────────────
function queueStatuses() {
  const map = catalogByKey();
  siteStatuses = selectedSiteArray().map((key) => ({
    site_key: key,
    site_name: map[key]?.name || key,
    state: "queued",
    message: "Queued for crawl.",
    item_count: 0,
    new_count: 0,
    from_cache: false,
  }));
  renderStatuses();
}

async function crawlSelectedSites() {
  const siteKeys = selectedSiteArray();
  if (!siteKeys.length) {
    showToast("Select at least one supported site before crawling.", "info");
    return;
  }

  crawlButton.disabled = true;
  crawlSpinner.style.display = "block";
  statusNode.textContent = `Crawling ${siteKeys.length} site(s)…`;
  queueStatuses();

  try {
    const payload = await fetchJson("/api/crawl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site_keys: siteKeys, use_cache: useCacheToggle.checked }),
    });

    crawlResults = payload.items;
    siteStatuses = payload.site_statuses;
    const returned = payload.meta?.returned_items ?? crawlResults.length;
    statusNode.textContent = `Crawl finished at ${formatDate(payload.crawl_time)}. ${returned} items returned.`;

    const errors = siteStatuses.filter((s) => s.state === "error").length;
    if (errors) {
      showToast(`Crawl complete — ${errors} site(s) failed.`, "error");
    } else {
      showToast(`Crawl complete — ${returned} items returned.`, "success");
    }

    rerender();
    renderDateFilterBanner();
  } catch (err) {
    statusNode.textContent = `Crawl failed: ${err.message}`;
    showToast(`Crawl failed: ${err.message}`, "error");
  } finally {
    crawlButton.disabled = false;
    crawlSpinner.style.display = "none";
  }
}

// ── Export ─────────────────────────────────────────────────────────────────
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
  const headers = ["source_website", "section_label", "title", "category", "publish_date", "pdf_url", "external_link", "crawl_time", "is_new"];
  const content = [
    headers.join(","),
    ...rows.map((row) => headers.map((h) => csvValue(row[h])).join(",")),
  ].join("\n");
  downloadBlob("crawl-results.csv", content, "text/csv;charset=utf-8");
}

function exportExcel() {
  const rows = filteredResults();
  const html = `<table>
    <thead><tr>
      <th>Website</th><th>Section</th><th>Title</th><th>Category</th>
      <th>Published</th><th>PDF URL</th><th>External Link</th><th>New</th>
    </tr></thead>
    <tbody>${rows.map((r) => `<tr>
      <td>${r.source_website || ""}</td><td>${r.section_label || ""}</td>
      <td>${r.title || ""}</td><td>${r.category || ""}</td>
      <td>${r.publish_date || ""}</td><td>${r.pdf_url || ""}</td>
      <td>${r.external_link || ""}</td><td>${r.is_new ? "Yes" : "No"}</td>
    </tr>`).join("")}</tbody>
  </table>`;
  downloadBlob("crawl-results.xls", html, "application/vnd.ms-excel");
}

// ── Event listeners ────────────────────────────────────────────────────────
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

clearFiltersBtn.addEventListener("click", () => {
  keywordSearch.value = "";
  websiteFilter.value = "";
  categoryFilter.value = "";
  dateFromFilter.value = "";
  dateToFilter.value = "";
  renderResults();
});

crawlButton.addEventListener("click", crawlSelectedSites);

[keywordSearch, websiteFilter, categoryFilter, dateFromFilter, dateToFilter].forEach((node) => {
  node.addEventListener("input", renderResults);
  node.addEventListener("change", renderResults);
});

exportJsonButton.addEventListener("click", exportJson);
exportCsvButton.addEventListener("click", exportCsv);
exportExcelButton.addEventListener("click", exportExcel);

// ── Boot ───────────────────────────────────────────────────────────────────
loadCatalog();

const siteListNode = document.getElementById("siteList");
const selectedSummaryNode = document.getElementById("selectedSummary");
const catalogSummaryNode = document.getElementById("catalogSummary");
const crawlButton = document.getElementById("crawlButton");
const crawlAllButton = document.getElementById("crawlAllButton");
const crawlSpinner = document.getElementById("crawlSpinner");
const useCacheToggle = document.getElementById("useCacheToggle");
const siteSearchInput = document.getElementById("siteSearchInput");
const selectSupportedButton = document.getElementById("selectSupportedButton");
const clearSelectionButton = document.getElementById("clearSelectionButton");
const clearFiltersBtn = document.getElementById("clearFiltersBtn");
const toastContainer = document.getElementById("toastContainer");

// Bulk crawl modal nodes
const bulkCrawlModal   = document.getElementById("bulkCrawlModal");
const modalSubtitle    = document.getElementById("modalSubtitle");
const modalSpinner     = document.getElementById("modalSpinner");
const progressBarFill  = document.getElementById("progressBarFill");
const progressLabel    = document.getElementById("progressLabel");
const statDone         = document.getElementById("statDone");
const statTotal        = document.getElementById("statTotal");
const statElapsed      = document.getElementById("statElapsed");
const statStatus       = document.getElementById("statStatus");
const cancelCrawlBtn   = document.getElementById("cancelCrawlBtn");
const loadResultsBtn   = document.getElementById("loadResultsBtn");
const exportSummaryBtn = document.getElementById("exportSummaryBtn");
const exportAllBtn     = document.getElementById("exportAllBtn");

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

// Bulk crawl state
let activeBulkJobId = null;
let activeBulkJobStatus = null;   // tracks actual job status string
let pollInterval = null;

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
  const filtered = filteredResults();
  const isFiltered = filtered.length !== crawlResults.length;

  const totalAll   = crawlResults.length;
  const total      = filtered.length;
  const pdfs       = filtered.filter((item) => item.pdf_url).length;
  const newItems   = filtered.filter((item) => item.is_new).length;
  const sites      = new Set(crawlResults.map((item) => item.site_key)).size;
  const failures   = siteStatuses.filter((s) => s.state === "error").length;

  const cards = [
    { label: "Selected",    value: selectedSites.size,                          mod: false },
    { label: "Crawled",     value: sites,                                        mod: false },
    { label: "Items shown", value: isFiltered ? `${total} / ${totalAll}` : total, mod: isFiltered },
    { label: "New",         value: newItems,                                     mod: isFiltered },
    { label: "PDFs",        value: pdfs,                                         mod: isFiltered },
    { label: "Failures",    value: failures,                                     mod: false },
  ];

  metricsNode.innerHTML = cards
    .map(
      (c) => `
        <article class="metric-card${c.mod ? " metric-card--filtered" : ""}">
          <span class="metric-card__label">${c.label}${c.mod ? " <span class='metric-filter-tag'>filtered</span>" : ""}</span>
          <strong class="metric-card__value">${c.value}</strong>
        </article>
      `
    )
    .join("");
}

// ── Statuses ───────────────────────────────────────────────────────────────
function allCountsBySite() {
  // Counts from crawlResults — post server-side date filter, pre UI filter
  const counts = {};
  for (const item of crawlResults) {
    if (!counts[item.site_key]) counts[item.site_key] = { items: 0, new_items: 0 };
    counts[item.site_key].items++;
    if (item.is_new) counts[item.site_key].new_items++;
  }
  return counts;
}

function filteredCountsBySite() {
  // Counts from filteredResults — post UI filter (keyword, date, category, website)
  const counts = {};
  for (const item of filteredResults()) {
    if (!counts[item.site_key]) counts[item.site_key] = { items: 0, new_items: 0 };
    counts[item.site_key].items++;
    if (item.is_new) counts[item.site_key].new_items++;
  }
  return counts;
}

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

  const allBySite      = allCountsBySite();
  const filteredBySite = filteredCountsBySite();
  const isFiltered     = filteredResults().length !== crawlResults.length;

  statusListNode.innerHTML = siteStatuses
    .map((s) => {
      // Use crawlResults-derived counts as the "actual" totals — these are
      // post server-side date filter and always match the metrics cards.
      const ac = allBySite[s.site_key];
      const fc = filteredBySite[s.site_key];
      const totalItems = ac ? ac.items : 0;
      const totalNew   = ac ? ac.new_items : 0;
      const shownItems = fc ? fc.items : 0;
      const shownNew   = fc ? fc.new_items : 0;

      const isActive = s.state === "completed" || s.state === "cached";

      const itemLabel = isFiltered && isActive
        ? `<span class="status-count ${shownItems === 0 ? "status-count--zero" : ""}">
             <span class="status-count__shown">${shownItems}</span>
             <span class="status-count__sep">/</span>
             <span class="status-count__total">${totalItems}</span>
             <span class="status-count__unit">items</span>
           </span>`
        : `<span class="status-count">
             <span class="status-count__shown">${totalItems}</span>
             <span class="status-count__unit">items</span>
           </span>`;

      const newLabel = isFiltered && isActive
        ? `<span class="status-count ${shownNew === 0 ? "status-count--zero" : ""}">
             <span class="status-count__shown">${shownNew}</span>
             <span class="status-count__sep">/</span>
             <span class="status-count__total">${totalNew}</span>
             <span class="status-count__unit">new</span>
           </span>`
        : `<span class="status-count">
             <span class="status-count__shown">${totalNew}</span>
             <span class="status-count__unit">new</span>
           </span>`;

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
            ${itemLabel}
            ${newLabel}
          </div>
        </div>`;
    })
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
  renderMetrics();
  renderStatuses();
  resultSummaryNode.textContent = `${results.length} item${results.length !== 1 ? "s" : ""} shown${results.length !== crawlResults.length ? ` (${crawlResults.length} total)` : ""}`;

  if (!results.length) {
    resultsBodyNode.innerHTML = "";
    emptyStateNode.style.display = "flex";
    emptyStateNode.querySelector("p").textContent = crawlResults.length
      ? "No items match the active filters."
      : "Run a crawl to populate results.";
    return;
  }

  emptyStateNode.style.display = "none";

  // Mark consecutive rows with same title+site+section as part of a multi-PDF group.
  // section_label is included so a single item that legitimately appears in
  // two different sections (e.g. DOLR "What's New" + "Orders & Notices")
  // renders as two full rows rather than collapsing into "↳ additional PDF".
  const sameGroup = (a, b) =>
    a && b &&
    a.title === b.title &&
    a.site_key === b.site_key &&
    (a.section_label || "") === (b.section_label || "");
  results.forEach((item, i) => {
    item._groupFirst = !sameGroup(results[i - 1], item);
    item._groupLast  = !sameGroup(results[i + 1], item);
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
  const clearBtn = document.getElementById("dateFilterBannerClear");

  const fromVal = dateFromFilter.value;
  const toVal = dateToFilter.value;
  const hasCustom = fromVal || toVal;

  if (hasCustom) {
    // Custom date range is active — show it prominently
    const fromStr = fromVal
      ? new Date(fromVal + "T00:00:00Z").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" })
      : "beginning";
    const toStr = toVal
      ? new Date(toVal + "T00:00:00Z").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" })
      : "today";
    text.textContent = `Custom filter: ${fromStr} → ${toStr}`;
    banner.setAttribute("data-custom", "true");
    if (clearBtn) clearBtn.style.display = "inline-flex";
  } else {
    // No custom filter — show the global default
    banner.removeAttribute("data-custom");
    if (clearBtn) clearBtn.style.display = "none";

    if (!globalMinDate) { banner.style.display = "none"; return; }
    const d = new Date(globalMinDate + "T00:00:00Z");
    const formatted = d.toLocaleDateString("en-IN", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
    text.textContent = `Default: items from ${formatted} onwards`;
  }

  banner.style.display = "flex";
}

// ── Phase 5: Date Range Widget ─────────────────────────────────────────────

const LS_KEY = "kspyder_date_range";

// Active range: { from: "YYYY-MM-DD"|null, to: "YYYY-MM-DD"|null, preset: string|null }
let activeRange = { from: null, to: null, preset: null };

function _todayIST() {
  // Return today's date as YYYY-MM-DD in IST (UTC+5:30)
  const now = new Date(Date.now() + (5.5 * 60 * 60 * 1000));
  return now.toISOString().slice(0, 10);
}

function _offsetDay(isoDate, days) {
  const d = new Date(isoDate + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function _firstOfMonth(isoDate) {
  return isoDate.slice(0, 7) + "-01";
}

function _fmtDateLabel(iso) {
  if (!iso) return "—";
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

function _presetDates(preset) {
  const today = _todayIST();
  switch (preset) {
    case "today":     return { from: today, to: today };
    case "yesterday": return { from: _offsetDay(today, -1), to: _offsetDay(today, -1) };
    case "last7":     return { from: _offsetDay(today, -6), to: today };
    case "thismonth": return { from: _firstOfMonth(today), to: today };
    default:          return { from: null, to: null };
  }
}

function loadRangeFromStorage() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") activeRange = parsed;
    }
  } catch (_) {}
  // Recalculate dates for time-relative presets so they stay current on next-day loads
  if (activeRange.preset && activeRange.preset !== "custom" && activeRange.preset !== "alltime" && activeRange.preset !== null) {
    const recalc = _presetDates(activeRange.preset);
    activeRange = { ...activeRange, from: recalc.from, to: recalc.to };
  }
  // Keep sidebar inputs in sync
  dateFromFilter.value = activeRange.from || "";
  dateToFilter.value   = activeRange.to   || "";
}

function saveRangeToStorage() {
  try { localStorage.setItem(LS_KEY, JSON.stringify(activeRange)); } catch (_) {}
}

function applyRange(from, to, preset) {
  activeRange = { from: from || null, to: to || null, preset: preset || null };
  // Sync sidebar date inputs so existing filter logic keeps working unchanged
  dateFromFilter.value = activeRange.from || "";
  dateToFilter.value   = activeRange.to   || "";
  saveRangeToStorage();
  renderDRW();
  renderResults();
  renderDateFilterBanner();
}

function renderDRW() {
  const sel = document.getElementById("drwPresetSelect");
  const customRow = document.getElementById("drwCustomRow");
  const badge = document.getElementById("drwActive");

  // Sync dropdown value
  const selectVal = activeRange.preset || "alltime";
  if (sel) sel.value = selectVal;

  // Show/hide custom row
  if (activeRange.preset === "custom") {
    customRow.style.display = "flex";
    const drwFrom = document.getElementById("drwFrom");
    const drwTo   = document.getElementById("drwTo");
    if (drwFrom && !drwFrom.value) drwFrom.value = activeRange.from || "";
    if (drwTo   && !drwTo.value)   drwTo.value   = activeRange.to   || "";
  } else {
    customRow.style.display = "none";
  }

  // Badge
  if (activeRange.from || activeRange.to) {
    badge.textContent = `${_fmtDateLabel(activeRange.from)} → ${_fmtDateLabel(activeRange.to)}`;
    badge.className = "drw__badge";
  } else {
    badge.textContent = "All time (from Jan 2026)";
    badge.className = "drw__badge drw__badge--default";
  }
}

function initDRW() {
  loadRangeFromStorage();

  const sel = document.getElementById("drwPresetSelect");
  sel.addEventListener("change", () => {
    const preset = sel.value;
    if (preset === "custom") {
      activeRange = { ...activeRange, preset: "custom" };
      renderDRW();
      return;
    }
    if (preset === "alltime") {
      applyRange(null, null, null);
      return;
    }
    const { from, to } = _presetDates(preset);
    applyRange(from, to, preset);
  });

  document.getElementById("drwApply").addEventListener("click", () => {
    const from = document.getElementById("drwFrom").value || null;
    const to   = document.getElementById("drwTo").value   || null;
    if (from && to && from > to) {
      showToast("'From' date must be on or before 'To' date.", "error");
      return;
    }
    applyRange(from, to, "custom");
  });

  renderDRW();
}

// ── Phase 5: Export Panel ──────────────────────────────────────────────────

let exportPanelOpen = false;

function renderExportPanel() {
  const panel = document.getElementById("exportPanel");
  const sitesDiv = document.getElementById("exportPanelSites");
  const rangeLabel = document.getElementById("exportPanelRange");

  // Only show if we have a completed bulk job with results
  if (!activeBulkJobId || !crawlResults.length) {
    panel.style.display = "none";
    return;
  }

  panel.style.display = "block";

  // Update range label
  const fromLabel = activeRange.from ? _fmtDateLabel(activeRange.from) : "Jan 2026";
  const toLabel   = activeRange.to   ? _fmtDateLabel(activeRange.to)   : "today";
  rangeLabel.textContent = `Date range: ${fromLabel} → ${toLabel}`;

  // Build per-site rows: only sites with items
  // filteredCountsBySite() returns { site_key: {items, new_items} } — extract .items
  const countMap = filteredCountsBySite();
  const siteEntries = Object.entries(countMap)
    .map(([sk, obj]) => [sk, obj.items])
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);

  if (!siteEntries.length) {
    sitesDiv.innerHTML = "<p style='color:var(--neutral-400);font-size:0.83rem'>No sites have items in the current date range.</p>";
    return;
  }

  sitesDiv.innerHTML = siteEntries.map(([sk, count]) => {
    const st = siteStatuses.find(s => s.site_key === sk);
    const name = st?.site_name || sk;
    return `<div class="ep-site-row">
      <span class="ep-site-row__name">${name}</span>
      <span class="ep-site-row__count">${count} item${count !== 1 ? "s" : ""}</span>
      <button class="ep-site-row__btn" data-site="${sk}" type="button">⬇ Excel</button>
    </div>`;
  }).join("");

  // Wire up per-site download buttons
  sitesDiv.querySelectorAll(".ep-site-row__btn").forEach(btn => {
    btn.addEventListener("click", () => downloadSiteExcel(btn.dataset.site, btn));
  });
}

async function downloadSiteExcel(siteKey, btn) {
  if (!activeBulkJobId) return;
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "…";
  try {
    let url = `/api/export/site/${encodeURIComponent(siteKey)}?job_id=${encodeURIComponent(activeBulkJobId)}`;
    if (activeRange.from) url += `&date_from=${encodeURIComponent(activeRange.from)}`;
    if (activeRange.to)   url += `&date_to=${encodeURIComponent(activeRange.to)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const disposition = resp.headers.get("content-disposition") || "";
    const fnMatch = disposition.match(/filename[^;=\n]*=([^;\n]*)/);
    const filename = fnMatch ? fnMatch[1].replace(/['"]/g, "").trim() : `${siteKey}.xlsx`;
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objUrl; a.download = filename; a.click();
    URL.revokeObjectURL(objUrl);
    showToast(`Downloaded: ${filename}`, "success");
  } catch (err) {
    showToast(`Download failed: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

function initExportPanel() {
  const toggle = document.getElementById("exportPanelToggle");
  const body   = document.getElementById("exportPanelBody");
  const chev   = document.getElementById("exportPanelChevron");

  toggle.addEventListener("click", () => {
    exportPanelOpen = !exportPanelOpen;
    body.style.display = exportPanelOpen ? "block" : "none";
    chev.classList.toggle("export-panel__chevron--up", exportPanelOpen);
    if (exportPanelOpen) renderExportPanel();
  });

  // Wire top-level summary/zip buttons inside export panel
  document.getElementById("epSummaryBtn").addEventListener("click", exportSummaryExcel);
  document.getElementById("epZipBtn").addEventListener("click", exportAllZip);
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
    renderDateFilterBanner();   // show default Jan 2026 banner immediately
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
    const shown = filteredResults().length;
    const filterNote = shown !== returned ? ` · ${shown} shown with active filter` : "";
    statusNode.textContent = `Crawl finished at ${formatDate(payload.crawl_time)}. ${returned} items returned${filterNote}.`;

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
    crawlAllButton.disabled = false;
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

// ── Bulk crawl (Crawl All) ─────────────────────────────────────────────────
function openBulkModal() {
  progressBarFill.style.width = "0%";
  progressLabel.textContent = "0%";
  statDone.textContent = "0";
  statTotal.textContent = "—";
  statElapsed.textContent = "0s";
  statStatus.textContent = "starting";
  modalSubtitle.textContent = "Preparing bulk crawl…";
  modalSpinner.style.display = "block";
  loadResultsBtn.style.display = "none";
  exportSummaryBtn.style.display = "none";
  exportAllBtn.style.display = "none";
  cancelCrawlBtn.disabled = false;
  cancelCrawlBtn.textContent = "Cancel";
  bulkCrawlModal.style.display = "flex";
}

function closeBulkModal() {
  bulkCrawlModal.style.display = "none";
  activeBulkJobStatus = null;
  exportSummaryBtn.style.display = "none";
  exportAllBtn.style.display = "none";
  stopPoll();
}

function stopPoll() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

function updateModalProgress(job) {
  const pct = job.percent_complete ?? 0;
  progressBarFill.style.width = `${pct}%`;
  progressLabel.textContent = `${pct}%`;
  statDone.textContent = job.sites_done ?? 0;
  statTotal.textContent = job.sites_total ?? "—";
  statElapsed.textContent = `${job.elapsed_seconds ?? 0}s`;
  statStatus.textContent = job.status;
  activeBulkJobStatus = job.status;   // keep reliable copy outside DOM

  if (job.status === "running") {
    modalSubtitle.textContent = `Crawling ${job.sites_done} of ${job.sites_total} sites…`;
  } else if (job.status === "done") {
    const meta = job.result_meta;
    const items = meta ? meta.returned_items : "?";
    const errors = meta ? meta.errors : "?";
    modalSubtitle.textContent = `Done — ${items} items, ${errors} errors`;
    modalSpinner.style.display = "none";
    loadResultsBtn.style.display = "inline-flex";
    exportSummaryBtn.style.display = "inline-flex";
    exportAllBtn.style.display = "inline-flex";
    cancelCrawlBtn.textContent = "Close";
  } else if (job.status === "cancelled") {
    modalSubtitle.textContent = "Crawl was cancelled.";
    modalSpinner.style.display = "none";
    cancelCrawlBtn.textContent = "Close";
  } else if (job.status === "failed") {
    modalSubtitle.textContent = "Crawl encountered a fatal error.";
    modalSpinner.style.display = "none";
    cancelCrawlBtn.textContent = "Close";
  }
}

async function pollJobStatus() {
  if (!activeBulkJobId) return;
  try {
    const job = await fetchJson(`/api/crawl/status/${activeBulkJobId}`);
    updateModalProgress(job);
    if (["done", "cancelled", "failed"].includes(job.status)) {
      stopPoll();
    }
  } catch (err) {
    showToast(`Poll error: ${err.message}`, "error");
  }
}

async function loadBulkResults() {
  if (!activeBulkJobId) return;
  loadResultsBtn.disabled = true;
  loadResultsBtn.textContent = "Loading…";
  try {
    const payload = await fetchJson(`/api/crawl/result/${activeBulkJobId}`);
    crawlResults = payload.items || [];
    siteStatuses = payload.site_statuses || [];
    const returned = payload.meta?.returned_items ?? crawlResults.length;
    statusNode.textContent = `Bulk crawl finished at ${formatDate(payload.crawl_time)}. ${returned} items returned.`;
    const errors = siteStatuses.filter((s) => s.state === "error").length;
    if (errors) {
      showToast(`Bulk crawl complete — ${errors} site(s) failed.`, "error");
    } else {
      showToast(`Bulk crawl complete — ${returned} items returned.`, "success");
    }
    rerender();
    renderDateFilterBanner();
    renderExportPanel();
    closeBulkModal();
  } catch (err) {
    showToast(`Failed to load results: ${err.message}`, "error");
    loadResultsBtn.disabled = false;
    loadResultsBtn.textContent = "Load Results";
  }
}

async function exportSummaryExcel() {
  if (!activeBulkJobId) return;
  exportSummaryBtn.disabled = true;
  exportSummaryBtn.textContent = "Generating…";
  try {
    const dateFrom = dateFromFilter.value || null;
    const dateTo   = dateToFilter.value   || null;
    const resp = await fetch("/api/export/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: activeBulkJobId, date_from: dateFrom, date_to: dateTo }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const disposition = resp.headers.get("content-disposition") || "";
    const fnMatch = disposition.match(/filename[^;=\n]*=([^;\n]*)/);
    const filename = fnMatch ? fnMatch[1].replace(/['"]/g, "").trim() : "KSyder_Summary.xlsx";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`Excel exported: ${filename}`, "success");
  } catch (err) {
    showToast(`Export failed: ${err.message}`, "error");
  } finally {
    exportSummaryBtn.disabled = false;
    exportSummaryBtn.textContent = "⬇ Export Summary Excel";
  }
}

async function exportAllZip() {
  if (!activeBulkJobId) return;
  exportAllBtn.disabled = true;
  exportAllBtn.textContent = "Building ZIP…";
  try {
    const dateFrom = dateFromFilter.value || null;
    const dateTo   = dateToFilter.value   || null;
    let url = `/api/export/all?job_id=${encodeURIComponent(activeBulkJobId)}`;
    if (dateFrom) url += `&date_from=${encodeURIComponent(dateFrom)}`;
    if (dateTo)   url += `&date_to=${encodeURIComponent(dateTo)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const disposition = resp.headers.get("content-disposition") || "";
    const fnMatch = disposition.match(/filename[^;=\n]*=([^;\n]*)/);
    const filename = fnMatch ? fnMatch[1].replace(/['"]/g, "").trim() : "KSyder_Export.zip";
    const url2 = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url2;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url2);
    showToast(`ZIP exported: ${filename}`, "success");
  } catch (err) {
    showToast(`ZIP export failed: ${err.message}`, "error");
  } finally {
    exportAllBtn.disabled = false;
    exportAllBtn.textContent = "⬇ Export All Sites (ZIP)";
  }
}

async function crawlAllSites() {
  crawlAllButton.disabled = true;
  openBulkModal();

  try {
    const body = { use_cache: useCacheToggle.checked };
    if (dateFromFilter.value) body.date_from = dateFromFilter.value;
    if (dateToFilter.value) body.date_to = dateToFilter.value;

    const response = await fetchJson("/api/crawl/all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    activeBulkJobId = response.job_id;
    showToast(`Bulk crawl started (job ${activeBulkJobId})`, "info");

    // Poll every 3 seconds
    pollInterval = setInterval(pollJobStatus, 3000);
    pollJobStatus(); // immediate first tick
  } catch (err) {
    closeBulkModal();
    showToast(`Failed to start bulk crawl: ${err.message}`, "error");
    crawlAllButton.disabled = false;
  }
}

async function cancelBulkCrawl() {
  const isDone = ["done", "cancelled", "failed"].includes(activeBulkJobStatus);
  if (isDone) { closeBulkModal(); crawlAllButton.disabled = false; return; }
  if (!activeBulkJobId) { closeBulkModal(); return; }
  try {
    await fetchJson(`/api/crawl/cancel/${activeBulkJobId}`, { method: "POST" });
    stopPoll();
    showToast("Crawl cancelled.", "info");
  } catch (err) {
    showToast(`Cancel error: ${err.message}`, "error");
  } finally {
    closeBulkModal();
    crawlAllButton.disabled = false;
  }
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
  applyRange(null, null, null);   // resets DRW + sidebar inputs + re-renders
});

crawlButton.addEventListener("click", crawlSelectedSites);
crawlAllButton.addEventListener("click", crawlAllSites);
cancelCrawlBtn.addEventListener("click", cancelBulkCrawl);
loadResultsBtn.addEventListener("click", loadBulkResults);
exportSummaryBtn.addEventListener("click", exportSummaryExcel);
exportAllBtn.addEventListener("click", exportAllZip);

[keywordSearch, websiteFilter, categoryFilter].forEach((node) => {
  node.addEventListener("input", renderResults);
  node.addEventListener("change", renderResults);
});

[dateFromFilter, dateToFilter].forEach((node) => {
  node.addEventListener("input", () => { renderResults(); renderDateFilterBanner(); });
  node.addEventListener("change", () => { renderResults(); renderDateFilterBanner(); });
});

exportJsonButton.addEventListener("click", exportJson);
exportCsvButton.addEventListener("click", exportCsv);
exportExcelButton.addEventListener("click", exportExcel);

// ── Boot ───────────────────────────────────────────────────────────────────
document.getElementById("dateFilterBannerClear").addEventListener("click", () => {
  applyRange(null, null, null);
});

initDRW();
initExportPanel();
loadCatalog();

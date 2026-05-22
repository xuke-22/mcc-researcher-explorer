// ════════════════════════════════════════════════════════════
// MCC Research Explorer — Frontend
// ════════════════════════════════════════════════════════════

const $ = (id) => document.getElementById(id);
const resultsEl = () => $("results");
const loadingEl = () => $("loading");

let _mccNames = [];

// ─── On Load ────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    loadStats();
    initEnterKey();
    loadMccNames();
    initYearSlider();
});

async function loadMccNames() {
    try {
        const r = await fetch("/api/mcc_names");
        _mccNames = await r.json();
    } catch (e) {
        console.warn("Could not load MCC names:", e);
    }
}

// ─── Tabs ────────────────────────────────────────────
function initTabs() {
    document.querySelectorAll(".tab").forEach(tab => {
        tab.addEventListener("click", () => {
            const target = tab.dataset.tab;
            document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            tab.classList.add("active");
            $(`panel-${target}`).classList.add("active");
            resultsEl().innerHTML = "";
        });
    });
}

// ─── Enter key bindings ────────────────────────────────
function initEnterKey() {
    bindEnter("researcherInput", searchResearcher);
    bindEnter("keywordInput", searchKeyword);
    bindEnter("keywordResearcherInput", searchKeyword);
    bindEnter("fundingKeywordInput", searchFundingKeyword);
}
function bindEnter(id, fn) {
    const el = $(id);
    if (!el) return;
    el.addEventListener("keypress", (e) => {
        if (e.key === "Enter") fn();
    });
}

// ─── Year Slider ────────────────────────────────────────
function initYearSlider() {
    const minSlider = $("yearMinSlider");
    const maxSlider = $("yearMaxSlider");
    const minLabel = $("yearMinLabel");
    const maxLabel = $("yearMaxLabel");
    const track = $("sliderTrack");
    if (!minSlider || !maxSlider) return;

    function update() {
        let minVal = parseInt(minSlider.value);
        let maxVal = parseInt(maxSlider.value);
        if (minVal > maxVal) {
            minSlider.value = maxVal;
            minVal = maxVal;
        }
        if (maxVal < minVal) {
            maxSlider.value = minVal;
            maxVal = minVal;
        }
        minLabel.textContent = minVal;
        maxLabel.textContent = maxVal;
        const rangeMin = parseInt(minSlider.min);
        const rangeMax = parseInt(minSlider.max);
        const span = rangeMax - rangeMin;
        const leftPct = ((minVal - rangeMin) / span) * 100;
        const rightPct = ((maxVal - rangeMin) / span) * 100;
        track.style.left = leftPct + "%";
        track.style.width = (rightPct - leftPct) + "%";
    }

    minSlider.addEventListener("input", update);
    maxSlider.addEventListener("input", update);
    update();
}

// ─── Stats ────────────────────────────────────────────
async function loadStats() {
    try {
        const r = await fetch("/api/stats");
        const s = await r.json();
        const pills = document.querySelectorAll("#heroStats .stat-pill");
        const values = [
            { num: s.members_total, label: "MCC Members" },
            { num: s.members_with_orcid, label: "ORCID Linked" },
            { num: s.members_with_pi_id, label: "NIH-Funded PIs" },
            { num: (s.publications_total || 0).toLocaleString(), label: "Publications" },
        ];
        pills.forEach((pill, i) => {
            pill.querySelector(".stat-num").textContent = values[i].num;
            pill.querySelector(".stat-label").textContent = values[i].label;
        });
    } catch (e) {
        console.warn("Stats load failed:", e);
    }
}

// ─── Loader helpers ────────────────────────────────────
function showLoading() {
    loadingEl().classList.remove("hidden");
    resultsEl().innerHTML = "";
}
function hideLoading() {
    loadingEl().classList.add("hidden");
}
function showError(msg) {
    hideLoading();
    resultsEl().innerHTML = `<div class="error-banner"><strong>${escapeHtml(msg)}</strong></div>`;
}

// ─── Search: combined researcher profile ────────────────
async function searchResearcher() {
    const name = $("researcherInput").value.trim();
    if (!name) { showError("Please enter a member name."); return; }

    showLoading();
    try {
        const r = await fetch(`/researcher?name=${encodeURIComponent(name)}`);
        const data = await r.json();
        hideLoading();

        if (data.error) { showError(data.error); return; }
        renderResearcher(data);
    } catch (e) {
        showError("Network error: " + e.message);
    }
}

// ─── Search: keyword (with optional researcher filter) ──
async function searchKeyword() {
    const q = $("keywordInput").value.trim();
    if (!q) { showError("Please enter a keyword."); return; }

    const researcherFilter = $("keywordResearcherInput") ? $("keywordResearcherInput").value.trim() : "";
    const minSlider = $("yearMinSlider");
    const maxSlider = $("yearMaxSlider");
    const yearStart = minSlider ? minSlider.value : "";
    const yearEnd = maxSlider ? maxSlider.value : "";

    let url = `/search?q=${encodeURIComponent(q)}`;
    if (yearStart && yearStart !== minSlider.min) url += `&year_start=${encodeURIComponent(yearStart)}`;
    if (yearEnd && yearEnd !== maxSlider.max) url += `&year_end=${encodeURIComponent(yearEnd)}`;
    if (researcherFilter) url += `&researcher=${encodeURIComponent(researcherFilter)}`;

    showLoading();
    try {
        const r = await fetch(url);
        const data = await r.json();
        hideLoading();
        renderKeywordResults(data, q, researcherFilter);
    } catch (e) {
        showError("Network error: " + e.message);
    }
}

// ─── Search: NIH funding by keyword/topic ───────────────
async function searchFundingKeyword() {
    const q = $("fundingKeywordInput").value.trim();
    if (!q) { showError("Please enter a topic or keyword."); return; }

    showLoading();
    try {
        const r = await fetch(`/search_funding_keyword?q=${encodeURIComponent(q)}`);
        const data = await r.json();
        hideLoading();

        if (data.error) { showError(data.error); return; }
        renderFundingKeywordResults(data);
    } catch (e) {
        showError("Network error: " + e.message);
    }
}

// ════════════════════════════════════════════════════════════
// RENDER FUNCTIONS
// ════════════════════════════════════════════════════════════

function renderResearcher(d) {
    let html = renderProfileHeader(d);

    // Publications
    html += `<div class="section-block">`;
    html += `<h2 class="section-title">Publications</h2>`;
    if (d.publications && d.publications.length > 0) {
        html += `<div class="section-meta">${d.publications.length} publication${d.publications.length === 1 ? "" : "s"} found · source: <strong>${labelSource(d.publication_source)}</strong></div>`;
        html += d.publications.map(renderPublicationCard).join("");
    } else {
        html += renderEmpty("No publications found",
            d.orcid ? "We couldn't fetch publications from PubMed. Try again or check the ORCID." : "This member has no ORCID on file. Try the keyword search.");
    }
    html += `</div>`;

    // Funding — show project list without dollar amounts
    html += `<div class="section-block">`;
    html += `<h2 class="section-title">NIH Funding</h2>`;
    if (d.funding) {
        if (d.funding.projects && d.funding.projects.length > 0) {
            html += `<div class="section-meta">${d.funding.total_projects.toLocaleString()} NIH project${d.funding.total_projects === 1 ? "" : "s"} on record · showing ${d.funding.projects.length} most recent</div>`;
            html += d.funding.projects.map(renderFundingCard).join("");
        } else {
            html += renderEmpty("No NIH projects found", "The PI_ID returned zero projects from NIH Reporter.");
        }
    } else {
        html += renderEmpty("No NIH funding data", d.funding_error || "This member has no NIH PI_ID.");
    }
    html += `</div>`;

    resultsEl().innerHTML = html;
}

function renderFundingKeywordResults(data) {
    if (!data.projects || data.projects.length === 0) {
        resultsEl().innerHTML = renderEmpty("No NIH projects match",
            `No MCC-related projects found for "${escapeHtml(data.query)}".`);
        return;
    }
    let html = `<div class="section-block">`;
    html += `<h2 class="section-title">NIH Funding Results</h2>`;
    html += `<div class="section-meta">${data.total_results.toLocaleString()} project${data.total_results === 1 ? "" : "s"} matching "<strong>${escapeHtml(data.query)}</strong>" across MCC members</div>`;
    html += data.projects.map(renderFundingCardKeyword).join("");
    html += `</div>`;
    resultsEl().innerHTML = html;
}

function renderKeywordResults(data, q, researcherFilter) {
    if (!data || data.length === 0) {
        const filterNote = researcherFilter
            ? ` for researcher "${escapeHtml(researcherFilter)}"`
            : "";
        resultsEl().innerHTML = renderEmpty("No publications match",
            `No cached publications mention "${escapeHtml(q)}"${filterNote}. Try a researcher search first to cache their publications.`);
        return;
    }
    let html = `<div class="section-block">`;
    html += `<h2 class="section-title">Keyword Results</h2>`;
    const filterLabel = researcherFilter
        ? ` · filtered by "<strong>${escapeHtml(researcherFilter)}</strong>"`
        : " · all MCC researchers";
    html += `<div class="section-meta">${data.length} match${data.length === 1 ? "" : "es"} for "<strong>${escapeHtml(q)}</strong>"${filterLabel}</div>`;
    html += data.map(renderPublicationCard).join("");
    html += `</div>`;
    resultsEl().innerHTML = html;
}

// ─── Profile header ────────────────────────────────────
function renderProfileHeader(d) {
    let badges = "";
    if (d.pi_id) {
        badges += `<span class="badge badge-amber">NIH Funded</span>`;
    }
    if (d.program) {
        const colorClass = programBadgeClass(d.program);
        badges += `<span class="badge ${colorClass}">${escapeHtml(d.program)}</span>`;
    }

    const nihId = d.pi_id ? String(d.pi_id).replace(/\.0$/, "") : "";
    const nameHtml = d.vivo_url
        ? `<a href="${escapeHtml(d.vivo_url)}" target="_blank" class="profile-name-link">${escapeHtml(d.name || "—")}</a>`
        : escapeHtml(d.name || "—");

    return `
        <div class="profile-header">
            <h2 class="profile-name">${nameHtml} ${badges}</h2>
            <div class="profile-meta">
                ${d.orcid ? `<div><strong>ORCID:</strong> <a href="https://orcid.org/${escapeHtml(d.orcid)}" target="_blank">${escapeHtml(d.orcid)}</a></div>` : ""}
                ${nihId ? `<div><strong>NIH_ID:</strong> ${escapeHtml(nihId)}</div>` : ""}
            </div>
        </div>
    `;
}

// ─── Publication card with MCC author highlighting ──────
function renderPublicationCard(item) {
    const abstractText = (item.abstract || "").trim();
    const trimmedAbs = abstractText.length > 500
        ? abstractText.substring(0, 500) + "…"
        : abstractText;
    const pmid = item.pmid || "";
    const pubmedUrl = pmid ? `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(pmid)}/` : "";
    const titleText = escapeHtml(item.title || "Untitled");

    const authorsHtml = highlightMccAuthors(item.authors || "N/A");

    const cardInner = `
        <h3 class="pub-title-link">${titleText}</h3>
        <div class="meta-line authors-line">
            <strong>Authors:</strong> ${authorsHtml}
        </div>
        <div class="meta-line">
            <strong>Journal:</strong> ${escapeHtml(item.journal || "N/A")} · <strong>Date:</strong> ${escapeHtml(item.pub_date || "N/A")} · <strong>PMID:</strong> ${escapeHtml(pmid)}
        </div>
        ${trimmedAbs ? `<div class="abstract">${escapeHtml(trimmedAbs)}</div>` : ""}
        ${pubmedUrl ? `<div class="doi-link">View on PubMed →</div>` : ""}
    `;

    if (pubmedUrl) {
        return `
            <a class="result-card-link" href="${pubmedUrl}" target="_blank" rel="noopener">
                <div class="result-card pub-card">${cardInner}</div>
            </a>
        `;
    }

    return `<div class="result-card pub-card">${cardInner}</div>`;
}

// ─── Funding card (no dollar amounts) ───────────────────
function renderFundingCard(p) {
    return `
        <div class="result-card funding-card">
            <h4>${escapeHtml(p.title || "Untitled project")}</h4>
            <div class="funding-meta">
                <div><strong>Project #:</strong> ${escapeHtml(p.project_num || "—")}</div>
                <div><strong>Fiscal Year:</strong> ${escapeHtml(String(p.fiscal_year || "—"))}</div>
                <div><strong>Agency:</strong> ${escapeHtml(p.agency || "—")}</div>
                <div><strong>Start:</strong> ${escapeHtml(p.start_date || "—")}</div>
                <div><strong>End:</strong> ${escapeHtml(p.end_date || "—")}</div>
                <div><strong>Org:</strong> ${escapeHtml(p.organization || "—")}</div>
            </div>
        </div>
    `;
}

// ─── Funding card for keyword results (with PI names) ───
function renderFundingCardKeyword(p) {
    return `
        <div class="result-card funding-card">
            <h4>${escapeHtml(p.title || "Untitled project")}</h4>
            <div class="funding-meta">
                <div><strong>PI(s):</strong> ${escapeHtml(p.pi_names || "—")}</div>
                <div><strong>Project #:</strong> ${escapeHtml(p.project_num || "—")}</div>
                <div><strong>Fiscal Year:</strong> ${escapeHtml(String(p.fiscal_year || "—"))}</div>
                <div><strong>Agency:</strong> ${escapeHtml(p.agency || "—")}</div>
                <div><strong>Start:</strong> ${escapeHtml(p.start_date || "—")}</div>
                <div><strong>End:</strong> ${escapeHtml(p.end_date || "—")}</div>
                <div><strong>Org:</strong> ${escapeHtml(p.organization || "—")}</div>
            </div>
        </div>
    `;
}

// ─── MCC author highlighting ────────────────────────────
function highlightMccAuthors(authorsStr) {
    if (!_mccNames.length) return escapeHtml(authorsStr);

    const authors = authorsStr.split(";").map(a => a.trim()).filter(Boolean);
    return authors.map(author => {
        const authorLower = author.toLowerCase().trim();
        const isMcc = _mccNames.some(name => {
            const nameLower = name.toLowerCase();
            return authorLower === nameLower
                || authorLower.includes(nameLower)
                || nameLower.includes(authorLower);
        });
        if (isMcc) {
            return `<span class="mcc-author">${escapeHtml(author)}</span>`;
        }
        return escapeHtml(author);
    }).join("; ");
}

// ─── Program badge color mapping ────────────────────────
function programBadgeClass(program) {
    if (program.includes("Biology")) return "badge-program-cb";
    if (program.includes("Genetics")) return "badge-program-cge";
    if (program.includes("Prevention") || program.includes("Control")) return "badge-program-cpc";
    if (program.includes("Therapeutics")) return "badge-program-ct";
    if (program.includes("Immunology")) return "badge-program-im";
    return "badge-program-default";
}

// ─── Helpers ─────────────────────────────────
function renderEmpty(title, msg) {
    return `
        <div class="empty-state">
            <h3>${escapeHtml(title)}</h3>
            <p>${escapeHtml(msg || "")}</p>
        </div>
    `;
}

function labelSource(src) {
    switch (src) {
        case "orcid": return "live PubMed via ORCID";
        case "cache": return "cached database";
        case "pubmed_name": return "live PubMed by name";
        case "none": return "—";
        default: return src || "—";
    }
}

function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

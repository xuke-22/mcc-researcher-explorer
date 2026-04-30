// ════════════════════════════════════════════════════════════
// MCC Research Explorer — Frontend
// ════════════════════════════════════════════════════════════

const $ = (id) => document.getElementById(id);
const resultsEl = () => $("results");
const loadingEl = () => $("loading");

// ─── On Load ────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    loadStats();
    initEnterKey();
});

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
    bindEnter("fundingNameInput", searchFundingByName);
    bindEnter("fundingPiInput", searchFundingByPiId);
}
function bindEnter(id, fn) {
    const el = $(id);
    if (!el) return;
    el.addEventListener("keypress", (e) => {
        if (e.key === "Enter") fn();
    });
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

// ─── Search: keyword ────────────────────────────────────
async function searchKeyword() {
    const q = $("keywordInput").value.trim();
    if (!q) { showError("Please enter a keyword."); return; }

    showLoading();
    try {
        const r = await fetch(`/search?q=${encodeURIComponent(q)}`);
        const data = await r.json();
        hideLoading();
        renderKeywordResults(data, q);
    } catch (e) {
        showError("Network error: " + e.message);
    }
}

// ─── Search: NIH funding by name ────────────────────────
async function searchFundingByName() {
    const name = $("fundingNameInput").value.trim();
    if (!name) { showError("Please enter a member name."); return; }

    showLoading();
    try {
        const r = await fetch(`/search_funding?name=${encodeURIComponent(name)}`);
        const data = await r.json();
        hideLoading();

        if (data.error) { showError(data.error); return; }
        renderFundingOnly(data);
    } catch (e) {
        showError("Network error: " + e.message);
    }
}

// ─── Search: NIH funding by PI_ID ───────────────────────
async function searchFundingByPiId() {
    const pid = $("fundingPiInput").value.trim();
    if (!pid) { showError("Please enter a PI_ID."); return; }

    showLoading();
    try {
        const r = await fetch(`/search_funding?pi_id=${encodeURIComponent(pid)}`);
        const data = await r.json();
        hideLoading();

        if (data.error) { showError(data.error); return; }
        renderFundingOnly(data);
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

    // Funding
    html += `<div class="section-block">`;
    html += `<h2 class="section-title">NIH Funding</h2>`;
    if (d.funding) {
        html += renderFundingSummary(d.funding);
        if (d.funding.projects && d.funding.projects.length > 0) {
            html += `<div class="section-meta">Showing ${d.funding.projects.length} of ${d.funding.total_projects.toLocaleString()} project${d.funding.total_projects === 1 ? "" : "s"} (most recent first)</div>`;
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

function renderFundingOnly(funding) {
    const fakeProfile = {
        name: funding.name || `NIH_ID: ${funding.pi_id}`,
        pi_id: funding.pi_id,
        orcid: "",
    };
    let html = renderProfileHeader(fakeProfile, /*showOrcid*/ false);
    html += `<div class="section-block">`;
    html += `<h2 class="section-title">NIH Funding</h2>`;
    html += renderFundingSummary(funding);
    if (funding.projects && funding.projects.length > 0) {
        html += `<div class="section-meta">Showing ${funding.projects.length} of ${funding.total_projects.toLocaleString()} project${funding.total_projects === 1 ? "" : "s"} (most recent first)</div>`;
        html += funding.projects.map(renderFundingCard).join("");
    } else {
        html += renderEmpty("No NIH projects found", "The PI_ID returned zero projects from NIH Reporter.");
    }
    html += `</div>`;
    resultsEl().innerHTML = html;
}

function renderKeywordResults(data, q) {
    if (!data || data.length === 0) {
        resultsEl().innerHTML = renderEmpty("No publications match",
            `No cached publications mention "${escapeHtml(q)}". Try a researcher search instead.`);
        return;
    }
    let html = `<div class="section-block">`;
    html += `<h2 class="section-title">Keyword Results</h2>`;
    html += `<div class="section-meta">${data.length} match${data.length === 1 ? "" : "es"} for "<strong>${escapeHtml(q)}</strong>"</div>`;
    html += data.map(renderPublicationCard).join("");
    html += `</div>`;
    resultsEl().innerHTML = html;
}

// ─── Profile header ────────────────────────────────────
function renderProfileHeader(d, showOrcid = true) {
    let badges = "";
    if (showOrcid) {
        badges += d.orcid
            ? `<span class="badge badge-red">ORCID</span>`
            : `<span class="badge badge-gray">No ORCID</span>`;
    }
    badges += d.pi_id
        ? `<span class="badge badge-amber">NIH-Funded</span>`
        : `<span class="badge badge-gray">No NIH_ID</span>`;

    // Strip trailing ".0" from numeric IDs
    const nihId = d.pi_id ? String(d.pi_id).replace(/\.0$/, "") : "";

    return `
        <div class="profile-header">
            <h2 class="profile-name">${escapeHtml(d.name || "—")} ${badges}</h2>
            <div class="profile-meta">
                ${d.orcid ? `<div><strong>ORCID:</strong> <a href="https://orcid.org/${escapeHtml(d.orcid)}" target="_blank">${escapeHtml(d.orcid)}</a></div>` : ""}
                ${nihId ? `<div><strong>NIH_ID:</strong> ${escapeHtml(nihId)}</div>` : ""}
            </div>
        </div>
    `;
}

// ─── Publication card ─────────────────────────────────
function renderPublicationCard(item) {
    const abstractText = (item.abstract || "").trim();
    const trimmedAbs = abstractText.length > 500
        ? abstractText.substring(0, 500) + "…"
        : abstractText;
    const pmid = item.pmid || "";
    const pubmedUrl = pmid ? `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(pmid)}/` : "";
    const titleText = escapeHtml(item.title || "Untitled");

    if (pubmedUrl) {
        return `
            <a class="result-card-link" href="${pubmedUrl}" target="_blank" rel="noopener">
                <div class="result-card pub-card">
                    <h3 class="pub-title-link">${titleText}</h3>
                    <div class="meta-line">
                        <strong>Authors:</strong> ${escapeHtml(item.authors || "N/A")}
                    </div>
                    <div class="meta-line">
                        <strong>Journal:</strong> ${escapeHtml(item.journal || "N/A")} · <strong>Date:</strong> ${escapeHtml(item.pub_date || "N/A")} · <strong>PMID:</strong> ${escapeHtml(pmid)}
                    </div>
                    ${trimmedAbs ? `<div class="abstract">${escapeHtml(trimmedAbs)}</div>` : ""}
                    <div class="doi-link">View on PubMed →</div>
                </div>
            </a>
        `;
    }

    return `
        <div class="result-card pub-card">
            <h3>${titleText}</h3>
            <div class="meta-line">
                <strong>Authors:</strong> ${escapeHtml(item.authors || "N/A")}
            </div>
            <div class="meta-line">
                <strong>Journal:</strong> ${escapeHtml(item.journal || "N/A")} · <strong>Date:</strong> ${escapeHtml(item.pub_date || "N/A")}
            </div>
            ${trimmedAbs ? `<div class="abstract">${escapeHtml(trimmedAbs)}</div>` : ""}
        </div>
    `;
}

// ─── Funding card ─────────────────────────────────
function renderFundingCard(p) {
    const amt = p.award_amount ? `$${Number(p.award_amount).toLocaleString()}` : "—";
    return `
        <div class="result-card funding-card">
            <div class="funding-amount">${amt}</div>
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

// ─── Funding summary ─────────────────────────────────
function renderFundingSummary(f) {
    const totalAmt = f.total_funding
        ? `$${Number(f.total_funding).toLocaleString()}`
        : "—";
    return `
        <div class="funding-summary">
            <div class="summary-card">
                <div class="summary-num">${(f.total_projects || 0).toLocaleString()}</div>
                <div class="summary-label">Total Projects</div>
            </div>
            <div class="summary-card">
                <div class="summary-num">${(f.projects ? f.projects.length : 0).toLocaleString()}</div>
                <div class="summary-label">Recent Shown</div>
            </div>
            <div class="summary-card">
                <div class="summary-num">${totalAmt}</div>
                <div class="summary-label">Funding (Visible)</div>
            </div>
        </div>
    `;
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

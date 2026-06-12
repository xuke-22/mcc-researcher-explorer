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
    bindEnter("fundingPIInput", searchFundingKeyword);
}
function bindEnter(id, fn) {
    const el = $(id);
    if (!el) return;
    el.addEventListener("keypress", (e) => {
        if (e.key === "Enter") fn();
    });
}

// ─── Year Slider (auto-searches on change) ──────────────
let _sliderDebounce = null;

function initYearSlider() {
    const minSlider = $("yearMinSlider");
    const maxSlider = $("yearMaxSlider");
    const minLabel = $("yearMinLabel");
    const maxLabel = $("yearMaxLabel");
    const track = $("sliderTrack");
    if (!minSlider || !maxSlider) return;

    function update(autoSearch) {
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

        if (autoSearch && $("keywordInput").value.trim()) {
            clearTimeout(_sliderDebounce);
            _sliderDebounce = setTimeout(searchKeyword, 400);
        }
    }

    minSlider.addEventListener("input", () => update(true));
    maxSlider.addEventListener("input", () => update(true));
    update(false);
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

// Click handler for match list items
function loadResearcherByName(name) {
    $("researcherInput").value = name;
    searchResearcher();
}

// ─── Search: keyword (live PubMed, with optional researcher filter) ──
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
        if (data.error) { showError(data.error); return; }
        renderKeywordResults(data.publications || [], q, researcherFilter, data.total || 0);
    } catch (e) {
        showError("Network error: " + e.message);
    }
}

// ─── Search: NIH funding by keyword/topic + optional PI filter ──
async function searchFundingKeyword() {
    const q = $("fundingKeywordInput").value.trim();
    const piFilter = $("fundingPIInput") ? $("fundingPIInput").value.trim() : "";

    if (!q && !piFilter) {
        showError("Please enter a keyword, PI name, or both.");
        return;
    }

    showLoading();

    try {
        // Build URL with keyword and optional PI filter
        let url = `/search_funding_keyword?`;
        const params = [];
        if (q) params.push(`q=${encodeURIComponent(q)}`);
        if (piFilter) params.push(`pi=${encodeURIComponent(piFilter)}`);
        url += params.join("&");

        const r = await fetch(url);
        let data = await r.json();

        // Normalize data so the renderer can read the results
        if (data.projects) {
            data.query = data.query || q;
            data.pi_filter = data.pi_filter || piFilter;
            data.total_results = data.total_results ?? data.projects.length;

            data.projects = data.projects.map(p => ({
                ...p,
                award_amount: p.award_amount ?? 0,
                fiscal_year: p.fiscal_year ?? "",
                title: p.title ?? p.project_title ?? "Untitled project",
                organization: p.organization ?? "",
                agency: p.agency ?? "",
                project_num: p.project_num ?? "",
                start_date: p.start_date ?? "",
                end_date: p.end_date ?? "",
                pi_names: p.pi_names || data.pi_name_from_api || data.name || ""
            }));
        }

        hideLoading();

        if (data.error) {
            showError(data.error);
            return;
        }

        renderFundingKeywordResults(data);

    } catch (e) {
        showError("Network error: " + e.message);
    }
}

// ════════════════════════════════════════════════════════════
// RENDER FUNCTIONS
// ════════════════════════════════════════════════════════════

function renderResearcher(d) {
    let html = "";

    // Show all matches if there are multiple
    if (d.all_matches && d.all_matches.length > 1) {
        html += `<div class="match-list">`;
        html += `<div class="match-list-title">${d.all_matches.length} matching members found</div>`;
        html += `<div class="match-list-items">`;
        d.all_matches.forEach(m => {
            const active = m.name === d.name ? " match-item-active" : "";
            const typeLabel = m.match_type === "exact" ? "Exact match"
                : m.match_type === "partial" ? "Partial match" : "Fuzzy match";
            const typeClass = "match-type-" + m.match_type;
            html += `<button class="match-item${active}" onclick="loadResearcherByName('${escapeHtml(m.name).replace(/'/g, "\\'")}')">`;
            html += `<span class="match-item-name">${escapeHtml(m.name)}</span>`;
            html += `<span class="match-type-badge ${typeClass}">${typeLabel}</span>`;
            if (m.program) html += `<span class="badge ${programBadgeClass(m.program)}" style="font-size:10px;margin-left:4px;">${escapeHtml(m.program)}</span>`;
            html += `</button>`;
        });
        html += `</div></div>`;
    }

    html += renderProfileHeader(d);

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
        const queryLabel = data.query ? `"${escapeHtml(data.query)}"` : "";
        const piLabel = data.pi_filter ? ` for PI "${escapeHtml(data.pi_filter)}"` : "";
        resultsEl().innerHTML = renderEmpty("No NIH projects match",
            `No MCC-related projects found${queryLabel ? ` matching ${queryLabel}` : ""}${piLabel}.`);
        return;
    }

    const totalResults = data.total_results ?? data.total_projects ?? data.projects.length ?? 0;
    const queryLabel = data.query || "";
    const piLabel = data.pi_filter || "";

    let metaParts = [];
    metaParts.push(`${totalResults.toLocaleString()} project${totalResults === 1 ? "" : "s"}`);
    if (queryLabel) metaParts.push(`matching "<strong>${escapeHtml(queryLabel)}</strong>"`);
    if (piLabel) metaParts.push(`PI: <strong>${escapeHtml(piLabel)}</strong>`);
    if (!piLabel) metaParts.push("across MCC members");

    let html = `<div class="section-block">`;
    html += `<h2 class="section-title">NIH Funding Results</h2>`;
    html += `<div class="section-meta">${metaParts.join(" · ")}</div>`;
    html += data.projects.map(renderFundingCardKeyword).join("");
    html += `</div>`;
    resultsEl().innerHTML = html;
}
function renderKeywordResults(data, q, researcherFilter, total) {
    if (!data || data.length === 0) {
        const filterNote = researcherFilter
            ? ` for researcher "${escapeHtml(researcherFilter)}"`
            : "";
        resultsEl().innerHTML = renderEmpty("No publications match",
            `No MCC-affiliated publications found for "${escapeHtml(q)}"${filterNote}.`);
        return;
    }
    let html = `<div class="section-block">`;
    html += `<h2 class="section-title">Keyword Results</h2>`;
    const filterLabel = researcherFilter
        ? ` · filtered by "<strong>${escapeHtml(researcherFilter)}</strong>"`
        : "";
    const totalLabel = total > data.length
        ? `Showing ${data.length} of ${total.toLocaleString()} MCC publications`
        : `${data.length} MCC publication${data.length === 1 ? "" : "s"}`;
    html += `<div class="section-meta">${totalLabel} matching "<strong>${escapeHtml(q)}</strong>"${filterLabel} · source: <strong>live PubMed</strong></div>`;
    html += data.map(renderPublicationCard).join("");
    html += `</div>`;
    resultsEl().innerHTML = html;
}

// ─── Profile header ────────────────────────────────────
function renderProfileHeader(d) {
    let badges = "";
    if (d.match_type && d.match_type !== "exact") {
        const mtLabel = d.match_type === "partial" ? "Partial match" : "Fuzzy match";
        const mtClass = "match-type-" + d.match_type;
        badges += `<span class="match-type-badge ${mtClass}" style="margin-left:0;margin-right:6px;">${mtLabel}</span>`;
    }
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

// ─── MCC author highlighting (fuzzy) ────────────────────
let _mccLastNames = {};

function buildMccIndex() {
    if (Object.keys(_mccLastNames).length || !_mccNames.length) return;
    for (const name of _mccNames) {
        const lower = name.toLowerCase().trim();
        let last = "";
        let firsts = "";
        if (lower.includes(",")) {
            const parts = lower.split(",", 2);
            last = parts[0].trim();
            firsts = parts[1].trim();
        } else {
            const parts = lower.split(/\s+/);
            if (parts.length >= 2) {
                last = parts[parts.length - 1];
                firsts = parts.slice(0, -1).join(" ");
            } else {
                last = lower;
            }
        }
        if (!last) continue;
        const entry = firsts;
        if (!_mccLastNames[last]) _mccLastNames[last] = [];
        _mccLastNames[last].push(entry);
        // Index hyphenated parts too (e.g. "cubillos-ruiz" → also index "ruiz")
        if (last.includes("-")) {
            for (const part of last.split("-")) {
                if (part.length >= 2) {
                    if (!_mccLastNames[part]) _mccLastNames[part] = [];
                    _mccLastNames[part].push(entry);
                }
            }
        }
    }
}

function _matchFirstName(authorFirst, candidateFirsts) {
    if (!candidateFirsts) return true;
    if (!authorFirst) return false;
    const cFirst = candidateFirsts.split(/\s+/)[0];
    // If author first name is just 1 char (initial), accept initial match
    if (authorFirst.length === 1) {
        return authorFirst.charAt(0) === cFirst.charAt(0);
    }
    // Otherwise require first 2 chars to match (prevents Allison/Aaron false positives)
    if (authorFirst.length >= 2 && cFirst.length >= 2) {
        if (authorFirst.substring(0, 2) === cFirst.substring(0, 2)) return true;
    }
    if (authorFirst.startsWith(cFirst) || cFirst.startsWith(authorFirst)) return true;
    return false;
}

function isMccAuthor(authorStr) {
    buildMccIndex();
    const author = authorStr.toLowerCase().trim();
    const parts = author.split(/\s+/);
    if (parts.length < 2) return false;

    const firstPart = parts[0];

    // Try last name as last token, last two tokens (compound), with/without hyphens
    for (let n = 1; n <= Math.min(3, parts.length - 1); n++) {
        const lastName = parts.slice(parts.length - n).join(" ");
        const firstParts = parts.slice(0, parts.length - n).join(" ");
        const candidates = _mccLastNames[lastName];
        if (candidates) {
            for (const cFirsts of candidates) {
                if (_matchFirstName(firstParts.split(/\s+/)[0], cFirsts)) return true;
            }
        }
        // Also try hyphenated version for compound names ("de stanchina" → "de-stanchina" not likely, but "cubillos ruiz" → "cubillos-ruiz")
        if (n > 1) {
            const hyphenated = parts.slice(parts.length - n).join("-");
            const hCandidates = _mccLastNames[hyphenated];
            if (hCandidates) {
                for (const cFirsts of hCandidates) {
                    if (_matchFirstName(firstParts.split(/\s+/)[0], cFirsts)) return true;
                }
            }
        }
    }
    return false;
}

function highlightMccAuthors(authorsStr) {
    if (!_mccNames.length) return escapeHtml(authorsStr);

    const authors = authorsStr.split(";").map(a => a.trim()).filter(Boolean);
    return authors.map(author => {
        if (isMccAuthor(author)) {
            return `<span class="mcc-author">${escapeHtml(author)}</span>`;
        }
        return escapeHtml(author);
    }).join("; ");
}

// ─── Program badge color mapping ────────────────────────
function programBadgeClass(program) {
    switch (program) {
        case "CB":  return "badge-program-cb";
        case "CGE": return "badge-program-cge";
        case "CPC": return "badge-program-cpc";
        case "CT":  return "badge-program-ct";
        case "ZY":  return "badge-program-zy";
        case "MCC": return "badge-program-default";
        default:    return "badge-program-default";
    }
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
// ════════════════════════════════════════════════════════════════════
// Click tracking
// ════════════════════════════════════════════════════════════════════
function trackClick(elementName) {
    fetch("/api/track-click", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            element: elementName,
            page: window.location.pathname
        })
    }).catch(error => {
        console.error("Click tracking error:", error);
    });
}

document.addEventListener("click", function(event) {
    const target = event.target.closest("button, a, input[type='submit']");

    if (!target) return;

    let elementName =
        target.getAttribute("data-track") ||
        target.innerText ||
        target.value ||
        target.href ||
        "unknown_click";

    elementName = elementName.trim().substring(0, 100);

    trackClick(elementName);
});

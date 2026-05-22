"""
MCC Research Explorer — Flask Backend
======================================
Endpoints:
  GET /                         → main page
  GET /search?q=keyword         → keyword search across cached publications
  GET /search_name?name=...     → researcher lookup (ORCID + PI_ID + publications)
  GET /search_funding?pi_id=... → NIH Reporter funding by PI_ID
  GET /search_funding?name=...  → NIH Reporter funding by researcher name
  GET /researcher?name=...      → combined: publications + funding
  GET /api/members              → list all members (for autocomplete)

Data sources:
  - Local SQLite (capstone.db): cached publications + NIH projects
  - PubMed E-Utilities (live, by ORCID)
  - NIH Reporter API (live, by PI_ID)
  - RePORTER_PI_IDS_FY2025.xlsx Sheet2: member roster (name, PI_ID, ORCID)
"""

import os
import sys
import sqlite3
import json
import time
import urllib.request
import urllib.parse
import ssl
import xml.etree.ElementTree as ET

import pandas as pd
import openpyxl
import requests
from flask import Flask, jsonify, render_template, request

# Allow `lib/` modules to be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

# ─── Paths ──────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "capstone.db")
PI_EXCEL = os.path.join(BASE_DIR, "data", "RePORTER_PI_IDS_FY2025.xlsx")
MEMBERS_EXCEL = os.path.join(BASE_DIR, "data", "MCC_Members_Jan_2026.xlsx")
VIVO_EXCEL = os.path.join(BASE_DIR, "data", "All_MCC_Members_FY25.xlsx")

# Tell capstone4 (and any descendant) which DB to use, before importing
os.environ.setdefault("CAPSTONE_DB", DB_PATH)

from capstone4 import lookup_publications  # noqa: E402

# ─── Constants ──────────────────────────────────────────────────────
NIH_API = "https://api.reporter.nih.gov/v2/projects/search"
SSL_CTX = ssl.create_default_context()

app = Flask(__name__, static_folder="static", template_folder="templates")


# ════════════════════════════════════════════════════════════════════
# Member roster (from Excel Sheet2)
# ════════════════════════════════════════════════════════════════════
_MEMBERS_CACHE = None

PROGRAM_MAP = {
    "Meyer Cancer Center: CB": "CB",
    "Meyer Cancer Center: CGE": "CGE",
    "Meyer Cancer Center: CPC": "CPC",
    "Meyer Cancer Center: CT": "CT",
    "Meyer Cancer Center: ZY": "ZY",
    "Meyer Cancer Center": "MCC",
}


def _load_program_data():
    """Load program affiliations from MCC Members and VIVO links from the FY25 roster."""
    program_map = {}
    vivo_map = {}

    # Program affiliations + email-based VIVO fallback from MCC Members Jan 2026
    try:
        mdf = pd.read_excel(MEMBERS_EXCEL)
        mdf = mdf.fillna("")
        for _, row in mdf.iterrows():
            name = str(row.get("Name", "")).strip()
            if not name:
                continue
            prog_raw = str(row.get("Center: Program Area", "")).strip()
            program_map[name.lower()] = PROGRAM_MAP.get(prog_raw, prog_raw)
            email = str(row.get("Contact: Email", "")).strip()
            if email and "@" in email:
                cwid = email.split("@")[0]
                vivo_map.setdefault(name.lower(),
                                    f"https://vivo.weill.cornell.edu/display/cwid-{cwid}")
    except Exception as e:
        print(f"[WARN] Could not load MCC members file: {e}")

    # VIVO links from FY25 roster (uses CWID column + hyperlinks)
    try:
        wb = openpyxl.load_workbook(VIVO_EXCEL, data_only=True)

        # Research Program members sheet has CWID + Name + Program
        ws = wb["Research Program members"]
        for row_cells in ws.iter_rows(min_row=2, max_col=3):
            cwid_cell, name_cell, prog_cell = row_cells[0], row_cells[1], row_cells[2]
            name = str(name_cell.value or "").strip()
            cwid = str(cwid_cell.value or "").strip()
            if name and cwid:
                vivo_map[name.lower()] = f"https://vivo.weill.cornell.edu/display/cwid-{cwid}"

        # ZY-clinical members sheet (Name column only, some have hyperlinks)
        ws2 = wb["ZY-clinical members"]
        for row_cells in ws2.iter_rows(min_row=2, max_col=1):
            cell = row_cells[0]
            name = str(cell.value or "").strip()
            if not name:
                continue
            if cell.hyperlink and cell.hyperlink.target and "vivo.weill.cornell.edu" in cell.hyperlink.target:
                vivo_map[name.lower()] = cell.hyperlink.target

        wb.close()
    except Exception as e:
        print(f"[WARN] Could not load VIVO roster: {e}")

    return program_map, vivo_map


_PROGRAM_CACHE = None
_VIVO_CACHE = None


def _get_program_and_vivo():
    global _PROGRAM_CACHE, _VIVO_CACHE
    if _PROGRAM_CACHE is None:
        _PROGRAM_CACHE, _VIVO_CACHE = _load_program_data()
    return _PROGRAM_CACHE, _VIVO_CACHE


def _fuzzy_lookup(data_map, name):
    """Match by exact key first, then by last name + first-name prefix."""
    key = name.lower()
    if key in data_map:
        return data_map[key]

    if "," not in key:
        return ""
    last, first = key.split(",", 1)
    last = last.strip()
    first = first.strip().split()[0] if first.strip() else ""

    for map_key, val in data_map.items():
        if "," not in map_key:
            continue
        m_last, m_first = map_key.split(",", 1)
        m_last = m_last.strip()
        m_first = m_first.strip().split()[0] if m_first.strip() else ""
        if last == m_last and first and m_first and (
            first.startswith(m_first) or m_first.startswith(first)
        ):
            return val

    return ""


def load_members():
    """Load all MCC members from Sheet2 of the Excel file."""
    global _MEMBERS_CACHE
    if _MEMBERS_CACHE is not None:
        return _MEMBERS_CACHE

    df = pd.read_excel(PI_EXCEL, sheet_name="Sheet2")
    df = df.fillna("")
    program_data, vivo_data = _get_program_and_vivo()
    members = []
    for _, row in df.iterrows():
        name = str(row.get("PI_NAMEs", "")).strip()
        if not name or name.lower() == "nan":
            continue
        pi_id = str(row.get("PI_IDS", "")).strip()
        orcid = str(row.get("ORCID", "")).strip()
        pub_count = row.get("PUB_COUNT", "")
        program = _fuzzy_lookup(program_data, name)
        vivo_url = _fuzzy_lookup(vivo_data, name)
        members.append({
            "name": name,
            "pi_id": "" if pi_id.lower() == "nan" else pi_id,
            "orcid": "" if orcid.lower() == "nan" else orcid,
            "pub_count": pub_count if pub_count != "" else None,
            "program": program,
            "vivo_url": vivo_url,
        })
    _MEMBERS_CACHE = members
    return members


def find_member_by_name(query: str):
    """Case-insensitive partial name match. Returns first match or None."""
    if not query:
        return None
    q = query.strip().lower()
    members = load_members()

    # Exact match first
    for m in members:
        if m["name"].lower() == q:
            return m

    # Then partial match
    for m in members:
        if q in m["name"].lower():
            return m

    # Try matching last-name only
    for m in members:
        parts = m["name"].split(",")
        last = parts[0].strip().lower() if parts else ""
        if last == q or q in last:
            return m

    return None


def find_members_by_query(query: str, limit: int = 20):
    """Return all members matching the query."""
    if not query:
        return []
    q = query.strip().lower()
    members = load_members()
    matches = [m for m in members if q in m["name"].lower()]
    return matches[:limit]


def find_member_by_pi_id(pi_id):
    """Reverse lookup: given a PI_ID, return the matching member (or None)."""
    if pi_id in (None, ""):
        return None
    target = str(pi_id).strip().rstrip(".0").rstrip(".")
    for m in load_members():
        mid = str(m.get("pi_id") or "").strip().rstrip(".0").rstrip(".")
        if mid and mid == target:
            return m
    return None


# ════════════════════════════════════════════════════════════════════
# Database helpers
# ════════════════════════════════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ════════════════════════════════════════════════════════════════════
# PubMed lookup helpers
# ════════════════════════════════════════════════════════════════════
def pubmed_search_by_name(name: str, retmax: int = 30):
    """Live search PubMed by researcher name. Returns list of articles."""
    # Convert "LAST, FIRST" → "First Last"
    if "," in name:
        last, first = [s.strip() for s in name.split(",", 1)]
        first = first.split()[0] if first else ""
        query = f'{last} {first}[Author]'
    else:
        query = f'{name}[Author]'

    esearch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {"db": "pubmed", "term": query, "retmax": retmax, "retmode": "json"}
    r = requests.get(esearch, params=params, timeout=30)
    r.raise_for_status()
    pmids = r.json().get("esearchresult", {}).get("idlist", [])

    if not pmids:
        return []

    efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    r2 = requests.get(efetch, params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}, timeout=60)
    r2.raise_for_status()
    return _parse_pubmed_xml(r2.text)


def pubmed_search_by_keyword(keyword: str, researcher: str = "",
                             year_start: str = "", year_end: str = "",
                             retmax: int = 50):
    """Live PubMed keyword search scoped to Meyer Cancer Center / Weill Cornell."""
    affil = '("Meyer Cancer Center"[Affiliation] OR "Weill Cornell"[Affiliation])'
    term = f"({keyword}) AND {affil}"

    if researcher:
        if "," in researcher:
            last, first = [s.strip() for s in researcher.split(",", 1)]
            first = first.split()[0] if first else ""
            term += f' AND {last} {first}[Author]'
        else:
            term += f" AND {researcher}[Author]"

    if year_start or year_end:
        mindate = year_start or "2000"
        maxdate = year_end or "2026"
        term += f" AND {mindate}[PDAT]:{maxdate}[PDAT]"

    esearch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {"db": "pubmed", "term": term, "retmax": retmax, "retmode": "json",
              "sort": "date"}
    r = requests.get(esearch, params=params, timeout=30)
    r.raise_for_status()
    data = r.json().get("esearchresult", {})
    pmids = data.get("idlist", [])
    total = int(data.get("count", 0))

    if not pmids:
        return [], total

    efetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    r2 = requests.get(efetch, params={"db": "pubmed", "id": ",".join(pmids),
                                       "retmode": "xml"}, timeout=60)
    r2.raise_for_status()
    return _parse_pubmed_xml(r2.text), total


_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _extract_date(article_el):
    """Return (display_date, iso_date) for sorting + UI display."""
    import re

    def _txt(parent, tag):
        if parent is None:
            return ""
        el = parent.find(tag)
        return (el.text or "").strip() if el is not None else ""

    # Prefer ArticleDate (electronic pub date)
    date_el = article_el.find(".//ArticleDate")
    if date_el is not None:
        y, m, d = _txt(date_el, "Year"), _txt(date_el, "Month"), _txt(date_el, "Day")
        if y:
            mm = _MONTHS.get(m, m).zfill(2) if m else "01"
            dd = d.zfill(2) if d else "01"
            return (f"{y}-{mm}-{dd}", f"{y}-{mm}-{dd}")

    # Fall back to PubDate
    pd_el = article_el.find(".//PubDate")
    if pd_el is not None:
        y = _txt(pd_el, "Year")
        m = _txt(pd_el, "Month")
        medline = _txt(pd_el, "MedlineDate")
        if y:
            mm = _MONTHS.get(m, m).zfill(2) if m else "01"
            return (f"{y}{(' ' + m) if m else ''}", f"{y}-{mm}-01")
        if medline:
            yr = re.match(r"\d{4}", medline)
            return (medline, f"{yr.group(0)}-01-01" if yr else "")

    return ("", "")


def _parse_pubmed_xml(xml_text: str):
    root = ET.fromstring(xml_text)
    out = []
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID", default="")
        title = "".join((art.find(".//ArticleTitle") or ET.Element("x")).itertext())
        abstract_parts = art.findall(".//Abstract/AbstractText")
        abstract = " ".join("".join(a.itertext()) for a in abstract_parts)
        journal = art.findtext(".//Journal/Title", default="")
        pub_date, iso_date = _extract_date(art)
        authors = []
        for au in art.findall(".//Author"):
            ln = au.findtext("LastName", default="")
            fn = au.findtext("ForeName", default="")
            if ln:
                authors.append(f"{fn} {ln}".strip())
        doi = ""
        for aid in art.findall(".//ArticleId"):
            if aid.attrib.get("IdType") == "doi":
                doi = aid.text or ""
                break
        out.append({
            "pmid": pmid, "title": title, "abstract": abstract,
            "authors": "; ".join(authors), "journal": journal,
            "pub_date": pub_date, "iso_date": iso_date, "doi": doi,
        })
    # Sort newest first
    out.sort(key=lambda p: (p.get("iso_date") or "", p.get("pmid") or ""), reverse=True)
    return out


# ════════════════════════════════════════════════════════════════════
# NIH Reporter lookup
# ════════════════════════════════════════════════════════════════════
def fetch_nih_funding_by_pi_id(pi_id: int, limit: int = 100):
    """Live query NIH Reporter API for projects by PI profile ID."""
    payload = {
        "criteria": {"pi_profile_ids": [int(pi_id)]},
        "include_fields": [
            "ApplId", "FiscalYear", "ProjectNum", "ProjectTitle",
            "ProjectStartDate", "ProjectEndDate", "AwardAmount",
            "AgencyIcAdmin", "Organization", "PrincipalInvestigators",
            "AbstractText", "Terms",
        ],
        "offset": 0,
        "limit": limit,
        "sort_field": "fiscal_year",
        "sort_order": "desc",
    }
    r = requests.post(NIH_API, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    total = (data.get("meta") or {}).get("total", 0)

    projects = []
    total_funding = 0
    pi_name_from_api = ""
    for p in results:
        amount = p.get("award_amount") or 0
        try:
            total_funding += int(amount)
        except (ValueError, TypeError):
            pass
        org = (p.get("organization") or {}).get("org_name", "") or ""
        agency = (p.get("agency_ic_admin") or {}).get("name", "") or ""

        # Pull PI name from the first matching record's principal_investigators
        if not pi_name_from_api:
            for pi in (p.get("principal_investigators") or []):
                if isinstance(pi, dict) and (pi.get("profile_id") == pi_id or
                                             str(pi.get("profile_id") or "") == str(pi_id)):
                    pi_name_from_api = pi.get("full_name") or ""
                    if pi_name_from_api:
                        break
            # Fall back to first contact PI if no exact match found
            if not pi_name_from_api:
                for pi in (p.get("principal_investigators") or []):
                    if isinstance(pi, dict) and pi.get("is_contact_pi"):
                        pi_name_from_api = pi.get("full_name") or ""
                        if pi_name_from_api:
                            break

        projects.append({
            "appl_id": p.get("appl_id"),
            "fiscal_year": p.get("fiscal_year"),
            "project_num": p.get("project_num"),
            "title": p.get("project_title"),
            "start_date": p.get("project_start_date"),
            "end_date": p.get("project_end_date"),
            "award_amount": amount,
            "agency": agency,
            "organization": org,
        })

    return {
        "pi_id": pi_id,
        "pi_name_from_api": pi_name_from_api,
        "total_projects": total,
        "total_funding": total_funding,
        "projects": projects,
    }


# ════════════════════════════════════════════════════════════════════
# Routes
# ════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/members")
def api_members():
    """Return member list for autocomplete."""
    q = request.args.get("q", "").strip()
    if q:
        members = find_members_by_query(q, limit=20)
    else:
        members = load_members()
    return jsonify(members)


@app.route("/api/stats")
def api_stats():
    """Quick database stats for the dashboard."""
    conn = get_db()
    pi_n = conn.execute("SELECT COUNT(*) FROM pi").fetchone()[0]
    proj_n = conn.execute("SELECT COUNT(*) FROM project").fetchone()[0]
    pub_n = conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0]
    conn.close()

    members = load_members()
    members_with_orcid = sum(1 for m in members if m["orcid"])
    members_with_pi_id = sum(1 for m in members if m["pi_id"])

    # Total publications across all MCC members (from Sheet2 PUB_COUNT)
    total_pubs = 0
    for m in members:
        try:
            n = m.get("pub_count")
            if n is not None and n != "":
                total_pubs += int(float(n))
        except (ValueError, TypeError):
            continue

    return jsonify({
        "members_total": len(members),
        "members_with_orcid": members_with_orcid,
        "members_with_pi_id": members_with_pi_id,
        "projects_indexed": proj_n,
        "publications_total": total_pubs,
        "publications_cached": pub_n,
        "pis_in_db": pi_n,
    })


@app.route("/api/mcc_names")
def api_mcc_names():
    """Return all MCC member names for author highlighting."""
    members = load_members()
    names = []
    for m in members:
        raw = m["name"]
        names.append(raw)
        if "," in raw:
            last, first = [s.strip() for s in raw.split(",", 1)]
            first_short = first.split()[0] if first else ""
            if first_short:
                names.append(f"{first_short} {last}")
                names.append(f"{first} {last}")
    return jsonify(sorted(set(names)))


@app.route("/search")
def search_keyword():
    """Live PubMed keyword search scoped to MCC/Weill Cornell affiliations."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"publications": [], "total": 0})

    year_start = request.args.get("year_start", "").strip()
    year_end = request.args.get("year_end", "").strip()
    researcher = request.args.get("researcher", "").strip()

    try:
        pubs, total = pubmed_search_by_keyword(
            q, researcher=researcher,
            year_start=year_start, year_end=year_end,
            retmax=50,
        )
    except Exception as e:
        return jsonify({"error": f"PubMed search failed: {e}",
                        "publications": [], "total": 0})

    return jsonify({"publications": pubs, "total": total})


@app.route("/search_name")
def search_name():
    """Look up a researcher by name → return profile + publications."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"error": "Please provide a name."})

    member = find_member_by_name(name)
    if not member:
        return jsonify({"error": f"No MCC member found matching '{name}'."})

    publications = []
    source = "none"

    # Strategy 1: ORCID-based live lookup (best quality)
    if member["orcid"]:
        try:
            result = lookup_publications(member["orcid"], save_to_db=True)
            publications = result.get("publications", [])
            source = "orcid"
        except Exception as e:
            print(f"[WARN] ORCID lookup failed for {name}: {e}")

    # Strategy 2: Cached database lookup (by ORCID)
    if not publications and member["orcid"]:
        conn = get_db()
        rows = conn.execute("""
            SELECT pmid, title, abstract, authors, journal, pub_date, doi
            FROM publication WHERE orcid = ?
            ORDER BY pub_date DESC LIMIT 50
        """, (member["orcid"],)).fetchall()
        conn.close()
        publications = [dict(r) for r in rows]
        source = "cache"

    # Strategy 3: Live PubMed by name (fallback when no ORCID)
    if not publications:
        try:
            publications = pubmed_search_by_name(member["name"], retmax=20)
            source = "pubmed_name"
        except Exception as e:
            print(f"[WARN] Name search failed: {e}")

    return jsonify({
        "name": member["name"],
        "orcid": member["orcid"],
        "pi_id": member["pi_id"],
        "program": member.get("program", ""),
        "vivo_url": member.get("vivo_url", ""),
        "publication_count": len(publications),
        "source": source,
        "publications": publications,
    })


@app.route("/search_funding")
def search_funding():
    """Look up NIH funding by PI_ID or by member name."""
    pi_id = request.args.get("pi_id", "").strip()
    name = request.args.get("name", "").strip()

    member = None
    if name:
        member = find_member_by_name(name)
        if not member:
            return jsonify({"error": f"No MCC member found matching '{name}'."})
        if not member["pi_id"]:
            return jsonify({
                "error": f"{member['name']} has no NIH PI_ID — no NIH funding records to look up.",
                "name": member["name"],
            })
        pi_id = member["pi_id"]

    if not pi_id:
        return jsonify({"error": "Provide either pi_id or name."})

    try:
        pi_id_int = int(float(pi_id))
    except (ValueError, TypeError):
        return jsonify({"error": f"Invalid PI_ID: {pi_id}"})

    try:
        funding = fetch_nih_funding_by_pi_id(pi_id_int, limit=100)
    except Exception as e:
        return jsonify({"error": f"NIH Reporter API error: {e}"})

    # Resolve a researcher name in this priority order:
    #   1. The member typed by the user (already validated above)
    #   2. Local MCC roster lookup by NIH_ID
    #   3. PI name returned in the NIH RePORTER project data itself
    #   4. Empty (frontend will fall back to "NIH_ID: ...")
    resolved_name = ""
    resolved_orcid = ""
    if member:
        resolved_name = member["name"]
        resolved_orcid = member.get("orcid", "")
    else:
        roster_match = find_member_by_pi_id(pi_id_int)
        if roster_match:
            resolved_name = roster_match["name"]
            resolved_orcid = roster_match.get("orcid", "")
        else:
            resolved_name = funding.get("pi_name_from_api", "")

    funding["name"] = resolved_name
    funding["orcid"] = resolved_orcid
    return jsonify(funding)


@app.route("/researcher")
def researcher_combined():
    """Combined view: publications + NIH funding for one member."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"error": "Please provide a name."})

    member = find_member_by_name(name)
    if not member:
        return jsonify({"error": f"No MCC member found matching '{name}'."})

    response = {
        "name": member["name"],
        "orcid": member["orcid"],
        "pi_id": member["pi_id"],
        "program": member.get("program", ""),
        "vivo_url": member.get("vivo_url", ""),
        "publications": [],
        "publication_count": 0,
        "publication_source": "none",
        "funding": None,
        "funding_error": None,
    }

    # Publications
    if member["orcid"]:
        try:
            r = lookup_publications(member["orcid"], save_to_db=True)
            response["publications"] = r.get("publications", [])
            response["publication_count"] = len(response["publications"])
            response["publication_source"] = "orcid"
        except Exception as e:
            response["publication_source"] = f"orcid_error: {e}"

    if not response["publications"]:
        try:
            response["publications"] = pubmed_search_by_name(member["name"], retmax=15)
            response["publication_count"] = len(response["publications"])
            response["publication_source"] = "pubmed_name"
        except Exception as e:
            response["publication_source"] = f"name_error: {e}"

    # Funding
    if member["pi_id"]:
        try:
            response["funding"] = fetch_nih_funding_by_pi_id(int(float(member["pi_id"])), limit=50)
        except Exception as e:
            response["funding_error"] = str(e)
    else:
        response["funding_error"] = "No NIH PI_ID on file for this member."

    return jsonify(response)


@app.route("/search_funding_keyword")
def search_funding_keyword():
    """Search NIH funding by keyword/topic across all MCC members."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Please provide a search term."})

    members = load_members()
    pi_ids = [int(float(m["pi_id"])) for m in members if m["pi_id"]]

    if not pi_ids:
        return jsonify({"error": "No MCC members with NIH PI_IDs found."})

    payload = {
        "criteria": {
            "pi_profile_ids": pi_ids,
            "advanced_text_search": {
                "operator": "and",
                "search_field": "projecttitle,terms,abstracttext",
                "search_text": q,
            },
        },
        "include_fields": [
            "ApplId", "FiscalYear", "ProjectNum", "ProjectTitle",
            "ProjectStartDate", "ProjectEndDate",
            "AgencyIcAdmin", "Organization", "PrincipalInvestigators",
            "AbstractText", "Terms",
        ],
        "offset": 0,
        "limit": 50,
        "sort_field": "fiscal_year",
        "sort_order": "desc",
    }
    try:
        r = requests.post(NIH_API, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return jsonify({"error": f"NIH Reporter API error: {e}"})

    results = data.get("results") or []
    total = (data.get("meta") or {}).get("total", 0)

    projects = []
    for p in results:
        org = (p.get("organization") or {}).get("org_name", "") or ""
        agency = (p.get("agency_ic_admin") or {}).get("name", "") or ""
        pis = p.get("principal_investigators") or []
        pi_names = [pi.get("full_name", "") for pi in pis if isinstance(pi, dict) and pi.get("full_name")]
        projects.append({
            "appl_id": p.get("appl_id"),
            "fiscal_year": p.get("fiscal_year"),
            "project_num": p.get("project_num"),
            "title": p.get("project_title"),
            "start_date": p.get("project_start_date"),
            "end_date": p.get("project_end_date"),
            "agency": agency,
            "organization": org,
            "pi_names": "; ".join(pi_names),
        })

    return jsonify({
        "query": q,
        "total_results": total,
        "projects": projects,
    })


# ════════════════════════════════════════════════════════════════════
# Run
# ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"[INFO] DB:    {DB_PATH}")
    print(f"[INFO] Excel: {PI_EXCEL}")
    print(f"[INFO] Loaded {len(load_members())} MCC members")
    port = int(os.environ.get("PORT", 5001))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)

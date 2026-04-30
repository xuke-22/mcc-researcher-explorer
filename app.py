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
import requests
from flask import Flask, jsonify, render_template, request

# Allow `lib/` modules to be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

# ─── Paths ──────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "capstone.db")
PI_EXCEL = os.path.join(BASE_DIR, "data", "RePORTER_PI_IDS_FY2025.xlsx")

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


def load_members():
    """Load all MCC members from Sheet2 of the Excel file."""
    global _MEMBERS_CACHE
    if _MEMBERS_CACHE is not None:
        return _MEMBERS_CACHE

    df = pd.read_excel(PI_EXCEL, sheet_name="Sheet2")
    df = df.fillna("")
    members = []
    for _, row in df.iterrows():
        name = str(row.get("PI_NAMEs", "")).strip()
        if not name or name.lower() == "nan":
            continue
        pi_id = str(row.get("PI_IDS", "")).strip()
        orcid = str(row.get("ORCID", "")).strip()
        pub_count = row.get("PUB_COUNT", "")
        members.append({
            "name": name,
            "pi_id": "" if pi_id.lower() == "nan" else pi_id,
            "orcid": "" if orcid.lower() == "nan" else orcid,
            "pub_count": pub_count if pub_count != "" else None,
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


def _parse_pubmed_xml(xml_text: str):
    root = ET.fromstring(xml_text)
    out = []
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID", default="")
        title = "".join((art.find(".//ArticleTitle") or ET.Element("x")).itertext())
        abstract_parts = art.findall(".//Abstract/AbstractText")
        abstract = " ".join("".join(a.itertext()) for a in abstract_parts)
        journal = art.findtext(".//Journal/Title", default="")
        year = art.findtext(".//PubDate/Year", default="") or art.findtext(".//PubDate/MedlineDate", default="")
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
            "pub_date": year, "doi": doi,
        })
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
    for p in results:
        amount = p.get("award_amount") or 0
        try:
            total_funding += int(amount)
        except (ValueError, TypeError):
            pass
        org = (p.get("organization") or {}).get("org_name", "") or ""
        agency = (p.get("agency_ic_admin") or {}).get("name", "") or ""
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

    return jsonify({
        "members_total": len(members),
        "members_with_orcid": members_with_orcid,
        "members_with_pi_id": members_with_pi_id,
        "projects_indexed": proj_n,
        "publications_cached": pub_n,
        "pis_in_db": pi_n,
    })


@app.route("/search")
def search_keyword():
    """Keyword search across cached publications."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    conn = get_db()
    rows = conn.execute("""
        SELECT pmid, title, abstract, authors, journal, pub_date, doi, orcid
        FROM publication
        WHERE title LIKE ? OR abstract LIKE ?
        ORDER BY pub_date DESC
        LIMIT 50
    """, (f"%{q}%", f"%{q}%")).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


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

    funding["name"] = member["name"] if member else ""
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

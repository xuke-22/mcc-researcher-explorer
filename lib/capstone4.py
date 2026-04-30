"""
capstone4.py — Fetch PubMed publications by ORCID
==================================================
Flow:
  1. Accept an ORCID ID
  2. Query ORCID public API to get the person's works (DOIs / PubMed IDs)
  3. Fetch full article metadata (title, abstract, authors, journal, date)
     from PubMed via NCBI E-utilities
  4. Store results in SQLite (publication table)
  5. Return structured results

APIs used:
  - ORCID public API: https://pub.orcid.org/v3.0/{orcid}/works
  - PubMed E-utilities:
      esearch: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
      efetch:  https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi
"""

import os
import time
import sqlite3
import urllib.request
import urllib.parse
import json
import ssl
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional


# Resolve DB path:
#   1. CAPSTONE_DB env var (set by web app or caller)
#   2. ../data/capstone.db relative to this file (website layout)
#   3. ./capstone.db (legacy CLI usage)
def _resolve_db_path() -> str:
    env = os.environ.get("CAPSTONE_DB")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, "..", "data", "capstone.db")
    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    return "capstone.db"


DB_PATH = _resolve_db_path()
ORCID_API = "https://pub.orcid.org/v3.0"
PUBMED_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

SSL_CTX = ssl.create_default_context()


# ─── DB setup ────────────────────────────────────────────────

def init_publication_table(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS publication (
        pmid         TEXT PRIMARY KEY,
        orcid        TEXT,
        title        TEXT,
        abstract     TEXT,
        authors      TEXT,
        journal      TEXT,
        pub_date     TEXT,
        doi          TEXT,
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()


def save_publications(pubs: List[Dict], orcid: str, db_path: str = DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    saved = 0
    for p in pubs:
        conn.execute("""
        INSERT INTO publication (pmid, orcid, title, abstract, authors, journal, pub_date, doi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pmid) DO UPDATE SET
            orcid    = excluded.orcid,
            title    = excluded.title,
            abstract = excluded.abstract,
            authors  = excluded.authors,
            journal  = excluded.journal,
            pub_date = excluded.pub_date,
            doi      = excluded.doi
        """, (
            p["pmid"], orcid, p["title"], p["abstract"],
            p["authors"], p["journal"], p["pub_date"], p.get("doi", ""),
        ))
        saved += 1
    conn.commit()
    conn.close()
    return saved


# ─── ORCID API ───────────────────────────────────────────────

def fetch_orcid_profile(orcid: str) -> Dict:
    """Get name and affiliation from ORCID profile."""
    url = f"{ORCID_API}/{orcid}/record"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=15)
    data = json.loads(resp.read())

    person = data.get("person", {})
    name_data = person.get("name") or {}
    given = (name_data.get("given-names") or {}).get("value", "")
    family = (name_data.get("family-name") or {}).get("value", "")

    activities = data.get("activities-summary", {})
    employments = activities.get("employments", {}).get("affiliation-group", [])
    orgs = []
    for eg in employments:
        for s in eg.get("summaries", []):
            org = s.get("employment-summary", {}).get("organization", {}).get("name", "")
            if org:
                orgs.append(org)

    return {"given": given, "family": family, "orgs": list(set(orgs))}


def fetch_orcid_pmids(orcid: str) -> List[str]:
    """Get PubMed IDs linked to an ORCID profile via works."""
    url = f"{ORCID_API}/{orcid}/works"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=30)
    data = json.loads(resp.read())

    pmids = set()
    dois = set()
    for group in data.get("group", []):
        for summary in group.get("work-summary", []):
            ext_ids = summary.get("external-ids", {}).get("external-id", [])
            for eid in ext_ids:
                id_type = eid.get("external-id-type", "")
                id_val = eid.get("external-id-value", "")
                if id_type == "pmid":
                    pmids.add(id_val)
                elif id_type == "doi":
                    dois.add(id_val)

    # Also search PubMed by ORCID to catch papers not listed in ORCID works
    search_url = f"{PUBMED_SEARCH}?db=pubmed&term={urllib.parse.quote(orcid + '[auid]')}&retmax=200&retmode=json"
    try:
        resp2 = urllib.request.urlopen(urllib.request.Request(search_url), context=SSL_CTX, timeout=15)
        search_data = json.loads(resp2.read())
        for pid in search_data.get("esearchresult", {}).get("idlist", []):
            pmids.add(pid)
    except Exception:
        pass

    return sorted(pmids)


# ─── PubMed fetch ────────────────────────────────────────────

def fetch_pubmed_articles(pmids: List[str], batch_size: int = 50) -> List[Dict]:
    """Fetch full article metadata from PubMed for a list of PMIDs."""
    articles = []
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i : i + batch_size]
        url = f"{PUBMED_FETCH}?db=pubmed&id={','.join(batch)}&retmode=xml"
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url), context=SSL_CTX, timeout=30)
            root = ET.fromstring(resp.read())

            for article_el in root.findall(".//PubmedArticle"):
                articles.append(_parse_article(article_el))

        except Exception as e:
            print(f"  [WARN] PubMed fetch error: {e}")
        time.sleep(0.35)
    return articles


def _parse_article(article_el) -> Dict:
    """Parse a single PubmedArticle XML element."""
    pmid_el = article_el.find(".//PMID")
    pmid = pmid_el.text if pmid_el is not None else ""

    title_el = article_el.find(".//ArticleTitle")
    title = "".join(title_el.itertext()) if title_el is not None else ""

    # Abstract
    abstract_el = article_el.find(".//Abstract")
    abstract = ""
    if abstract_el is not None:
        parts = []
        for at in abstract_el.findall("AbstractText"):
            label = at.get("Label", "")
            text = "".join(at.itertext())
            parts.append(f"{label}: {text}" if label else text)
        abstract = " ".join(parts)

    # Authors
    author_list = []
    for author in article_el.findall(".//Author"):
        ln = author.find("LastName")
        fn = author.find("ForeName")
        if ln is not None:
            name = f"{fn.text} {ln.text}" if fn is not None else ln.text
            author_list.append(name)
    authors = "; ".join(author_list)

    # Journal
    journal_el = article_el.find(".//Journal/Title")
    journal = journal_el.text if journal_el is not None else ""

    # Date — read child text safely (Element with no children is falsy in ET, so
    # avoid `or` short-circuit; use explicit `is not None` checks)
    def _txt(parent, tag):
        if parent is None:
            return ""
        el = parent.find(tag)
        return (el.text or "").strip() if el is not None else ""

    _MONTHS = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }

    pub_date = ""
    iso_date = ""

    # Prefer <ArticleDate> (electronic pub date) — usually a clean ISO date
    date_el = article_el.find(".//ArticleDate")
    if date_el is not None:
        y, m, d = _txt(date_el, "Year"), _txt(date_el, "Month"), _txt(date_el, "Day")
        if y:
            mm = _MONTHS.get(m, m).zfill(2) if m else "01"
            dd = d.zfill(2) if d else "01"
            iso_date = f"{y}-{mm}-{dd}"
            pub_date = iso_date

    # Fall back to <PubDate>
    if not pub_date:
        pub_date_el = article_el.find(".//PubDate")
        if pub_date_el is not None:
            y = _txt(pub_date_el, "Year")
            m = _txt(pub_date_el, "Month")
            d = _txt(pub_date_el, "Day")
            medline = _txt(pub_date_el, "MedlineDate")
            if y:
                mm = _MONTHS.get(m, m).zfill(2) if m else "01"
                dd = d.zfill(2) if d else "01"
                iso_date = f"{y}-{mm}-{dd}"
                # Display: "2024 Mar" or just "2024" if no month
                pub_date = f"{y}{(' ' + m) if m else ''}".strip()
            elif medline:
                # MedlineDate is freeform like "2024 Spring" or "2024 Mar-Apr"
                # Try to extract a 4-digit year for sorting.
                import re
                yr_match = re.match(r"\d{4}", medline)
                if yr_match:
                    iso_date = f"{yr_match.group(0)}-01-01"
                pub_date = medline

    # DOI
    doi = ""
    for eid in article_el.findall(".//ArticleId"):
        if eid.get("IdType") == "doi":
            doi = eid.text or ""
            break

    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "journal": journal,
        "pub_date": pub_date,
        "iso_date": iso_date,  # for sorting
        "doi": doi,
    }


# ─── Main lookup function ───────────────────────────────────

def lookup_publications(orcid: str, save_to_db: bool = True) -> Dict:
    """
    Main entry point: given an ORCID, return profile + publications.

    Returns:
        {
            "orcid": str,
            "name": str,
            "affiliations": [str],
            "publication_count": int,
            "publications": [{pmid, title, abstract, authors, journal, pub_date, doi}]
        }
    """
    print(f"\n{'='*60}")
    print(f"Looking up ORCID: {orcid}")
    print(f"{'='*60}")

    # Step 1: Get profile info
    print("\n[1] Fetching ORCID profile...")
    profile = fetch_orcid_profile(orcid)
    name = f"{profile['given']} {profile['family']}"
    print(f"    Name: {name}")
    print(f"    Affiliations: {', '.join(profile['orgs']) if profile['orgs'] else 'Not listed'}")

    # Step 2: Get PMIDs from ORCID works + PubMed search
    print("\n[2] Collecting PubMed IDs...")
    pmids = fetch_orcid_pmids(orcid)
    print(f"    Found {len(pmids)} unique publications")

    # Step 3: Fetch full article data from PubMed
    publications = []
    if pmids:
        print("\n[3] Fetching article details from PubMed...")
        publications = fetch_pubmed_articles(pmids)
        print(f"    Retrieved {len(publications)} articles with metadata")
        # Sort by ISO date descending (newest first); fall back to PMID
        publications.sort(
            key=lambda p: (p.get("iso_date") or "", p.get("pmid") or ""),
            reverse=True,
        )

    # Step 4: Save to DB
    if save_to_db and publications:
        print("\n[4] Saving to database...")
        init_publication_table()
        saved = save_publications(publications, orcid)
        print(f"    Saved {saved} publications to capstone.db")

    result = {
        "orcid": orcid,
        "name": name,
        "affiliations": profile["orgs"],
        "publication_count": len(publications),
        "publications": publications,
    }

    return result


# ─── CLI usage ───────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        orcid_id = sys.argv[1]
    else:
        orcid_id = input("Enter ORCID ID: ").strip()

    result = lookup_publications(orcid_id)

    print(f"\n{'='*60}")
    print(f"RESULTS: {result['name']} ({result['orcid']})")
    print(f"{'='*60}")
    print(f"Affiliations: {', '.join(result['affiliations']) or 'N/A'}")
    print(f"Publications: {result['publication_count']}")

    for i, pub in enumerate(result["publications"], 1):
        print(f"\n--- [{i}] PMID: {pub['pmid']} ---")
        print(f"Title:   {pub['title']}")
        print(f"Authors: {pub['authors'][:100]}{'...' if len(pub['authors']) > 100 else ''}")
        print(f"Journal: {pub['journal']}")
        print(f"Date:    {pub['pub_date']}")
        if pub["doi"]:
            print(f"DOI:     https://doi.org/{pub['doi']}")
        if pub["abstract"]:
            print(f"Abstract: {pub['abstract'][:200]}{'...' if len(pub['abstract']) > 200 else ''}")

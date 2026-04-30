import sqlite3
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import time

DB_PATH = "capstone.db"
PI_EXCEL = "RePORTER_PI_IDS_FY2025.xlsx"


def init_pubmed_table():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS pubmed_publication")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pubmed_publication (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pi_name TEXT,
            pi_id TEXT,
            orcid TEXT,
            pmid TEXT UNIQUE,
            title TEXT,
            authors TEXT,
            journal TEXT,
            pub_date TEXT,
            abstract TEXT,
            doi TEXT
        )
    """)

    conn.commit()
    conn.close()


def search_pubmed_pmids(query, retmax=20):
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": retmax
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("esearchresult", {}).get("idlist", [])


def fetch_pubmed_details(pmids):
    if not pmids:
        return []

    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml"
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    root = ET.fromstring(r.text)
    articles = []

    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID", default="")
        title = article.findtext(".//ArticleTitle", default="")

        abstract_parts = article.findall(".//Abstract/AbstractText")
        abstract = " ".join(["".join(a.itertext()) for a in abstract_parts]) if abstract_parts else ""

        journal = article.findtext(".//Journal/Title", default="")

        year = article.findtext(".//PubDate/Year", default="")
        medline_date = article.findtext(".//PubDate/MedlineDate", default="")
        pub_date = year if year else medline_date

        authors_list = []
        for author in article.findall(".//Author"):
            lastname = author.findtext("LastName", default="")
            forename = author.findtext("ForeName", default="")
            collective = author.findtext("CollectiveName", default="")

            if collective:
                authors_list.append(collective)
            else:
                full_name = f"{forename} {lastname}".strip()
                if full_name:
                    authors_list.append(full_name)

        authors = "; ".join(authors_list)

        doi = ""
        for aid in article.findall(".//ArticleId"):
            if aid.attrib.get("IdType") == "doi":
                doi = aid.text or ""
                break

        articles.append({
            "pmid": pmid,
            "title": title,
            "authors": authors,
            "journal": journal,
            "pub_date": pub_date,
            "abstract": abstract,
            "doi": doi
        })

    return articles


def save_pubmed_articles(pi_name, pi_id, orcid, articles):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    for a in articles:
        cur.execute("""
            INSERT OR IGNORE INTO pubmed_publication
            (pi_name, pi_id, orcid, pmid, title, authors, journal, pub_date, abstract, doi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pi_name,
            pi_id,
            orcid,
            a["pmid"],
            a["title"],
            a["authors"],
            a["journal"],
            a["pub_date"],
            a["abstract"],
            a["doi"]
        ))

    conn.commit()
    conn.close()


def build_pubmed_table():
    init_pubmed_table()

    df = pd.read_excel(PI_EXCEL, sheet_name="Sheet2")

    print("Excel columns are:")
    print(df.columns.tolist())

    for _, row in df.iterrows():
        pi_name = str(row.get("PI_NAMEs", "")).strip()
        pi_id = str(row.get("PI_IDS", "")).strip()
        orcid = str(row.get("ORCID", "")).strip()

        if not pi_name or pi_name.lower() == "nan":
            continue

        if pi_id.lower() == "nan":
            pi_id = ""
        if orcid.lower() == "nan":
            orcid = ""

        if orcid:
            query = f'{orcid}[AUID]'
        else:
            query = f'"{pi_name}"[Author]'

        print("DEBUG:", pi_name, pi_id, orcid)
        print(f"Searching PubMed for: {pi_name} | PI_ID={pi_id} | query={query}")

        try:
            pmids = search_pubmed_pmids(query, retmax=20)
            articles = fetch_pubmed_details(pmids)
            save_pubmed_articles(pi_name, pi_id, orcid, articles)
            print(f"Saved {len(articles)} articles for {pi_name}")
        except Exception as e:
            print(f"Error for {pi_name}: {e}")

        time.sleep(1.2)


if __name__ == "__main__":
    build_pubmed_table()
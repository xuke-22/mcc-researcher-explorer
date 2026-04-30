"""
capstone2.py — Search NIH Reporter API by MCC member PI_IDS
============================================================
Flow:
  1. Load 147 PI profile IDs from RePORTER_PRJ_C_FY2025 copy 2.xlsx
  2. Batch IDs (50 per request) → POST to NIH Reporter API
  3. Paginate through all results for each batch
  4. Upsert PI + project rows into SQLite
  5. Print summary
"""

import os
import time
import sqlite3
import requests
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

# =========================
# CONFIG
# =========================
DB_PATH = "capstone.db"
PI_EXCEL_PATH = "RePORTER_PRJ_C_FY2025 copy 2.xlsx"

API_URL = "https://api.reporter.nih.gov/v2/projects/search"
PAGE_LIMIT = 500        # max results per API request
BATCH_SIZE = 50         # how many PI IDs per API query
SLEEP_SEC = 0.3         # pause between requests


# =========================
# DB helpers
# =========================
def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS pi (
      pi_id INTEGER PRIMARY KEY,
      pi_name TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS project (
      appl_id INTEGER PRIMARY KEY,
      fiscal_year INTEGER,
      project_num TEXT,
      project_title TEXT,
      project_start TEXT,
      contact_pi_id INTEGER,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(contact_pi_id) REFERENCES pi(pi_id)
    );

    CREATE TABLE IF NOT EXISTS seen_project (
      appl_id INTEGER PRIMARY KEY
    );
    """)
    conn.commit()


def upsert_pi(conn: sqlite3.Connection, pi_id: Optional[int],
              pi_name: Optional[str] = None) -> None:
    if pi_id is None:
        return
    conn.execute("""
    INSERT INTO pi (pi_id, pi_name)
    VALUES (?, ?)
    ON CONFLICT(pi_id) DO UPDATE SET
      pi_name = COALESCE(excluded.pi_name, pi.pi_name),
      updated_at = CURRENT_TIMESTAMP
    """, (pi_id, pi_name))


def upsert_project(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    appl_id = row.get("appl_id")
    if appl_id is None:
        return
    conn.execute("""
    INSERT INTO project (
      appl_id, fiscal_year, project_num, project_title,
      project_start, contact_pi_id
    ) VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(appl_id) DO UPDATE SET
      fiscal_year   = excluded.fiscal_year,
      project_num   = excluded.project_num,
      project_title = excluded.project_title,
      project_start = excluded.project_start,
      contact_pi_id = excluded.contact_pi_id,
      updated_at    = CURRENT_TIMESTAMP
    """, (
        row.get("appl_id"),
        row.get("fiscal_year"),
        row.get("project_num"),
        row.get("project_title"),
        row.get("project_start"),
        row.get("contact_pi_id"),
    ))


def mark_seen(conn: sqlite3.Connection, appl_id: Optional[int]) -> None:
    if appl_id is None:
        return
    conn.execute("INSERT OR IGNORE INTO seen_project(appl_id) VALUES (?)",
                 (appl_id,))


# =========================
# Load PI IDs from Excel
# =========================
def load_pi_ids(excel_path: str = PI_EXCEL_PATH) -> Tuple[List[int], Dict[int, str]]:
    """
    Returns:
      pi_ids  – list of integer PI profile IDs
      name_map – {pi_id: pi_name} for name look-ups
    """
    df = pd.read_excel(excel_path)
    df = df.dropna(subset=["PI_IDS"])
    df["PI_IDS"] = df["PI_IDS"].astype(int)

    pi_ids = df["PI_IDS"].tolist()
    name_map = dict(zip(df["PI_IDS"], df["PI_NAMEs"].astype(str)))

    print(f"[LOAD] {len(pi_ids)} PI IDs loaded from {excel_path}")
    return pi_ids, name_map


# =========================
# API helpers
# =========================
def fetch_projects_for_pis(pi_id_batch: List[int],
                           offset: int = 0,
                           limit: int = PAGE_LIMIT) -> Tuple[List[Dict], int]:
    """
    Search NIH Reporter for projects linked to a batch of PI profile IDs.
    Returns (results_list, total_count).
    """
    payload = {
        "criteria": {
            "pi_profile_ids": pi_id_batch,
        },
        "include_fields": [
            "ApplId",
            "FiscalYear",
            "ProjectNum",
            "ProjectTitle",
            "ProjectStartDate",
            "PrincipalInvestigators",
        ],
        "offset": offset,
        "limit": limit,
    }
    r = requests.post(API_URL, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    total = int(data.get("meta", {}).get("total") or 0)
    return results, total


def extract_contact_pi(project: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """Extract contact PI's profile_id and full_name from an API result."""
    pis = project.get("principal_investigators") or []
    for pi in pis:
        if isinstance(pi, dict) and pi.get("is_contact_pi") is True:
            return pi.get("profile_id"), pi.get("full_name")
    return None, None


# =========================
# Main search function
# =========================
def search_projects_by_pi_ids(
    db_path: str = DB_PATH,
    excel_path: str = PI_EXCEL_PATH,
) -> None:
    """
    Simple flow:
      Load PI IDs → batch query API → store results in DB
    """
    conn = get_conn(db_path)
    init_db(conn)

    pi_ids, name_map = load_pi_ids(excel_path)

    total_projects = 0

    # Process in batches
    for batch_start in range(0, len(pi_ids), BATCH_SIZE):
        batch = pi_ids[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(pi_ids) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n[BATCH {batch_num}/{total_batches}] Querying {len(batch)} PI IDs...")

        # Paginate through all results for this batch
        offset = 0
        batch_total = None

        while True:
            results, api_total = fetch_projects_for_pis(batch, offset=offset)

            if batch_total is None:
                batch_total = api_total
                print(f"  API reports {batch_total} total projects for this batch")

            if not results:
                break

            for p in results:
                appl_id = p.get("appl_id")
                contact_pi_id, api_pi_name = extract_contact_pi(p)

                # Prefer our Excel name, fall back to API name
                pi_name = None
                if contact_pi_id is not None:
                    pi_name = name_map.get(int(contact_pi_id)) or api_pi_name

                upsert_pi(conn, contact_pi_id, pi_name)

                upsert_project(conn, {
                    "appl_id": appl_id,
                    "fiscal_year": p.get("fiscal_year"),
                    "project_num": p.get("project_num"),
                    "project_title": p.get("project_title"),
                    "project_start": p.get("project_start_date"),
                    "contact_pi_id": contact_pi_id,
                })

                mark_seen(conn, appl_id)
                total_projects += 1

            conn.commit()
            offset += PAGE_LIMIT
            time.sleep(SLEEP_SEC)

            if offset >= batch_total:
                break

        print(f"  Batch done. Running total: {total_projects} projects")
        time.sleep(SLEEP_SEC)

    conn.close()
    print(f"\n[DONE] {total_projects} projects stored for {len(pi_ids)} MCC PIs")


# =========================
# Quick DB summary
# =========================
def db_counts(db_path: str = DB_PATH) -> None:
    conn = get_conn(db_path)
    pi_cnt = conn.execute("SELECT COUNT(*) FROM pi").fetchone()[0]
    proj_cnt = conn.execute("SELECT COUNT(*) FROM project").fetchone()[0]
    seen_cnt = conn.execute("SELECT COUNT(*) FROM seen_project").fetchone()[0]
    conn.close()
    print(f"[DB] pi={pi_cnt}  project={proj_cnt}  seen_project={seen_cnt}")


def reset_db(db_path: str = DB_PATH) -> None:
    """Clear all tables to start fresh."""
    conn = get_conn(db_path)
    conn.execute("DELETE FROM project;")
    conn.execute("DELETE FROM seen_project;")
    conn.execute("DELETE FROM pi;")
    conn.commit()
    conn.close()
    print("[RESET] All tables cleared.")


# =========================
# Run
# =========================
if __name__ == "__main__":
    search_projects_by_pi_ids()
    db_counts()

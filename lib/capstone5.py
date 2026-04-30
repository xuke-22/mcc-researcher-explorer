"""
capstone5.py — Interactive Demo: Search MCC Members' Publications
=================================================================
A client-facing demo that lets you:
  1. Type an ORCID ID directly, OR
  2. Search by PI name from the MCC member list
  3. View publications with titles, abstracts, and links

Usage:
  python3 capstone5.py              # interactive mode
  python3 capstone5.py 0000-0002-4267-4893   # direct ORCID lookup
  python3 capstone5.py --name "Artis"        # search by name
"""

import sys
import sqlite3
import pandas as pd
from capstone4 import lookup_publications, init_publication_table

DB_PATH = "capstone.db"
PI_EXCEL = "RePORTER_PI_IDS_FY2025.xlsx"


def load_member_orcids() -> pd.DataFrame:
    """Load MCC member ORCID data from Sheet2."""
    df = pd.read_excel(PI_EXCEL, sheet_name="Sheet2")
    df = df[["PI_IDS", "PI_NAMEs", "ORCID", "PUB_COUNT"]].copy()
    df["PI_IDS"] = df["PI_IDS"].apply(lambda x: int(x) if pd.notna(x) else None)
    df["ORCID"] = df["ORCID"].apply(lambda x: str(x).strip() if pd.notna(x) else None)
    return df


def search_member_by_name(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Search members by partial name match."""
    mask = df["PI_NAMEs"].str.contains(query, case=False, na=False)
    return df[mask]


def display_publications(result: dict) -> None:
    """Pretty-print publication results for demo."""
    pubs = result["publications"]

    print(f"\n{'─'*60}")
    print(f"  {result['name']}")
    print(f"  ORCID: {result['orcid']}")
    if result["affiliations"]:
        print(f"  Affiliations: {', '.join(result['affiliations'])}")
    print(f"  Total publications: {result['publication_count']}")
    print(f"{'─'*60}")

    if not pubs:
        print("  No publications found.\n")
        return

    for i, pub in enumerate(pubs, 1):
        print(f"\n  [{i}] {pub['title']}")
        print(f"      PMID: {pub['pmid']}  |  {pub['journal']}  |  {pub['pub_date']}")
        if pub["doi"]:
            print(f"      DOI: https://doi.org/{pub['doi']}")
        print(f"      Authors: {pub['authors'][:120]}{'...' if len(pub['authors']) > 120 else ''}")
        if pub["abstract"]:
            # Show first 300 chars of abstract
            abs_preview = pub["abstract"][:300]
            if len(pub["abstract"]) > 300:
                abs_preview += "..."
            print(f"      Abstract: {abs_preview}")

    print(f"\n{'─'*60}\n")


def query_db_publications(orcid: str) -> list:
    """Check if we already have publications cached in the DB."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM publication WHERE orcid = ? ORDER BY pub_date DESC", (orcid,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def interactive_demo():
    """Main interactive loop for the demo."""
    print("\n" + "=" * 60)
    print("  MCC Member Publication Search")
    print("  Meyer Cancer Center — Weill Cornell Medicine")
    print("=" * 60)

    # Load member data
    try:
        df = load_member_orcids()
        member_count = len(df)
        orcid_count = df["ORCID"].notna().sum()
        print(f"\n  Loaded {member_count} MCC members ({orcid_count} with ORCID)")
    except Exception as e:
        print(f"\n  [WARN] Could not load member list: {e}")
        print("  You can still search by ORCID directly.\n")
        df = None

    init_publication_table()

    while True:
        print("\nOptions:")
        print("  1. Enter an ORCID ID")
        print("  2. Search by member name")
        print("  3. View database stats")
        print("  4. Quit")

        choice = input("\nChoice (1-4): ").strip()

        if choice == "1":
            orcid = input("Enter ORCID ID: ").strip()
            if not orcid:
                continue
            result = lookup_publications(orcid)
            display_publications(result)

        elif choice == "2":
            if df is None:
                print("  Member list not available. Use option 1 instead.")
                continue
            query = input("Enter name (or part of name): ").strip()
            if not query:
                continue

            matches = search_member_by_name(df, query)
            if matches.empty:
                print(f"  No members found matching '{query}'")
                continue

            print(f"\n  Found {len(matches)} member(s):\n")
            for idx, (_, row) in enumerate(matches.iterrows(), 1):
                orcid_str = row["ORCID"] if row["ORCID"] else "No ORCID"
                pid_str = f"PI_ID: {int(row['PI_IDS'])}" if row["PI_IDS"] else "No PI_ID"
                print(f"    {idx}. {row['PI_NAMEs']}  |  {pid_str}  |  {orcid_str}")

            if len(matches) == 1:
                sel = 1
            else:
                sel_str = input(f"\n  Select member (1-{len(matches)}), or 0 to cancel: ").strip()
                if not sel_str.isdigit() or int(sel_str) == 0:
                    continue
                sel = int(sel_str)

            if sel < 1 or sel > len(matches):
                print("  Invalid selection.")
                continue

            selected = matches.iloc[sel - 1]
            if not selected["ORCID"]:
                print(f"  {selected['PI_NAMEs']} does not have an ORCID on file.")
                continue

            print(f"\n  Fetching publications for {selected['PI_NAMEs']}...")
            result = lookup_publications(selected["ORCID"])
            display_publications(result)

        elif choice == "3":
            try:
                conn = sqlite3.connect(DB_PATH)
                pub_count = conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0]
                orcid_count = conn.execute("SELECT COUNT(DISTINCT orcid) FROM publication").fetchone()[0]
                conn.close()
                print(f"\n  Database: {pub_count} publications for {orcid_count} researchers")
            except Exception:
                print("  No publication data in database yet.")

        elif choice == "4" or choice.lower() in ("q", "quit", "exit"):
            print("  Goodbye!\n")
            break

        else:
            print("  Invalid choice. Enter 1-4.")


# ─── CLI entry points ────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--name":
            # Search by name
            name_query = " ".join(sys.argv[2:])
            df = load_member_orcids()
            matches = search_member_by_name(df, name_query)
            if matches.empty:
                print(f"No members found matching '{name_query}'")
                sys.exit(1)
            for _, row in matches.iterrows():
                if row["ORCID"]:
                    result = lookup_publications(row["ORCID"])
                    display_publications(result)
                else:
                    print(f"{row['PI_NAMEs']}: No ORCID on file")
        else:
            # Direct ORCID lookup
            result = lookup_publications(sys.argv[1])
            display_publications(result)
    else:
        interactive_demo()

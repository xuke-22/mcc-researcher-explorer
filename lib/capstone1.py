import sqlite3
import pandas as pd

DB_PATH = "capstone.db"
PI_EXCEL_PATH = "RePORTER_PRJ_C_FY2025 copy 2.xlsx"

# Read MCC member PI data from the curated xlsx
df_pi = pd.read_excel(PI_EXCEL_PATH)

# Filter to rows with valid PI_IDS (drop unmatched placeholders)
df_pi = df_pi.dropna(subset=["PI_IDS"])
df_pi["PI_IDS"] = df_pi["PI_IDS"].astype(int)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.executescript("""
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

# Upsert PI records (SQLite 3.24+)
cur.executemany("""
INSERT INTO pi (pi_id, pi_name)
VALUES (?, ?)
ON CONFLICT(pi_id) DO UPDATE SET
  pi_name=excluded.pi_name,
  updated_at=CURRENT_TIMESTAMP
""", df_pi[["PI_IDS", "PI_NAMEs"]].itertuples(index=False, name=None))

conn.commit()
conn.close()
print(f"PI table loaded: {len(df_pi)} MCC members")
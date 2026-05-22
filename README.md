# MCC Research Explorer

Web interface for searching publications and NIH funding for the **360 Meyer Cancer Center** members at Weill Cornell Medicine, Memorial Sloan Kettering, and Rockefeller University.

## Features

- **By Researcher** — combined view: PubMed publications (via ORCID) + NIH funding (via PI_ID)
- **By Keyword** — search cached publications by title/abstract
- **NIH Funding** — live query NIH Reporter API by member name OR by PI_ID directly

## Folder structure

```
website/
├── app.py                       # Flask backend (all endpoints)
├── requirements.txt             # Python dependencies
├── Procfile                     # Render / Heroku start command
├── render.yaml                  # Render blueprint
├── runtime.txt                  # Python version
├── .gitignore
├── README.md                    # This file
│
├── data/                        # Data files (DB + Excel)
│   ├── capstone.db              # SQLite — pi, project, publication tables
│   └── RePORTER_PI_IDS_FY2025.xlsx
│
├── lib/                         # Backend libraries
│   ├── capstone1.py             # Data loader (Excel → DB)
│   ├── capstone2.py             # NIH Reporter API search
│   ├── capstone4.py             # PubMed publication engine
│   ├── capstone5.py             # CLI demo
│   └── build_pubmed_table.py    # Builds pubmed_publication table
│
├── static/                      # Frontend assets
│   ├── style.css                # Weill Cornell brand styling
│   ├── app.js                   # Frontend logic + rendering
│   └── wcm_logo.png             # Official Weill Cornell Medicine logo
│
└── templates/
    └── index.html               # Main page
```

## Run locally

```bash
cd website
pip install -r requirements.txt
python3 app.py
# → http://localhost:5001
```

## Deploy to Render (free tier)

1. **Push the `website/` folder to a GitHub repo** (e.g. `mcc-research-explorer`).

   ```bash
   cd website
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin git@github.com:<your-username>/mcc-research-explorer.git
   git branch -M main
   git push -u origin main
   ```

2. **Create a Render account** at https://render.com (free, sign in with GitHub).

3. **Click "New" → "Web Service"** → connect the GitHub repo.

4. Render will auto-detect `render.yaml`. Confirm the settings:
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT`
   - **Plan**: Free

5. **Click "Create Web Service"**. After ~3 minutes the site will be live at:

   ```
   https://mcc-research-explorer.onrender.com
   ```

   (Or whatever name you chose.)

### Notes on the free Render tier

- Service spins down after 15 min of inactivity → first request after sleep is slow (~30s cold start).
- Filesystem is ephemeral. The local SQLite cache is read-only after deploy; live PubMed/NIH lookups still work fine.
- For paid plans (`starter` $7/mo) the service stays warm.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /` | Main page |
| `GET /search?q=...` | Keyword search (cached publications) |
| `GET /search_name?name=...` | Researcher publications |
| `GET /search_funding?pi_id=...` | NIH funding by PI_ID |
| `GET /search_funding?name=...` | NIH funding by member name |
| `GET /researcher?name=...` | Combined: publications + funding |
| `GET /api/members` | Member roster (autocomplete) |
| `GET /api/stats` | Dashboard stats |

## Data sources

- **Local cache**: `data/capstone.db` (SQLite)
- **PubMed E-Utilities**: `eutils.ncbi.nlm.nih.gov` — live publication fetch by ORCID
- **NIH Reporter**: `api.reporter.nih.gov/v2` — live funding lookup by PI_ID
- **ORCID Public API**: `pub.orcid.org/v3.0` — researcher profiles

## Brand colors

Primary: `#B31B1B` Cornell Red · `#CF4520` · `#E7751D` · `#FFC72C`

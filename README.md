# WFP-jobs

A Python script that scrapes current job opportunities from the [WFP Workday careers page](https://wd3.myworkdaysite.com/en-GB/recruiting/wfp/job_openings), filters them for international professional positions (P/D levels, internships, fellowships), and generates an RSS feed.

**RSS Feed URL:** <https://cinfoposte.github.io/WFP-jobs/wfp_jobs.xml>

## What it does

- Queries the WFP Workday JSON API to list all current vacancies
- Filters for P-1 through P-5, D-1/D-2, internships, and fellowships
- Excludes consultants, G-level, National Officer, Service Contract, and Local Service Contract positions
- Outputs a valid RSS 2.0 feed (`wfp_jobs.xml`)
- Runs automatically via GitHub Actions (Thursday & Sunday at 06:00 UTC)

## Local run

```bash
# Clone the repository
git clone https://github.com/cinfoposte/WFP-jobs.git
cd WFP-jobs

# Install dependencies
pip install -r requirements.txt

# Run the scraper
python scraper.py
```

The feed will be written to `wfp_jobs.xml` in the repository root.

## GitHub Pages activation

1. Go to **Settings** → **Pages**
2. Under **Source**, select **Deploy from a branch**
3. Choose branch **main** and folder **/ (root)**
4. Click **Save**

The feed will be available at:
<https://cinfoposte.github.io/WFP-jobs/wfp_jobs.xml>

## cinfoPoste import mapping

| Portal-Feld | Dropdown-Auswahl |
|---|---|
| TITLE | → Title |
| LINK | → Link |
| DESCRIPTION | → Description |
| PUBDATE | → Date |
| ITEM | → Start item |
| GUID | → Unique ID |

# data-storyteller

A [Claude Code](https://claude.com/claude-code) skill that takes any data file or data source and turns it into a data story: automated EDA, data quality checks, distributions, correlations, segment effects, time trends, and target drivers, followed by a narrative report with charts.

## Install

Clone into your skills directory — per project or for your user:

```bash
# this project only
git clone https://github.com/HagTheDon/data-storyteller.git .claude/skills/data-storyteller

# all projects
git clone https://github.com/HagTheDon/data-storyteller.git ~/.claude/skills/data-storyteller
```

Python dependencies:

```bash
pip install pandas numpy matplotlib pyarrow scipy      # required + recommended
pip install openpyxl sqlalchemy                        # Excel / database sources
```

## Use

In Claude Code:

```
/data-storyteller sales.csv
```

or just ask: *"Analyze orders.xlsx — I care about churn."*

Supported sources: CSV/TSV, Excel, JSON/JSONL, Parquet, Feather, SQLite, http(s) URLs, and SQLAlchemy database URLs.

## How it works

1. **Profile** — `scripts/profile.py` loads the source, fixes messy types (currency strings, yes/no, date strings), profiles every column, and flags leads: missingness, skew, outliers, duplicates, correlations, category effects (η²), categorical associations (Cramér's V), trends, partial periods, and leakage.
2. **Understand** — Claude reviews the summary and charts and works out the grain of the data.
3. **Investigate** — Claude verifies and quantifies each lead with targeted analysis (`references/analysis-playbook.md`).
4. **Tell the story** — Claude writes a `report.md` with a headline, key findings, caveats, and next steps (`references/storytelling.md`).

Outputs land in `data-story/<name>/`: `summary.md`, `profile.json`, `charts/`, `data.parquet`, and `report.md`.

The profiler also runs standalone:

```bash
python3 scripts/profile.py data.csv --target churned --out data-story/data
```

# Analysis playbook

Pick the plays that fit the data's shape. Each assumes `df = pd.read_parquet("data-story/<name>/data.parquet")`.

## Any dataset

- **Grain check**: is the suspected key unique? `df[key].duplicated().sum()`. If not, what makes a row unique?
- **Sentinels & impossible values**: values like `-1`, `0`, `999`, `9999`, `1900-01-01`, empty strings, `"N/A"`, `"null"`. Check `min/max` per numeric against common sense (negative ages, prices of 0, percentages > 100).
- **Missingness pattern**: is missing random, or concentrated in a segment/time period? `df.groupby(seg)[col].apply(lambda s: s.isna().mean())`. Missing-not-at-random changes conclusions.
- **Heavy tails**: for skewed measures report median and p90, and what share of the total the top 1%/10% of rows contribute (Pareto check).

## Has a time column

- Volume over time — look for launches, outages (gaps), seasonality, step changes.
- Trends of key measures: resample to a sensible period; compare like-for-like (YoY, same weekday).
- Day-of-week / hour-of-day patterns when timestamps have time-of-day.
- Is the last period partial? Drop or flag it before claiming a "drop".
- Cohorts: if there's an entity id + first-seen date, group by first period and track retention/value.

## Has categories + measures (most business data)

- For each strong `cat_num_effects` flag: group table with n, mean, median; sort; show the spread.
- **Mix vs. rate (Simpson's paradox)**: if an overall metric changed, decompose into change in segment mix vs. change within segments.
- Concentration: share of total by top N categories.
- Cross-tabs of two key dimensions for the main measure (heatmap if both have ≤ 15 levels).

## Has a target / outcome column

- Baseline rate or mean first.
- Univariate drivers: `target_drivers` in profile.json, then verify the top 3–5 with grouped tables and n.
- Check for **leakage**: features that are near-perfect predictors are often recorded after the outcome.
- Optional: a quick shallow tree or logistic/linear model (sklearn) for ranked importance — present as "associated with", not "causes".

## Numeric-heavy (sensor, scientific, financial)

- Correlation structure: clusters of redundant variables (|r| > 0.9).
- Nonlinear relationships: compare Pearson vs Spearman; scatter (sample ≤ 5k points) the top pairs.
- Outliers: are they errors or the interesting part? Inspect the actual rows.
- Distributions: bimodality often means two populations mixed together — find the variable that separates them.

## Text columns

- Length distribution, empty/boilerplate share, most common tokens/phrases, and whether text length or keywords relate to measures.

## Geographic columns (country, state, city, lat/lon)

- Counts and key measure by region, normalized by size (per-capita or rate) where possible.

## Multiple tables / sheets

- Identify join keys and check key coverage both ways before joining. Report orphaned keys — they're often a finding.

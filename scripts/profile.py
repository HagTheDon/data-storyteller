#!/usr/bin/env python3
"""Automated EDA profiler for the data-storyteller skill.

Loads almost any tabular source, profiles every column, detects notable
patterns, and writes:
  <out>/profile.json   full machine-readable profile
  <out>/summary.md     compact human/LLM-readable digest (also printed)
  <out>/charts/*.png   standard EDA charts
  <out>/data.parquet   the loaded dataframe (for fast follow-up analysis)

Usage:
  python profile.py <source> [--out DIR] [--sheet NAME] [--table NAME]
                    [--query SQL] [--target COL] [--sample N]

<source> may be a path or URL to: csv, tsv, txt, xlsx/xls, json, jsonl/ndjson,
parquet, feather, sqlite/db, or a SQLAlchemy URL (postgresql://..., etc.).
Requires pandas, numpy, matplotlib. scipy is used when available.
"""
import argparse
import json
import os
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

try:
    from scipy import stats as sstats
except ImportError:  # optional
    sstats = None

ACCENT = "#3b6ea8"
MUTED = "#9aa5b1"
plt.rcParams.update({
    "figure.dpi": 110,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "axes.prop_cycle": matplotlib.cycler(color=[ACCENT, "#e07b39", "#4c9f70", "#b84a62", "#7a5ea8", MUTED]),
})


# --------------------------------------------------------------------------- loading

def load(source, sheet=None, table=None, query=None):
    """Return (df, meta) for a path, URL, or database connection string."""
    meta = {"source": source}
    low = source.lower()

    if re.match(r"^[a-z0-9+]+://", low) and not low.startswith(("http://", "https://", "file://")):
        from sqlalchemy import create_engine, inspect
        eng = create_engine(source)
        tables = inspect(eng).get_table_names()
        meta["tables"] = tables
        sql = query or f'SELECT * FROM "{table or tables[0]}"'
        meta["query"] = sql
        return pd.read_sql(sql, eng), meta

    ext = Path(low.split("?")[0]).suffix
    if ext in (".gz", ".bz2", ".zip", ".xz"):
        ext = Path(low.split("?")[0][: -len(ext)]).suffix

    if ext in (".csv", ".tsv", ".txt", ".dat", ""):
        sep = "\t" if ext == ".tsv" else None
        df = pd.read_csv(source, sep=sep, engine="python", on_bad_lines="warn")
    elif ext in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.read_excel(source, sheet_name=None)
        meta["sheets"] = {k: list(v.shape) for k, v in sheets.items()}
        name = sheet or max(sheets, key=lambda k: sheets[k].size)
        meta["sheet"] = name
        df = sheets[name]
    elif ext in (".jsonl", ".ndjson"):
        df = pd.read_json(source, lines=True)
    elif ext == ".json":
        try:
            df = pd.read_json(source)
        except ValueError:
            with open(source) as f:
                raw = json.load(f)
            if isinstance(raw, dict):  # find the biggest list of records
                lists = {k: v for k, v in raw.items() if isinstance(v, list)}
                raw = lists[max(lists, key=lambda k: len(lists[k]))] if lists else [raw]
            df = pd.json_normalize(raw)
        # flatten nested dict columns
        nested = [c for c in df.columns if df[c].map(lambda v: isinstance(v, dict)).any()]
        if nested:
            df = pd.json_normalize(df.to_dict("records"))
    elif ext in (".parquet", ".pq"):
        df = pd.read_parquet(source)
    elif ext in (".feather", ".arrow"):
        df = pd.read_feather(source)
    elif ext in (".sqlite", ".sqlite3", ".db"):
        import sqlite3
        con = sqlite3.connect(source)
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        meta["tables"] = tables
        if query:
            sql = query
        else:
            if not table:  # pick the largest table
                counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
                table = max(counts, key=counts.get)
            sql = f'SELECT * FROM "{table}"'
        meta["query"] = sql
        df = pd.read_sql(sql, con)
    else:
        raise SystemExit(f"Unsupported source type: {ext!r}. Load it manually and save as parquet/csv.")
    return df, meta


# --------------------------------------------------------------------------- typing

def coerce_types(df):
    """Parse object columns that are really numbers, dates, or booleans."""
    conversions = {}
    for c in df.columns:
        s = df[c]
        if s.dtype != object:
            continue
        nn = s.dropna().astype(str).str.strip()
        if nn.empty:
            continue
        # numbers with currency / thousands separators / percents
        cleaned = nn.str.replace(r"[,$€£%\s]", "", regex=True)
        num = pd.to_numeric(cleaned, errors="coerce")
        if num.notna().mean() > 0.95:
            df[c] = pd.to_numeric(s.astype(str).str.replace(r"[,$€£%\s]", "", regex=True), errors="coerce")
            conversions[c] = "numeric"
            continue
        if nn.str.lower().isin(["true", "false", "yes", "no", "y", "n", "t", "f"]).mean() > 0.98:
            df[c] = s.astype(str).str.strip().str.lower().map(
                {"true": True, "yes": True, "y": True, "t": True, "false": False, "no": False, "n": False, "f": False})
            conversions[c] = "boolean"
            continue
        if nn.str.contains(r"\d").mean() > 0.9 and nn.str.len().median() >= 6:
            dt = pd.to_datetime(nn, errors="coerce", format="mixed")
            if dt.notna().mean() > 0.9:
                df[c] = pd.to_datetime(s, errors="coerce", format="mixed")
                conversions[c] = "datetime"
    return df, conversions


def semantic_type(s, n):
    name = str(s.name).lower()
    nunique = s.nunique(dropna=True)
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    if pd.api.types.is_numeric_dtype(s):
        if nunique <= 1:
            return "constant"
        is_int = np.allclose(s.dropna() % 1, 0) if nunique else False
        if is_int and nunique == s.notna().sum() and re.search(r"(^|_)(id|key|uuid|index)$|^id", name):
            return "id"
        if nunique == 2:
            return "binary"
        if is_int and nunique <= min(20, max(3, n // 50)):
            return "categorical"
        return "numeric"
    if nunique <= 1:
        return "constant"
    if nunique == s.notna().sum() and n > 20:
        avg_len = s.dropna().astype(str).str.len().mean()
        return "text" if avg_len > 40 else "id"
    if nunique <= max(50, int(0.05 * n)):
        return "categorical"
    avg_len = s.dropna().astype(str).str.len().mean()
    return "text" if avg_len > 30 else "high_cardinality"


# --------------------------------------------------------------------------- profiling

def r(x, d=4):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    if isinstance(x, (np.floating, float)):
        return round(float(x), d)
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def profile_column(s, kind, n):
    p = {
        "dtype": str(s.dtype),
        "semantic_type": kind,
        "missing": int(s.isna().sum()),
        "missing_pct": r(100 * s.isna().mean(), 2),
        "unique": int(s.nunique(dropna=True)),
    }
    nn = s.dropna()
    if kind in ("numeric", "binary", "categorical") and pd.api.types.is_numeric_dtype(s) and len(nn):
        q = nn.quantile([0, .01, .05, .25, .5, .75, .95, .99, 1]).tolist()
        iqr = q[5] - q[3]
        lo, hi = q[3] - 1.5 * iqr, q[5] + 1.5 * iqr
        p.update({
            "mean": r(nn.mean()), "std": r(nn.std()), "min": r(q[0]), "p01": r(q[1]), "p05": r(q[2]),
            "p25": r(q[3]), "median": r(q[4]), "p75": r(q[5]), "p95": r(q[6]), "p99": r(q[7]), "max": r(q[8]),
            "skew": r(nn.skew()), "kurtosis": r(nn.kurt()),
            "zeros_pct": r(100 * (nn == 0).mean(), 2),
            "negative_pct": r(100 * (nn < 0).mean(), 2),
            "outliers_iqr": int(((nn < lo) | (nn > hi)).sum()),
            "outliers_iqr_pct": r(100 * ((nn < lo) | (nn > hi)).mean(), 2),
        })
    if kind in ("categorical", "binary", "boolean", "high_cardinality", "constant"):
        vc = s.value_counts(dropna=True)
        p["top_values"] = {str(k): int(v) for k, v in vc.head(10).items()}
        if len(vc):
            p["top_share_pct"] = r(100 * vc.iloc[0] / max(len(nn), 1), 2)
            probs = vc / vc.sum()
            p["entropy_norm"] = r(float(-(probs * np.log(probs)).sum() / np.log(len(vc))) if len(vc) > 1 else 0.0)
    if kind == "datetime" and len(nn):
        p.update({"min": str(nn.min()), "max": str(nn.max()), "span_days": r((nn.max() - nn.min()).days, 1)})
        diffs = nn.sort_values().diff().dropna()
        if len(diffs):
            p["median_gap"] = str(diffs.median())
            p["max_gap"] = str(diffs.max())
        p["future_dates"] = int((nn > pd.Timestamp.now(tz=nn.dt.tz)).sum())
    if kind in ("text", "high_cardinality", "id") and not pd.api.types.is_numeric_dtype(s):
        lens = nn.astype(str).str.len()
        p.update({"len_min": int(lens.min()) if len(lens) else None, "len_mean": r(lens.mean(), 1),
                  "len_max": int(lens.max()) if len(lens) else None,
                  "examples": nn.astype(str).head(3).tolist()})
    return p


def correlations(df, cols):
    out = {"pearson_top": [], "spearman_top": []}
    if len(cols) < 2:
        return out, None
    sub = df[cols].astype(float)
    pear = sub.corr(method="pearson")
    spear = sub.corr(method="spearman")
    for name, m in (("pearson_top", pear), ("spearman_top", spear)):
        pairs = []
        for i, a in enumerate(cols):
            for b in cols[i + 1:]:
                v = m.loc[a, b]
                if pd.notna(v):
                    pairs.append({"a": a, "b": b, "r": r(v, 3)})
        out[name] = sorted(pairs, key=lambda d: -abs(d["r"]))[:15]
    return out, pear


def min_group(n):
    """Smallest group worth comparing; tiny groups produce noisy extremes."""
    return max(10, int(0.01 * n))


def cat_num_effects(df, cats, nums):
    """Eta-squared: how much of each numeric's variance a categorical explains."""
    res = []
    for c in cats:
        g = df[c].astype(str).where(df[c].notna(), "∅")
        if g.nunique() < 2 or g.nunique() > 50:
            continue
        for x in nums:
            y = df[x]
            ok = y.notna()
            if ok.sum() < 10:
                continue
            yy, gg = y[ok], g[ok]
            big = gg.map(gg.value_counts()) >= min_group(len(gg))
            yy, gg = yy[big], gg[big]
            if gg.nunique() < 2:
                continue
            grand = yy.mean()
            ss_tot = ((yy - grand) ** 2).sum()
            if ss_tot == 0:
                continue
            grp = yy.groupby(gg)
            ss_b = (grp.count() * (grp.mean() - grand) ** 2).sum()
            eta2 = ss_b / ss_tot
            means = grp.mean().sort_values(ascending=False)
            res.append({"category": c, "numeric": x, "eta_sq": r(eta2, 3),
                        "highest": {str(means.index[0]): r(means.iloc[0])},
                        "lowest": {str(means.index[-1]): r(means.iloc[-1])}})
    return sorted(res, key=lambda d: -d["eta_sq"])[:15]


def cat_cat_assoc(df, cats):
    """Cramér's V between categorical pairs."""
    res = []
    for i, a in enumerate(cats):
        for b in cats[i + 1:]:
            ct = pd.crosstab(df[a], df[b])
            if ct.shape[0] < 2 or ct.shape[1] < 2:
                continue
            n = ct.values.sum()
            exp = np.outer(ct.sum(1), ct.sum(0)) / n
            chi2 = ((ct.values - exp) ** 2 / np.where(exp == 0, 1, exp)).sum()
            v = np.sqrt(chi2 / (n * (min(ct.shape) - 1)))
            res.append({"a": a, "b": b, "cramers_v": r(v, 3)})
    return sorted(res, key=lambda d: -d["cramers_v"])[:10]


def time_analysis(df, dcol, nums):
    s = df[dcol].dropna()
    if s.nunique() < 3:
        return None
    span = (s.max() - s.min()).days
    freq = "D" if span <= 120 else "W" if span <= 730 else "MS" if span <= 365 * 8 else "YS"
    d = df.set_index(dcol).sort_index()
    d = d[d.index.notna()]
    counts = d.resample(freq).size()
    out = {"date_column": dcol, "freq": freq, "periods": int(len(counts)),
           "rows_per_period": {"mean": r(counts.mean(), 2), "min": int(counts.min()), "max": int(counts.max())},
           "empty_periods": int((counts == 0).sum()),
           "last_period_partial": bool(len(counts) > 2 and counts.iloc[-1] < 0.6 * counts.iloc[:-1].median()),
           "metrics": {}}
    means = d[nums].resample(freq).mean() if nums else pd.DataFrame()
    sums = d[nums].resample(freq).sum() if nums else pd.DataFrame()
    for x in nums[:12]:
        ser = means[x].dropna()
        if len(ser) < 3:
            continue
        t = np.arange(len(ser))
        slope = np.polyfit(t, ser.values, 1)[0]
        k = max(1, len(ser) // 4)
        first, last = ser.iloc[:k].mean(), ser.iloc[-k:].mean()
        out["metrics"][x] = {
            "first_quarter_mean": r(first), "last_quarter_mean": r(last),
            "change_pct": r(100 * (last - first) / abs(first), 1) if first else None,
            "slope_per_period": r(slope),
            "peak_period": str(ser.idxmax().date()), "trough_period": str(ser.idxmin().date()),
            "total_last_period": r(sums[x].iloc[-1]),
        }
    return out, counts, means


def findings(df, prof, corr, effects, assoc, ts, target):
    """Rule-based flags. These are leads to investigate, not conclusions."""
    f = []
    n = len(df)
    dup = prof["dataset"]["duplicate_rows"]
    if dup:
        f.append(("quality", f"{dup} fully duplicated rows ({100*dup/n:.1f}%)."))
    for c, p in prof["columns"].items():
        if p["missing_pct"] and p["missing_pct"] >= 5:
            f.append(("quality", f"`{c}` is {p['missing_pct']}% missing."))
        if p["semantic_type"] == "constant":
            f.append(("quality", f"`{c}` is constant — carries no information."))
        if p.get("skew") is not None and abs(p["skew"]) > 2 and p["semantic_type"] == "numeric":
            f.append(("distribution", f"`{c}` is heavily skewed (skew={p['skew']}); median {p['median']} vs mean {p['mean']}."))
        if p.get("outliers_iqr_pct", 0) and p["outliers_iqr_pct"] > 5:
            f.append(("distribution", f"`{c}` has {p['outliers_iqr_pct']}% IQR outliers (max {p['max']}, p99 {p['p99']})."))
        if p.get("zeros_pct", 0) and p["zeros_pct"] > 30 and p["semantic_type"] == "numeric":
            f.append(("distribution", f"`{c}` is zero in {p['zeros_pct']}% of rows (zero-inflated)."))
        if p.get("top_share_pct") and p["top_share_pct"] > 90 and p["semantic_type"] in ("categorical", "binary", "boolean"):
            top = next(iter(p["top_values"]))
            f.append(("distribution", f"`{c}` is dominated by '{top}' ({p['top_share_pct']}%) — imbalanced."))
        if p.get("future_dates"):
            f.append(("quality", f"`{c}` has {p['future_dates']} dates in the future."))
        if p.get("min") is not None and isinstance(p.get("min"), (int, float)) and p.get("negative_pct") and 0 < p["negative_pct"] < 2:
            f.append(("quality", f"`{c}` has a few negative values ({p['negative_pct']}%) — possible errors or refunds."))
    for pair in corr.get("pearson_top", []):
        if abs(pair["r"]) >= 0.5:
            tag = "possible redundancy" if abs(pair["r"]) > 0.95 else "strong relationship"
            f.append(("relationship", f"`{pair['a']}` ↔ `{pair['b']}` r={pair['r']} ({tag})."))
    for e in effects:
        if e["eta_sq"] >= 0.1:
            f.append(("segment", f"`{e['category']}` explains {100*e['eta_sq']:.0f}% of variance in `{e['numeric']}` "
                                 f"(highest {e['highest']}, lowest {e['lowest']})."))
    for a in assoc:
        if a["cramers_v"] >= 0.3:
            tag = " — same information encoded twice (redundant, or leakage if one is a target)" if a["cramers_v"] > 0.98 else ""
            f.append(("relationship", f"`{a['a']}` and `{a['b']}` are associated (Cramér's V={a['cramers_v']}){tag}."))
    if ts:
        for x, m in ts["metrics"].items():
            if m["change_pct"] is not None and abs(m["change_pct"]) >= 15:
                word = "rose" if m["change_pct"] > 0 else "fell"
                f.append(("trend", f"Avg `{x}` {word} {abs(m['change_pct'])}% from the first to the last quarter of the timeline "
                                   f"(peak {m['peak_period']}, trough {m['trough_period']})."))
        if ts["last_period_partial"]:
            f.append(("quality", f"Last {ts['freq']} period of `{ts['date_column']}` looks partial — exclude it before calling a drop."))
        if ts["empty_periods"]:
            f.append(("quality", f"{ts['empty_periods']} empty {ts['freq']} periods in `{ts['date_column']}` — gaps in collection?"))
    if target and target in prof["columns"]:
        f.append(("target", f"Target `{target}` — see `target_drivers` in profile.json."))
    return [{"kind": k, "text": t} for k, t in f]


def target_drivers(df, target, nums, cats):
    y = df[target]
    out = []
    if pd.api.types.is_numeric_dtype(y) and y.nunique() > 2:
        for x in nums:
            if x == target:
                continue
            v = df[[x, target]].dropna().corr(method="spearman").iloc[0, 1]
            if pd.notna(v):
                out.append({"feature": x, "metric": "spearman", "value": r(v, 3)})
        for c in cats:
            if c == target:
                continue
            e = cat_num_effects(df, [c], [target])
            if e:
                out.append({"feature": c, "metric": "eta_sq", "value": e[0]["eta_sq"]})
    else:
        yb = y.astype(str)
        base = yb.value_counts(normalize=True)
        pos = base.index[-1] if len(base) == 2 else base.index[0]
        for x in nums:
            if x == target:
                continue
            g = df.groupby(yb)[x].median()
            out.append({"feature": x, "metric": "median_by_class", "value": {str(k): r(v) for k, v in g.items()}})
        for c in cats:
            if c == target:
                continue
            grp = (yb == pos).groupby(df[c].astype(str))
            rates = grp.mean()[grp.size() >= min_group(len(df))]
            if len(rates) > 1:
                out.append({"feature": c, "metric": f"rate_of_{pos}_spread",
                            "value": r(rates.max() - rates.min(), 3),
                            "max": {str(rates.idxmax()): r(rates.max(), 3)}, "min": {str(rates.idxmin()): r(rates.min(), 3)}})
    key = lambda d: -abs(d["value"]) if isinstance(d["value"], (int, float)) else 0
    return sorted(out, key=key)[:20]


# --------------------------------------------------------------------------- charts

def save(fig, path):
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def make_charts(df, prof, nums, cats, pear, ts_data, outdir):
    cd = outdir / "charts"
    cd.mkdir(parents=True, exist_ok=True)
    charts = []

    miss = pd.Series({c: p["missing_pct"] for c, p in prof["columns"].items() if p["missing_pct"]}).sort_values()
    if len(miss):
        miss = miss.tail(30)
        fig, ax = plt.subplots(figsize=(7, max(2, 0.28 * len(miss) + 1)))
        ax.barh(miss.index.astype(str), miss.values, color=ACCENT)
        ax.set_xlabel("% missing")
        ax.set_title("Missing values by column")
        charts.append(save(fig, cd / "01_missing.png"))

    if nums:
        show = nums[:16]
        cols = 4 if len(show) > 4 else len(show)
        rows = int(np.ceil(len(show) / cols))
        fig, axes = plt.subplots(rows, cols, figsize=(3.2 * cols, 2.4 * rows), squeeze=False)
        for ax, c in zip(axes.flat, show):
            v = df[c].dropna()
            lo, hi = v.quantile([0.005, 0.995])
            v = v[(v >= lo) & (v <= hi)]
            ax.hist(v, bins=40, color=ACCENT, alpha=0.9)
            ax.axvline(df[c].median(), color="#e07b39", lw=1.2, ls="--")
            ax.set_title(str(c), fontsize=9)
        for ax in list(axes.flat)[len(show):]:
            ax.axis("off")
        fig.suptitle("Numeric distributions (dashed = median, clipped at p0.5–p99.5)", fontsize=10)
        charts.append(save(fig, cd / "02_distributions.png"))

    show_c = [c for c in cats if prof["columns"][c]["unique"] > 1][:9]
    if show_c:
        cols = 3 if len(show_c) > 3 else len(show_c)
        rows = int(np.ceil(len(show_c) / cols))
        fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 2.8 * rows), squeeze=False)
        for ax, c in zip(axes.flat, show_c):
            vc = df[c].astype(str).value_counts().head(10)[::-1]
            ax.barh([s[:24] for s in vc.index], vc.values, color=ACCENT)
            ax.set_title(str(c), fontsize=9)
        for ax in list(axes.flat)[len(show_c):]:
            ax.axis("off")
        fig.suptitle("Top categories", fontsize=10)
        charts.append(save(fig, cd / "03_categories.png"))

    if pear is not None and len(pear) >= 2:
        m = pear.iloc[:25, :25]
        fig, ax = plt.subplots(figsize=(0.45 * len(m) + 3, 0.45 * len(m) + 2.5))
        im = ax.imshow(m.values, cmap="RdBu_r", vmin=-1, vmax=1)
        ax.set_xticks(range(len(m)), [str(c)[:18] for c in m.columns], rotation=60, ha="right")
        ax.set_yticks(range(len(m)), [str(c)[:18] for c in m.index])
        if len(m) <= 12:
            for i in range(len(m)):
                for j in range(len(m)):
                    ax.text(j, i, f"{m.values[i, j]:.2f}", ha="center", va="center", fontsize=7,
                            color="white" if abs(m.values[i, j]) > 0.6 else "black")
        fig.colorbar(im, ax=ax, shrink=0.7)
        ax.set_title("Pearson correlation")
        charts.append(save(fig, cd / "04_correlation.png"))

    if ts_data:
        counts, means = ts_data
        metrics = [c for c in means.columns[:5]]
        fig, axes = plt.subplots(1 + len(metrics), 1, figsize=(9, 2 * (1 + len(metrics))), sharex=True, squeeze=False)
        axes = axes.flat
        axes[0].plot(counts.index, counts.values, color=MUTED)
        axes[0].set_title("Rows per period")
        for ax, c in zip(axes[1:], metrics):
            ax.plot(means.index, means[c].values, color=ACCENT)
            ax.set_title(f"Mean {c} per period")
        charts.append(save(fig, cd / "05_time_trends.png"))

    return charts


# --------------------------------------------------------------------------- summary

def summary_md(prof):
    d = prof["dataset"]
    L = [f"# Profile: {d['name']}", "",
         f"- **Rows × cols:** {d['rows']:,} × {d['cols']}  |  **duplicates:** {d['duplicate_rows']}  "
         f"|  **memory:** {d['memory_mb']} MB  |  **overall missing:** {d['missing_cells_pct']}%"]
    if prof["load"].get("sheets"):
        L.append(f"- **Sheets:** {prof['load']['sheets']} (used `{prof['load']['sheet']}`)")
    if prof["load"].get("tables"):
        L.append(f"- **Tables:** {prof['load']['tables']} (query: `{prof['load']['query']}`)")
    if prof["type_conversions"]:
        L.append(f"- **Auto-converted:** {prof['type_conversions']}")
    if prof.get("sampled_from"):
        L.append(f"- **Sampled** {d['rows']:,} of {prof['sampled_from']:,} rows")
    L += ["", "## Columns", "", "| column | type | missing% | unique | summary |", "|---|---|---|---|---|"]
    for c, p in prof["columns"].items():
        if "median" in p:
            s = f"median {p['median']}, mean {p['mean']}, range [{p['min']}, {p['max']}]"
        elif p["semantic_type"] == "datetime":
            s = f"{p.get('min')} → {p.get('max')}"
        elif "top_values" in p:
            s = ", ".join(f"{k} ({v})" for k, v in list(p["top_values"].items())[:3])
        else:
            s = f"e.g. {p.get('examples', [''])[0][:40]!r}" if p.get("examples") else ""
        L.append(f"| {c} | {p['semantic_type']} | {p['missing_pct']} | {p['unique']} | {s} |")
    L += ["", "## Automated flags (leads, not conclusions)", ""]
    for kind in ("quality", "distribution", "relationship", "segment", "trend", "target"):
        items = [x["text"] for x in prof["findings"] if x["kind"] == kind]
        if items:
            L.append(f"**{kind.title()}**")
            L += [f"- {t}" for t in items[:12]]
            L.append("")
    if prof.get("target_drivers"):
        L += ["## Target drivers", ""]
        L += [f"- `{t['feature']}` {t['metric']}: {t['value']}" for t in prof["target_drivers"][:10]]
        L.append("")
    L += ["## Charts", ""] + [f"- {c}" for c in prof["charts"]]
    return "\n".join(L)


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--out", default=None)
    ap.add_argument("--sheet")
    ap.add_argument("--table")
    ap.add_argument("--query")
    ap.add_argument("--target", help="outcome column to explain")
    ap.add_argument("--sample", type=int, default=500_000, help="max rows to profile (default 500k)")
    a = ap.parse_args()

    df, meta = load(a.source, a.sheet, a.table, a.query)
    df.columns = [str(c).strip() for c in df.columns]
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(a.source.split("?")[0]).stem or "dataset")
    outdir = Path(a.out or f"data-story/{name}")
    outdir.mkdir(parents=True, exist_ok=True)

    full_n = len(df)
    if full_n > a.sample:
        df = df.sample(a.sample, random_state=0).sort_index()
    df, conversions = coerce_types(df)
    n = len(df)

    kinds = {c: semantic_type(df[c], n) for c in df.columns}
    nums = [c for c, k in kinds.items() if k == "numeric"]
    cats = [c for c, k in kinds.items() if k in ("categorical", "binary", "boolean")]
    dates = [c for c, k in kinds.items() if k == "datetime"]

    hashable = df.apply(lambda s: s.astype(str) if s.map(lambda v: isinstance(v, (list, dict))).any() else s)
    prof = {
        "load": meta,
        "type_conversions": conversions,
        "sampled_from": full_n if full_n > n else None,
        "dataset": {
            "name": name, "rows": n, "cols": df.shape[1],
            "duplicate_rows": int(hashable.duplicated().sum()),
            "memory_mb": r(df.memory_usage(deep=True).sum() / 1e6, 2),
            "missing_cells_pct": r(100 * df.isna().mean().mean(), 2),
            "column_types": {k: [c for c in kinds if kinds[c] == k] for k in sorted(set(kinds.values()))},
        },
        "columns": {c: profile_column(df[c], kinds[c], n) for c in df.columns},
    }

    corr, pear = correlations(df, nums)
    prof["correlations"] = corr
    prof["cat_num_effects"] = cat_num_effects(df, cats, nums)
    prof["cat_cat_assoc"] = cat_cat_assoc(df, cats)

    ts, ts_data = None, None
    if dates:
        dcol = max(dates, key=lambda c: df[c].notna().sum())
        res = time_analysis(df, dcol, nums)
        if res:
            ts, counts, means = res
            ts_data = (counts, means)
    prof["time_series"] = ts

    if a.target:
        if a.target not in df.columns:
            sys.exit(f"--target {a.target!r} not in columns: {list(df.columns)}")
        prof["target_drivers"] = target_drivers(df, a.target, nums, cats)

    prof["findings"] = findings(df, prof, corr, prof["cat_num_effects"], prof["cat_cat_assoc"], ts, a.target)
    prof["charts"] = make_charts(df, prof, nums, cats, pear, ts_data, outdir)

    try:
        df.to_parquet(outdir / "data.parquet")
        prof["cached_data"] = str(outdir / "data.parquet")
    except Exception as e:  # mixed-type object columns can fail
        prof["cached_data"] = None
        prof["cache_error"] = str(e)[:200]

    with open(outdir / "profile.json", "w") as f:
        json.dump(prof, f, indent=2, default=str)
    md = summary_md(prof)
    (outdir / "summary.md").write_text(md)
    print(md)
    print(f"\n[outputs] {outdir.resolve()}")


if __name__ == "__main__":
    main()

"""
merging_script.py
=================
Step 3 of the thesis data pipeline.

Merges the PR-level CSV files (from the mining script) with the defect signal
CSV files (from the defect signal script) into a single clean analysis dataset.

Applies the following filters before saving:
  - Excludes PRs with incomplete observation windows (obs_window_excluded = 1)
  - Excludes PRs with no defect signal record (failed during defect collection)

Produces:
  analysis_dataset.csv  — final analysis-ready dataset for R
  merge_report.txt      — human-readable merge report for documentation

Usage
-----
  python merging_script.py
"""
from pathlib import Path
import pandas as pd
 
# ─── INPUT FILES ──────────────────────────────────────────────────────────────
 
# INPUT_FILES = {
#     "formbricks/formbricks": {
#         "pr_csv":     "formbricks_post_adoption_pr_level.csv",
#         "defect_csv": "formbricks_post_adoption_defect_signals_tighter_defect_detection.csv",
#     },
#     "triggerdotdev/trigger.dev": {
#         "pr_csv":     "triggerdotdev_trigger_dev_post_adoption_pr_level.csv",
#         "defect_csv": "triggerdotdev_trigger_dev_post_adoption_defect_signals_tighter_defect_detection.csv",
#     },
# }


INPUT_FILES = {
    "formbricks/formbricks": {
        "pr_csv":     "formbricks_pre_adoption_pr_level.csv",
        "defect_csv": "formbricks_pre_adoption_defect_signals_tighter_defect_detection.csv",
    },
    "triggerdotdev/trigger.dev": {
        "pr_csv":     "triggerdotdev_trigger_dev_pre_adoption_pr_level.csv",
        "defect_csv": "triggerdotdev_trigger_dev_pre_adoption_defect_signals_tighter_defect_detection.csv",
    },
}

OUTPUT_CSV = Path("pre_adoption_full.csv")
REPORT_TXT = Path("merge_report_full_pre_tighter_defect_signal.txt")
 
# OUTPUT_CSV    = Path("post_adoption_with_tighter_defect_signal_without_human_vars.csv")
# REPORT_TXT    = Path("merge_report_post_tighter_defect_signal_no_human_vars.txt")
 
# Columns pulled from the defect signal CSV into the final dataset
DEFECT_COLS = [
    "pr_number", "repo",
    "defect_signal", "obs_window_excluded", "days_to_defect",
]
 
# Columns required in the PR-level CSV for the analysis
# REQUIRED_PR_COLS = {
#     "pr_number", "repo", "merged_at", "created_at",
#     "churn", "files_changed", "commits", "pr_lifetime_hours",
#     "ai_presence", "ai_visible_event_count",
#     "non_doc_file_names_json",
# }
REQUIRED_PR_COLS = {
    "pr_number", "repo", "merged_at", "created_at",
    "churn", "files_changed", "commits", "pr_lifetime_hours",
    "ai_presence", "ai_visible_event_count",
    "non_doc_file_names_json",
    "human_comment_count", "human_reviewer_count", "is_self_merged",
}
 
# Columns required in the defect signal CSV.
# Note: "repo" is NOT listed here because the defect signal script does not
# write a repo column — it is added programmatically after loading.
REQUIRED_DEFECT_COLS = {
    "pr_number",
    "defect_signal", "obs_window_excluded",
}
 
 
# ─── HELPERS ──────────────────────────────────────────────────────────────────
 
def load_and_validate(path: str, required_cols: set, label: str) -> pd.DataFrame:
    """Load a CSV and verify required columns are present."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    df = pd.read_csv(p)
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"{label} is missing columns: {missing}")
    print(f"  Loaded {label}: {len(df):,} rows, {len(df.columns)} columns")
    return df
 
 
def check_duplicates(df: pd.DataFrame, keys: list, label: str) -> None:
    """Warn if duplicate rows exist on the given key columns."""
    dupes = df.duplicated(subset=keys).sum()
    if dupes > 0:
        print(f"  WARNING: {dupes} duplicate rows on {keys} in {label} — keeping first")
 
 
# ─── MAIN ─────────────────────────────────────────────────────────────────────
 
def main() -> None:
    report_lines = []
 
    def log(msg: str = "") -> None:
        print(msg)
        report_lines.append(msg)
 
    log("=" * 60)
    log("DATASET MERGE REPORT")
    log("=" * 60)
 
    # ── Step 1: Load and concatenate PR-level data ────────────────────────────
    log("\n--- PR-level data ---")
    pr_frames = []
    for repo_name, paths in INPUT_FILES.items():
        df = load_and_validate(
            paths["pr_csv"], REQUIRED_PR_COLS,
            f"PR CSV [{repo_name}]"
        )
        # Ensure repo column matches the key exactly
        df["repo"] = repo_name
        check_duplicates(df, ["pr_number", "repo"], f"PR CSV [{repo_name}]")
        df = df.drop_duplicates(subset=["pr_number", "repo"], keep="first")
        pr_frames.append(df)

 
    pr_all = pd.concat(pr_frames, ignore_index=True)
    pr_all["merged_at"]  = pd.to_datetime(pr_all["merged_at"],  utc=True, format="ISO8601")
    pr_all["created_at"] = pd.to_datetime(pr_all["created_at"], utc=True, format="ISO8601")
    pr_all["pr_number"]  = pr_all["pr_number"].astype(int)
 
    log(f"\nTotal PR rows (all repos combined) : {len(pr_all):,}")
    for repo, count in pr_all["repo"].value_counts().items():
        log(f"  {repo}: {count:,}")
 
    # ── Step 2: Load and concatenate defect signal data ───────────────────────
    log("\n--- Defect signal data ---")
    ds_frames = []
    for repo_name, paths in INPUT_FILES.items():
        df = load_and_validate(
            paths["defect_csv"], REQUIRED_DEFECT_COLS,
            f"Defect CSV [{repo_name}]"
        )
        df["repo"] = repo_name
        check_duplicates(df, ["pr_number", "repo"], f"Defect CSV [{repo_name}]")
        df = df.drop_duplicates(subset=["pr_number", "repo"], keep="first")
        ds_frames.append(df)
 
    ds_all = pd.concat(ds_frames, ignore_index=True)
    ds_all["pr_number"] = ds_all["pr_number"].astype(int)
 
    log(f"\nTotal defect signal rows (all repos) : {len(ds_all):,}")
 
    # ── Step 3: Merge ─────────────────────────────────────────────────────────
    log("\n--- Merging ---")
 
    # Left join: every PR row kept, defect columns added where available
    merged = pr_all.merge(
        ds_all[DEFECT_COLS],
        on=["pr_number", "repo"],
        how="left",
        validate="many_to_one",   # each PR should match at most one defect row
    )
 
    log(f"Rows after merge               : {len(merged):,}")
 
    # ── Step 4: Diagnose unmatched rows ──────────────────────────────────────
    no_defect_record = merged["defect_signal"].isna() & merged["obs_window_excluded"].isna()
    n_unmatched = no_defect_record.sum()
    if n_unmatched > 0:
        log(f"\nWARNING: {n_unmatched} PRs have no defect signal record.")
        log("  These PRs were likely skipped due to errors in the defect script.")
        log("  They will be excluded from the analysis dataset.")
        # Save them for inspection
        merged[no_defect_record][["repo", "pr_number", "merged_at", "html_url"]].to_csv(
            "unmatched_prs.csv", index=False
        )
        log("  Saved to unmatched_prs.csv for inspection.")
    else:
        log("\nAll PRs have a defect signal record. No unmatched rows.")
 
    # ── Step 5: Apply exclusions ──────────────────────────────────────────────
    log("\n--- Applying exclusions ---")
 
    before = len(merged)
 
    # Exclude PRs with incomplete observation windows
    excl_window = merged["obs_window_excluded"] == 1
    n_excl_window = excl_window.sum()
 
    # Exclude PRs with no defect record (failed during collection)
    excl_no_record = no_defect_record
 
    analysis_df = merged[~excl_window & ~excl_no_record].copy()
 
    log(f"Excluded (incomplete window)   : {n_excl_window:,}")
    log(f"Excluded (no defect record)    : {n_unmatched:,}")
    log(f"Final analysis dataset         : {len(analysis_df):,} rows")
 
    # ── Step 6: Enforce data types ────────────────────────────────────────────
    analysis_df["defect_signal"]  = analysis_df["defect_signal"].astype(int)
    analysis_df["ai_presence"]    = analysis_df["ai_presence"].astype(int)
    analysis_df["pr_number"]      = analysis_df["pr_number"].astype(int)
    analysis_df["files_changed"]  = analysis_df["files_changed"].astype(int)
    analysis_df["commits"]        = analysis_df["commits"].astype(int)
 
    # ── Step 7: Sanity checks ─────────────────────────────────────────────────
    log("\n--- Sanity checks ---")
 
    log(f"\nRows per repository:")
    for repo, count in analysis_df["repo"].value_counts().items():
        log(f"  {repo}: {count:,}")
 
    log(f"\nDefect signal distribution:")
    log(f"  defect_signal = 1 : {analysis_df['defect_signal'].sum():,} "
        f"({analysis_df['defect_signal'].mean()*100:.1f}%)")
    log(f"  defect_signal = 0 : {(analysis_df['defect_signal'] == 0).sum():,}")
 
    log(f"\nAI presence distribution:")
    log(f"  ai_presence = 1   : {analysis_df['ai_presence'].sum():,} "
        f"({analysis_df['ai_presence'].mean()*100:.1f}%)")
    log(f"  ai_presence = 0   : {(analysis_df['ai_presence'] == 0).sum():,}")
 
    log(f"\nCross-tabulation (ai_presence x defect_signal):")
    crosstab = pd.crosstab(
        analysis_df["ai_presence"],
        analysis_df["defect_signal"],
        margins=True
    )
    log(crosstab.to_string())
 
    log(f"\nMissing values in key columns:")
    key_cols = [
        "defect_signal", "ai_presence", "churn",
        "files_changed", "commits", "pr_lifetime_hours"
    ]
    for col in key_cols:
        n_null = analysis_df[col].isna().sum()
        log(f"  {col}: {n_null} nulls")
 
    log(f"\nDescriptive statistics (key numeric columns):")
    log(analysis_df[key_cols].describe().round(2).to_string())
 
    # ── Step 8: Separation check ──────────────────────────────────────────────
    log("\n--- Separation check (logistic regression pre-check) ---")
    ct = pd.crosstab(analysis_df["ai_presence"], analysis_df["defect_signal"])
    for val in [0, 1]:
        if val in ct.index:
            zeros = ct.loc[val, 0] if 0 in ct.columns else 0
            ones  = ct.loc[val, 1] if 1 in ct.columns else 0
            if zeros == 0 or ones == 0:
                log(f"  WARNING: Complete separation detected for ai_presence={val}.")
                log(f"  Consider Firth logistic regression (logistf package in R).")
            else:
                log(f"  ai_presence={val}: {zeros} zeros, {ones} ones — OK")
 
    # ── Step 9: Save ──────────────────────────────────────────────────────────
    analysis_df.to_csv(OUTPUT_CSV, index=False)
    log(f"\nSaved analysis dataset to: {OUTPUT_CSV}")
    log(f"Total rows: {len(analysis_df):,}")
 
    # Save report
    REPORT_TXT.write_text("\n".join(report_lines), encoding="utf-8")
    log(f"Merge report saved to: {REPORT_TXT}")
 
 
if __name__ == "__main__":
    main()
 
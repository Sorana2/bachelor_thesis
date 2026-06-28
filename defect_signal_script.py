"""
defect_signal_script.py
=======================
Step 2 of the thesis data pipeline.

Reads the PR-level CSV produced by the mining script and, for each merged PR,
scans a 30-day observation window on the default branch for commits that:
  1. Contain a defect-related keyword in the commit message, AND
  2. Touch at least two non-documentation file that was also changed by the
     original PR.

If both conditions are met, defect_signal = 1. Otherwise defect_signal = 0.
PRs merged within OBSERVATION_WINDOW_DAYS of DATA_COLLECTION_DATE are marked
as obs_window_excluded = 1 and omitted from the main analysis.

Output
------
{PREFIX}_defect_signals.csv   — one row per PR with defect_signal and metadata
{PREFIX}_defect_failed_prs.txt — PR numbers that raised exceptions (skipped)
 
Resumable
---------
The script checks which PR numbers are already in the output CSV and skips them.
Re-run at any time to continue from where it stopped.

Usage
-----
  export GITHUB_TOKEN=your_token
  python defect_signal_script.py
"""

import os
import json
import time
import re
from datetime import timedelta
from pathlib import Path

import requests
import pandas as pd

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

TOKEN = os.getenv("GITHUB_TOKEN")
if not TOKEN:
    raise ValueError("GITHUB_TOKEN environment variable is not set.")

# Must match the OWNER / REPO used in the mining script.
OWNER = "triggerdotdev"
REPO  = "trigger.dev"
# Consistent prefix — same logic as the mining script.
if OWNER == REPO:
    PREFIX = OWNER
else:
    PREFIX = f"{OWNER}_{REPO}"

PREFIX = PREFIX.replace("/", "_").replace(".", "_").replace("-", "_")

# Observation window length (days) after each PR's merge timestamp. Subjective to change to what fits the data best 
OBSERVATION_WINDOW_DAYS = 30

# The date on which you ran (or are running) the mining script.
# PRs merged within OBSERVATION_WINDOW_DAYS of this date are excluded because
# their observation window is incomplete at the time of data collection.
DATA_COLLECTION_DATE = pd.to_datetime("2026-05-09T00:00:00Z", utc=True)

# Derived cutoff: PRs merged after this date get obs_window_excluded = 1.
OBSERVATION_CUTOFF = DATA_COLLECTION_DATE - timedelta(days=OBSERVATION_WINDOW_DAYS)

# GitHub API settings.
PER_PAGE      = 100
SLEEP_SECONDS = 0.1   # slightly conservative to stay well under rate limits

# Pilot mode: set to e.g. 50 to test on the first 50 PRs, None for full run.
MAX_PRS = None

# ─── DEFECT KEYWORD LIST ──────────────────────────────────────────────────────
# Case-insensitive substring match against the commit message.
# Kept deliberately broad; false positives are a known limitation (see thesis).
# DEFECT_KEYWORDS = [
#     "fix",
#     "bug",
#     "crash",
#     "defect",
#     "error",
#     "regression",
#     "patch",
#     "hotfix",
#     "revert",
# ]

# NON_DEFECT_PREFIXES = [
#     "chore:", "feat:", "docs:", "test:",
#     "ci:", "build:", "style:", "perf:", "refactor:"
# ]

# Better list of words to use and avoid
DEFECT_KEYWORDS = [
    "fix", "fixes", "fixed",
    "bug", "bugs", "bugfix",
    "crash", "crashes", "crashed",
    "defect", "defects",
    "error", "errors",
    "regression", "regressions",
    "patch", "patched",
    "hotfix",
    "revert", "reverts", "reverted",
]

NON_DEFECT_PREFIXES = [
    "chore:", "feat:", "feature:", "docs:", "test:",
    "ci:", "build:", "style:", "perf:", "refactor:",
    "wip", "wip:", "merge "
]


# ─── BOT COMMIT AUTHORS TO EXCLUDE ───────────────────────────────────────────
# These bots frequently produce commits with "fix" in the message (e.g.
# "fix(deps): bump ...") that are dependency updates, not defect repairs.
# Excluding them reduces noise in the defect proxy.
# Add or remove logins here based on what you observe in each repository.
EXCLUDED_COMMIT_AUTHOR_LOGINS = {
    "dependabot[bot]",
    "renovate[bot]",
    "github-actions[bot]",
    "snyk-bot",
    "renovate-bot",
}

# ─── DOCUMENTATION FILE EXTENSIONS ───────────────────────────────────────────
# Files with these extensions are excluded from the overlap check.
DOC_EXTENSIONS = (".md", ".txt", ".rst", ".mdx")

# # ─── I/O PATHS ───────────────────────────────────────────────────────────────
# PR_CSV            = Path(f"{PREFIX}_post_adoption_pr_level.csv")
# FILES_CSV         = Path(f"{PREFIX}_post_adoption_pr_files.csv")
# DEFECT_CSV        = Path(f"{PREFIX}_defect_signals.csv")
# FAILED_DEFECT_TXT = Path(f"{PREFIX}_defect_failed_prs.txt")

# PR_CSV            = Path(f"{PREFIX}_pre_adoption_pr_level.csv")
# DEFECT_CSV        = Path(f"{PREFIX}_pre_adoption_defect_signals.csv")
# FAILED_DEFECT_TXT = Path(f"{PREFIX}_pre_adoption_defect_failed_prs.txt")


# Check witha slightly tighter defect signal proxy
# For pre-adoption
# PR_CSV     = Path(f"{PREFIX}_pre_adoption_pr_level.csv")
# DEFECT_CSV = Path(f"{PREFIX}_pre_adoption_defect_signals_tighter_defect_detection.csv")
# FAILED_DEFECT_TXT = Path(f"{PREFIX}_pre_adoption_defect_failed_prs_tighter_defect_detection.txt")

# For post-adoption
PR_CSV = Path(f"{PREFIX}_post_adoption_pr_level.csv")
DEFECT_CSV = Path(f"{PREFIX}_post_adoption_defect_signals_tighter_defect_detection.csv")
FAILED_DEFECT_TXT = Path(f"{PREFIX}_post_adoption_defect_failed_prs_tighter_defect_detection.txt")

DEFECT_PATTERN = re.compile(
    r"(^|[^a-z])("
    r"fix|fixes|fixed|bug|bugs|bugfix|crash|crashes|crashed|"
    r"defect|defects|error|errors|regression|regressions|"
    r"patch|patched|hotfix|revert|reverts|reverted"
    r")([^a-z]|$)",
    re.IGNORECASE,
)



COMMIT_FILE_CACHE_PATH = Path(f"{PREFIX}_commit_file_cache.json")

LOCK_FILES = {
            "package.json", "package-lock.json",
            "yarn.lock", "pnpm-lock.yaml", "bun.lockb"
        }



# Faster



def load_commit_file_cache() -> dict[str, set[str]]:
    if not COMMIT_FILE_CACHE_PATH.exists():
        return {}

    try:
        with open(COMMIT_FILE_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {sha: set(files) for sha, files in raw.items()}
    except Exception as exc:
        print(f"[warning] could not load commit file cache: {exc}")
        return {}


COMMIT_FILE_CACHE = load_commit_file_cache()


def save_commit_file_cache() -> None:
    tmp_path = COMMIT_FILE_CACHE_PATH.with_suffix(".tmp")
    serializable = {
        sha: sorted(files)
        for sha, files in COMMIT_FILE_CACHE.items()
    }

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f)

    tmp_path.replace(COMMIT_FILE_CACHE_PATH)
# Paths for pre adoption


# ─── HTTP SESSION ─────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
    "Accept": "application/vnd.github+json",
})


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_json(url: str, params: dict = None, max_retries: int = 8) -> dict | list:
    """
    GET a GitHub API URL and return the parsed JSON.
    Retries on rate limits (403/429) and transient server errors (5xx).
    Raises RuntimeError after max_retries consecutive failures.
    """
    retries = 0
    while True:
        try:
            response = session.get(url, params=params, timeout=60)

            # ── Rate limit handling ───────────────────────────────────────────
            if response.status_code in (403, 429):
                text_lower = response.text.lower()
                remaining  = response.headers.get("X-RateLimit-Remaining")
                retry_after = response.headers.get("Retry-After")
                reset       = response.headers.get("X-RateLimit-Reset")

                is_rate_limit = (
                    remaining == "0"
                    or "rate limit" in text_lower
                    or "secondary rate limit" in text_lower
                )

                if is_rate_limit:
                    if retry_after is not None:
                        wait = int(retry_after) + 5
                    elif remaining == "0" and reset is not None:
                        wait = max(int(reset) - int(time.time()), 0) + 5
                    else:
                        wait = min(60 * (2 ** retries), 900)
                    print(f"  [rate limit] sleeping {wait}s …")
                    time.sleep(wait)
                    retries += 1
                    if retries > max_retries:
                        raise RuntimeError("Too many consecutive rate-limit retries.")
                    continue

            # ── Transient server errors ───────────────────────────────────────
            if response.status_code in (500, 502, 503, 504):
                wait = min(10 * (2 ** retries), 300)
                print(f"  [server error {response.status_code}] retrying in {wait}s …")
                time.sleep(wait)
                retries += 1
                if retries > max_retries:
                    response.raise_for_status()
                continue

            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            wait = min(10 * (2 ** retries), 300)
            print(f"  [timeout] retrying in {wait}s …")
            time.sleep(wait)
            retries += 1
            if retries > max_retries:
                raise

        except requests.exceptions.ConnectionError:
            wait = min(10 * (2 ** retries), 300)
            print(f"  [connection error] retrying in {wait}s …")
            time.sleep(wait)
            retries += 1
            if retries > max_retries:
                raise


def get_paginated(url: str, extra_params: dict = None) -> list:
    """Fetch all pages of a paginated GitHub endpoint and return a flat list."""
    if extra_params is None:
        extra_params = {}
    results = []
    page = 1
    while True:
        params = {"per_page": PER_PAGE, "page": page, **extra_params}
        data   = get_json(url, params=params)
        if not data:
            break
        results.extend(data)
        if len(data) < PER_PAGE:
            break
        page += 1
        time.sleep(SLEEP_SECONDS)
    return results


def is_doc_file(filename: str) -> bool:
    """Return True if the file should be excluded from the overlap check."""
    return filename.lower().endswith(DOC_EXTENSIONS)


# def contains_defect_keyword(message: str) -> bool:
#     """Return True if the commit message contains at least one defect keyword."""
#     message_lower = message.lower().strip()
#     # Exclude commits whose type is clearly not a defect fix
#     if any(message_lower.startswith(p) for p in NON_DEFECT_PREFIXES):
#         return False
#     return any(kw in message_lower for kw in DEFECT_KEYWORDS)

# Testing tighter defect signal function
def contains_defect_keyword(message: str) -> bool:
    """Return True if the commit subject contains a defect keyword."""
    subject = message.splitlines()[0].lower().strip()

    # Exclude commits whose subject is clearly not a defect fix
    if any(subject.startswith(prefix) for prefix in NON_DEFECT_PREFIXES):
        return False

    return bool(DEFECT_PATTERN.search(subject))

def is_bot_commit(commit_obj: dict) -> bool:
    """
    Return True if the commit was authored or committed by a known bot.
    Uses the 'author' and 'committer' GitHub user objects (may be None for
    unlinked accounts).
    """
    author_login    = (commit_obj.get("author")    or {}).get("login", "")
    committer_login = (commit_obj.get("committer") or {}).get("login", "")
    return (
        author_login    in EXCLUDED_COMMIT_AUTHOR_LOGINS
        or committer_login in EXCLUDED_COMMIT_AUTHOR_LOGINS
    )


def get_default_branch(owner: str, repo: str) -> str:
    """Fetch the repository's default branch name (usually 'main' or 'master')."""
    repo_data = get_json(f"https://api.github.com/repos/{owner}/{repo}")
    return repo_data.get("default_branch", "main")


def get_commit_non_doc_files(owner: str, repo: str, sha: str) -> set[str]:
    """
    Return the set of non-documentation files touched by a single commit.
    Cached by SHA so overlapping PR windows do not repeatedly fetch the same
    commit details from GitHub.
    """
    if sha in COMMIT_FILE_CACHE:
        return COMMIT_FILE_CACHE[sha]

    try:
        data = get_json(f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}")
        files = data.get("files", [])

        result = {
            f["filename"]
            for f in files
            if not is_doc_file(f["filename"])
        }

        COMMIT_FILE_CACHE[sha] = result
        return result

    except Exception as exc:
        print(f"    [warning] could not fetch files for commit {sha}: {exc}")
        COMMIT_FILE_CACHE[sha] = set()
        return set()

# ─── CORE FUNCTION ────────────────────────────────────────────────────────────

def compute_defect_signal(
    owner: str,
    repo: str,
    pr_number: int,
    merged_at: pd.Timestamp,
    pr_non_doc_files: set[str],
    default_branch: str,
) -> dict:
    """
    Compute the defect signal for one merged PR.

    Returns a dict with:
      defect_signal              — 0 or 1
      obs_window_excluded        — 1 if the observation window is incomplete
      commits_scanned            — total commits in the window
      bot_commits_excluded       — commits skipped because of bot authorship
      keyword_matching_commits   — commits that passed keyword filter
      defect_commit_sha          — SHA of the first defect-matching commit (or None)
      defect_commit_message      — truncated message of that commit (or None)
      defect_matched_files_json  — JSON list of overlapping files (or None)
      window_end                 — ISO timestamp of observation window end
    """
    window_start = merged_at
    window_end   = merged_at + timedelta(days=OBSERVATION_WINDOW_DAYS)

    # ── Incomplete observation window ─────────────────────────────────────────
    if merged_at > OBSERVATION_CUTOFF:
        return {
            "pr_number":                pr_number,
            "defect_signal":            None,
            "obs_window_excluded":      1,
            "commits_scanned":          None,
            "bot_commits_excluded":     None,
            "keyword_matching_commits": None,
            "defect_commit_sha":        None,
            "defect_commit_message":    None,
            "defect_matched_files_json": None,
            "defect_commit_date":       None,  
            "days_to_defect":           None, 
            "window_end":               window_end.isoformat(),
        }

    # ── No source files to match against → signal = 0 ────────────────────────
    # Edge case: PR only changed documentation files.
    if not pr_non_doc_files:
        return {
            "pr_number":                pr_number,
            "defect_signal":            0,
            "obs_window_excluded":      0,
            "commits_scanned":          0,
            "bot_commits_excluded":     0,
            "keyword_matching_commits": 0,
            "defect_commit_sha":        None,
            "defect_commit_message":    None,
            "defect_matched_files_json": None,
            "defect_commit_date":       None,  
            "days_to_defect":           None, 
            "window_end":               window_end.isoformat(),
        }

    # ── Fetch all commits in the observation window ───────────────────────────
    commits_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    all_commits = get_paginated(commits_url, extra_params={
        "sha":   default_branch,
        "since": window_start.isoformat(),
        "until": window_end.isoformat(),
    })

    total_commits   = len(all_commits)
    bot_excluded    = 0
    keyword_matched = 0

    for commit_obj in all_commits:

        # ── Skip bot commits ──────────────────────────────────────────────────
        if is_bot_commit(commit_obj):
            bot_excluded += 1
            continue

        sha     = commit_obj["sha"]
        message = (commit_obj.get("commit") or {}).get("message", "")

        # ── Skip commits without defect keywords ──────────────────────────────
        if not contains_defect_keyword(message):
            continue

        keyword_matched += 1
        if sha not in COMMIT_FILE_CACHE:
            time.sleep(SLEEP_SECONDS)

        commit_files = get_commit_non_doc_files(owner, repo, sha)
        
        overlap = (pr_non_doc_files & commit_files) - LOCK_FILES

        if len(overlap) >=2:
            # Found a defect signal — return immediately on first match.
             # Fetch commit date to compute days_to_defect for window validation
            commit_date_str = (
                commit_obj
                .get("commit", {})
                .get("committer", {})
                .get("date")
            )
            
            days_to_defect = None
            if commit_date_str:
                commit_ts = pd.to_datetime(commit_date_str, utc=True, format="ISO8601")
                days_to_defect = (commit_ts - merged_at).days

            return {
                "pr_number":                 pr_number,
                "defect_signal":             1,
                "obs_window_excluded":       0,
                "commits_scanned":           total_commits,
                "bot_commits_excluded":      bot_excluded,
                "keyword_matching_commits":  keyword_matched,
                "defect_commit_sha":         sha,
                "defect_commit_message":     message[:300],
                "defect_matched_files_json": json.dumps(sorted(overlap)),
                "defect_commit_date":        commit_date_str,
                "days_to_defect":            days_to_defect,
                "window_end":                window_end.isoformat(),
            }
        

    # ── No defect signal found ────────────────────────────────────────────────
    return {
        "pr_number":                 pr_number,
        "defect_signal":             0,
        "obs_window_excluded":       0,
        "commits_scanned":           total_commits,
        "bot_commits_excluded":      bot_excluded,
        "keyword_matching_commits":  keyword_matched,
        "defect_commit_sha":         None,
        "defect_commit_message":     None,
        "defect_matched_files_json": None,
        "defect_commit_date":        None,
        "days_to_defect":            None,
        "window_end":                window_end.isoformat(),
    
    }


# ─── CHECKPOINT HELPERS ───────────────────────────────────────────────────────

def load_processed_pr_numbers() -> set[int]:
    """Return the set of PR numbers already written to DEFECT_CSV."""
    if not DEFECT_CSV.exists():
        return set()
    existing = pd.read_csv(DEFECT_CSV, usecols=["pr_number"])
    return set(existing["pr_number"].astype(int).tolist())


def append_row_to_csv(row: dict, csv_path: Path) -> None:
    """Append a single result row to the output CSV."""
    pd.DataFrame([row]).to_csv(
        csv_path,
        mode="a",
        header=not csv_path.exists(),
        index=False,
    )


def log_failure(pr_number: int, error: Exception) -> None:
    """Append a failed PR number and its error message to the failure log."""
    with open(FAILED_DEFECT_TXT, "a", encoding="utf-8") as f:
        f.write(f"{pr_number}\t{error}\n")


# ─── LOAD INPUT DATA ─────────────────────────────────────────────────────────

def load_pr_dataframe() -> pd.DataFrame:
    """
    Load the PR-level CSV produced by the mining script.
    Validates required columns and parses timestamps.
    """
    if not PR_CSV.exists():
        raise FileNotFoundError(
            f"Mining output not found: {PR_CSV}\n"
            "Run the mining script first."
        )

    df = pd.read_csv(PR_CSV)

    required_columns = {
        "pr_number", "merged_at",
        "non_doc_file_names_json",
    }
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(
            f"PR CSV is missing expected columns: {missing}\n"
            "Make sure you are using the correct mining script output."
        )

    df["merged_at"] = pd.to_datetime(df["merged_at"], utc=True, format="ISO8601")
    df["pr_number"] = df["pr_number"].astype(int)

    return df


def parse_non_doc_files(json_str) -> set[str]:
    """
    Parse the non_doc_file_names_json column from the PR CSV into a Python set.
    Returns an empty set on any parse error.
    """
    if pd.isna(json_str) or str(json_str).strip() in ("", "[]", "nan"):
        return set()
    try:
        return set(json.loads(json_str))
    except (json.JSONDecodeError, TypeError):
        return set()


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print(f"Defect signal linker — {OWNER}/{REPO}")
    print(f"Observation window : {OBSERVATION_WINDOW_DAYS} days after merge")
    print(f"Data collection date : {DATA_COLLECTION_DATE.date()}")
    print(f"Observation cutoff   : {OBSERVATION_CUTOFF.date()}")
    print("=" * 60)

    # ── Load PR data ──────────────────────────────────────────────────────────
    pr_df = load_pr_dataframe()
    print(f"\nLoaded {len(pr_df)} post-adoption merged PRs from {PR_CSV}")

    # ── Apply pilot cap ───────────────────────────────────────────────────────
    if MAX_PRS is not None:
        pr_df = pr_df.head(MAX_PRS).copy()
        print(f"Pilot mode: capped at {MAX_PRS} PRs.")

    # ── Detect default branch (once) ─────────────────────────────────────────
    print(f"\nDetecting default branch for {OWNER}/{REPO} …")
    default_branch = get_default_branch(OWNER, REPO)
    print(f"Default branch: {default_branch}")

    # ── Observation window stats ──────────────────────────────────────────────
    excluded_count = (pr_df["merged_at"] > OBSERVATION_CUTOFF).sum()
    eligible_count = len(pr_df) - excluded_count
    print(f"\nPRs with complete observation window : {eligible_count}")
    print(f"PRs with incomplete window (excluded): {excluded_count}")

    # ── Resumability: skip already-processed PRs ──────────────────────────────
    processed = load_processed_pr_numbers()
    print(f"Already processed (from checkpoint)  : {len(processed)}")

    pending_df = pr_df[~pr_df["pr_number"].isin(processed)].copy()
    print(f"PRs still to process in this run     : {len(pending_df)}")

    if pending_df.empty:
        print("\nNothing to do — all PRs already processed.")
        _print_summary()
        return

    # ── Main loop ─────────────────────────────────────────────────────────────
    print("\nStarting defect signal computation …\n")

    for i, row in enumerate(pending_df.itertuples(index=False), start=1):
        pr_number       = int(row.pr_number)
        merged_at       = row.merged_at
        pr_non_doc_files = parse_non_doc_files(row.non_doc_file_names_json)

        pct = (i / len(pending_df)) * 100
        print(f"[{i}/{len(pending_df)} | {pct:.1f}%] PR #{pr_number} "
              f"(merged {merged_at.date()}) — {len(pr_non_doc_files)} source files")

        try:
            result = compute_defect_signal(
                owner          = OWNER,
                repo           = REPO,
                pr_number      = pr_number,
                merged_at      = merged_at,
                pr_non_doc_files = pr_non_doc_files,
                default_branch = default_branch,
            )

            append_row_to_csv(result, DEFECT_CSV)

            try:
                save_commit_file_cache()
            except Exception as cache_exc:
                print(f"  [warning] could not save commit cache: {cache_exc}")

            signal_label = (
                "EXCLUDED (window incomplete)" if result["obs_window_excluded"]
                else ("DEFECT FOUND" if result["defect_signal"] else "no defect")
            )
            print(f"  → {signal_label} "
                  f"(scanned {result['commits_scanned']} commits, "
                  f"{result['keyword_matching_commits']} keyword matches)")

        except Exception as exc:
            print(f"  [ERROR] PR #{pr_number}: {exc}")
            log_failure(pr_number, exc)
            print("  Skipping and continuing.")
            continue

        time.sleep(SLEEP_SECONDS)

    print("\nDone.")
    _print_summary()


def _print_summary() -> None:
    """Print a summary of the defect signal output file."""
    if not DEFECT_CSV.exists():
        print("\nNo output file found yet.")
        return

    df = pd.read_csv(DEFECT_CSV)
    total       = len(df)
    excluded    = int(df["obs_window_excluded"].sum())
    eligible    = total - excluded
    with_signal = int(df[df["obs_window_excluded"] != 1]["defect_signal"].sum())
    without     = eligible - with_signal

    print("\n" + "=" * 60)
    print("DEFECT SIGNAL SUMMARY")
    print("=" * 60)
    print(f"Total rows written          : {total}")
    print(f"Excluded (incomplete window): {excluded}")
    print(f"Eligible PRs                : {eligible}")
    print(f"  defect_signal = 1         : {with_signal} ({with_signal/eligible*100:.1f}%)" if eligible else "  (no eligible PRs)")
    print(f"  defect_signal = 0         : {without}")
    print(f"\nOutput: {DEFECT_CSV}")
    if FAILED_DEFECT_TXT.exists():
        print(f"Failed PRs logged in: {FAILED_DEFECT_TXT}")


if __name__ == "__main__":
    main()
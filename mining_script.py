import os
import time
import json
from pathlib import Path

import requests
import pandas as pd

TOKEN = os.getenv("GITHUB_TOKEN")
if not TOKEN:
    raise ValueError("GITHUB_TOKEN is not set")



OWNER = "triggerdotdev"
REPO = "trigger.dev"

if OWNER == REPO:
    PREFIX = OWNER
else:
    PREFIX = f"{OWNER}_{REPO}"

PREFIX = PREFIX.replace("/", "_").replace(".", "_").replace("-", "_")


# # Collect PRs merged BEFORE CodeRabbit adoption
# ADOPTION_DATE = pd.to_datetime("2024-09-18T00:00:00Z", utc=True)  # formbricks adoption

# # Collect 12 months before adoption as the pre-adoption window
# PRE_ADOPTION_START = pd.to_datetime("2023-10-01T00:00:00Z", utc=True)

# Common pre-adoption start used across repos for comparability
ADOPTION_DATE = pd.to_datetime("2024-09-18T00:00:00Z", utc=True)
PRE_ADOPTION_START = pd.to_datetime("2023-10-01T00:00:00Z", utc=True)

BOT_LOGIN = "coderabbitai[bot]"

# All bot logins excluded from human engagement counts
BOT_LOGINS = {
    "coderabbitai[bot]",
    "github-actions[bot]",
    "dependabot[bot]",
    "renovate[bot]",
    "snyk-bot",
    "vercel[bot]",
    "devin-ai-integration[bot]",
    "mintlify[bot]",
    "sonarqubecloud[bot]",
    "netlify[bot]",
    "linear[bot]",
}

PER_PAGE = 100
SLEEP_SECONDS = 0.2

# Pilot first. Change to None for the full run.
MAX_PRS = None

BASIC_CSV = Path(f"{PREFIX}_pre_adoption_basic.csv")
PR_CSV = Path(f"{PREFIX}_pre_adoption_pr_level.csv")
FILES_CSV = Path(f"{PREFIX}_pre_adoption_pr_files.csv")

CODERABBIT_STATUS_PHRASES = [
    "auto review skipped",
    "auto reviews are disabled",
    "invoke the @coderabbitai review command",
    "coderabbitai has been disabled",
    "commenter has been blocked",
    "rate limit",
]

def is_coderabbit_status_message(comment_body: str) -> bool:
    body_lower = comment_body.lower()
    return any(phrase in body_lower for phrase in CODERABBIT_STATUS_PHRASES)

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
    "Accept": "application/vnd.github+json",
})


def get_json(url, params=None, max_retries=8):
    retries = 0

    while True:
        try:
            response = session.get(url, params=params, timeout=60)

            if response.status_code in (403, 429):
                text_lower = response.text.lower()
                remaining = response.headers.get("X-RateLimit-Remaining")
                retry_after = response.headers.get("Retry-After")
                reset = response.headers.get("X-RateLimit-Reset")

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

                    print(f"Rate limited. Sleeping {wait}s")
                    time.sleep(wait)

                    retries += 1
                    if retries > max_retries:
                        raise RuntimeError("Too many rate-limit retries")
                    continue

            if response.status_code in (500, 502, 503, 504):
                wait = min(10 * (2 ** retries), 300)
                print(f"Server error {response.status_code}. Retrying in {wait}s for {url}")
                time.sleep(wait)

                retries += 1
                if retries > max_retries:
                    response.raise_for_status()
                continue

            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            wait = min(10 * (2 ** retries), 300)
            print(f"Request timed out. Retrying in {wait}s for {url}")
            time.sleep(wait)

            retries += 1
            if retries > max_retries:
                raise

        except requests.exceptions.ConnectionError:
            wait = min(10 * (2 ** retries), 300)
            print(f"Connection error. Retrying in {wait}s for {url}")
            time.sleep(wait)

            retries += 1
            if retries > max_retries:
                raise

def get_paginated(url, extra_params=None):
    if extra_params is None:
        extra_params = {}

    results = []
    page = 1

    while True:
        params = {"per_page": PER_PAGE, "page": page, **extra_params}
        data = get_json(url, params=params)

        if not data:
            break

        results.extend(data)

        if len(data) < PER_PAGE:
            break

        page += 1
        time.sleep(SLEEP_SECONDS)

    return results


def is_doc_file(filename: str) -> bool:
    filename_lower = filename.lower()
    doc_extensions = (".md", ".txt", ".rst")
    return filename_lower.endswith(doc_extensions)


def get_pre_adoption_merged_prs(owner, repo, pre_adoption_start, adoption_date):
    all_rows = []
    page = 1

    while True:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        params = {
            "state": "closed",
            "per_page": PER_PAGE,
            "page": page,
        }

        prs = get_json(url, params=params)

        if not prs:
            break

        merged_prs = [pr for pr in prs if pr.get("merged_at") is not None]

        page_rows = []
        for pr in merged_prs:
            merged_at = pd.to_datetime(pr["merged_at"], utc=True)

            if pre_adoption_start <= merged_at < adoption_date:
                page_rows.append({
                    "repo": f"{owner}/{repo}",
                    "pr_number": pr["number"],
                    "title": pr["title"],
                    "author": pr["user"]["login"],
                    "created_at": pr["created_at"],
                    "merged_at": pr["merged_at"],
                    "html_url": pr["html_url"],
                    "period": "pre_adoption", 
                })

        all_rows.extend(page_rows)
        
        print(
            f"Page {page}: fetched {len(prs)} closed PRs, "
            f"{len(merged_prs)} merged, "
            f"{len(page_rows)} pre-adoption window merged"
        )

        # Stop early if we have gone past the pre-adoption window
        # GitHub returns PRs in reverse chronological order, so once the
        # oldest PR on the current page is before pre_adoption_start,
        # all subsequent pages will also be out of range.
        if merged_prs:
            oldest_on_page = min(
                pd.to_datetime(pr["merged_at"], utc=True)
                for pr in merged_prs
            )
            if oldest_on_page < pre_adoption_start:
                print("Reached pre-adoption window start, stopping early.")
                break

        page += 1
        time.sleep(SLEEP_SECONDS)

    df = pd.DataFrame(all_rows)
    if df.empty:
        return df

    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df["merged_at"] = pd.to_datetime(df["merged_at"], utc=True)
    df = df.sort_values("merged_at", ascending=True).reset_index(drop=True)

    return df


def enrich_pr(owner, repo, pr_number):
    pr_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    pr_detail = get_json(pr_url)

    files_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
    files = get_paginated(files_url)

    reviews_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    reviews = get_paginated(reviews_url)

    review_comments_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    review_comments = get_paginated(review_comments_url)

    issue_comments_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    issue_comments = get_paginated(issue_comments_url)

    filenames = [f["filename"] for f in files]
    non_doc_filenames = [f for f in filenames if not is_doc_file(f)]

    ai_reviews_count = sum(
        1 for r in reviews
        if r.get("user", {}).get("login") == BOT_LOGIN 
        and not is_coderabbit_status_message(r.get("body", ""))
    )
   

    ai_review_comments_count = sum(
        1 for c in review_comments
        if c.get("user", {}).get("login") == BOT_LOGIN
        and not is_coderabbit_status_message(c.get("body", ""))
    )

    ai_issue_comments_count = sum(
        1 for c in issue_comments
        if c.get("user", {}).get("login") == BOT_LOGIN
        and not is_coderabbit_status_message(c.get("body", ""))
    )

    ai_visible_event_count = (
        ai_reviews_count
        + ai_review_comments_count
        + ai_issue_comments_count
    )

    created_at = pd.to_datetime(pr_detail["created_at"], utc=True)
    merged_at = pd.to_datetime(pr_detail["merged_at"], utc=True)
    pr_lifetime_hours = (merged_at - created_at).total_seconds() / 3600.0

    additions = pr_detail.get("additions", 0)
    deletions = pr_detail.get("deletions", 0)
    churn = additions + deletions

    # ── Human review engagement ───────────────────────────────────────────────────
    # Exclude known bot accounts from human counts.
    # Using both login name and account type for robustness.
  
    human_review_comments = sum(
        1 for c in review_comments
        if c.get("user", {}).get("login") not in BOT_LOGINS
        and c.get("user", {}).get("type") != "Bot"
    )

    human_issue_comments = sum(
        1 for c in issue_comments
        if c.get("user", {}).get("login") not in BOT_LOGINS
        and c.get("user", {}).get("type") != "Bot"
    )

    human_substantive_reviews = sum(
        1 for r in reviews
        if r.get("user", {}).get("login") not in BOT_LOGINS
        and r.get("user", {}).get("type") != "Bot"
        and r.get("body", "").strip()  # only reviews with actual content
    )

    human_comment_count = (
        human_review_comments
        + human_issue_comments
        + human_substantive_reviews
    )

    # Distinct human accounts who left at least one review
    human_reviewer_logins = {
        r.get("user", {}).get("login")
        for r in reviews
        if r.get("user", {}).get("login") not in BOT_LOGINS
        and r.get("user", {}).get("type") != "Bot"
        and r.get("user", {}).get("login") is not None
    }

    human_reviewer_count = len(human_reviewer_logins)

    # Self-merged: PR author merged their own PR
    merged_by_login = (pr_detail.get("merged_by") or {}).get("login", "")
    author_login     = (pr_detail.get("user")      or {}).get("login", "")
    is_self_merged   = int(
        bool(merged_by_login)
        and merged_by_login == author_login
    )

    pr_row = {
        "repo": f"{owner}/{repo}",
        "pr_number": pr_number,
        "title": pr_detail.get("title"),
        "author": pr_detail.get("user", {}).get("login"),
        "created_at": pr_detail.get("created_at"),
        "merged_at": pr_detail.get("merged_at"),
        "html_url": pr_detail.get("html_url"),
        "additions": additions,
        "deletions": deletions,
        "churn": churn,
        "files_changed": pr_detail.get("changed_files"),
        "commits": pr_detail.get("commits"),
        "pr_lifetime_hours": pr_lifetime_hours,
        "ai_presence": int(ai_visible_event_count > 0),
        "ai_visible_event_count": ai_visible_event_count,
        "ai_reviews_count": ai_reviews_count,
        "ai_review_comments_count": ai_review_comments_count,
        "ai_issue_comments_count": ai_issue_comments_count,
        "file_names_json": json.dumps(filenames),
        "non_doc_file_names_json": json.dumps(non_doc_filenames),
        "non_doc_files_changed_count": len(non_doc_filenames),
        "period": "pre_adoption", 
        "human_comment_count":   human_comment_count,
        "human_reviewer_count":  human_reviewer_count,
        "is_self_merged":        is_self_merged,
    }

    file_rows = []
    for filename in filenames:
        file_rows.append({
            "repo": f"{owner}/{repo}",
            "pr_number": pr_number,
            "filename": filename,
            "is_doc_file": int(is_doc_file(filename)),
        })

    return pr_row, file_rows


def append_dict_row_to_csv(row_dict, csv_path: Path):
    row_df = pd.DataFrame([row_dict])
    row_df.to_csv(csv_path, mode="a", header=not csv_path.exists(), index=False)


def append_dict_rows_to_csv(rows, csv_path: Path):
    if not rows:
        return
    rows_df = pd.DataFrame(rows)
    rows_df.to_csv(csv_path, mode="a", header=not csv_path.exists(), index=False)


def load_basic_df():
    if BASIC_CSV.exists():
        print(f"Loading existing basic file: {BASIC_CSV}")
        df = pd.read_csv(BASIC_CSV)
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True, format="ISO8601")
        df["merged_at"]  = pd.to_datetime(df["merged_at"],  utc=True, format="ISO8601")
        df = df.sort_values("merged_at", ascending=True).reset_index(drop=True)
        return df

    print("Fetching pre-adoption merged PR list from GitHub...")
    df = get_pre_adoption_merged_prs(OWNER, REPO, PRE_ADOPTION_START, ADOPTION_DATE)
    if df.empty:
        print("No pre-adoption merged PRs found.")
        raise SystemExit

    df.to_csv(BASIC_CSV, index=False)
    print(f"Saved basic file: {BASIC_CSV}")
    return df


def load_processed_pr_numbers():
    if not PR_CSV.exists():
        return set()

    existing = pd.read_csv(PR_CSV, usecols=["pr_number"])
    return set(existing["pr_number"].astype(int).tolist())


# -------------------------
# MAIN
# -------------------------

basic_df = load_basic_df()

print("\nRange check:")
print("Adoption date:", ADOPTION_DATE)
print("Earliest merged_at:", basic_df["merged_at"].min())
print("Latest merged_at:", basic_df["merged_at"].max())
print("Any PR after adoption?:", (basic_df["merged_at"] >= ADOPTION_DATE).any())
# This should always print False — if True something is wrong

print("\nEarliest pre-adoption merged PRs:")
print(basic_df[["pr_number", "created_at", "merged_at", "html_url"]].head(10))

if MAX_PRS is not None:
    basic_df = basic_df.head(MAX_PRS).copy()
    print(f"\nTesting mode: keeping first {len(basic_df)} pre-adoption merged PRs")
else:
    print(f"\nFull mode: {len(basic_df)} pre-adoption merged PRs")

processed_prs = load_processed_pr_numbers()
print(f"Already processed PRs found in checkpoint file: {len(processed_prs)}")

pending_df = basic_df[~basic_df["pr_number"].isin(processed_prs)].copy()
print(f"PRs still to process in this run: {len(pending_df)}")

for i, pr_number in enumerate(pending_df["pr_number"], start=1):
    try:
        print(f"Enriching PR {pr_number} ({i}/{len(pending_df)})")
        pr_row, pr_file_rows = enrich_pr(OWNER, REPO, int(pr_number))

        append_dict_row_to_csv(pr_row, PR_CSV)
        append_dict_rows_to_csv(pr_file_rows, FILES_CSV)

        pct = (i / len(pending_df)) * 100
        print(f"Saved PR {pr_number}. Progress: {i}/{len(pending_df)} ({pct:.1f}%)")


        time.sleep(SLEEP_SECONDS)

    # except Exception as e:
    #     print(f"Error on PR {pr_number}: {e}")
    #     print("Stopping run. Re-run the script to continue from the last saved PR.")
    #     raise

    except Exception as e:
        print(f"Error on PR {pr_number}: {e}")
        with open(f"{PREFIX}_pre_adoption_failed_prs.txt", "a", encoding="utf-8") as f:
            f.write(f"{pr_number}\t{e}\n")
        print("Skipping this PR and continuing.")
        continue

print("\nDone.")

if PR_CSV.exists():
    pr_df = pd.read_csv(PR_CSV)
    print("\nSummary from saved PR file:")
    print(f"Saved PR rows: {len(pr_df)}")
    print(f"PRs with ai_presence = 0 (expected — all pre-adoption): {int((pr_df['ai_presence'] == 0).sum())}")
    print(f"PRs with ai_presence = 1 (unexpected — investigate if > 0): {int(pr_df['ai_presence'].sum())}")

print(f"\nSaved basic PR list to: {BASIC_CSV}")
print(f"Saved PR-level data to: {PR_CSV}")
print(f"Saved file-level data to: {FILES_CSV}")
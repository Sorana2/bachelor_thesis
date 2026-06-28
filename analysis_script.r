# =============================================================================
# analysis_tighter.R
# BSc Thesis: AI-Assisted Code Review and Post-Merge Defect Follow-Up
# =============================================================================
#
# This script analyzes one prepared dataset at a time.
#
# Recommended runs:
#   1. post_adoption_with_tighter_defect_signal_without_human_vars.csv
#   2. post_adoption_full.csv
#   3. combined_pre_post_analysis.csv without human vars
#   4. combined_pre_post_analysis.csv with human vars, if available
#
# Main outcome:
#   defect_signal: 1 if a later default-branch commit within 30 days matched
#   the tightened defect proxy, 0 otherwise.
#
# =============================================================================


# ── 0. SETUP ─────────────────────────────────────────────────────────────────

library(tidyverse)
library(car)
library(broom)
library(logistf)

set.seed(42)

# ── CHANGE THESE FOR EACH RUN ────────────────────────────────────────────────
# 
# DATA_PATH <- "post_adoption_with_tighter_defect_signal_without_human_vars.csv"
# ANALYSIS_NAME <- "post_tighter_no_human"
# USE_HUMAN_VARS <- FALSE

# Examples:
# DATA_PATH <- "post_adoption_full.csv"
# ANALYSIS_NAME <- "post_tighter_with_human"
# USE_HUMAN_VARS <- TRUE

# DATA_PATH <- "combined_pre_post_analysis.csv"
# ANALYSIS_NAME <- "combined_tighter_no_human"
# USE_HUMAN_VARS <- FALSE

DATA_PATH <- "combined_pre_post_analysis.csv"
ANALYSIS_NAME <- "combined_tighter_with_human"
USE_HUMAN_VARS <- TRUE

OUT_DIR <- file.path("results", ANALYSIS_NAME)
dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

if (!file.exists(DATA_PATH)) {
  stop(paste("Dataset not found:", DATA_PATH))
}


# ── 1. LOAD AND VALIDATE DATA ────────────────────────────────────────────────

df_raw <- read_csv(DATA_PATH, show_col_types = FALSE)

cat("============================================================\n")
cat("Analysis:", ANALYSIS_NAME, "\n")
cat("Dataset:", DATA_PATH, "\n")
cat("Rows:", nrow(df_raw), "\n")
cat("Columns:", ncol(df_raw), "\n")
cat("============================================================\n\n")

required_cols <- c(
  "pr_number", "repo",
  "defect_signal", "ai_presence", "ai_visible_event_count",
  "churn", "files_changed", "commits", "pr_lifetime_hours"
)

if (USE_HUMAN_VARS) {
  required_cols <- c(
    required_cols,
    "human_comment_count",
    "human_reviewer_count",
    "is_self_merged"
  )
}

missing_cols <- setdiff(required_cols, names(df_raw))

if (length(missing_cols) > 0) {
  stop(paste("Missing required columns:", paste(missing_cols, collapse = ", ")))
}

# If period exists, this is a combined pre/post dataset.
has_period <- "period" %in% names(df_raw)

cat("Period column present:", has_period, "\n")
cat("Using human variables:", USE_HUMAN_VARS, "\n\n")


# ── 2. PREPARE DATA ─────────────────────────────────────────────────────────

df <- df_raw %>%
  mutate(
    repo = factor(repo),
    
    defect_signal = as.integer(defect_signal),
    ai_presence = as.integer(ai_presence),
    
    log_churn = log1p(churn),
    log_files_changed = log1p(files_changed),
    log_commits = log1p(commits),
    log_lifetime = log1p(pr_lifetime_hours),
    log_ai_count = log1p(ai_visible_event_count)
  )

if (has_period) {
  df <- df %>%
    mutate(period = factor(period, levels = c("pre_adoption", "post_adoption")))
}

if (USE_HUMAN_VARS) {
  df <- df %>%
    mutate(
      is_self_merged = as.integer(is_self_merged),
      log_human_comment_count = log1p(human_comment_count),
      log_human_reviewer_count = log1p(human_reviewer_count)
    )
}

# Basic integrity checks
if (any(is.na(df$defect_signal))) {
  stop("defect_signal contains missing values. Check merge/filtering.")
}

if (!all(df$defect_signal %in% c(0, 1))) {
  stop("defect_signal must contain only 0/1 values.")
}

if (!all(df$ai_presence %in% c(0, 1))) {
  stop("ai_presence must contain only 0/1 values.")
}

cat("=== Prepared data ===\n")
cat("Rows retained:", nrow(df), "\n")
cat("Repos:", paste(levels(df$repo), collapse = ", "), "\n")
if (has_period) {
  cat("Periods:", paste(levels(df$period), collapse = ", "), "\n")
}
cat("\n")


# ── 3. EXPLORATORY DATA ANALYSIS ────────────────────────────────────────────

cat("=== EDA ===\n\n")

cat("--- Defect signal distribution ---\n")
print(table(df$defect_signal))
cat("Overall defect rate:", round(mean(df$defect_signal) * 100, 1), "%\n\n")

cat("--- AI presence distribution ---\n")
print(table(df$ai_presence))
cat("AI presence rate:", round(mean(df$ai_presence) * 100, 1), "%\n\n")

cat("--- ai_presence x defect_signal ---\n")
ct_ai <- table(ai_presence = df$ai_presence, defect_signal = df$defect_signal)
print(ct_ai)
cat("\nDefect rate by AI presence (%):\n")
print(round(prop.table(ct_ai, margin = 1) * 100, 1))
cat("\n")

if (has_period) {
  cat("--- period x defect_signal ---\n")
  ct_period <- table(period = df$period, defect_signal = df$defect_signal)
  print(ct_period)
  cat("\nDefect rate by period (%):\n")
  print(round(prop.table(ct_period, margin = 1) * 100, 1))
  cat("\n")
}

repo_summary <- df %>%
  group_by(repo) %>%
  summarise(
    n_prs = n(),
    ai_presence_pct = round(mean(ai_presence) * 100, 1),
    defect_rate_pct = round(mean(defect_signal) * 100, 1),
    median_churn = median(churn),
    median_files = median(files_changed),
    median_commits = median(commits),
    median_lifetime_hrs = round(median(pr_lifetime_hours), 1),
    .groups = "drop"
  )

cat("--- Repository summary ---\n")
print(repo_summary)
cat("\n")

write_csv(repo_summary, file.path(OUT_DIR, "repo_summary.csv"))

# Correlation matrix for numeric predictors
cor_vars <- c(
  "ai_presence",
  "log_churn",
  "log_files_changed",
  "log_commits",
  "log_lifetime"
)

if (USE_HUMAN_VARS) {
  cor_vars <- c(
    cor_vars,
    "log_human_comment_count",
    "log_human_reviewer_count",
    "is_self_merged"
  )
}

cor_matrix <- cor(df[cor_vars], method = "spearman", use = "complete.obs")

cat("--- Spearman correlation matrix ---\n")
print(round(cor_matrix, 3))
cat("\n")

write_csv(
  as.data.frame(cor_matrix) %>% rownames_to_column("variable"),
  file.path(OUT_DIR, "correlation_matrix.csv")
)


# ── 4. PLOTS ─────────────────────────────────────────────────────────────────
# ── 4. PLOTS ─────────────────────────────────────────────────────────────────

# Shared theme
plot_theme <- theme_minimal(base_size = 13) +
  theme(
    panel.grid.major.x = element_blank(),
    plot.title = element_text(face = "bold"),
    plot.subtitle = element_text(color = "grey40")
  )

# Plot 1: Defect rate by AI presence with 95% CI error bars
p_defect_by_ai <- df %>%
  group_by(ai_presence) %>%
  summarise(
    n        = n(),
    defect_rate = mean(defect_signal),
    se       = sqrt(defect_rate * (1 - defect_rate) / n),
    ci_low   = pmax(0, defect_rate - 1.96 * se),
    ci_high  = pmin(1, defect_rate + 1.96 * se),
    .groups  = "drop"
  ) %>%
  mutate(
    ai_label = ifelse(ai_presence == 1, "CodeRabbit present", "No CodeRabbit")
  ) %>%
  ggplot(aes(x = ai_label, y = defect_rate, fill = ai_label)) +
  geom_col(width = 0.5) +
  geom_errorbar(
    aes(ymin = ci_low, ymax = ci_high),
    width = 0.10,
    linewidth = 0.5,
    color = "grey55"
  ) +
  geom_text(
    aes(
      y = ci_high + 0.035,
      label = paste0(round(defect_rate * 100, 1), "%\n(n=", n, ")")
    ),
    size = 3.6
  ) +
  scale_fill_manual(
    values = c("CodeRabbit present" = "#4C72B0", "No CodeRabbit" = "#C44E52"),
    guide = "none"
  ) +
  scale_y_continuous(
    labels = scales::percent_format(),
    limits = c(0, 0.65),
    expand = expansion(mult = c(0, 0.05))
  ) +
  labs(
    title    = "Defect signal rate by CodeRabbit presence",
    subtitle = "Unadjusted comparison, error bars show approximate 95% CI",
    x        = NULL,
    y        = "Defect signal rate"
  ) +
  plot_theme

ggsave(
  file.path(OUT_DIR, "plot_defect_rate_by_ai.png"),
  p_defect_by_ai,
  width = 6, height = 4.5, dpi = 150
)

# Plot 2 (combined runs only): Defect rate by period and repo with 95% CI
if (has_period) {
  p_defect_by_period <- df %>%
    group_by(period, repo) %>%
    summarise(
      n           = n(),
      defect_rate = mean(defect_signal),
      se          = sqrt(defect_rate * (1 - defect_rate) / n),
      ci_low      = pmax(0, defect_rate - 1.96 * se),
      ci_high     = pmin(1, defect_rate + 1.96 * se),
      .groups     = "drop"
    ) %>%
    mutate(
      period_label = dplyr::recode(as.character(period),
        "pre_adoption"  = "Pre-adoption",
        "post_adoption" = "Post-adoption"
      ),
      period_label = factor(period_label,
        levels = c("Pre-adoption", "Post-adoption")
      ),
      repo_label = dplyr::recode(as.character(repo),
        "formbricks/formbricks" = "Formbricks",
        "triggerdotdev/trigger.dev" = "Trigger.dev"
      )
    ) %>%
    ggplot(aes(x = period_label, y = defect_rate, fill = period_label)) +
    geom_col(width = 0.5) +
    geom_errorbar(
      aes(ymin = ci_low, ymax = ci_high),
      width = 0.10,
      linewidth = 0.5,
      color = "grey55"
    ) +
    geom_text(
      aes(
        y = ci_high + 0.035,
        label = paste0(round(defect_rate * 100, 1), "%\n(n=", n, ")")
      ),
      size = 3.4
    ) +
    scale_fill_manual(
      values = c("Pre-adoption" = "#C44E52", "Post-adoption" = "#4C72B0"),
      guide  = "none"
    ) +
    scale_y_continuous(
      labels = scales::percent_format(),
      limits = c(0, 0.65),
      expand = expansion(mult = c(0, 0.05))
    ) +
    facet_wrap(~ repo_label) +
    labs(
      title    = "Defect signal rate by period and repository",
      subtitle = "Unadjusted comparison, error bars show approximate 95% CI",
      x        = NULL,
      y        = "Defect signal rate"
    ) +
    plot_theme

  ggsave(
    file.path(OUT_DIR, "plot_defect_rate_by_period_repo.png"),
    p_defect_by_period,
    width = 8, height = 5, dpi = 150
  )
}


# ── 5. MODEL FORMULAS ────────────────────────────────────────────────────────

base_predictors <- c(
  "ai_presence",
  "log_churn",
  "log_files_changed",
  "log_commits",
  "log_lifetime",
  "repo"
)

# In combined pre/post data, period is necessary so the model does not confuse
# "before adoption" with "no AI presence".
if (has_period) {
  base_predictors <- c(
    "ai_presence",
    "period",
    "log_churn",
    "log_files_changed",
    "log_commits",
    "log_lifetime",
    "repo"
  )
}

if (USE_HUMAN_VARS) {
  base_predictors <- c(
    base_predictors,
    "log_human_comment_count",
    "log_human_reviewer_count",
    "is_self_merged"
  )
}

formula_main <- as.formula(
  paste("defect_signal ~", paste(base_predictors, collapse = " + "))
)

cat("=== Main model formula ===\n")
print(formula_main)
cat("\n")


# ── 6. MAIN LOGISTIC REGRESSION ──────────────────────────────────────────────

model_main <- glm(
  formula_main,
  data = df,
  family = binomial(link = "logit")
)

cat("=== Main model summary ===\n")
print(summary(model_main))
cat("\n")

tidy_main <- tidy(
  model_main,
  conf.int = TRUE,
  exponentiate = TRUE
) %>%
  mutate(across(where(is.numeric), ~ round(.x, 4)))

cat("=== Main model odds ratios ===\n")
print(tidy_main)
cat("\n")

write_csv(tidy_main, file.path(OUT_DIR, "main_model_odds_ratios.csv"))

# Model fit
fit_stats <- tibble(
  analysis = ANALYSIS_NAME,
  n = nrow(df),
  aic = AIC(model_main),
  null_deviance = model_main$null.deviance,
  residual_deviance = model_main$deviance,
  mcfadden_r2 = 1 - (model_main$deviance / model_main$null.deviance)
)

print(fit_stats)
write_csv(fit_stats, file.path(OUT_DIR, "main_model_fit_stats.csv"))


# ── 7. MODEL DIAGNOSTICS ─────────────────────────────────────────────────────

cat("\n=== Diagnostics ===\n\n")

# 7a. VIF
cat("--- VIF / multicollinearity check ---\n")

vif_raw <- car::vif(model_main)

if (is.matrix(vif_raw)) {
  # For factor variables, car::vif returns GVIF. The adjusted GVIF is comparable.
  vif_table <- as.data.frame(vif_raw) %>%
    rownames_to_column("term") %>%
    mutate(vif_comparable = `GVIF^(1/(2*Df))`)
} else {
  vif_table <- tibble(
    term = names(vif_raw),
    vif_comparable = as.numeric(vif_raw)
  )
}

print(vif_table)
write_csv(vif_table, file.path(OUT_DIR, "vif_check.csv"))

if (any(vif_table$vif_comparable > 5, na.rm = TRUE)) {
  cat("WARNING: Some VIF/GVIF values are above 5. Check collinearity.\n\n")
} else {
  cat("VIF check passed: no serious multicollinearity detected.\n\n")
}

# 7b. Simple separation pre-check
cat("--- Simple separation pre-check ---\n")
sep_table <- table(df$ai_presence, df$defect_signal)
print(sep_table)

if (any(sep_table == 0)) {
  cat("WARNING: Empty cell in ai_presence x defect_signal table.\n")
  cat("Firth logistic regression may be useful as a sensitivity check.\n\n")
  
  model_firth <- logistf(formula_main, data = df)
  firth_table <- tibble(
    term = names(model_firth$coefficients),
    estimate = model_firth$coefficients
  )
  
  write_csv(firth_table, file.path(OUT_DIR, "firth_model_coefficients.csv"))
} else {
  cat("No empty cells in ai_presence x defect_signal table.\n\n")
}

# 7c. Influential observations
cat("--- Influential observations ---\n")
cook_d <- cooks.distance(model_main)
influential <- cook_d > 4 / nrow(df)

cat("Number of influential observations:", sum(influential), "\n\n")

influence_summary <- tibble(
  n_total = nrow(df),
  n_influential = sum(influential),
  threshold = 4 / nrow(df)
)

write_csv(influence_summary, file.path(OUT_DIR, "influence_summary.csv"))

if (sum(influential) > 0) {
  df_no_infl <- df[!influential, ]
  
  model_no_infl <- glm(
    formula_main,
    data = df_no_infl,
    family = binomial(link = "logit")
  )
  
  tidy_no_infl <- tidy(
    model_no_infl,
    conf.int = TRUE,
    exponentiate = TRUE
  ) %>%
    mutate(across(where(is.numeric), ~ round(.x, 4)))
  
  write_csv(
    tidy_no_infl,
    file.path(OUT_DIR, "main_model_without_influential.csv")
  )
}


# ── 8. ROBUSTNESS CHECKS ─────────────────────────────────────────────────────

cat("=== Robustness checks ===\n\n")

# 8a. Count model: AI visible event count instead of binary ai_presence.
# This is only useful if ai_visible_event_count has variation.
if (length(unique(df$ai_visible_event_count)) > 1) {
  count_predictors <- base_predictors
  count_predictors[count_predictors == "ai_presence"] <- "log_ai_count"
  
  formula_count <- as.formula(
    paste("defect_signal ~", paste(count_predictors, collapse = " + "))
  )
  
  cat("--- Count model formula ---\n")
  print(formula_count)
  
  model_count <- glm(
    formula_count,
    data = df,
    family = binomial(link = "logit")
  )
  
  tidy_count <- tidy(
    model_count,
    conf.int = TRUE,
    exponentiate = TRUE
  ) %>%
    mutate(across(where(is.numeric), ~ round(.x, 4)))
  
  write_csv(tidy_count, file.path(OUT_DIR, "count_model_odds_ratios.csv"))
  
  comparison <- bind_rows(
    tidy_main %>% mutate(model = "main_binary_ai"),
    tidy_count %>% mutate(model = "count_ai_events")
  ) %>%
    filter(term %in% c("ai_presence", "log_ai_count")) %>%
    select(model, term, estimate, conf.low, conf.high, p.value)
  
  write_csv(comparison, file.path(OUT_DIR, "ai_model_comparison.csv"))
  
  cat("\nAI coefficient comparison:\n")
  print(comparison)
  cat("\n")
} else {
  cat("Skipping count model: ai_visible_event_count has no variation.\n\n")
}

# 8b. Per-repository models
cat("--- Per-repository models ---\n")

repo_results <- list()

for (r in levels(df$repo)) {
  df_repo <- df %>% filter(repo == r)
  
  cat("Repository:", r, "\n")
  cat("n =", nrow(df_repo), "\n")
  
  if (length(unique(df_repo$ai_presence)) < 2 ||
      length(unique(df_repo$defect_signal)) < 2) {
    cat("Skipping: no variation in ai_presence or defect_signal.\n\n")
    next
  }
  
  repo_predictors <- c(
    "ai_presence",
    "log_churn",
    "log_files_changed",
    "log_commits",
    "log_lifetime"
  )
  
  if (has_period) {
    repo_predictors <- c(
      "ai_presence",
      "period",
      "log_churn",
      "log_files_changed",
      "log_commits",
      "log_lifetime"
    )
  }
  
  if (USE_HUMAN_VARS) {
    repo_predictors <- c(
      repo_predictors,
      "log_human_comment_count",
      "log_human_reviewer_count",
      "is_self_merged"
    )
  }
  
  formula_repo <- as.formula(
    paste("defect_signal ~", paste(repo_predictors, collapse = " + "))
  )
  
  m_repo <- tryCatch(
    glm(formula_repo, data = df_repo, family = binomial(link = "logit")),
    error = function(e) NULL
  )
  
  if (is.null(m_repo)) {
    cat("Model failed.\n\n")
    next
  }
  
  repo_results[[r]] <- tidy(
    m_repo,
    conf.int = TRUE,
    exponentiate = TRUE
  ) %>%
    mutate(repo = r)
  
  cat("Model fitted.\n\n")
}

if (length(repo_results) > 0) {
  repo_model_table <- bind_rows(repo_results) %>%
    mutate(across(where(is.numeric), ~ round(.x, 4)))
  
  write_csv(repo_model_table, file.path(OUT_DIR, "per_repo_models.csv"))
}


# ── 9. SAVE EDA TABLES ───────────────────────────────────────────────────────

write_csv(
  df %>%
    group_by(repo, ai_presence, defect_signal) %>%
    summarise(n = n(), .groups = "drop"),
  file.path(OUT_DIR, "eda_ai_by_defect_counts.csv")
)

if (has_period) {
  write_csv(
    df %>%
      group_by(repo, period, defect_signal) %>%
      summarise(n = n(), .groups = "drop"),
    file.path(OUT_DIR, "eda_period_by_defect_counts.csv")
  )
}


# ── 10. FINAL KEY RESULT ─────────────────────────────────────────────────────

cat("\n============================================================\n")
cat("Analysis complete:", ANALYSIS_NAME, "\n")
cat("Outputs saved in:", OUT_DIR, "\n")
cat("============================================================\n\n")

ai_row <- tidy_main %>% filter(term == "ai_presence")

if (nrow(ai_row) == 1) {
  cat("Key result: ai_presence\n")
  cat(sprintf(
    "OR = %.4f, 95%% CI [%.4f, %.4f], p = %.4f\n",
    ai_row$estimate,
    ai_row$conf.low,
    ai_row$conf.high,
    ai_row$p.value
  ))
  
  if (ai_row$p.value < 0.05) {
    direction <- ifelse(ai_row$estimate < 1, "lower", "higher")
    cat(sprintf(
      "Interpretation: CodeRabbit presence is associated with %s odds of the defect signal, after adjustment.\n",
      direction
    ))
  } else {
    cat("Interpretation: ai_presence is not statistically significant at p < 0.05.\n")
  }
} else {
  cat("ai_presence was not found in the model output.\n")
}
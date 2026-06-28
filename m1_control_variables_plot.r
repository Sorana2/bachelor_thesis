library(tidyverse)

m1 <- read_csv(
  "results/post_tighter_no_human/main_model_odds_ratios.csv",
  show_col_types = FALSE
) %>%
  filter(term != "(Intercept)") %>%
  mutate(
    term_label = case_when(
      term == "ai_presence" ~ "CodeRabbit presence",
      term == "log_churn" ~ "Churn",
      term == "log_files_changed" ~ "Files changed",
      term == "log_commits" ~ "Commits",
      term == "log_lifetime" ~ "PR lifetime hours",
      term == "repotriggerdotdev/trigger.dev" ~ "Repo: Trigger.dev vs Formbricks",
      TRUE ~ term
    ),
    term_label = factor(term_label, levels = rev(c(
      "CodeRabbit presence",
      "Churn",
      "Files changed",
      "Commits",
      "PR lifetime hours",
      "Repo: Trigger.dev vs Formbricks"
    ))),
    label = paste0(
      "OR=", round(estimate, 2),
      " [", round(conf.low, 2), ", ", round(conf.high, 2), "]"
    )
  )

p_m1_forest <- ggplot(m1, aes(x = estimate, y = term_label)) +
  geom_vline(xintercept = 1, linetype = "dashed", color = "grey45") +
  geom_errorbar(
    aes(xmin = conf.low, xmax = conf.high),
    height = 0.15,
    linewidth = 0.5,
    color = "grey45"
  ) +
  geom_point(size = 2.5, color = "black") +
  geom_text(
    aes(x = 5.5, label = sprintf("%.2f [%.2f, %.2f]", estimate, conf.low, conf.high)),
    hjust = 0,
    size = 3.2,
    color = "grey20"
  ) +
  scale_x_continuous(
    limits = c(0.45, 7.0),
    breaks = c(0.5, 1, 2, 3, 4, 5)
  ) +
  labs(
    title = "Main model odds ratios",
    subtitle = "Post-adoption model with binary CodeRabbit presence, horizontal lines show 95% CIs",
    x = "Odds ratio",
    y = NULL
  ) +
  theme_minimal(base_size = 13) +
  theme(
    panel.grid.major.y = element_blank(),
    plot.title = element_text(face = "bold"),
    plot.subtitle = element_text(color = "grey40"),
    plot.margin = margin(5.5, 90, 5.5, 5.5)
  )

ggsave(
  "results/plot_m1_main_model_forest.png",
  p_m1_forest,
  width = 9.5,
  height = 4.8,
  dpi = 150
)

write_csv(m1, "results/m1_main_model_forest_table.csv")

cat("Saved plot to: results/plot_m1_main_model_forest.png\n")
cat("Saved table to: results/m1_main_model_forest_table.csv\n")
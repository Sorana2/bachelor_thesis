coef_compare <- coef_compare %>%
  mutate(
    label = paste0(
      "OR=", round(estimate, 2),
      " [", round(conf.low, 2), ", ", round(conf.high, 2), "]"
    )
  )

p_ai_coefficients <- ggplot(coef_compare, aes(x = estimate, y = model)) +
  geom_vline(xintercept = 1, linetype = "dashed", color = "grey45") +
  geom_errorbarh(
    aes(xmin = conf.low, xmax = conf.high),
    height = 0.18,
    linewidth = 0.6,
    color = "grey45"
  ) +
  geom_point(size = 2.8, color = "black") +
  geom_text(
    aes(x = 2.05, label = label),
    hjust = 0,
    size = 3.2
  ) +
  scale_x_continuous(
    limits = c(0.45, 2.75),
    breaks = c(0.5, 0.75, 1, 1.25, 1.5, 1.75, 2)
  ) +
  labs(
    title = "AI-related odds ratios across model specifications",
    subtitle = "Points show odds ratios; horizontal lines show 95% confidence intervals",
    x = "Odds ratio",
    y = NULL
  ) +
  theme_minimal(base_size = 13) +
  theme(
    panel.grid.major.y = element_blank(),
    plot.title = element_text(face = "bold"),
    plot.subtitle = element_text(color = "grey40"),
    legend.position = "none",
    plot.margin = margin(5.5, 40, 5.5, 5.5)
  )

ggsave(
  "results/plot_ai_coefficients_across_models.png",
  p_ai_coefficients,
  width = 10,
  height = 4.8,
  dpi = 150
)
write_csv(coef_compare, "results/ai_coefficients_across_models.csv")

cat("Saved plot to: results/plot_ai_coefficients_across_models.png\n")
cat("Saved table to: results/ai_coefficients_across_models.csv\n")
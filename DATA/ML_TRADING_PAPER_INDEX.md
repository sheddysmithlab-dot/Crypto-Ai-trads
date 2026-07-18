# ML Bitcoin Trading Paper — Fast Memory Index

Source: Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting (arXiv:2606.00060v1)
Authors: Andrei Bysik, Robert Ślepaczuk

Full paper text is in RAM via `ml_trading_memory.py` (microsecond fetch).
Do NOT paste the full paper into every LLM turn — fetch by id/alias.

## Agent takeaways (always relevant)

- Naive sign-based ML trades fail at ~10 bp transaction costs due to turnover.
- Cost-aware filter: trade only when |forecast| exceeds λ × transaction-cost threshold.
- Strongest reported setup: long-only XGBoost with cost-aware execution (>65% ann., Sharpe>1) — regime-dependent.
- Walk-forward (27-fold) evaluation required; avoid random CV leakage.
- Execution discipline often matters more than model architecture / feature enrichment.

## How to fetch
- `fetch_ml('cost aware')` → cost-aware execution filter
- `fetch_ml('xgboost')` / `fetch_ml('h2')` / `fetch_ml('walk forward')`
- `search_ml('transaction costs')`
- API: `GET /agent/ml/fetch?q=cost-aware`

## TOC

- `abstract` — Abstract (1527 chars)
- `1_introduction` — 1 Introduction (7198 chars)
- `2_1_return_predictability_and_financial_machine_learning` — 2.1 Return predictability and financial machine learning (2561 chars)
- `2_2_transaction_costs_and_the_prediction_to_trading_gap` — 2.2 Transaction costs and the prediction-to-trading gap (2453 chars)
- `2_3_walk_forward_evaluation_and_statistical_inference` — 2.3 Walk-forward evaluation and statistical inference (1693 chars)
- `2_4_positioning_of_this_paper` — 2.4 Positioning of this paper (2734 chars)
- `3_1_data_source_and_sample` — 3.1 Data source and sample (1595 chars)
- `3_2_preprocessing_and_target_variable` — 3.2 Preprocessing and target variable (1350 chars)
- `3_3_descriptive_statistics` — 3.3 Descriptive statistics (2602 chars)
- `3_4_walk_forward_empirical_design` — 3.4 Walk-forward empirical design (919 chars)
- `4_1_trading_strategy_and_cost_aware_execution` — 4.1 Trading strategy and cost-aware execution (2976 chars)
- `4_2_walk_forward_optimisation` — 4.2 Walk-forward optimisation (1685 chars)
- `4_3_feature_engineering` — 4.3 Feature engineering (3969 chars)
- `4_4_forecasting_models` — 4.4 Forecasting models (1156 chars)
- `4_4_1_xgboost` — 4.4.1 XGBoost (1442 chars)
- `4_4_2_long_short_term_memory` — 4.4.2 Long Short-Term Memory (1700 chars)
- `4_4_3_itransformer` — 4.4.3 iTransformer (3277 chars)
- `4_5_hyperparameter_optimisation` — 4.5 Hyperparameter optimisation (1209 chars)
- `4_6_performance_metrics` — 4.6 Performance metrics (2882 chars)
- `4_7_model_selection_criteria` — 4.7 Model selection criteria (2689 chars)
- `4_8_statistical_inference` — 4.8 Statistical inference (1428 chars)
- `5_1_h1_transaction_costs_and_naive_machine_learning_trading` — 5.1 H1: Transaction costs and naive machine-learning trading (6409 chars)
- `5_2_h2_cost_aware_execution` — 5.2 H2: Cost-aware execution (8883 chars)
- `5_3_h3_feature_enrichment` — 5.3 H3: Feature enrichment (8354 chars)
- `5_4_h4_model_architecture_comparison` — 5.4 H4: Model architecture comparison (6652 chars)
- `5_5_h5_loss_function_comparison` — 5.5 H5: Loss function comparison (5075 chars)
- `5_6_h6_model_selection_criterion` — 5.6 H6: Model selection criterion (4514 chars)
- `6_1_cost_aware_threshold_sensitivity` — 6.1 Cost-aware threshold sensitivity (4870 chars)
- `6_2_transaction_cost_sensitivity` — 6.2 Transaction-cost sensitivity (4742 chars)
- `6_3_fold_level_stability` — 6.3 Fold-level stability (3937 chars)
- `7_conclusion` — 7 Conclusion (5264 chars)
- `appendix_a_data_preprocessing_details` — Appendix A Data preprocessing details (323 chars)
- `a_1_missing_timestamp_audit` — A.1 Missing timestamp audit (1260 chars)
- `a_2_stationarity_diagnostics` — A.2 Stationarity diagnostics (1151 chars)
- `a_3_normality_diagnostic` — A.3 Normality diagnostic (743 chars)
- `appendix_b_hyperparameter_search_spaces_and_model_details` — Appendix B Hyperparameter search spaces and model details (229 chars)
- `b_1_hyperparameter_search_spaces` — B.1 Hyperparameter search spaces (2021 chars)
- `b_2_feature_selection_diagnostics` — B.2 Feature-selection diagnostics (1873 chars)
- `b_3_model_implementation_summary` — B.3 Model implementation summary (635 chars)
- `appendix_c_bootstrap_robustness_checks` — Appendix C Bootstrap robustness checks (335 chars)
- `c_1_h1_bootstrap_robustness_checks` — C.1 H1 bootstrap robustness checks (1872 chars)
- `c_2_h2_bootstrap_robustness_checks` — C.2 H2 bootstrap robustness checks (1971 chars)
- `c_3_h3_bootstrap_robustness_checks` — C.3 H3 bootstrap robustness checks (4171 chars)
- `c_4_h4_bootstrap_robustness_checks` — C.4 H4 bootstrap robustness checks (2247 chars)
- `c_5_h6_bootstrap_robustness_checks` — C.5 H6 bootstrap robustness checks (2689 chars)
- `appendix_d_fold_level_decomposition` — Appendix D Fold-level decomposition (6208 chars)
- `appendix_e_validation_loss_diagnostic` — Appendix E Validation loss diagnostic (5245 chars)
- `data_and_code_availability` — Data and code availability (209 chars)
- `references` — References (5790 chars)

**Loaded:** 49 sections · 146717 chars
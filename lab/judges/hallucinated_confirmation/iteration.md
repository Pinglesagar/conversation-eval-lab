# `hallucinated_confirmation`: prompt v1 -> v2

Same label set (`cd660a33b628a6ca`, 24 items), same model (`synthetic/deterministic-stand-in`). Only the prompt changed.

| metric | v1 | v2 | delta |
|---|---|---|---|
| true positive rate | 1.000 (8/8) | 1.000 (8/8) | +0.000 |
| true negative rate | 0.625 (10/16) | 0.938 (15/16) | +0.312 |
| precision | 0.571 (8/14) | 0.889 (8/9) | +0.317 |
| F1 | 0.727 (16/22) | 0.941 (16/17) | +0.214 |
| raw agreement | 0.750 (18/24) | 0.958 (23/24) | +0.208 |
| Cohen's kappa | 0.526 | 0.909 | +0.383 |
| false positives | 6 | 1 | -5 |
| false negatives | 0 | 0 | +0 |

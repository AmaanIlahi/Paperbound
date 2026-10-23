# Paperbound evals

The synthetic set is **template-generated** (seeded; a few layouts per type, so it is easier than real documents). CUAD is **real contracts** scored by **span overlap** against human labels. Neither is a benchmark of production accuracy.

## synthetic (run 20261023_022926, as of 2026-10-22, 40 documents)

| Group | Docs | Classif. | Field acc. | Missing ok | Grounding | Page acc. | Oblig. | Brief lines | Actions | Cost/doc | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| insurance_certificate/all | 20 | 100.0% | 98.4% | 66.7% | 98.1% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 12.8 s |
| insurance_certificate/clean | 12 | 100.0% | 98.3% | - | 98.3% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 18.0 s |
| insurance_certificate/injection | 3 | 100.0% | 98.0% | - | 98.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 9.7 s |
| insurance_certificate/late_page | 2 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 8.2 s |
| insurance_certificate/missing_field | 3 | 100.0% | 98.0% | 66.7% | 95.9% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0012 | 8.6 s |
| lease/all | 20 | 100.0% | 97.1% | 66.7% | 98.9% | 100.0% | 70.0% | 70.1% | 50.0% | $0.0014 | 12.2 s |
| lease/clean | 12 | 100.0% | 97.0% | - | 99.4% | 100.0% | 66.7% | 67.7% | 41.7% | $0.0014 | 17.5 s |
| lease/injection | 3 | 100.0% | 100.0% | - | 100.0% | 100.0% | 66.7% | 91.7% | 66.7% | $0.0013 | 10.3 s |
| lease/late_page | 2 | 100.0% | 100.0% | - | 96.4% | 100.0% | 100.0% | 81.2% | 100.0% | $0.0017 | 12.2 s |
| lease/missing_field | 3 | 100.0% | 92.9% | 66.7% | 97.5% | 100.0% | 66.7% | 44.4% | 33.3% | $0.0011 | 11.1 s |

### Injection

| Type | Attacks | Model resisted | System held | Flagged | Unflagged breach | Reached actions |
|---|---|---|---|---|---|---|
| insurance_certificate | 3 | 100.0% | 100.0% | 0 | 0 | 0 |
| lease | 3 | 100.0% | 100.0% | 0 | 0 | 0 |

### Calibration (all variants)

**insurance_certificate**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 0 | - | - |
| 0.7-0.9 | 6 | 100.0% | 0.87 |
| >=0.9 | 367 | 98.4% | 1.00 |

ECE: 0.015 over 373 fields.

**lease**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 11 | 72.7% | 0.70 |
| 0.7-0.9 | 3 | 66.7% | 0.87 |
| >=0.9 | 264 | 98.5% | 1.00 |

ECE: 0.017 over 278 fields.


### Top failure modes

- action: 10
- wrong_field: current_rent: 7
- obligation: 6
- brief_withheld: deadline: 6
- brief_withheld: days_remaining: 6
- wrong_field: type: 5
- brief_wrong: current_rent: 4
- brief_wrong: next_rent: 4

Failure log: `evals/results/failures_synthetic_20261023_022926.json`


## cuad (run 20261023_023204, as of 2026-10-22, 50 documents)

| Category | Labeled | Token P | Token R | Token F1 | Detected | False pos. | Date exact | Grounding |
|---|---|---|---|---|---|---|---|---|
| effective_date | 38/50 | 55.9% | 78.6% | 56.1% | 100.0% | 100.0% | 77.3% (22) | 100.0% |
| expiration_date | 39/50 | 89.3% | 40.1% | 50.2% | 92.3% | 36.4% | 83.3% (6) | 97.5% |
| notice_to_terminate_renewal | 14/50 | 92.9% | 29.9% | 43.4% | 92.9% | 63.9% | - | 94.4% |
| parties | 50/50 | 45.1% | 72.9% | 51.2% | 100.0% | - | - | 82.0% |
| renewal_term | 18/50 | 94.4% | 38.1% | 50.6% | 94.4% | 46.9% | - | 93.8% |
| all | 159/250 | 68.3% | 58.5% | 51.4% | 96.9% | 59.3% | 78.6% (28) | 93.3% |

Cost per contract $0.0014, 8604 tokens, p95 latency 4.9 s.

### Top failure modes

- unlabeled_extraction: notice_to_terminate_renewal: 23
- low_overlap: parties: 21
- low_overlap: expiration_date: 19
- unlabeled_extraction: renewal_term: 15
- low_overlap: effective_date: 13
- unlabeled_extraction: effective_date: 12
- low_overlap: renewal_term: 8
- low_overlap: notice_to_terminate_renewal: 8

Failure log: `evals/results/failures_cuad_20261023_023204.json`



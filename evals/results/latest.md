# Paperbound evals

The synthetic set is **template-generated** (seeded; a few layouts per type, so it is easier than real documents). CUAD is **real contracts** scored by **span overlap** against human labels. Neither is a benchmark of production accuracy.

## synthetic (run insurance_certificate 20261024_200857, lease 20261024_200857, as of 2026-10-22, 40 documents)

| Group | Docs | Classif. | Field acc. | Missing ok | Grounding | Page acc. | Oblig. | Brief lines | Actions | Cost/doc | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| insurance_certificate/all | 20 | 100.0% | 98.7% | 66.7% | 98.1% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 13.5 s |
| insurance_certificate/clean | 12 | 100.0% | 98.7% | - | 98.3% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 15.3 s |
| insurance_certificate/injection | 3 | 100.0% | 98.0% | - | 98.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 11.5 s |
| insurance_certificate/late_page | 2 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 8.2 s |
| insurance_certificate/missing_field | 3 | 100.0% | 98.0% | 66.7% | 95.9% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0012 | 8.3 s |
| lease/all | 20 | 100.0% | 98.2% | 100.0% | 100.0% | 100.0% | 100.0% | 85.1% | 80.0% | $0.0017 | 17.8 s |
| lease/clean | 12 | 100.0% | 97.6% | - | 100.0% | 100.0% | 100.0% | 80.2% | 75.0% | $0.0018 | 18.3 s |
| lease/injection | 3 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 11.5 s |
| lease/late_page | 2 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0017 | 11.2 s |
| lease/missing_field | 3 | 100.0% | 97.6% | 100.0% | 100.0% | 100.0% | 100.0% | 77.8% | 66.7% | $0.0013 | 11.7 s |

### Injection

| Type | Attacks | Model resisted | System held | Flagged | Unflagged breach | Reached actions |
|---|---|---|---|---|---|---|
| insurance_certificate | 3 | 100.0% | 100.0% | 0 | 0 | 0 |
| lease | 3 | 100.0% | 100.0% | 0 | 0 | 0 |

### Calibration (all variants)

**insurance_certificate**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 1 | 100.0% | 0.62 |
| 0.7-0.9 | 6 | 100.0% | 0.87 |
| >=0.9 | 366 | 98.6% | 1.00 |

ECE: 0.013 over 373 fields.

**lease**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 5 | 40.0% | 0.70 |
| 0.7-0.9 | 0 | - | - |
| >=0.9 | 272 | 99.3% | 1.00 |

ECE: 0.012 over 277 fields.


### Top failure modes

- wrong_field: current_rent: 5
- wrong_field: type: 4
- action: 4
- brief_withheld: current_rent: 3
- brief_withheld: next_rent: 3
- brief_withheld: holdover_rent: 3
- brief_withheld: holdover_extra: 3
- brief_withheld: holdover_percent: 3

Failure log: `evals/results/failures_synthetic_20261024_200857.json`

- DROP synthetic/insurance_certificate/missing_field/grounding_rate: 100.0% -> 95.9% (-4.08 points)
- DROP synthetic/insurance_certificate/missing_field/usable_rate: 100.0% -> 95.8% (-4.17 points)
- DROP synthetic/lease/injection/usable_rate: 100.0% -> 97.6% (-2.38 points)

## generic (run 20261024_200922, as of 2026-10-22, 5 documents)

| Docs | Routed generic | Field acc. | Field precision | Parties | Dates | Amounts | Obligations | Grounding | Page acc. | Oblig. set | Actions | Cap held | Cost/doc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0005 |

Label guess right: 100.0%; field label right (of matched): 88.6%; email drafts proposed: 0; extra fields not in the ground truth: 0.

### Top failure modes

- none

Failure log: `evals/results/failures_generic_20261024_200922.json`


## cuad (run 20261024_201156, as of 2026-10-22, 50 documents)

| Category | Labeled | Token P | Token R | Token F1 | Detected | False pos. | Date exact | Grounding |
|---|---|---|---|---|---|---|---|---|
| effective_date | 38/50 | 56.2% | 80.6% | 57.8% | 100.0% | 100.0% | 80.0% (25) | 100.0% |
| expiration_date | 39/50 | 83.9% | 37.3% | 47.2% | 87.2% | 36.4% | 83.3% (6) | 97.4% |
| notice_to_terminate_renewal | 14/50 | 92.9% | 29.6% | 43.1% | 92.9% | 58.3% | - | 94.1% |
| parties | 50/50 | 43.8% | 71.7% | 50.1% | 100.0% | - | - | 80.0% |
| renewal_term | 18/50 | 94.4% | 38.2% | 50.8% | 94.4% | 40.6% | - | 93.3% |
| all | 159/250 | 66.7% | 57.9% | 50.7% | 95.6% | 54.9% | 80.6% (31) | 92.6% |

Cost per contract $0.0014, 8601 tokens, p95 latency 5.5 s.

### Top failure modes

- low_overlap: parties: 22
- unlabeled_extraction: notice_to_terminate_renewal: 21
- low_overlap: expiration_date: 17
- unlabeled_extraction: renewal_term: 13
- unlabeled_extraction: effective_date: 12
- low_overlap: effective_date: 11
- low_overlap: renewal_term: 8
- low_overlap: notice_to_terminate_renewal: 8

Failure log: `evals/results/failures_cuad_20261024_201156.json`

- DROP cuad/all/date_exact: 86.2% -> 80.6% (-5.56 points)
- DROP cuad/effective_date/date_exact: 87.0% -> 80.0% (-6.96 points)
- DROP cuad/expiration_date/f1: 49.2% -> 47.2% (-2.01 points)
- DROP cuad/expiration_date/recall: 39.6% -> 37.3% (-2.32 points)
- DROP cuad/notice_to_terminate_renewal/grounding_rate: 97.1% -> 94.1% (-2.94 points)
- DROP cuad/parties/grounding_rate: 84.0% -> 80.0% (-4.0 points)
- DROP cuad/renewal_term/grounding_rate: 96.7% -> 93.3% (-3.33 points)


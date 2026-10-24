# Paperbound evals

The synthetic set is **template-generated** (seeded; a few layouts per type, so it is easier than real documents). CUAD is **real contracts** scored by **span overlap** against human labels. Neither is a benchmark of production accuracy.

## synthetic (run insurance_certificate 20261024_185557, lease 20261024_185557, as of 2026-10-22, 40 documents)

| Group | Docs | Classif. | Field acc. | Missing ok | Grounding | Page acc. | Oblig. | Brief lines | Actions | Cost/doc | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| insurance_certificate/all | 20 | 100.0% | 98.9% | 66.7% | 99.2% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 11.9 s |
| insurance_certificate/clean | 12 | 100.0% | 98.7% | - | 98.7% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 12.0 s |
| insurance_certificate/injection | 3 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 9.9 s |
| insurance_certificate/late_page | 2 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 9.0 s |
| insurance_certificate/missing_field | 3 | 100.0% | 98.0% | 66.7% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0012 | 8.7 s |
| lease/all | 20 | 100.0% | 97.5% | 66.7% | 99.3% | 100.0% | 100.0% | 80.9% | 70.0% | $0.0017 | 14.9 s |
| lease/clean | 12 | 100.0% | 97.6% | - | 98.8% | 100.0% | 100.0% | 81.2% | 75.0% | $0.0016 | 14.9 s |
| lease/injection | 3 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 9.6 s |
| lease/late_page | 2 | 100.0% | 96.4% | - | 100.0% | 100.0% | 100.0% | 68.8% | 50.0% | $0.0025 | 15.2 s |
| lease/missing_field | 3 | 100.0% | 95.2% | 66.7% | 100.0% | 100.0% | 100.0% | 66.7% | 33.3% | $0.0013 | 10.6 s |

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
| 0.7-0.9 | 0 | - | - |
| >=0.9 | 373 | 98.9% | 1.00 |

ECE: 0.008 over 373 fields.

**lease**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 6 | 50.0% | 0.70 |
| 0.7-0.9 | 2 | 100.0% | 0.90 |
| >=0.9 | 270 | 98.5% | 1.00 |

ECE: 0.018 over 278 fields.


### Top failure modes

- wrong_field: current_rent: 6
- action: 6
- wrong_field: type: 3
- brief_wrong: current_rent: 3
- brief_wrong: next_rent: 3
- brief_wrong: holdover_rent: 3
- brief_wrong: holdover_extra: 3
- brief_withheld: current_rent: 3

Failure log: `evals/results/failures_synthetic_20261024_185557.json`

- DROP synthetic/lease/all/action_correct: 75.0% -> 70.0% (-5.0 points)
- DROP synthetic/lease/all/missing_field_correct: 100.0% -> 66.7% (-33.33 points)
- DROP synthetic/lease/injection/usable_rate: 100.0% -> 97.6% (-2.38 points)
- DROP synthetic/lease/late_page/action_correct: 100.0% -> 50.0% (-50.0 points)
- DROP synthetic/lease/missing_field/action_correct: 66.7% -> 33.3% (-33.33 points)
- DROP synthetic/lease/missing_field/missing_field_correct: 100.0% -> 66.7% (-33.33 points)

## generic (run 20261024_185622, as of 2026-10-22, 5 documents)

| Docs | Routed generic | Field acc. | Field precision | Parties | Dates | Amounts | Obligations | Grounding | Page acc. | Oblig. set | Actions | Cap held | Cost/doc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 100.0% | 95.3% | 95.3% | 100.0% | 100.0% | 77.8% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0005 |

Label guess right: 100.0%; field label right (of matched): 87.9%; email drafts proposed: 0; extra fields not in the ground truth: 2.

### Top failure modes

- missed_field: amount: 2
- extra_field: amount: 2

Failure log: `evals/results/failures_generic_20261024_185622.json`

- DROP generic/generic/all/amount_accuracy: 100.0% -> 77.8% (-22.22 points)
- DROP generic/generic/all/field_accuracy: 100.0% -> 95.3% (-4.65 points)
- DROP generic/generic/all/field_precision: 100.0% -> 95.3% (-4.65 points)
- DROP generic/generic/all/usable_rate: 97.7% -> 93.0% (-4.65 points)

## cuad (run 20261024_185845, as of 2026-10-22, 50 documents)

| Category | Labeled | Token P | Token R | Token F1 | Detected | False pos. | Date exact | Grounding |
|---|---|---|---|---|---|---|---|---|
| effective_date | 38/50 | 53.3% | 77.6% | 54.4% | 100.0% | 100.0% | 88.0% (25) | 98.0% |
| expiration_date | 39/50 | 84.2% | 38.5% | 47.9% | 87.2% | 36.4% | 66.7% (6) | 97.4% |
| notice_to_terminate_renewal | 14/50 | 92.9% | 31.2% | 44.9% | 92.9% | 55.6% | - | 100.0% |
| parties | 50/50 | 43.8% | 70.3% | 49.7% | 100.0% | - | - | 84.0% |
| renewal_term | 18/50 | 94.4% | 35.5% | 48.9% | 94.4% | 37.5% | - | 96.6% |
| all | 159/250 | 66.0% | 56.9% | 49.9% | 95.6% | 52.7% | 83.9% (31) | 94.5% |

Cost per contract $0.0014, 8599 tokens, p95 latency 4.1 s.

### Top failure modes

- low_overlap: parties: 21
- unlabeled_extraction: notice_to_terminate_renewal: 20
- low_overlap: expiration_date: 18
- unlabeled_extraction: effective_date: 12
- unlabeled_extraction: renewal_term: 12
- low_overlap: effective_date: 12
- low_overlap: renewal_term: 8
- low_overlap: notice_to_terminate_renewal: 7

Failure log: `evals/results/failures_cuad_20261024_185845.json`

- DROP cuad/all/date_exact: 86.2% -> 83.9% (-2.34 points)
- DROP cuad/all/recall: 58.9% -> 56.9% (-2.0 points)
- DROP cuad/effective_date/f1: 56.8% -> 54.4% (-2.42 points)
- DROP cuad/effective_date/grounding_rate: 100.0% -> 98.0% (-2.0 points)
- DROP cuad/effective_date/precision: 55.7% -> 53.3% (-2.34 points)
- DROP cuad/expiration_date/date_exact: 83.3% -> 66.7% (-16.67 points)
- DROP cuad/parties/recall: 72.5% -> 70.3% (-2.2 points)


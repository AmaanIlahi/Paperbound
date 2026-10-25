# Paperbound evals

The synthetic set is **template-generated** (seeded; a few layouts per type, so it is easier than real documents). CUAD is **real contracts** scored by **span overlap** against human labels. Neither is a benchmark of production accuracy.

## synthetic (run insurance_certificate 20261025_125054, lease 20261025_125054, as of 2026-10-22, 40 documents)

| Group | Docs | Classif. | Field acc. | Missing ok | Grounding | Page acc. | Oblig. | Brief lines | Actions | Cost/doc | p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| insurance_certificate/all | 20 | 100.0% | 98.7% | 66.7% | 98.7% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 13.2 s |
| insurance_certificate/clean | 12 | 100.0% | 98.3% | - | 98.3% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0014 | 13.5 s |
| insurance_certificate/injection | 3 | 100.0% | 100.0% | - | 98.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 9.2 s |
| insurance_certificate/late_page | 2 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0013 | 8.0 s |
| insurance_certificate/missing_field | 3 | 100.0% | 98.0% | 66.7% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0012 | 8.0 s |
| lease/all | 20 | 100.0% | 96.8% | 66.7% | 99.3% | 100.0% | 100.0% | 74.0% | 75.0% | $0.0018 | 17.4 s |
| lease/clean | 12 | 100.0% | 97.0% | - | 100.0% | 100.0% | 100.0% | 75.0% | 75.0% | $0.0018 | 17.4 s |
| lease/injection | 3 | 100.0% | 100.0% | - | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0015 | 13.0 s |
| lease/late_page | 2 | 100.0% | 96.4% | - | 96.4% | 100.0% | 100.0% | 50.0% | 50.0% | $0.0025 | 17.5 s |
| lease/missing_field | 3 | 100.0% | 92.9% | 66.7% | 97.5% | 100.0% | 100.0% | 55.6% | 66.7% | $0.0013 | 12.1 s |

### Injection

| Type | Attacks | Model resisted | System held | Flagged | Unflagged breach | Reached actions |
|---|---|---|---|---|---|---|
| insurance_certificate | 3 | 100.0% | 100.0% | 0 | 0 | 0 |
| lease | 3 | 100.0% | 100.0% | 0 | 0 | 0 |

### Calibration (all variants)

**insurance_certificate**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 2 | 50.0% | 0.56 |
| 0.7-0.9 | 0 | - | - |
| >=0.9 | 371 | 98.9% | 1.00 |

ECE: 0.008 over 373 fields.

**lease**

| Confidence | Fields | Accuracy | Mean confidence |
|---|---|---|---|
| <0.7 | 9 | 33.3% | 0.68 |
| 0.7-0.9 | 1 | 100.0% | 0.85 |
| >=0.9 | 268 | 98.9% | 1.00 |

ECE: 0.021 over 278 fields.


### Top failure modes

- wrong_field: current_rent: 8
- brief_withheld: holdover_rent: 6
- brief_withheld: holdover_extra: 6
- brief_withheld: holdover_percent: 6
- action: 5
- brief_withheld: current_rent: 5
- brief_withheld: next_rent: 5
- wrong_field: type: 4

Failure log: `evals/results/failures_synthetic_20261025_125054.json`

- DROP synthetic/lease/all/brief_doc_correct: 60.0% -> 55.0% (-5.0 points)
- DROP synthetic/lease/all/missing_field_correct: 100.0% -> 66.7% (-33.33 points)
- DROP synthetic/lease/late_page/action_correct: 100.0% -> 50.0% (-50.0 points)
- DROP synthetic/lease/missing_field/field_accuracy: 95.2% -> 92.9% (-2.38 points)
- DROP synthetic/lease/missing_field/grounding_rate: 100.0% -> 97.5% (-2.5 points)
- DROP synthetic/lease/missing_field/missing_field_correct: 100.0% -> 66.7% (-33.33 points)

## generic (run 20261025_123854, as of 2026-10-22, 5 documents)

| Docs | Routed generic | Field acc. | Field precision | Parties | Dates | Amounts | Obligations | Grounding | Page acc. | Oblig. set | Actions | Cap held | Cost/doc |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | $0.0005 |

Label guess right: 100.0%; field label right (of matched): 88.6%; email drafts proposed: 0; extra fields not in the ground truth: 0.

### Top failure modes

- none

Failure log: `evals/results/failures_generic_20261025_123854.json`


## cuad (run 20261025_124139, as of 2026-10-22, 50 documents)

| Category | Labeled | Token P | Token R | Token F1 | Detected | False pos. | Date exact | Grounding |
|---|---|---|---|---|---|---|---|---|
| effective_date | 38/50 | 56.0% | 80.6% | 57.7% | 100.0% | 100.0% | 72.0% (25) | 100.0% |
| expiration_date | 39/50 | 87.1% | 40.3% | 50.3% | 92.3% | 36.4% | 83.3% (6) | 97.5% |
| notice_to_terminate_renewal | 14/50 | 100.0% | 31.1% | 45.5% | 100.0% | 61.1% | - | 97.2% |
| parties | 50/50 | 42.9% | 70.3% | 49.1% | 100.0% | - | - | 82.0% |
| renewal_term | 18/50 | 100.0% | 38.0% | 51.5% | 100.0% | 46.9% | - | 93.9% |
| all | 159/250 | 68.4% | 58.3% | 51.4% | 98.1% | 58.2% | 74.2% (31) | 93.8% |

Cost per contract $0.0014, 8604 tokens, p95 latency 4.3 s.

### Top failure modes

- low_overlap: parties: 22
- unlabeled_extraction: notice_to_terminate_renewal: 22
- low_overlap: expiration_date: 18
- unlabeled_extraction: renewal_term: 15
- unlabeled_extraction: effective_date: 12
- low_overlap: effective_date: 11
- low_overlap: renewal_term: 9
- low_overlap: notice_to_terminate_renewal: 9

Failure log: `evals/results/failures_cuad_20261025_124139.json`



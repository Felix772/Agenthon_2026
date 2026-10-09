# Revision-scale candidate provenance — 2026-10-09

The runtime change uses only fields in the supplied task and pre-cutoff corpus.
The point is the supplied latest estimate; the interval width was fixed before
collecting outcomes. No archived outcomes or fitted constants were added to the
runtime package. The generic direction default is unchanged.

## Public practice evaluation

The organizer's [October 5/7 clarification](https://github.com/Agenthon-2026/track4-analysis-public/issues/24)
allows the public practice outcomes to inform Final design with provenance.
October 7 is the permission date, not the publication date of the values below.
Our comparison uses the level printed in each task's specified resolving release,
not a subsequently revised current-series value. All sources were retrieved on
**2026-10-09**. These are exposed practice observations, so the comparison is
in-sample and does not establish Final generalization.

| Series / reference month | Resolving release | Published level | Original units | Source |
| --- | --- | ---: | --- | --- |
| DGORDER / 2024-07 | 2024-10-25 | 289419 | USD millions, SA | [Census, Table 1](https://www.census.gov/manufacturing/m3/historical_data/pressreleases/adv/2024/sep24adv.pdf) |
| DGORDER / 2024-08 | 2024-10-25 | 287018 | USD millions, SA | Same release |
| HOUST / 2024-07 | 2024-10-18 | 1262 | Thousands of units, SAAR | [Census/HUD, Table 3a](https://www.census.gov/construction/nrc/pdf/newresconst_202409.pdf) |
| PAYEMS / 2024-07 | 2024-10-04 | 158692 | Thousands of persons, SA | [BLS, Table B-1](https://www.bls.gov/news.release/archives/empsit_10042024.htm) |
| PAYEMS / 2024-08 | 2024-10-04 | 158851 | Thousands of persons, SA | Same release |
| PAYEMS / 2024-08 | 2024-11-01 | 158770 | Thousands of persons, SA | [BLS, Table B-1](https://www.bls.gov/news.release/archives/empsit_11012024.htm) |
| PI / 2024-07 | 2024-10-31 | 24819.3 | USD billions, SAAR | [BEA, Table 1](https://www.bea.gov/sites/default/files/2024-10/pi0924.pdf) |
| PI / 2024-08 | 2024-11-27 | 24740.2 | USD billions, SAAR | [BEA, Table 1](https://www.bea.gov/sites/default/files/2024-11/pi1024.pdf) |
| RSAFS / 2024-07 | 2024-10-17 | 710851 | USD millions, SA | [Census, Table 1](https://www2.census.gov/marts/adv2409.pdf) |
| RSAFS / 2024-08 | 2024-11-15 | 710038 | USD millions, SA | [Census, Table 1](https://www2.census.gov/marts/adv2410.pdf) |

Two INDPRO rows are excluded: the [October 17 Federal Reserve release](https://www.federalreserve.gov/releases/g17/20241017/default.htm)
prints July/August as 102.6/102.9, but the exact four-decimal task outcomes could
not be established. Rounded values are not substituted as exact targets.

Per-row values, source locators, frozen prediction hashes and raw interval scores
are recorded in `verification/t4-revision-practice-20261009.json` at the workspace
root. Raw scores are not averaged across incompatible units. The sealed official
naive-answer parameters are unavailable, so the official composite was not reproduced.

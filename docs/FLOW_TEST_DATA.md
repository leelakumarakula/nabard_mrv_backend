# Flow test – data sheet (every value to enter)

Use with [FLOW_TEST_GUIDE.md](FLOW_TEST_GUIDE.md) or the checklist page. Enter exactly these values, in this order, into
the empty `flow_test_nabard_mrv` database. Fields not listed: leave empty. All people, phone numbers and emails here are
**invented test values**. Today's date in this sheet is 4 Nov 2026; never enter a date after today for something that
already happened (the platform rejects future observations).
Dates are written **mm/dd/yyyy** (month first), as the date boxes show them: `11/03/2026` = 3 November 2026.

The **Expected** lines tell you what you should see, so you can tell whether the platform behaved correctly.

---

## 0. Accounts used in this test

| Who | Email (sign-in) | Temporary password | Created in step |
|---|---|---|---|
| You – Platform Super Admin | charana@vayublue.com | your own, 12+ characters | A.4 (PowerShell) |
| State coordinator – Odisha | coordinator.od@example.org | `CoordTest-2026` | 1.8 |
| Field officer – Koraput | field.officer@example.org | `FieldTest-2026` | 1.8 |
| MRV analyst | analyst@example.org | `AnalystTest-2026` | 1.8 |
| Internal reviewer | reviewer@example.org | `ReviewTest-2026` | 1.8 |
| Independent verifier | verifier@example.org | `VerifyTest-2026` | 1.8 |

Each person must change the temporary password at first sign-in. Write the new passwords down for the test.

---

## Stage A – PowerShell

```powershell
cd D:\Desktop\Nabard_mrv\nabard_mrv_backend
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\python -m app.seed reference
$env:MRV_ADMIN_PASSWORD = "<your password, 12+ characters>"
.\.venv\Scripts\python -m app.seed admin --email "charana@vayublue.com" --name "charan akula"
Remove-Item Env:MRV_ADMIN_PASSWORD
```

Expected: `Super admin charana@vayublue.com created; password change required at first sign-in.`

---

## Stage 1 – Programme set-up (sign in as **you**)

### 1.1 Programmes → New programme

| Field | Value |
|---|---|
| Code | `CCRL-TEST` |
| Name | `Climate-Resilient Coffee Landscapes – flow test` |
| Status | `active` |
| Funding framework | `NABARD CCF-ID (proposed)` |
| Programme version | `Proposal 29 Oct 2026` |
| Proposal date | `10/29/2026` |
| Duration (months) | `36` |
| Proposal title | `Climate-Resilient Coffee Landscapes and Inclusive Rural Livelihoods Programme` |
| Start date | `10/01/2026` |
| End date | `09/30/2029` |
| Description | `Test programme for the end-to-end flow test. Not real programme data.` |
| Funding note | `₹15 crore proposed to NABARD; not sanctioned.` |

### 1.2 Programme → Add period

| Field | Value |
|---|---|
| Code | `2026-Q4` |
| Name | `October – December 2026` |
| Type | `quarter` |
| Status | `open` |
| Start | `10/01/2026` |
| End | `12/31/2026` |

### 1.3 Geography

| Button | Name | Code |
|---|---|---|
| Add state | `Odisha` | `OD` |
| On Odisha → add district | `Koraput` | `OD-KPT` |
| On Koraput → add cluster | `Koraput Coffee Cluster 1` | `OD-KPT-C1` |

Leave Geometry reference empty. Expected: a tree Odisha → Koraput → Koraput Coffee Cluster 1.

### 1.4 FPOs & organisations → New organisation

| Field | Value |
|---|---|
| Type | `fpo` |
| Code | `OD-FPO-01` |
| Name | `Koraput Coffee Growers FPO` |
| Geography | `Koraput (district)` |
| Registration reference | `TEST-REG-0001` |
| Contact name | `Bhagaban Muduli` |
| Contact phone | `9000000001` |
| Contact email | `fpo.koraput@example.org` |
| Status | `active` |

### 1.5 Programme → Link organisation

| Field | Value |
|---|---|
| Organisation | `Koraput Coffee Growers FPO` |
| Role | `implementing_fpo` |
| From | `10/01/2026` |
| To | leave empty |

### 1.6 Indicators → New indicator (save as draft; **do not approve yet**)

| Field | Value |
|---|---|
| Code | `HH_CLIMATE_ADOPTION` |
| Name | `Households adopting climate-resilient practices` |
| Family | `outcome` |
| Workstream | `Farmer & Community Implementation` |
| Unit | `households` |
| Calculation method | `Households with field-observed practice adoption` (`households_adopting`) |
| Definition | `Number of unique households in which at least one climate-resilient practice was observed in the field by programme staff.` |
| Numerator | `Unique households with at least one field-observed climate-resilient practice up to the period end` |
| Denominator | `Not applicable – this is a count` |
| Eligibility | `Farmers enrolled in the programme with consent recorded` |
| Exclusions | `Advice received, training attended or farmer self-report without field observation` |
| Method reference / version | `households_adopting v1.0 – flow test definition` |
| Method parameters | leave empty (uses the default list of climate-resilient practices, which includes Mulching) |
| Frequency | `quarterly` |
| Aggregation | `count of distinct households` |
| Approved disaggregation | `gender` |
| Baseline method | `Baseline is 0 households at programme start (test assumption)` |
| Data sources | `Practice records with field observation` |
| Evidence rules | `Field observation recorded by a field officer; photo optional for this test` |
| Verification sample approach | `Simple random sample of observed practice records, confirmed by a field visit of the independent verifier` |
| Plausibility rules | `{"min": 0, "max": 1500}` |
| Limitations | `Test definition for the flow test only` |

Expected: the indicator appears with status `draft`.

### 1.7 Programme → Add target (three times)

| Field | Target 1 | Target 2 | Target 3 |
|---|---|---|---|
| Metric code | `HOUSEHOLDS` | `COFFEE_AREA_HA` | `HH_ADOPTION` |
| Metric name | `Households (proposed target)` | `Existing coffee area` | `Households adopting climate-resilient practices` |
| Target value | `1500` | `700` | `1000` |
| Unit | `households` | `ha` | `households` |
| Period | `36 months` | `36 months` | `36 months` |
| Status | `proposed` | `proposed` | `proposed` |
| Target version | `1` | `1` | `1` |
| Linked indicator code | leave empty | leave empty | `HH_CLIMATE_ADOPTION` |
| Source / approval note | `Proposal §5.1 – Odisha figures (test)` | `Proposal §5.1 – Odisha figures (test)` | `Test value for the flow test` |

Expected: three targets. Try editing target 1 and changing the value: the form only lets you change the figure through
"Change the target figure", which keeps version 1 and creates version 2.

### 1.8 Users → New user (five times)

| Field | Coordinator | Field officer | Analyst | Reviewer | Verifier |
|---|---|---|---|---|---|
| Email | `coordinator.od@example.org` | `field.officer@example.org` | `analyst@example.org` | `reviewer@example.org` | `verifier@example.org` |
| Temporary password | `CoordTest-2026` | `FieldTest-2026` | `AnalystTest-2026` | `ReviewTest-2026` | `VerifyTest-2026` |
| Full name | `Anita Pradhan` | `Priya Nayak` | `Arjun Rao` | `Meena Das` | `Suresh Iyer` |
| Phone | `9000000011` | `9000000012` | `9000000013` | `9000000014` | `9000000015` |
| Roles | State/District Coordinator | Field Officer / Community Resource Person | MRV Analyst | Internal Reviewer | Independent Verifier |
| Geography scope | `Odisha` | `Koraput` | empty | empty | empty |
| FPO / organisation scope | empty | empty | empty | empty | empty |
| Belongs to organisation | empty | `Koraput Coffee Growers FPO` | empty | empty | empty |

Expected: 6 users in total (including you).

---

## Stage 2a – Farmer registration (sign in as **coordinator.od@example.org**)

Field officers can record field work but cannot register farmers; coordinators (and admins) can.

### 2.1 Farmers & plots → Register farmer (four times)

| Field | Farmer 1 | Farmer 2 | Farmer 3 | Farmer 4 |
|---|---|---|---|---|
| Farmer code | `OD-F-001` | `OD-F-002` | `OD-F-003` | `OD-F-004` |
| Household code | `OD-HH-001` | `OD-HH-002` | `OD-HH-003` | `OD-HH-004` |
| Full name | `Lakshmi Gemel` | `Ravi Khara` | `Sita Majhi` | `Gopal Pangi` |
| Geography | `Koraput Coffee Cluster 1` | same | same | same |
| Primary FPO | `Koraput Coffee Growers FPO` | same | same | same |
| Gender | `female` | `male` | `female` | `male` |
| Year of birth | `1984` | `1979` | `1990` | `1972` |
| Phone | `9000000101` | `9000000102` | `9000000103` | `9000000104` |
| Village | `Pottangi` | `Pottangi` | `Semiliguda` | `Semiliguda` |
| Consent | `given` | `given` | `given` | `given` |
| Consent date | `10/10/2026` | `10/10/2026` | `10/11/2026` | `10/11/2026` |
| Consent purpose | `Programme monitoring and reporting to NABARD` | same | same | same |

### 2.2 Farmer page → Enrolment (each farmer)

| Field | Value |
|---|---|
| Programme | `Climate-Resilient Coffee Landscapes – flow test` |
| Status | `enrolled` |
| Enrolment date | `10/15/2026` |
| Notes | leave empty |

### 2.2b Farmer page → Assign category (Lakshmi Gemel and Sita Majhi only)

| Field | Value |
|---|---|
| Category | `Woman beneficiary` |
| Basis | `self_declared` |
| Date | `10/15/2026` |
| Note | `Self-declared at enrolment (test)` |

Women's inclusion is counted from these explicit assignments, never guessed from the gender field.

### 2.3 Farmer page → Add farm (each farmer)

| Field | Farmer 1 | Farmer 2 | Farmer 3 | Farmer 4 |
|---|---|---|---|---|
| Farm code | `OD-FARM-001` | `OD-FARM-002` | `OD-FARM-003` | `OD-FARM-004` |
| Name | `Gemel coffee garden` | `Khara coffee garden` | `Majhi coffee garden` | `Pangi coffee garden` |
| Village | `Pottangi` | `Pottangi` | `Semiliguda` | `Semiliguda` |
| Total area | `1.2` | `0.8` | `1.0` | `1.5` |
| Area unit | `ha` | `ha` | `ha` | `ha` |
| Area source | `declared` | `declared` | `declared` | `declared` |

### 2.4 Farm → Add plot (one per farm)

| Field | Farmer 1 | Farmer 2 | Farmer 3 | Farmer 4 |
|---|---|---|---|---|
| Plot code | `OD-P-001` | `OD-P-002` | `OD-P-003` | `OD-P-004` |
| Area | `1.0` | `0.8` | `0.9` | `1.2` |
| Area unit | `ha` | `ha` | `ha` | `ha` |
| Area source | `gps_measured` | `gps_measured` | `declared` | `declared` |
| Crop | `arabica` | `arabica` | `robusta` | `mixed` |
| Variety | `Chandragiri` | `SLN 795` | `CxR` | `Chandragiri` |
| Plantation year | `2012` | `2016` | `2021` | `2005` |
| Plantation stage | `productive` | `productive` | `young` | `underperforming` |
| Shade type | `silver oak` | `silver oak` | `natural forest` | `mixed shade` |
| Irrigation type | `rainfed` | `sprinkler` | `rainfed` | `rainfed` |
| Latitude | `18.8120` | `18.8250` | `18.7950` | `18.8400` |
| Longitude | `82.7080` | `82.7350` | `82.6900` | `82.7600` |

Expected: Dashboard shows 4 farmers, 4 enrolled. Farm map shows 4 points near Koraput.

---

## Stage 2b – Field work (sign in as **field.officer@example.org**)

New records are saved as **draft**; open each one and click **Submit & validate**.

### 2.5 Module 6 Farmer & community → New record → Training session

| Field | Value |
|---|---|
| FPO / grower group | `Koraput Coffee Growers FPO` |
| Observed / activity date-time | `11/02/2026 10:00 AM` |
| Title | `Mulching for moisture retention` |
| Topic | `Mulching and soil moisture conservation` |
| Trainer | `Priya Nayak` |
| Mode | `field` |
| Duration | `3` |
| Notes | `Field session at Pottangi` |

Then on the record → Participants → **Add** four times: Lakshmi Gemel, Ravi Khara, Sita Majhi, Gopal Pangi.
Then **Submit & validate**. Expected: validation `valid`.

### 2.6 Practice adoption → New practice record (four times)

| Field | Farmer 1 | Farmer 2 | Farmer 3 | Farmer 4 |
|---|---|---|---|---|
| Farmer | `OD-F-001 – Lakshmi Gemel` | `OD-F-002 – Ravi Khara` | `OD-F-003 – Sita Majhi` | `OD-F-004 – Gopal Pangi` |
| Plot | `OD-P-001` | `OD-P-002` | `OD-P-003` | `OD-P-004` |
| Workstream | `Soil & Water Management and Conservation` | same | same | same |
| Practice | `Mulching` | `Mulching` | `Mulching` | `Ground cover / cover crops` |
| Status | `recommended` | `recommended` | `recommended` | `recommended` |
| Recommended action | `Mulch 5–8 cm around coffee bushes before the dry season` | same | same | `Sow a cover crop between coffee rows` |
| Action date | `10/20/2026` | `10/20/2026` | `10/20/2026` | `10/20/2026` |

### 2.7 Edit practice records of farmers 1, 2 and 3 → status reported

| Field | Value |
|---|---|
| Status | `reported` |
| Farmer-reported action | `Applied leaf mulch around all bushes` |
| Action date | `10/28/2026` |

Leave Gopal Pangi (farmer 4) at `recommended`.

### 2.8 Record field observation – farmers 1 and 2 only

| Field | Value |
|---|---|
| Practice observed in the field | ticked |
| What was observed | `Leaf mulch 5–7 cm deep around most bushes on the plot` |
| Observation date | `11/03/2026` |

Leave Sita Majhi (farmer 3) at `reported`.
Expected status: Lakshmi `observed`, Ravi `observed`, Sita `reported`, Gopal `recommended`.

### 2.9 Module 3 Pest & disease → New record → Scouting / inspection

| Field | Value |
|---|---|
| Plot | `OD-P-002` |
| Observed / activity date-time | `11/03/2026 9:30 AM` |
| Title | `Berry borer scouting – Khara plot` |
| Pest / disease | `coffee_berry_borer` |
| Plants inspected | `50` |
| Plants affected | `4` |
| Severity (0–5) | `1` |
| Scouting method | `Random walk, 50 bushes` |
| Recommended action | `Collect fallen berries; set traps` |

Submit & validate. Expected: value `8 %` (4 ÷ 50 – calculated by the platform).

### 2.10 Module 4 Soil & microclimate → New record → Soil test

| Field | Value |
|---|---|
| Plot | `OD-P-001` |
| Observed / activity date-time | `11/01/2026 11:00 AM` |
| Title | `Soil test – Gemel plot` |
| Laboratory / kit | `Koraput Soil Testing Laboratory` |
| Sample depth | `15` |
| pH | `5.4` |
| Organic carbon | `1.2` |
| Available N | `280` |
| Available P | `18` |
| Available K | `150` |
| Electrical conductivity | `0.3` |

Submit & validate. Expected: value `5.4` (the pH).

### 2.11 IoT devices → Register device

| Field | Value |
|---|---|
| Device code / serial | `MP-OD-0001` |
| Type | `moisture_probe` |
| Provider | `Moisture Probe (test)` |
| Assigned plot | `OD-P-001` |
| Installed on | `10/25/2026` |
| Status | `active` |
| Data gap justifying deployment | `No soil-moisture data for rainfed plots in Pottangi` |

---

## Stage 3 – Outside data (sign in as **you**)

Integrations → Ready-made connectors. For each connector: click it, then enter

| Field | Value |
|---|---|
| Programme | `CCRL-TEST – Climate-Resilient Coffee Landscapes – flow test` |
| Source code | keep the suggested code |
| Name | keep the suggested name |

Save, then click **Run pull** on the source page.

| Connector | Expected after Run pull |
|---|---|
| Open-Meteo weather | 56 records = 4 plots × 14 (7 past days as estimates + 7 days of forecast from today); 0 rejected |
| Run pull again (same source, same day) | 0 accepted, 56 duplicate (a past day's estimate may occasionally come back as "accepted" when Open-Meteo has refined it) |
| NASA POWER weather | about 14 records per plot; 0 rejected |
| Sentinel-2 NDVI | 1 record per plot (4); NDVI between about 0.3 and 0.9 |
| SAMPLE Climate engine | 4 records, labelled DEMO |
| SAMPLE Pest engine | 4 records, labelled DEMO |
| SAMPLE Water engine | 4 records, labelled DEMO |
| SAMPLE IoT gateway | 8 records (moisture probe: soil moisture + soil temperature × 4 readings), labelled DEMO |

---

## Stage 4 – Prove it

### 4.1 Analyst – Indicators → HH_CLIMATE_ADOPTION → Preview calculation

| Field | Value |
|---|---|
| Period start | `10/01/2026` |
| Period end | `12/31/2026` |
| Scope | `programme` |
| Try other parameters | leave empty |

Expected: **2** (Lakshmi and Ravi – observed Mulching). Sita (only reported) and Gopal (only recommended) are not
counted. Nothing is saved.

### 4.2 Analyst – MRV workspace → Run calculation

| Field | Value |
|---|---|
| Indicator | `HH_CLIMATE_ADOPTION` |
| Reporting period | `2026-Q4` |
| Scope | `programme` |
| Parameter overrides | leave empty |

Expected: **refused** with the message "Indicator HH_CLIMATE_ADOPTION v1 is pending programme-owner approval;
calculations are enabled only for approved definitions". This is the safe default: no stored number exists for an
unapproved definition. (Only for demonstrations, an admin can switch on System settings →
`mrv.allow_provisional_calculations`; results are then stored but marked PROVISIONAL. Leave it off for this test.)

### 4.3 You – approve the definition

Indicators → HH_CLIMATE_ADOPTION → **Submit for approval** (Note: `Ready for approval – flow test`) →
**Approve definition** (Note: `Approved for the flow test`). Expected: status `approved`. If approval is refused, the
message lists the empty definition fields – fill them from 1.6 and try again.

### 4.4 Analyst – Run calculation again (same values as 4.2)

Expected: now accepted – result **2**, not provisional.

### 4.5 Analyst – result → Submit for internal review

| Field | Value |
|---|---|
| Internal reviewer | `Meena Das` |
| Due date | `11/07/2026` |
| Note for reviewer | `Q4 2026 adoption count for review` |

### 4.6 Reviewer – MRV workspace → the task

**Add finding**:

| Field | Value |
|---|---|
| Severity | `minor` |
| Type | `evidence_gap` |
| Finding | `No photo attached to the field observations` |
| Corrective action required | `Attach photos in the next round` |

**Request correction** – What must be corrected: `Confirm the observation dates for OD-F-001 and OD-F-002`

### 4.7 Analyst – the same task → Resubmit

What changed: `Observation dates confirmed with the field officer (3 Nov 2026)`

### 4.8 Reviewer – the task → Complete internal review

Review note: `Count checked against the practice records; minor finding noted`

### 4.9 Verifier – the task → Independent decision

| Field | Value |
|---|---|
| Decision | `verified` |
| Rationale | `Count matches the observed practice records; lineage checked` |

### 4.10 Verifier – Independent verification → New sample

| Field | Value |
|---|---|
| Title | `Q4 2026 mulching verification – Koraput` |
| Records to verify | `practice_record` |
| Sample frame description | `All observed Mulching practice records in Koraput, Oct–Dec 2026` |
| Frame period start | `10/01/2026` |
| Frame period end | `12/31/2026` |
| Frame area | `Koraput` |
| Practices | `Mulching` |
| Selection method | `simple_random` |
| Sample size | `2` |
| Random seed | `2026` |
| Independent verifier | `Suresh Iyer` |
| Reporting period | `2026-Q4` |

Then **Draw sample**. Expected: frame size 2, both observed Mulching records drawn.

### 4.11 Verifier – Record visit (each sampled record)

| Field | Lakshmi Gemel's record | Ravi Khara's record |
|---|---|---|
| Result | `confirmed` | `discrepancy` |
| Visit date | `11/04/2026` | `11/04/2026` |
| Observation | `Mulch present around most bushes` | `Mulch only on about a third of the plot` |
| Discrepancy | – | `Coverage much lower than reported` |

Then **Complete with conclusion**:

| Field | Value |
|---|---|
| Conclusion | `discrepancies_found` |
| Conclusion note | `1 of 2 confirmed; 1 discrepancy on coverage` |

Expected: Lakshmi's practice `verified`, Ravi's `not_verified`.

### 4.12 Analyst – Run calculation twice more

| Run | Parameter overrides | Expected |
|---|---|---|
| A | leave empty | **2** – field-observed adoption |
| B | `{"require_verified": true}` | **1** – only independently verified adoption |

This difference is the heart of MRV: observed is not the same as verified.

---

## Stage 5 – Output (sign in as **you** or the analyst)

### 5.1 Reports → Generate report

| Field | Value |
|---|---|
| Report template | leave empty |
| Report type | `Quarterly progress report` |
| Reporting period | `2026-Q4` |
| Scope | `programme` |
| Title | `Q4 2026 progress – flow test` |

Then download **PDF** and **Excel**. Expected: 4 farmers, the targets, your indicator result, and an inclusion section
that uses the Woman beneficiary assignments from 2.2b.

### 5.2 Check

| Screen | Expected |
|---|---|
| Dashboard | 4 farmers, adoption funnel, targets with values |
| Status & decisions | "1/1 definitions approved" |
| Audit log | every action of this sheet, with person and time |

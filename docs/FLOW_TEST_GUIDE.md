# End-to-end flow test – start from an empty database

This guide walks one person through the whole platform on an **empty** database (`flow_test_nabard_mrv`), creating every
record by hand, so you see each step of the MRV flow and what it produces. Nothing is pre-loaded except the system rule
book (roles, permissions, settings, workstreams). Allow 3–4 hours; you can stop after any stage and continue later.

Tip: keep two browsers (or one normal + one private window) so you can be signed in as two people at once.

---

## Part A – Prepare the empty database (10 minutes)

Run in PowerShell, in `D:\Desktop\Nabard_mrv\nabard_mrv_backend`. Check first that `.env` has
`DATABASE_URL=...flow_test_nabard_mrv...`.

```powershell
# 1. Create the empty tables
.\.venv\Scripts\alembic upgrade head

# 2. Load the rule book: 10 roles + permissions, settings, the 7 workstreams and their activities,
#    proposed beneficiary categories and report templates. No farmers, no programme, no users.
.\.venv\Scripts\python -m app.seed reference

# 3. Create YOUR administrator account (choose a password of 12+ characters; it is not stored in any file)
$env:MRV_ADMIN_PASSWORD = "Test@1234"
.\.venv\Scripts\python -m app.seed admin --email "charana@vayublue.com" --name "charan akula"
Remove-Item Env:MRV_ADMIN_PASSWORD
```

Start the two parts of the application (two PowerShell windows):

```powershell
# window 1 – backend (the brain)
cd D:\Desktop\Nabard_mrv\nabard_mrv_backend
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8010

# window 2 – frontend (the screens)
cd D:\Desktop\Nabard_mrv\nabard_mrv_frontend
npm start
```

Open http://localhost:4200 and sign in with the email and password from step 3.

**What happened:** the database now has empty tables plus the rule book. You are the only user.

---

## Stage 1 – Set up the programme (you, as administrator / programme owner)

| # | Screen → button | Enter | What it means |
|---|---|---|---|
| 1 | (forced) Profile → change password | a new password | The first sign-in always forces a password change. |
| 2 | Programmes → **New programme** | code e.g. `CCRL-TEST`, name, start/end dates, 36 months | The funded project everything else belongs to. |
| 3 | Programme detail → **Add period** | e.g. `2026-Q4`, 1 Oct – 31 Dec 2026 | Reporting periods – calculations and reports are per period. |
| 4 | Geography → **Add state** | Odisha (code `OD`) | Places are a tree: state → district → cluster. |
| 5 | Geography → on Odisha → add district, then cluster | Koraput → one cluster | Users and farmers are attached to a place; this drives who sees what. |
| 6 | Programme detail → **Add target** | Households 1,500 (state Odisha), coffee area 700 ha | Targets from the proposal. Note: once saved, the figure is locked – changing it needs a revision with a reason. |
| 7 | FPOs & organisations → **New organisation** | an FPO in Koraput | The farmer group (Farmer Producer Organisation). |
| 8 | Indicators → **New indicator** | e.g. "Households adopting climate-resilient practices", method `households_adopting`, unit households. Fill every text field: definition, method reference, eligibility, baseline method, evidence requirements, verification sample approach | What will be measured and how it is calculated. Approval is refused while any of those fields is empty. |
| 9 | Indicator → **Submit for approval**, then **Approve definition** | – | Until a definition is approved, stored calculations are refused (only Preview works). Do this in Stage 4, after you have seen the refusal. |
| 10 | Users → **New user** (×5) | a state coordinator (scope: Odisha), a field officer (scope: Koraput district), an MRV analyst, an internal reviewer, a verifier – each with a temporary password | One person per job. Field roles must have a place (scope); programme-wide roles do not. |

Optional: `.\.venv\Scripts\python -m app.seed decisions --programme CCRL-TEST` loads the register of decisions still
pending from the programme owner (Status & decisions screen).

**Check:** Dashboard shows 0 farmers and your targets with no values yet.

---

## Stage 2 – Field work

Exact values for every field: [FLOW_TEST_DATA.md](FLOW_TEST_DATA.md). Steps 11–14 are done by the **state coordinator**
(field officers cannot register farmers); steps 15–21 by the **field officer**. Each must change their temporary
password at first sign-in.

| # | Screen → button | Enter | What it means |
|---|---|---|---|
| 11 | Farmers & plots → **Register farmer** | name, gender, village, the Koraput place, the FPO, consent = given | A farmer exists only with recorded consent. |
| 12 | Farmer → **Enrolment** | your programme | Enrolled = counted for this programme. Weather and satellite connectors use only enrolled farmers' plots. |
| 13 | Farmer → **Add farm**, then **Add plot** | area, crop, plantation stage, **latitude and longitude** (e.g. 18.81, 82.71 – a coffee area near Koraput) | GPS is what puts the plot on the map and lets weather/satellite data be fetched for it. |
| 14 | Repeat 11–13 for 4 farmers; on the women farmers' pages use **Assign category** → Woman beneficiary | | Women's inclusion is counted from explicit category assignments, never guessed from gender. |
| 15 | (field officer) 6. Farmer & community → **New record** → Training session | topic, trainer, participants | Training is recorded, but attendance is not adoption. |
| 16 | Practice adoption → **New practice record** | farmer, plot, activity e.g. Mulching, status *recommended* | The advice given. |
| 17 | Edit it → status *reported* | – | The farmer says they did it. |
| 18 | **Record field observation** | date, what was seen, evidence | The officer saw it on the farm. Now it is *observed*. |
| 19 | 3. Pest & disease → **New record** → Scouting / inspection | plants inspected 50, affected 4 | Incidence % is calculated by the platform. |
| 20 | 4. Soil & microclimate → **New record** → Soil test | pH, organic carbon … | |
| 21 | IoT devices → **Register device** | type Moisture Probe, on one plot, status *active* | Needed for the sample sensor gateway in Stage 3. |

**Check:** Farm map shows your plots as points; clicking one shows its practice progress.

---

## Stage 3 – Data arriving from outside systems (sign in as administrator)

Integrations → **Ready-made connectors**. Click one, choose your programme, save, then on the source click **Run pull**.

| Connector | Data | What you will see |
|---|---|---|
| Open-Meteo weather | **REAL** | Daily rain/temperature for the last 7 days and a 7-day forecast for each located plot (module 1). |
| NASA POWER weather | **REAL** | Satellite/reanalysis daily weather (a few days' delay). |
| Sentinel-2 NDVI | **REAL** | Vegetation greenness from the latest cloud-free satellite image (module 5). Takes ~1 s per plot. |
| SAMPLE climate / pest / water engine | synthetic, labelled DEMO | Weekly risk estimates and irrigation advice – stand-ins until the FarmFuture APIs arrive. |
| SAMPLE IoT gateway | synthetic, labelled DEMO | Readings every 6 hours from your active device. |

Things to try:
- Click **Run pull** twice – the second run reports *duplicate*, not new records (idempotency).
- Open the source's **Sync runs** tab – every pull is logged with read / accepted / duplicate / rejected counts.
- Open the Farm map and change **Colour by** – practice progress, pest risk, NDVI, forecast rain.
- Weather estimates are labelled *calculated* and forecasts *model prediction* – never confused with field observations.

None of this is IMD data. IMD and the FarmFuture engines need agreements and API documents first.

---

## Stage 4 – Proof: calculate, review, verify

| # | Who | Screen → button | What it means |
|---|---|---|---|
| 22 | MRV analyst | Indicators → your indicator → **Preview calculation** (nothing saved), then MRV workspace → **Run calculation** for the indicator and period | The number is computed and every input record is saved with it (lineage). Open the result and see which farmers were counted. |
| 23 | MRV analyst | Result → **Submit for internal review** | Creates a review task. |
| 24 | Internal reviewer | MRV workspace → the review task → **Add finding**, **Request correction** | Our own check. Try a correction once to see the loop. |
| 25 | MRV analyst | the same task → **Resubmit** (say what changed) | |
| 26 | Internal reviewer | **Complete internal review** | |
| 27 | Verifier | task → **Independent decision** | The outside checker's decision. The person who made the number cannot approve it. |
| 28 | Verifier | Independent verification → **New sample** (practice records, simple random, size 2), **Draw sample** | Random farms to visit. The seed makes the draw repeatable. |
| 29 | Verifier | sample → **Record visit** for each farm, then **Complete with conclusion** | Practices confirmed in the visit become *verified*. |
| 30 | MRV analyst | Run the calculation again | Compare the numbers before and after verification. |

---

## Stage 5 – Output and audit

| # | Who | Screen → button | What it means |
|---|---|---|---|
| 31 | Administrator | Reports → **Generate report** (quarterly, your period) → **PDF** / **Excel** | The document NABARD receives. Check your farmers, targets and the PROVISIONAL/verified labels. |
| 32 | Administrator | Dashboard | Progress against targets, adoption funnel, women's inclusion. |
| 33 | Administrator | Audit log | Every action from this guide with person, time, old and new values. Nothing is deleted. |

---

## When you have finished

You have done, by hand, the whole cycle the platform runs for 6,500 farmers over 36 months:
**set up → register → act in the field → receive outside data → calculate → review → verify → report → audit.**

To start again from zero: drop and recreate `flow_test_nabard_mrv` in SQL Server, then repeat Part A.

# Dashboard Build Guide (Power BI Desktop)

> The finished report is [`dashboards/B2B_Product_Health_Tracker.pbix`](../dashboards/B2B_Product_Health_Tracker.pbix), condensed to 4 pages (Executive Summary, Churn Analysis, Churn Risk Prioritization, About and Method). This guide documents the full page-by-page build, including two optional pages (Expansion Targets, Adoption & Segments) not in the current build - useful if you want to extend it further.

This guide turns the CSVs, DAX and theme in this repo into a recruiter-ready Power BI report.
Power BI Desktop is free - no Pro licence is needed to build or save the `.pbix`.

Budget about 3-4 hours the first time.

---

## 0. Set up the model (20 min)

1. **Get Data -> Text/CSV** and load:
   - `data/processed/enhanced_b2b_product_metrics.csv` -> rename table to **`Usage`**
   - `data/processed/dim_account_snapshot.csv` -> rename table to **`AccountSnapshot`**
2. In Power Query, set `Usage[usage_month]` and `AccountSnapshot[snapshot_month]` to **Date**. Everything else auto-detects.
3. **Model view** -> relationship `AccountSnapshot[account_id]` (1) -> `Usage[account_id]` (*), single direction.
4. **Enter Data** -> create an empty table named **`_Measures`** (one column, no rows needed, then hide the column).
   **Fast route:** open **DAX query view** (left rail), paste all of `dax/measures_bulk_import.dax`, and click **Update model with changes**. That adds all 43 measures at once.
   **Fallback:** if that view is missing or errors, paste each measure from `dax/measures.dax` via *New measure* (about 20 min).
5. Set formats: `%` measures -> Percentage (1 dp); `ARR`, `ARR at Risk`, `Expansion Base ARR`, `Estimated Expansion ARR`, `Low Adoption ARR` -> Currency, display units *Millions*.
6. **Sort** `AccountSnapshot[account_status]` by `status_sort`.
7. **View -> Themes -> Browse for themes** -> `dashboards/b2b_health_theme.json`.
8. Hide helper columns you do not need in report view (`status_sort`, `snapshot_month`).

### Validation checkpoints (reporting month = Aug 2026, no other filters)

Check these before styling anything. They come from an independent pandas calculation.

| Measure | Expected |
|---|---|
| Total Accounts | 1,000 |
| API Calls | 71,942,111 |
| Active Seats | 72,228 |
| Seat Utilization % | 52.4% |
| Credit Utilization % | 47.2% |
| API MoM % | +4.7% |
| API 3M Growth % | -1.0% |
| ARR | $80.8M |
| Accounts with Severe Usage Decline | 241 (Jul: 242, Jun: 243) |
| Accounts at Risk / ARR at Risk | 241 / $16.7M (20.7% of ARR) |
| Expansion Accounts / Expansion Base ARR | 124 / $8.0M |
| Tier Upgrade Candidates | 94 |
| Low Adoption Accounts | 101 |
| Accounts Over Credit Allowance | 75 |

If a number is off, the fix is almost always the `usage_month` data type or the relationship direction.

---

### Gotchas that cost people an hour

- **The month slicer will blank your trend charts.** Any chart with months on the X axis (the cohort line chart, the seats-over-time line) must ignore the month slicer: select the slicer, then **Format -> Edit interactions**, and click the "no interaction" icon on those charts.
- **Use `usage_month` itself on the X axis**, not the Date Hierarchy: click the field's dropdown in the well and choose `usage_month`. Set the X axis type to **Categorical**.
- **Slicer default:** Power BI keeps whatever you select when you save. Select **Aug 2026** in the month slicer, then save.
- **Scatter charts:** put `Usage[account_name]` in **Details**, otherwise Power BI plots one dot for the whole dataset.
- **Legend colours are not inherited from the theme.** Set them per value under Format -> Markers/Lines -> Colors so Churn Risk is red on every page.
- **Keep `=` and `>=` out of measure names.** They can leave a measure stuck with a red warning triangle.
- **Long text in cards:** if `[Headline Insight]` truncates, turn on word wrap in the card's format options, or use a Multi-row card.

---

## 1. Global design rules

| Rule | Value |
|---|---|
| Canvas | 16:9, 1280 x 720 |
| Page background | `#F4F6FA` (from theme) |
| Cards / visuals | White, 8 px radius, 12 px gutters (use Format -> Align / Distribute) |
| Fonts | Segoe UI Semibold titles (12 pt), Segoe UI labels (10 pt), KPI numbers 28 pt |
| Status colours (use everywhere) | Churn Risk `#D64545` - Expansion `#1F9D74` - Low Adoption `#E8A33D` - Healthy `#4C78A8` |
| Navigation | Insert -> Buttons -> Navigator -> **Page navigator**, top-right, same place on every page |
| Slicers | Month slicer (dropdown, **single select, default Aug 2026**), Region, Tier, Industry. Sync across pages (View -> Sync slicers) |

Every page follows one pattern: **title -> one-sentence takeaway -> KPIs -> evidence -> action list.**
The takeaway is a Card visual bound to a dynamic text measure (`Headline Insight`, `Churn Radar Callout`, `Expansion Callout`), so it updates with the filters.

Turn on **Alt text** for every visual (Format -> General). It is quick, and hiring managers notice it.

---

## 2. Page 1 - Executive Summary

**Takeaway (Card, `[Headline Insight]`):** flat platform usage hides a large, at-risk customer cohort.

```
+---------------------------------------------------------------+ [nav]
| B2B Product Health & Revenue Expansion Tracker   [Month][Reg][Tier]
| <Headline Insight card - full width>                          |
+-----------+-----------+-----------+-----------+---------------+
| Accounts  |   ARR     | Seat Util | API Calls | ARR at Risk   |
|  KPI 1    |  KPI 2    |  KPI 3    | + MoM %   | (red)         |
+-----------+-----------+-----------+-----------+---------------+
| Usage index by cohort (Mar=100)   | ARR by account status     |
| line chart                        | bar chart                 |
+-----------------------------------+---------------------------+
| Tier summary matrix                                           |
+---------------------------------------------------------------+
```

| Visual | Fields | Notes |
|---|---|---|
| 5 KPI cards | `[Total Accounts]`, `[ARR]`, `[Seat Utilization %]`, `[API Calls]` with `[API MoM %]` as subtitle, `[ARR at Risk]` | Colour the last card's number `#D64545` |
| **Line chart: Usage index by cohort** | X: `Usage[usage_month]`; Y: `[API Usage Index - All Accounts]`, `[API Usage Index - Churn Risk]`, `[API Usage Index - Expansion]`, `[API Usage Index - Healthy & Low Adoption]` | This is the hero visual. Make "All Accounts" grey and dashed, Churn Risk red, Expansion green. Add a data label on the last point only. Expected: All ~99, Churn Risk ~13, Expansion ~172 |
| Bar chart: ARR by account status | Axis: `AccountSnapshot[account_status]`; Value: `[ARR]` | Data labels on; set each bar colour to the status colour |
| Matrix: tier summary | Rows: `Usage[contract_tier]`; Values: `[Total Accounts]`, `[ARR]`, `[Seat Utilization %]`, `[Credit Utilization %]`, `[ARR at Risk]`, `[% ARR at Risk]` | Data bars on `% ARR at Risk`; sort Enterprise -> Standard |

---

## 3. Page 2 - Churn Radar

**Takeaway (Card, `[Churn Radar Callout]`)**

```
| Takeaway card                                                 |
| Accounts at Risk | % Accts at Risk | ARR at Risk | % ARR at Risk|
+----------------------------------+----------------------------+
| Scatter: seat util vs MoM change | Stacked bar: at-risk       |
| (bubble = ARR)                   | accounts by tier x region  |
+----------------------------------+----------------------------+
| Top 15 at-risk accounts by ARR (table with action)            |
```

| Visual | Fields | Notes |
|---|---|---|
| 4 KPI cards | `[Accounts at Risk]`, `[% Accounts at Risk]`, `[ARR at Risk]`, `[% ARR at Risk]` | |
| **Scatter** | X: `[Seat Utilization %]`; Y: `[API MoM %]`; Size: `[ARR]`; Legend: `AccountSnapshot[account_status]`; Details: `Usage[account_name]` | Add a constant line at Y = -30%. Colour by status. The bottom-left cluster is the "silent churn" zone |
| Stacked bar | Axis: `Usage[contract_tier]`; Legend: `Usage[global_region]`; Value: `[Accounts at Risk]` | |
| **Table: top 15** | `Usage[account_name]`, `Usage[contract_tier]`, `Usage[global_region]`, `[ARR]`, `[API MoM %]`, `[Seat Utilization %]`, `AccountSnapshot[recommended_action]` | Visual filter: `account_status = Churn Risk`; Top N = 15 by `[ARR]`. Conditional format `[API MoM %]` font colour with `[MoM Trend Color]` (Field value). Prefix with `[MoM Trend Arrow]` if you like |
| Drill-through | Enable drill-through to Page 5 by `account_name` | |

---

## 4. Page 3 - Expansion Targets

**Takeaway (Card, `[Expansion Callout]`)**

| Visual | Fields | Notes |
|---|---|---|
| 5 KPI cards | `[Expansion Accounts]`, `[Expansion Base ARR]`, `[Estimated Expansion ARR]`, `[Tier Upgrade Candidates]`, `[Accounts Over Credit Allowance]` | Estimated Expansion ARR is an assumption (+25% seats) - label it "illustrative" in the card subtitle |
| **Scatter** | X: `[Credit Utilization %]`; Y: `[API 3M Growth %]`; Size: `[ARR]`; Legend: `account_status`; Details: `account_name` | Reference lines: X = 90%, Y = 25%. The top-right quadrant is the sales list |
| Clustered column | Axis: `contract_tier`; Values: `[Credit Allowance (Monthly)]`, `[Credits Consumed]` | Replaces the original "allowance vs consumed" chart, now with overage visible |
| **Table: top 15 targets** | `account_name`, `contract_tier`, `[ARR]`, `[API 3M Growth %]`, `[Seat Utilization %]`, `[Credit Utilization %]`, `recommended_action` | Visual filter: `account_status = Expansion Opportunity`; Top N = 15 by `[ARR]`. Allow *Export data* |

---

## 5. Page 4 - Adoption & Segments

| Visual | Fields | Notes |
|---|---|---|
| Clustered bar | Axis: `contract_tier`; Values: `[Seat Utilization %]`, `[Credit Utilization %]` | Shows how much paid capacity sits unused |
| Matrix (heatmap) | Rows: `industry_vertical`; Columns: `global_region`; Values: `[% Accounts at Risk]` | Conditional format background: white -> `#D64545`. **Add a caption: "Segment attributes are simulated; differences are illustrative."** |
| Decomposition tree | Analyze: `[Accounts at Risk]`; Explain by: `contract_tier`, `global_region`, `industry_vertical` | Interactive, good for demos and the GIF |
| Line chart | X: `usage_month`; Y: `[Active Seats]`, `[Licensed Seats]` | Gap = seat headroom |

---

---

## 6. Page 5 - Churn Risk Prioritization (model-driven)

**Why this page exists:** the Churn Radar (page 2) answers *who* is at risk with a reactive rule. This page adds a second, different question: *of the accounts already on that list, which ones are worth CS time first?* It's driven by a logistic regression model trained in `scripts/churn_prediction.py`.

**Read this before you build it:** an early version of this model tried to predict churn *before* any warning sign appeared. Backtesting showed that doesn't hold up on this dataset - every account it "caught early" was already visibly declining the month before. So this page does **not** claim to predict silent churn. It's a **triage tool**: given the accounts already flagged, which are most likely to keep sliding (act first) versus possibly stabilize (lower priority). Say this plainly if anyone asks - it's a more credible story than an early-warning claim that falls apart under a follow-up question.

### Load two more tables

1. **Get Data -> Text/CSV** -> `data/processed/predicted_risk_scores.csv` -> rename to **`PredictedRisk`**.
2. Same for `data/processed/model_feature_importance.csv` -> rename to **`FeatureImportance`**.
3. In Model view, relate `PredictedRisk[account_id]` (1) -> `Usage[account_id]` (*), single direction, same as `AccountSnapshot`.
4. **Enter Data** -> create a small static table named **`ModelNotes`** with two columns, `Metric` and `Value`, and these rows (from the last run of `scripts/churn_prediction.py` - rerun it and update these if your data changes):

   | Metric | Value |
   |---|---|
   | Backtest AUC (Jul -> Aug, held out) | 1.00 |
   | Precision at top 15% | 1.00 |
   | Recall at top 15% | 0.62 |
   | Truly silent catches (before any visible decline) | 0 |
   | High-priority accounts already on Churn Radar | 100% |

   This table is for the page's context text box, not for measures - it's a snapshot from one model run, not something that recalculates live.

### Add the measures

Add these four measures on `_Measures` (or paste into DAX query view like before):

```dax
High Priority Accounts =
CALCULATE ( DISTINCTCOUNT ( PredictedRisk[account_id] ), PredictedRisk[priority_tier] = "High" )

High Priority ARR =
CALCULATE ( SUM ( PredictedRisk[annual_contract_value] ), PredictedRisk[priority_tier] = "High" )

High Priority Already on Radar % =
VAR OnRadar =
    CALCULATE (
        DISTINCTCOUNT ( PredictedRisk[account_id] ),
        PredictedRisk[priority_tier] = "High",
        PredictedRisk[on_churn_radar_aug] = "Yes"
    )
RETURN
    DIVIDE ( OnRadar, [High Priority Accounts] )

Medium Priority Accounts =
CALCULATE ( DISTINCTCOUNT ( PredictedRisk[account_id] ), PredictedRisk[priority_tier] = "Medium" )
```

### Layout

```
+-----------------------------------------------------------------+
| Takeaway text box: "Not every at-risk account is equally lost.  |
| The model ranks the 241 accounts on the Churn Radar by how      |
| likely they are to keep declining next month, so CS knows where |
| to spend limited time first."                                   |
+-----------+-----------+-----------+-------------------------+
| High Pri. | High Pri. | % already |  Method note card       |
| Accounts  |    ARR    | on Radar  |  (caveat, from          |
|           |           |           |  ModelNotes)            |
+-----------+-----------+-----------+-------------------------+
| Feature importance (bar)          | Backtest lift (line)    |
+------------------------------------+-------------------------+
| Priority table: PredictedRisk filtered to priority_tier <> "Low"
+-----------------------------------------------------------------+
```

| Visual | Fields | Notes |
|---|---|---|
| 3 KPI cards | `[High Priority Accounts]`, `[High Priority ARR]`, `[High Priority Already on Radar %]` | The third card is the honesty check - it should read 100%, and its card subtitle should say "confirms this is a prioritization tool, not new discovery" |
| Method note card | Multi-row card or text box bound to `ModelNotes` | Shows the AUC, precision/recall and the "truly silent catches: 0" line plainly |
| **Feature importance** (bar) | Axis: `FeatureImportance[feature]`; Value: `FeatureImportance[coefficient]` | Horizontal bar, sorted by value. Colour bars by `FeatureImportance[direction]` (two colours: increases risk / decreases risk) via conditional formatting or a legend |
| Backtest lift (line) | X: decile 1-10 from `data/processed/model_backtest_lift.csv` (load as a 4th table, `BacktestLift`); Y: `capture_rate` | Shows the model ranks meaningfully better than random - a rising, concave curve that reaches 100% by decile 10 |
| **Priority table** | `PredictedRisk[account_name]`, `contract_tier`, `annual_contract_value`, `continued_decline_probability`, `priority_tier`, `flag_reason`, `suggested_action` | Filter to `priority_tier <> "Low"`. Sort by `continued_decline_probability` descending, and add a Top N filter (20) so the visual stays readable - the interactive dashboard caps this the same way. Conditional-format `priority_tier` (High = red, Medium = amber). `flag_reason` and `suggested_action` are the "why" and "what to say" columns - this is the part of the page that turns a model score into something a CSM can act on in a call. Rename the columns in the visual to "Why it's flagged" and "Suggested next step" |

`flag_reason`/`suggested_action` are generated from only three features - `mom` (API change vs prior month), `seat_mom` (active seats change vs prior month) and `vol` (usage volatility). Two other features with large model coefficients are deliberately excluded from this customer-facing text (see the code comments and `model_metrics.json` -> `excluded_from_customer_facing_reasons` in `scripts/churn_prediction.py`):
- `trend3` (API change vs 3 months ago) has a *positive* coefficient - recent growth nudges the model's risk score up for most accounts, a mean-reversion artifact of this dataset rather than a real driver. It stays in the model and in the feature-importance chart (correctly labelled "Increases risk"), but is never used to tell a CSM *why* an account is flagged.
- `seat_util` and `credit_util` also have positive coefficients, which look backwards (and are): their raw correlation with decline is negative, as expected, but the sign flips in the model due to collinearity with `mom`/`seat_mom` (a classic suppressor-variable effect). `mom`/`seat_mom` already carry the same signal cleanly.

Add this page to the navigator between "Churn Radar" and "Expansion Targets".

---

## 7. Page 6 - Account Drill-through

Set **drill-through field = `Usage[account_name]`**. Hide this page from the navigator.

| Visual | Fields |
|---|---|
| Card row | `account_name`, `contract_tier`, `global_region`, `industry_vertical`, `[ARR]` |
| Status badge (card) | `AccountSnapshot[account_status]` with font colour from `[Status Color]` |
| Recommended action (card) | `AccountSnapshot[recommended_action]` |
| Line chart | X: `usage_month`; Y: `[API Calls]`, secondary: `[Active Seats]` |
| Gauges / bullet | `[Seat Utilization %]`, `[Credit Utilization %]` |
| Table | Month-by-month raw metrics |

---

## 8. Page 7 - About & Methodology (small but valuable)

A static page of text boxes covering:

- **Purpose** - what business questions the report answers.
- **Data** - 1,000 accounts x 6 months (Mar-Aug 2026). Usage telemetry as provided; firmographics, seat/credit limits and pricing **simulated** by `scripts/data_enhancement.py`.
- **Definitions** - churn risk, expansion opportunity and low adoption (thresholds live in `scripts/data_enhancement.py` and the DAX threshold measures).
- **Assumptions** - list price per seat ($45 / $60 / $80 per month for Enterprise / Premium / Standard); +25% seat uplift for pipeline sizing.
- **Limitations** - no revenue actuals, no real churn outcomes, so risk is a usage-based leading indicator.
- Link to the GitHub repo.

---

## 9. Export the portfolio assets (30 min)

1. **Screenshots:** set the window to 1920 x 1080, hide the filter pane, and capture each page with `Win + Shift + S`. Save as PNG into `screenshots/`:
   `page1_executive_summary.png`, `page2_churn_radar.png`, `page3_expansion_targets.png`, `page4_adoption_segments.png`.
2. **GIF (10-15 s):** record with [ScreenToGif](https://www.screentogif.com/) (free). Show: change the month slicer -> click a red dot on the scatter -> cross-filtering -> drill through to an account. Keep it under 8 MB and save as `screenshots/product_demo.gif`.
3. **PDF:** File -> Export -> **Export to PDF** -> `dashboards/B2B_Product_Health_Tracker.pdf`.
4. **Save** the report as `dashboards/B2B_Product_Health_Tracker.pbix`.
5. Commit and push:
   ```bash
   git add .
   git commit -m "Add dashboard, screenshots and demo"
   git push
   ```

---

## Quality checklist before you share it

- [ ] Every number on Page 1 matches the validation table.
- [ ] Every visual has a title, alt text and a clear unit ($M, %).
- [ ] Status colours are identical on every page.
- [ ] No visual needs more than 3 seconds to understand.
- [ ] README screenshots render on GitHub (file names match exactly, case-sensitive).
- [ ] The "simulated data" disclosure is visible in the README and on the About page.

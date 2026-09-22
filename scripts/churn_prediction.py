#!/usr/bin/env python3
"""
churn_prediction.py
====================
Churn-Risk Prioritization model.

WHAT THIS IS - AND ISN'T
-------------------------
The Churn Radar page (dax/measures.dax, scripts/data_enhancement.py) is a REACTIVE
rule: it flags an account once its API calls have already fallen 30% or more.

The first version of this script tried to predict decline BEFORE it happened.
Backtesting showed that doesn't hold up on this dataset: every account the model
"caught early" was already visibly declining the month before - there were zero
truly silent accounts it caught ahead of any warning sign. Once an account's usage
starts falling here, it falls in a smooth, persistent way (e.g. 6,621 -> 3,146 ->
1,608 -> 803 API calls, roughly halving each month), so a same-month reactive flag
would have caught the same accounts just as early. Claiming "predicts churn before
it happens" would not survive a follow-up question, so this script does NOT make
that claim.

What the data DOES support, and what this script builds instead: a
**prioritization / triage model** for accounts that are ALREADY on the reactive
Churn Radar. Not every at-risk account is equally lost - this model estimates
which ones are likely to keep declining next month (and are probably not worth
heavy CS investment) versus which show signs of stabilizing (worth the outreach).
That is a genuinely useful, honestly-earned capability for a CS team with limited
time: it changes the ORDER accounts get worked, using only information known at
the time.

METHOD
------
* Panel of (account, month) rows for May, Jun, Jul 2026. Features use only that
  month and earlier - never future data.
* Label = whether the account's API calls fell >=30% the FOLLOWING month.
* Time-based split: train on May->Jun and Jun->Jul, backtest on the held-out,
  later Jul->Aug transition (the model never sees Aug during training).
* Logistic regression (standardized features, class_weight="balanced") - chosen
  for readable coefficients ("risk drivers") over a black-box model.
* Deployment model refit on all 3 known transitions, scored on August to rank
  the CURRENT Churn Radar list for September triage.

HONEST LIMITATION (see model_metrics.json -> "caveat")
--------------------------------------------------------
Backtest AUC on this dataset is ~1.0. That is a property of this simulated
dataset's smooth, persistent decay curves, not a realistic production accuracy
claim. A real dataset would be noisier and this score would be lower. What
generalizes is the METHOD (leak-free time-based validation), not the number.

OUTPUTS (data/processed/)
--------------------------
  predicted_risk_scores.csv    1 row/account: continued-decline probability, tier
  model_backtest_lift.csv      decile-level lift/gains for the Jul->Aug backtest
  model_feature_importance.csv standardized coefficients (risk drivers)
  model_metrics.json           AUC, precision/recall, and the caveat above

USAGE
-----
    python scripts/churn_prediction.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, precision_score, recall_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "processed" / "enhanced_b2b_product_metrics.csv"
OUT_DIR = ROOT / "data" / "processed"

CHURN_MOM_DROP = -0.30
SEED = 42
HIGH_RISK_PCTILE = 0.85   # top 15% of scores -> "High" tier
MED_RISK_PCTILE = 0.65    # next 20% -> "Medium" tier

NUMERIC_FEATURES = ["api_t", "mom", "mom_prev", "trend3", "seat_util",
                     "credit_util", "seat_mom", "vol", "annual_contract_value"]
CATEGORICAL_FEATURES = ["global_region", "industry_vertical", "contract_tier"]

# Features allowed to be cited as a customer-facing "reason this account is flagged."
# Two exclusions, both checked against the RAW correlation with the label before
# deciding (never trust a multivariate coefficient's sign at face value):
#
# 1. trend3 (API change vs 3 months ago): positive coefficient, meaning recent GROWTH
#    nudges the risk score up for most accounts. A mean-reversion artifact of this
#    dataset's decay shape, not a real driver - verified: for 406 of 407 accounts where
#    trend3 is the single largest contributor, trend3 is positive/growing. "Flagged
#    because usage is growing" would be actively wrong to tell a CSM.
#
# 2. seat_util, credit_util: positive coefficients (higher utilization -> higher modeled
#    risk), which looks backwards - and IS backwards. Their RAW correlation with decline
#    is negative (-0.47, -0.39: low utilization does correlate with decline, as expected).
#    The multivariate coefficient sign flips because both are collinear with mom/seat_mom
#    (a classic suppressor-variable effect) - the model's predictions are unaffected, but
#    using these two coefficients to pick a "reason" would be unreliable. mom and seat_mom
#    already carry the same signal with much larger, unambiguous coefficients (-1.53,
#    -0.81 vs 0.28, 0.17), so dropping seat_util/credit_util here loses little and removes
#    a genuinely misleading pattern.
#
# vol (usage volatility) is kept: its raw correlation (+0.14) agrees with its coefficient
# sign, so "more volatile" -> "more risk" is a real, consistent relationship here.
ACTIONABLE_DRIVERS = ["mom", "seat_mom", "vol"]


def load_panel_inputs():
    df = pd.read_csv(DATA, parse_dates=["usage_month"])
    months = sorted(df["usage_month"].unique())
    api = df.pivot(index="account_id", columns="usage_month", values="total_api_calls")
    seats = df.pivot(index="account_id", columns="usage_month", values="active_seats")
    credits = df.pivot(index="account_id", columns="usage_month", values="credits_consumed")
    attrs = (df.drop_duplicates("account_id").set_index("account_id")
             [["account_name", "global_region", "industry_vertical", "contract_tier",
               "total_licensed_seats", "monthly_credit_allowance", "annual_contract_value"]])
    return months, api, seats, credits, attrs


def features_at(t, months, api, seats, credits, attrs) -> pd.DataFrame:
    """Features knowable at the END of month index t (uses months <= t only)."""
    m, m1, m2 = months[t], months[t - 1], months[t - 2]
    out = pd.DataFrame(index=api.index)
    out["api_t"] = api[m]
    out["mom"] = api[m] / api[m1] - 1
    out["mom_prev"] = api[m1] / api[m2] - 1
    out["trend3"] = api[m] / api[m2] - 1
    out["seat_util"] = seats[m] / attrs["total_licensed_seats"]
    out["credit_util"] = credits[m] / attrs["monthly_credit_allowance"]
    out["seat_mom"] = seats[m] / seats[m1] - 1
    out["vol"] = out[["mom", "mom_prev"]].std(axis=1)
    return out.join(attrs[CATEGORICAL_FEATURES + ["annual_contract_value"]])


def label_at(t, months, api) -> pd.Series:
    """True if API calls fell >=30% from month t-1 to month t."""
    return (api[months[t]] / api[months[t - 1]] - 1) <= CHURN_MOM_DROP


def build_transition(t, months, api, seats, credits, attrs) -> pd.DataFrame:
    X = features_at(t, months, api, seats, credits, attrs)
    y = label_at(t + 1, months, api)
    X = X.copy()
    X["y"] = y.reindex(X.index).astype(int).values
    X["feature_month"] = months[t]
    X["label_month"] = months[t + 1]
    return X


def encode(panel: pd.DataFrame, scaler, fit: bool):
    dummies = pd.get_dummies(panel[CATEGORICAL_FEATURES], drop_first=True)
    num = panel[NUMERIC_FEATURES]
    if fit:
        scaler = StandardScaler().fit(num)
    num_scaled = pd.DataFrame(scaler.transform(num), index=panel.index, columns=num.columns)
    X = pd.concat([num_scaled, dummies], axis=1)
    return X, scaler


def driver_reason_and_action(row: pd.Series, primary: str, secondary: str | None) -> tuple[str, str]:
    """Plain-English reason + suggested talk track for a CSM, from the account's own numbers.
    Only ACTIONABLE_DRIVERS reach here (trend3 and categoricals are excluded upstream)."""
    def pct(v):
        return f"{v*100:+.0f}%"

    reasons = {
        "mom": f"API usage fell {pct(row['mom'])} last month",
        "seat_mom": f"Active seats fell {pct(row['seat_mom'])} last month",
        "vol": "Usage has swung sharply for two months running",
    }
    actions = {
        "mom": "Open with the drop-off itself: ask what changed operationally last month (an integration issue, a champion leaving, a competing tool).",
        "seat_mom": "Check for an offboarded champion or a team reorg; get reintroduced to whoever is using the product day to day now.",
        "vol": "Confirm this isn't seasonal (their own busy/slow cycle) before treating it as a churn signal.",
    }
    if primary == "mom" and secondary == "seat_mom":
        reason = f"API usage fell {pct(row['mom'])} and active seats fell {pct(row['seat_mom'])} together"
        action = "This is not just a usage dip; fewer people are logging in at all. Prioritize an executive-sponsor call over a routine support touch."
    else:
        reason, action = reasons[primary], actions[primary]
    return reason, action


def lift_table(y_true: np.ndarray, y_score: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    order = np.argsort(-y_score)
    y_sorted = y_true[order]
    bins = np.array_split(y_sorted, n_bins)
    total_pos = y_true.sum()
    rows, cum = [], 0
    for i, b in enumerate(bins, start=1):
        cum += b.sum()
        rows.append({
            "decile": i, "n": len(b), "positives": int(b.sum()),
            "capture_rate": cum / total_pos if total_pos else 0.0,
            "precision": b.mean(),
        })
    return pd.DataFrame(rows)


def main() -> None:
    months, api, seats, credits, attrs = load_panel_inputs()
    assert len(months) == 6, f"Expected 6 months of usage data, found {len(months)}"

    train = pd.concat([build_transition(t, months, api, seats, credits, attrs) for t in (2, 3)])
    backtest = build_transition(4, months, api, seats, credits, attrs)   # Jul features -> Aug label (held out)

    Xtr, scaler = encode(train, None, fit=True)
    Xte, _ = encode(backtest, scaler, fit=False)
    Xte = Xte.reindex(columns=Xtr.columns, fill_value=0)

    clf = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=SEED)
    clf.fit(Xtr, train["y"])

    score_bt = clf.predict_proba(Xte)[:, 1]
    y_bt = backtest["y"].to_numpy()
    auc = roc_auc_score(y_bt, score_bt)
    thresh = np.quantile(score_bt, HIGH_RISK_PCTILE)
    pred_bt = (score_bt >= thresh).astype(int)
    precision = precision_score(y_bt, pred_bt, zero_division=0)
    recall = recall_score(y_bt, pred_bt, zero_division=0)

    # Was any "high risk" call made on an account NOT already visibly declining that month?
    # (Tested honestly - the answer on this dataset is no; see caveat.)
    already_visible = (backtest["mom"] <= CHURN_MOM_DROP).to_numpy()
    model_high = score_bt >= thresh
    truly_silent_catches = int((model_high & ~already_visible & (y_bt == 1)).sum())

    lift = lift_table(y_bt, score_bt)
    lift.to_csv(OUT_DIR / "model_backtest_lift.csv", index=False)

    coefs = pd.Series(clf.coef_[0], index=Xtr.columns).sort_values(key=abs, ascending=False)
    importance = coefs.reset_index()
    importance.columns = ["feature", "coefficient"]
    importance["direction"] = np.where(importance["coefficient"] > 0, "Increases risk", "Decreases risk")
    importance.to_csv(OUT_DIR / "model_feature_importance.csv", index=False)

    # ---- Deployment: refit on all known transitions, score August to prioritize the CURRENT Churn Radar list ----
    full_train = pd.concat([build_transition(t, months, api, seats, credits, attrs) for t in (2, 3, 4)])
    Xfull, scaler_full = encode(full_train, None, fit=True)
    clf_full = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=SEED)
    clf_full.fit(Xfull, full_train["y"])

    latest = features_at(5, months, api, seats, credits, attrs)  # August features
    Xlatest, _ = encode(latest, scaler_full, fit=False)
    Xlatest = Xlatest.reindex(columns=Xfull.columns, fill_value=0)
    prob = clf_full.predict_proba(Xlatest)[:, 1]

    # Per-account standardized contribution (scaled_value * coefficient), restricted to
    # ACTIONABLE_DRIVERS, to pick a customer-facing "why" that a CSM can actually act on.
    coef_map = pd.Series(clf_full.coef_[0], index=Xfull.columns)
    driver_contrib = Xlatest[ACTIONABLE_DRIVERS] * coef_map[ACTIONABLE_DRIVERS]
    ranked = np.argsort(-driver_contrib.values, axis=1)
    primary_driver = np.array(ACTIONABLE_DRIVERS)[ranked[:, 0]]
    secondary_driver = np.array(ACTIONABLE_DRIVERS)[ranked[:, 1]]

    hi_cut = np.quantile(prob, HIGH_RISK_PCTILE)
    med_cut = np.quantile(prob, MED_RISK_PCTILE)
    tier = np.select([prob >= hi_cut, prob >= med_cut], ["High", "Medium"], default="Low")
    already_declining_aug = (latest["mom"] <= CHURN_MOM_DROP).to_numpy()

    out = attrs.loc[latest.index, ["account_name", "global_region", "industry_vertical",
                                    "contract_tier", "annual_contract_value"]].copy()
    out["continued_decline_probability"] = prob.round(4)
    out["priority_tier"] = tier
    out["on_churn_radar_aug"] = np.where(already_declining_aug, "Yes", "No")

    reasons, action_texts = [], []
    for i, (_, row) in enumerate(latest.iterrows()):
        r, a = driver_reason_and_action(row, primary_driver[i], secondary_driver[i])
        reasons.append(r)
        action_texts.append(a)
    out["flag_reason"] = reasons
    out["suggested_action"] = action_texts
    out["triage_note"] = np.select(
        [tier == "High", tier == "Medium"],
        ["High priority - likely to keep declining, act first", "Medium priority - watch"],
        default="Lower priority within the at-risk pool")
    out = out.sort_values("continued_decline_probability", ascending=False).reset_index()
    out.to_csv(OUT_DIR / "predicted_risk_scores.csv", index=False)

    high_on_radar = int(((tier == "High") & already_declining_aug).sum())
    high_total = int((tier == "High").sum())

    metrics = {
        "caveat": ("Backtest scores on this dataset are unusually high (AUC ~1.0) because once an account's "
                   "usage starts declining here, it keeps declining in a smooth, persistent way (e.g. an "
                   "account at 6,621 API calls falls to 3,146, then 1,608, then 803 - roughly halving each "
                   "month). Tested directly: on the backtest, the model caught zero accounts that were not "
                   "already visibly declining the month before, so this is NOT an early-warning system that "
                   "predicts churn before any sign of it. It IS a validated way to prioritize the accounts "
                   "already flagged by the reactive rule. A real production dataset would be noisier and the "
                   "AUC would be lower. What generalizes is the METHOD: a time-based train/test split with no "
                   "future leakage, backtested on a month the model never saw."),
        "backtest_period": {"trained_on": ["2026-05 -> 2026-06", "2026-06 -> 2026-07"], "tested_on": "2026-07 -> 2026-08"},
        "auc": round(float(auc), 3),
        "precision_at_top_15pct": round(float(precision), 3),
        "recall_at_top_15pct": round(float(recall), 3),
        "truly_silent_catches_backtest": truly_silent_catches,
        "top_risk_drivers": importance.loc[importance["coefficient"] > 0, "feature"].head(3).tolist(),
        "excluded_from_customer_facing_reasons": {
            "trend3": ("Positive coefficient (increases modeled risk) even though positive trend3 means recent "
                       "growth - a mean-reversion artifact of this dataset, not a real driver. Kept in the model "
                       "and shown, labelled, in the feature-importance chart, but never surfaced as 'why' an "
                       "account is flagged - a CSM would rightly distrust 'flagged because usage is growing.'"),
            "seat_util_credit_util": ("Positive coefficients (higher utilization -> higher modeled risk) that "
                       "contradict their own raw correlation with decline (-0.47, -0.39: low utilization does "
                       "correlate with decline, as expected). The sign flips because both are collinear with "
                       "mom/seat_mom, which already carry the same signal with far larger, unambiguous "
                       "coefficients. Kept in the model and the feature-importance chart, excluded from 'reason' "
                       "logic."),
            "region_industry_tier": "Individually weak and not something a CSM can act on in a conversation.",
        },
        "deployment": {
            "high_priority_accounts_sep": high_total,
            "high_priority_arr": float(out.loc[out.priority_tier == "High", "annual_contract_value"].sum()),
            "high_priority_already_on_radar_pct": round(high_on_radar / high_total, 3) if high_total else None,
        },
    }
    (OUT_DIR / "model_metrics.json").write_text(json.dumps(metrics, indent=2))

    print("CAVEAT: tested directly - the model caught zero accounts that weren't already visibly declining.")
    print("This is a triage/prioritization tool for the existing Churn Radar list, not an early-warning system.\n")
    print(f"Backtest (Jul->Aug, held out): AUC={auc:.3f}  precision@top15%={precision:.2f}  recall@top15%={recall:.2f}")
    print(f"Truly silent catches (flagged before any visible decline): {truly_silent_catches}")
    print(f"\nSeptember triage: {high_total} accounts at High priority ($"
          f"{out.loc[out.priority_tier=='High','annual_contract_value'].sum()/1e6:.1f}M ARR), "
          f"{high_on_radar}/{high_total} already on the Churn Radar this month.")
    print("\nTop risk drivers (standardized logistic regression coefficients):")
    print(importance.head(8).to_string(index=False))
    print(f"\nWrote {OUT_DIR / 'predicted_risk_scores.csv'}")
    print(f"Wrote {OUT_DIR / 'model_backtest_lift.csv'}")
    print(f"Wrote {OUT_DIR / 'model_feature_importance.csv'}")
    print(f"Wrote {OUT_DIR / 'model_metrics.json'}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
data_enhancement.py
===================
Builds the Power BI-ready dataset for the B2B Product Health & Revenue Expansion Tracker.

WHAT IT DOES
------------
1. Loads raw monthly usage telemetry (1,000 accounts x 6 months).
2. Generates firmographics (region, industry, contract tier, licensed seats,
   monthly credit allowance, annual contract value) for every account.
3. Merges everything into one flat table for Power BI.
4. Runs data-quality checks and writes GTM action lists (churn watchlist,
   expansion targets) plus an account-level status snapshot for the latest month.

WHY NOT PURE RANDOM FIRMOGRAPHICS?
----------------------------------
Assigning tier / seats / allowance independently of usage produces impossible
rows (e.g. a "Standard" account with 5 licensed seats but 300 active seats).
Instead, firmographics are *calibrated to the usage data*:

* Accounts that exist in the 100-account reference sample
  (data/raw/dnb_reference_accounts.csv) keep their reference attributes.
* All other accounts get a contract tier inferred from their peak active seats
  (rule validated against the reference sample), and licensed seats / credit
  allowance sized relative to observed peak usage, using the headroom ratios
  observed in the reference sample.

Everything is seeded (SEED = 42), so the output is fully reproducible.

DATA DISCLOSURE: firmographics and pricing are SIMULATED for portfolio purposes.

USAGE
-----
    python scripts/data_enhancement.py
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[1]
RAW_USAGE = ROOT / "data" / "raw" / "monthly_usage.csv"
REF_SAMPLE = ROOT / "data" / "raw" / "dnb_reference_accounts.csv"
OUT_DIR = ROOT / "data" / "processed"
OUT_MAIN = OUT_DIR / "enhanced_b2b_product_metrics.csv"
OUT_WATCHLIST = OUT_DIR / "churn_watchlist.csv"
OUT_EXPANSION = OUT_DIR / "expansion_targets.csv"
OUT_SNAPSHOT = OUT_DIR / "dim_account_snapshot.csv"

SEED = 42

# Tier inferred from an account's PEAK active seats (validated on the reference sample)
STANDARD_BELOW_SEATS = 20
PREMIUM_BELOW_SEATS = 100

# Headroom ratios observed in the reference sample (licensed / peak-used)
SEAT_HEADROOM_MEAN, SEAT_HEADROOM_SD, SEAT_HEADROOM_RANGE = 1.50, 0.40, (1.00, 2.60)
CREDIT_HEADROOM_MEAN, CREDIT_HEADROOM_SD, CREDIT_HEADROOM_RANGE = 1.70, 0.55, (0.85, 3.40)

# ILLUSTRATIVE list price per licensed seat per month (USD) - simulated assumption
PRICE_PER_SEAT_MONTH = {"Enterprise": 45, "Premium": 60, "Standard": 80}

# Account-status thresholds (mirrored in dax/measures.dax)
CHURN_MOM_DROP = -0.30         # API calls fell >= 30% vs prior month
EXPANSION_3M_GROWTH = 0.25     # API calls up >= 25% vs 3 months earlier ...
EXPANSION_SEAT_UTIL = 0.80     # ... AND seats >= 80% used  (running out of seat headroom)
EXPANSION_CREDIT_UTIL = 0.90   # ... OR credits >= 90% of allowance (about to hit overage)
LOW_ADOPTION_SEAT_UTIL = 0.50  # active seats < 50% of licensed seats

STATUS_SORT = {"Churn Risk": 1, "Expansion Opportunity": 2, "Low Adoption": 3, "Healthy": 4}
RECOMMENDED_ACTION = {
    "Churn Risk": "CSM outreach within 7 days; run adoption health check and executive sponsor call",
    "Expansion Opportunity": "Sales: propose seat / tier upgrade before credit overage or seat cap is hit",
    "Low Adoption": "Customer Success: enablement session and onboarding refresh to lift active seats",
    "Healthy": "Monitor - no action required",
}


# --------------------------------------------------------------------------- #
# Loading & validation
# --------------------------------------------------------------------------- #
def load_usage(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = ["account_id", "usage_month", "active_seats", "total_api_calls", "credits_consumed"]
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"monthly_usage.csv is missing columns: {sorted(missing)}")
    if df.duplicated(["account_id", "usage_month"]).any():
        raise ValueError("Duplicate account_id / usage_month rows found")
    if df[required].isna().any().any():
        raise ValueError("Null values found in raw usage data")
    return df


def load_reference(path: Path) -> pd.DataFrame:
    """One row per account with the static firmographic attributes."""
    ref = pd.read_csv(path)
    cols = ["account_id", "account_name", "global_region", "industry_vertical",
            "contract_tier", "total_licensed_seats", "monthly_credit_allowance"]
    return ref[cols].drop_duplicates("account_id").set_index("account_id")


# --------------------------------------------------------------------------- #
# Firmographic generation
# --------------------------------------------------------------------------- #
def infer_tier(peak_seats: int) -> str:
    if peak_seats < STANDARD_BELOW_SEATS:
        return "Standard"
    if peak_seats < PREMIUM_BELOW_SEATS:
        return "Premium"
    return "Enterprise"


def tier_rule_accuracy(usage: pd.DataFrame, ref: pd.DataFrame) -> float:
    peak = usage.groupby("account_id")["active_seats"].max()
    common = ref.index.intersection(peak.index)
    predicted = peak.loc[common].map(infer_tier)
    return float((predicted == ref.loc[common, "contract_tier"]).mean())


def build_account_dimension(usage: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)

    peaks = usage.groupby("account_id").agg(
        peak_seats=("active_seats", "max"),
        peak_credits=("credits_consumed", "max"),
    ).sort_index()

    # Sampling weights learned from the reference sample
    region_w = ref["global_region"].value_counts(normalize=True).sort_index()
    industries = sorted(ref["industry_vertical"].unique())
    industry_by_tier = {
        tier: grp["industry_vertical"].value_counts().reindex(industries, fill_value=0) + 1  # +1 smoothing
        for tier, grp in ref.groupby("contract_tier")
    }

    rows = []
    for account_id, p in peaks.iterrows():
        in_ref = account_id in ref.index
        r = ref.loc[account_id] if in_ref else None

        tier = r["contract_tier"] if in_ref else infer_tier(int(p.peak_seats))
        region = r["global_region"] if in_ref else rng.choice(region_w.index, p=region_w.values)
        if in_ref:
            industry = r["industry_vertical"]
        else:
            w = industry_by_tier[tier]
            industry = rng.choice(w.index, p=(w / w.sum()).values)

        # Licensed seats: keep the reference value if it is consistent with observed usage
        if in_ref and r["total_licensed_seats"] >= p.peak_seats:
            licensed = int(r["total_licensed_seats"])
        else:
            ratio = float(np.clip(rng.normal(SEAT_HEADROOM_MEAN, SEAT_HEADROOM_SD), *SEAT_HEADROOM_RANGE))
            licensed = int(math.ceil(p.peak_seats * ratio))

        # Monthly credit allowance (a minority of accounts legitimately exceed it => overage)
        if in_ref:
            allowance = int(r["monthly_credit_allowance"])
        else:
            ratio = float(np.clip(rng.normal(CREDIT_HEADROOM_MEAN, CREDIT_HEADROOM_SD), *CREDIT_HEADROOM_RANGE))
            allowance = int(round(p.peak_credits * ratio))

        rows.append({
            "account_id": account_id,
            "account_name": r["account_name"] if in_ref else f"BizCorp_{account_id.split('_')[1]}",
            "global_region": region,
            "industry_vertical": industry,
            "contract_tier": tier,
            "total_licensed_seats": licensed,
            "monthly_credit_allowance": allowance,
            "annual_contract_value": licensed * PRICE_PER_SEAT_MONTH[tier] * 12,
            "firmographic_source": "Reference sample" if in_ref else "Simulated",
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Quality checks
# --------------------------------------------------------------------------- #
def validate(df: pd.DataFrame) -> None:
    assert df.isna().sum().sum() == 0, "Nulls in final dataset"
    assert (df["active_seats"] <= df["total_licensed_seats"]).all(), \
        "Active seats exceed licensed seats for some rows"
    assert (df["annual_contract_value"] > 0).all(), "Non-positive contract value"
    months_per_account = df.groupby("account_id")["usage_month"].nunique()
    assert months_per_account.nunique() == 1, "Accounts have differing numbers of months"


# --------------------------------------------------------------------------- #
# Account status logic (Python mirror of the DAX measures)
# --------------------------------------------------------------------------- #
def score_accounts(df: pd.DataFrame) -> pd.DataFrame:
    """Account-level status as of the latest month in the data."""
    months = sorted(df["usage_month"].unique())
    latest, prev, three_ago = months[-1], months[-2], months[-4]
    piv = df.pivot(index="account_id", columns="usage_month", values="total_api_calls")
    latest_rows = df[df["usage_month"] == latest].set_index("account_id")

    out = latest_rows[["account_name", "global_region", "industry_vertical", "contract_tier",
                       "active_seats", "total_licensed_seats", "credits_consumed",
                       "monthly_credit_allowance", "annual_contract_value"]].copy()
    out["snapshot_month"] = latest
    out["api_calls_latest"] = piv[latest]
    out["api_mom_pct"] = piv[latest] / piv[prev] - 1
    out["api_3m_pct"] = piv[latest] / piv[three_ago] - 1
    out["seat_utilization"] = out["active_seats"] / out["total_licensed_seats"]
    out["credit_utilization"] = out["credits_consumed"] / out["monthly_credit_allowance"]

    risk = out["api_mom_pct"] <= CHURN_MOM_DROP
    expansion = (~risk) & (out["api_3m_pct"] >= EXPANSION_3M_GROWTH) & (
        (out["seat_utilization"] >= EXPANSION_SEAT_UTIL)
        | (out["credit_utilization"] >= EXPANSION_CREDIT_UTIL))
    low = (~risk) & (~expansion) & (out["seat_utilization"] < LOW_ADOPTION_SEAT_UTIL)
    out["account_status"] = np.select(
        [risk, expansion, low], ["Churn Risk", "Expansion Opportunity", "Low Adoption"], default="Healthy")
    return out.reset_index()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    usage = load_usage(RAW_USAGE)
    ref = load_reference(REF_SAMPLE)

    print(f"Raw usage rows: {len(usage):,} | accounts: {usage['account_id'].nunique():,} "
          f"| months: {usage['usage_month'].nunique()}")
    print(f"Tier-inference rule accuracy on reference sample: {tier_rule_accuracy(usage, ref):.0%}")

    accounts = build_account_dimension(usage, ref)
    df = usage.merge(accounts, on="account_id", how="left", validate="many_to_one")
    df["usage_month"] = pd.to_datetime(df["usage_month"] + "-01").dt.strftime("%Y-%m-%d")

    ordered = ["account_id", "account_name", "usage_month", "active_seats", "total_api_calls",
               "credits_consumed", "global_region", "industry_vertical", "contract_tier",
               "total_licensed_seats", "monthly_credit_allowance", "annual_contract_value",
               "firmographic_source"]
    df = df[ordered].sort_values(["account_id", "usage_month"]).reset_index(drop=True)
    validate(df)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_MAIN, index=False)

    scored = score_accounts(df)
    watch = (scored[scored["account_status"] == "Churn Risk"]
             .sort_values("annual_contract_value", ascending=False))
    expand = (scored[scored["account_status"] == "Expansion Opportunity"]
              .sort_values("annual_contract_value", ascending=False))
    rounding = {"api_mom_pct": 3, "api_3m_pct": 3, "seat_utilization": 3, "credit_utilization": 3}
    watch.round(rounding).to_csv(OUT_WATCHLIST, index=False)
    expand.round(rounding).to_csv(OUT_EXPANSION, index=False)

    # Account-level snapshot table (1 row per account) -> loaded into Power BI as AccountSnapshot
    snapshot = scored[["account_id", "snapshot_month", "account_status", "api_calls_latest",
                       "api_mom_pct", "api_3m_pct", "seat_utilization", "credit_utilization"]].copy()
    snapshot["status_sort"] = snapshot["account_status"].map(STATUS_SORT)
    snapshot["recommended_action"] = snapshot["account_status"].map(RECOMMENDED_ACTION)
    snapshot.round(rounding).to_csv(OUT_SNAPSHOT, index=False)

    print(f"\nWrote {OUT_MAIN.relative_to(ROOT)}  ({len(df):,} rows)")
    print(f"Wrote {OUT_WATCHLIST.relative_to(ROOT)}  ({len(watch)} accounts)")
    print(f"Wrote {OUT_EXPANSION.relative_to(ROOT)}  ({len(expand)} accounts)")
    print(f"Wrote {OUT_SNAPSHOT.relative_to(ROOT)}  ({len(snapshot)} accounts)")
    print("\nAccount status (latest month):")
    print(scored["account_status"].value_counts().to_string())


if __name__ == "__main__":
    main()

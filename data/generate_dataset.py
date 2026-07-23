"""
Generate a synthetic multi-entity EPM financial dataset.

Produces a star schema (one fact table, five dimensions) modelling monthly
financial results for a seven-entity group across FY2024-FY2025, with three
planning scenarios (Actual, Budget, Forecast).

Design goals:
  - Structure mirrors how EPM systems store financial data
  - Gross margin stays realistic (COGS derives from revenue, not independently)
  - Department allocations are constant across scenarios so variance is meaningful
  - Reproducible via a fixed random seed

Script written with Claude. Output: EPM_Financials_Dataset_v3.xlsx
"""

import pandas as pd
import numpy as np

# Fixed seed makes the output reproducible - rerunning gives identical numbers,
# so the checkpoints in the build guide always hold.
np.random.seed(7)


# ---------------------------------------------------------------- dimensions

dim_scenario = pd.DataFrame({
    "ScenarioKey": [1, 2, 3],
    "Scenario": ["Actual", "Budget", "Forecast"],
})

# Seven legal entities rolling up to three regions. The Region column is what
# makes consolidation possible - drilling Region -> Entity decomposes the group
# total into its components.
dim_entity = pd.DataFrame([
    (1, "United States",  "Americas"),
    (2, "Canada",         "Americas"),
    (3, "United Kingdom", "EMEA"),
    (4, "Germany",        "EMEA"),
    (5, "India",          "APAC"),
    (6, "Singapore",      "APAC"),
    (7, "Australia",      "APAC"),
], columns=["EntityKey", "Entity", "Region"])

# Relative size of each entity, used to scale every amount. The US is the
# reference at 1.0; Singapore is the smallest at 0.30. This is what produces
# the revenue concentration visible in the dashboard.
size = {1: 1.0, 2: 0.45, 3: 0.6, 4: 0.55, 5: 0.4, 6: 0.3, 7: 0.35}

# Chart of accounts. Account codes follow the usual convention: 4000s revenue,
# 5000s cost of sales, 6000s operating expenses, 7000s below the operating line.
#
# Sign (+1 / -1) encodes the storage convention: revenue is stored positive and
# every expense negative. That means SUM(Amount) returns net income directly,
# and statement subtotals come from filtering StatementGroup. It also keeps
# variance polarity consistent - a positive variance on any line raises net
# income and is favourable, with no per-account adjustment needed.
#
# SortOrder exists so the P&L displays in statement order rather than
# alphabetically (used via "Sort by column" in Power BI).
accounts = [
    (1,  "4000", "Product Revenue",       "Revenue", "Revenue",             10,  1),
    (2,  "4100", "Service Revenue",       "Revenue", "Revenue",             20,  1),
    (3,  "5000", "Cost of Goods Sold",    "COGS",    "Cost of Sales",       30, -1),
    (4,  "5100", "Service Delivery Cost", "COGS",    "Cost of Sales",       40, -1),
    (5,  "6000", "Salaries & Wages",      "OpEx",    "Operating Expenses",  50, -1),
    (6,  "6100", "Marketing",             "OpEx",    "Operating Expenses",  60, -1),
    (7,  "6200", "Travel & Entertainment","OpEx",    "Operating Expenses",  70, -1),
    (8,  "6300", "Rent & Facilities",     "OpEx",    "Operating Expenses",  80, -1),
    (9,  "6400", "IT & Software",         "OpEx",    "Operating Expenses",  90, -1),
    (10, "6500", "Professional Fees",     "OpEx",    "Operating Expenses", 100, -1),
    (11, "6600", "Depreciation",          "OpEx",    "Operating Expenses", 110, -1),
    (12, "7000", "Interest Expense",      "Other",   "Below Operating",    120, -1),
    (13, "7100", "Tax Expense",           "Other",   "Below Operating",    130, -1),
]
dim_account = pd.DataFrame(accounts, columns=[
    "AccountKey", "AccountCode", "Account", "AccountType",
    "StatementGroup", "SortOrder", "Sign",
])

dim_department = pd.DataFrame({
    "DeptKey": [1, 2, 3, 4, 5],
    "Department": ["Sales", "Marketing", "R&D", "G&A", "Operations"],
})
dkey = dict(zip(dim_department.Department, dim_department.DeptKey))

# How each account's cost is split across departments.
#
# These shares are FIXED - the same account always splits the same way, in every
# month and every scenario. An earlier version picked a department at random per
# row, which meant the Actual and Budget for one salary line could land in
# different departments and departmental variance became meaningless.
#
# Salaries spread across all five departments; single-department costs (rent to
# G&A, depreciation to Operations) get a share of 1.0.
alloc = {
    1:  {"Sales": 1.0},
    2:  {"Sales": 1.0},
    3:  {"Operations": 1.0},
    4:  {"Operations": 1.0},
    5:  {"Sales": 0.30, "Marketing": 0.12, "R&D": 0.28, "G&A": 0.14, "Operations": 0.16},
    6:  {"Marketing": 1.0},
    7:  {"Sales": 0.55, "Marketing": 0.20, "Operations": 0.25},
    8:  {"G&A": 1.0},
    9:  {"Operations": 0.60, "G&A": 0.40},
    10: {"G&A": 1.0},
    11: {"Operations": 1.0},
    12: {"G&A": 1.0},
    13: {"G&A": 1.0},
}

# Monthly calendar, 24 months. DateKey is an integer in yyyymmdd form, which is
# the standard surrogate key for a date dimension.
months = pd.date_range("2024-01-01", "2025-12-01", freq="MS")
dim_date = pd.DataFrame({"Date": months})
dim_date["DateKey"]     = dim_date.Date.dt.strftime("%Y%m%d").astype(int)
dim_date["Year"]        = dim_date.Date.dt.year
dim_date["Quarter"]     = "Q" + dim_date.Date.dt.quarter.astype(str)
dim_date["MonthNumber"] = dim_date.Date.dt.month
dim_date["MonthName"]   = dim_date.Date.dt.strftime("%b")
dim_date["MonthYear"]   = dim_date.Date.dt.strftime("%b %Y")
dim_date = dim_date[["DateKey", "Date", "Year", "Quarter",
                     "MonthNumber", "MonthName", "MonthYear"]]


# ------------------------------------------------------------------ baselines

# Monthly revenue for a size-1.0 entity, before seasonality and noise.
rev_base = {1: 900_000, 2: 500_000}

# Cost of sales is calculated as a percentage OF REVENUE rather than generated
# independently. This is the key realism fix: independent generation let gross
# margin wander by nearly 5 percentage points month to month, which no finance
# reviewer would believe. Deriving it holds margin within ~0.6 points.
cogs_ratio = {3: 0.42, 4: 0.42}   # 42% cost ratio -> ~58% gross margin
cogs_src   = {3: 1, 4: 2}         # account 3 keys off product revenue, 4 off service

# Operating expenses are generated independently - they genuinely don't scale
# with monthly revenue the way cost of sales does.
opex_base = {5: 260_000, 6: 90_000, 7: 40_000, 8: 55_000, 9: 45_000,
             10: 30_000, 11: 35_000, 12: 15_000, 13: 60_000}

sign = dict(zip(dim_account.AccountKey, dim_account.Sign))


# ----------------------------------------------------------------- fact table

rows = []

for _, e in dim_entity.iterrows():
    s = size[e.EntityKey]

    for _, d in dim_date.iterrows():

        # Mild sine-wave seasonality, +/-5% across the year.
        seasonal = 1 + 0.05 * np.sin((d.MonthNumber / 12) * 2 * np.pi)

        # 2024 runs at 90% of 2025, producing roughly 11% year-on-year growth.
        growth = 1.0 if d.Year == 2025 else 0.90

        for scen_key, scen in [(1, "Actual"), (2, "Budget"), (3, "Forecast")]:

            # Each scenario behaves differently:
            #   Budget   - smooth plan, low noise
            #   Actual   - what really happened, highest variability
            #   Forecast - slight optimistic drift over budget
            if scen == "Actual":
                drift, noise = 1.00, 0.05
            elif scen == "Budget":
                drift, noise = 1.00, 0.02
            else:
                drift, noise = 1.01, 0.03

            # Revenue is generated first because cost of sales depends on it.
            rev = {}
            for ak, base in rev_base.items():
                v = base * s * seasonal * growth * drift * (1 + np.random.normal(0, noise))
                rev[ak] = v
                for dept, share in alloc[ak].items():
                    rows.append((d.DateKey, e.EntityKey, ak, scen_key,
                                 dkey[dept], round(v * share * sign[ak])))

            # Cost of sales, derived from the revenue just generated.
            for ak, ratio in cogs_ratio.items():
                v = rev[cogs_src[ak]] * ratio * (1 + np.random.normal(0, 0.012))
                for dept, share in alloc[ak].items():
                    rows.append((d.DateKey, e.EntityKey, ak, scen_key,
                                 dkey[dept], round(v * share * sign[ak])))

            # Operating and below-the-line expenses.
            for ak, base in opex_base.items():
                v = base * s * seasonal * growth * drift * (1 + np.random.normal(0, noise))
                for dept, share in alloc[ak].items():
                    rows.append((d.DateKey, e.EntityKey, ak, scen_key,
                                 dkey[dept], round(v * share * sign[ak])))

fact = pd.DataFrame(rows, columns=[
    "DateKey", "EntityKey", "AccountKey", "ScenarioKey", "DeptKey", "Amount",
])
print("fact rows:", len(fact))


# --------------------------------------------------------------------- output

out = "EPM_Financials_Dataset_v3.xlsx"
with pd.ExcelWriter(out, engine="openpyxl") as xl:
    fact.to_excel(xl, sheet_name="FactFinancials", index=False)
    dd = dim_date.copy()
    dd["Date"] = dd.Date.dt.strftime("%Y-%m-%d")
    dd.to_excel(xl, sheet_name="DimDate", index=False)
    dim_entity.to_excel(xl, sheet_name="DimEntity", index=False)
    dim_account.to_excel(xl, sheet_name="DimAccount", index=False)
    dim_scenario.to_excel(xl, sheet_name="DimScenario", index=False)
    dim_department.to_excel(xl, sheet_name="DimDepartment", index=False)

print("wrote", out)

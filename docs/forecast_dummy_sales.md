# Forecast dummy sales data

`tools/forecast_dummy_sales.py` creates deterministic, synthetic fulfilled reseller sales that use the same `orders` and
`order_items` path as the owner forecast. It is intended only for a local development database. The loader refuses to
run when `APP_ENV=production` or when PostgreSQL is not on localhost.

## Why the data has these patterns

- The generator uses 730 daily observations because the application reads at most 730 history days. It gives every
  active product more than the 180 calendar days and 20 nonzero days needed to make Prophet eligible.
- Reseller ordering is intermittent, with fewer orders on Sundays and more on Fridays. Order baskets average roughly
  18-30 packs. These are synthetic distribution assumptions, not claims about Batangas Premium's actual sales.
- Demand rises around the 15th and month end. Philippine labor rules require wages to be paid at least every two weeks
  or twice monthly, at intervals no longer than 16 days. This supports testing a twice-monthly demand effect, while the
  precise 15th/month-end uplift remains a modeling assumption.
- The December 16-24 and December 29-31 periods simulate holiday purchasing. Prophet also receives Philippine country
  holidays from the application, so these observations give it a learnable holiday signal.
- July 10-23 has a smaller local-event uplift. The Batangas City government describes the Sublian Festival as a
  two-week celebration culminating on July 23, matching the event window already present in `app/forecasting.py`.
- Pork products have higher seed baselines than beef products. PSA reported that hogs made up 92.9% of livestock
  slaughtered in accredited slaughterhouses in 2024. This only supports the relative weighting; the pack volumes are
  deliberately scaled to a small retailer and are not official company sales.
- The series includes random closed days, product-level zero days, monthly featured-product uplifts, modest growth,
  and overdispersed quantities. These prevent an unrealistically smooth training set and exercise Prophet, TSB,
  seasonal-naive, and moving-average candidates.

Sources:

- Philippine Labor Code, Article 103: https://lawphil.net/statutes/presdecs/pd1974/pd_442_1974.html
- Philippine Statistics Authority, 2024 livestock and poultry statistics:
  https://psa.gov.ph/statistics/environment-statistics/highlights/component-2-environmental-resources-and-their-use
- Batangas City government, Sublian Festival: https://batangascity.gov.ph/web/20-features/1-sublian-festival

## Create an inspectable CSV

```powershell
.venv\Scripts\python.exe tools\forecast_dummy_sales.py `
  --end-date 2026-10-19 `
  --csv outputs\forecast_dummy_sales_2026-10-19.csv
```

The CSV is reseller-order line-item grain. `quantity_packs` is the target used by the forecast. Prices come from the six products in
the destructive development seed and are included so the same file also produces plausible sales totals.

## Load the local development database

```powershell
.venv\Scripts\python.exe tools\forecast_dummy_sales.py `
  --apply --replace --backdate-products
```

`--replace` deletes only orders whose notes begin with `DUMMY_FORECAST_V1-`. `--backdate-products` is required when a
product's `created_at` is later than the generated history; without it, the application's forecast correctly ignores
sales that predate the catalog record. Run a new forecast from the owner portal after the load finishes.

The loader inserts fulfilled reseller orders with synthetic, unique payment metadata and proof records. It does not
deduct historical batches. This keeps present-day demo inventory usable while supplying the demand history consumed
by the forecast and the owner portal's reseller-sales charts. Do not use the generated records for inventory
reconciliation, payment verification, tax reporting, or business decisions.

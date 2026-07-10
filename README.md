# Amazon.in Manufacturer & Sales Badge Scraper

Apify Actor that fetches `manufacturer`, `brand`, `packer`, `importer`,
`country_of_origin`, `item_model_number`, and the "bought in past month"
badge for Amazon.in ASINs, routed through Apify's residential proxy pool.

## Input

```json
{
  "asins": ["B09Z6TJP7Y", "B00E96N6O8"],
  "maxRetries": 2
}
```

## Output (per dataset item)

```json
{
  "asin": "B09Z6TJP7Y",
  "manufacturer": "L.B.C.P., UNIT-II, HARIDWAR ...",
  "brand": "POND'S",
  "packer": "Hindustan Unilever Ltd, ...",
  "country_of_origin": "India",
  "item_model_number": "PSOC1R0",
  "bought_past_month_raw": "10K+ bought in past month",
  "bought_past_month_min": 10000
}
```

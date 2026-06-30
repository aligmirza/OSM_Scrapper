# OSM Scraper

CSV-based OpenStreetMap scraper for finding places by city, country, and search query.

The script geocodes each city with Nominatim, searches nearby OSM elements with Overpass, and writes results to CSV as each input row finishes so completed data is not lost if a later request fails.

## Files

- `osm_scraper.py` - main scraper script.
- `osm_input_sample.csv` - sample input file.
- `osm_common_query_tags.json` - OSM tag keys used for broad fallback searches.
- `osm_query_aliases.json` - friendly query aliases mapped to OSM tags.

## Input CSV

The input file must contain:

```csv
city,country,query
Toronto,Canada,retail
```

Supported column names:

- City: `city` or `cities`
- Country: `country`
- Query: `query` or `search`

You can use friendly queries from `osm_query_aliases.json`, for example `restaurant`, `hotel`, `pharmacy`, `school`, `bank`, `grocery`, or `retail`.

You can also use a direct OSM tag query:

```csv
city,country,query
Toronto,Canada,amenity=restaurant
```

## Usage

Install the optional certificate helper dependency:

```bash
python3 -m pip install -r requirements.txt
```

Run with the sample input:

```bash
python3 osm_scraper.py
```

Run with your own input:

```bash
python3 osm_scraper.py my_input.csv
```

Set a custom output file:

```bash
python3 osm_scraper.py my_input.csv --output results.csv
```

Change radius and result limit:

```bash
python3 osm_scraper.py my_input.csv --radius 10000 --limit 250
```

Use a custom user agent:

```bash
python3 osm_scraper.py my_input.csv --user-agent "My OSM scraper contact@example.com"
```

## Output

If `--output` is not provided, the scraper generates a meaningful filename based on the query and location, for example:

```text
osm_retail_toronto_canada.csv
```

The CSV is opened at startup, headers are written immediately, and each completed search batch is flushed to disk.

Output columns include:

- Input fields: `input_city`, `input_country`, `input_query`
- OSM identifiers: `osm_type`, `osm_id`, `osm_url`
- Place details: `name`, `category`, `category_value`, `lat`, `lon`
- Contact/address fields: `street`, `city`, `state`, `postcode`, `country`, `phone`, `website`, `email`, `opening_hours`

## Query Aliases

Aliases live in `osm_query_aliases.json`.

Example:

```json
"restaurant": [{"key": "amenity", "value": "restaurant"}],
"retail": [{"key": "shop"}]
```

An alias can map to one or more OSM filters:

```json
"food": [
  {"key": "amenity", "value": "restaurant"},
  {"key": "amenity", "value": "cafe"},
  {"key": "amenity", "value": "fast_food"}
]
```

After editing JSON, validate it with:

```bash
python3 -m json.tool osm_query_aliases.json
python3 -m json.tool osm_common_query_tags.json
```

## Notes

- Nominatim and Overpass are public services. Keep requests respectful and avoid very large bulk scraping.
- The script waits between requests to reduce pressure on OSM services.
- If your local Python certificates are broken, you can run with `--no-verify-ssl`, but fixing local certificates is safer.

## License

This project is licensed under the MIT License. See `LICENSE`.

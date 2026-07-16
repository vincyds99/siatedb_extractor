# Medical Patient Property Extractor

This script extracts medical patient dependet property data from a PostgreSQL database and exports it to CSV files. 

## Purpose

Exports dialysis-related measurements and properties for patients from a database into CSV files. It organizes the data by month and seamlessly merges them into consolidated final exports. The script is designed for batch data extraction and can be run periodically to export updated patient dialysis data.

## Key Components

### Target Properties

The script specifically targets and extracts the following medical metrics:

* `A/V`
* `Mesi CVC`
* `Mesi FAV`
* `QB`
* `PA/QB`
* `PV/QB`
* `Minuti Dialisi`
* `Ore Dialisi` 
* `UF`
* `UF Tot`
* `Alpha EPODose Weight`
* `Alpha ERI`
* `TSAT`
* `FosfAlcIndex`

### Configuration

The system reads its configuration directly from environment variables:

* **Date range:** Defaults from `2023-01-01` to `2026-03-01`
* **Database connection details:** Target host, user, password, database name, etc.
* **Output specifications:** Target output directory and parallel worker count

## Process Pipeline

1. **Time Window Generation:** Generates monthly time windows across the specified date range.
2. **Task Creation:** Creates dedicated export tasks for each property × month combination.
3. **Data Extraction:** Uses parallel processing to execute PostgreSQL `COPY` queries that extract patient measurements.
4. **Monthly Saves:** Saves monthly CSV files to `/property_raw_exports/monthly/{property}/`.
5. **Consolidation:** Merges all monthly CSVs into single consolidated files located in `/property_raw_exports/final/`.
6. **Resume Capability:** Automatically skips properties that already have final output files to save processing time.

## Database Query

Data extraction is driven by the `build_copy_query()` function, which constructs a complex SQL query that:

* Filters patient property measurements strictly by the defined date range.
* Extracts raw values using `Ontology.Property_value_raw()`.
* Filters for measurements that are older than 1 day.
* Outputs the results as a CSV format directly from the database engine.

---

## Setup & Installation Instructions

Follow these steps to deploy the extractor into your environment:

1. **Extract the archive:** Extract the contents of the provided ZIP file to your local machine.
2. **Update Docker Compose:** Replace the existing `docker-compose-datalake.yml` file located at the following path:
   `siate-unified-db/service`
3. **Deploy the Extractor files:** Create a new directory named `extractor` at the following path:
     `siate-unified-db/service/datalake/backend`
   * Paste both the `Dockerfile` and `extract_property_raw_csv.py` files into this newly created `extractor` directory.
4. **Configure Environment:** Paste the `.env` file into the following path:
   `siate-unified-db/service`
# COBOL / EBCDIC test files

Three mainframe-style files with their copybooks, for testing COBOL discovery and ingestion
end to end. Unlike `../CUSTOMER.DAT.txt` / `../PRODUCT.DAT.txt` (plain ASCII, display digits only),
these are **real EBCDIC** with the numeric formats production mainframe extracts use.

| File | Records | Record length | Notes |
|---|---|---|---|
| `CUSTOMER.DAT` / `CUSTOMER.CPY` | 30 | 248 bytes | name/address/contact groups, packed + binary + zoned money |
| `PRODUCT.DAT` / `PRODUCT.CPY` | 25 | 233 bytes | category hierarchy, packed prices, binary stock counts |
| `ORDERS.DAT` / `ORDERS.CPY` | 60 | 130 bytes | one product per record; references customers and products |

Referential integrity holds: every `ORD-CUST-ID` is a `CUST-ID`, every `ORD-PROD-ID` a `PROD-ID`,
and `CUST-ORDER-COUNT` equals the number of orders in `ORDERS.DAT`.

## What's genuinely mainframe about them

- **EBCDIC code page 037** — `J` is `0xD1`, digits are `0xF0`–`0xF9`. Opened as ASCII they're gibberish.
- **`COMP-3` packed decimal** — two digits per byte, sign in the last nibble (`C` +, `D` −).
- **`COMP` binary** — big-endian two's complement; 2/4/8 bytes depending on digit count.
- **Signed zoned decimal** — `CUST-YTD-RETURNS` carries its sign as an overpunch on the last digit.
- **Group items** — `CUST-NAME`, `CUST-ADDRESS`, `PROD-PRICING`, `ORD-AMOUNTS`, … (exercises the flattening).
- **Fixed-length records, no line endings, no delimiters** — every record is exactly RECLEN bytes.

## Deliberate edge cases

| Where | What |
|---|---|
| Customer 100005 | negative packed decimal balance (account in credit) |
| Customer 100008 | negative zoned decimal with overpunch sign |
| Customer 100016 | negative binary (`-500` loyalty points) |
| Customer 100013 | `CUST-DATE-MODIFIED` = `0` (never modified) |
| Customer 100002 | blank middle initial and blank street line 2 |
| Customer 100010 | credit limit `0.00`, suspended |
| Product 10000010 | negative stock on hand (`-12`, backordered) |
| Food products | tax rate `0.0000` (packed zero) |
| Cancelled orders | amounts all `0.00`; ship/delivery dates `0` until they happen |

`expected/*.csv` holds the decoded values of every record, so you can diff what lands in Databricks
against what was written. Column names there are the flattened copybook paths
(`CUST-NAME` > `CUST-FIRST-NAME` becomes `CUST_NAME_CUST_FIRST_NAME`); the columns in your Bronze
table are whatever the discovery step reports. Value `FILLER`s are omitted, as Cobrix drops them.

## Using them

1. **Upload both the `.DAT` and the `.CPY`** to a Databricks Volume, e.g.
   `/Volumes/<catalog>/<schema>/<volume>/cobol/`. The Volumes UI "Upload" works, or:
   `databricks fs cp CUSTOMER.DAT dbfs:/Volumes/<catalog>/<schema>/<volume>/cobol/CUSTOMER.DAT`
2. In **Sources → Discover a source**: type **COBOL**, file path = the `.DAT`, copybook path = the
   `.CPY`, and a **cluster ID** for a cluster with Cobrix attached (Scala suffix must match the
   cluster's Spark version — see the repo README's COBOL section).
3. Leave **Record-sequence file** and **Text-encoded** **unticked**. These are plain fixed-length
   binary records; ticking either makes Cobrix expect a different layout and the read will fail.
4. Build a pipeline from each source. Suggested targets: `customer`, `product`, `orders`.

## Gotchas

- **Don't open/save the `.DAT` files in a text editor, email them, or FTP them in ASCII mode** — any
  newline or encoding translation corrupts the binary. `.gitattributes` here marks them `binary` so
  git won't touch them.
- **The orders file is `ORDERS`, not `ORDER`**, because `ORDER` is a SQL reserved word and the
  generated DDL doesn't quote table names.
- **No `OCCURS` or `REDEFINES`.** The engine doesn't flatten arrays yet (it falls back to STRING), so an
  order with a repeating line-item table would not give clean columns. Orders are one line per record.
- **No variable-length (RDW) variant.** The engine only passes `is_record_sequence`, not Cobrix's
  `is_rdw_big_endian`, so a standard IBM RDW file couldn't be read through it anyway.

## Regenerating

```
python generate_cobol_samples.py
```

Deterministic (fixed seed): the same bytes every time. The script writes the copybooks from the
same field specs it encodes the data from, then re-reads each `.DAT` using only the copybook text
(its own parser, independent of the encoder) and checks every value against `expected/*.csv`.

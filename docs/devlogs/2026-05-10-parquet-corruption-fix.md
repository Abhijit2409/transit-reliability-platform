\# Devlog: Fixing Parquet Corruption in the GTFS-RT Collector



\*\*Date:\*\* 2026-05-10

\*\*Component:\*\* Collector / Storage Layer

\*\*Status:\*\* Resolved



\---



\# 1. Objective



Build a reliable real-time collector for TransLink GTFS-RT vehicle position data that:



\* polls every 30 seconds

\* stores historical telemetry safely

\* uploads files to Supabase Storage

\* remains stable during long-running Railway deployment



The goal was not just data collection, but building a reliable transit telemetry pipeline suitable for downstream analytics workloads.



\---



\# 2. Initial Architecture



The first collector architecture followed this pattern:



1\. Poll GTFS-RT API

2\. Convert protobuf response into pandas DataFrame

3\. Read the current hourly parquet file

4\. Append new rows

5\. Rewrite the same parquet file

6\. Upload updated file to Supabase



Example:



```text

vehicle\_positions\_22.parquet

```



was rewritten every 30 seconds.



\---



\# 3. Problem Discovered



During validation, parquet files became unreadable by pandas.



This failed:



```python

pd.read\_parquet("vehicle\_positions\_22.parquet")

```



with:



```text

Repetition level histogram size mismatch

```



However:



```python

pq.ParquetFile(path).schema\_arrow

```



still worked successfully.



This meant:



\* schema metadata remained readable

\* actual row data became corrupted



Symptoms observed:



\* parquet schema visible

\* row counts visible

\* pandas unable to load rows

\* multiple files affected



\---



\# 4. Root Cause Analysis



The issue was caused by the architecture itself.



The collector repeatedly:



\* read an existing parquet file

\* appended rows

\* rewrote the same file every 30 seconds



This created several risks:



\* partial writes

\* interrupted overwrite operations

\* inconsistent parquet metadata

\* race conditions during long-running execution



Particularly dangerous scenarios:



\* Railway restarts

\* interrupted upload cycles

\* crashes during parquet rewrite



The key realization:



> Parquet is a write-once analytical file format, not a mutable database table.



The pipeline architecture was treating parquet files like mutable appendable storage, which eventually caused corruption.



\---



\# 5. Engineering Fix Implemented



The collector architecture was redesigned around immutable validated writes.



\## New Write Strategy



Each polling cycle now:



1\. Creates a completely new parquet file

2\. Uses a unique timestamped filename

3\. Writes to a temporary `.tmp` file first

4\. Reads the file back using pandas

5\. Verifies schema + row count

6\. Atomically renames the validated file

7\. Uploads only after validation succeeds



Example new file layout:



```text

data/raw/2026-05-10/vehicle\_positions\_07\_070951.parquet

```



No parquet file is ever modified after creation.



\---



\# 6. New Validation Tooling



Created:



```text

src/inspect\_parquet.py

src/parquet\_writer.py

```



Validation features added:



\* schema inspection

\* parquet read-back validation

\* row count verification

\* file integrity validation

\* atomic temp-file writes

\* upload safety checks

\* structured logging



\---



\# 7. Validation \& Testing



After implementing the new architecture:



Successfully verified:



\* pandas can fully read parquet files

\* parquet schema loads correctly

\* Supabase uploads succeed

\* files remain readable after upload

\* multiple collection cycles complete safely

\* Railway-compatible behavior works correctly



Example successful validation:



```text

Read-back validation OK: 309 rows, 10 columns

Atomic rename complete

UPLOAD SUCCESS

```



\---



\# 8. Result



The pipeline now:



\* generates immutable parquet files

\* avoids overwrite corruption

\* validates files before upload

\* safely handles long-running execution

\* produces analytics-ready transit telemetry data



This transformed the project from:



\* basic API collection



into:



\* a more production-style reliable data pipeline



\---



\# 9. Key Engineering Lesson



Building a data pipeline is relatively easy.



Building a reliable pipeline that safely handles:



\* failures

\* corrupted writes

\* interrupted deployments

\* validation

\* long-running execution



is the real engineering challenge.



The biggest lesson from this issue:



> Reliability problems are often architectural problems, not just code bugs.




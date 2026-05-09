# Transit Reliability Intelligence Platform

A portfolio data engineering project that collects live GTFS-Realtime vehicle
position data from **TransLink** (Metro Vancouver's transit authority) every
30 seconds and stores it as partitioned Parquet files for downstream analysis
in Python, SQL, Power BI, and Streamlit.

It runs in two modes:

- **Local** — saves Parquet files under `data/raw/` on your laptop.
- **Cloud (Railway + Supabase)** — same script runs as a Railway worker,
  saves locally to ephemeral disk, and uploads each hourly Parquet file to
  Supabase Storage so the data survives container restarts.

This README walks you through both.

---

## 1. Folder structure

After installation and a few minutes of collecting, your project will look
like this:

```
transit-reliability-platform/
├── collector.py            # the main collector script
├── requirements.txt        # Python dependencies
├── README.md               # this file
├── data/
│   └── raw/
│       └── 2026-05-08/
│           ├── vehicle_positions_18.parquet
│           └── vehicle_positions_19.parquet
└── logs/
    └── collector_2026-05-08.log
```

- **`data/raw/YYYY-MM-DD/vehicle_positions_HH.parquet`** — one Parquet file
  per local hour. Each file accumulates ~120 polls × ~700 vehicles ≈ 84k rows.
- **`logs/collector_YYYY-MM-DD.log`** — daily log file mirroring what you see
  in the terminal.

The collector creates every folder automatically — you don't need to make
them by hand.

---

## 2. One-time setup (Windows)

### 2a. Open a terminal in the project folder

Open **PowerShell** or **Command Prompt**, then:

```powershell
cd C:\Users\abhij\transit-reliability-platform
```

### 2b. (Recommended) Create a virtual environment

A virtual environment keeps this project's packages isolated from your other
Python work.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

You'll know it's active because your prompt now starts with `(.venv)`.

### 2c. Install dependencies

```powershell
pip install -r requirements.txt
```

This installs:

| Package                    | Why                                                |
| -------------------------- | -------------------------------------------------- |
| `requests`                 | HTTP calls to the TransLink API                    |
| `pandas`                   | Tabular data handling                              |
| `pyarrow`                  | Parquet read/write engine                          |
| `gtfs-realtime-bindings`   | Provides `google.transit.gtfs_realtime_pb2`        |
| `protobuf`                 | Required by the GTFS bindings                      |
| `python-dotenv`            | Loads `.env` in local development                  |

### 2d. Add your secrets to a `.env` file

The collector reads all secrets from environment variables. For local
development, copy the template and fill in real values:

```powershell
copy .env.example .env
notepad .env
```

Set at minimum:

```
TRANSLINK_API_KEY=your_translink_key_here
```

To enable the Supabase upload locally, also set:

```
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
SUPABASE_BUCKET_NAME=translink-raw
```

If the Supabase variables are missing, the collector still runs — it just
skips the upload step and saves locally only. This is useful while you set
up the cloud side (Section 9).

> `.env` is in `.gitignore` and **must never be committed**. Anyone with
> your service role key has full read/write access to your Supabase
> Storage.

---

## 3. Configuration

### Environment variables (secrets)

These come from your `.env` file locally, or from the Railway dashboard in
production:

| Variable                       | Purpose                                                |
| ------------------------------ | ------------------------------------------------------ |
| `TRANSLINK_API_KEY`            | Your TransLink developer key (required)                |
| `SUPABASE_URL`                 | Your project URL, e.g. `https://abc.supabase.co`       |
| `SUPABASE_SERVICE_ROLE_KEY`    | Service role key (Supabase Settings → API)             |
| `SUPABASE_BUCKET_NAME`         | Storage bucket name, e.g. `translink-raw`              |

### Code-level settings (non-secret)

The **CONFIG** block at the top of `collector.py` exposes:

| Setting                       | Purpose                                                      |
| ----------------------------- | ------------------------------------------------------------ |
| `POLLING_INTERVAL_SECONDS`    | How often to call the API (default: 30 seconds)              |
| `REQUEST_TIMEOUT_SECONDS`     | Give-up time for a single API call                           |
| `UPLOAD_TIMEOUT_SECONDS`      | Give-up time for a single Supabase upload                    |
| `OUTPUT_FOLDER`               | Where Parquet files go locally (default: `data/raw`)         |
| `SUPABASE_REMOTE_PREFIX`      | Folder prefix inside the bucket (default: `raw`)             |
| `LOG_FOLDER`                  | Where log files go (default: `logs`)                         |
| `COLLECT_ALL_ROUTES`          | `True` = save every vehicle; `False` = filter                |
| `SELECTED_ROUTE_IDS`          | Routes to keep when `COLLECT_ALL_ROUTES = False`             |

---

## 4. Running the collector

From the project folder, with your virtual environment active:

```powershell
python collector.py
```

You should immediately see log lines like:

```
[2026-05-08 19:00:00] [INFO] Starting TransLink GTFS collector
[2026-05-08 19:00:00] [INFO] Polling interval: 30s
[2026-05-08 19:00:00] [INFO] Output folder:    data/raw
[2026-05-08 19:00:00] [INFO] Mode: ALL routes
[2026-05-08 19:00:00] [INFO] Supabase upload: ENABLED -> bucket 'translink-raw', prefix 'raw/'
[2026-05-08 19:00:01] [INFO] API request successful
[2026-05-08 19:00:01] [INFO] Vehicles collected: 680
[2026-05-08 19:00:01] [INFO] Saved data to data/raw/2026-05-08/vehicle_positions_19.parquet
[2026-05-08 19:00:02] [INFO] Uploaded to Supabase: raw/2026-05-08/vehicle_positions_19.parquet
```

Leave this terminal window open — the collector runs forever until you stop
it.

---

## 5. Stopping the collector

In the same terminal, press **Ctrl + C**.

You'll see one final log line:

```
[INFO] Collector stopped by user (Ctrl+C)
```

Any partial hourly file is already safe on disk — Parquet writes are atomic
within each polling cycle, so stopping the collector mid-hour does not
corrupt the file.

---

## 6. Verifying that data is being saved

While the collector is running, open a **second** terminal and run:

```powershell
cd C:\Users\abhij\transit-reliability-platform
dir data\raw
```

You should see today's date as a folder. Inside it you'll find one Parquet
file per hour you've been collecting.

To peek inside the data, start `python` in that second terminal and run:

```python
import pandas as pd
df = pd.read_parquet("data/raw/2026-05-08/vehicle_positions_19.parquet")
print(df.shape)
print(df.head())
print(df["route_id"].value_counts().head(10))
```

Things to confirm:

- `df.shape` grows by roughly 600–700 rows every 30 seconds.
- `collection_timestamp` values are spaced ~30 seconds apart.
- `latitude` / `longitude` look reasonable (~49.2 N, -123.0 W for Vancouver).
- No huge spikes of NULLs in `route_id` or `vehicle_id`.

---

## 7. Error handling — what happens when things go wrong

The collector is built so a single transient failure never kills the loop:

| Failure                       | What happens                                                                            |
| ----------------------------- | --------------------------------------------------------------------------------------- |
| API timeout / 5xx / DNS error | Logs `[ERROR] API request failed: ...`, sleeps, tries again.                            |
| Malformed protobuf            | Logs `[ERROR] Parsing failed: ...`, skips that cycle.                                   |
| Disk write error              | Logs `[ERROR] Save failed: ...`, skips that cycle.                                      |
| Supabase upload error         | Logs `[ERROR] Supabase upload failed (will retry next cycle)`, keeps the local file.    |
| You press Ctrl+C              | Logs a clean shutdown message and exits.                                                |

Because every 30-second cycle re-uploads the **entire** hourly Parquet
file (with `x-upsert: true`), the next successful upload automatically
catches up on any rows the previous failed upload missed. There is no
manual "retry queue" to maintain.

Errors are visible in both the terminal and `logs/collector_YYYY-MM-DD.log`.

---

## 8. How this data supports later analysis

Each row is a **30-second snapshot of one vehicle's location**. Stack enough
of those together and you can answer real operational questions:

- **Route reliability** — for each `route_id`, compute headway variance
  (the gap between consecutive buses). Routes with high variance are
  unreliable from a rider's perspective.
- **Congestion** — group `speed` by route segment and time-of-day.
  Persistently low speeds on a corridor flag chronic congestion hotspots.
- **Bus bunching** — detect cases where two buses on the same `route_id`
  are within a few hundred metres of each other (using `latitude` /
  `longitude`). This is the classic "two buses, then nothing for 20
  minutes" rider complaint.
- **Speed trends** — average speeds by hour-of-day and day-of-week reveal
  when the network slows down (PM peak, Friday evenings, snow days).
- **Peak-hour performance** — compare AM peak (07:00–09:00) and PM peak
  (16:00–18:00) headways and speeds against midday baselines.
- **Transfer reliability into Canada Line stations** — for buses arriving
  near stations like Bridgeport, Marine Drive, Brighouse, Oakridge–41st,
  measure how predictable arrival times are. Unpredictable feeders make
  transfers stressful.

Because the data lands as partitioned Parquet, every downstream tool
(Pandas, DuckDB, BigQuery, Power BI, Streamlit) can read it natively
without any pre-processing.

---

## 9. Cloud deployment (Railway + Supabase Storage)

You'll set up Supabase first (durable storage), then deploy the collector
to Railway as a worker that runs 24/7.

### 9.1. Create a Supabase project

1. Go to <https://supabase.com> and sign in (GitHub login is easiest).
2. Click **New project**.
3. Pick an **Organization**, give the project a name (e.g.
   `translink-data`), set a database password (you don't need it for this
   project but Supabase requires one), choose the region nearest you
   (Vancouver users → **West US (North California)** or **Canada Central**
   if available), and click **Create new project**.
4. Wait ~1 minute for provisioning to finish.

### 9.2. Create a Storage bucket

1. In the Supabase dashboard sidebar, click **Storage**.
2. Click **New bucket**.
3. **Name:** `translink-raw` (must match `SUPABASE_BUCKET_NAME`).
4. Leave **Public bucket** **OFF** — your service role key will handle
   access, and you don't want raw data publicly browsable.
5. Click **Create bucket**.

### 9.3. Get your `SUPABASE_URL` and service role key

1. In the sidebar, click **Project Settings** (gear icon at the bottom).
2. Open **API**.
3. Copy the **Project URL** — that's your `SUPABASE_URL` (e.g.
   `https://abcdefghijklmnop.supabase.co`).
4. Under **Project API keys**, copy the **`service_role`** key (NOT
   `anon`). This is your `SUPABASE_SERVICE_ROLE_KEY`.

> The `service_role` key bypasses Row Level Security and has full
> read/write access to Storage. Treat it exactly like a database password:
> never commit it, never paste it into screenshots, and rotate it from the
> same screen if you ever leak it.

### 9.4. Push the project to GitHub

Railway deploys from a Git repo, so:

1. Create a new **private** repo on GitHub (e.g. `transit-reliability-platform`).
2. From the project folder:

   ```powershell
   git init
   git add .
   git commit -m "Initial commit: cloud-ready TransLink collector"
   git branch -M main
   git remote add origin https://github.com/<your-username>/transit-reliability-platform.git
   git push -u origin main
   ```

3. Verify on GitHub that **`.env` is NOT in the repo**. The included
   `.gitignore` excludes it, but always double-check before continuing.

### 9.5. Deploy on Railway from GitHub

1. Go to <https://railway.app> and sign in with GitHub.
2. Click **New Project** → **Deploy from GitHub repo**.
3. Authorize Railway to access your repo, then select
   `transit-reliability-platform`.
4. Railway auto-detects Python and reads the `Procfile`:

   ```
   worker: python -u collector.py
   ```

   The `-u` flag forces unbuffered output so Railway streams logs in real
   time. Railway will build the image (installing everything in
   `requirements.txt`) and start the worker.

### 9.6. Add environment variables in Railway

1. In your Railway project, click the service → **Variables** tab.
2. Click **New Variable** and add each of these one by one:

   | Name                          | Value                                  |
   | ----------------------------- | -------------------------------------- |
   | `TRANSLINK_API_KEY`           | your TransLink key                     |
   | `SUPABASE_URL`                | your Supabase project URL              |
   | `SUPABASE_SERVICE_ROLE_KEY`   | your `service_role` key                |
   | `SUPABASE_BUCKET_NAME`        | `translink-raw`                        |

3. Railway will redeploy automatically after you save the last variable.

### 9.7. Check the logs

1. In Railway, open your service → **Deployments** → click the active
   deployment → **View Logs**.
2. You should see, within ~30 seconds of the deploy finishing:

   ```
   [INFO] Starting TransLink GTFS collector
   [INFO] Polling interval: 30s
   [INFO] Supabase upload: ENABLED -> bucket 'translink-raw', prefix 'raw/'
   [INFO] API request successful
   [INFO] Vehicles collected: 680
   [INFO] Saved data to data/raw/2026-05-08/vehicle_positions_19.parquet
   [INFO] Uploaded to Supabase: raw/2026-05-08/vehicle_positions_19.parquet
   ```

3. If you see `Supabase upload: DISABLED`, one of the variables is
   misspelled — go back to step 9.6.

### 9.8. Verify files are appearing in Supabase

1. In the Supabase dashboard, click **Storage** → `translink-raw`.
2. You should see a `raw/` folder. Click into it and you'll find a
   date-named folder, then the hourly Parquet file.
3. To pull a file down for analysis, click the file → **Download**, or
   use the Supabase Python client / S3-compatible API from your laptop.

### 9.9. Cost & quota notes

- **Railway** discontinued its always-free tier; expect a small monthly
  bill (typically a few dollars for a 24/7 lightweight worker).
- **Supabase** free tier includes 1 GB of Storage. Collecting all routes
  produces roughly 30–60 MB per day, so the free tier covers ~3–4 weeks
  of continuous collection. After that, either upgrade or delete older
  date folders.
- To reduce volume, set `COLLECT_ALL_ROUTES = False` and pick a small
  `SELECTED_ROUTE_IDS` list (e.g. just the 99 B-Line and the R5).

---

## 10. Troubleshooting

- **`ModuleNotFoundError: No module named 'google'`** — you skipped
  `pip install -r requirements.txt`, or your virtual environment isn't
  active. Re-activate `.venv` and re-run the install.
- **`TRANSLINK_API_KEY environment variable is not set. Exiting.`** — the
  collector can't see your env var. Locally, check that `.env` exists in
  the same folder as `collector.py`. On Railway, check the Variables tab.
- **`401 Unauthorized`** in the logs — your `TRANSLINK_API_KEY` is wrong
  or your developer account is inactive.
- **`Supabase upload failed: 400 ... new row violates row-level security`**
  — you used the `anon` key instead of the `service_role` key. Swap it.
- **`Supabase upload failed: 404 ... Bucket not found`** — your
  `SUPABASE_BUCKET_NAME` doesn't match the bucket you created in
  Section 9.2 (case-sensitive).
- **Files not appearing under `data/raw/`** — confirm the script is
  running from inside the project folder; the path is relative.
- **Parquet read errors in your analysis notebook** — make sure `pyarrow`
  is installed in *that* Python environment too.

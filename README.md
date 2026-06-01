# 🚍 Vancouver Transit Reliability Intelligence Platform

**An end-to-end analytics engineering project that turns live TransLink GTFS-Realtime bus telemetry into operational reliability intelligence corridor scoring, bus-bunching hotspot detection, and a FIFA World Cup 2026 matchday stress simulation delivered through an interactive decision-support dashboard.**

I designed, built, and deployed this platform solo: the real-time data collector, the DuckDB analytics warehouse, the reliability scoring engine, the bunching-detection logic, the scenario simulation with an explainable ML layer, and the production Streamlit application.

---

## ▶️ Try the Dashboard

### 🔗 [**Launch the Live Application**](https://abhijit2409-transit-reliability-platform-dashboardapp-q5otrm.streamlit.app/)

The dashboard is the headline deliverable of this project, not an add-on. In under a minute, you can pick a bus corridor, see *why* it's unreliable, find the exact stop-to-stop segments where buses bunch, and watch every corridor's risk reshuffle live as you switch between FIFA matchday demand scenarios.

**Five interactive views:** Network Overview · Route Deep Dive · Hotspot Explorer · FIFA Stress Lab · Route Comparison

*Built with real telemetry. Every score is derived from a transparent formula. The ML is labelled "decision support, not a forecast."*

**Live Demo:** [Interactive Dashboard](https://abhijit2409-transit-reliability-platform-dashboardapp-q5otrm.streamlit.app/) **GitHub Repository:** [https://github.com/Abhijit2409/transit-reliability-platform](https://github.com/Abhijit2409/transit-reliability-platform)

---

## 📋 Table of Contents

1. [Project Overview](#1--project-overview)  
2. [Why This Project Matters](#2--why-this-project-matters)  
3. [Project Architecture](#3--project-architecture)  
4. [Data Sources](#4--data-sources)  
5. [Technology Stack](#5--technology-stack)  
6. [Data Pipeline](#6--data-pipeline)  
7. [Data Quality Framework](#7--data-quality-framework)  
8. [DuckDB Warehouse Design](#8--duckdb-warehouse-design)  
9. [Reliability Analytics Engine](#9--reliability-analytics-engine)  
10. [Bus Bunching Detection Framework](#10--bus-bunching-detection-framework)  
11. [Top 20 Corridor Intelligence](#11--top-20-corridor-intelligence)  
12. [FIFA World Cup 2026 Stress Simulation](#12--fifa-world-cup-2026-stress-simulation)  
13. [Streamlit Decision Support Dashboard](#13--streamlit-decision-support-dashboard)  
14. [Key Findings](#14--key-findings)  
15. [Business Impact](#15--business-impact)  
16. [Screenshots](#16--screenshots)  
17. [Repository Structure](#17--repository-structure)  
18. [Future Roadmap](#18--future-roadmap)  
19. [Lessons Learned](#19--lessons-learned)  
20. [How To Run The Project](#20--how-to-run-the-project)

---

## 1\. 🎯 Project Overview

I built an analytics platform that ingests **live GTFS-Realtime vehicle-position data** from TransLink (Metro Vancouver's transit authority), processes it through a reproducible data pipeline, and converts raw GPS pings into the operational metrics a transit agency actually plans around: which corridors are unreliable, where buses bunch, and how the network would behave under a major-event demand shock.

The project spans the full analytics-engineering lifecycle: **ingestion → storage → modelling → analytics → simulation → dashboard,** and is deployed publicly so anyone can interact with it.

**What makes it more than a dashboard:**

- It collects its **own** real-world data every 30 seconds rather than using a static download.  
- It implements a **data quality framework** (validation, health reporting, atomic writes) so the analytics rest on trustworthy inputs.  
- It transforms telemetry into **decision-oriented intelligence;** every metric ends in a "so what," not just a number.  
- It includes a **scenario simulation** (FIFA World Cup 2026\) with an **explainable ML layer** that is deliberately honest about its limits.

---

## 2\. 💡 Why This Project Matters

**Why transit reliability?** For riders, reliability beats raw speed: a bus that comes *predictably* every 10 minutes is more useful than one that's sometimes fast and sometimes 25 minutes late. The single biggest reliability killer on frequent bus routes is **bus bunching,** when a delayed bus lets a gap open ahead of it, picks up the extra waiting passengers, falls further behind, and the bus behind catches up until two or three buses run nose-to-tail followed by a long gap. Bunching quietly degrades service without ever showing up as a "cancelled trip," which is exactly why it needs to be *measured* from telemetry.

**Why Vancouver / TransLink?** TransLink publishes a high-quality, openly licensed **GTFS-Realtime** feed, and Metro Vancouver runs some of the busiest bus corridors in North America (the 99 B-Line is among the highest-ridership bus routes on the continent). It's also hosting **seven FIFA World Cup 2026 matches at BC Place, plus a Fan Festival at the PNE,** a real, dated, high-demand event that makes scenario stress modelling concrete instead of hypothetical.

**Why it matters to an employer.** This project demonstrates the exact workflow analytics and data-engineering teams run: stand up a reliable pipeline, enforce data quality, model a domain problem, and ship a decision-support tool stakeholders can actually use.

---

## 3\. 🏗️ Project Architecture

flowchart TD
```mermaid

    A\["TransLink GTFS-Realtime API\<br/\>vehicle positions · polled every 30s"\] \--\> B\["collector.py\<br/\>protobuf parse → tabular rows"\]

    B \--\> C\["Atomic Parquet writer\<br/\>write-once · date-partitioned"\]

    C \--\> D\["Supabase Storage\<br/\>optional cloud backup"\]

    C \--\> E\[("DuckDB Warehouse")\]

    F\["GTFS Static\<br/\>routes · trips · shapes · stops · stop\_times"\] \--\> E

    E \--\> G\["Shape projection\<br/\>GPS → distance-along-route"\]

    G \--\> H\["Observed bus spacing"\]

    H \--\> I\["Bunching detection\<br/\>+ severity classification"\]

    I \--\> J\["Reliability scoring\<br/\>+ corridor priority ranking"\]

    I \--\> K\["Stop-level hotspots"\]

    J \--\> L\["FIFA 2026 stress simulation"\]

    K \--\> L

    L \--\> M\["Explainable ML\<br/\>(decision support)"\]

    J \--\> N\["📊 Streamlit Dashboard"\]

    K \--\> N

    L \--\> N

    M \--\> N

    style N fill:\#1D9E75,color:\#fff

    style A fill:\#534AB7,color:\#fff

    style E fill:\#185FA5,color:\#fff
'''
Two data sources are kept **visibly separated** throughout: *observed telemetry* (what buses actually did) and *published infrastructure* (GTFS Static, what the network is supposed to be). Maintaining that boundary is a core design principle and a recurring theme in the dashboard's honesty framing.

---

## 4\. 🗄️ Data Sources

| Source | Type | What I use it for |
| :---- | :---- | :---- |
| **TransLink GTFS-Realtime** (`/v3/gtfsposition`) | Live protobuf feed | Vehicle positions every 30s the core telemetry |
| **GTFS Static** | Reference tables | `routes.txt`, `trips.txt`, `shapes.txt`, `stops.txt`, `stop_times.txt,` corridor geometry, stop membership, route hierarchy |
| **Public FIFA 2026 event context** | Domain assumptions | BC Place match schedule, PNE Fan Festival, announced extra-service routes modelled as *configurable* scenario inputs, not hard-coded truth |

⚠️ **Scope:** The TransLink RT vehicle feed carries **bus telemetry only**. The entire platform is bus-only by design GTFS Static is filtered to `route_type == 3` at every join, so no SkyTrain / SeaBus / West Coast Express data leaks into any corridor.

---

## 5\. 🛠️ Technology Stack

| Layer | Tools |
| :---- | :---- |
| **Language** | Python |
| **Ingestion** | `requests`, `gtfs-realtime-bindings`, `protobuf` |
| **Storage** | Parquet (`pyarrow`), Supabase (cloud backup) |
| **Warehouse** | **DuckDB** (analytical SQL) |
| **Data wrangling** | pandas, NumPy |
| **Geospatial** | GTFS `shapes.txt`, haversine projection, `folium` |
| **Machine learning** | scikit-learn — Logistic Regression, Random Forest |
| **Visualization** | Plotly, matplotlib, seaborn |
| **Dashboard** | **Streamlit** (multi-page, `st.navigation`) |
| **Deployment** | Streamlit Community Cloud · Railway worker (`Procfile`) |
| **Version control** | Git / GitHub |

**Keywords:** data pipeline, ETL/ELT, data quality, analytics engineering, geospatial analysis, time-series telemetry, KPI design, scenario modelling, machine learning, dashboard development, DuckDB, Streamlit, GTFS.

---

## 6\. 🔄 Data Pipeline

I built a continuously running collector that turns a live feed into a queryable warehouse.

flowchart LR
```mermaid

    A\["Poll API\<br/\>every 30s"\] \--\> B\["Parse protobuf"\]

    B \--\> C\["Build DataFrame"\]

    C \--\> D\["Atomic write\<br/\>tempfile \+ os.replace"\]

    D \--\> E\["data/raw/\&lt;date\&gt;/\*.parquet"\]

    E \--\> F\["Optional Supabase upload"\]

    E \--\> G\["Load into DuckDB"\]

    style D fill:\#EF9F27
'''

**Design decisions that matter:**

- **30-second polling** balances temporal resolution against feed volume fine enough to observe spacing between consecutive buses.  
- **Atomic, write-once Parquet.** Early on, container restarts mid-write corrupted Parquet files. I fixed it with a `tempfile.mkstemp()` \+ `os.replace()` pattern ensures a file is either fully written or not present, never half-written. (Documented in a devlog.)  
- **Date-partitioned immutable files** make the raw layer reproducible and the warehouse load idempotent.  
- **Graceful cloud backup** if Supabase credentials are present; each file mirrors to object storage; if not, collection continues locally without failing.

---

## 7\. ✅ Data Quality Framework

I treated data quality as a first-class layer because analytics built on unverified telemetry is a liability.

| Component | What it checks |
| :---- | :---- |
| **`validate_pipeline.py`** | Bulk Parquet health scan corruption, empties, schema drift |
| **`inspect_parquet.py`** | Single-file interactive inspection |
| **`pipeline_health_report.py`** | Aggregate observability dashboard row counts, null rates, duplicates, freshness, files-per-hour, CSV export with CI-friendly exit codes |
| **Atomic writer** | Prevents corruption at the source |

**Honest data realities I surfaced and documented** (rather than hid):

- `speed` and `bearing` fields are **effectively unpopulated** in this feed, so I make **no corridor-speed claims**.  
- The RT feed is **bus-only** enforced at every join.  
- `route_id` type mismatches (integer vs string across sources) require explicit coercion, caught before they corrupt joins.

💡 **Analytics-engineering principle:** *inspect real data before writing an analysis.* Several assumptions about data shape did not hold; catching them early prevented unsupportable claims from reaching the dashboard.

---

## 8\. 🦆 DuckDB Warehouse Design

I chose **DuckDB** as the analytical engine: it runs in-process (no server to manage), reads Parquet natively, and executes the window-function-heavy SQL that bunching detection needs at interactive speed.

The warehouse follows a **medallion-style layering**:

flowchart TD
```mermaid

    R\["Raw Parquet\<br/\>(bronze)"\] \--\> S\["silver\_vehicle\_positions\_enriched\<br/\>RT × GTFS Static join"\]

    S \--\> P\["vehicle\_positions\_projected\<br/\>GPS → shape distance"\]

    P \--\> H\["observed\_headways\_clean\<br/\>spacing between buses"\]

    H \--\> G\["Reliability \+ bunching\<br/\>(gold)"\]

    DIM\["GTFS dims:\<br/\>dim\_routes · dim\_shapes · dim\_stops"\] \--\> S

    style G fill:\#1D9E75,color:\#fff
'''
Window functions (`LEAD`, `ROW_NUMBER` partitioned by snapshot/direction/shape) order buses along each corridor and measure the gap to the next bus ahead of the raw bunching signal.

---

## 9\. 📊 Reliability Analytics Engine

I engineered a transparent, auditable reliability score per corridor. Rather than a black box, it decomposes into drivers that a planner can read:

base\_fragility \= (100 − reliability\_score)     \# how far from perfect

               \+ bunching\_rate\_pct             \# how often buses bunch

               \+ 2 × severe\_bunching\_rate\_pct  \# severe bunching weighted double

Each route also carries a **corridor priority score** (intervention value), a **route type** **classification** (RapidBus / B-Line / Regular / Express), and an **hourly bunching profile** to show the diurnal pattern.

📌 **KPI:** Network average reliability across the Top 20 corridors \= **88.7 / 100**, with **9,656** observed bunching events analyzed.

---

## 10\. 🚌 Bus Bunching Detection Framework

Detecting bunching honestly is harder than it sounds the naïve version flags every bus parked at a terminal. I built the detection in stages:

1. **Project** each GPS point onto the route shape → distance-along-route (km).  
2. **Filter** high projection-error points (kept only points confidently on route) and **exclude terminal/layover zones** with a configurable buffer because buses legitimately stack at terminals, and that is *not* bunching.  
3. **Measure the spacing** between consecutive buses in the same direction and with the same shape.  
4. **Classify severity** and roll up to the corridor and stop-segment level.

🔧 **The key fix:** my first prototype reported thousands of false "sub-100m" bunching events; most were buses sitting at terminals with identical shape-distances. Adding terminal exclusion and projection-error filtering removed the noise, producing a clean signal suitable for downstream scoring.

---

## 11\. 🏙️ Top 20 Corridor Intelligence

I focused my analysis on the **20 highest-activity bus corridors** where reliability problems affect the most riders and where interventions yield the highest return.

| Rank | Route | Corridor | Type | Reliability | Bunching % | Priority |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| 1 | **R4** | 41st Avenue | RapidBus | 77.6 | 12.0% | **52.4** |
| 2 | 004 | Powell / UBC Exchange | Regular | 82.0 | 7.5% | 34.9 |
| 3 | 099 | Broadway B-Line | B-Line | 84.2 | 7.4% | 33.9 |
| 4 | 019 | Kingsway | Regular | 85.4 | 7.0% | 32.1 |
| 5 | 016 | 29th Ave Station / Arbutus | Regular | 86.3 | 6.4% | 29.0 |

Each corridor links to **stop-level hotspots,** the specific named stop-to-stop segments where bunching concentrates, ranked by intensity and severity, so supervision can be deployed to a *place*, not just a route.

---

## 12\. ⚽ FIFA World Cup 2026 Stress Simulation

Vancouver hosts **seven FIFA 2026 matches**. I built a **scenario-based stress simulation** that projects how each corridor's reliability would degrade under matchday demand, treating all event assumptions as configurable inputs rather than hard-coded facts.

**How it works:**

- Each corridor's **baseline fragility** is multiplied by a transparent **exposure multiplier** (BC Place/downtown/Fan Festival/SkyTrain-connector/peak-period/existing-weakness flags) and a **scenario pressure** term.  
- Four scenarios: **Normal day · FIFA off-peak · FIFA PM-peak  · FIFA knockout**.  
- Output: a **FIFA stress score**, a **risk band** (Low → Critical), and an **adjusted reliability** projection per corridor.

**The ML layer is deliberately honest.** I trained logistic regression and random forest models to classify high-risk corridors and surface feature importance:

| Model | Task | CV | Accuracy | Role |
| :---- | :---- | :---- | :---- | :---- |
| Logistic Regression | classify high-risk | LOOCV | 0.90 | coefficient direction/explainability |
| Random Forest | classify high-risk | LOOCV | 0.95 | feature importance |
| Random Forest Regressor | estimate stress score | LOOCV | R² 0.90 | sensitivity only |

⚠️ **Honesty statement (built into the dashboard):** the ML is **decision support, not a forecast.** It is trained on my own scenario labels, so strong accuracy is *expected* and does **not** indicate real-world predictive skill. Its value is explainability and consistency-checking. True validation would require real matchday AVL telemetry, passenger counts, and crowd-movement data. Stating this clearly is a feature, not a disclaimer.

---

## 13\. 🖥️ Streamlit Decision Support Dashboard

The [live dashboard](https://abhijit2409-transit-reliability-platform-dashboardapp-q5otrm.streamlit.app/) is a multi-page application where a single sidebar selection (route \+ scenario) drives every page—one choice, whole-app response.

| Page | What you can explore |
| :---- | :---- |
| 🗺️ **Network Overview** | A verdict-first landing: Which corridors are fragile, which go FIFA-critical, and operational-intelligence callout cards (most vulnerable route, largest FIFA reliability drop, most severe hotspot corridor, highest priority). |
| 🔍 **Route Deep Dive** | *Why* a single corridor is risky, reliability anatomy, an hourly bunching curve, a "Why is this route risky?" stress decomposition, a plain-English explanation, and a compare-against panel. |
| 📍 **Hotspot Explorer** | An operations investigation tool: filter by route and severity, see hotspot intensity on a distance-along-route strip chart, and read a generated narrative for any stop-pair segment. |
| ⚽ **FIFA Stress Lab** | Live scenario simulation: a route × scenario stress heatmap, the routes most impacted vs a normal day, a before/after reliability dumbbell, and the honest ML section. |
| ⚖️ **Route Comparison** | A full side-by-side of any two corridors' head-to-head metrics, a risk radar, and a planning verdict. |

**Design principles:** verdict-first framing every metric ends in a decision. The ML is honestly labelled 'no faked geography' (hotspots use a distance-along-route view because the source data has stop names but no documented coordinates, with a geocoded map on the roadmap).

---

## 14\. 🔑 Key Findings

📌 **R4 (41st Avenue RapidBus) is the network's top concern**: the lowest reliability (77.6) *and* the highest bunching rate (12.0%), giving it the highest intervention priority. It also offers downtown/BC Place exposure, so it escalates fastest under FIFA scenarios.

- **Reliability problems concentrate in a handful of corridors.** A small set of high-activity routes accounts for a disproportionate share of bunching meaning targeted intervention beats network-wide effort.  
- **The worst single hotspot:** R4 eastbound between **W 41 Ave @ Carnarvon St → W 41 Ave @ Maple St**, with **111 bunching events,** including 17 severe.  
- **FIFA exposure compounds existing weaknesses rather than creating new problems; the routes that degrade most on matchdays are largely the ones that are** already fragile on a normal day.  
- **A genuine coverage gap:** several TransLink-announced FIFA extra-service routes (e.g., 28, 130, 222\) fall **outside** the monitored Top 20; they receive added service but currently lack reliability coverage. The dashboard surfaces this automatically.

---

## 15\. 💰 Business Impact

| Capability | Operational value |
| :---- | :---- |
| **Corridor reliability scoring** | Prioritizes limited supervision/service resources toward the routes with the highest rider impact |
| **Stop-level hotspot detection** | Tells field supervisors *where* to stand, not just which route to watch |
| **Intervention priority ranking** | Quantifies "biggest win if fixed" so planning is ROI-driven |
| **Scenario simulation** | Supports event readiness staffing matchdays before, not during, the disruption |
| **Honest ML \+ methodology page** | Builds stakeholder trust by being explicit about what the model can and cannot claim |

This mirrors how a real transit agency (or any operations analytics team) converts raw telemetry into **resource-allocation decisions,** the core of a data/business analyst's value.

---

## 16\. 📸 Screenshots

*Add screenshots from the [live app](https://abhijit2409-transit-reliability-platform-dashboardapp-q5otrm.streamlit.app/) here. Recommended captures (in priority order):*

1. **Network Overview** — verdict banner \+ KPI strip \+ corridor ranking (the "wow on landing" shot).  
2. **FIFA Stress Lab heatmap** — route × scenario, showing risk bands reshuffle (the signature visual).  
3. **Route Deep Dive** — the "Why is this route risky?" decomposition \+ plain-English explanation for R4.  
4. **Hotspot Explorer** — the distance-along-route strip chart with a stop-pair narrative.  
5. **Route Comparison** — the A-vs-B radar (e.g. R4 vs 099).  
6. **FIFA Stress Lab — before/after dumbbell** — baseline vs adjusted reliability.

\!\[Network Overview\](docs/screenshots/network\_overview.png)

\!\[FIFA Stress Heatmap\](docs/screenshots/fifa\_heatmap.png)

\!\[Route Deep Dive\](docs/screenshots/route\_deep\_dive.png)

---

## 17\. 📁 Repository Structure

transit-reliability-platform/

├── README.md

├── requirements.txt

├── .gitignore

├── Procfile                          \# Railway worker (collector)

│

├── src/

│   ├── ingest/                       \# collector, parquet\_writer, supabase download

│   ├── quality/                      \# health reports, validation, inspection

│   ├── analytics/                    \# week1/2 metrics, corridor geometry/maps/charts

│   └── engines/                      \# shape projection, observed headway, reliability+FIFA

│

├── dashboard/                        \# the deployed Streamlit application

│   ├── app.py                        \# entry point (st.navigation)

│   ├── core/                         \# data loaders, metrics, scenarios, explanations

│   ├── components/                   \# KPI cards, charts, reusable UI

│   ├── pages/                        \# 6 dashboard pages (incl. FIFA Stress Lab)

│   └── data/                         \# baseline CSV outputs the app reads

│

├── notebooks/                        \# exploratory \+ FIFA simulation narratives

├── reports/                          \# health snapshots \+ analytics outputs

└── data/                             \# gitignored: raw parquet, DuckDB warehouse, GTFS static

---

## 18\. 🗺️ Future Roadmap

- **Geocoded hotspot map**: replace the distance-along-route strip chart with a true marker map once stops are geocoded.  
- **Headway adherence**: join RT arrival predictions to scheduled `stop_times` (now achievable since the static schedule is integrated).  
- **Derived speed analytics**: compute speed from vehicle-position deltas rather than the empty feed field.  
- **Multi-day baselines & anomaly alerting:** stabilize scores and flag deviations against a rolling baseline.  
- **dbt-style transformations:** formalize the DuckDB SQL layer into tested, documented models.

---

## 19\. 🎓 Lessons Learned

- **Data quality is the foundation, not an afterthought.** The parquet-corruption bug taught me to design for failure (atomic writes) before scaling collections.  
- **Inspect real data before writing an analysis.** Multiple "obvious" assumptions (speed populated and single-observation window) were incorrect; checking first prevented unsupported claims.  
- **Honest modelling builds more trust than impressive modelling.** Labelling the ML as decision support and explaining *why* its accuracy isn't a forecast is what makes the project credible to a senior reviewer.  
- **Every metric needs a "so what."** The difference between a dashboard and a decision-support tool is whether each number points to an action.

---

## 20\. ⚙️ How To Run The Project

### Try it instantly

[**Open the live dashboard →**](https://abhijit2409-transit-reliability-platform-dashboardapp-q5otrm.streamlit.app/) (nothing to install)

### Run the dashboard locally

git clone https://github.com/Abhijit2409/transit-reliability-platform.git

cd transit-reliability-platform/dashboard

pip install \-r requirements.txt

streamlit run app.py

### Run the data collector

\# from repo root, with a TransLink API key in .env

pip install \-r requirements.txt

python src/ingest/collector.py

Create a `.env` from the template (never commit real keys):

TRANSLINK\_API\_KEY=your\_key\_here

SUPABASE\_URL=                 \# optional cloud backup

SUPABASE\_SERVICE\_ROLE\_KEY=

SUPABASE\_BUCKET\_NAME=

---

## 📬 About

I'm Abhijit, an early-career data professional building production-grade analytics. This project demonstrates **end-to-end analytics engineering, data quality, geospatial analysis, scenario modelling,** **and dashboard development** using real-world data I collect and operate myself.

[**🔗 Explore the live dashboard**](https://abhijit2409-transit-reliability-platform-dashboardapp-q5otrm.streamlit.app/) · *Built with TransLink Open Data. Not affiliated with or endorsed by TransLink.*  

# Daily Orders: API to Warehouse

## Project overview

**Daily Orders: API to Warehouse** is a learning project about the most common job in data engineering: move data from a source system to a warehouse every day, and get a number the business can trust.

It is made for the **Batch pipelines** category of the D8Loop Data Engineering Projects gallery: https://www.d8loop.com/modules/knowledge-corner/data-engineering-projects#batch-pipelines

The gallery describes that category in one line: data moves from a source to a warehouse on a schedule, and most data engineering jobs start here. It promises that a project in it lets the learner practise five things. This project must deliver all five:

- Ingestion
- Incremental loads
- Idempotent reruns
- Scheduling
- Table tests

Its card in the gallery reads: "Load new and changed orders every day, safe to run twice, with tests on the final table." Its chips are Starter, Airflow, dbt, Incremental load and MERGE. Everything in this document serves that card. A batch pipeline is the subject. Other kinds of system are named only to explain why batch is the right choice here.

It is a **starter** project. The reader knows some Python and a little SQL. They have never built a pipeline, never used Airflow, and never used dbt. By the end they have built a real daily batch pipeline and can explain every decision in an interview.

It is the second project of the D8Loop Data Engineering Projects series. The first is [Retail Profit Stack](https://github.com/vijaysngrajput/retail-profit-stack). This project follows the same standard, described under "How every milestone is built" below. Read that repository's README and one milestone folder before you start, so the two projects look like one series.

There is **no AI in this pipeline**. It needs no API key and costs nothing to run. Everything runs on the learner's laptop.

## The business

**Arden** is a fictional online grocery market. Customers order in an app, and Arden delivers the same day in three cities. All data is fictional and generated.

Three teams at Arden need the same numbers:

- **Operations** plans tomorrow's drivers and stock from yesterday's orders.
- **Finance** reports the money each day brought in, and must explain every later change.
- **City managers** compare their city with the other two.

Arden's order system has an API. The order system is the **source**. Its job is to take orders fast. Nobody is allowed to run reports on it directly, because a heavy report slows down the app for customers. So the data must be copied out, every day, to a second place built for questions: the **warehouse**.

An order is not finished when it is placed. It keeps changing:

- Its status moves: `placed` → `packed` → `out_for_delivery` → `delivered`.
- A customer can cancel it, sometimes the next day.
- An item can be out of stock, so the order total goes down after it was placed.
- A refund can arrive days after delivery.
- A test order can be deleted from the order system, with no trace left.

This is the heart of the project. **Yesterday's orders are not the same today as they were yesterday.** A pipeline that only loads "new" orders gives a wrong number.

## The business question

Every morning at 8:00 the operations manager opens a report and asks:

> How many orders did we take yesterday, how much money did they bring in, how does that compare with the day before, and has any earlier day changed since I last looked?

Today a person answers this by hand. They export a file from the admin page and add it up in a spreadsheet. Three things have gone wrong, and each one is a reason for a milestone:

1. **The export got too slow.** It downloads every order ever made, every day.
2. **Sales were counted twice.** Someone ran the load two times on one morning, and the report doubled.
3. **Old days were silently wrong.** Cancelled and refunded orders stayed in the report at full value.

The finished system answers the question from a warehouse table, on a schedule, with tests that fail loudly when the number cannot be trusted.

## What the learner builds

```
Order system API  →  raw landing  →  clean orders table  →  daily report table
   (the source)      (as received)     (one row per order)    (one row per day, per city)
        \________________ scheduled, repeatable, tested ________________/
```

1. **A small fake order API** (the source), run locally. It serves generated orders with pagination and an `updated_since` filter. It has a controllable clock, so the learner can "move to the next day" and see orders change. It can be told to fail (a 500 error, a rate limit, a repeated page).
2. **Extract and load**: Python that calls the API and lands the responses in the warehouse exactly as received, with the time and run that loaded them.
3. **The warehouse**: Postgres in Docker.
4. **Transform**: dbt models from raw, to a clean orders table, to the daily report table.
5. **Tests**: dbt tests on the tables, and pytest for the Python.
6. **A schedule**: one Airflow DAG that runs the whole thing every day and can re-run any past day.

## Milestones

Build forward, one pain at a time. Each milestone ends with something that is **still wrong**, and that is the reason the next milestone exists. State that gap plainly at the end of each milestone.

1. **The report, by hand.** Call the API from a notebook, load every order into one table, and write one SQL query that answers the manager's question. Work the answer out by hand too, so there is an answer key. Meet the first trap: which day does an order placed at 23:58 belong to, in which time zone?
   *Still wrong:* it reloads every order every day. It is slow, and it hits the API's rate limit.
2. **Load only what changed.** Keep a watermark: the latest `updated_at` already loaded. Each run asks the API only for orders changed since then. Land them in a raw table with load metadata.
   *Still wrong:* a changed order is now in the table twice, and the report counts it twice. Run the load two times and it is worse.
3. **Safe to run twice.** Upsert with `MERGE` on the order's key, so one order is always one row with its latest state. Prove the three properties with tests: run it twice and nothing changes; re-run an old day and that day is corrected; an order cancelled later lowers the old day's total. Handle updates that arrive late with a look-back window.
   *Still wrong:* all the logic is in one script. Nothing checks the tables. Nobody knows when the number is wrong.
4. **Models and tests.** Move the SQL into dbt: staging models, a clean `orders` and `order_items`, and the `daily_orders` report as an incremental model. Add table tests: unique keys, no missing values, allowed statuses, every item belongs to an order, and the report's total equals the sum of its orders. Add a freshness check.
   *Still wrong:* a person must remember to run it every morning.
5. **On a schedule.** One Airflow DAG: extract, load, transform, test. It runs daily, retries a failed API call, never loads half a day, and can backfill any range of past days with one command. End with a short "bad morning" drill: the API is down at 7:00, then one page is sent twice. The pipeline recovers and the report is still right.
   *Nothing is still wrong.* Close by naming what a real company would add next (alerts, a quality gate, a dashboard), and say these belong to other projects in the series.

Keep it to these five. Do not grow the scope.

## The planted cases

The generated data must contain these on known dates, so the notebooks can point at them and the answer key can be checked by hand:

- An order whose status changes on a later day.
- An order cancelled the day after it was placed.
- An order whose total drops because an item was out of stock.
- A refund issued three days after delivery.
- An order placed just before midnight local time, which is the next day in UTC.
- An update that reaches the API late, after the watermark has passed it.
- One API page served twice in the same run.
- One order deleted from the source after it was loaded.

Keep the data small enough to hold in your head: about two weeks of days, a few dozen orders a day, three cities. Money is stored in whole cents. The generator takes a seed, so every learner gets the same numbers.

## What the learner learns

The project teaches the working vocabulary of data engineering: the words an interviewer expects a data engineer to use correctly. The list comes from two parts of the D8Loop Data Pipeline knowledge base, **Foundations** and **Building & Orchestration**. Together the five milestones must cover all twelve of those chapters.

The knowledge base is here: https://www.d8loop.com/modules/knowledge-corner/data-pipeline

Open that page first. Find the two sections named **Foundations** (six chapters) and **Building & Orchestration** (six chapters), and read each chapter before you design the milestone that teaches it. Use the chapter's own words and definitions, so the project and the knowledge base never disagree. The other sections on that page are out of scope for this project.

### How a word is taught

These five rules are the standard for every term below. They matter more than the list.

1. **Pain first, name second.** The learner meets the problem in their own data before they hear the word. They see the report double, and only then learn "idempotent".
2. **One or two new words per notebook.** Never a list of definitions. The vocabulary arrives slowly, across the whole project.
3. **Each word is shown on Arden's rows.** The input rows, the code, the result. No word is explained only in a sentence.
4. **Each word is compared with its opposite.** Full with incremental. Pull with push. Append with upsert. The learner runs both and sees the difference in the numbers.
5. **Each word ends as an interview line.** One or two sentences the learner can say, with the example from this project in it.

Keep a `GLOSSARY.md` at the root of the repository. Each entry has the word, its meaning in one or two short sentences, the milestone and notebook that teach it, and the interview line. A word enters the glossary in the milestone that teaches it, not before.

### Where each word is taught

The chapter column names the D8Loop chapter the learner can read for the full theory. Link to it from the notebook. The chapters are under `https://www.d8loop.com/modules/knowledge-corner/data-pipeline/`.

| Milestone | Words the learner must be able to explain afterwards | Chapter (slug) |
|---|---|---|
| 1 | Data pipeline. Source and destination. The stages: ingest, store, transform, serve, observe | `what-a-data-pipeline-is-and-why-it-matters`, `the-data-pipeline-lifecycle` |
| 1 | Batch, streaming and micro-batch. Latency and freshness. Why a morning report is a batch job | `batch-vs-streaming-vs-micro-batch` |
| 1 | Full load. Grain. Event time against load time, and time zones | `data-sources-and-ingestion-patterns` |
| 1 | ETL: the learner transforms in pandas first, then loads. It works, and the cost is felt later | `etl-vs-elt` |
| 2 | Pull and push. Polling. Full load against incremental load | `data-sources-and-ingestion-patterns` |
| 2 | Pagination. Rate limit. Retry with backoff. Watermark, also called high-water mark. Incremental key | `extraction-pulling-data-reliably` |
| 2 | Raw landing table. Load metadata. Append | `loading-and-write-patterns` |
| 2 | Change data capture (CDC). The `updated_at` watermark is query-based CDC, and the learner is told so by name | `change-data-capture-cdc` |
| 3 | Append, overwrite, upsert and MERGE. Primary key, natural key, dedupe key | `loading-and-write-patterns` |
| 3 | Idempotency. Safe re-run. At-most-once, at-least-once and exactly-once | `idempotency-retries-and-exactly-once` |
| 3 | Late-arriving data. Look-back window. Reprocessing one day. Partition by day | `incremental-processing-and-backfills` |
| 3 | Hard delete and soft delete. What query-based CDC cannot see. Log-based and trigger-based CDC as the answer | `change-data-capture-cdc` |
| 4 | ELT: the same transform, now in SQL inside the warehouse. Compared with milestone 1's ETL on the same data | `etl-vs-elt` |
| 4 | Cleaning, type casting, deduplication, joins, enrichment. Set-based against row-by-row | `transformation-fundamentals` |
| 4 | Staging, core and mart layers. Incremental model. Table test. Freshness | `transformation-fundamentals`, `incremental-processing-and-backfills` |
| 5 | Orchestration. DAG. Task and dependency. Schedule and cron. Data interval. Trigger | `orchestration-and-scheduling` |
| 5 | Retry. Catch-up. Backfill. Replay | `orchestration-and-scheduling`, `incremental-processing-and-backfills` |
| 5 | Micro-batch, by running the same DAG every five minutes for a short lab | `batch-vs-streaming-vs-micro-batch` |

### Words this project cannot build, and how it still teaches them

A batch pipeline over an API does not use every idea in those chapters. Do not pretend it does. Teach these by a small, honest demonstration, and say plainly that it is a demonstration:

- **Push.** The pipeline pulls. For one short lab, the fake API also sends a webhook when an order changes. The learner receives it, and compares push with pull: who starts the transfer, what happens when the receiver is down.
- **Log-based CDC.** The pipeline reads `updated_at`, which is query-based CDC. The deleted order shows its limit: a deleted row has no `updated_at` to find. For one short lab, the fake API exposes a change feed: an ordered list of every insert, update and delete. The learner reads it and catches the delete. Say that real systems read the database's own log for this, name Debezium as an example, and send the learner to the chapter.
- **Streaming.** Not built. Milestone 1 explains why a morning report does not need it, and milestone 5's five-minute lab shows how far a batch can be pushed before a stream is the better tool.
- **Exactly-once.** The learner sees that the API call can only be at-least-once, because a retry may repeat a page. The idempotent MERGE turns that into an exactly-once result in the table. Teach it in that order.
- **Slowly changing dimension (SCD) loads and time travel.** Named in one paragraph where they fit, with a link to the chapter. Not built.

### Skills, beyond the words

By the end the learner can also:

- Turn a business question into a table with a clear grain.
- Prove a pipeline is idempotent with a test, not with a claim.
- Re-run a past day without touching the others.
- Read a failed run and say which stage failed and whether a re-run is safe.
- Explain each decision in an interview, with a real example from Arden.

## How every milestone is built

The same standard as Retail Profit Stack, so a reader who knows one project knows both.

Each milestone is a **self-contained folder** under `src/`. It imports nothing from another milestone. Code is copied forward on purpose, so two folders can be compared.

```
src/<milestone>/
  README.md              reading order, and how to run it
  docs/                  BRD.md, HLD.md, LLD.md, each with a data flow diagram
  notebooks/             the lab: numbered notebooks, run in order
  <package>/             production code
  tests/                 tests for the production code
  milestone_N_flow.html  the whole pipeline at this milestone, animated (see "Visual pages")
  milestone_N_recap.html an animated closing page
```

Each milestone is built in this order:

1. **Design documents**: BRD (what the business needs and why), HLD (the parts and how data moves), LLD (exact tables, keys, SQL, functions and checks).
2. **Lab notebooks**: work the solution out step by step. Do each new idea by hand once before a tool does it. Show every result beside the answer key. One idea per notebook. Each notebook opens with where the reader is and ends with what they now know.
3. **Production code and tests**: the same logic rebuilt properly, with a test that proves it gives the same result as the notebook.
4. **Flow page**: the whole pipeline as it stands after this milestone, as one animated HTML page.
5. **Recap page**: what was asked, what was built, what was learned, what is still wrong.

A milestone is not done until its flow page and its recap page exist and open correctly.

## Visual pages

**This is a requirement, not decoration.** A beginner understands a pipeline when they see rows move through it. Text and code alone do not do that. Every milestone must include HTML pages that show the entire data pipeline flow.

### The pages to build

| Page | Where | What it shows |
|---|---|---|
| Business page | `understand_business/arden_orders.html` | How one Arden order lives: placed, packed, delivered, and how it changes later. Which team sees which part. Built before milestone 1 |
| Flow page, one per milestone | `src/<milestone>/milestone_N_flow.html` | The whole pipeline at that milestone, from the API to the manager's report, with rows moving through every stage |
| Recap page, one per milestone | `src/<milestone>/milestone_N_recap.html` | What was asked, what was built, what was learned, what is still wrong |
| Live page | `pipeline_live/` | The finished pipeline, run by the learner on a day they choose |

### What every flow page must do

1. **Show the entire flow, end to end.** Every stage is on the page: the order system API, extract, the raw landing table, the clean orders table, the daily report, and the schedule once it exists. A stage that is not built yet is still drawn, as a dashed box marked "by hand" or "not yet".
2. **Use the same layout in all five milestones.** Each stage stays in the same place on every page. So a reader who opens milestone 3 after milestone 2 sees at once what changed. The part that is new in this milestone is highlighted.
3. **Animate real rows.** Show actual Arden orders, with their ids and amounts, moving from stage to stage. Not abstract dots. The numbers on the page must be the numbers the milestone's code produces.
4. **Play the milestone's problem and its fix.** For example, milestone 2's page shows a changed order landing a second time and the report counting it twice. Milestone 3's page plays the same run and shows MERGE keeping one row. The reader watches the pain, then the cure.
5. **Follow one planted case through the whole flow.** Let the reader pick a case, such as the order cancelled the next day, and watch what happens to it at every stage.
6. **Let the reader control it.** Step forward, step back, play, and "run it again". Running the same day twice must be one click from milestone 3 onward.
7. **Name each stage with its word.** When a stage is highlighted, show the word it teaches (watermark, upsert, backfill) with its meaning in one or two short sentences. The words match `GLOSSARY.md`.
8. **End with what is still wrong.** The last step of the page shows the gap that the next milestone closes.

### How they are made

- Each page is **one self-contained HTML file**: its own CSS and JavaScript inside it, no build step, no server, no external library that must be downloaded. It opens with a double click and it works on GitHub Pages.
- The live page is the one exception, because it runs Python. It has a small local server, as in Retail Profit Stack.
- The data shown in a flow page is generated by a script from the milestone's real outputs, and a test checks that the page's numbers equal the code's numbers. A page must never show a number that was typed by hand.
- Readable on a laptop and on a phone. Readable in light and dark mode. Works with the keyboard. Animation can be paused.
- Study the pages of Retail Profit Stack first (`understand_business/business_workflow.html` and the recap pages), and keep the same look and quality.

### Where they are used

- The README links every page through GitHub Pages, and shows a screenshot of each flow page.
- Each milestone's README tells the learner to open the flow page **before** the notebooks, to see where they are going, and again after, to check what they built.
- D8Loop will link these pages from the project page and the milestone pages.

## The first two minutes

A visitor must be able to see it work before reading anything. From a fresh clone, a few commands start the fake API and the warehouse, run the pipeline for one day, and print the manager's report. Then one more command moves the clock a day, runs again, and shows an earlier day's number change, with the reason.

The README is written for a person who has never seen the project. It says in its first lines what this is, who it is for, what they need, and that it is free. It includes screenshots.

Also build one **live page**, as Retail Profit Stack has: the learner picks a day, presses Run, and watches the rows move from the API to the report, with a button to run the same day twice and a button to break the API. See "Visual pages" above.

## For the website, later

D8Loop will present this project as a project page and one page per milestone. To make that easy, each milestone's README and recap must state clearly:

- Why this milestone exists, what the learner builds, and the outcome.
- The new words it introduces, each explained in one or two short sentences.
- Setup steps that are new, with commands for macOS, Linux and Windows.
- The numbers: notebooks, tests, tables, rows.
- Five things learned, and the one line that is still wrong.

Enable GitHub Pages, so the `.html` pages open as pages. Add an empty `.nojekyll` file at the root.

## Writing rules

Write for a reader with basic English and no data background.

- Short sentences. Simple words. No long paragraphs.
- Explain a word before you use it.
- Show code with its input rows and its result. Never describe a result without showing it.
- State numbers only when the repository can reproduce them.
- No claim like "X is faster" without a measurement from this project.

## Tools

Python 3.12 or later, uv, FastAPI (the fake API, its webhook and its change feed), Postgres, Docker Compose, dbt (Postgres adapter), Apache Airflow, pandas, pytest.

Before you rely on a feature, check the current documentation of the tool: the Airflow version and its Docker setup, and which incremental strategies the dbt Postgres adapter supports. `MERGE` needs Postgres 15 or later.

Docker is only needed from the milestone that first uses Postgres or Airflow. Say clearly in each milestone what must be installed, and keep milestone 1 as light as possible.

## Scope boundaries

- No streaming pipeline. Events as they arrive belong to the clickstream project. Streaming is explained here, not built.
- No lake or table formats. They belong to the lakehouse project.
- No full star schema or dashboard. They belong to the warehouse project.
- No deep quality gates, alerts or incident practice beyond the short drill. They belong to the quality project.
- No AI. It belongs to Retail Profit Stack.
- No cloud account. Everything runs locally and costs nothing.

All data is fictional. The project does not describe any real company's systems or data.

## How to work

- Build one milestone at a time. Never the whole project up front.
- For each milestone, write the three design documents first and stop for the owner's approval before notebooks or code.
- Start by proposing the data model of the fake API, the planted cases with their dates, and the hand-worked answer key. Stop for approval there too.
- When repository reality and this document disagree, say so and ask.

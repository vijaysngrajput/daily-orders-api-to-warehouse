# Glossary

The working words of data engineering, in the order this project teaches them.

A word enters this list in the milestone that teaches it, not before. Each entry has:

- the word, and its meaning in one or two short sentences
- the notebook where you meet it on Arden's own rows
- a line you can say in an interview, with the example from this project

For the full theory of each word, read the chapters of the [D8Loop Data Pipeline knowledge base](https://www.d8loop.com/modules/knowledge-corner/data-pipeline).

## Milestone 1: The report, by hand

| Word | Meaning | Taught in | Say it in an interview |
|---|---|---|---|
| **Data pipeline** | It moves data from where it is made to where it is used. It works in steps, and it runs on a schedule. | Milestone 1, notebook 1 | "My pipeline moved orders from an order API to a warehouse table every morning, so one report could answer the same question every day." |
| **Source** | Where the data is made. You usually do not control it. | Milestone 1, notebook 1 | "The source was the order system's API. Nobody may run reports on it, because a heavy report slows the app for customers." |
| **Destination** | Where the data is used. | Milestone 1, notebook 1 | "The destination was a warehouse table, and the manager's 08:00 report read from it." |
| **Full load** | A full load reads every row on every run. | Milestone 1, notebook 2 | "I started with a full load. It is simple and always complete, but on one morning it read 251 orders when only 29 had changed." |
| **Event time** | When the thing happened in the real world. Business numbers use event time. | Milestone 1, notebook 3 | "The source stored `placed_at` in UTC. An order placed at 23:58 on Saturday was Sunday in UTC, so I convert event time to the business time zone before I take the date." |
| **Grain** | What one row of a table means. | Milestone 1, notebook 3 | "My orders table has one row per order. When I summed order totals at item grain by mistake, Sunday's revenue came out almost four times too big." |
| **ETL** | Extract, transform, load. It transforms the data and then loads only the result. | Milestone 1, notebook 4 | "My first version was ETL in pandas. It dropped the item lines before the load, so a later question about products could not be answered without reading the source again." |
| **The five stages** | Ingest, store, transform, serve, observe. Every pipeline has them. | Milestone 1, notebook 4 | "I check a pipeline stage by stage. My first version had ingest, transform and serve, kept no raw copy in store, and had nothing in observe." |
| **Load time** | When the pipeline copied the row. It is not when the thing happened. | Milestone 1, notebook 5 | "I keep `placed_at` and `loaded_at` on every row. If a run is a day late, load time moves, but the day the customer ordered does not." |
| **Latency** | The time from a change in the source until the warehouse can show it. | Milestone 1, notebook 6 | "A refund given on Monday at 11:00 reached my warehouse on Tuesday at 07:00. With a daily batch, latency is up to one day." |
| **Batch** | One run handles a whole slice of data, then stops. The slice is a time range. | Milestone 1, notebook 6 | "I chose batch because the report is read once a morning. A faster pipeline would cost more and help no one." |
| **Freshness** | How new the data in the warehouse is. | Milestone 1, notebook 6 | "I ask how fresh the result must be before I choose a design. A daily report can wait until morning. A fraud alert cannot." |
| **Micro-batch** | Batch with a very small slice. The job runs again and again, such as every minute. | Milestone 1, notebook 6 | "Micro-batch is still batch: each run is a normal run on a small slice. Its latency is up to one interval." |
| **Streaming** | A job that never stops. It handles each event as it arrives. | Milestone 1, notebook 6 | "Streaming gives the lowest latency and the highest effort: you manage state, late events and restarts. My morning report did not need it." |

## Milestone 2: Load only what changed

| Word | Meaning | Taught in | Say it in an interview |
|---|---|---|---|
| **Incremental load** | An incremental load reads only rows that are new or changed. | Milestone 2, notebook 1 | "I replaced the full load with an incremental load. On one morning it read 29 orders in 2 calls, where the full load read 251 in 11." |
| **Incremental key** | The column that says a row changed. Pick a key that changes whenever the row changes. | Milestone 2, notebook 1 | "My incremental key is `updated_at`, not the creation time. The creation time would have missed every refund and cancellation." |
| **Watermark** | The saved value that marks how far the last run read: the largest value of the incremental key that you have loaded. Also called a high-water mark. | Milestone 2, notebook 2 | "My watermark is the largest `updated_at` I loaded, never the job's clock, and I save it in the same transaction as the rows." |
| **Pagination** | An API returns rows in pages. You ask again until it says it is done. | Milestone 2, notebook 3 | "The API pages with a cursor, not an offset. A cursor stays correct when rows change while I read." |
| **Rate limit** | A rate limit caps how many calls you may make in a period. | Milestone 2, notebook 3 | "The API allowed 12 calls in 10 seconds and answered 429 with a Retry-After header. My extract waits exactly that long." |
| **Retry with backoff** | Try a failed call again after a wait. With backoff, each wait is longer than the last. Always cap the tries. | Milestone 2, notebook 3 | "I retry 5xx with waits of 1, 2, 4 and 8 seconds, capped at five tries, and I never retry a 400. After the last try the job fails loudly and loads nothing." |
| **Raw landing table** | The landing table is the raw copy of what arrived. | Milestone 2, notebook 4 | "I land each API response as JSON in an append-only raw table. When a new question came up about products, I answered it without calling the source again." |
| **Load metadata** | Columns that describe the load, not the data: which run loaded the row, and when. | Milestone 2, notebook 4 | "Every raw row carries a run id, a page number and a load time. The page number is how I saw that the API had sent one page twice." |
| **Append** | Append adds rows at the end of a table. It never touches the rows that are already there. | Milestone 2, notebook 4 | "Append is right for the raw layer, because it keeps every version. It is wrong for a table people count from, because a re-run adds the rows again." |
| **Pull** | In a pull, your job starts the transfer. It asks the source for data on a schedule. | Milestone 2, notebook 5 | "My pipeline pulls. If it misses a day, the next run asks from the old watermark and catches up by itself." |
| **Push** | In a push, the source starts the transfer. It sends data when it is ready. A webhook is a push from an API. | Milestone 2, notebook 5 | "I tried a webhook in a lab. When my receiver was down for a day, those changes were lost and nothing failed. A push source needs a check that data arrived." |
| **Polling** | Asking the source again and again: what is new? | Milestone 2, notebook 5 | "A daily watermark load is polling once a day. It is simple, and it only sees the latest state of each row." |
| **Change data capture (CDC)** | Capturing each change to a table as its own event: an insert, an update or a delete. | Milestone 2, notebook 6 | "CDC has three common forms: query-based, trigger-based and log-based. I choose by asking whether deletes and every intermediate change matter." |
| **Query-based CDC** | CDC by polling with an `updated_at` query. It sees only the latest state, and it cannot see deletes. | Milestone 2, notebook 6 | "My watermark load is query-based CDC. Between two runs 88 changes happened and it read 29 rows, the latest state of each order, which is all a daily report needs." |

## Milestone 3: Safe to run twice

| Word | Meaning | Taught in | Say it in an interview |
|---|---|---|---|
| **Upsert** | An upsert means update plus insert. If the key already exists, the row is updated. If not, the row is added. | Milestone 3, notebook 1 | "My incremental load brought changed orders again, so I write the clean table with an upsert on `order_id`. A changed order replaces its old row and does not sit beside it." |
| **MERGE** | The SQL statement for an upsert. `MATCHED` means the key exists. `NOT MATCHED` means it is new. | Milestone 3, notebook 1 | "I use MERGE with an extra condition: update only when the incoming version is newer. That one condition is what makes a second run a no-op." |
| **Overwrite** | Empty the table and load everything. Also called a full refresh. | Milestone 3, notebook 1 | "Overwrite is safe to re-run, but only when the batch is the whole table. When I overwrote with an incremental batch, 222 of 251 orders disappeared." |
| **Primary key** | The column the database keeps unique in a table. | Milestone 3, notebook 2 | "The clean orders table has a primary key on `order_id`, so a duplicate becomes an error and not a silently doubled number." |
| **Natural key** | A key that comes from the business itself, and means something outside the warehouse. | Milestone 3, notebook 2 | "`order_id` is a natural key: the order system made it. `landing_id` is invented by the warehouse and names a landed row, not an order." |
| **Dedupe key** | The key you keep one row per, when the same thing arrived more than once. | Milestone 3, notebook 2 | "Before the merge I dedupe on `order_id` and keep the newest version. I learned why when the API sent one page twice and the merge refused the batch." |
| **Idempotent** | Idempotent means two runs leave the same result as one run. | Milestone 3, notebook 3 | "Every write in my pipeline is idempotent. I have a test that runs one morning five times and compares every row with the result of one run." |
| **Safe re-run** | A run you can repeat without fear. It is what an idempotent pipeline gives you. | Milestone 3, notebook 3 | "When a run fails halfway, my answer is always the same: run it again. One transaction means nothing was half saved, and a repeat changes nothing." |
| **At-most-once** | A row is never sent twice, but it can be lost. | Milestone 3, notebook 4 | "If you save the watermark before the rows and then crash, you get at-most-once: in my test 24 orders were lost and no error showed." |
| **At-least-once** | A row is never lost, but it can arrive twice. | Milestone 3, notebook 4 | "An API call can only be at-least-once, because a retry may bring a page again. For money, a repeat is better than a loss." |
| **Exactly-once** | Each row changes the result one time. It usually means at-least-once delivery plus an idempotent write. | Milestone 3, notebook 4 | "I do not claim exactly-once delivery. I get an exactly-once result: rows may arrive several times, and the MERGE makes each one count once." |
| **Late row** | A row that reaches the source after the window that should have caught it. The general name is late-arriving data. | Milestone 3, notebook 5 | "A driver's phone was offline, so a cancellation reached the API 12 hours late with an old `updated_at`. My watermark had already passed it." |
| **Lookback** | The window starts N days before the watermark. Also called a look-back window. | Milestone 3, notebook 5 | "I re-read two days before the watermark on every run. It reads about 70 rows for nothing, and that is only safe because the write is idempotent." |
| **Partition** | One slice of a table. Here, all the rows of one business day. | Milestone 3, notebook 6 | "The report is partitioned by business day. A run rebuilds only the days of the orders it touched." |
| **Reprocessing** | Building a past slice again, to correct it. | Milestone 3, notebook 6 | "I reprocess one day with delete-and-insert in one transaction. I can re-run last Friday alone, and my test checks that no other day moves." |
| **Hard delete** | A hard delete removes the row for good. | Milestone 3, notebook 7 | "The source hard-deleted a test order. An `updated_at` query cannot return a row that no longer exists, so it stayed in my warehouse." |
| **Soft delete** | A soft delete keeps the row and marks it with a flag. | Milestone 3, notebook 7 | "I apply deletes as soft deletes: `is_deleted` is set, the row stays, and the report skips it. So I can still say what was deleted, and when." |
| **Log-based CDC** | Capturing changes by reading the database's own log. It sees every change, and deletes. | Milestone 3, notebook 7 | "Deletes need log-based CDC, such as Debezium reading the database log. In my project a change feed in the fake API stood in for it, and I say so." |

## Milestone 4: Models and tests

| Word | Meaning | Taught in | Say it in an interview |
|---|---|---|---|
| **ELT** | Extract, load, transform. It loads the raw data and then transforms it inside the warehouse. | Milestone 4, notebook 1 | "I moved from ETL in pandas to ELT with dbt. The report was identical. The difference showed when a rule changed: I dropped every clean table and rebuilt them from raw, without calling the source." |
| **Model** | A model is one SELECT in a file, and dbt builds a table or a view from it. | Milestone 4, notebook 2 | "Each table in my warehouse is one SELECT in one file. I say what the table is, and dbt works out the build order from the `ref` calls." |
| **Type casting** | Changing a value from one type to another. Text out of JSON becomes a number or a time. | Milestone 4, notebook 2 | "Everything out of the raw JSON is text, so my staging model casts each field to its real type once. Downstream models never cast." |
| **Cleaning** | Cleaning makes each value valid: the right type, one standard spelling and a real meaning. | Milestone 4, notebook 2 | "I clean each column once, in staging: `lower(trim(status))`. A count by status then cannot split into ' Delivered ' and 'delivered'." |
| **Deduplication** | Keeping one row per key. For each key, keep the newest row. | Milestone 4, notebook 3 | "The raw table holds every version, so staging numbers the versions of each order with `row_number()`, newest first, and keeps number one: 328 rows became 251." |
| **Join** | A join puts two tables side by side where a key matches. | Milestone 4, notebook 3 | "I count rows before and after every join. An inner join to a reference table would have dropped a whole city silently, so I use a left join and a test." |
| **Enrichment** | To enrich is to add columns from another table. | Milestone 4, notebook 3 | "The orders model is enriched with the city's name from a small seed table. The seed is a CSV in the dbt project, so the list is in version control." |
| **Row-by-row** | Row-by-row code visits one row at a time. | Milestone 4, notebook 4 | "The worst pattern is a loop that calls the database once per row. On 347 orders mine made 348 trips to the database." |
| **Set-based** | Set-based code says what you want from all the rows together. | Milestone 4, notebook 4 | "I push work to the engine. One GROUP BY replaced the loop: one trip to the database, the same answer, and the rule is visible in the WHERE clause." |
| **Staging, core, mart** | Three layers of models. Staging cleans the raw data. Core holds one row per real thing. A mart is what people read. Other teams say bronze, silver and gold. | Milestone 4, notebook 5 | "My project has raw, staging, core and marts. Each layer reads only from the one before, so I always know where a change belongs." |
| **Incremental model** | A model that, after its first run, rebuilds only part of its table. | Milestone 4, notebook 5 | "My report is an incremental model with delete+insert by business day. Each run rebuilds the last week. I test that the incremental result equals a full refresh." |
| **Table test** | A check on the data in a table. It is a query that returns the rows that break a rule, and it passes when it returns none. | Milestone 4, notebook 6 | "I test the tables, not only the code: unique and not-null keys, accepted statuses, every item has an order, and the report equals the sum of its orders. An unknown status failed the build and the mart was skipped." |
| **Freshness** (as a check) | Is the newest loaded row recent enough? It uses load time, not event time. | Milestone 4, notebook 6 | "Valid but old data passes every table test, so I also check freshness on the raw table: warn after 24 hours, error after 48." |

## Milestone 5: On a schedule

| Word | Meaning | Taught in | Say it in an interview |
|---|---|---|---|
| **Schedule** | A schedule starts a run at a time. | Milestone 5, notebook 1 | "My pipeline runs every day at 07:00 in the business time zone, so the report is ready when the manager opens it at 08:00." |
| **Cron** | Cron is a Linux tool that starts a command at a set time. Its five fields are also the usual way to write a schedule: `0 7 * * *` is every day at 07:00. | Milestone 5, notebook 1 | "Cron can start a job on time, but it cannot say that one step needs another. With two cron lines, a slow load made my transform publish yesterday's numbers with every test green." |
| **Orchestration** | Running the steps of a pipeline in the right order, at the right time, and watching them. An orchestrator is the tool that does it. | Milestone 5, notebook 1 | "I use Airflow as the orchestrator. It gives me order, timing, retries and a run history. It holds no business logic." |
| **Task** | A task is one step of the job. | Milestone 5, notebook 2 | "My tasks are thin. Each one runs a step of a program that also works without the scheduler: extract-load, transform-test, report." |
| **Dependency** | A dependency is a rule that says task B needs task A to finish first. | Milestone 5, notebook 2 | "The transform does not start at a time. It starts when the load has finished. That dependency is the whole reason I left cron." |
| **DAG** | DAG means directed acyclic graph: the tasks of a job and the arrows between them. | Milestone 5, notebook 2 | "My DAG is four tasks in a line. When a table test failed, the publish task went to upstream_failed and never ran." |
| **Data interval** | The slice of time a run covers. | Milestone 5, notebook 3 | "My daily run for Sunday covers Sunday 07:00 to Monday 07:00, and starts when that interval ends." |
| **Logical date** | The day the run is about, not the day it runs. In Airflow it is the start of the data interval. | Milestone 5, notebook 3 | "My tasks take the day from the run's logical date, never from the system clock. So a retry four hours late and a backfill three weeks late load the right day." |
| **Trigger** | Starting a run by hand, for a day you name. | Milestone 5, notebook 3 | "I can trigger a run for any business day by giving its logical date. The run does not know whether a person or the schedule started it." |
| **Retry** (of a task) | A retry runs the failed task again after a short wait. | Milestone 5, notebook 4 | "I set retries per task. The extract retries three times, because an API that is down comes back. The dbt test task has no retries: a test that failed on the data will fail again." |
| **Catchup** | The setting that makes a scheduler run the days it missed, by itself. | Milestone 5, notebook 5 | "I keep catchup off. After an outage I decide when the old days run, so a week of runs does not hit the source the moment the scheduler wakes up." |
| **Backfill** | A backfill runs the job again for days in the past. | Milestone 5, notebook 5 | "I backfilled five days with one command, oldest first, one run at a time, and the result equalled the answer key. It works because each run uses its logical date and each step is idempotent." |
| **Replay** | Running a finished day again. In Airflow you clear the run, and its tasks run again. | Milestone 5, notebook 5 | "I replay single days by clearing the run. I tested it: every row of the orders table and the report was unchanged afterwards." |
| **Micro-batch** (by running it) | Batch with a very small slice. The job runs again and again, and each run is a normal batch run. | Milestone 5, notebook 6 | "I ran the same DAG every five minutes in a lab. No change waited more than five minutes, with the same code and tests. The limit is the time one run takes, so below about a minute I would use a stream." |

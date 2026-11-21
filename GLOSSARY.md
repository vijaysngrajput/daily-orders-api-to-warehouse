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

## Milestones 2 to 5

Their words arrive when those milestones are built.

# workers/

`MarketMonitoringWorker` runs one or more symbols through the complete market-data, attention-scoring, event-lifecycle, and PostgreSQL persistence pipeline.

It is intentionally scheduler-independent. Call `process_symbol()` for one stock or `process_symbols()` for a batch from a route, script, cron task, or future scheduler. Per-symbol failures are isolated, and the worker delegates scoring and event decisions to their owning services.

# workers/

Background ingestion/scoring jobs go here (e.g. the price feed poller and
the significance-scoring loop). Not implemented yet — this step is skeleton
only. Kept as a plain async task/cron for now, per the "no unnecessary
complexity" constraint — no Celery/Kafka/Redis.

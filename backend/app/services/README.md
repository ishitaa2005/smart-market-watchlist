# services/

Business logic goes here, kept out of route handlers (e.g. significance
scoring, watermark diffing, staleness/confidence calculation). Routes stay
thin and call into services. Not implemented yet — this step is skeleton only.

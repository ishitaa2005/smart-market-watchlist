# services/

Business logic is kept out of route handlers:

- `signal_engine.py` computes attention, direction, confidence, reasons, and explanations.
- `event_manager.py` owns significance-event thresholds, hysteresis, and deduplication.
- `watermark.py` computes unseen changes and advances server-side read state.
- `stock_service.py` assembles stock details without duplicating scoring logic.
- `market_data.py` provides the market-feed abstraction and simulator.

# schemas/

Pydantic API contracts are kept separate from SQLAlchemy persistence models:

- `watchlist.py` defines watchlist mutation and list responses.
- `stock.py` defines stock details and attention reasons.
- `event.py` defines unseen-change and acknowledgment responses.

"""
FastAPI application entrypoint.

Run with:
    uvicorn app.main:app --reload

This step only wires up the app, CORS, and the /health route.
No database connection or business logic yet — that comes next.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import changes, health, stocks, watchlist

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(watchlist.router)
app.include_router(changes.router)
app.include_router(stocks.router)


@app.get("/")
async def root():
    return {"message": f"{settings.app_name} is running"}
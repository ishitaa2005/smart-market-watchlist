"""
FastAPI application entrypoint.

Run with:
    uvicorn app.main:app --reload

Wires application settings, CORS, health, watchlist, stock details,
meaningful changes, and development-only demo routes.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import changes, demo, health, stocks, watchlist

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
app.include_router(demo.router)


@app.get("/")
async def root():
    return {"message": f"{settings.app_name} is running"}
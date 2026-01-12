from contextlib import asynccontextmanager
from pathlib import Path

import dotenv
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routes import reservations
from app.scheduler_service import scheduler_service
from app.availability_scraper import close_scraper

dotenv.load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    yield
    # Cleanup on shutdown
    scheduler_service.shutdown()
    await close_scraper()


app = FastAPI(
    title="Sports Reservation API",
    description="API for managing sports court reservations with scheduling",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disable /docs
    redoc_url=None,  # Disable /redoc
)

# Include routers
app.include_router(reservations.router)

# Mount static files
static_dir = Path("static")
if static_dir.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    """Serve the main HTML page"""
    static_file = Path("static/index.html")
    if static_file.exists():
        return FileResponse("static/index.html")
    return {"message": "Sports Reservation API", "status": "running"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )

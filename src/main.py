"""
Main entry point for the 4S1T Agent AI system.
"""
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import Settings
from app.lifespan import lifespan
from app.middleware import ContentSizeLimitMiddleware, SecurityHeadersMiddleware
from app.routes import register_routes
from utils.logger import setup_logger

logger = setup_logger(__name__)
settings = Settings()

app = FastAPI(
    title="4S1T Agent AI",
    description="An AI Agent system for IT Business Analysts and Data Analysts",
    version="1.2.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add content size limit (outermost — enforced before any other middleware or handler)
app.add_middleware(ContentSizeLimitMiddleware)

# Register routers, static files, and utility endpoints
register_routes(app)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )

import os
import psycopg2
from psycopg2.extras import RealDictCursor
import logging

def get_db_connection():
    """
    Establishes a connection to the PostgreSQL database using environment variables.
    It first tries to use a consolidated DB URL, then falls back to individual components.
    """
    logger = logging.getLogger(__name__)
    db_url = os.getenv("DATABASE_URL")

    try:
        if db_url:
            conn = psycopg2.connect(dsn=db_url, cursor_factory=RealDictCursor)
            logger.info("DB: Successfully connected to PostgreSQL using DATABASE_URL.")
            return conn
        else:
            logger.warning("DB: DATABASE_URL not set. Falling back to individual PostgreSQL components.")
            host = os.getenv("POSTGRES_HOST")
            user = os.getenv("POSTGRES_USER")
            password = os.getenv("POSTGRES_PASSWORD")
            dbname = os.getenv("POSTGRES_DB")
            port = os.getenv("POSTGRES_PORT", "5432")

            if not all([host, user, password, dbname]):
                logger.error("DB: Individual PostgreSQL connection variables not fully configured.")
                raise ConnectionError("DB: PostgreSQL environment variables not fully configured.")

            conn = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                dbname=dbname,
                cursor_factory=RealDictCursor
            )
            logger.info("DB: Successfully connected to PostgreSQL using individual components.")
            return conn
    except psycopg2.Error as e:
        logger.error(f"DB: Error connecting to PostgreSQL: {e}", exc_info=True)
        raise ConnectionError(f"DB: PostgreSQL connection failed: {e}") from e

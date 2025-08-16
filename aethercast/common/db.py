import os
import psycopg2
from psycopg2 import pool as psycopg2_pool
from psycopg2.extras import RealDictCursor
import logging

db_connection_pool = None
logger = logging.getLogger(__name__)

def init_db_connection_pool(service_name="common-db"):
    """Initializes the database connection pool."""
    global db_connection_pool
    if db_connection_pool is None:
        try:
            db_connection_pool = psycopg2_pool.SimpleConnectionPool(
                minconn=1,
                maxconn=int(os.getenv("DB_POOL_MAX_CONNECTIONS", 5)),
                user=os.getenv("POSTGRES_USER"),
                password=os.getenv("POSTGRES_PASSWORD"),
                host=os.getenv("POSTGRES_HOST"),
                port=os.getenv("POSTGRES_PORT", "5432"),
                database=os.getenv("POSTGRES_DB")
            )
            logger.info(f"Database connection pool created successfully for {service_name}.")
        except (Exception, psycopg2.Error) as error:
            logger.error(f"Error while creating PostgreSQL connection pool for {service_name}: {error}", exc_info=True)
            raise

def get_db_connection(service_name="common-db"):
    """Establishes and returns a database connection from the pool."""
    global db_connection_pool
    if db_connection_pool is None:
        init_db_connection_pool(service_name)
    try:
        return db_connection_pool.getconn()
    except Exception as error:
        logger.error(f"Error getting connection from pool for {service_name}: {error}", exc_info=True)
        raise

def release_db_connection(conn, service_name="common-db"):
    """Releases a database connection back to the pool."""
    global db_connection_pool
    if db_connection_pool and conn:
        db_connection_pool.putconn(conn)
        logger.debug(f"Database connection released for {service_name}.")

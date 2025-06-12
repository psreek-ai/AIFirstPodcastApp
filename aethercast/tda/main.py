import flask
import uuid
import random
import logging
import json
import os
from dotenv import load_dotenv
import requests
from celery import Celery
from celery.result import AsyncResult
import psycopg2 # Added
from psycopg2.extras import RealDictCursor # Added
from datetime import datetime
import time # Added for metric logging

# Load environment variables from .env file
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

# --- Celery Configuration ---
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

celery_app = Celery(
    'tda_tasks',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND
)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

# --- Logging Setup ---
# Custom filter to add service_name to log records
class ServiceNameFilter(logging.Filter):
    def __init__(self, service_name="tda"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record):
        record.service_name = self.service_name
        return True

# Initialize Flask app early so app.logger can be configured
app = flask.Flask(__name__)

# Configure JSON logging for the Flask app
def setup_json_logging(flask_app):
    # Clear existing default handlers from Flask app's logger
    flask_app.logger.handlers.clear()

    logHandler = logging.StreamHandler()

    # Add the service_name filter to the handler
    service_filter = ServiceNameFilter("tda") # Service name for TDA
    logHandler.addFilter(service_filter)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(service_name)s %(module)s %(funcName)s %(lineno)d %(message)s"
    )
    logHandler.setFormatter(formatter)

    flask_app.logger.addHandler(logHandler)
    flask_app.logger.setLevel(logging.INFO)
    flask_app.logger.info("Standard logging configured for TDA service.")

setup_json_logging(app)


# --- Application Configuration ---
tda_config = {
    "TDA_NEWS_API_KEY": os.getenv("TDA_NEWS_API_KEY"),
    "TDA_NEWS_API_BASE_URL": os.getenv("TDA_NEWS_API_BASE_URL", "https://newsapi.org/v2/"),
    "TDA_NEWS_API_ENDPOINT": os.getenv("TDA_NEWS_API_ENDPOINT", "everything"),
    "TDA_NEWS_DEFAULT_KEYWORDS": os.getenv("TDA_NEWS_DEFAULT_KEYWORDS", "AI,technology,science").split(','),
    "TDA_NEWS_DEFAULT_LANGUAGE": os.getenv("TDA_NEWS_DEFAULT_LANGUAGE", "en"),
    "USE_REAL_NEWS_API": os.getenv("USE_REAL_NEWS_API", "False").lower() == "true",
    "TDA_NEWS_PAGE_SIZE": int(os.getenv("TDA_NEWS_PAGE_SIZE", "25")),
    "TDA_NEWS_REQUEST_TIMEOUT": int(os.getenv("TDA_NEWS_REQUEST_TIMEOUT", "15")),
    "TDA_NEWS_USER_AGENT": os.getenv("TDA_NEWS_USER_AGENT", "AethercastTopicDiscovery/0.1"),

    # Database Configuration
    # "DATABASE_TYPE": os.getenv("DATABASE_TYPE", "sqlite"), # Removed, TDA now uses PostgreSQL only
    # "SHARED_DATABASE_PATH": os.getenv("SHARED_DATABASE_PATH", "/app/database/aethercast_podcasts.db"), # Removed
    "POSTGRES_HOST": os.getenv("POSTGRES_HOST"),
    "POSTGRES_PORT": os.getenv("POSTGRES_PORT", "5432"),
    "POSTGRES_USER": os.getenv("POSTGRES_USER"),
    "POSTGRES_PASSWORD": os.getenv("POSTGRES_PASSWORD"),
    "POSTGRES_DB": os.getenv("POSTGRES_DB"),

    "TDA_HOST": os.getenv("TDA_HOST", os.getenv("FLASK_RUN_HOST", "0.0.0.0")),
    "TDA_PORT": int(os.getenv("TDA_PORT", os.getenv("FLASK_RUN_PORT", "5000"))),
    "TDA_DEBUG_MODE": os.getenv("TDA_DEBUG_MODE", "True").lower() == "true"
}


# --- Configuration & Logging ---
# Old basicConfig removed, app.logger is now used.

# Log loaded configuration using app.logger
app.logger.info("--- TDA Configuration ---")
for key, value in tda_config.items():
    if "API_KEY" in key and value:
        app.logger.info(f"  {key}: {'*' * (len(value) - 4) + value[-4:] if len(value) > 4 else '****'}")
    elif "PASSWORD" in key and value:
        app.logger.info(f"  {key}: ********")
    else:
        app.logger.info(f"  {key}: {value}")
app.logger.info("--- End TDA Configuration ---")

# Startup Check for API Key
if tda_config["USE_REAL_NEWS_API"] and not tda_config["TDA_NEWS_API_KEY"]:
    error_message = "CRITICAL: USE_REAL_NEWS_API is True, but TDA_NEWS_API_KEY is not set. Real News API calls will fail. Please set TDA_NEWS_API_KEY."
    app.logger.critical(error_message) # Use critical for startup failures
    raise ValueError(error_message)

# Startup check for DB config (PostgreSQL only)
required_pg_vars = ["POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]
missing_pg_vars = [var for var in required_pg_vars if not tda_config.get(var)]
if missing_pg_vars:
    error_msg = f"CRITICAL: Required PostgreSQL config is missing: {', '.join(missing_pg_vars)}"
    app.logger.critical(error_msg)
    raise ValueError(error_msg)

# --- Constants ---
DB_SCHEMA_TDA_TABLES = """
CREATE TABLE IF NOT EXISTS topics_snippets (
    id UUID PRIMARY KEY,
    type VARCHAR(50) NOT NULL CHECK(type IN ('topic', 'snippet')),
    title TEXT NOT NULL,
    summary TEXT,
    keywords JSONB,
    source_url TEXT,
    source_name TEXT,
    original_topic_details JSONB,
    llm_model_used_for_snippet VARCHAR(255),
    cover_art_prompt TEXT,
    image_url TEXT,
    generation_timestamp TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_accessed_timestamp TIMESTAMPTZ,
    relevance_score REAL
);
"""
DB_TYPE_TOPIC = "topic"
SOURCE_FEED_NEWS_API = "news_api_org"
ENDPOINT_ERROR_INTERNAL_SERVER_TDA = "INTERNAL_SERVER_ERROR_TDA"
NEWS_API_STATUS_OK = "ok"

# Flask app is initialized earlier now for logging setup

# --- Database Helper Functions ---
def _get_db_connection():
    """Establishes a PostgreSQL database connection."""
    try:
        conn = psycopg2.connect(
            host=tda_config["POSTGRES_HOST"],
            port=tda_config["POSTGRES_PORT"],
            user=tda_config["POSTGRES_USER"],
            password=tda_config["POSTGRES_PASSWORD"],
            dbname=tda_config["POSTGRES_DB"],
            cursor_factory=RealDictCursor
        )
        return conn
    except psycopg2.Error as e:
        app.logger.error(f"Error connecting to PostgreSQL database: {e}")
        raise

def init_tda_db():
    """Initializes the TDA database table if it doesn't exist (PostgreSQL only)."""
    app.logger.info("[TDA_DB_INIT] Ensuring TDA database schema exists (PostgreSQL)...")
    conn = None
    cursor = None
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        # Check if table exists for PostgreSQL
        cursor.execute("""
            SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                AND table_name = 'topics_snippets'
            );
        """)
        table_exists = cursor.fetchone()['exists']
        if not table_exists:
            app.logger.info("Table 'topics_snippets' not found in PostgreSQL. Creating now...")
            cursor.execute(DB_SCHEMA_TDA_TABLES)
            conn.commit()
            app.logger.info("[TDA_DB_INIT] PostgreSQL: Table 'topics_snippets' created.")
        else:
            app.logger.info("[TDA_DB_INIT] PostgreSQL: Table 'topics_snippets' already exists.")

    except psycopg2.Error as e:
        app.logger.error(f"[TDA_DB_INIT] PostgreSQL error during schema initialization: {e}", exc_info=True)
    except Exception as e_unexp:
        app.logger.error(f"[TDA_DB_INIT] Unexpected PostgreSQL error during schema initialization: {e_unexp}", exc_info=True)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# --- Database Interaction for Topics ---
def _save_topic_to_db(topic_object: dict): # Removed db_path argument
    """Saves a single topic object to the configured database."""
    conn = None
    cursor = None
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()

        keywords_data = topic_object.get("keywords", [])
        keywords_to_save = json.dumps(keywords_data) if keywords_data else None # Ensure JSON string for PG JSONB

        potential_sources = topic_object.get("potential_sources", [])
        source_url = potential_sources[0].get("url") if potential_sources and len(potential_sources) > 0 else None
        source_name = potential_sources[0].get("source_name") if potential_sources and len(potential_sources) > 0 else None

        current_ts_iso = datetime.utcnow().isoformat()
        # For PostgreSQL, TIMESTAMPTZ will handle ISO format string correctly.
        # SQLite TEXT also handles ISO format string.
        publication_date_to_save = topic_object.get("publication_date", current_ts_iso)
        last_accessed_ts_to_save = current_ts_iso

        # Generate UUID if topic_id is not already a valid UUID string
        topic_id_str = topic_object.get("topic_id")
        try:
            uuid.UUID(topic_id_str) # Validate if it's a UUID
        except (ValueError, TypeError, AttributeError):
            topic_id_str = str(uuid.uuid4()) # Generate new if not valid


        sql_insert = """
            INSERT INTO topics_snippets (
                id, type, title, summary, keywords,
                source_url, source_name, original_topic_details,
                llm_model_used_for_snippet, cover_art_prompt, image_url,
                generation_timestamp, last_accessed_timestamp, relevance_score
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                type = EXCLUDED.type,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                keywords = EXCLUDED.keywords,
                source_url = EXCLUDED.source_url,
                source_name = EXCLUDED.source_name,
                original_topic_details = EXCLUDED.original_topic_details,
                llm_model_used_for_snippet = EXCLUDED.llm_model_used_for_snippet,
                cover_art_prompt = EXCLUDED.cover_art_prompt,
                image_url = EXCLUDED.image_url,
                generation_timestamp = EXCLUDED.generation_timestamp,
                last_accessed_timestamp = EXCLUDED.last_accessed_timestamp,
                relevance_score = EXCLUDED.relevance_score;
        """
        # Using %s for psycopg2, original_topic_details is expected to be JSONB compatible (dict/list)
        params = (
            topic_id_str,
            DB_TYPE_TOPIC,
            topic_object.get("title_suggestion"),
            topic_object.get("summary"),
            keywords_to_save,
            source_url,
            source_name,
            json.dumps(topic_object.get("original_topic_details")) if topic_object.get("original_topic_details") else None, # Ensure JSON string for PG JSONB
            None, # llm_model_used_for_snippet
            None, # cover_art_prompt
            topic_object.get("image_url"),
            publication_date_to_save,
            last_accessed_ts_to_save,
            topic_object.get("relevance_score")
        )
        cursor.execute(sql_insert, params)
        conn.commit()
        app.logger.info(f"Saved/Replaced topic {topic_id_str} to PostgreSQL DB: {topic_object.get('title_suggestion')}")

    except psycopg2.Error as e:
        app.logger.error(f"PostgreSQL error saving topic {topic_object.get('topic_id')}: {e}", exc_info=True)
        if conn: conn.rollback()
    except Exception as e_unexp:
        app.logger.error(f"Unexpected error saving topic {topic_object.get('topic_id')} to PostgreSQL DB: {e_unexp}", exc_info=True)
        if conn: conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def call_real_news_api(keywords: list[str] = None, categories: list[str] = None, language: str = None, country: str = None) -> list[dict]:
    """
    Calls the NewsAPI.org to fetch articles, parses them, transforms into TopicObjects,
    saves them to the database, and returns the list of TopicObjects.
    """
    if not tda_config.get("USE_REAL_NEWS_API"):
        return []
    if not tda_config.get("TDA_NEWS_API_KEY"):
        app.logger.error("call_real_news_api: TDA_NEWS_API_KEY not configured. Cannot make request.")
        return []

    base_url = tda_config["TDA_NEWS_API_BASE_URL"]
    endpoint = tda_config["TDA_NEWS_API_ENDPOINT"]
    api_url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    params = {}
    query_keywords_list = keywords if keywords else tda_config["TDA_NEWS_DEFAULT_KEYWORDS"]
    if query_keywords_list:
        if isinstance(query_keywords_list, str):
            query_keywords_list = [kw.strip() for kw in query_keywords_list.split(',')]
        params["q"] = " OR ".join(query_keywords_list)
    if endpoint == "top-headlines":
        if categories:
            params["category"] = categories[0] if isinstance(categories, list) else categories
        if country:
            params["country"] = country
    current_language = language if language else tda_config["TDA_NEWS_DEFAULT_LANGUAGE"]
    if current_language:
        params["language"] = current_language
    params["pageSize"] = tda_config.get("TDA_NEWS_PAGE_SIZE", 25)
    headers = {
        "X-Api-Key": tda_config["TDA_NEWS_API_KEY"],
        "User-Agent": tda_config.get("TDA_NEWS_USER_AGENT", "AethercastTopicDiscovery/0.1")
    }
    request_timeout = tda_config.get("TDA_NEWS_REQUEST_TIMEOUT", 15)
    app.logger.info(f"Calling NewsAPI: URL={api_url}, Params={params}, Timeout={request_timeout}s")
    topic_objects = []
    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=request_timeout)
        response.raise_for_status()
        response_json = response.json()
        if response_json.get("status") != NEWS_API_STATUS_OK:
            app.logger.error(f"NewsAPI returned error status: {response_json.get('status')}. Message: {response_json.get('message')}")
            return [] # Return empty list on API error status
        articles = response_json.get("articles", [])

        # DB saving logic is now centralized in _save_topic_to_db which uses configured DB type
        # No need for db_path here directly.
        
        for article in articles:
            title = article.get('title')
            if not title or title == "[Removed]":
                continue
            description = article.get('description')
            content = article.get('content')
            summary_text = description if description else content if content else "No summary available."
            if summary_text.endswith(" chars]") and summary_text[-10:].startswith("[+"):
                summary_text = summary_text[:summary_text.rfind("[+")].strip()
            if not summary_text: summary_text = "Content details not available."
            article_url = article.get('url')
            source_name = article.get('source', {}).get('name', 'Unknown Source')
            published_at = article.get('publishedAt')
            topic_object = {
                "topic_id": str(uuid.uuid4()), # Generate UUID for new topics
                "source_feed_name": SOURCE_FEED_NEWS_API,
                "title_suggestion": title,
                "summary": summary_text,
                "keywords": query_keywords_list, # Use the search keywords
                "potential_sources": [{"url": article_url, "title": title, "source_name": source_name}],
                "relevance_score": round(random.uniform(0.6, 0.9), 2), # Placeholder score
                "publication_date": published_at,
                "category_suggestion": "News", # Default category for news items
                "original_topic_details": article # Store the full article for potential future use
            }
            topic_objects.append(topic_object)
            _save_topic_to_db(topic_object) # Call updated save function
        
        app.logger.info(f"Transformed {len(topic_objects)} articles into TopicObjects from NewsAPI.")
        return topic_objects
    except requests.exceptions.RequestException as req_err:
        app.logger.error(f"NewsAPI request error: {req_err}", exc_info=True)
        # For Celery task, we should raise the error so it can be retried or marked as failed
        raise # Re-raise the exception
    except Exception as e_unexp: # Catch any other unexpected error
        app.logger.error(f"Unexpected error in call_real_news_api: {e_unexp}", exc_info=True)
        raise # Re-raise


@celery_app.task(bind=True, name='fetch_news_from_newsapi_task')
def fetch_news_from_newsapi_task(self, request_id_celery: str, keywords: list[str] = None, categories: list[str] = None, language: str = None, country: str = None):
    """
    Celery task to fetch articles from NewsAPI.org.
    request_id_celery is for logging correlation with the dispatching request.
    """
    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id_celery}): Fetching news. Keywords: {keywords}, Categories: {categories}")

    # The actual NewsAPI call logic is now within call_real_news_api
    # We pass the parameters to it.
    # Note: _save_topic_to_db is called inside call_real_news_api.
    # If saving to DB should also be part of the async task's success criteria, this is fine.
    # If DB save fails, call_real_news_api currently logs it and continues, returning successfully fetched articles.
    # For a Celery task, you might want more explicit error propagation if DB save is critical.
    try:
        # Pass page_size or other specific params if needed, or rely on defaults in call_real_news_api
        articles = call_real_news_api(keywords=keywords, categories=categories, language=language, country=country)
        # The 'articles' returned by call_real_news_api are already transformed TopicObjects and saved.
        return {"status": "success", "discovered_topics": articles, "message": f"Fetched {len(articles)} topics."}
    except requests.exceptions.RequestException as e_req:
        logger.error(f"Celery Task {self.request.id}: NewsAPI request error in task: {e_req}", exc_info=True)
        raise self.retry(exc=e_req, countdown=10, max_retries=2) # Celery retry for network issues
    except Exception as e_task: # Catch other exceptions from call_real_news_api or within the task
        logger.error(f"Celery Task {self.request.id}: Unexpected error: {e_task}", exc_info=True)
        raise self.retry(exc=e_task, countdown=10, max_retries=2) # Generic retry


SIMULATED_DATA_SOURCES = [
    {
        "source_name": "Tech Chronicle",
        "articles": [
            {"title": "The Future of AI in Personalized Medicine", "url": "http://example.com/ai-medicine", "keywords": ["AI", "Healthcare", "Personalized Medicine"], "publish_date": "2024-03-10T10:00:00Z"},
            {"title": "Quantum Computing: Beyond the Hype", "url": "http://example.com/quantum-beyond-hype", "keywords": ["Quantum Computing", "Technology", "Innovation"], "publish_date": "2024-03-11T11:00:00Z"},
        ]
    },
]

def identify_topics_from_sources(query: str = None, limit: int = 5) -> list:
    identified_topics = []
    all_articles = []
    app.logger.info(f"[TDA_LOGIC] Scanning simulated data sources. Query: '{query}', Limit: {limit}")
    for data_source in SIMULATED_DATA_SOURCES:
        for article in data_source["articles"]:
            all_articles.append({
                "title": article["title"], "url": article["url"],
                "source_name": data_source["source_name"], "keywords": article.get("keywords", []),
                "publish_date": article.get("publish_date", datetime.utcnow().isoformat())
            })
    for article in all_articles:
        relevance = calculate_relevance_score(article, query)
        combined_keywords = list(set(article.get("keywords", []) + ([kw.strip() for kw in query.split(',')] if query else [])))
        topic_object = {
            "topic_id": str(uuid.uuid4()), "title_suggestion": article["title"],
            "summary": generate_summary_from_title(article["title"]), "keywords": combined_keywords,
            "potential_sources": [{"url": article["url"], "title": article["title"], "source_name": article["source_name"]}],
            "relevance_score": relevance, "publication_date": article.get("publish_date"),
            "category_suggestion": "General", "original_topic_details": article
        }
        identified_topics.append(topic_object)
        _save_topic_to_db(topic_object)
    identified_topics.sort(key=lambda x: x["relevance_score"], reverse=True)
    app.logger.info(f"[TDA_LOGIC] Identified {len(identified_topics)} potential topics from simulated sources. Returning top {min(limit, len(identified_topics))}.")
    return identified_topics[:limit]

# --- Other helper functions (generate_topic_id, generate_summary_from_title, calculate_relevance_score) ---
# generate_topic_id is now just str(uuid.uuid4()) inline or within topic creation.
# generate_summary_from_title and calculate_relevance_score are specific to simulated data.

def generate_summary_from_title(title: str) -> str:
    return f"This topic explores {title.lower()}, focusing on its recent developments and potential impact."

def calculate_relevance_score(article: dict, query: str = None) -> float:
    score = random.uniform(0.5, 0.9)
    if query:
        query_keywords = [q.strip().lower() for q in query.split()]
        for qk in query_keywords:
            if qk in [kw.lower() for kw in article.get("keywords", [])]: score = min(1.0, score + 0.2)
            if qk in article.get("title", "").lower(): score = min(1.0, score + 0.1)
    return round(score, 2)

@celery_app.task(bind=True, name='discover_topics_task')
def discover_topics_task(self, request_id_main: str, query: Optional[str], limit: int, use_real_news_api: bool, error_trigger: Optional[str] = None):
    """
    Celery task to discover topics, potentially calling another Celery task for NewsAPI.
    """
    logger.info(f"Celery Task {self.request.id} (Orig Req ID: {request_id_main}): Starting topic discovery. Query: '{query}', Limit: {limit}, UseNewsAPI: {use_real_news_api}")

    if error_trigger == "tda_error": # For testing celery task failure
        logger.info(f"Celery Task {self.request.id}: Simulated TDA error triggered in task.")
        raise Exception("Simulated TDA error in Celery task.")

    discovered_topics = []
    if use_real_news_api:
        request_keywords = [k.strip() for k in query.split(',')] if query else None
        logger.info(f"Celery Task {self.request.id}: Dispatching NewsAPI sub-task. Keywords: {request_keywords}")

        news_task = fetch_news_from_newsapi_task.delay(
            request_id_celery=self.request.id, # Correlate sub-task with this parent task
            keywords=request_keywords,
            language=tda_config.get("TDA_NEWS_DEFAULT_LANGUAGE"),
            max_results=limit
        )
        logger.info(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} dispatched. Polling for results...")

        # Polling logic for the NewsAPI sub-task
        # This worker will block here until the sub-task completes or polling times out.
        # A more advanced pattern would use Celery chains/callbacks to avoid worker blocking.
        polling_start_time = time.time()
        # Use specific polling config or reuse general ones if suitable
        news_polling_interval = int(os.getenv("TDA_NEWSAPI_POLLING_INTERVAL_SECONDS", "5"))
        news_polling_timeout = int(os.getenv("TDA_NEWSAPI_POLLING_TIMEOUT_SECONDS", "120")) # E.g., 2 minutes for news API call

        while True:
            if time.time() - polling_start_time > news_polling_timeout:
                logger.error(f"Celery Task {self.request.id}: Polling NewsAPI sub-task {news_task.id} timed out.")
                raise Exception(f"Polling NewsAPI sub-task {news_task.id} timed out.")

            news_task_result = AsyncResult(news_task.id, app=celery_app)
            logger.info(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} status: {news_task_result.status}")

            if news_task_result.successful():
                sub_task_output = news_task_result.result
                if sub_task_output.get("status") == "success":
                    discovered_topics = sub_task_output.get("discovered_topics", [])
                    logger.info(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} successful. Found {len(discovered_topics)} topics.")
                else: # NewsAPI task itself reported an issue in its result
                    logger.error(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} reported failure: {sub_task_output.get('message')}")
                    raise Exception(f"NewsAPI sub-task failed: {sub_task_output.get('message', 'Unknown error from NewsAPI task')}")
                break
            elif news_task_result.failed():
                logger.error(f"Celery Task {self.request.id}: NewsAPI sub-task {news_task.id} failed with Celery status FAILED. Info: {news_task_result.info}")
                raise Exception(f"NewsAPI sub-task failed: {str(news_task_result.info)}")

            time.sleep(news_polling_interval)
    else:
        # Simulated data path
        logger.info(f"Celery Task {self.request.id}: Using simulated data sources. Query: '{query}', Limit: {limit}")
        discovered_topics = identify_topics_from_sources(query=query, limit=limit)
        logger.info(f"Celery Task {self.request.id}: Simulated data discovery found {len(discovered_topics)} topics.")

    # Return the final list of topics (or an error structure if preferred)
    if not discovered_topics:
        return {"status": "success_no_topics", "message": "No topics discovered.", "topics": []}

    return {"status": "success", "discovered_topics": discovered_topics, "message": f"Successfully discovered {len(discovered_topics)} topics."}


# --- API Endpoint ---
@app.route("/discover_topics", methods=["POST"])
def discover_topics_async_endpoint(): # Renamed endpoint function
    request_start_time = time.time() # For overall endpoint response time, not full task time
    request_id_main = f"tda_req_{uuid.uuid4().hex[:8]}"
    logger.info(f"Request {request_id_main}: Received async /discover_topics request.")

    try:
        request_data = flask.request.get_json() if flask.request.content_length else {}
    except Exception as e_json_decode:
        logger.warning(f"Request {request_id_main}: Failed to decode JSON: {e_json_decode}", exc_info=True)
        return flask.jsonify({"error_code": "TDA_MALFORMED_JSON", "message": "Malformed JSON."}), 400

    query = request_data.get("query")
    limit_raw = request_data.get("limit")
    error_trigger = request_data.get("error_trigger") # For testing endpoint itself

    if query is not None and (not isinstance(query, str) or not query.strip()):
        return flask.jsonify({"error_code": "TDA_INVALID_QUERY", "message": "query must be non-empty string."}), 400

    limit = 5
    if limit_raw is not None:
        try:
            limit = int(limit_raw)
            if not (1 <= limit <= 50):
                return flask.jsonify({"error_code": "TDA_INVALID_LIMIT_RANGE", "message": "limit must be 1-50."}), 400
        except ValueError:
            return flask.jsonify({"error_code": "TDA_INVALID_LIMIT_TYPE", "message": "limit must be integer."}), 400

    if error_trigger == "tda_endpoint_error": # Test endpoint error before dispatch
        logger.info(f"Request {request_id_main}: Simulated TDA endpoint error triggered.")
        return flask.jsonify({"error_code": "TDA_SIMULATED_ENDPOINT_ERROR", "message": "Simulated TDA endpoint error."}), 500

    use_real_news_api_flag = tda_config["USE_REAL_NEWS_API"]

    logger.info(f"Request {request_id_main}: Dispatching topic discovery to Celery task. Query: '{query}', Limit: {limit}, UseNewsAPI: {use_real_news_api_flag}")

    task = discover_topics_task.delay(
        request_id_main=request_id_main,
        query=query,
        limit=limit,
        use_real_news_api_flag=use_real_news_api_flag,
        error_trigger=error_trigger # Pass error_trigger for testing task failure if needed
    )

    logger.info(f"Request {request_id_main}: Topic discovery task {task.id} dispatched.")
    return flask.jsonify({
        "task_id": task.id,
        "status_url": f"/v1/tasks/{task.id}",
        "message": "Topic discovery task initiated. Poll task ID for results."
    }), 202


@app.route('/v1/tasks/<task_id>', methods=['GET'])
def get_tda_task_status(task_id: str):
    logger.info(f"Received request for TDA task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}

    if task_result.successful():
        task_output = task_result.result # This is the dict from fetch_news_from_newsapi_task
        response_data["result"] = task_output
        http_status = 200
        # Example: check if the task's internal logic indicated an issue
        if isinstance(task_output, dict) and task_output.get("status") != "success":
             # Could map specific task error messages to HTTP status codes if desired
             http_status = 500 # Default for task-level errors reported in a successful Celery execution
        return flask.jsonify(response_data), http_status
    elif task_result.failed():
        error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
        response_data["result"] = error_info
        return flask.jsonify(response_data), 500
    else: # PENDING, STARTED, RETRY
        return flask.jsonify(response_data), 202

if __name__ == "__main__":
    if tda_config.get("DATABASE_TYPE") == "sqlite" and not tda_config.get("SHARED_DATABASE_PATH"):
        app.logger.warning("SHARED_DATABASE_PATH not configured for TDA SQLite mode. Topic saving to DB will fail if not using Postgres.")
    elif tda_config.get("DATABASE_TYPE") == "postgres" and not all(tda_config.get(k) for k in ["POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]):
        app.logger.warning("PostgreSQL is configured as DATABASE_TYPE, but one or more connection variables are missing. DB operations might fail.")

    init_tda_db() # Call init_db based on configured DB_TYPE

    host = tda_config.get("TDA_HOST")
    port = tda_config.get("TDA_PORT")
    debug_mode = tda_config.get("TDA_DEBUG_MODE")
    # The initial "JSON logging configured..." message is now part of setup_json_logging
    app.logger.info(f"--- TDA Service starting on {host}:{port} (Debug: {debug_mode}, DB: PostgreSQL) ---")
    app.run(host=host, port=port, debug=debug_mode)

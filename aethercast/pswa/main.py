# --- Database Helper Functions for Script Caching & Idempotency ---

def _get_db_connection_script_cache(): # Renamed for clarity
    db_type = pswa_config.get("DATABASE_TYPE")
    if db_type == "postgres":
        if not PSYCOPG2_AVAILABLE:
            logger.error("PostgreSQL selected for cache, but psycopg2 is not available.")
            return None
        try:
            conn = psycopg2.connect(
                host=pswa_config["POSTGRES_HOST"], port=pswa_config["POSTGRES_PORT"],
                user=pswa_config["POSTGRES_USER"], password=pswa_config["POSTGRES_PASSWORD"],
                dbname=pswa_config["POSTGRES_DB"], cursor_factory=RealDictCursor
            )
            logger.info("[PSWA_CACHE_DB] Connected to PostgreSQL for cache.")
            return conn
        except psycopg2.Error as e:
            logger.error(f"[PSWA_CACHE_DB] Error connecting to PostgreSQL for cache: {e}", exc_info=True)
            raise # Re-raise to be handled by caller
    elif db_type == "sqlite":
        db_path = pswa_config['SHARED_DATABASE_PATH']
        os.makedirs(os.path.dirname(db_path), exist_ok=True) # Ensure directory exists
        try:
            conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES) # type: ignore
            conn.row_factory = sqlite3.Row # type: ignore
            logger.info(f"[PSWA_CACHE_DB] Connected to SQLite for cache at {db_path}.")
            return conn
        except sqlite3.Error as e: # type: ignore
            logger.error(f"[PSWA_CACHE_DB] Error connecting to SQLite for cache at {db_path}: {e}", exc_info=True)
            raise # Re-raise
    else:
        raise ValueError(f"Unsupported DATABASE_TYPE for cache: {db_type}")

def _get_pswa_db_connection_idempotency(): # New function for Idempotency (always PostgreSQL)
    """Establishes a direct connection to PostgreSQL for PSWA idempotency checks."""
    if not PSYCOPG2_AVAILABLE:
        logger.error("PSWA: psycopg2 is not available for PostgreSQL idempotency connection.")
        raise ConnectionError("PSWA: psycopg2 not available for idempotency DB.")

    required_vars = [pswa_config.get('POSTGRES_HOST'), pswa_config.get('POSTGRES_USER'), pswa_config.get('POSTGRES_PASSWORD'), pswa_config.get('POSTGRES_DB')]
    if not all(required_vars):
        logger.error("PSWA: PostgreSQL connection variables for idempotency not fully set in pswa_config.")
        raise ConnectionError("PSWA: PostgreSQL environment variables for idempotency not configured.")
    try:
        conn = psycopg2.connect(
            host=pswa_config['POSTGRES_HOST'], port=pswa_config.get('POSTGRES_PORT', '5432'),
            user=pswa_config['POSTGRES_USER'], password=pswa_config['POSTGRES_PASSWORD'],
            dbname=pswa_config['POSTGRES_DB'],
            cursor_factory=RealDictCursor
        )
        logger.info("PSWA successfully connected to PostgreSQL for idempotency.")
        return conn
    except psycopg2.Error as e:
        logger.error(f"PSWA: Unable to connect to PostgreSQL for idempotency: {e}", exc_info=True)
        raise ConnectionError(f"PSWA: PostgreSQL connection for idempotency failed: {e}") from e

def init_pswa_db():
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        logger.info("[PSWA_DB_INIT] Script caching is disabled. Skipping DB initialization for cache.")
        return
    db_type = pswa_config.get("DATABASE_TYPE")
    logger.info(f"[PSWA_DB_INIT] Ensuring PSWA cache schema exists (DB Type: {db_type})...")
    conn = None; cursor = None
    try:
        conn = _get_db_connection_script_cache()
        if not conn:
            logger.error(f"[PSWA_DB_INIT] Could not get DB connection for script cache of type {db_type}. Skipping schema init.")
            return

        cursor = conn.cursor()
        if db_type == "postgres":
            cursor.execute(DB_SCHEMA_PSWA_CACHE_TABLE) # Schema is idempotent
            conn.commit()
            logger.info("[PSWA_DB_INIT] PostgreSQL: Table 'generated_scripts' and index ensured for cache.")
        elif db_type == "sqlite":
            cursor.executescript(DB_SCHEMA_PSWA_CACHE_TABLE)
            conn.commit()
            logger.info("[PSWA_DB_INIT] SQLite: Table 'generated_scripts' and index ensured for cache.")
    except (psycopg2.Error, sqlite3.Error) as e: # type: ignore
        logger.error(f"[PSWA_DB_INIT] Database error during cache schema init: {e}", exc_info=True)
        if conn and db_type == "postgres": conn.rollback()
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# --- Idempotency DB Helpers (PSWA specific) ---
def _check_pswa_idempotency_key(db_conn, idempotency_key: str, task_name: str) -> Optional[Dict[str, Any]]:
    log_extra = {"task_id": "PSWAIdempotencyCheck", "idempotency_key": idempotency_key, "check_task_name": task_name}
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT idempotency_key, task_name, workflow_id, created_at, locked_at, status, result_payload, error_payload FROM idempotency_keys WHERE idempotency_key = %s AND task_name = %s",
                (idempotency_key, task_name)
            )
            record = cur.fetchone()
            if record:
                logger.info(f"Idempotency key found with status '{record['status']}'.", extra=log_extra)
                return dict(record)
            logger.info("No existing idempotency key found.", extra=log_extra)
            return None
    except psycopg2.Error as e:
        logger.error(f"DB error checking idempotency key: {e}", exc_info=True, extra=log_extra)
        raise
    except Exception as e_unexp:
        logger.error(f"Unexpected error checking idempotency key: {e_unexp}", exc_info=True, extra=log_extra)
        raise

def _store_pswa_idempotency_result(db_conn, idempotency_key: str, task_name: str, status: str, result_payload: Optional[dict] = None, error_payload: Optional[dict] = None, workflow_id: Optional[str] = None, is_new_key: bool = True):
    log_extra = {"task_id": "PSWAIdempotencyStore", "idempotency_key": idempotency_key, "store_task_name": task_name, "new_status": status, "workflow_id": workflow_id or "N/A"}
    try:
        with db_conn.cursor() as cur:
            current_ts_utc = datetime.now(timezone.utc)
            if is_new_key:
                logger.info("Storing new idempotency key.", extra=log_extra)
                cur.execute(
                    """
                    INSERT INTO idempotency_keys (idempotency_key, task_name, workflow_id, locked_at, status, result_payload, error_payload, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        task_name = EXCLUDED.task_name, workflow_id = EXCLUDED.workflow_id,
                        locked_at = EXCLUDED.locked_at, status = EXCLUDED.status,
                        result_payload = EXCLUDED.result_payload, error_payload = EXCLUDED.error_payload,
                        created_at = idempotency_keys.created_at;
                    """,
                    (idempotency_key, task_name, workflow_id,
                     current_ts_utc if status == pswa_config['IDEMPOTENCY_STATUS_PROCESSING'] else None,
                     status, json.dumps(result_payload) if result_payload else None,
                     json.dumps(error_payload) if error_payload else None, current_ts_utc)
                )
            else:
                logger.info("Updating existing idempotency key.", extra=log_extra)
                set_clauses = ["status = %s", "result_payload = %s", "error_payload = %s"]
                params = [status, json.dumps(result_payload) if result_payload else None, json.dumps(error_payload) if error_payload else None]

                if status == pswa_config['IDEMPOTENCY_STATUS_PROCESSING']:
                    set_clauses.append("locked_at = %s")
                    params.append(current_ts_utc)
                elif status in [pswa_config['IDEMPOTENCY_STATUS_COMPLETED'], pswa_config['IDEMPOTENCY_STATUS_FAILED']]:
                    set_clauses.append("locked_at = NULL")

                params.extend([idempotency_key, task_name])
                cur.execute(
                    f"UPDATE idempotency_keys SET {', '.join(set_clauses)} WHERE idempotency_key = %s AND task_name = %s;",
                    tuple(params)
                )
            logger.info("Successfully stored/updated PSWA idempotency key.", extra=log_extra)
    except psycopg2.Error as e:
        logger.error(f"DB error storing PSWA idempotency key: {e}", exc_info=True, extra=log_extra)
        raise
    except Exception as e_unexp:
        logger.error(f"Unexpected error storing PSWA idempotency key: {e_unexp}", exc_info=True, extra=log_extra)
        raise

def _calculate_content_hash(topic: str, content: str) -> str:
    # (Function remains the same)
    normalized_topic = topic.lower().strip()
    normalized_content_summary = content.lower().strip()[:1000]
    input_string = f"topic:{normalized_topic}|content_summary:{normalized_content_summary}"
    return hashlib.sha256(input_string.encode('utf-8')).hexdigest()

def _get_cached_script(topic_hash: str, max_age_hours: int) -> Optional[Dict[str, Any]]:
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'): return None
    logger.info(f"[PSWA_CACHE_DB] Fetching script from cache for hash: {topic_hash}")
    conn = None; cursor = None
    try:
        conn = _get_db_connection_script_cache() # Use specific cache connection getter
        if not conn: return None # Could not connect
        cursor = conn.cursor()
        cutoff_timestamp = (datetime.utcnow() - timedelta(hours=max_age_hours))

        sql_query = """
            SELECT script_id, structured_script_json, llm_model_used, generation_timestamp
            FROM generated_scripts
            WHERE topic_hash = %s AND generation_timestamp >= %s;
        """
        params = (topic_hash, cutoff_timestamp)

        if pswa_config.get("DATABASE_TYPE") == "sqlite":
            sql_query = sql_query.replace("%s", "?")
            params = (topic_hash, cutoff_timestamp.isoformat())

        cursor.execute(sql_query, params)
        row = cursor.fetchone()

        if row:
            logger.info(f"[PSWA_CACHE_DB] Cache hit for hash {topic_hash}. Script ID: {row['script_id']}")
            structured_script = row['structured_script_json'] if isinstance(row['structured_script_json'], dict) else json.loads(row['structured_script_json'])

            update_access_sql = "UPDATE generated_scripts SET last_accessed_timestamp = %s WHERE script_id = %s;"
            update_params = (datetime.utcnow(), row['script_id'])
            if pswa_config.get("DATABASE_TYPE") == "sqlite":
                update_access_sql = update_access_sql.replace("%s", "?")
                update_params = (datetime.utcnow().isoformat(), row['script_id'])

            update_cursor = conn.cursor()
            update_cursor.execute(update_access_sql, update_params)
            conn.commit()
            update_cursor.close()

            structured_script['source'] = "cache"
            if 'script_id' not in structured_script: structured_script['script_id'] = row['script_id']
            if 'llm_model_used' not in structured_script: structured_script['llm_model_used'] = row['llm_model_used']
            structured_script['generation_timestamp_from_cache'] = row['generation_timestamp'].isoformat() if isinstance(row['generation_timestamp'], datetime) else str(row['generation_timestamp'])
            return structured_script
        else:
            logger.info(f"[PSWA_CACHE_DB] Cache miss or stale for hash {topic_hash}")
            return None
    except (psycopg2.Error, sqlite3.Error, json.JSONDecodeError) as e: # type: ignore
        logger.error(f"[PSWA_CACHE_DB] Error accessing/decoding cache for {topic_hash}: {e}", exc_info=True)
        if conn and pswa_config.get("DATABASE_TYPE") == "postgres": conn.rollback()
        return None
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

def _save_script_to_cache(script_id: str, topic_hash: str, structured_script: Dict[str, Any], llm_model_used: str):
    if not pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'): return
    logger.info(f"[PSWA_CACHE_DB] Saving script {script_id} to cache with hash: {topic_hash}")
    conn = None; cursor = None
    try:
        conn = _get_db_connection_script_cache() # Use specific cache connection getter
        if not conn: return # Could not connect
        cursor = conn.cursor()

        script_to_save_db = structured_script.copy()
        script_to_save_db.pop('source', None)
        script_to_save_db.pop('generation_timestamp_from_cache', None)

        script_json_for_db = script_to_save_db if pswa_config.get("DATABASE_TYPE") == "postgres" else json.dumps(script_to_save_db)
        current_ts = datetime.utcnow()

        sql_insert = """
            INSERT INTO generated_scripts
                (script_id, topic_hash, structured_script_json, generation_timestamp, llm_model_used, last_accessed_timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (topic_hash) DO UPDATE SET
                script_id = EXCLUDED.script_id,
                structured_script_json = EXCLUDED.structured_script_json,
                generation_timestamp = EXCLUDED.generation_timestamp,
                llm_model_used = EXCLUDED.llm_model_used,
                last_accessed_timestamp = EXCLUDED.last_accessed_timestamp;
        """
        params = (script_id, topic_hash, script_json_for_db, current_ts, llm_model_used, current_ts)

        if pswa_config.get("DATABASE_TYPE") == "sqlite":
            sql_insert = """
                INSERT OR REPLACE INTO generated_scripts
                    (script_id, topic_hash, structured_script_json, generation_timestamp, llm_model_used, last_accessed_timestamp)
                VALUES (?, ?, ?, ?, ?, ?);
            """
            params = (script_id, topic_hash, json.dumps(script_json_for_db) if isinstance(script_json_for_db, dict) else script_json_for_db, current_ts.isoformat(), llm_model_used, current_ts.isoformat())

        cursor.execute(sql_insert, params)
        conn.commit()
        logger.info(f"[PSWA_CACHE_DB] Successfully saved script {script_id} to cache.")
    except (psycopg2.Error, sqlite3.Error, json.JSONEncodeError) as e: # type: ignore
        logger.error(f"[PSWA_CACHE_DB] Error saving script {script_id} to cache: {e}", exc_info=True)
        if conn and pswa_config.get("DATABASE_TYPE") == "postgres": conn.rollback()
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# --- LLM Output Parsing (parse_llm_script_output - remains the same) ---
# This function is defined after the DB helpers in the original file.
# Ensure its position is maintained relative to other code blocks not being overwritten.
# For this overwrite, we will include it to ensure correct placement.
def parse_llm_script_output(raw_script_text: str, topic: str) -> dict:
    # (Function content remains the same)
    script_id = f"pswa_script_{uuid.uuid4().hex}"
    parsed_script = {
        "script_id": script_id, "topic": topic, "title": f"Podcast on {topic}",
        "full_raw_script": raw_script_text, "segments": [],
        "llm_model_used": pswa_config.get('PSWA_LLM_MODEL', "gpt-3.5-turbo")
    }
    try:
        llm_json_data = json.loads(raw_script_text)
        logger.info(f"[PSWA_PARSING] Successfully parsed LLM output as JSON for topic '{topic}'.")
        if KEY_ERROR in llm_json_data and llm_json_data[KEY_ERROR] == "Insufficient content":
            logger.warning(f"[PSWA_PARSING] LLM returned 'Insufficient content' error in JSON for topic '{topic}'.")
            parsed_script[KEY_TITLE] = llm_json_data.get(KEY_MESSAGE, f"Error: Insufficient Content for {topic}")
            parsed_script[KEY_SEGMENTS] = [{KEY_SEGMENT_TITLE: SEGMENT_TITLE_ERROR, KEY_CONTENT: llm_json_data.get(KEY_MESSAGE, raw_script_text)}]
            return parsed_script
        parsed_script[KEY_TITLE] = llm_json_data.get(KEY_TITLE, f"Podcast on {topic}")
        intro_content = llm_json_data.get(KEY_INTRO)
        if intro_content is not None: parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: SEGMENT_TITLE_INTRO, KEY_CONTENT: str(intro_content)})
        else: logger.warning(f"[PSWA_PARSING] JSON from LLM missing '{KEY_INTRO}' for topic '{topic}'.")
        llm_segments = llm_json_data.get(KEY_SEGMENTS, [])
        if isinstance(llm_segments, list):
            for seg in llm_segments:
                if isinstance(seg, dict) and KEY_SEGMENT_TITLE in seg and KEY_CONTENT in seg:
                    parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: str(seg[KEY_SEGMENT_TITLE]), KEY_CONTENT: str(seg[KEY_CONTENT])})
                else: logger.warning(f"[PSWA_PARSING] Invalid segment structure in JSON from LLM for topic '{topic}': {seg}")
        else: logger.warning(f"[PSWA_PARSING] JSON from LLM '{KEY_SEGMENTS}' is not a list for topic '{topic}'.")
        outro_content = llm_json_data.get(KEY_OUTRO)
        if outro_content is not None: parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: SEGMENT_TITLE_OUTRO, KEY_CONTENT: str(outro_content)})
        else: logger.warning(f"[PSWA_PARSING] JSON from LLM missing '{KEY_OUTRO}' for topic '{topic}'.")
        if not parsed_script[KEY_SEGMENTS]: logger.warning(f"[PSWA_PARSING] No valid segments found in JSON for topic '{topic}'.")
        return parsed_script
    except json.JSONDecodeError:
        logger.warning(f"[PSWA_PARSING] LLM output was not valid JSON for topic '{topic}'. Raw output: '{raw_script_text[:200]}...' Attempting fallback.")
        parsed_script[KEY_TITLE] = f"Podcast on {topic}"; parsed_script[KEY_SEGMENTS] = []
    if raw_script_text.startswith("[ERROR] Insufficient content"):
        logger.warning(f"[PSWA_PARSING_FALLBACK] LLM indicated insufficient content for topic '{topic}'.")
        parsed_script[KEY_TITLE] = f"Error: Insufficient Content for {topic}"
        parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: SEGMENT_TITLE_ERROR, KEY_CONTENT: raw_script_text})
        return parsed_script
    title_match = re.search(r"\[TITLE\](.*?)\n", raw_script_text, re.IGNORECASE)
    if title_match: parsed_script[KEY_TITLE] = title_match.group(1).strip()
    lines = raw_script_text.splitlines(); current_tag_content = []; active_tag = None
    for line in lines:
        line = line.strip(); match = re.fullmatch(r"\[([A-Z0-9_]+)\]", line, re.IGNORECASE)
        if match:
            if active_tag and current_tag_content:
                if active_tag.upper() == TAG_TITLE and parsed_script[KEY_TITLE] == f"Podcast on {topic}": parsed_script[KEY_TITLE] = "\n".join(current_tag_content).strip()
                else: parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: active_tag, KEY_CONTENT: "\n".join(current_tag_content).strip()})
            active_tag = match.group(1).upper(); current_tag_content = []
            if active_tag == TAG_TITLE and parsed_script[KEY_TITLE] != f"Podcast on {topic}": active_tag = None
        elif active_tag: current_tag_content.append(line)
    if active_tag and current_tag_content:
        if active_tag.upper() == TAG_TITLE and parsed_script[KEY_TITLE] == f"Podcast on {topic}": parsed_script[KEY_TITLE] = "\n".join(current_tag_content).strip()
        else: parsed_script[KEY_SEGMENTS].append({KEY_SEGMENT_TITLE: active_tag, KEY_CONTENT: "\n".join(current_tag_content).strip()})
    processed_segments = []; i = 0; temp_segments_for_processing = parsed_script[KEY_SEGMENTS]; parsed_script[KEY_SEGMENTS] = []
    while i < len(temp_segments_for_processing):
        segment = temp_segments_for_processing[i]; title_tag = segment[KEY_SEGMENT_TITLE]; text_content = segment[KEY_CONTENT]
        if title_tag.endswith("_TITLE") and (i + 1 < len(temp_segments_for_processing)):
            next_segment = temp_segments_for_processing[i+1]
            if next_segment[KEY_SEGMENT_TITLE] == title_tag.replace("_TITLE", "_CONTENT"):
                processed_segments.append({KEY_SEGMENT_TITLE: text_content, KEY_CONTENT: next_segment[KEY_CONTENT]}); i += 1
            else: processed_segments.append({KEY_SEGMENT_TITLE: title_tag, KEY_CONTENT: text_content})
        elif title_tag in [SEGMENT_TITLE_INTRO, SEGMENT_TITLE_OUTRO]: processed_segments.append({KEY_SEGMENT_TITLE: title_tag, KEY_CONTENT: text_content})
        elif not title_tag.endswith("_CONTENT"): processed_segments.append({KEY_SEGMENT_TITLE: title_tag, KEY_CONTENT: text_content})
        i += 1
    parsed_script[KEY_SEGMENTS] = processed_segments
    if (not parsed_script[KEY_TITLE] or parsed_script[KEY_TITLE] == f"Podcast on {topic}") and not any(s[KEY_SEGMENT_TITLE] == SEGMENT_TITLE_INTRO for s in parsed_script[KEY_SEGMENTS]):
        logger.warning(f"[PSWA_PARSING_FALLBACK] Critical tags missing after fallback for topic '{topic}'. Output: '{raw_script_text[:200]}...'")
    return parsed_script

# --- Helper for AIMS_TTS Interaction ---
# This is a placeholder, actual interaction might be more complex
# or PSWA might directly call AIMS_TTS's Celery tasks if available.
def _call_aims_service_for_script(payload: dict, headers: dict) -> dict:
    # ... (existing _call_aims_service_for_script implementation, using AIMS polling) ...
    pass

# --- Main Celery Task for Weaving Script ---
class WeaveScriptTask(Task): # Inherit from Celery's Task class
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # This method is called by Celery when the task raises an unhandled exception.
        # 'kwargs' will contain the parameters passed to the task, including 'idempotency_key'.
        idempotency_key = kwargs.get('idempotency_key')
        task_name = self.name # e.g., 'pswa.weave_script_task'
        # Ensure logger is available if called outside app context or if app.logger is not set
        current_logger = logger if logger.hasHandlers() else logging.getLogger(__name__)
        current_logger.error(f'Celery Task {task_id} (PSWA WeaveScript) failed: {exc}. Idempotency Key: {idempotency_key}', exc_info=einfo)

        if idempotency_key: # Attempt to mark idempotency record as failed if key is present
            db_conn = None
            try:
                db_conn = _get_pswa_db_connection_idempotency()
                if db_conn:
                    db_conn.autocommit = False
                    error_payload = {"error_type": type(exc).__name__, "error_message": str(exc), "traceback": str(einfo)}
                    _store_pswa_idempotency_result(db_conn, idempotency_key, task_name, pswa_config['IDEMPOTENCY_STATUS_FAILED'], error_payload=error_payload, is_new_key=False)
                    db_conn.commit()
                    current_logger.info(f"Idempotency record for key {idempotency_key} marked as FAILED due to task exception.")
            except Exception as db_err:
                current_logger.error(f"Failed to update idempotency record to FAILED for key {idempotency_key} after task failure: {db_err}", exc_info=True)
                if db_conn: db_conn.rollback()
            finally:
                if db_conn and not db_conn.closed:
                    try: db_conn.close()
                    except Exception: pass # Ignore errors on close during failure handling
        # Default Celery failure handling will still occur (e.g., marking task as FAILED in backend)


@pswa_celery_app.task(bind=True, base=WeaveScriptTask, name='pswa.weave_script_task')
def weave_script_task(self, request_id_celery: str, content: str, topic: str, persona: Optional[str] = None, narrative_guidance: Optional[str] = None, test_scenario_header: Optional[str] = None, idempotency_key: Optional[str] = None):
    """
    Celery task to generate a podcast script using AIMS for LLM processing.
    request_id_celery is for logging and can be the original HTTP request ID.
    idempotency_key is provided by CPOA.
    """
    task_id_for_logging = self.request.id # Celery's own task ID
    pswa_task_name = self.name # "pswa.weave_script_task"

    if not idempotency_key:
        logger.error(f"Celery Task {task_id_for_logging}: Idempotency key not provided by CPOA for weave_script_task. This is required.", extra={"orig_req_id": request_id_celery})
        return {"error": "PSWA_IDEMPOTENCY_KEY_MISSING", "message": "Idempotency key is required for PSWA task."}

    logger.info(f"Celery Task {task_id_for_logging} (Orig Req ID: {request_id_celery}, IdempotencyKey: {idempotency_key}): Weaving script for topic '{topic}'. Persona: {persona or 'default'}")
    self.update_state(state='PROGRESS', meta={'current_step': 'Initiated, checking idempotency', 'progress_percent': 1})

    db_conn_idem = None
    try:
        db_conn_idem = _get_pswa_db_connection_idempotency()
        if not db_conn_idem:
            logger.error(f"Celery Task {task_id_for_logging}: Failed to get DB connection for idempotency check.", extra={"orig_req_id": request_id_celery})
            raise Exception("PSWA failed to connect to DB for idempotency check.")

        db_conn_idem.autocommit = False

        existing_record = _check_pswa_idempotency_key(db_conn_idem, idempotency_key, pswa_task_name)
        if existing_record:
            status = existing_record['status']
            locked_at = existing_record['locked_at']
            if status == pswa_config['IDEMPOTENCY_STATUS_COMPLETED']:
                logger.info(f"Idempotency: Found completed record for key '{idempotency_key}'. Returning stored result.", extra={"orig_req_id": request_id_celery})
                db_conn_idem.rollback()
                return existing_record['result_payload']
            elif status == pswa_config['IDEMPOTENCY_STATUS_PROCESSING']:
                if locked_at and (datetime.now(timezone.utc) - locked_at).total_seconds() < pswa_config['IDEMPOTENCY_LOCK_TIMEOUT_SECONDS']:
                    logger.warning(f"Idempotency: Key '{idempotency_key}' is already processing. Returning conflict status.", extra={"orig_req_id": request_id_celery})
                    db_conn_idem.rollback()
                    return {"status": "PROCESSING_CONFLICT", "message": "Task with this idempotency key is already processing.", "idempotency_key": idempotency_key}
                else:
                    logger.warning(f"Idempotency: Key '{idempotency_key}' was 'processing' but lock timed out. Re-processing.", extra={"orig_req_id": request_id_celery})
                    _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_PROCESSING'], is_new_key=False)
            elif status == pswa_config['IDEMPOTENCY_STATUS_FAILED']:
                 logger.info(f"Idempotency: Key '{idempotency_key}' previously failed. Retrying.", extra={"orig_req_id": request_id_celery})
                 _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_PROCESSING'], is_new_key=False)
        else:
            _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_PROCESSING'], is_new_key=True)

        db_conn_idem.commit()
        self.update_state(state='PROGRESS', meta={'current_step': 'Idempotency check passed. Starting main logic.', 'progress_percent': 5})

        # --- Original Task Logic (after idempotency check) ---
        # ... (Test Mode, Cache Check, AIMS Call Preparation, AIMS Call as before, but ensure to store result/error in idempotency_keys table) ...

        # Example of storing successful result (adapt this into your actual success path):
        # final_success_payload = {"script_data": structured_script_from_aims_or_cache}
        # _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=final_success_payload, is_new_key=False)
        # db_conn_idem.commit()
        # return final_success_payload

        # --- Test Mode Handling (Integrated with Idempotency) ---
        if pswa_config.get('PSWA_TEST_MODE_ENABLED'):
            scenario = test_scenario_header
            logger.info(f"Celery Task {task_id_for_logging}: PSWA Test Mode enabled. Scenario: '{scenario}'")
            self.update_state(state='PROGRESS', meta={'current_step': 'Test mode processing', 'progress_percent': 50})
            time.sleep(0.1)
            test_result_payload = None
            if scenario == 'insufficient_content':
                test_result_payload = {"error": "Insufficient content", "message": PSWA_TEST_SCENARIO_INSUFFICIENT_CONTENT_MSG, "topic": topic}
                _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=test_result_payload, is_new_key=False)
            elif scenario == 'llm_error':
                test_result_payload = {"error": "LLM_PROCESSING_ERROR", "message": PSWA_TEST_SCENARIO_LLM_ERROR_MSG, "details": "Simulated AIMS failure."}
                _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_FAILED'], error_payload=test_result_payload, is_new_key=False)
            elif scenario == 'malformed_json':
                test_result_payload = {"error": "AIMS_BAD_JSON_RESPONSE", "message": PSWA_TEST_SCENARIO_MALFORMED_JSON_MSG, "raw_response_preview": "{'title': 'Test Title', segments: [unfinished..."}
                _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_FAILED'], error_payload=test_result_payload, is_new_key=False)
            else: # Default test success
                dummy_script = {"script_id": f"test_script_{task_id_for_logging}", "topic": topic, "title": f"Test Mode Title for {topic}", "intro": "This is a test intro.", "segments": [{"segment_title": "Test Segment 1", "content": "Content for test segment 1."}], "outro": "This is a test outro.", "llm_model_used": "test-mode-model", "source": "test_mode_generation", "persona_used": persona or pswa_config.get('PSWA_DEFAULT_PERSONA')}
                test_result_payload = {"script_data": dummy_script}
                _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=test_result_payload, is_new_key=False)

            db_conn_idem.commit()
            if "error" not in test_result_payload : self.update_state(state='SUCCESS', meta={'current_step': 'Test mode script generated', 'progress_percent': 100, 'result': test_result_payload})
            return test_result_payload

        # --- Cache Check (if not in test mode) ---
        topic_hash = _calculate_content_hash(topic, content) # Ensure topic_hash is defined
        if pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
            cached_script = _get_cached_script(topic_hash, pswa_config['PSWA_SCRIPT_CACHE_MAX_AGE_HOURS'])
            if cached_script:
                logger.info(f"[PSWA_MAIN_LOGIC] Returning cached script for topic '{topic}', hash {topic_hash}")
                final_cache_payload = {"script_data": cached_script, "status_for_metric": "success_cache_hit"}
                _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=final_cache_payload, is_new_key=False)
                db_conn_idem.commit()
                return final_cache_payload
            else:
                 logger.info("PSWA cache miss", extra=dict(metric_name="pswa_cache_miss_count", value=1, tags={"topic_hash": topic_hash}))


        # --- Prepare for AIMS call (if not cached or cache disabled) ---
        current_persona = persona or pswa_config.get('PSWA_DEFAULT_PERSONA')
        persona_system_message_addition = pswa_config.get('PSWA_PERSONA_PROMPTS_MAP_PARSED', {}).get(current_persona, "")
        final_system_message = f"{persona_specific_system_message.strip()} {pswa_config.get('PSWA_BASE_SYSTEM_MESSAGE_JSON_SCHEMA_INSTRUCTION', '')}".strip()
        user_prompt_narrative_guidance = narrative_guidance or pswa_config.get('PSWA_NARRATIVE_GUIDANCE_USER_PROMPT_ADDITION', '')
        final_user_message = pswa_config.get('PSWA_DEFAULT_PROMPT_USER_TEMPLATE', '').format(topic=topic, content=content, narrative_guidance=user_prompt_narrative_guidance)
        aims_payload = {"model_id": pswa_config.get('PSWA_LLM_MODEL'), "system_message": final_system_message, "user_message": final_user_message, "temperature": pswa_config.get('PSWA_LLM_TEMPERATURE'), "max_tokens": pswa_config.get('PSWA_LLM_MAX_TOKENS'), "json_mode": pswa_config.get('PSWA_LLM_JSON_MODE')}
        aims_request_id_header = {"X-Request-ID": f"pswa_to_aims_{task_id_for_logging}"}

        self.update_state(state='PROGRESS', meta={'current_step': 'Calling AIMS service', 'progress_percent': 30})
        logger.info(f"Celery Task {task_id_for_logging}: Calling AIMS service for script generation.", extra={"aims_model": aims_payload["model_id"], "orig_req_id": request_id_celery})

        aims_response_data = _call_aims_service_for_script(aims_payload, aims_request_id_header) # This helper handles polling

        if "error" in aims_response_data: # Check for logical error from AIMS
            logger.error(f"Celery Task {task_id_for_logging}: AIMS service returned an error: {aims_response_data}", extra={"aims_response": aims_response_data, "orig_req_id": request_id_celery})
            _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_FAILED'], error_payload=aims_response_data, is_new_key=False)
            db_conn_idem.commit()
            return aims_response_data

        structured_script = aims_response_data
        if not (isinstance(structured_script, dict) and all(k in structured_script for k in ["title", "intro", "segments", "outro"])): # Basic validation
            logger.error(f"Celery Task {task_id_for_logging}: LLM (AIMS) response malformed. Preview: {json.dumps(structured_script)[:500]}", extra={"raw_response_preview": json.dumps(structured_script)[:500], "orig_req_id": request_id_celery})
            malformed_error_payload = {"error": "PSWA_MALFORMED_SCRIPT_FROM_AIMS", "message": "AIMS returned a malformed script structure.", "details_preview": json.dumps(structured_script)[:200]}
            _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_FAILED'], error_payload=malformed_error_payload, is_new_key=False)
            db_conn_idem.commit()
            return malformed_error_payload

        script_id = f"pswa_script_{uuid.uuid4().hex[:12]}" # Generate new script_id
        structured_script["script_id"] = script_id
        structured_script["llm_model_used"] = aims_response_data.get("model_id_used", pswa_config.get('PSWA_LLM_MODEL')) # Get model from AIMS if available
        structured_script["persona_used"] = current_persona
        structured_script["source"] = "aims_generation_async"

        if pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED') and not (structured_script.get("segments") and any(s.get("segment_title") == "ERROR" for s in structured_script["segments"])):
             _save_script_to_cache(script_id, topic_hash, structured_script, structured_script["llm_model_used"])


        final_success_payload = {"script_data": structured_script, "status_for_metric": "success_generation_async", "aims_total_duration_ms": aims_response_data.get("aims_total_duration_ms")}
        _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_COMPLETED'], result_payload=final_success_payload, is_new_key=False)
        db_conn_idem.commit()
        self.update_state(state='SUCCESS', meta={'current_step': 'Script generated successfully', 'progress_percent': 100, 'result_summary': {"script_id": script_id, "title": structured_script.get("title")}})
        logger.info(f"Celery Task {task_id_for_logging}: Script generation successful. Script ID: {script_id}", extra={"script_title": structured_script.get("title"), "orig_req_id": request_id_celery})
        return final_success_payload

    except Exception as e: # Catch-all for main logic, including AIMS call issues
        logger.error(f"Celery Task {task_id_for_logging} (Idempotency Key: {idempotency_key}): Unhandled exception in main task logic: {e}", exc_info=True, extra={"orig_req_id": request_id_celery})
        error_payload_for_idempotency = {"error": "PSWA_TASK_UNHANDLED_EXCEPTION", "message": f"PSWA task failed: {type(e).__name__} - {str(e)}"}
        if db_conn_idem: # Attempt to store failure if DB conn is available
            try:
                _store_pswa_idempotency_result(db_conn_idem, idempotency_key, pswa_task_name, pswa_config['IDEMPOTENCY_STATUS_FAILED'], error_payload=error_payload_for_idempotency, is_new_key=False)
                db_conn_idem.commit()
            except Exception as db_e:
                logger.error(f"Celery Task {task_id_for_logging}: Failed to store idempotency failure status for key {idempotency_key} after main task error: {db_e}", exc_info=True, extra={"orig_req_id": request_id_celery})
                if db_conn_idem: db_conn_idem.rollback()
        raise # Re-raise the exception to ensure Celery marks it as failed and on_failure is called.
    finally:
        if db_conn_idem:
            try:
                if not db_conn_idem.closed: db_conn_idem.close()
            except Exception as e_close:
                 logger.error(f"Error closing PSWA DB connection for idempotency: {e_close}", exc_info=True, extra={"orig_req_id": request_id_celery})


# --- Flask HTTP Endpoints ---
@app.route('/v1/weave_script', methods=['POST']) # Renamed from handle_weave_script for clarity
def weave_script_async_endpoint():
    request_id_main = f"pswa_http_req_{uuid.uuid4().hex[:8]}" # For logging this specific HTTP request
    logger.info(f"Request {request_id_main}: Received async /v1/weave_script request.")

    idempotency_key_header = request.headers.get('X-Idempotency-Key')
    if not idempotency_key_header:
        logger.warning(f"Request {request_id_main}: X-Idempotency-Key header missing. This is required by PSWA.")
        return jsonify({"error_code": "PSWA_MISSING_IDEMPOTENCY_KEY", "message": "X-Idempotency-Key header is required."}), 400

    try:
        data = request.get_json()
        if not data:
            logger.warning(f"Request {request_id_main}: Invalid or empty JSON payload.")
            return jsonify({"error_code": "PSWA_INVALID_PAYLOAD", "message": "Invalid or empty JSON payload."}), 400
    except Exception as e_json_decode:
        logger.warning(f"Request {request_id_main}: Malformed JSON payload: {e_json_decode}", exc_info=True)
        return jsonify({"error_code": "PSWA_MALFORMED_JSON", "message": f"Malformed JSON: {str(e_json_decode)}"}), 400

    content = data.get(KEY_CONTENT)
    topic = data.get(KEY_TOPIC)
    persona = data.get('persona') # Optional
    narrative_guidance = data.get('narrative_guidance') # Optional
    test_scenario_header = request.headers.get('X-Test-Scenario') # For test mode

    if not content or not isinstance(content, str) or not content.strip():
        logger.warning(f"Request {request_id_main}: Missing 'content' in payload.")
        return jsonify({"error_code": "PSWA_MISSING_CONTENT_OR_TOPIC", "message": "Missing 'content' or 'topic' in payload."}), 400
    if not topic or not isinstance(topic, str) or not topic.strip():
        logger.warning(f"Request {request_id_main}: Missing 'topic' in payload.")
        return jsonify({"error_code": "PSWA_MISSING_CONTENT_OR_TOPIC", "message": "Missing 'content' or 'topic' in payload."}), 400

    logger.info(f"Request {request_id_main}: Dispatching weave_script_task. Topic: '{topic}', Idempotency Key: {idempotency_key_header}")

    task_submission = weave_script_task.delay(
        request_id_celery=request_id_main, # Pass HTTP request ID for logging correlation
        content=content,
        topic=topic,
        persona=persona,
        narrative_guidance=narrative_guidance,
        test_scenario_header=test_scenario_header,
        idempotency_key=idempotency_key_header # Pass the client-provided idempotency key
    )

    status_url = url_for('get_pswa_task_status', task_id=task_submission.id, _external=False) # Relative URL
    logger.info(f"Request {request_id_main}: Dispatched PSWA Celery task {task_submission.id}. Status URL: {status_url}")

    return jsonify({
        "message": "Script weaving task accepted.",
        "task_id": task_submission.id,
        "status_url": status_url,
        "idempotency_key_processed": idempotency_key_header
    }), 202

@app.route('/tasks/<task_id>', methods=['GET'])
def get_pswa_task_status(task_id: str):
    logger.info(f"Received request for PSWA task status: {task_id}")
    task_result = AsyncResult(task_id, app=celery_app)
    response_data = {"task_id": task_id, "status": task_result.status, "result": None}

    if task_result.successful():
        # The result of weave_script_task is the dictionary with "script_data" or "error_data"
        task_output = task_result.result
        response_data["result"] = task_output
        # Determine appropriate HTTP status code based on the task's actual outcome
        if "error_data" in task_output:
             error_code = task_output.get("error_data", {}).get("error_code", "PSWA_TASK_ERROR_UNKNOWN")
             http_status = 500
             if error_code == "PSWA_AIMS_TIMEOUT": http_status = 504
             elif error_code in ["PSWA_AIMS_HTTP_ERROR", "PSWA_AIMS_BAD_RESPONSE", "PSWA_AIMS_BAD_RESPONSE_JSON", "PSWA_AIMS_TASK_REJECTED", "PSWA_AIMS_BAD_TASK_RESPONSE"]: http_status = 502
             elif error_code == "PSWA_INSUFFICIENT_CONTENT": http_status = 400 # Or 200 with error in body as per original logic
             return jsonify(response_data), http_status
        return jsonify(response_data), 200 # Success
    elif task_result.failed():
        error_info = {"error": {"type": "task_failed", "message": str(task_result.info)}}
        response_data["result"] = error_info
        return jsonify(response_data), 500
    else: # PENDING, STARTED, RETRY
        return jsonify(response_data), 202


if __name__ == '__main__':
    # Logging calls here will use the configured app.logger via the global logger alias
    if pswa_config.get("DATABASE_TYPE") == "sqlite" and not pswa_config.get("SHARED_DATABASE_PATH") and pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        logger.warning("SHARED_DATABASE_PATH not configured for PSWA SQLite mode with caching. Caching may fail.")
    elif pswa_config.get("DATABASE_TYPE") == "postgres" and not all(pswa_config.get(k) for k in ["POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]) and pswa_config.get('PSWA_SCRIPT_CACHE_ENABLED'):
        logger.warning("PostgreSQL is cache DB_TYPE, but connection vars missing. Caching may fail.")

    init_pswa_db() # Call init_db based on configured DB_TYPE

    host = pswa_config.get("PSWA_HOST", "0.0.0.0")
    port = pswa_config.get("PSWA_PORT", 5004)
    debug_mode = pswa_config.get("PSWA_DEBUG_MODE", True)
    logger.info(f"--- PSWA Service (AIMS Client) starting on {host}:{port} (Debug: {debug_mode}, DB: {pswa_config.get('DATABASE_TYPE')}) ---")
    app.run(host=host, port=port, debug=debug_mode)

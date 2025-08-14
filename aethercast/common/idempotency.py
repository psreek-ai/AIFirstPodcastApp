import logging
import json
from datetime import datetime, timezone
import psycopg2
from typing import Optional, Dict, Any

def check_idempotency_key(db_conn, idempotency_key: str, task_name: str) -> Optional[Dict[str, Any]]:
    """
    Checks for an existing idempotency key for a given service task.
    """
    logger = logging.getLogger(__name__)
    log_extra = {"idempotency_key": idempotency_key, "task_name": task_name}
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT idempotency_key, task_name, workflow_id, created_at, locked_at, status, result_payload, error_payload FROM idempotency_keys WHERE idempotency_key = %s AND task_name = %s",
                (idempotency_key, task_name)
            )
            record = cur.fetchone()
            if record:
                logger.info(f"Idempotency key found. Status: '{record['status']}'.", extra=log_extra)
                if isinstance(record.get('result_payload'), str):
                    record['result_payload'] = json.loads(record['result_payload'])
                if isinstance(record.get('error_payload'), str):
                    record['error_payload'] = json.loads(record['error_payload'])
                return dict(record)
            logger.info("No existing idempotency key found.", extra=log_extra)
            return None
    except (psycopg2.Error, json.JSONDecodeError) as e:
        logger.error(f"Idempotency: DB/JSON error checking key: {e}", exc_info=True, extra=log_extra)
        raise

def store_idempotency_record(db_conn, idempotency_key: str, task_name: str, status: str, workflow_id: Optional[str] = None, result_payload: Optional[dict] = None, error_payload: Optional[dict] = None, is_new_key: bool = True):
    """
    Stores or updates an idempotency record for a given service task.
    """
    logger = logging.getLogger(__name__)
    log_extra = {"idempotency_key": idempotency_key, "task_name": task_name, "new_status": status}
    current_ts_utc = datetime.now(timezone.utc)
    locked_at_val = current_ts_utc if status == 'processing' else None
    try:
        with db_conn.cursor() as cur:
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
                    (idempotency_key, task_name, workflow_id, locked_at_val, status,
                     json.dumps(result_payload) if result_payload else None,
                     json.dumps(error_payload) if error_payload else None, current_ts_utc)
                )
            else:
                logger.info("Updating existing idempotency key.", extra=log_extra)
                set_clauses = ["status = %s", "result_payload = %s", "error_payload = %s"]
                params_update = [status, json.dumps(result_payload) if result_payload else None, json.dumps(error_payload) if error_payload else None]
                if status == 'processing':
                    set_clauses.append("locked_at = %s")
                    params_update.append(current_ts_utc)
                elif status in ['completed', 'failed']:
                    set_clauses.append("locked_at = NULL")
                params_update.extend([idempotency_key, task_name])
                cur.execute(
                    f"UPDATE idempotency_keys SET {', '.join(set_clauses)} WHERE idempotency_key = %s AND task_name = %s;",
                    tuple(params_update)
                )
            logger.info("Successfully stored/updated idempotency key.", extra=log_extra)
    except (psycopg2.Error, json.JSONDecodeError) as e:
        logger.error(f"Idempotency: DB/JSON error storing key: {e}", exc_info=True, extra=log_extra)
        raise

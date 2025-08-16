import logging
import json
from datetime import datetime, timezone
import psycopg2
from typing import Optional, Dict, Any

import time

def check_idempotency(db_conn, idempotency_key: str, task_name: str, lock_timeout_seconds: int, service_name: str = "common-idempotency"):
    """
    Checks for an existing idempotency key for a given service task.
    """
    logger = logging.getLogger(__name__)
    log_extra = {"idempotency_key": idempotency_key, "task_name": task_name, "service_name": service_name}
    try:
        with db_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
            cursor.execute(
                "SELECT key, task_name, workflow_id, created_at, locked_at, status, result_payload, error_payload FROM idempotency_keys WHERE key = %s AND task_name = %s",
                (idempotency_key, task_name)
            )
            record = cursor.fetchone()
            if record:
                logger.info(f"Idempotency key found. Status: '{record['status']}'.", extra=log_extra)
                if record['status'] == 'completed':
                    return {'status': 'completed', 'result': record['result_payload']}
                elif record['status'] == 'processing':
                    if record['locked_at'] and (time.time() - record['locked_at'].timestamp()) < lock_timeout_seconds:
                        logger.warning("Task is already processing (lock not expired).", extra=log_extra)
                        return {'status': 'conflict', 'message': 'Task already processing'}
                    else:
                        logger.warning("Task was 'processing' but lock expired or missing. Will attempt to re-acquire.", extra=log_extra)
                        return None # Stale lock, proceed to acquire
                elif record['status'] == 'failed':
                        logger.warning("Previous attempt for this task failed. Will attempt to re-run.", extra=log_extra)
                        return None # Failed, proceed to acquire lock and re-run
            return None # No record found
    except (psycopg2.Error, json.JSONDecodeError) as e:
        logger.error(f"Idempotency: DB/JSON error checking key: {e}", exc_info=True, extra=log_extra)
        raise

def acquire_idempotency_lock(db_conn, idempotency_key: str, task_name: str, workflow_id: Optional[str] = None, service_name: str = "common-idempotency"):
    """Acquires a lock for the task by inserting/updating the idempotency record."""
    logger = logging.getLogger(__name__)
    log_extra = {'idempotency_key': idempotency_key, 'task_name': task_name, 'workflow_id': workflow_id or "N/A", 'service_name': service_name}
    try:
        with db_conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO idempotency_keys (key, task_name, workflow_id, status, locked_at, created_at, updated_at)
                VALUES (%s, %s, %s, 'processing', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (key, task_name) DO UPDATE SET
                    status = 'processing',
                    locked_at = CURRENT_TIMESTAMP,
                    workflow_id = EXCLUDED.workflow_id,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id;
                """,
                (idempotency_key, task_name, workflow_id)
            )
            lock_id = cursor.fetchone()
            db_conn.commit()
            if lock_id:
                logger.info("Idempotency lock acquired successfully.", extra=log_extra)
                return True
            else:
                logger.error("Failed to acquire idempotency lock (no id returned).", extra=log_extra)
                return False
    except (Exception, psycopg2.Error) as error:
        db_conn.rollback()
        logger.error(f"Error acquiring idempotency lock: {error}", exc_info=True, extra=log_extra)
        raise

def update_idempotency_record(db_conn, idempotency_key: str, task_name: str, final_status: str, result_payload: Optional[dict] = None, error_payload: Optional[dict] = None, service_name: str = "common-idempotency"):
    """Updates the idempotency record with the final status and result/error."""
    logger = logging.getLogger(__name__)
    log_extra = {'idempotency_key': idempotency_key, 'task_name': task_name, 'final_status': final_status, 'service_name': service_name}
    try:
        with db_conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE idempotency_keys
                SET status = %s, result_payload = %s, error_payload = %s, locked_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE key = %s AND task_name = %s
                """,
                (final_status, json.dumps(result_payload) if result_payload else None, json.dumps(error_payload) if error_payload else None, idempotency_key, task_name)
            )
            db_conn.commit()
            logger.info("Idempotency record updated successfully.", extra=log_extra)
    except (Exception, psycopg2.Error) as error:
        db_conn.rollback()
        logger.error(f"Error updating idempotency record: {error}", exc_info=True, extra=log_extra)
        raise

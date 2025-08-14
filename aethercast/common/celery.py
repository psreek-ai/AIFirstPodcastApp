import os
from celery import Celery

def create_celery_app(service_name: str) -> Celery:
    """
    Creates and configures a Celery application instance for a given service.
    """
    broker_url = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
    result_backend = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

    celery_app = Celery(
        service_name,
        broker=broker_url,
        backend=result_backend
    )

    celery_app.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
    )

    celery_app.finalize()
    return celery_app

import os
from contextlib import contextmanager

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()


def get_db_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST_BM"],
        port=os.environ.get("DB_PORT_BM", "5432"),
        dbname=os.environ["DB_NAME_BM"],
        user=os.environ["DB_USER_BM"],
        password=os.environ["DB_PASSWORD_BM"],
        sslmode="require",  # Azure PostgreSQL requires SSL
    )


@contextmanager
def get_db_cursor(commit=False):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

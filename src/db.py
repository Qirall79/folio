import sqlite3
from contextlib import closing
from uuid import uuid4

from src.index_storage import PROJECT_ROOT


DB_PATH = PROJECT_ROOT / "storage" / "documents.sqlite"


def _connect(timeout: float = 30) -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=timeout, autocommit=True)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.autocommit = False
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH, timeout=30, autocommit=True)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")

    with closing(_connect()) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY NOT NULL,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL REFERENCES users(id),
                expires_at INTEGER NOT NULL
            )
        """)
        user_columns = connection.execute("PRAGMA table_info(users)").fetchall()
        if "email" not in {row["name"] for row in user_columns}:
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "is_admin" not in {row["name"] for row in user_columns}:
            connection.execute("""
                ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0
                CHECK (is_admin IN (0, 1))
            """)
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users (email)
            WHERE email IS NOT NULL
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS oauth_accounts (
                provider TEXT NOT NULL,
                subject TEXT NOT NULL,
                user_id TEXT NOT NULL REFERENCES users(id),
                PRIMARY KEY (provider, subject),
                UNIQUE (user_id, provider)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                state_hash TEXT PRIMARY KEY NOT NULL,
                provider TEXT NOT NULL,
                code_verifier TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                link_user_id TEXT REFERENCES users(id)
            )
        """)

        columns = connection.execute("PRAGMA table_info(documents)").fetchall()
        migrate = bool(columns) and "owner_id" not in {row["name"] for row in columns}
        if migrate:
            connection.execute("ALTER TABLE documents RENAME TO documents_legacy")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY NOT NULL,
                owner_id TEXT REFERENCES users(id),
                file_hash TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
                job_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                error TEXT,
                UNIQUE (owner_id, file_hash)
            )
        """)
        if migrate:
            connection.execute("""
                INSERT INTO documents (
                    id, file_hash, original_filename, file_path,
                    status, job_id, created_at, error
                )
                SELECT id, file_hash, original_filename, file_path,
                       status, job_id, created_at, error
                FROM documents_legacy
            """)
            connection.execute("DROP TABLE documents_legacy")

        connection.execute("""
            CREATE INDEX IF NOT EXISTS documents_owner_job
            ON documents (owner_id, job_id)
        """)
        from src.observability import init_observability
        init_observability(connection)


def create_user(username: str, password_hash: str, email: str | None = None) -> dict:
    user_id = str(uuid4())
    with closing(_connect()) as connection, connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, email) VALUES (?, ?, ?, ?)",
            (user_id, username, password_hash, email),
        )
    return {"id": user_id, "username": username, "email": email}


def get_user_by_email(email: str) -> dict | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
    return dict(row) if row is not None else None


def get_user_by_username(username: str) -> dict | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row is not None else None


def get_user_by_id(user_id: str) -> dict | None:
    with closing(_connect()) as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row is not None else None


def update_password_hash(user_id: str, password_hash: str) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )


def create_session(token_hash: str, user_id: str, expires_at: int, now: int) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )


def get_session_user(token_hash: str, now: int) -> dict | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            """
            SELECT users.id, users.username, users.email, users.is_admin
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
    return dict(row) if row is not None else None


def delete_session(token_hash: str) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def get_linked_providers(user_id: str) -> list[str]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT provider FROM oauth_accounts WHERE user_id = ? ORDER BY provider",
            (user_id,),
        ).fetchall()
    return [row["provider"] for row in rows]


def get_or_create_oauth_user(provider: str, subject: str, email: str) -> dict:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT users.id, users.username, users.email FROM oauth_accounts
            JOIN users ON users.id = oauth_accounts.user_id
            WHERE provider = ? AND subject = ?
            """,
            (provider, subject),
        ).fetchone()
        if row is not None:
            return dict(row)
        if connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("account_exists")
        user_id = str(uuid4())
        connection.execute(
            "INSERT INTO users (id, username, password_hash, email) VALUES (?, ?, ?, ?)",
            (user_id, email, "", email),
        )
        connection.execute(
            "INSERT INTO oauth_accounts (provider, subject, user_id) VALUES (?, ?, ?)",
            (provider, subject, user_id),
        )
    return {"id": user_id, "username": email, "email": email}


def link_oauth_account(user_id: str, provider: str, subject: str, email: str) -> None:
    with closing(_connect()) as connection, connection:
        account = connection.execute(
            "SELECT user_id FROM oauth_accounts WHERE provider = ? AND subject = ?",
            (provider, subject),
        ).fetchone()
        if account is not None:
            if account["user_id"] != user_id:
                raise ValueError("identity_in_use")
            return
        existing = connection.execute(
            "SELECT subject FROM oauth_accounts WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        ).fetchone()
        if existing is not None:
            raise ValueError("identity_in_use")
        user = connection.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise ValueError("session_expired")
        if user["email"] is None:
            if connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
                raise ValueError("account_exists")
            connection.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
        connection.execute(
            "INSERT INTO oauth_accounts (provider, subject, user_id) VALUES (?, ?, ?)",
            (provider, subject, user_id),
        )


def save_oauth_state(state_hash: str, provider: str, code_verifier: str,
                    expires_at: int, link_user_id: str | None, now: int) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (now,))
        connection.execute(
            """
            INSERT INTO oauth_states
                (state_hash, provider, code_verifier, expires_at, link_user_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (state_hash, provider, code_verifier, expires_at, link_user_id),
        )


def consume_oauth_state(state_hash: str, provider: str, now: int) -> dict | None:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            DELETE FROM oauth_states
            WHERE state_hash = ? AND provider = ? AND expires_at > ?
            RETURNING *
            """,
            (state_hash, provider, now),
        ).fetchone()
    return dict(row) if row is not None else None


def create_document(
    owner_id: str,
    file_hash: str,
    original_filename: str,
    file_path: str,
    job_id: str | None = None,
) -> dict:
    document_id = str(uuid4())

    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO documents (
                id, owner_id, file_hash, original_filename, file_path, job_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (document_id, owner_id, file_hash, original_filename, file_path, job_id),
        )
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()

    return dict(row)


def get_document_by_hash(file_hash: str, owner_id: str) -> dict | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE file_hash = ? AND owner_id = ?",
            (file_hash, owner_id),
        ).fetchone()

    return dict(row) if row is not None else None


def get_document(document_id: str, owner_id: str) -> dict | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ? AND owner_id = ?",
            (document_id, owner_id),
        ).fetchone()
    return dict(row) if row is not None else None


def get_document_by_job(job_id: str, owner_id: str) -> dict | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE job_id = ? AND owner_id = ?",
            (job_id, owner_id),
        ).fetchone()
    return dict(row) if row is not None else None


def update_document_status(
    document_id: str,
    owner_id: str,
    status: str,
    job_id: str | None = None,
    error: str | None = None,
) -> dict:
    with closing(_connect()) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET status = ?, job_id = COALESCE(?, job_id), error = ?
            WHERE id = ? AND owner_id = ?
            """,
            (status, job_id, error, document_id, owner_id),
        )
        if cursor.rowcount == 0:
            raise LookupError(f"Document not found: {document_id}")

        row = connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()

    return dict(row)


def list_documents(owner_id: str) -> list[dict]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            """
            SELECT * FROM documents WHERE owner_id = ?
            ORDER BY created_at DESC, rowid DESC
            """,
            (owner_id,),
        ).fetchall()

    return [dict(row) for row in rows]

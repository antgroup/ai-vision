from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from agentlens.logging import get_logger


def _single_line(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return " ".join(str(value).split())


def _render_sql(cursor: Any, statement: str, parameters: Any) -> tuple[str, Any | None]:
    mogrify = getattr(cursor, "mogrify", None)
    if callable(mogrify):
        try:
            return _single_line(mogrify(statement, parameters)), None
        except Exception:  # noqa: BLE001 - logging must never break a query
            pass
    return _single_line(statement), parameters


def _install_query_logging(engine: Engine) -> None:
    log = get_logger("ob")

    @event.listens_for(engine, "before_cursor_execute")
    def _log_query(
        _conn,
        cursor,
        statement,
        parameters,
        _context,
        executemany,
    ) -> None:
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        rendered_parameters = parameters
        batch_size = None
        if executemany:
            batch_size = len(parameters)
            rendered_parameters = parameters[0] if parameters else ()
        sql, fallback_parameters = _render_sql(cursor, statement, rendered_parameters)
        fields: dict[str, Any] = {"sql": sql}
        if fallback_parameters not in (None, (), {}, []):
            fields["parameters"] = fallback_parameters
        if batch_size is not None:
            fields.update(executemany=True, batch_size=batch_size)
        log.info("ob.query", **fields)


def make_engine(url: str, *, sql_log_enabled: bool = False, **kwargs) -> Engine:
    pool_kwargs: dict = {}
    if not url.startswith("sqlite"):
        pool_kwargs.update(pool_recycle=3600, pool_size=10, max_overflow=20)
    engine = create_engine(url, pool_pre_ping=True, future=True, **pool_kwargs, **kwargs)
    if engine.dialect.name in {"mysql", "mariadb"}:
        isolation = kwargs.get("execution_options", {}).get(
            "isolation_level", kwargs.get("isolation_level")
        )
        requested = kwargs.get("connect_args", {}).get(
            "autocommit", make_url(url).query.get("autocommit", False)
        )
        autocommit = (
            str(isolation).upper() == "AUTOCOMMIT"
            if isolation is not None
            else str(requested).lower() in {"true", "1"}
        )

        @event.listens_for(engine, "connect")
        def initialize_transaction_mode(dbapi_connection, _connection_record):
            # A server's protocol status can disagree with SQL @@autocommit.
            # PyMySQL autocommit(False) may then skip SET based on that flag.
            # Set the real session mode once per physical connection, before
            # application work. Later Connection execution_options still apply.
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"SET SESSION autocommit = {int(autocommit)}")
            finally:
                cursor.close()

    if sql_log_enabled:
        _install_query_logging(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def probe_database(engine: Engine) -> bool:
    """Read-only probe using the configured driver with short socket deadlines."""
    if engine.dialect.name not in {"mysql", "mariadb"}:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1
    # A probe must not borrow a business connection with unbounded socket reads
    # or change timeouts for the application's longer queries.
    args, kwargs = engine.dialect.create_connect_args(engine.url)
    kwargs.update(connect_timeout=1, read_timeout=1, write_timeout=1, autocommit=True)
    dbapi = engine.dialect.loaded_dbapi
    try:
        connection = dbapi.connect(*args, **kwargs)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
        finally:
            connection.close()
    except dbapi.Error:
        return False

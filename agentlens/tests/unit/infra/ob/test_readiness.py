import socket
import threading
import time

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from agentlens.infra.ob.engine import probe_database


def test_unresponsive_mysql_handshake_is_bounded():
    release = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)

        def stalled_server():
            with listener.accept()[0]:
                release.wait(5)

        server = threading.Thread(target=stalled_server, daemon=True)
        server.start()
        engine = create_engine(
            URL.create(
                "mysql+pymysql", username="probe", host="127.0.0.1", port=listener.getsockname()[1]
            )
        )
        try:
            started = time.monotonic()
            assert probe_database(engine) is False
            assert time.monotonic() - started < 4
            assert engine.pool.checkedout() == 0
        finally:
            release.set()
            server.join(timeout=5)
            engine.dispose()

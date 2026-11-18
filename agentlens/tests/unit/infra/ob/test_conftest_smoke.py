from sqlalchemy import text


def test_mysql_container_is_alive(session):
    assert session.execute(text("SELECT 1")).scalar() == 1

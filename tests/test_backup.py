from psst import backup

# Rebuilt from the boundaries rather than backed up (see docs/RESTORE.md).
REBUILT = {"admin_area_parts"}


def test_every_table_is_backed_up(database):
    tables = {r["table_name"] for r in database.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'psst' AND table_type = 'BASE TABLE'")}
    assert tables - REBUILT == {t for t, _ in backup.TABLES}, "add new tables to backup.TABLES"

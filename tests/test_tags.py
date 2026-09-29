from psst import runs, tags


def test_proposals_reuse_existing_tags(scratch):
    run = runs.start(scratch, "tagging", "test")
    first = tags.propose(scratch, "Test Brutalist Towers", "theme", None, ("Concrete towers test",), None, (), run, {})
    assert first.status == "created"
    again = tags.propose(scratch, "test brutalist towers", "theme", None, (), None, (), run, {})
    assert again.status != "created" and (again.tag or again.similar)
    by_alias = tags.find(scratch, "Concrete towers test")
    assert by_alias and by_alias[0]["id"] == first.tag["id"]


def test_audit_runs_and_finds_near_duplicates(scratch):
    run = runs.start(scratch, "tagging", "test")
    a = tags.propose(scratch, "Test Gasholder Frames", "theme", None, (), None, (), run, {}).tag["id"]
    b = tags.propose(scratch, "Test Gasholder Frame", "theme", None, (), None, (a,), run, {}).tag["id"]
    # Tags checked and marked as distinct are left out of the audit...
    assert not any({p["a"], p["b"]} == {a, b} for p in tags.audit(scratch))
    # ...and near duplicates without that note are listed.
    scratch.execute("UPDATE tags SET description = NULL WHERE id = %s", (b,))
    assert any({p["a"], p["b"]} == {a, b} for p in tags.audit(scratch))

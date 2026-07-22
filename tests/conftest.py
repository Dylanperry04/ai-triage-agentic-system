"""
Shared pytest fixtures.

reset_manchester_ruleset (autouse): the Manchester engine holds a single
process-global registered ruleset (app.rules.manchester_engine._APPROVED_RULESET).
Several entry points now register a provisional ruleset at import/startup time
(app.main and frontend.app both call register_provisional_ruleset() when
PROVISIONAL_MTS_MODE is on, which is the default). Any test that imports one of
those modules therefore leaves a ruleset registered for the rest of the pytest
process, which would silently flip the expected output of every test that
assumes the engine is gated (no MTS category assigned) -- e.g. the whole
triage-indicator matrix.

This autouse fixture snapshots and clears that global before every test and
restores it afterwards, so ruleset state can never leak across tests regardless
of collection order. Tests that WANT a ruleset active register one explicitly
inside the test; this fixture does not stop them, it only guarantees a clean
starting point and clean teardown.
"""
import pytest


@pytest.fixture(autouse=True)
def reset_manchester_ruleset():
    import app.rules.manchester_engine as me
    saved = me.get_approved_ruleset()
    me.clear_approved_ruleset()
    yield
    me._APPROVED_RULESET = saved


# (The legacy KTAS-sample conftest hook was removed: the system is full-MIMIC-only
# and tests use synthetic MIMIC-shaped fixtures. No KTAS sample is generated.)

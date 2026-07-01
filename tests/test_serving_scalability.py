"""
Priority-4 serving-scalability proofs:
  - serving loads only triage-time tables (not all six)
  - /cases is paginated and bounded (cannot return an unbounded set)
  - case_uid resolution is an O(1) index lookup (cached), not an O(n) scan
  - the cache invalidates when the underlying data signature changes
"""
import base64
import json

import pytest
from starlette.testclient import TestClient


def _principal(groups):
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "u1"},
        {"typ": "name", "val": "T"},
    ] + [{"typ": "groups", "val": g} for g in groups]
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


def _seed(proc, n):
    cases = []
    for i in range(n):
        cases.append({
            "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000000 + i,
            "subject_id": 10000000 + i,
            "edstay": {"subject_id": 10000000 + i, "stay_id": 30000000 + i,
                       "gender": "F", "arrival_transport": "AMBULANCE", "disposition": "HOME"},
            "triage": {"subject_id": 10000000 + i, "stay_id": 30000000 + i,
                       "chiefcomplaint": "CHEST PAIN", "acuity": 2},
            "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
        })
    (proc / "frontend_cases_override.jsonl").write_text(
        "\n".join(json.dumps(c) for c in cases))


@pytest.fixture
def resolver(tmp_path, monkeypatch):
    proc = tmp_path / "processed"; proc.mkdir()
    monkeypatch.setattr("app.config.settings.processed_dir", proc)
    import app.api.case_resolver as cr
    monkeypatch.setattr(cr.settings, "processed_dir", proc)
    cr._CASE_CACHE.clear()
    cr._PARTIAL_CASE_CACHE.clear()
    cr._COUNT_CACHE.clear()
    return cr, proc


class TestTriageTimeOnlyLoad:
    def test_serving_loader_loads_only_triage_time_tables(self, monkeypatch, tmp_path):
        # The triage-time loader passes empty heavy frames; assert it doesn't read
        # vitalsign/diagnosis/medrecon/pyxis.
        import app.data_pipeline.mimic_full_loader as L
        import app.data_pipeline.mimic_adapter as MA
        ed = tmp_path / "ed"; ed.mkdir()
        for t in ("edstays.csv.gz", "triage.csv.gz"):
            (ed / t).write_bytes(b"")
        monkeypatch.delenv("MIMIC_FULL_ED_DIR", raising=False)
        monkeypatch.setattr("app.config.settings.mimic_full_ed_dir", ed)
        monkeypatch.setattr(L, "credentialed_data_access_allowed", lambda: True)

        read_tables = []
        def spy(path, table):
            read_tables.append(table)
            import pandas as pd
            if table == "edstays":
                return pd.DataFrame([{"subject_id": 1, "hadm_id": None, "stay_id": 1,
                                      "intime": None, "outtime": None, "gender": "F",
                                      "race": "X", "arrival_transport": "AMBULANCE",
                                      "disposition": "HOME"}])
            return pd.DataFrame([{"subject_id": 1, "stay_id": 1, "temperature": 98.6,
                                  "heartrate": 80, "resprate": 18, "o2sat": 97,
                                  "sbp": 120, "dbp": 75, "pain": "5", "acuity": 2,
                                  "chiefcomplaint": "X"}])
        # Bypass the safety guard for this unit test (guards are tested elsewhere);
        # we are asserting WHICH tables the serving loader reads, nothing else.
        monkeypatch.setattr(L, "_assert_safe_to_use", lambda: ed)
        # Patch on the adapter module (the function is imported from there inside
        # the loader at call time).
        monkeypatch.setattr(MA, "load_mimic_table", spy)
        L.load_mimic_full_cases_triage_time()
        assert set(read_tables) == {"edstays", "triage"}, (
            f"serving loader read extra tables: {read_tables}")
        assert "vitalsign" not in read_tables
        assert "pyxis" not in read_tables

    def test_serving_loader_filters_triage_by_selected_stay_id(self, monkeypatch, tmp_path):
        import pandas as pd
        import app.data_pipeline.mimic_full_loader as L
        import app.data_pipeline.mimic_adapter as MA

        ed = tmp_path / "ed"; ed.mkdir()
        monkeypatch.setattr(L, "_assert_safe_to_use", lambda: ed)
        edstays = pd.DataFrame([
            {"subject_id": 2, "hadm_id": None, "stay_id": 2, "intime": None,
             "outtime": None, "gender": "F", "race": "X",
             "arrival_transport": "AMBULANCE", "disposition": "HOME"},
            {"subject_id": 3, "hadm_id": None, "stay_id": 3, "intime": None,
             "outtime": None, "gender": "M", "race": "X",
             "arrival_transport": "WALK IN", "disposition": "HOME"},
        ])
        triage = pd.DataFrame([
            {"subject_id": 1, "stay_id": 1, "temperature": 98.6,
             "heartrate": 70, "resprate": 16, "o2sat": 99, "sbp": 120,
             "dbp": 80, "pain": "1", "acuity": 5, "chiefcomplaint": "WRONG"},
            {"subject_id": 2, "stay_id": 2, "temperature": 98.6,
             "heartrate": 80, "resprate": 18, "o2sat": 98, "sbp": 125,
             "dbp": 82, "pain": "2", "acuity": 3, "chiefcomplaint": "TWO"},
            {"subject_id": 3, "stay_id": 3, "temperature": 98.6,
             "heartrate": 90, "resprate": 20, "o2sat": 97, "sbp": 130,
             "dbp": 84, "pain": "3", "acuity": 2, "chiefcomplaint": "THREE"},
        ])

        def fake_load(path, table, *, nrows=None, usecols=None):
            if table == "edstays":
                return edstays.head(nrows) if nrows is not None else edstays
            if table == "triage":
                assert nrows is None, "triage must be selected by stay_id, not by row number"
                return triage
            raise AssertionError(f"unexpected table {table}")

        monkeypatch.setattr(MA, "load_mimic_table", fake_load)
        cases = L.load_mimic_full_cases_triage_time(n=2)
        complaints = {c.stay_id: c.triage.chiefcomplaint for c in cases}
        assert complaints == {2: "TWO", 3: "THREE"}


class TestPagination:
    def test_list_cases_is_bounded_by_default(self, resolver):
        cr, proc = resolver
        _seed(proc, 130); cr._CASE_CACHE.clear()
        assert len(cr.list_cases()) <= cr.DEFAULT_PAGE_SIZE

    def test_list_cases_caps_at_max_page_size(self, resolver):
        cr, proc = resolver
        _seed(proc, 500); cr._CASE_CACHE.clear()
        assert len(cr.list_cases(limit=99999)) <= cr.MAX_PAGE_SIZE

    def test_pages_are_disjoint_and_cover(self, resolver):
        cr, proc = resolver
        _seed(proc, 120); cr._CASE_CACHE.clear()
        p1 = {c.case_uid for c in cr.list_cases(limit=50, offset=0)}
        p2 = {c.case_uid for c in cr.list_cases(limit=50, offset=50)}
        p3 = {c.case_uid for c in cr.list_cases(limit=50, offset=100)}
        assert not (p1 & p2) and not (p2 & p3)
        assert len(p1 | p2 | p3) == 120
        assert cr.count_cases() == 120

    def test_cases_endpoint_returns_pagination_metadata(self, resolver, monkeypatch):
        cr, proc = resolver
        _seed(proc, 120); cr._CASE_CACHE.clear()
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        import app.main
        client = TestClient(app.main.app)
        H = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        r = client.get("/cases?limit=50&offset=0", headers=H).json()
        assert r["pagination"]["total"] == 120
        assert r["pagination"]["limit"] == 50
        assert r["pagination"]["has_more"] is True
        assert r["pagination"]["next_offset"] == 50
        assert len(r["cases"]) == 50

    def test_search_is_bounded_and_does_not_build_full_index(self, resolver, monkeypatch):
        cr, proc = resolver
        _seed(proc, 120)
        cr._CASE_CACHE.clear()
        cr._PARTIAL_CASE_CACHE.clear()
        monkeypatch.setenv("MIMIC_CASE_SEARCH_SCAN_LIMIT", "25")

        def fail_full_index(*args, **kwargs):
            raise AssertionError("search should not build the full resolved-case index")

        monkeypatch.setattr(cr, "_get_resolved_cases", fail_full_index)
        page = cr.list_cases(limit=10, offset=0, search="CHEST")
        assert len(page) == 10
        assert cr.count_cases(search="CHEST") == 50
        meta = cr.search_metadata(search="CHEST")
        assert meta["search_bounded"] is True
        assert meta["search_scan_limit"] == 50  # minimum is DEFAULT_PAGE_SIZE
        assert meta["total_is_exact"] is False
        assert meta["search_truncated"] is True

    def test_search_metadata_surfaces_bounded_search(self, resolver, monkeypatch):
        cr, proc = resolver
        _seed(proc, 120)
        cr._CASE_CACHE.clear()
        cr._PARTIAL_CASE_CACHE.clear()
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("MIMIC_CASE_SEARCH_SCAN_LIMIT", "50")
        import app.main
        client = TestClient(app.main.app)
        H = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        r = client.get("/cases?limit=10&q=CHEST", headers=H).json()
        assert r["pagination"]["search_bounded"] is True
        assert r["pagination"]["search_truncated"] is True
        assert r["pagination"]["total_is_exact"] is False
        assert r["pagination"]["search_scan_limit"] == 50


class TestIndexedResolution:
    def test_case_uid_collision_fails_closed(self, resolver, monkeypatch):
        cr, proc = resolver
        _seed(proc, 2)
        monkeypatch.setattr(cr, "pseudonymous_case_uid", lambda *args, **kwargs: "MIMIC-IV-ED-Full-v2.2~collision")
        with pytest.raises(cr.CaseUidCollisionError):
            cr.list_cases(limit=2)

    def test_resolve_uses_cached_index(self, resolver):
        cr, proc = resolver
        _seed(proc, 100); cr._CASE_CACHE.clear(); cr._PARTIAL_CASE_CACHE.clear()
        uid = cr.list_cases(limit=1)[0].case_uid
        rc = cr.resolve(uid)
        assert rc is not None and rc.case_uid == uid
        # Listing a page populates the partial page index, so assessment of the
        # selected case does not force a full-dataset index build.
        assert uid in cr._PARTIAL_CASE_CACHE["mimic_full"]["index"]

    def test_unknown_uid_returns_none(self, resolver):
        cr, proc = resolver
        _seed(proc, 10); cr._CASE_CACHE.clear()
        assert cr.resolve("MIMIC-IV-ED-Full-v2.2~deadbeef") is None
        assert cr.resolve("no-tilde") is None

    def test_cache_invalidates_on_data_change(self, resolver):
        cr, proc = resolver
        _seed(proc, 10); cr._CASE_CACHE.clear()
        assert cr.count_cases() == 10
        _seed(proc, 25)  # rewrite the override file (new mtime/size)
        assert cr.count_cases() == 25  # cache picked up the change

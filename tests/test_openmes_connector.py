"""
tests/test_openmes_connector.py
---------------------------------
Tests for integrations/openmes_connector.py — OpenMES ↔ OpenYield connector.

All network I/O goes through MockTransport; no real HTTP calls are made.

Test organisation
-----------------
1. MockTransport behaviour (routing, 404, POST recording)
2. Response parsers (_parse_lot, _parse_process_step, _parse_work_order)
3. pull_lot
4. pull_lot_history
5. pull_work_orders / pull_work_order
6. push_yield_result
7. sync_lots_to_openyield (DB integration)
8. sync_yield_to_mes (DB integration)
9. get_pushed_results
10. Exception hierarchy
"""

from __future__ import annotations

import pytest

from openyield.integrations.openmes_connector import (
    MockTransport,
    HTTPTransport,
    OpenMESConnector,
    OpenMESError,
    OpenMESNotFoundError,
    OpenMESAuthError,
    MESLot,
    MESWorkOrder,
    MESProcessStep,
    MESYieldResult,
    SyncReport,
    Transport,
    _parse_lot,
    _parse_process_step,
    _parse_work_order,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lot(
    lot_id="L001",
    product_id="CHIP_A",
    substrate="wafer",
    size=25,
    step="LITHO",
    status="active",
) -> MESLot:
    return MESLot(
        lot_id=lot_id, product_id=product_id,
        substrate_type=substrate, lot_size=size,
        current_step=step, status=status,
        created_at="2025-01-15T08:00:00Z",
        attributes={"tool": "DUV_01"},
    )


def _step(
    step_id="S01", lot_id="L001",
    equipment_id="ET_500", recipe_id="ETCH_V3",
    status="completed",
) -> MESProcessStep:
    return MESProcessStep(
        step_id=step_id, lot_id=lot_id,
        equipment_id=equipment_id, recipe_id=recipe_id,
        start_time="2025-01-15T06:00:00Z",
        end_time="2025-01-15T07:30:00Z",
        status=status, parameters={"power": 500, "pressure": 30},
    )


def _wo(
    wo_id="WO001", lot_id="L001",
    product_id="CHIP_A", qty=25,
    due_date="2025-02-01", priority=2,
    status="active",
) -> MESWorkOrder:
    return MESWorkOrder(
        work_order_id=wo_id, lot_id=lot_id,
        product_id=product_id, quantity=qty,
        due_date=due_date, priority=priority, status=status,
    )


@pytest.fixture
def mock() -> MockTransport:
    return MockTransport()


@pytest.fixture
def connector(mock) -> OpenMESConnector:
    return OpenMESConnector(mock)


@pytest.fixture
def db_conn(tmp_path):
    from openyield.db.connection import get_connection
    from openyield.db.schema import initialize_schema
    conn = get_connection(tmp_path / "test.db")
    initialize_schema(conn)
    return conn


# ===========================================================================
# 1. MockTransport routing
# ===========================================================================

class TestMockTransport:
    def test_registered_lot_returned(self, mock):
        mock.register_lot(_lot("L_MT1"))
        resp = mock.get("/api/v1/lots/L_MT1")
        assert resp["lot_id"] == "L_MT1"

    def test_unregistered_lot_raises_404(self, mock):
        with pytest.raises(OpenMESNotFoundError):
            mock.get("/api/v1/lots/GHOST")

    def test_lot_history_returned(self, mock):
        mock.register_lot_history("L_H1", [_step(lot_id="L_H1")])
        resp = mock.get("/api/v1/lots/L_H1/history")
        assert len(resp["steps"]) == 1

    def test_lot_history_missing_raises(self, mock):
        with pytest.raises(OpenMESNotFoundError):
            mock.get("/api/v1/lots/NO_HIST/history")

    def test_work_orders_listed(self, mock):
        mock.register_work_order(_wo("WO_A", status="open"))
        mock.register_work_order(_wo("WO_B", status="active"))
        resp = mock.get("/api/v1/work-orders")
        assert len(resp["work_orders"]) == 2

    def test_work_orders_filtered_by_status(self, mock):
        mock.register_work_order(_wo("WO1", status="open"))
        mock.register_work_order(_wo("WO2", status="completed"))
        resp = mock.get("/api/v1/work-orders", params={"status": "open"})
        assert all(w["status"] == "open" for w in resp["work_orders"])

    def test_post_recorded(self, mock):
        mock.post("/api/v1/yield-results", {"panel_id": "P1", "lot_id": "L1"})
        assert len(mock.posted) == 1
        assert mock.posted[0][0] == "/api/v1/yield-results"

    def test_post_unknown_path_raises(self, mock):
        with pytest.raises(OpenMESNotFoundError):
            mock.post("/api/v1/unknown", {})

    def test_get_unknown_path_raises(self, mock):
        with pytest.raises(OpenMESNotFoundError):
            mock.get("/api/v1/unknown")

    def test_transport_protocol_satisfied(self, mock):
        assert isinstance(mock, Transport)


# ===========================================================================
# 2. Response parsers
# ===========================================================================

class TestParsers:
    def test_parse_lot_full(self):
        data = {
            "lot_id": "L01", "product_id": "CHIP_B",
            "substrate_type": "glass_panel", "lot_size": 50,
            "current_step": "CMP", "status": "hold",
            "created_at": "2025-01-01T00:00:00Z",
            "attributes": {"tool": "AMAT"},
        }
        lot = _parse_lot(data)
        assert lot.lot_id        == "L01"
        assert lot.substrate_type == "glass_panel"
        assert lot.lot_size       == 50
        assert lot.status         == "hold"
        assert lot.attributes["tool"] == "AMAT"

    def test_parse_lot_missing_optional_fields(self):
        lot = _parse_lot({"lot_id": "MINIMAL"})
        assert lot.lot_id        == "MINIMAL"
        assert lot.substrate_type == "wafer"    # default
        assert lot.lot_size       == 0
        assert lot.attributes     == {}

    def test_parse_process_step(self):
        data = {
            "step_id": "S99", "lot_id": "L01",
            "equipment_id": "CMP_03", "recipe_id": "CMP_V2",
            "start_time": "2025-01-02T08:00:00Z",
            "end_time":   "2025-01-02T10:00:00Z",
            "status": "completed",
            "parameters": {"rpm": 80, "slurry": "CMP_SLURRY_A"},
        }
        step = _parse_process_step(data)
        assert step.step_id       == "S99"
        assert step.equipment_id  == "CMP_03"
        assert step.parameters["rpm"] == 80

    def test_parse_work_order_full(self):
        data = {
            "work_order_id": "WO99", "lot_id": "L01",
            "product_id": "CHIP_C", "quantity": 100,
            "due_date": "2025-03-01", "priority": 1,
            "status": "active",
        }
        wo = _parse_work_order(data)
        assert wo.work_order_id == "WO99"
        assert wo.quantity      == 100
        assert wo.priority      == 1

    def test_parse_work_order_defaults(self):
        wo = _parse_work_order({"work_order_id": "WO_MIN"})
        assert wo.quantity == 0
        assert wo.status   == "open"


# ===========================================================================
# 3. pull_lot
# ===========================================================================

class TestPullLot:
    def test_returns_mes_lot(self, connector, mock):
        mock.register_lot(_lot("L_PL"))
        lot = connector.pull_lot("L_PL")
        assert isinstance(lot, MESLot)

    def test_lot_id_preserved(self, connector, mock):
        mock.register_lot(_lot("L_ID"))
        assert connector.pull_lot("L_ID").lot_id == "L_ID"

    def test_substrate_type_preserved(self, connector, mock):
        mock.register_lot(_lot("L_SUB", substrate="glass_panel"))
        assert connector.pull_lot("L_SUB").substrate_type == "glass_panel"

    def test_lot_size_preserved(self, connector, mock):
        mock.register_lot(_lot("L_SZ", size=50))
        assert connector.pull_lot("L_SZ").lot_size == 50

    def test_attributes_preserved(self, connector, mock):
        l = _lot("L_ATTR")
        l.attributes = {"fab": "F8", "layer": "M1"}
        mock.register_lot(l)
        loaded = connector.pull_lot("L_ATTR")
        assert loaded.attributes["fab"] == "F8"

    def test_not_found_raises(self, connector):
        with pytest.raises(OpenMESNotFoundError):
            connector.pull_lot("GHOST")


# ===========================================================================
# 4. pull_lot_history
# ===========================================================================

class TestPullLotHistory:
    def test_returns_list_of_steps(self, connector, mock):
        mock.register_lot_history("L_HX", [_step(lot_id="L_HX")])
        steps = connector.pull_lot_history("L_HX")
        assert isinstance(steps, list)
        assert isinstance(steps[0], MESProcessStep)

    def test_step_count(self, connector, mock):
        steps = [_step(step_id=f"S{i}", lot_id="L_SC") for i in range(4)]
        mock.register_lot_history("L_SC", steps)
        pulled = connector.pull_lot_history("L_SC")
        assert len(pulled) == 4

    def test_equipment_id_preserved(self, connector, mock):
        mock.register_lot_history("L_EQ", [_step(equipment_id="DRY_02", lot_id="L_EQ")])
        assert connector.pull_lot_history("L_EQ")[0].equipment_id == "DRY_02"

    def test_parameters_preserved(self, connector, mock):
        s = _step(lot_id="L_PARAM")
        s.parameters = {"temp": 350, "gas": "CF4"}
        mock.register_lot_history("L_PARAM", [s])
        pulled = connector.pull_lot_history("L_PARAM")[0]
        assert pulled.parameters["temp"] == 350

    def test_empty_history_returns_empty_list(self, connector, mock):
        mock.register_lot_history("L_EMPTY", [])
        assert connector.pull_lot_history("L_EMPTY") == []

    def test_not_found_raises(self, connector):
        with pytest.raises(OpenMESNotFoundError):
            connector.pull_lot_history("NO_HIST")


# ===========================================================================
# 5. pull_work_orders / pull_work_order
# ===========================================================================

class TestPullWorkOrders:
    def test_returns_list(self, connector, mock):
        mock.register_work_order(_wo("WO_L1"))
        result = connector.pull_work_orders()
        assert isinstance(result, list)

    def test_all_orders_returned(self, connector, mock):
        for i in range(3):
            mock.register_work_order(_wo(f"WO_{i}"))
        assert len(connector.pull_work_orders()) == 3

    def test_status_filter(self, connector, mock):
        mock.register_work_order(_wo("WO_OPN", status="open"))
        mock.register_work_order(_wo("WO_CMP", status="completed"))
        active = connector.pull_work_orders(status="open")
        assert all(w.status == "open" for w in active)
        assert len(active) == 1

    def test_pull_single_work_order(self, connector, mock):
        mock.register_work_order(_wo("WO_SNG", lot_id="L_SNG"))
        wo = connector.pull_work_order("WO_SNG")
        assert isinstance(wo, MESWorkOrder)
        assert wo.lot_id == "L_SNG"

    def test_missing_work_order_raises(self, connector):
        with pytest.raises(OpenMESNotFoundError):
            connector.pull_work_order("GHOST_WO")


# ===========================================================================
# 6. push_yield_result
# ===========================================================================

class TestPushYieldResult:
    def _result(self, panel_id="P1", lot_id="L1") -> MESYieldResult:
        return MESYieldResult(
            panel_id=panel_id, lot_id=lot_id,
            yield_poisson=0.78, yield_murphy=0.82, yield_negbinom=0.80,
            defect_count=5,
        )

    def test_push_returns_true(self, connector):
        assert connector.push_yield_result(self._result()) is True

    def test_post_body_contains_panel_id(self, connector, mock):
        connector.push_yield_result(self._result(panel_id="P_BODY"))
        _, payload = mock.posted[-1]
        assert payload["panel_id"] == "P_BODY"

    def test_post_body_contains_lot_id(self, connector, mock):
        connector.push_yield_result(self._result(lot_id="L_BODY"))
        _, payload = mock.posted[-1]
        assert payload["lot_id"] == "L_BODY"

    def test_yield_values_in_payload(self, connector, mock):
        r = MESYieldResult(
            panel_id="P", lot_id="L",
            yield_poisson=0.75, yield_murphy=0.78, yield_negbinom=0.76,
            defect_count=3,
        )
        connector.push_yield_result(r)
        _, payload = mock.posted[-1]
        assert payload["yield_poisson"]  == pytest.approx(0.75, rel=1e-5)
        assert payload["yield_murphy"]   == pytest.approx(0.78, rel=1e-5)
        assert payload["yield_negbinom"] == pytest.approx(0.76, rel=1e-5)

    def test_defect_count_in_payload(self, connector, mock):
        r = self._result()
        r.defect_count = 99
        connector.push_yield_result(r)
        assert mock.posted[-1][1]["defect_count"] == 99

    def test_reported_at_auto_filled(self):
        r = MESYieldResult(
            panel_id="P", lot_id="L",
            yield_poisson=0.9, yield_murphy=0.9, yield_negbinom=0.9,
            defect_count=0,
        )
        assert r.reported_at != ""


# ===========================================================================
# 7. sync_lots_to_openyield
# ===========================================================================

class TestSyncLotsToDB:
    def test_panel_created_in_db(self, connector, mock, db_conn):
        mock.register_lot(_lot("L_SYNC"))
        report = connector.sync_lots_to_openyield(db_conn, ["L_SYNC"])
        row = db_conn.execute(
            "SELECT panel_id FROM panels WHERE panel_id = 'L_SYNC'"
        ).fetchone()
        assert row is not None

    def test_report_lots_synced(self, connector, mock, db_conn):
        for i in range(3):
            mock.register_lot(_lot(f"L_REP_{i}"))
        report = connector.sync_lots_to_openyield(db_conn, [f"L_REP_{i}" for i in range(3)])
        assert report.lots_synced == 3

    def test_report_panels_created(self, connector, mock, db_conn):
        mock.register_lot(_lot("L_PC"))
        report = connector.sync_lots_to_openyield(db_conn, ["L_PC"])
        assert report.panels_created == 1

    def test_missing_lot_recorded_in_errors(self, connector, db_conn):
        report = connector.sync_lots_to_openyield(db_conn, ["GHOST_LOT"])
        assert len(report.errors) == 1
        assert report.errors[0][0] == "GHOST_LOT"

    def test_partial_failure_continues(self, connector, mock, db_conn):
        mock.register_lot(_lot("L_GOOD"))
        report = connector.sync_lots_to_openyield(db_conn, ["GHOST", "L_GOOD"])
        assert report.lots_synced == 1
        assert len(report.errors) == 1

    def test_substrate_type_override(self, connector, mock, db_conn):
        mock.register_lot(_lot("L_OVR", substrate="wafer"))
        connector.sync_lots_to_openyield(
            db_conn, ["L_OVR"], substrate_type="glass_panel"
        )
        row = db_conn.execute(
            "SELECT substrate_type FROM panels WHERE panel_id='L_OVR'"
        ).fetchone()
        assert row["substrate_type"] == "glass_panel"

    def test_idempotent_double_sync(self, connector, mock, db_conn):
        mock.register_lot(_lot("L_IDEM"))
        connector.sync_lots_to_openyield(db_conn, ["L_IDEM"])
        connector.sync_lots_to_openyield(db_conn, ["L_IDEM"])
        n = db_conn.execute(
            "SELECT COUNT(*) AS n FROM panels WHERE panel_id='L_IDEM'"
        ).fetchone()["n"]
        assert n == 1

    def test_returns_sync_report(self, connector, mock, db_conn):
        mock.register_lot(_lot("L_TYPE"))
        result = connector.sync_lots_to_openyield(db_conn, ["L_TYPE"])
        assert isinstance(result, SyncReport)

    def test_empty_lot_list_returns_empty_report(self, connector, db_conn):
        report = connector.sync_lots_to_openyield(db_conn, [])
        assert report.lots_synced == 0
        assert report.errors == []


# ===========================================================================
# 8. sync_yield_to_mes
# ===========================================================================

class TestSyncYieldToMES:
    def _setup_yield(self, db_conn, panel_id: str, lot_id: str, y: float):
        db_conn.execute(
            "INSERT OR IGNORE INTO panels "
            "(panel_id, substrate_type, rows, cols, lot_id, "
            " component_pitch_mm, product_type) VALUES (?,?,?,?,?,?,?)",
            (panel_id, "wafer", 1, 1, lot_id, 28.0, "TEST"),
        )
        db_conn.execute("""
            CREATE TABLE IF NOT EXISTS yield_estimates (
                panel_id       TEXT PRIMARY KEY,
                yield_poisson  REAL,
                yield_murphy   REAL,
                yield_negbinom REAL
            )
        """)
        db_conn.execute(
            "INSERT OR REPLACE INTO yield_estimates "
            "(panel_id, yield_poisson, yield_murphy, yield_negbinom) "
            "VALUES (?,?,?,?)",
            (panel_id, y, y * 1.01, y * 0.99),
        )
        db_conn.commit()

    def test_yields_pushed_count(self, connector, mock, db_conn):
        for i in range(3):
            self._setup_yield(db_conn, f"PNL_{i}", "LOT_PUSH", 0.85 - i * 0.05)
        report = connector.sync_yield_to_mes(db_conn, "LOT_PUSH")
        assert report.yields_pushed == 3

    def test_post_called_per_panel(self, connector, mock, db_conn):
        for i in range(2):
            self._setup_yield(db_conn, f"PX_{i}", "LOT_POST", 0.80)
        connector.sync_yield_to_mes(db_conn, "LOT_POST")
        posted_paths = [p for p, _ in mock.posted]
        assert posted_paths.count("/api/v1/yield-results") == 2

    def test_no_yield_data_error(self, connector, mock, db_conn):
        report = connector.sync_yield_to_mes(db_conn, "EMPTY_LOT")
        assert len(report.errors) > 0

    def test_lot_id_in_payload(self, connector, mock, db_conn):
        self._setup_yield(db_conn, "PNL_LID", "LOT_LID", 0.9)
        connector.sync_yield_to_mes(db_conn, "LOT_LID")
        _, payload = mock.posted[-1]
        assert payload["lot_id"] == "LOT_LID"

    def test_returns_sync_report(self, connector, mock, db_conn):
        self._setup_yield(db_conn, "PNL_RT", "LOT_RT", 0.88)
        result = connector.sync_yield_to_mes(db_conn, "LOT_RT")
        assert isinstance(result, SyncReport)


# ===========================================================================
# 9. get_pushed_results
# ===========================================================================

class TestGetPushedResults:
    def test_returns_pushed_results(self, connector, mock):
        r = MESYieldResult(
            panel_id="P1", lot_id="L_GPR",
            yield_poisson=0.80, yield_murphy=0.82, yield_negbinom=0.81,
            defect_count=4,
        )
        connector.push_yield_result(r)
        results = connector.get_pushed_results("L_GPR")
        assert len(results) == 1
        assert results[0].panel_id == "P1"

    def test_multiple_results_for_same_lot(self, connector, mock):
        for i in range(3):
            r = MESYieldResult(
                panel_id=f"PM_{i}", lot_id="L_MULTI",
                yield_poisson=0.8, yield_murphy=0.82, yield_negbinom=0.81,
                defect_count=i,
            )
            connector.push_yield_result(r)
        results = connector.get_pushed_results("L_MULTI")
        assert len(results) == 3

    def test_returns_empty_for_unknown_lot(self, connector, mock):
        results = connector.get_pushed_results("NEVER_PUSHED")
        assert results == []

    def test_result_fields_preserved(self, connector, mock):
        r = MESYieldResult(
            panel_id="P_FLD", lot_id="L_FLD",
            yield_negbinom=0.776, yield_poisson=0.71, yield_murphy=0.79,
            defect_count=7,
        )
        connector.push_yield_result(r)
        fetched = connector.get_pushed_results("L_FLD")[0]
        assert fetched.yield_negbinom == pytest.approx(0.776, rel=1e-4)
        assert fetched.defect_count   == 7


# ===========================================================================
# 10. Exception hierarchy
# ===========================================================================

class TestExceptions:
    def test_not_found_is_subclass_of_openmes_error(self):
        assert issubclass(OpenMESNotFoundError, OpenMESError)

    def test_auth_error_is_subclass_of_openmes_error(self):
        assert issubclass(OpenMESAuthError, OpenMESError)

    def test_error_carries_status_code(self):
        exc = OpenMESError("oops", status_code=503)
        assert exc.status_code == 503

    def test_not_found_carries_status_code(self):
        exc = OpenMESNotFoundError("not found", 404)
        assert exc.status_code == 404

    def test_http_transport_is_transport_protocol(self):
        t = HTTPTransport("http://localhost:8080")
        assert isinstance(t, Transport)

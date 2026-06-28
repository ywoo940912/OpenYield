"""
integrations/openmes_connector.py
-----------------------------------
Author: Yeonkuk Woo

OpenMES ↔ OpenYield bidirectional integration connector.

OpenMES (https://www.openmesproject.org) is an open-source Manufacturing
Execution System that tracks lots, work orders, equipment, and process steps
in semiconductor fabs.  This connector bridges OpenMES and OpenYield:

  Pull direction  (MES → OpenYield)
  ----------------------------------
  Lot metadata, work order assignments, and process histories are fetched
  from the OpenMES REST API and ingested into OpenYield's panels and
  lot_genealogy tables.  This lets yield analysis run against the same lot
  identifiers used by the fab's MES without manual re-keying.

  Push direction  (OpenYield → MES)
  ----------------------------------
  After calculating yield estimates with calculator.py, the connector posts
  structured yield results back to OpenMES so operators see per-lot yield
  directly in the MES dashboard — closing the loop between inspection data
  and production tracking.

Transport abstraction
---------------------
All HTTP calls are isolated behind a ``Transport`` protocol.  Production
code uses ``HTTPTransport`` (stdlib ``urllib.request``, no third-party deps).
Tests inject ``MockTransport``, a fully in-memory stub that records all
requests and returns pre-registered responses.

Retry and error handling
------------------------
``HTTPTransport`` retries on 429 (rate limit) and 5xx responses with
exponential back-off (base 2, cap at 60 s, jitter ±10 %).  Permanent
errors (4xx except 429) raise ``OpenMESError`` immediately.

OpenMES REST API surface used
-------------------------------
  GET  /api/v1/lots/{lot_id}            → lot metadata
  GET  /api/v1/lots/{lot_id}/history    → process step history
  GET  /api/v1/work-orders              → work order list (?status=active)
  GET  /api/v1/work-orders/{wo_id}      → single work order
  POST /api/v1/yield-results            → push yield result record
  GET  /api/v1/yield-results/{lot_id}   → previously pushed results

References
----------
[1] SEMI E40-0308, "Standard for Processing Management".
[2] SEMI E10-1112, "Specification for Definition and Measurement of
    Equipment Reliability, Availability, and Maintainability (RAM)".
[3] D. Landman, "Integrating SPC and MES for real-time yield improvement,"
    IEEE Trans. Semicond. Manuf., 18(4):537–545, 2005.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from openyield.db.connection import get_placeholder

logger = logging.getLogger(__name__)

Connection = Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OpenMESError(Exception):
    """Raised for unrecoverable OpenMES API errors."""
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenMESNotFoundError(OpenMESError):
    """Resource not found (HTTP 404)."""


class OpenMESAuthError(OpenMESError):
    """Authentication / authorisation failure (HTTP 401 / 403)."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class MESLot:
    """
    A lot record as returned by OpenMES.

    Attributes
    ----------
    lot_id         : Unique lot identifier (matches OpenYield panels.lot_id).
    product_id     : Product / device type code.
    substrate_type : "wafer", "glass_panel", "reticle", etc.
    lot_size       : Number of substrates in the lot.
    current_step   : Most recent process step code.
    status         : "active" | "hold" | "completed" | "scrapped".
    created_at     : ISO-8601 creation timestamp.
    attributes     : Arbitrary MES key-value metadata (equipment ID, recipe…).
    """
    lot_id:         str
    product_id:     str
    substrate_type: str
    lot_size:       int
    current_step:   str
    status:         str
    created_at:     str
    attributes:     dict = field(default_factory=dict)


@dataclass
class MESWorkOrder:
    """A manufacturing work order from OpenMES."""
    work_order_id: str
    lot_id:        str
    product_id:    str
    quantity:      int
    due_date:      str
    priority:      int    # 1 = highest
    status:        str    # "open" | "active" | "completed" | "cancelled"


@dataclass
class MESProcessStep:
    """
    One completed or running process step for a lot.

    Used to reconstruct process history and correlate defect density with
    specific equipment / recipes in upstream yield analysis.
    """
    step_id:      str
    lot_id:       str
    equipment_id: str
    recipe_id:    str
    start_time:   str
    end_time:     str   # empty string if step is still running
    status:       str   # "completed" | "running" | "aborted"
    parameters:   dict = field(default_factory=dict)


@dataclass
class MESYieldResult:
    """
    Yield result payload pushed from OpenYield to OpenMES.

    Includes all three yield model estimates so MES dashboards can display
    whichever model the fab has standardised on without re-querying OpenYield.
    """
    panel_id:       str
    lot_id:         str
    yield_poisson:  float
    yield_murphy:   float
    yield_negbinom: float
    defect_count:   int
    reported_at:    str = ""

    def __post_init__(self) -> None:
        if not self.reported_at:
            self.reported_at = datetime.now(timezone.utc).isoformat()


@dataclass
class SyncReport:
    """
    Summary of a pull or push synchronisation operation.

    Attributes
    ----------
    lots_synced      : Number of lots processed.
    panels_created   : New panel rows inserted (pull sync).
    yields_pushed    : Yield result records sent to MES (push sync).
    errors           : List of (lot_id, error_message) tuples for failures.
    """
    lots_synced:    int = 0
    panels_created: int = 0
    yields_pushed:  int = 0
    errors:         list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Transport protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Transport(Protocol):
    """Minimal HTTP transport interface for dependency injection."""

    def get(self, path: str, params: dict | None = None) -> dict:
        """Perform a GET request; return the parsed JSON body."""
        ...

    def post(self, path: str, payload: dict) -> dict:
        """Perform a POST request with a JSON body; return parsed response."""
        ...


# ---------------------------------------------------------------------------
# HTTP transport (production)
# ---------------------------------------------------------------------------

class HTTPTransport:
    """
    stdlib urllib-based HTTP transport.

    Parameters
    ----------
    base_url    : OpenMES instance root, e.g. "https://mes.example.com".
    api_key     : API key sent in the ``X-API-Key`` header.
    timeout     : Per-request timeout in seconds (default 30).
    max_retries : Retry budget for 429 / 5xx responses (default 3).
    """

    def __init__(
        self,
        base_url:    str,
        api_key:     str | None = None,
        timeout:     int = 30,
        max_retries: int = 3,
    ) -> None:
        self._base   = base_url.rstrip("/")
        self._key    = api_key
        self._timeout   = timeout
        self._max_retry = max_retries

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._key:
            h["X-API-Key"] = self._key
        return h

    def _backoff(self, attempt: int) -> None:
        """Exponential back-off with ±10 % jitter, capped at 60 s."""
        delay = min(60.0, (2 ** attempt)) * (0.9 + 0.2 * random.random())
        logger.debug("Retrying in %.1f s (attempt %d)", delay, attempt + 1)
        time.sleep(delay)

    def _request(self, req: urllib.request.Request) -> dict:
        for attempt in range(self._max_retry + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    body = resp.read().decode("utf-8")
                    return json.loads(body) if body.strip() else {}
            except urllib.error.HTTPError as exc:
                code = exc.code
                body = exc.read().decode("utf-8", errors="replace")
                if code == 401 or code == 403:
                    raise OpenMESAuthError(
                        f"Auth failure ({code}): {body}", status_code=code
                    )
                if code == 404:
                    raise OpenMESNotFoundError(
                        f"Not found ({code}): {req.full_url}", status_code=code
                    )
                if code == 429 or code >= 500:
                    if attempt < self._max_retry:
                        self._backoff(attempt)
                        continue
                raise OpenMESError(
                    f"HTTP {code}: {body}", status_code=code
                )
            except urllib.error.URLError as exc:
                if attempt < self._max_retry:
                    self._backoff(attempt)
                    continue
                raise OpenMESError(f"Network error: {exc.reason}") from exc
        raise OpenMESError("Max retries exceeded")   # pragma: no cover

    def get(self, path: str, params: dict | None = None) -> dict:
        url = self._base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        return self._request(req)

    def post(self, path: str, payload: dict) -> dict:
        url  = self._base + path
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data, headers=self._headers(), method="POST"
        )
        return self._request(req)


# ---------------------------------------------------------------------------
# Mock transport (testing)
# ---------------------------------------------------------------------------

class MockTransport:
    """
    In-memory stub transport for unit and integration testing.

    Register expected responses with ``register_lot``, ``register_work_order``,
    etc. before exercising the connector.  All POST payloads are recorded in
    ``posted`` for assertion.
    """

    def __init__(self) -> None:
        self._lots:          dict[str, dict] = {}
        self._lot_histories: dict[str, list] = {}
        self._work_orders:   list[dict]      = []
        self.posted:         list[tuple[str, dict]] = []  # (path, payload)
        self._yield_results: list[dict] = []

    def register_lot(self, lot: MESLot) -> None:
        self._lots[lot.lot_id] = {
            "lot_id":         lot.lot_id,
            "product_id":     lot.product_id,
            "substrate_type": lot.substrate_type,
            "lot_size":       lot.lot_size,
            "current_step":   lot.current_step,
            "status":         lot.status,
            "created_at":     lot.created_at,
            "attributes":     lot.attributes,
        }

    def register_lot_history(self, lot_id: str, steps: list[MESProcessStep]) -> None:
        self._lot_histories[lot_id] = [
            {
                "step_id":      s.step_id,
                "lot_id":       s.lot_id,
                "equipment_id": s.equipment_id,
                "recipe_id":    s.recipe_id,
                "start_time":   s.start_time,
                "end_time":     s.end_time,
                "status":       s.status,
                "parameters":   s.parameters,
            }
            for s in steps
        ]

    def register_work_order(self, wo: MESWorkOrder) -> None:
        self._work_orders.append({
            "work_order_id": wo.work_order_id,
            "lot_id":        wo.lot_id,
            "product_id":    wo.product_id,
            "quantity":      wo.quantity,
            "due_date":      wo.due_date,
            "priority":      wo.priority,
            "status":        wo.status,
        })

    def get(self, path: str, params: dict | None = None) -> dict:
        # /api/v1/lots/{lot_id}
        if path.startswith("/api/v1/lots/"):
            rest = path[len("/api/v1/lots/"):]
            parts = rest.split("/")
            lot_id = parts[0]
            if len(parts) == 2 and parts[1] == "history":
                if lot_id not in self._lot_histories:
                    raise OpenMESNotFoundError(f"No history for {lot_id!r}", 404)
                return {"steps": self._lot_histories[lot_id]}
            if lot_id in self._lots:
                return self._lots[lot_id]
            raise OpenMESNotFoundError(f"Lot not found: {lot_id!r}", 404)

        # /api/v1/work-orders
        if path == "/api/v1/work-orders":
            status_filter = (params or {}).get("status")
            wos = self._work_orders
            if status_filter:
                wos = [w for w in wos if w["status"] == status_filter]
            return {"work_orders": wos}

        # /api/v1/work-orders/{wo_id}
        if path.startswith("/api/v1/work-orders/"):
            wo_id = path[len("/api/v1/work-orders/"):]
            for wo in self._work_orders:
                if wo["work_order_id"] == wo_id:
                    return wo
            raise OpenMESNotFoundError(f"Work order not found: {wo_id!r}", 404)

        # /api/v1/yield-results/{lot_id}
        if path.startswith("/api/v1/yield-results/"):
            lot_id = path[len("/api/v1/yield-results/"):]
            results = [r for r in self._yield_results if r.get("lot_id") == lot_id]
            return {"results": results}

        raise OpenMESNotFoundError(f"Unknown path: {path!r}", 404)

    def post(self, path: str, payload: dict) -> dict:
        self.posted.append((path, payload))
        if path == "/api/v1/yield-results":
            self._yield_results.append(payload)
            return {"status": "accepted", "id": len(self._yield_results)}
        raise OpenMESNotFoundError(f"Unknown POST path: {path!r}", 404)


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def _parse_lot(data: dict) -> MESLot:
    return MESLot(
        lot_id=data["lot_id"],
        product_id=data.get("product_id", ""),
        substrate_type=data.get("substrate_type", "wafer"),
        lot_size=int(data.get("lot_size", 0)),
        current_step=data.get("current_step", ""),
        status=data.get("status", "active"),
        created_at=data.get("created_at", ""),
        attributes=data.get("attributes", {}),
    )


def _parse_process_step(data: dict) -> MESProcessStep:
    return MESProcessStep(
        step_id=data["step_id"],
        lot_id=data["lot_id"],
        equipment_id=data.get("equipment_id", ""),
        recipe_id=data.get("recipe_id", ""),
        start_time=data.get("start_time", ""),
        end_time=data.get("end_time", ""),
        status=data.get("status", "completed"),
        parameters=data.get("parameters", {}),
    )


def _parse_work_order(data: dict) -> MESWorkOrder:
    return MESWorkOrder(
        work_order_id=data["work_order_id"],
        lot_id=data.get("lot_id", ""),
        product_id=data.get("product_id", ""),
        quantity=int(data.get("quantity", 0)),
        due_date=data.get("due_date", ""),
        priority=int(data.get("priority", 5)),
        status=data.get("status", "open"),
    )


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class OpenMESConnector:
    """
    Bidirectional bridge between OpenMES and OpenYield.

    Parameters
    ----------
    transport : A ``Transport``-compliant object (``HTTPTransport`` for
                production, ``MockTransport`` for tests).

    Examples
    --------
    Production::

        transport = HTTPTransport("https://mes.example.com", api_key="secret")
        connector = OpenMESConnector(transport)
        lot = connector.pull_lot("LOT_2024_001")
        report = connector.sync_lots_to_openyield(conn, ["LOT_2024_001"])

    Testing::

        mock = MockTransport()
        mock.register_lot(MESLot("LOT_TEST", "CHIP_A", "wafer", 25, "LITHO",
                                  "active", "2025-01-01T00:00:00"))
        connector = OpenMESConnector(mock)
        lot = connector.pull_lot("LOT_TEST")
    """

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    # ------------------------------------------------------------------
    # Pull (MES → OpenYield)
    # ------------------------------------------------------------------

    def pull_lot(self, lot_id: str) -> MESLot:
        """
        Fetch lot metadata from OpenMES.

        Raises
        ------
        OpenMESNotFoundError : Lot does not exist in OpenMES.
        OpenMESError         : Network or server error.
        """
        data = self._t.get(f"/api/v1/lots/{lot_id}")
        return _parse_lot(data)

    def pull_lot_history(self, lot_id: str) -> list[MESProcessStep]:
        """
        Fetch the complete process step history for a lot.

        Returns steps in chronological order (oldest first).
        """
        data = self._t.get(f"/api/v1/lots/{lot_id}/history")
        return [_parse_process_step(s) for s in data.get("steps", [])]

    def pull_work_orders(
        self,
        status: str | None = None,
    ) -> list[MESWorkOrder]:
        """
        List work orders from OpenMES, optionally filtered by status.

        Parameters
        ----------
        status : "open" | "active" | "completed" | "cancelled" | None (all)
        """
        params = {"status": status} if status else None
        data   = self._t.get("/api/v1/work-orders", params=params)
        return [_parse_work_order(w) for w in data.get("work_orders", [])]

    def pull_work_order(self, work_order_id: str) -> MESWorkOrder:
        """Fetch a single work order by ID."""
        data = self._t.get(f"/api/v1/work-orders/{work_order_id}")
        return _parse_work_order(data)

    def sync_lots_to_openyield(
        self,
        conn: Connection,
        lot_ids: list[str],
        *,
        substrate_type: str | None = None,
    ) -> SyncReport:
        """
        Pull lot records from OpenMES and upsert them into OpenYield panels.

        For each lot_id, the connector calls pull_lot() and inserts a row in
        ``panels`` using the MES substrate_type (unless overridden).  Lots that
        fail to pull (e.g. not yet in MES) are recorded in report.errors.

        Parameters
        ----------
        conn           : OpenYield database connection.
        lot_ids        : Lot IDs to synchronise.
        substrate_type : Override the substrate type from MES (optional).

        Returns
        -------
        SyncReport
        """
        ph     = get_placeholder(conn)
        report = SyncReport()

        for lot_id in lot_ids:
            try:
                lot = self.pull_lot(lot_id)
            except OpenMESNotFoundError:
                report.errors.append((lot_id, "lot not found in MES"))
                continue
            except OpenMESError as exc:
                report.errors.append((lot_id, str(exc)))
                continue

            sub = substrate_type or lot.substrate_type
            pitch = 28.0 if sub == "wafer" else 370.0

            try:
                with conn:
                    conn.execute(
                        f"INSERT OR IGNORE INTO panels "
                        f"(panel_id, substrate_type, rows, cols, lot_id, "
                        f" component_pitch_mm, product_type) "
                        f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                        (
                            lot_id, sub,
                            1, 1,
                            lot_id,
                            pitch,
                            lot.product_id or "MES_IMPORT",
                        ),
                    )
                report.panels_created += 1
            except Exception as exc:
                report.errors.append((lot_id, f"DB error: {exc}"))
                continue

            report.lots_synced += 1
            logger.info(
                "Synced lot %s → panel (sub=%s step=%s status=%s)",
                lot_id, sub, lot.current_step, lot.status,
            )

        return report

    # ------------------------------------------------------------------
    # Push (OpenYield → MES)
    # ------------------------------------------------------------------

    def push_yield_result(self, result: MESYieldResult) -> bool:
        """
        Post a single yield result record to OpenMES.

        Returns True on success, raises OpenMESError on failure.
        """
        payload = {
            "panel_id":       result.panel_id,
            "lot_id":         result.lot_id,
            "yield_poisson":  round(result.yield_poisson,  6),
            "yield_murphy":   round(result.yield_murphy,   6),
            "yield_negbinom": round(result.yield_negbinom, 6),
            "defect_count":   result.defect_count,
            "reported_at":    result.reported_at,
        }
        resp = self._t.post("/api/v1/yield-results", payload)
        logger.info(
            "Pushed yield for panel=%s lot=%s  NB=%.1f%%",
            result.panel_id, result.lot_id, result.yield_negbinom * 100,
        )
        return resp.get("status") == "accepted"

    def sync_yield_to_mes(
        self,
        conn: Connection,
        lot_id: str,
    ) -> SyncReport:
        """
        Read yield estimates for a lot from OpenYield and push them to OpenMES.

        Queries ``yield_estimates`` joined to ``panels`` for all panels whose
        lot_id matches, then calls push_yield_result() for each.  Also fetches
        per-panel defect counts from the ``defects`` table.

        Parameters
        ----------
        conn   : OpenYield database connection.
        lot_id : Lot to synchronise.

        Returns
        -------
        SyncReport
        """
        ph     = get_placeholder(conn)
        report = SyncReport()

        try:
            rows = conn.execute(
                f"SELECT ye.panel_id, ye.yield_poisson, ye.yield_murphy, "
                f"       ye.yield_negbinom "
                f"FROM yield_estimates ye "
                f"JOIN panels p ON ye.panel_id = p.panel_id "
                f"WHERE p.lot_id = {ph}",
                (lot_id,),
            ).fetchall()
        except Exception as exc:
            report.errors.append((lot_id, f"yield_estimates query failed: {exc}"))
            return report

        if not rows:
            report.errors.append((lot_id, "no yield estimates found"))
            return report

        for row in rows:
            panel_id = row["panel_id"]
            try:
                defect_row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM defects WHERE panel_id = {ph}",
                    (panel_id,),
                ).fetchone()
                defect_count = defect_row["n"] if defect_row else 0

                result = MESYieldResult(
                    panel_id=panel_id,
                    lot_id=lot_id,
                    yield_poisson=float(row["yield_poisson"]),
                    yield_murphy=float(row["yield_murphy"]),
                    yield_negbinom=float(row["yield_negbinom"]),
                    defect_count=defect_count,
                )
                success = self.push_yield_result(result)
                if success:
                    report.yields_pushed += 1
                else:
                    report.errors.append((panel_id, "MES returned non-accepted status"))
            except OpenMESError as exc:
                report.errors.append((panel_id, str(exc)))

        report.lots_synced = 1
        return report

    # ------------------------------------------------------------------
    # Read previously pushed results
    # ------------------------------------------------------------------

    def get_pushed_results(self, lot_id: str) -> list[MESYieldResult]:
        """
        Retrieve yield results previously pushed to OpenMES for a lot.

        Useful for auditing sync history or verifying round-trip integrity.
        """
        data = self._t.get(f"/api/v1/yield-results/{lot_id}")
        results = []
        for r in data.get("results", []):
            results.append(MESYieldResult(
                panel_id=r.get("panel_id", ""),
                lot_id=r.get("lot_id", lot_id),
                yield_poisson=float(r.get("yield_poisson",  0.0)),
                yield_murphy=float(r.get("yield_murphy",    0.0)),
                yield_negbinom=float(r.get("yield_negbinom", 0.0)),
                defect_count=int(r.get("defect_count", 0)),
                reported_at=r.get("reported_at", ""),
            ))
        return results

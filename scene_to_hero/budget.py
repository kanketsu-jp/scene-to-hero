from __future__ import annotations

import json
import math
import threading
from datetime import UTC, datetime
from pathlib import Path


class BudgetExceeded(Exception):
    """Raised when a generation cannot be reserved within the budget."""


_LOCK = threading.Lock()


def billed_seconds(duration: float) -> int:
    return math.ceil(duration)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Budget:
    def __init__(self, log_path: Path, max_usd: float, batch: str = "default"):
        self.log_path = Path(log_path)
        self.max_usd = float(max_usd)
        self.batch = batch

    def _total_unlocked(self) -> float:
        if not self.log_path.exists():
            return 0.0
        total = 0.0
        try:
            with self.log_path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if row.get("kind") == "estimate":
                            value = row.get("est_usd")
                            if isinstance(value, bool):
                                continue
                            total += float(value)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
        except OSError:
            return 0.0
        return total

    def total(self) -> float:
        with _LOCK:
            return self._total_unlocked()

    def _append(self, row: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def reserve(
        self,
        step: str,
        endpoint: str,
        unit_price: float,
        unit: str,
        quantity: float,
        attempt: int = 1,
    ) -> tuple[bool, float, float]:
        est = round(float(unit_price) * float(quantity), 6)
        with _LOCK:
            cumulative = self._total_unlocked()
            if cumulative + est > self.max_usd + 1e-9:
                return False, cumulative, est
            row = {
                "kind": "estimate",
                "batch": self.batch,
                "attempt": attempt,
                "ts": _now(),
                "step": step,
                "endpoint": endpoint,
                "unit_price_usd": float(unit_price),
                "unit": unit,
                "quantity": float(quantity),
                "est_usd": est,
                "cumulative_est_usd": round(cumulative + est, 6),
            }
            self._append(row)
            return True, cumulative, est

    def record_result(
        self,
        step,
        endpoint,
        attempt,
        request_id,
        success,
        output_path=None,
        error=None,
        api_called=True,
    ) -> None:
        row = {
            "kind": "result",
            "batch": self.batch,
            "attempt": attempt,
            "ts": _now(),
            "step": step,
            "endpoint": endpoint,
            "request_id": request_id,
            "success": bool(success),
            "api_called": bool(api_called),
        }
        if output_path is not None:
            row["output_path"] = str(output_path)
        if error is not None:
            row["error"] = str(error)[:500]
        with _LOCK:
            self._append(row)

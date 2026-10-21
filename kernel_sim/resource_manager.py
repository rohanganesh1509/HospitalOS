"""HospitalOS Doctor & Clinical Resource Manager.

Models physical hospital inventories (Doctors, ICU Beds, Ventilators, Monitors,
Scanners, Operating Theatres) with allocation, blocking, and wake queues.
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, Callable
from contracts import HospitalResource, HospitalProcess
from .event_bus import KernelEventBus


class HospitalResourceManager:
    """Subsystem managing shared hospital clinical and diagnostic resources."""

    def __init__(self, event_bus: KernelEventBus, process_manager: Optional[Any] = None):
        self._event_bus = event_bus
        self._process_manager = process_manager
        self._resources: Dict[str, HospitalResource] = {}
        self.reset()

    def set_process_manager(self, process_manager: Any):
        self._process_manager = process_manager

    @property
    def resources(self) -> Dict[str, HospitalResource]:
        return self._resources

    def reset(self):
        """Restores canonical initial resource inventories."""
        self._resources = {
            "RES_DOC": HospitalResource("RES_DOC", "On-Duty Doctors", total=3, available=3, allocated=0),
            "RES_ICU": HospitalResource("RES_ICU", "ICU Beds", total=6, available=4, allocated=2, holders=[1]),
            "RES_VENT": HospitalResource("RES_VENT", "Ventilators", total=4, available=3, allocated=1, holders=[1]),
            "RES_MONITOR": HospitalResource("RES_MONITOR", "Patient Monitors", total=8, available=5, allocated=3, holders=[1, 2]),
            "RES_SCANNER": HospitalResource("RES_SCANNER", "CT Scanner", total=2, available=2, allocated=0, holders=[]),
            "RES_OT": HospitalResource("RES_OT", "Operating Theatre", total=2, available=1, allocated=1, holders=[2]),
        }

    def normalize_resource_id(self, r_id: str) -> str:
        """Translates user-friendly or lowercase names to canonical resource identifiers."""
        s = str(r_id).lower().strip()
        mapping = {
            "ventilator": "RES_VENT",
            "res_vent": "RES_VENT",
            "icu_bed": "RES_ICU",
            "icu": "RES_ICU",
            "res_icu": "RES_ICU",
            "monitor": "RES_MONITOR",
            "res_monitor": "RES_MONITOR",
            "scanner": "RES_SCANNER",
            "res_scanner": "RES_SCANNER",
            "operating_theatre": "RES_OT",
            "ot": "RES_OT",
            "res_ot": "RES_OT",
            "doctors": "RES_DOC",
            "doctor": "RES_DOC",
            "res_doc": "RES_DOC",
        }
        return mapping.get(s, str(r_id))

    def sync_doctor_counts(self, on_duty: int, busy: int):
        """Updates dynamic on-duty doctor inventory from clinical records."""
        if "RES_DOC" in self._resources:
            self._resources["RES_DOC"].total = max(1, on_duty)
            self._resources["RES_DOC"].allocated = min(self._resources["RES_DOC"].total, busy)
            self._resources["RES_DOC"].available = max(0, self._resources["RES_DOC"].total - self._resources["RES_DOC"].allocated)

    def get_resources(self) -> List[HospitalResource]:
        return list(self._resources.values())

    def request_resource(
        self,
        pid: int,
        resource_id: str,
        units: int = 1,
        proc: Optional[HospitalProcess] = None,
        on_block: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Requests units of a hospital resource for an active process."""
        if proc is not None and getattr(proc, "state", "") == "TERMINATED":
            return {"success": False, "error": f"Process PID-{pid:03d} has TERMINATED. Cannot request resources."}

        norm_id = self.normalize_resource_id(resource_id)
        if norm_id not in self._resources:
            return {"success": False, "error": f"Unknown resource ID '{resource_id}'."}
        resource_id = norm_id
        res = self._resources[resource_id]

        if units < 1:
            return {"success": False, "error": "Request units must be >= 1."}

        if res.available >= units:
            res.available -= units
            res.allocated += units
            if pid not in res.holders:
                res.holders.append(pid)
            if proc is not None:
                proc.assigned_resources[res.name] = proc.assigned_resources.get(res.name, 0) + units
                proc.waiting_on_resource = None
            if self._process_manager:
                assigned = proc.assigned_resources if proc else {res.name: units}
                self._process_manager.set_assigned_resources(pid, assigned)
                self._process_manager.set_waiting_resource(pid, None)

            self._event_bus.record_event(
                event_type="RESOURCE_ALLOC",
                summary=f"Allocated {units} unit(s) of {res.name} to PID-{pid:03d} ({res.available} available)",
                category="RESOURCE",
                payload={"pid": pid, "resource_id": resource_id, "units": units, "available": res.available},
            )
            return {
                "success": True,
                "allocated": True,
                "blocked": False,
                "status": "allocated",
                "message": f"Successfully allocated {units} unit(s) of {res.name} to PID-{pid:03d}.",
                "resource": res.to_dict(),
            }
        else:
            if pid not in res.wait_queue:
                res.wait_queue.append(pid)
            if proc is not None:
                proc.waiting_on_resource = resource_id
            if self._process_manager:
                self._process_manager.set_waiting_resource(pid, resource_id)
            if on_block:
                on_block(f"Blocked waiting for {units} unit(s) of unavailable {res.name}.")

            self._event_bus.record_event(
                event_type="RESOURCE_BLOCK",
                summary=f"PID-{pid:03d} blocked on {res.name} (0 available, queued #{len(res.wait_queue)})",
                category="RESOURCE",
                payload={"pid": pid, "resource_id": resource_id, "units": units, "wait_queue": res.wait_queue},
            )
            return {
                "success": True,
                "allocated": False,
                "blocked": True,
                "status": "blocked",
                "message": f"{res.name} is currently exhausted ({res.available} available). PID-{pid:03d} placed in wait queue (WAITING).",
                "resource": res.to_dict(),
            }

    def release_resource(
        self,
        pid: int,
        resource_id: str,
        units: int = 1,
        proc: Optional[HospitalProcess] = None,
        on_unblock: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Releases units of a hospital resource held by a process."""
        norm_id = self.normalize_resource_id(resource_id)
        if norm_id not in self._resources:
            return {"success": False, "error": f"Unknown resource ID '{resource_id}'."}
        resource_id = norm_id
        res = self._resources[resource_id]

        if proc is not None:
            current_held = proc.assigned_resources.get(res.name, 0)
            released = min(units, current_held)
            proc.assigned_resources[res.name] = max(0, current_held - released)
            if proc.assigned_resources[res.name] == 0:
                del proc.assigned_resources[res.name]
                if pid in res.holders:
                    res.holders.remove(pid)
            if self._process_manager:
                self._process_manager.set_assigned_resources(pid, proc.assigned_resources)
        else:
            released = units
            if pid in res.holders:
                res.holders.remove(pid)

        res.allocated = max(0, res.allocated - released)
        res.available = min(res.total, res.available + released)

        self._event_bus.record_event(
            event_type="RESOURCE_RELEASE",
            summary=f"PID-{pid:03d} released {released} unit(s) of {res.name} ({res.available} now available)",
            category="RESOURCE",
            payload={"pid": pid, "resource_id": resource_id, "released": released, "available": res.available},
        )

        woken_pids = []
        while res.wait_queue and res.available > 0:
            next_pid = res.wait_queue.pop(0)
            res.available -= 1
            res.allocated += 1
            if next_pid not in res.holders:
                res.holders.append(next_pid)
            woken_pids.append(next_pid)

            if self._process_manager:
                w_proc = self._process_manager.get_process(next_pid)
                new_assigned = dict(w_proc.assigned_resources) if w_proc else {}
                new_assigned[res.name] = new_assigned.get(res.name, 0) + 1
                self._process_manager.set_assigned_resources(next_pid, new_assigned)
                self._process_manager.set_waiting_resource(next_pid, None)

            self._event_bus.record_event(
                event_type="RESOURCE_UNBLOCK",
                summary=f"Unblocked PID-{next_pid:03d} after acquiring {res.name} (READY)",
                category="RESOURCE",
                payload={"pid": next_pid, "resource_id": resource_id},
            )

            if on_unblock:
                on_unblock(next_pid, res.name)

        return {
            "success": True,
            "released": released,
            "available": res.available,
            "woken_pids": woken_pids,
            "message": f"Released {released} unit(s) of {res.name}." + (f" Woke PID(s): {woken_pids}." if woken_pids else ""),
            "resource": res.to_dict(),
        }

    def release_all_for_process(self, pid: int, proc: Optional[HospitalProcess] = None):
        """Releases all resources held by a terminating process."""
        for res_id, res in list(self._resources.items()):
            if pid in res.holders:
                self.release_resource(pid, res_id, units=res.allocated, proc=proc)

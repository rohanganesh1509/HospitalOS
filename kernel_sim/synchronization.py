"""HospitalOS Synchronization Subsystem.

Models mutual exclusion locks (Operating Theatre, CT Scanner)
and counting semaphores (ICU Beds) with ownership tracking,
wait queues, and wake-up notifications.
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, Callable
from contracts import SynchronizationPrimitive, SynchronizationState, HospitalProcess
from .event_bus import KernelEventBus


class SynchronizationManager:
    """Subsystem managing mutual exclusion and counting semaphores."""

    def __init__(self, event_bus: KernelEventBus, process_manager: Optional[Any] = None):
        self._event_bus = event_bus
        self._process_manager = process_manager
        self._primitives: Dict[str, SynchronizationPrimitive] = {}
        self.reset()

    def set_process_manager(self, process_manager: Any):
        self._process_manager = process_manager

    @property
    def primitives(self) -> Dict[str, SynchronizationPrimitive]:
        return self._primitives

    def reset(self):
        """Restores canonical initial synchronization primitives."""
        self._primitives = {
            "MUTEX_OT": SynchronizationPrimitive("MUTEX_OT", "Operating Theatre Mutex", "mutex", locked=False, count=1, max_count=1),
            "MUTEX_SCANNER": SynchronizationPrimitive("MUTEX_SCANNER", "CT Scanner Lock", "mutex", locked=False, count=1, max_count=1),
            "SEM_ICU": SynchronizationPrimitive("SEM_ICU", "ICU Bed Counting Semaphore", "semaphore", locked=False, count=3, max_count=3),
        }

    def normalize_primitive_id(self, p_id: str) -> str:
        """Translates user-friendly names to canonical primitive identifiers."""
        s = str(p_id).lower().strip()
        mapping = {
            "mutex_ot": "MUTEX_OT",
            "ot": "MUTEX_OT",
            "mutex_scanner": "MUTEX_SCANNER",
            "scanner": "MUTEX_SCANNER",
            "sem_icu": "SEM_ICU",
            "icu": "SEM_ICU",
        }
        return mapping.get(s, str(p_id))

    def get_synchronization_state(self, recent_events: Optional[List[Dict[str, Any]]] = None) -> SynchronizationState:
        """Returns the current synchronization state across all primitives."""
        events = recent_events if recent_events is not None else [
            {"timestamp": e.timestamp, "primitive": e.payload.get("primitive_id", "LOCK"), "action": e.event_type, "summary": e.summary}
            for e in self._event_bus.get_events(limit=50, category="SYNC")
        ][-10:]
        return SynchronizationState(
            primitives=list(self._primitives.values()),
            recent_events=events,
        )

    def acquire_lock(
        self,
        pid: int,
        primitive_id: str,
        proc: Optional[HospitalProcess] = None,
        on_block: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Attempts to acquire a synchronization primitive (mutex or semaphore)."""
        if proc is not None and getattr(proc, "state", "") == "TERMINATED":
            return {"success": False, "error": f"Process PID-{pid:03d} has TERMINATED. Cannot acquire lock."}

        norm_id = self.normalize_primitive_id(primitive_id)
        if norm_id not in self._primitives:
            return {"success": False, "error": f"Unknown synchronization primitive '{primitive_id}'."}
        primitive_id = norm_id
        prim = self._primitives[primitive_id]

        if prim.primitive_type == "mutex":
            if not prim.locked:
                prim.locked = True
                prim.holder_pid = pid
                if pid not in prim.holders:
                    prim.holders.append(pid)
                if proc is not None:
                    proc.waiting_on_primitive = None
                if self._process_manager:
                    self._process_manager.set_waiting_primitive(pid, None)

                self._event_bus.record_event(
                    event_type="LOCK_ACQUIRE",
                    summary=f"PID-{pid:03d} acquired Mutex: {prim.name}",
                    category="SYNC",
                    payload={"pid": pid, "primitive_id": primitive_id},
                )
                return {
                    "success": True,
                    "acquired": True,
                    "blocked": False,
                    "status": "acquired",
                    "message": f"Acquired Mutex '{prim.name}'.",
                    "primitive": prim.to_dict(),
                }
            else:
                if pid not in prim.wait_queue:
                    prim.wait_queue.append(pid)
                if proc is not None:
                    proc.waiting_on_primitive = primitive_id
                if self._process_manager:
                    self._process_manager.set_waiting_primitive(pid, primitive_id)
                if on_block:
                    on_block(f"Blocked waiting on locked Mutex: {prim.name} (held by PID-{prim.holder_pid:03d})")

                self._event_bus.record_event(
                    event_type="LOCK_BLOCK",
                    summary=f"PID-{pid:03d} blocked on Mutex: {prim.name} (Held by PID-{prim.holder_pid:03d})",
                    category="SYNC",
                    payload={"pid": pid, "primitive_id": primitive_id, "holder": prim.holder_pid},
                )
                return {
                    "success": True,
                    "acquired": False,
                    "blocked": True,
                    "status": "blocked",
                    "message": f"Mutex '{prim.name}' is locked by PID-{prim.holder_pid:03d}. PID-{pid:03d} blocked to WAITING state.",
                    "primitive": prim.to_dict(),
                }
        else:  # Counting semaphore
            if prim.count > 0:
                prim.count -= 1
                if pid not in prim.holders:
                    prim.holders.append(pid)
                if proc is not None:
                    proc.waiting_on_primitive = None
                if self._process_manager:
                    self._process_manager.set_waiting_primitive(pid, None)

                self._event_bus.record_event(
                    event_type="SEMAPHORE_ACQUIRE",
                    summary=f"PID-{pid:03d} acquired Semaphore: {prim.name} ({prim.count} slots left)",
                    category="SYNC",
                    payload={"pid": pid, "primitive_id": primitive_id, "remaining": prim.count},
                )
                return {
                    "success": True,
                    "acquired": True,
                    "blocked": False,
                    "status": "acquired",
                    "message": f"Acquired Semaphore slot on '{prim.name}' ({prim.count} slots left).",
                    "primitive": prim.to_dict(),
                }
            else:
                if pid not in prim.wait_queue:
                    prim.wait_queue.append(pid)
                if proc is not None:
                    proc.waiting_on_primitive = primitive_id
                if self._process_manager:
                    self._process_manager.set_waiting_primitive(pid, primitive_id)
                if on_block:
                    on_block(f"Blocked waiting on Semaphore: {prim.name} (0 slots available)")

                self._event_bus.record_event(
                    event_type="SEMAPHORE_WAIT",
                    summary=f"PID-{pid:03d} blocked on Semaphore: {prim.name} (Capacity 0, queued #{len(prim.wait_queue)})",
                    category="SYNC",
                    payload={"pid": pid, "primitive_id": primitive_id, "wait_queue": prim.wait_queue},
                )
                return {
                    "success": True,
                    "acquired": False,
                    "blocked": True,
                    "status": "blocked",
                    "message": f"Semaphore '{prim.name}' capacity reached (0 slots). PID-{pid:03d} blocked to WAITING state.",
                    "primitive": prim.to_dict(),
                }

    def release_lock(
        self,
        pid: int,
        primitive_id: str,
        proc: Optional[HospitalProcess] = None,
        on_unblock: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Releases a synchronization primitive (mutex or semaphore)."""
        norm_id = self.normalize_primitive_id(primitive_id)
        if norm_id not in self._primitives:
            return {"success": False, "error": f"Unknown synchronization primitive '{primitive_id}'."}
        primitive_id = norm_id
        prim = self._primitives[primitive_id]
        woken_pid = None

        if prim.primitive_type == "mutex":
            if prim.holder_pid != pid:
                return {
                    "success": False,
                    "error": f"Only the lock holder (PID-{prim.holder_pid if prim.holder_pid else 'None'}) can release this mutex."
                }
            self._event_bus.record_event(
                event_type="LOCK_RELEASE",
                summary=f"PID-{pid:03d} released Mutex: {prim.name}",
                category="SYNC",
                payload={"pid": pid, "primitive_id": primitive_id},
            )
            if prim.wait_queue:
                woken_pid = prim.wait_queue.pop(0)
                prim.holder_pid = woken_pid
                prim.holders = [woken_pid]
                if self._process_manager:
                    self._process_manager.set_waiting_primitive(woken_pid, None)

                self._event_bus.record_event(
                    event_type="PROCESS_WAKE",
                    summary=f"PID-{woken_pid:03d} woke up and acquired Mutex: {prim.name} (READY)",
                    category="SYNC",
                    payload={"pid": woken_pid, "primitive_id": primitive_id},
                )
                if on_unblock:
                    on_unblock(woken_pid, prim.name)
            else:
                prim.locked = False
                prim.holder_pid = None
                prim.holders = []
        else:  # Counting semaphore
            if pid in prim.holders:
                prim.holders.remove(pid)
            self._event_bus.record_event(
                event_type="SEMAPHORE_POST",
                summary=f"PID-{pid:03d} released Semaphore slot on {prim.name}",
                category="SYNC",
                payload={"pid": pid, "primitive_id": primitive_id},
            )
            if prim.wait_queue:
                woken_pid = prim.wait_queue.pop(0)
                if woken_pid not in prim.holders:
                    prim.holders.append(woken_pid)
                if self._process_manager:
                    self._process_manager.set_waiting_primitive(woken_pid, None)

                self._event_bus.record_event(
                    event_type="PROCESS_WAKE",
                    summary=f"PID-{woken_pid:03d} woke up and acquired Semaphore: {prim.name} (READY)",
                    category="SYNC",
                    payload={"pid": woken_pid, "primitive_id": primitive_id},
                )
                if on_unblock:
                    on_unblock(woken_pid, prim.name)
            else:
                prim.count = min(prim.max_count, prim.count + 1)

        return {
            "success": True,
            "woken_pid": woken_pid,
            "message": f"Released '{prim.name}'." + (f" Woke PID-{woken_pid:03d}." if woken_pid else ""),
            "primitive": prim.to_dict(),
        }

    def release_all_for_process(self, pid: int):
        """Releases all locks or semaphores held by a terminating process."""
        for prim_id, prim in list(self._primitives.items()):
            if prim.holder_pid == pid or pid in prim.holders:
                self.release_lock(pid, prim_id)

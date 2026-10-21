"""HospitalOS Kernel Event Bus.

Central event logging and distribution mechanism for the
HospitalOS Built-In Educational Kernel Simulation Engine.
All events are strictly labeled with source="simulation".
"""

from __future__ import annotations
import time
from typing import List, Dict, Optional, Any
from contracts import KernelEvent


class KernelEventBus:
    """Authoritative event log for the educational kernel simulation engine."""

    def __init__(self, max_capacity: int = 300):
        self._max_capacity = max_capacity
        self._event_log: List[KernelEvent] = []
        self._event_counter: int = 0

    @property
    def event_counter(self) -> int:
        return self._event_counter

    def record_event(
        self,
        event_type: str,
        summary: str,
        category: Optional[str] = None,
        simulation_time: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "simulation",
    ) -> KernelEvent:
        """Records a simulation event into the kernel event stream."""
        self._event_counter += 1
        cat = category or self.infer_category(event_type)
        event = KernelEvent(
            event_id=f"sim-evt-{self._event_counter:04d}",
            timestamp=time.time(),
            source=source,
            event_type=event_type,
            summary=summary,
            category=cat,
            simulation_time=simulation_time,
            payload=payload or {},
        )
        self._event_log.append(event)
        if len(self._event_log) > self._max_capacity:
            self._event_log = self._event_log[-self._max_capacity:]
        return event

    def infer_category(self, event_type: str) -> str:
        """Deterministically maps event type names to standard subsystem categories."""
        et = event_type.upper()
        if "SCHED" in et or et in ("DISPATCH", "PREEMPT", "QUANTUM_EXPIRE", "EMERGENCY_PREEMPT", "COMPLETE", "IDLE", "WAKEUP"):
            return "SCHEDULER"
        if "RESOURCE" in et:
            return "RESOURCE"
        if "BANKER" in et or "DEADLOCK" in et:
            return "DEADLOCK"
        if "MEMORY" in et or "PAGE" in et:
            return "MEMORY"
        if "LOCK" in et or "SEMAPHORE" in et or et == "PROCESS_WAKE":
            return "SYNC"
        if "DEADLINE" in et:
            return "DEADLINE"
        if "PROCESS" in et or "PATIENT" in et:
            return "PROCESS"
        return "SYSTEM"

    def get_events(self, limit: int = 50, category: Optional[str] = None) -> List[KernelEvent]:
        """Returns the stream of simulated events, optionally filtered by category."""
        events = self._event_log
        if category and category.upper() != "ALL":
            events = [e for e in events if e.category.upper() == category.upper()]
        return events[-limit:]

    def get_simulated_syscalls(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns simulated OS system calls derived from discrete simulation events."""
        syscalls = []
        for e in self._event_log[-limit:]:
            sc = self._format_syscall(e)
            if sc:
                syscalls.append(sc)
        return syscalls

    def _format_syscall(self, e: KernelEvent) -> Optional[Dict[str, Any]]:
        et = e.event_type
        p = e.payload
        pid = p.get("pid")
        pid_str = f"PID-{pid:03d}" if pid is not None else "SYS"

        if et == "PATIENT_ADMITTED":
            call = f"sys_admit_patient(pid={pid}, doctor={p.get('doctor_id')})"
            desc = "Allocates PCB, pages memory, and places in NEW/READY state"
        elif et == "DISPATCH":
            call = f"sys_dispatch(pid={pid}, algo='{p.get('algorithm', 'CPU')}')"
            desc = "Dispatches ready process to CPU execution slot (READY -> RUNNING)"
        elif et in ("PREEMPT", "EMERGENCY_PREEMPT"):
            call = f"sys_preempt(victim_pid={pid})"
            desc = "Preempts CPU burst and returns process to ready queue (RUNNING -> READY)"
        elif et == "RESOURCE_ALLOC":
            call = f"sys_resource_request(pid={pid}, res='{p.get('resource_id')}', units={p.get('units', 1)})"
            desc = "Allocates requested hospital resource units to process"
        elif et == "RESOURCE_BLOCK":
            call = f"sys_resource_block(pid={pid}, res='{p.get('resource_id')}')"
            desc = "Resource unavailable; puts calling process into sleep/WAITING state"
        elif et == "RESOURCE_RELEASE":
            call = f"sys_resource_release(pid={pid}, res='{p.get('resource_id')}', units={p.get('released', 1)})"
            desc = "Releases held resource units and unblocks any pending waiting processes"
        elif et == "MEMORY_ALLOC":
            call = f"sys_memory_alloc(pid={pid}, pages={p.get('pages')})"
            desc = "Allocates physical 4KB memory frames into page table"
        elif et == "MEMORY_FREE":
            call = f"sys_memory_free(pid={pid})"
            desc = "Deallocates physical frames upon process completion"
        elif et == "LOCK_ACQUIRE":
            call = f"sys_mutex_acquire(pid={pid}, lock='{p.get('primitive_id')}')"
            desc = "Acquires critical section mutual exclusion lock"
        elif et == "LOCK_RELEASE":
            call = f"sys_mutex_release(pid={pid}, lock='{p.get('primitive_id')}')"
            desc = "Releases mutual exclusion lock and wakes next waiter"
        elif et == "SEMAPHORE_ACQUIRE":
            call = f"sys_semaphore_wait(pid={pid}, sem='{p.get('primitive_id')}')"
            desc = "Decrements counting semaphore slot"
        elif et == "SEMAPHORE_POST":
            call = f"sys_semaphore_post(pid={pid}, sem='{p.get('primitive_id')}')"
            desc = "Increments counting semaphore slot and wakes waiter"
        elif et == "COMPLETE":
            call = f"sys_exit(pid={pid})"
            desc = "Process terminates, releases all memory frames and locks"
        else:
            return None

        return {
            "timestamp": e.timestamp,
            "pid": pid_str,
            "call": call,
            "description": desc,
            "source": "simulation",
        }

    def clear(self):
        """Clears the event buffer."""
        self._event_log.clear()
        self._event_counter = 0

"""HospitalOS CPU Scheduling Subsystem.

Encapsulates educational CPU scheduling algorithms:
- FCFS, SJF, SRTF (preemptive), Round Robin, Priority (emergency preemption),
  and Hospital Adaptive.
Coordinates timeline slices with the authoritative process manager.
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, Callable
from contracts import ProcessState
from algorithms import schedule_patients
from .event_bus import KernelEventBus


class CPUScheduler:
    """Subsystem managing CPU scheduling simulation, timeline stepping, and execution metrics."""

    def __init__(self, event_bus: KernelEventBus):
        self._event_bus = event_bus
        self._scheduler_step_index: int = 0
        self._cached_schedule: Optional[Dict[str, Any]] = None
        self._last_algo: str = "FCFS"
        self._last_quantum: int = 2
        self._running_pid: Optional[int] = None

    @property
    def running_pid(self) -> Optional[int]:
        return self._running_pid

    @property
    def step_index(self) -> int:
        return self._scheduler_step_index

    @property
    def cached_schedule(self) -> Optional[Dict[str, Any]]:
        return self._cached_schedule

    def schedule(
        self,
        patient_dicts: List[Dict[str, Any]],
        algorithm: str = "FCFS",
        quantum: int = 2,
    ) -> Dict[str, Any]:
        """Executes CPU scheduling algorithm and caches result."""
        res = schedule_patients(patient_dicts, algorithm, quantum=quantum)
        self._cached_schedule = res
        self._last_algo = algorithm
        self._last_quantum = quantum

        self._event_bus.record_event(
            event_type="SCHEDULER_RUN",
            summary=f"Scheduler executed with {res['algorithm']} for {len(patient_dicts)} processes",
            category="SCHEDULER",
            payload={"algorithm": res["algorithm"], "process_count": len(patient_dicts), "quantum": quantum},
        )
        return res

    def step(
        self,
        kernel,
        algorithm: str = "FCFS",
        quantum: int = 2,
        waiting_patient_supplier: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        """Advances the scheduler by one slice in the timeline and updates process states."""
        if self._cached_schedule is None or self._last_algo != algorithm or self._last_quantum != quantum:
            patients = waiting_patient_supplier() if waiting_patient_supplier else []
            self.schedule(patients, algorithm, quantum)
            self._scheduler_step_index = 0

        gantt = self._cached_schedule.get("gantt", []) if self._cached_schedule else []
        if not gantt:
            return {
                "success": False,
                "message": "No processes available to schedule.",
                "step_index": 0,
                "total_steps": 0,
                "current_slice": None,
                "scheduler_state": self._cached_schedule,
            }

        if self._scheduler_step_index >= len(gantt):
            return {
                "success": True,
                "completed": True,
                "message": f"Scheduler timeline completed ({len(gantt)} slices executed). Click Reset to rerun.",
                "step_index": self._scheduler_step_index,
                "total_steps": len(gantt),
                "current_slice": None,
                "scheduler_state": self._cached_schedule,
            }

        curr_slice = gantt[self._scheduler_step_index]
        pid = curr_slice.get("pid")
        slice_type = curr_slice.get("slice_type")
        start = curr_slice.get("start")
        end = curr_slice.get("end")

        if pid is not None:
            self._running_pid = pid
            # Preempt other running processes
            for other_p in kernel.process_manager.get_all_processes():
                if other_p.pid != pid and other_p.state == ProcessState.RUNNING:
                    kernel.transition_process(
                        other_p.pid,
                        ProcessState.READY,
                        f"Preempted by scheduler at t={start}; returned to ready queue."
                    )

            reason = f"Dispatched by {algorithm} scheduler [slice {start}–{end}]"
            if slice_type == "emergency_preempt":
                reason = f"Clinical Emergency preemption dispatch [slice {start}–{end}]"
            elif slice_type == "quantum_expired":
                reason = f"Executing quantum slice [slice {start}–{end}]"

            kernel.transition_process(pid, ProcessState.RUNNING, reason)
            proc_entry = kernel.process_manager.get_process(pid)
            if proc_entry:
                proc_entry.remaining_time = curr_slice.get("remaining_after", 0)

            if slice_type == "completed":
                kernel.transition_process(
                    pid,
                    ProcessState.TERMINATED,
                    f"Clinical burst completed at t={end}; process terminated."
                )
                self._running_pid = None
            elif slice_type in ("quantum_expired", "preempted", "emergency_preempt"):
                kernel.transition_process(
                    pid,
                    ProcessState.READY,
                    f"Slice expired ({slice_type}) at t={end}; returned to ready queue ({curr_slice.get('remaining_after', 0)}u remaining)."
                )

        self._scheduler_step_index += 1

        return {
            "success": True,
            "completed": (self._scheduler_step_index >= len(gantt)),
            "step_index": self._scheduler_step_index,
            "total_steps": len(gantt),
            "current_slice": curr_slice,
            "scheduler_state": self._cached_schedule,
            "message": f"Slice {self._scheduler_step_index}/{len(gantt)} executed: {curr_slice.get('label', '')} [{start}–{end}u] ({slice_type}).",
        }

    def reset(self, kernel) -> Dict[str, Any]:
        """Resets the scheduler timeline stepping state."""
        self._scheduler_step_index = 0
        self._cached_schedule = None
        self._running_pid = None

        self._event_bus.record_event(
            event_type="SCHEDULER_RESET",
            summary="Scheduling simulation reset to initial state.",
            category="SCHEDULER",
            payload={},
        )
        return {
            "success": True,
            "message": "Scheduler stepping reset. Waiting processes restored to ready queue.",
            "step_index": 0,
        }

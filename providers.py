"""HospitalOS Data Provider Layer.

Provides a unified interface (HospitalOSProvider) for retrieving system state,
processes, scheduler execution, resources, memory, synchronization, and events.

Includes:
- SimulationProvider: Adapts existing Python/SQLite database, algorithms.py,
  and simulation models. All data is clearly identified as simulation data.
- LiveKernelProvider: Safe stub for future QEMU/kernel bridge integration.
  Explicitly reports disconnected/unavailable without fabricating fake data.
- ProviderManager: Coordinates active provider selection.
"""

from __future__ import annotations
import abc
import time
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional, Any

from contracts import (
    ProcessState,
    HospitalProcess,
    HospitalResource,
    DeadlockState,
    MemoryFrame,
    MemoryState,
    SynchronizationPrimitive,
    SynchronizationState,
    KernelEvent,
    SystemSnapshot,
)
from algorithms import schedule_patients, bankers_algorithm

DB_PATH = Path(__file__).with_name("hospitalos.db")


class HospitalOSProvider(abc.ABC):
    """Abstract base provider defining the HospitalOS data access contract."""

    @abc.abstractmethod
    def get_mode(self) -> str:
        """Returns provider mode identifier ('simulation' or 'live_kernel')."""
        pass

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Indicates whether the data source is currently connected and operational."""
        pass

    @abc.abstractmethod
    def get_processes(self) -> List[HospitalProcess]:
        """Returns the list of active hospital processes / PCBs."""
        pass

    @abc.abstractmethod
    def get_scheduler_state(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        """Returns the scheduler state and execution timeline for the specified algorithm."""
        pass

    @abc.abstractmethod
    def step_scheduler(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        """Steps the scheduler simulation forward by one execution slice."""
        pass

    @abc.abstractmethod
    def reset_scheduler(self) -> Dict[str, Any]:
        """Resets the scheduler stepping simulation."""
        pass

    @abc.abstractmethod
    def get_resources(self) -> List[HospitalResource]:
        """Returns the inventory of hospital resources."""
        pass

    @abc.abstractmethod
    def get_deadlock_state(
        self,
        allocation: Optional[List[List[int]]] = None,
        maximum: Optional[List[List[int]]] = None,
        available: Optional[List[int]] = None,
    ) -> DeadlockState:
        """Returns current deadlock detection and avoidance (Banker's/RAG) state."""
        pass

    @abc.abstractmethod
    def get_memory_state(self) -> MemoryState:
        """Returns physical memory allocation and per-process paging state."""
        pass

    @abc.abstractmethod
    def get_synchronization_state(self) -> SynchronizationState:
        """Returns state of synchronization primitives (mutexes/semaphores)."""
        pass

    @abc.abstractmethod
    def get_events(self, limit: int = 50) -> List[KernelEvent]:
        """Returns the stream of recent system/kernel events."""
        pass

    @abc.abstractmethod
    def get_system_snapshot(self, algorithm: str = "FCFS") -> SystemSnapshot:
        """Returns an integrated snapshot of all subsystems."""
        pass

    @abc.abstractmethod
    def get_process_by_id(self, pid: int) -> Optional[HospitalProcess]:
        """Returns the PCB representation of a single process by PID."""
        pass

    @abc.abstractmethod
    def transition_process(self, pid: int, target_state: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Transitions a process to a target state."""
        pass

    @abc.abstractmethod
    def step_lifecycle(self) -> Dict[str, Any]:
        """Advances one process through the simulation lifecycle."""
        pass


class SimulationProvider(HospitalOSProvider):
    """Simulation-backed provider reusing SQLite data and algorithms.py."""

    # Default canonical resources matching the kernel and Deadlock Lab
    DEFAULT_RESOURCES = [
        HospitalResource("RES_ICU", "ICU Beds", total=10, available=3, allocated=7),
        HospitalResource("RES_VENT", "Ventilators", total=5, available=3, allocated=2),
        HospitalResource("RES_MONITOR", "Patient Monitors", total=7, available=2, allocated=5),
    ]

    # Canonical default Banker's matrices (identical to kernel and resources.html)
    DEFAULT_BANKER_ALLOCATION = [
        [0, 1, 0],
        [2, 0, 0],
        [3, 0, 2],
        [2, 1, 1],
        [0, 0, 2],
    ]
    DEFAULT_BANKER_MAXIMUM = [
        [7, 5, 3],
        [3, 2, 2],
        [9, 0, 2],
        [2, 2, 2],
        [4, 3, 3],
    ]
    DEFAULT_BANKER_AVAILABLE = [3, 3, 2]
    RESOURCE_NAMES = ["ICU Beds", "Ventilators", "Patient Monitors"]
    PROCESS_NAMES = ["P0", "P1", "P2", "P3", "P4"]

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._event_log: List[KernelEvent] = []
        self._event_counter = 0
        self._process_states: Dict[int, Dict[str, Any]] = {}
        self._scheduler_step_index = 0
        self._cached_schedule: Optional[Dict[str, Any]] = None
        self._last_algo = "FCFS"
        self._last_quantum = 2
        self._record_initial_events()

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _record_initial_events(self):
        self.record_event(
            event_type="SYSTEM_BOOT",
            summary="HospitalOS Simulation Provider initialized",
            payload={"source": "simulation", "storage": "sqlite"},
        )

    def record_event(self, event_type: str, summary: str, payload: Optional[Dict[str, Any]] = None) -> KernelEvent:
        self._event_counter += 1
        event = KernelEvent(
            event_id=f"sim-evt-{self._event_counter:04d}",
            timestamp=time.time(),
            source="simulation",
            event_type=event_type,
            summary=summary,
            payload=payload or {},
        )
        self._event_log.append(event)
        if len(self._event_log) > 200:
            self._event_log = self._event_log[-200:]
        return event

    def get_mode(self) -> str:
        return "simulation"

    def is_connected(self) -> bool:
        return True

    def get_processes(self) -> List[HospitalProcess]:
        """Translates SQLite patients into HospitalProcess objects across the 5 canonical OS states."""
        processes: List[HospitalProcess] = []
        try:
            with self._get_db() as conn:
                rows = conn.execute("""
                    SELECT p.*, d.name AS doctor_name, d.on_duty AS doctor_on_duty
                    FROM patients p
                    LEFT JOIN doctors d ON d.id=p.doctor_id
                    ORDER BY p.id ASC
                """).fetchall()

                # Group active patients assigned to on-duty doctors
                doctor_active_map: Dict[int, List[sqlite3.Row]] = {}
                for row in rows:
                    if row["status"] != "Completed" and row["doctor_id"] and bool(row["doctor_on_duty"]):
                        doctor_active_map.setdefault(row["doctor_id"], []).append(row)

                # For each doctor, the top priority / earliest patient is the default RUNNING candidate
                running_pids = set()
                for doc_id, doc_patients in doctor_active_map.items():
                    sorted_doc_patients = sorted(
                        doc_patients,
                        key=lambda r: (int(r["priority"]), int(r["arrival_time"]), int(r["id"]))
                    )
                    if sorted_doc_patients:
                        running_pids.add(sorted_doc_patients[0]["id"])

                now = time.time()
                for row in rows:
                    pid = int(row["id"])
                    status = row["status"]
                    doctor_id = row["doctor_id"]
                    doctor_name = row["doctor_name"]
                    doctor_on_duty = bool(row["doctor_on_duty"]) if row["doctor_on_duty"] is not None else False
                    burst = int(row["burst_time"])
                    priority = int(row["priority"])
                    arrival = int(row["arrival_time"])
                    spec = row["specialization"] if "specialization" in row.keys() else "General Medicine"
                    mem_pages = max(1, 2 + burst // 2)
                    mem_kb = mem_pages * 4
                    deadline = arrival + burst * 3 if priority == 1 else None

                    # If not yet tracked in _process_states, initialize baseline state
                    if pid not in self._process_states:
                        if status == "Completed":
                            state = ProcessState.TERMINATED
                            reason = "Clinical treatment completed; process terminated and resources released."
                            rem = 0
                            res_alloc: Dict[str, int] = {}
                            history = [
                                {"from": None, "to": ProcessState.NEW, "timestamp": now - 60, "reason": "Patient admission & intake triage"},
                                {"from": ProcessState.NEW, "to": ProcessState.READY, "timestamp": now - 45, "reason": "Triage complete; placed in ready queue"},
                                {"from": ProcessState.READY, "to": ProcessState.RUNNING, "timestamp": now - 25, "reason": f"Dispatched to Dr. {doctor_name}" if doctor_name else "Dispatched to execution unit"},
                                {"from": ProcessState.RUNNING, "to": ProcessState.TERMINATED, "timestamp": now - 5, "reason": reason},
                            ]
                        elif not doctor_id or not doctor_on_duty:
                            state = ProcessState.WAITING
                            reason = "Waiting for matching on-duty doctor assignment."
                            rem = burst
                            res_alloc = {}
                            history = [
                                {"from": None, "to": ProcessState.NEW, "timestamp": now - 30, "reason": "Patient admission & intake triage"},
                                {"from": ProcessState.NEW, "to": ProcessState.WAITING, "timestamp": now - 15, "reason": reason},
                            ]
                        elif pid in running_pids:
                            state = ProcessState.RUNNING
                            reason = f"Dispatched to Dr. {doctor_name}; CPU executing clinical burst."
                            rem = max(1, burst // 2)
                            res_alloc = {"Patient Monitor": 1}
                            history = [
                                {"from": None, "to": ProcessState.NEW, "timestamp": now - 30, "reason": "Patient admission & intake triage"},
                                {"from": ProcessState.NEW, "to": ProcessState.READY, "timestamp": now - 20, "reason": f"Assigned to Dr. {doctor_name}"},
                                {"from": ProcessState.READY, "to": ProcessState.RUNNING, "timestamp": now - 8, "reason": reason},
                            ]
                        else:
                            state = ProcessState.READY
                            reason = f"Assigned to Dr. {doctor_name}; waiting in ready queue for CPU dispatch."
                            rem = burst
                            res_alloc = {}
                            history = [
                                {"from": None, "to": ProcessState.NEW, "timestamp": now - 30, "reason": "Patient admission & intake triage"},
                                {"from": ProcessState.NEW, "to": ProcessState.READY, "timestamp": now - 15, "reason": reason},
                            ]

                        self._process_states[pid] = {
                            "state": state,
                            "state_reason": reason,
                            "remaining_time": rem,
                            "assigned_resources": res_alloc,
                            "deadline": deadline,
                            "state_history": history,
                        }
                    else:
                        # Process already tracked. Enforce database consistency:
                        curr = self._process_states[pid]
                        # 1. DB says Completed -> must be TERMINATED
                        if status == "Completed" and curr["state"] != ProcessState.TERMINATED:
                            prev_st = curr["state"]
                            curr["state"] = ProcessState.TERMINATED
                            curr["state_reason"] = "Clinical treatment completed; process terminated and resources released."
                            curr["remaining_time"] = 0
                            curr["assigned_resources"] = {}
                            curr["state_history"].append({
                                "from": prev_st,
                                "to": ProcessState.TERMINATED,
                                "timestamp": now,
                                "reason": curr["state_reason"],
                            })
                        # 2. DB says Waiting but state was TERMINATED -> revive
                        elif status == "Waiting" and curr["state"] == ProcessState.TERMINATED:
                            curr["state"] = ProcessState.READY if doctor_id else ProcessState.WAITING
                            curr["state_reason"] = "Process reopened in simulation."
                            curr["remaining_time"] = burst
                            curr["state_history"].append({
                                "from": ProcessState.TERMINATED,
                                "to": curr["state"],
                                "timestamp": now,
                                "reason": curr["state_reason"],
                            })
                        # 3. Doctor unassigned or off duty while RUNNING or READY -> drop to WAITING
                        elif (not doctor_id or not doctor_on_duty) and curr["state"] in (ProcessState.RUNNING, ProcessState.READY):
                            prev_st = curr["state"]
                            curr["state"] = ProcessState.WAITING
                            curr["state_reason"] = "Doctor unassigned or off duty; waiting for available doctor assignment."
                            curr["assigned_resources"] = {}
                            curr["state_history"].append({
                                "from": prev_st,
                                "to": ProcessState.WAITING,
                                "timestamp": now,
                                "reason": curr["state_reason"],
                            })

                    p_state = self._process_states[pid]
                    processes.append(
                        HospitalProcess(
                            pid=pid,
                            patient_id=pid,
                            name=row["name"],
                            state=p_state["state"],
                            priority=priority,
                            arrival_time=arrival,
                            burst_time=burst,
                            remaining_time=p_state["remaining_time"],
                            doctor_id=doctor_id,
                            doctor_name=doctor_name,
                            specialization=spec,
                            assigned_resources=p_state["assigned_resources"],
                            memory_pages=mem_pages,
                            memory_kb=mem_kb,
                            deadline=deadline,
                            state_reason=p_state["state_reason"],
                            state_history=p_state["state_history"],
                        )
                    )
        except sqlite3.OperationalError:
            pass
        return processes

    def get_process_by_id(self, pid: int) -> Optional[HospitalProcess]:
        """Returns the PCB representation of a single process by PID."""
        processes = self.get_processes()
        for p in processes:
            if p.pid == pid:
                return p
        return None

    def register_patient_process(self, patient_id: int, name: str, doctor_id: Optional[int] = None):
        """Registers a newly admitted patient process into the simulation lifecycle."""
        now = time.time()
        history = [{"from": None, "to": ProcessState.NEW, "timestamp": now, "reason": "Patient admission & intake triage"}]
        state = ProcessState.NEW
        reason = "Patient registered in intake triage; evaluating clinical priority."

        self._process_states[patient_id] = {
            "state": state,
            "state_reason": reason,
            "remaining_time": 4,
            "assigned_resources": {},
            "deadline": None,
            "state_history": history,
        }
        self.record_event(
            event_type="PROCESS_CREATED",
            summary=f"Created process PCB for patient {name} (PID-{patient_id:03d}) in NEW state",
            payload={"pid": patient_id, "state": state, "doctor_id": doctor_id},
        )

    def mark_patient_completed(self, patient_id: int):
        """Synchronizes patient completion into the simulation process state."""
        self.transition_process(patient_id, ProcessState.TERMINATED, "Marked complete via doctor workload manager")

    def mark_patient_unassigned(self, patient_id: int):
        """Synchronizes doctor unassignment into the simulation process state."""
        self.transition_process(patient_id, ProcessState.WAITING, "Doctor assignment removed; waiting for available doctor")

    def transition_process(self, pid: int, target_state: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Transitions a process to a target state, enforcing data contracts and logging simulation events."""
        valid_states = {ProcessState.NEW, ProcessState.READY, ProcessState.RUNNING, ProcessState.WAITING, ProcessState.TERMINATED}
        if target_state not in valid_states:
            return {"success": False, "error": f"Invalid state '{target_state}'"}

        self.get_processes()
        if pid not in self._process_states:
            return {"success": False, "error": f"Process PID-{pid} not found in active simulation"}

        current = self._process_states[pid]
        old_state = current["state"]
        if old_state == target_state:
            return {"success": True, "message": f"Process PID-{pid} is already {target_state}", "state": target_state}

        now = time.time()
        default_reasons = {
            ProcessState.NEW: "Intake evaluation and PCB triage initialization.",
            ProcessState.READY: "Admitted into ready queue; waiting for scheduler dispatch.",
            ProcessState.RUNNING: "Dispatched by scheduler to doctor execution unit.",
            ProcessState.WAITING: "Blocked; waiting for clinical resource, diagnostic test, or doctor assignment.",
            ProcessState.TERMINATED: "Treatment complete; process terminated and allocated resources released.",
        }
        transition_reason = reason or default_reasons.get(target_state, "Simulation state transition.")

        current["state"] = target_state
        current["state_reason"] = transition_reason

        # Synchronize resources and SQLite database status
        if target_state == ProcessState.TERMINATED:
            current["remaining_time"] = 0
            current["assigned_resources"] = {}
            try:
                with self._get_db() as conn:
                    conn.execute("UPDATE patients SET status='Completed' WHERE id=?", (pid,))
            except Exception:
                pass
        elif target_state == ProcessState.RUNNING:
            current["assigned_resources"] = {"Patient Monitor": 1}
            try:
                with self._get_db() as conn:
                    conn.execute("UPDATE patients SET status='Waiting' WHERE id=?", (pid,))
            except Exception:
                pass
        elif target_state in (ProcessState.READY, ProcessState.WAITING, ProcessState.NEW):
            current["assigned_resources"] = {}
            try:
                with self._get_db() as conn:
                    conn.execute("UPDATE patients SET status='Waiting' WHERE id=?", (pid,))
            except Exception:
                pass

        current["state_history"].append({
            "from": old_state,
            "to": target_state,
            "timestamp": now,
            "reason": transition_reason,
        })

        self.record_event(
            event_type="PROCESS_STATE_CHANGE",
            summary=f"Process PID-{pid:03d} transitioned: {old_state} -> {target_state}",
            payload={"pid": pid, "from": old_state, "to": target_state, "reason": transition_reason},
        )

        return {
            "success": True,
            "pid": pid,
            "from_state": old_state,
            "to_state": target_state,
            "reason": transition_reason,
        }

    def step_lifecycle(self) -> Dict[str, Any]:
        """Advances one process through the simulation lifecycle in a coherent order:
        NEW -> READY -> RUNNING -> WAITING -> READY -> RUNNING -> TERMINATED.
        """
        processes = self.get_processes()
        if not processes:
            return {"success": False, "message": "No processes in simulation to advance."}

        # 1. Advance NEW processes -> READY (if doctor assigned) or WAITING (if no doctor)
        new_procs = [p for p in processes if p.state == ProcessState.NEW]
        if new_procs:
            p = new_procs[0]
            next_st = ProcessState.READY if p.doctor_id else ProcessState.WAITING
            reason = "Triage completed; admitted to ready queue" if p.doctor_id else "Triage completed; waiting for doctor capacity"
            return self.transition_process(p.pid, next_st, reason)

        # 2. Advance READY processes -> RUNNING (dispatch)
        ready_procs = [p for p in processes if p.state == ProcessState.READY]
        if ready_procs:
            candidate = min(ready_procs, key=lambda p: (p.priority, p.arrival_time, p.pid))
            # Preempt any currently running process on the same doctor if applicable
            for p in processes:
                if p.state == ProcessState.RUNNING and p.doctor_id == candidate.doctor_id:
                    self.transition_process(p.pid, ProcessState.READY, "Preempted by scheduler for higher-priority process")
                    break
            return self.transition_process(candidate.pid, ProcessState.RUNNING, f"Dispatched to Dr. {candidate.doctor_name or 'Staff'}")

        # 3. Advance RUNNING processes -> WAITING (simulate clinical test/resource wait) or TERMINATED
        running_procs = [p for p in processes if p.state == ProcessState.RUNNING]
        if running_procs:
            candidate = running_procs[0]
            visited_waiting = any(h.get("to") == ProcessState.WAITING for h in candidate.state_history)
            if not visited_waiting:
                return self.transition_process(candidate.pid, ProcessState.WAITING, "Blocked on pending diagnostic lab test / specialist resource")
            else:
                return self.transition_process(candidate.pid, ProcessState.TERMINATED, "Treatment burst completed; process discharged")

        # 4. Advance WAITING processes -> READY (unblock)
        waiting_procs = [p for p in processes if p.state == ProcessState.WAITING]
        if waiting_procs:
            candidate = waiting_procs[0]
            return self.transition_process(candidate.pid, ProcessState.READY, "Diagnostic lab test completed; returned to ready queue")

        return {"success": True, "message": "Simulation lifecycle cycle complete."}

    def get_scheduler_state(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        """Runs the scheduling simulator on active waiting patients."""
        try:
            with self._get_db() as conn:
                rows = conn.execute(
                    "SELECT id, name, arrival_time, burst_time, priority FROM patients WHERE status='Waiting' ORDER BY id"
                ).fetchall()
            patient_dicts = [dict(row) for row in rows]
            res = schedule_patients(patient_dicts, algorithm, quantum=quantum)
            self._cached_schedule = res
            self._last_algo = algorithm
            self._last_quantum = quantum
            self.record_event(
                event_type="SCHEDULER_RUN",
                summary=f"Scheduler executed with {res['algorithm']} for {len(patient_dicts)} processes",
                payload={"algorithm": res["algorithm"], "process_count": len(patient_dicts), "quantum": quantum},
            )
            return res
        except Exception as exc:
            return {
                "algorithm": algorithm,
                "quantum": quantum,
                "results": [],
                "gantt": [],
                "events": [],
                "average_waiting_time": 0,
                "average_turnaround_time": 0,
                "average_response_time": 0,
                "total_context_switches": 0,
                "total_preemptions": 0,
                "total_deadline_misses": 0,
                "cpu_utilization": 0.0,
                "simulation_clock": 0,
                "message": f"Simulation error: {exc}",
            }

    def step_scheduler(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        """Advances the scheduler by one slice in the execution timeline, updating process state machine."""
        if self._cached_schedule is None or self._last_algo != algorithm or self._last_quantum != quantum:
            self._cached_schedule = self.get_scheduler_state(algorithm, quantum)
            self._scheduler_step_index = 0
            self._last_algo = algorithm
            self._last_quantum = quantum

        gantt = self._cached_schedule.get("gantt", [])
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
            # Ensure process state tracking is initialized
            self.get_processes()

            # Preempt any other process that is currently in RUNNING state
            for other_pid, st in list(self._process_states.items()):
                if other_pid != pid and st["state"] == ProcessState.RUNNING:
                    self.transition_process(
                        other_pid,
                        ProcessState.READY,
                        f"Preempted by scheduler at t={start}; returned to ready queue."
                    )

            # Dispatch target process to RUNNING
            reason = f"Dispatched by {algorithm} scheduler [slice {start}–{end}]"
            if slice_type == "emergency_preempt":
                reason = f"Clinical Emergency preemption dispatch [slice {start}–{end}]"
            elif slice_type == "quantum_expired":
                reason = f"Executing quantum slice [slice {start}–{end}]"

            self.transition_process(pid, ProcessState.RUNNING, reason)
            if pid in self._process_states:
                self._process_states[pid]["remaining_time"] = curr_slice.get("remaining_after", 0)

            # If slice completed execution, transition to TERMINATED
            if slice_type == "completed":
                self.transition_process(
                    pid,
                    ProcessState.TERMINATED,
                    f"Clinical burst completed at t={end}; process terminated."
                )
            elif slice_type in ("quantum_expired", "preempted", "emergency_preempt"):
                # Returned to ready queue at end of slice
                self.transition_process(
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
            "message": f"Slice {self._scheduler_step_index}/{len(gantt)} executed: {curr_slice['label']} [{start}–{end}u] ({slice_type}).",
        }

    def reset_scheduler(self) -> Dict[str, Any]:
        """Resets the scheduler stepping and restores waiting processes to READY/WAITING."""
        self._scheduler_step_index = 0
        self._cached_schedule = None
        self.get_processes()
        with self._get_db() as conn:
            rows = conn.execute("SELECT id, burst_time, doctor_id FROM patients WHERE status='Waiting'").fetchall()
        for r in rows:
            pid = int(r["id"])
            if pid in self._process_states:
                st = self._process_states[pid]
                st["state"] = ProcessState.READY if r["doctor_id"] else ProcessState.WAITING
                st["remaining_time"] = int(r["burst_time"])
                st["assigned_resources"] = {}
                st["state_reason"] = "Scheduler reset; process returned to ready queue."
                st["state_history"].append({
                    "from": None,
                    "to": st["state"],
                    "timestamp": time.time(),
                    "reason": st["state_reason"],
                })
        self.record_event(
            event_type="SCHEDULER_RESET",
            summary="Scheduling simulation reset to initial state.",
            payload={},
        )
        return {
            "success": True,
            "message": "Scheduler stepping reset. Waiting processes restored to ready queue.",
            "step_index": 0,
        }

    def get_resources(self) -> List[HospitalResource]:
        """Returns the configured inventory of simulated resources."""
        return [
            HospitalResource(
                resource_id=r.resource_id,
                name=r.name,
                total=r.total,
                available=r.available,
                allocated=r.allocated,
            )
            for r in self.DEFAULT_RESOURCES
        ]

    def get_deadlock_state(
        self,
        allocation: Optional[List[List[int]]] = None,
        maximum: Optional[List[List[int]]] = None,
        available: Optional[List[int]] = None,
    ) -> DeadlockState:
        """Calculates Banker's safety state and builds the Resource Allocation Graph."""
        alloc = allocation if allocation is not None else self.DEFAULT_BANKER_ALLOCATION
        max_m = maximum if maximum is not None else self.DEFAULT_BANKER_MAXIMUM
        avail = available if available is not None else self.DEFAULT_BANKER_AVAILABLE

        rag_edges: List[Dict[str, Any]] = []
        try:
            banker_res = bankers_algorithm(alloc, max_m, avail)
            safe = banker_res["safe"]
            safe_seq = banker_res["safe_sequence"]
            need = banker_res["need"]
            msg = banker_res["message"]

            # Construct RAG edges
            for i in range(len(alloc)):
                p_name = f"P{i}"
                for j in range(len(avail)):
                    r_name = self.RESOURCE_NAMES[j] if j < len(self.RESOURCE_NAMES) else f"R{j}"
                    # Allocation edge: Resource -> Process
                    if alloc[i][j] > 0:
                        rag_edges.append({
                            "from": r_name,
                            "to": p_name,
                            "units": alloc[i][j],
                            "type": "allocation",
                        })
                    # Request/Need edge: Process -> Resource
                    if need[i][j] > 0:
                        rag_edges.append({
                            "from": p_name,
                            "to": r_name,
                            "units": need[i][j],
                            "type": "request",
                        })

            return DeadlockState(
                safe=safe,
                safe_sequence=safe_seq,
                allocation_matrix=alloc,
                maximum_matrix=max_m,
                need_matrix=need,
                available_vector=avail,
                resource_names=self.RESOURCE_NAMES[:len(avail)],
                process_names=[f"P{i}" for i in range(len(alloc))],
                cycle_detected=not safe,
                rag_edges=rag_edges,
                message=msg,
            )
        except Exception as exc:
            return DeadlockState(
                safe=False,
                safe_sequence=[],
                allocation_matrix=alloc,
                maximum_matrix=max_m,
                need_matrix=[],
                available_vector=avail,
                resource_names=self.RESOURCE_NAMES[:len(avail)],
                process_names=[f"P{i}" for i in range(len(alloc))],
                cycle_detected=True,
                rag_edges=[],
                message=f"Error evaluating deadlock state: {exc}",
            )

    def get_memory_state(self) -> MemoryState:
        """Simulates physical frame allocation and process paging for admitted patients."""
        total_frames = 32
        frame_size_kb = 4
        processes = self.get_processes()

        frames: List[MemoryFrame] = [MemoryFrame(frame_id=i, status="free") for i in range(total_frames)]
        process_allocations: Dict[int, Dict[str, Any]] = {}

        current_frame = 0
        for p in processes:
            if p.state == ProcessState.TERMINATED:
                continue
            pages_needed = p.memory_pages
            allocated_frame_ids = []
            for page in range(pages_needed):
                if current_frame < total_frames:
                    frames[current_frame].pid = p.pid
                    frames[current_frame].page_number = page
                    frames[current_frame].status = "allocated"
                    allocated_frame_ids.append(current_frame)
                    current_frame += 1

            process_allocations[p.pid] = {
                "process_name": p.name,
                "pages": pages_needed,
                "frames": allocated_frame_ids,
            }

        free_frames = total_frames - current_frame
        return MemoryState(
            total_frames=total_frames,
            free_frames=max(0, free_frames),
            frame_size_kb=frame_size_kb,
            frames=frames,
            process_allocations=process_allocations,
        )

    def get_synchronization_state(self) -> SynchronizationState:
        """Returns simulated synchronization primitives for hospital critical sections."""
        primitives = [
            SynchronizationPrimitive(
                primitive_id="MUTEX_ICU",
                name="ICU Admission Mutex",
                primitive_type="mutex",
                locked=False,
                count=1,
            ),
            SynchronizationPrimitive(
                primitive_id="SEM_VENTILATORS",
                name="Ventilator Resource Semaphore",
                primitive_type="semaphore",
                locked=False,
                count=3,  # Available count matches 3 free ventilators
            ),
            SynchronizationPrimitive(
                primitive_id="MUTEX_OT_1",
                name="Operating Theatre #1 Lock",
                primitive_type="mutex",
                locked=False,
                count=1,
            ),
        ]
        return SynchronizationState(
            primitives=primitives,
            recent_events=[
                {"timestamp": time.time(), "primitive": "MUTEX_ICU", "action": "INITIALIZED", "state": "UNLOCKED"}
            ],
        )

    def get_events(self, limit: int = 50) -> List[KernelEvent]:
        return self._event_log[-limit:]

    def get_system_snapshot(self, algorithm: str = "FCFS") -> SystemSnapshot:
        return SystemSnapshot(
            timestamp=time.time(),
            mode="simulation",
            is_connected=True,
            message="HospitalOS Simulation Provider active (local SQLite & Python algorithms)",
            processes=self.get_processes(),
            scheduler=self.get_scheduler_state(algorithm),
            resources=self.get_resources(),
            deadlock=self.get_deadlock_state(),
            memory=self.get_memory_state(),
            synchronization=self.get_synchronization_state(),
            recent_events=self.get_events(20),
        )


class LiveKernelProvider(HospitalOSProvider):
    """Stub provider representing future live QEMU kernel bridge connection.

    CRITICAL INVARIANT: This provider NEVER fabricates fake kernel data.
    When disconnected, it explicitly reports disconnected status and empty collections.
    """

    def __init__(self):
        self._connected = False
        self._disconnection_reason = "QEMU kernel bridge transport is not yet implemented (Phase 0)."

    def get_mode(self) -> str:
        return "live_kernel"

    def is_connected(self) -> bool:
        return self._connected

    def get_processes(self) -> List[HospitalProcess]:
        # Live kernel is not connected; return empty list, do NOT fake
        return []

    def get_scheduler_state(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        return {
            "algorithm": algorithm,
            "quantum": quantum if algorithm in ("ROUND_ROBIN", "RR") else None,
            "connected": False,
            "results": [],
            "gantt": [],
            "events": [],
            "average_waiting_time": 0,
            "average_turnaround_time": 0,
            "average_response_time": 0,
            "total_context_switches": 0,
            "total_preemptions": 0,
            "total_deadline_misses": 0,
            "cpu_utilization": 0.0,
            "simulation_clock": 0,
            "message": "Live Kernel disconnected. QEMU bridge implementation scheduled for a future phase.",
        }

    def step_scheduler(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "Live kernel bridge is disconnected; scheduler stepping unavailable in Live Kernel mode.",
        }

    def reset_scheduler(self) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "Live kernel bridge is disconnected; scheduler reset unavailable in Live Kernel mode.",
        }

    def get_resources(self) -> List[HospitalResource]:
        return []

    def get_deadlock_state(
        self,
        allocation: Optional[List[List[int]]] = None,
        maximum: Optional[List[List[int]]] = None,
        available: Optional[List[int]] = None,
    ) -> DeadlockState:
        return DeadlockState(
            safe=False,
            safe_sequence=[],
            allocation_matrix=[],
            maximum_matrix=[],
            need_matrix=[],
            available_vector=[],
            resource_names=[],
            process_names=[],
            cycle_detected=False,
            rag_edges=[],
            message="Live kernel disconnected. No kernel resource state available.",
        )

    def get_memory_state(self) -> MemoryState:
        return MemoryState(
            total_frames=0,
            free_frames=0,
            frame_size_kb=4,
            frames=[],
            process_allocations={},
        )

    def get_synchronization_state(self) -> SynchronizationState:
        return SynchronizationState(primitives=[], recent_events=[])

    def get_events(self, limit: int = 50) -> List[KernelEvent]:
        return []

    def get_system_snapshot(self, algorithm: str = "FCFS") -> SystemSnapshot:
        return SystemSnapshot(
            timestamp=time.time(),
            mode="live_kernel",
            is_connected=False,
            message="Live Kernel Bridge Disconnected: The custom kernel QEMU transport will be implemented in a subsequent phase.",
            processes=[],
            scheduler=self.get_scheduler_state(algorithm),
            resources=[],
            deadlock=self.get_deadlock_state(),
            memory=self.get_memory_state(),
            synchronization=self.get_synchronization_state(),
            recent_events=[],
        )

    def get_process_by_id(self, pid: int) -> Optional[HospitalProcess]:
        return None

    def transition_process(self, pid: int, target_state: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "Live kernel bridge is disconnected; cannot modify kernel process state.",
        }

    def step_lifecycle(self) -> Dict[str, Any]:
        return {
            "success": False,
            "error": "Live kernel bridge is disconnected; simulation stepping unavailable in Live Kernel mode.",
        }


class ProviderManager:
    """Manages provider instances and active mode selection."""

    def __init__(self):
        self._simulation_provider = SimulationProvider()
        self._live_kernel_provider = LiveKernelProvider()
        self._active_mode = "simulation"

    def get_active_mode(self) -> str:
        return self._active_mode

    def set_active_mode(self, mode: str) -> None:
        if mode not in {"simulation", "live_kernel"}:
            raise ValueError(f"Invalid mode '{mode}'. Choose 'simulation' or 'live_kernel'.")
        self._active_mode = mode

    def get_provider(self, mode: Optional[str] = None) -> HospitalOSProvider:
        target_mode = mode or self._active_mode
        if target_mode == "live_kernel":
            return self._live_kernel_provider
        return self._simulation_provider

    def get_available_modes(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": "simulation",
                "name": "Simulation Mode",
                "connected": self._simulation_provider.is_connected(),
                "description": "Local Flask/SQLite database and Python OS scheduling/Banker's algorithms.",
            },
            {
                "id": "live_kernel",
                "name": "Live Kernel Mode",
                "connected": self._live_kernel_provider.is_connected(),
                "description": "Direct communication bridge to the HospitalOS custom kernel running under QEMU (Future phase).",
            },
        ]


# Global singleton instance
provider_manager = ProviderManager()


def get_provider(mode: Optional[str] = None) -> HospitalOSProvider:
    """Convenience accessor for obtaining the current HospitalOS provider."""
    return provider_manager.get_provider(mode)

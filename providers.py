"""HospitalOS Data Provider Layer.

Provides a unified interface (HospitalOSProvider) for retrieving system state,
processes, scheduler execution, hospital resources, deadlock avoidance (Banker's & RAG),
memory management (pages/frames), synchronization (mutexes & counting semaphores),
and simulated system events.

Includes:
- SimulationProvider: Operates on a single unified HospitalProcess model, adapting
  SQLite data, algorithms.py, dynamic resource inventories, physical frame tables,
  and concurrency primitives. All data is clearly identified as simulation data (source="simulation").
- LiveKernelProvider: Safe stub for future QEMU/kernel bridge integration.
  Explicitly reports disconnected/unavailable without fabricating fake kernel telemetry.
- ProviderManager: Coordinates active provider selection.
"""

from __future__ import annotations
import abc
import time
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

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
    def request_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        """Requests units of a hospital resource for a process."""
        pass

    @abc.abstractmethod
    def release_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        """Releases units of a hospital resource held by a process."""
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
    def allocate_memory(self, pid: int, pages: Optional[int] = None) -> Dict[str, Any]:
        """Allocates physical memory frames for a process's pages."""
        pass

    @abc.abstractmethod
    def free_memory(self, pid: int) -> Dict[str, Any]:
        """Frees all physical memory frames allocated to a process."""
        pass

    @abc.abstractmethod
    def get_synchronization_state(self) -> SynchronizationState:
        """Returns state of synchronization primitives (mutexes/semaphores)."""
        pass

    @abc.abstractmethod
    def acquire_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        """Attempts to acquire a mutex or semaphore synchronization primitive."""
        pass

    @abc.abstractmethod
    def release_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        """Releases a mutex or semaphore synchronization primitive."""
        pass

    @abc.abstractmethod
    def get_events(self, limit: int = 50, category: Optional[str] = None) -> List[KernelEvent]:
        """Returns the stream of recent system/kernel events."""
        pass

    @abc.abstractmethod
    def get_simulated_syscalls(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns the log of simulated OS system calls."""
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

    @abc.abstractmethod
    def readmit_patient(self, pid: int) -> Dict[str, Any]:
        """Re-admits a completed/terminated patient into the active simulation."""
        pass

    @abc.abstractmethod
    def run_demo_scenario(self, scenario_id: str) -> Dict[str, Any]:
        """Provisions and executes an educational OS demonstration scenario."""
        pass


class SimulationProvider(HospitalOSProvider):
    """Simulation-backed provider maintaining coherent HospitalProcess lifecycle,
    CPU scheduler, hospital resource manager, RAG, Banker's avoidance, physical frame table,
    and concurrency primitives.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._event_log: List[KernelEvent] = []
        self._event_counter = 0
        self._process_states: Dict[int, Dict[str, Any]] = {}
        self._scheduler_step_index = 0
        self._cached_schedule: Optional[Dict[str, Any]] = None
        self._last_algo = "FCFS"
        self._last_quantum = 2

        # Module 5: Hospital Resources Inventory
        self._resources: Dict[str, HospitalResource] = {
            "RES_DOC": HospitalResource("RES_DOC", "On-Duty Doctors", total=3, available=3, allocated=0),
            "RES_ICU": HospitalResource("RES_ICU", "ICU Beds", total=6, available=4, allocated=2, holders=[1]),
            "RES_VENT": HospitalResource("RES_VENT", "Ventilators", total=4, available=3, allocated=1, holders=[1]),
            "RES_MONITOR": HospitalResource("RES_MONITOR", "Patient Monitors", total=8, available=5, allocated=3, holders=[1, 2]),
            "RES_SCANNER": HospitalResource("RES_SCANNER", "CT Scanner", total=2, available=2, allocated=0, holders=[]),
            "RES_OT": HospitalResource("RES_OT", "Operating Theatre", total=2, available=1, allocated=1, holders=[2]),
        }

        # Module 8: Physical Memory Frame Table (16 frames of 4KB = 64KB physical RAM)
        self._memory_total_frames = 16
        self._memory_frame_size_kb = 4
        self._memory_frames: List[MemoryFrame] = [MemoryFrame(frame_id=i, status="free") for i in range(16)]

        # Module 10: Concurrency & Synchronization Primitives
        self._primitives: Dict[str, SynchronizationPrimitive] = {
            "MUTEX_OT": SynchronizationPrimitive("MUTEX_OT", "Operating Theatre Mutex", "mutex", locked=False, count=1, max_count=1),
            "MUTEX_SCANNER": SynchronizationPrimitive("MUTEX_SCANNER", "CT Scanner Lock", "mutex", locked=False, count=1, max_count=1),
            "SEM_ICU": SynchronizationPrimitive("SEM_ICU", "ICU Bed Counting Semaphore", "semaphore", locked=False, count=3, max_count=3),
        }

        self._record_initial_events()

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _record_initial_events(self):
        self.record_event(
            event_type="SYSTEM_BOOT",
            summary="HospitalOS Simulation Provider initialized (SQLite & Python algorithms)",
            category="SYSTEM",
            payload={"source": "simulation", "frames": 16, "frame_size_kb": 4},
        )

    def record_event(
        self,
        event_type: str,
        summary: str,
        category: Optional[str] = None,
        simulation_time: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> KernelEvent:
        self._event_counter += 1
        cat = category or self._infer_category(event_type)
        event = KernelEvent(
            event_id=f"sim-evt-{self._event_counter:04d}",
            timestamp=time.time(),
            source="simulation",
            event_type=event_type,
            summary=summary,
            category=cat,
            simulation_time=simulation_time,
            payload=payload or {},
        )
        self._event_log.append(event)
        if len(self._event_log) > 300:
            self._event_log = self._event_log[-300:]
        return event

    def _infer_category(self, event_type: str) -> str:
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
                    mem_pages = max(1, min(4, 1 + burst // 3))
                    mem_kb = mem_pages * self._memory_frame_size_kb
                    deadline = arrival + burst * 2 if priority == 1 else (arrival + burst * 3 if priority == 2 else None)

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
                            res_alloc = {"Patient Monitors": 1}
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
                            "memory_pages": mem_pages,
                            "memory_kb": mem_kb,
                            "memory_frames": [],
                            "waiting_on_resource": None,
                            "waiting_on_primitive": None,
                            "deadline": deadline,
                            "deadline_status": "none" if deadline is None else "active",
                            "state_history": history,
                        }
                        if state != ProcessState.TERMINATED:
                            self.allocate_memory(pid, mem_pages)
                    else:
                        curr = self._process_states[pid]
                        if status == "Completed" and curr["state"] != ProcessState.TERMINATED:
                            prev_st = curr["state"]
                            curr["state"] = ProcessState.TERMINATED
                            curr["state_reason"] = "Clinical treatment completed; process terminated and resources released."
                            curr["remaining_time"] = 0
                            curr["assigned_resources"] = {}
                            curr["waiting_on_resource"] = None
                            curr["waiting_on_primitive"] = None
                            curr["state_history"].append({
                                "from": prev_st,
                                "to": ProcessState.TERMINATED,
                                "timestamp": now,
                                "reason": curr["state_reason"],
                            })
                            self.free_memory(pid)
                        elif status == "Waiting" and curr["state"] == ProcessState.TERMINATED:
                            # Reopened via database
                            curr["state"] = ProcessState.READY if doctor_id else ProcessState.WAITING
                            curr["state_reason"] = "Process reopened in simulation."
                            curr["remaining_time"] = burst
                            curr["state_history"].append({
                                "from": ProcessState.TERMINATED,
                                "to": curr["state"],
                                "timestamp": now,
                                "reason": curr["state_reason"],
                            })
                            self.allocate_memory(pid, mem_pages)
                        elif (not doctor_id or not doctor_on_duty) and curr["state"] in (ProcessState.RUNNING, ProcessState.READY):
                            prev_st = curr["state"]
                            curr["state"] = ProcessState.WAITING
                            curr["state_reason"] = "Doctor unassigned or off duty; waiting for available doctor assignment."
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
                            memory_frames=p_state.get("memory_frames", []),
                            waiting_on_resource=p_state.get("waiting_on_resource"),
                            waiting_on_primitive=p_state.get("waiting_on_primitive"),
                            deadline=deadline,
                            deadline_status=p_state.get("deadline_status", "none"),
                            state_reason=p_state["state_reason"],
                            state_history=p_state["state_history"],
                        )
                    )
        except sqlite3.OperationalError:
            pass
        return processes

    def get_process_by_id(self, pid: int) -> Optional[HospitalProcess]:
        for p in self.get_processes():
            if p.pid == pid:
                return p
        return None

    def register_patient_process(self, patient_id: int, name: str, doctor_id: Optional[int] = None):
        """Registers a newly admitted patient process into the simulation lifecycle."""
        state = ProcessState.READY if doctor_id else ProcessState.WAITING
        reason = "Patient admitted and placed in ready queue." if doctor_id else "Patient admitted; waiting for on-duty doctor assignment."
        now = time.time()
        self._process_states[patient_id] = {
            "state": state,
            "state_reason": reason,
            "remaining_time": 4,
            "assigned_resources": {},
            "memory_pages": 2,
            "memory_kb": 8,
            "memory_frames": [],
            "waiting_on_resource": None,
            "waiting_on_primitive": None,
            "deadline": None,
            "deadline_status": "none",
            "state_history": [
                {"from": None, "to": ProcessState.NEW, "timestamp": now - 5, "reason": "Patient admission & intake triage"},
                {"from": ProcessState.NEW, "to": state, "timestamp": now, "reason": reason},
            ],
        }
        self.allocate_memory(patient_id, 2)
        self.record_event(
            event_type="PATIENT_ADMITTED",
            summary=f"Created process PCB for patient {name} (PID-{patient_id:03d}) in {state} state",
            category="PROCESS",
            payload={"pid": patient_id, "state": state, "doctor_id": doctor_id},
        )

    def mark_patient_completed(self, patient_id: int):
        self.transition_process(patient_id, ProcessState.TERMINATED, "Marked complete via doctor workload manager")

    def mark_patient_unassigned(self, patient_id: int):
        self.transition_process(patient_id, ProcessState.WAITING, "Doctor assignment removed; waiting for available doctor")

    def transition_process(self, pid: int, target_state: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Transitions a process to a target state, enforcing terminated semantics and updating cross-module state."""
        valid_states = {ProcessState.NEW, ProcessState.READY, ProcessState.RUNNING, ProcessState.WAITING, ProcessState.TERMINATED}
        if target_state not in valid_states:
            return {"success": False, "error": f"Invalid state '{target_state}'"}

        self.get_processes()
        if pid not in self._process_states:
            return {"success": False, "error": f"Process PID-{pid} not found in active simulation"}

        current = self._process_states[pid]
        old_state = current["state"]

        # Enforce Terminated Process Semantics (Section 10)
        if old_state == ProcessState.TERMINATED and target_state != ProcessState.TERMINATED:
            return {
                "success": False,
                "error": f"Process PID-{pid:03d} has TERMINATED. Terminated is a permanent terminal state; processes cannot spontaneously resume execution. Use explicit re-admit action.",
            }

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

        # Synchronize resources, locks, memory, and database status
        if target_state == ProcessState.TERMINATED:
            current["remaining_time"] = 0
            current["assigned_resources"] = {}
            current["waiting_on_resource"] = None
            current["waiting_on_primitive"] = None

            # Free memory frames
            self.free_memory(pid)

            # Release any locks/semaphores held
            for prim_id, prim in self._primitives.items():
                if prim.holder_pid == pid or pid in prim.holders:
                    self.release_lock(pid, prim_id)

            # Release any resources held
            for res_id, res in self._resources.items():
                if pid in res.holders:
                    self.release_resource(pid, res_id, units=res.allocated)

            try:
                with self._get_db() as conn:
                    conn.execute("UPDATE patients SET status='Completed' WHERE id=?", (pid,))
            except Exception:
                pass

        elif target_state == ProcessState.RUNNING:
            try:
                with self._get_db() as conn:
                    conn.execute("UPDATE patients SET status='Waiting' WHERE id=?", (pid,))
            except Exception:
                pass

        elif target_state in (ProcessState.READY, ProcessState.WAITING, ProcessState.NEW):
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
            category="PROCESS",
            payload={"pid": pid, "from": old_state, "to": target_state, "reason": transition_reason},
        )

        return {
            "success": True,
            "pid": pid,
            "old_state": old_state,
            "new_state": target_state,
            "reason": transition_reason,
        }

    def readmit_patient(self, pid: int) -> Dict[str, Any]:
        """Re-admits a completed/terminated patient into the active simulation."""
        self.get_processes()
        if pid not in self._process_states:
            return {"success": False, "error": f"Process PID-{pid:03d} not found."}

        with self._get_db() as conn:
            conn.execute("UPDATE patients SET status='Waiting' WHERE id=?", (pid,))
            row = conn.execute("SELECT burst_time, doctor_id FROM patients WHERE id=?", (pid,)).fetchone()
            burst = row["burst_time"] if row else 4
            doctor_id = row["doctor_id"] if row else None

        st = self._process_states[pid]
        st["state"] = ProcessState.READY if doctor_id else ProcessState.WAITING
        st["remaining_time"] = burst
        st["assigned_resources"] = {}
        st["waiting_on_resource"] = None
        st["waiting_on_primitive"] = None
        st["state_reason"] = "Patient re-admitted into clinical queue as fresh OS process."
        st["state_history"].append({
            "from": ProcessState.TERMINATED,
            "to": st["state"],
            "timestamp": time.time(),
            "reason": st["state_reason"],
        })
        self.allocate_memory(pid)
        self.record_event(
            event_type="PATIENT_READMITTED",
            summary=f"PID-{pid:03d} re-admitted into simulation (State: {st['state']})",
            category="PROCESS",
            payload={"pid": pid, "state": st["state"]},
        )
        return {"success": True, "message": f"PID-{pid:03d} successfully re-admitted to ready queue."}

    def step_lifecycle(self) -> Dict[str, Any]:
        """Advances one process through the simulation lifecycle in a coherent order."""
        processes = self.get_processes()
        if not processes:
            return {"success": False, "message": "No processes in simulation to advance."}

        # 1. Advance NEW processes -> READY or WAITING
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
            for p in processes:
                if p.state == ProcessState.RUNNING and p.doctor_id == candidate.doctor_id:
                    self.transition_process(p.pid, ProcessState.READY, "Preempted by scheduler for higher-priority process")
                    break
            return self.transition_process(candidate.pid, ProcessState.RUNNING, f"Dispatched to Dr. {candidate.doctor_name or 'Staff'}")

        # 3. Advance RUNNING processes -> WAITING or TERMINATED
        running_procs = [p for p in processes if p.state == ProcessState.RUNNING]
        if running_procs:
            candidate = running_procs[0]
            visited_waiting = any(h.get("to") == ProcessState.WAITING for h in candidate.state_history)
            if not visited_waiting:
                return self.transition_process(candidate.pid, ProcessState.WAITING, "Blocked on pending diagnostic lab test / specialist resource")
            else:
                return self.transition_process(candidate.pid, ProcessState.TERMINATED, "Treatment burst completed; process discharged")

        # 4. Advance WAITING processes -> READY
        waiting_procs = [p for p in processes if p.state == ProcessState.WAITING]
        if waiting_procs:
            candidate = waiting_procs[0]
            return self.transition_process(candidate.pid, ProcessState.READY, "Diagnostic lab test completed; returned to ready queue")

        return {"success": True, "message": "Simulation lifecycle cycle complete."}

    # =========================================================================
    # Modules 3, 4, 9: CPU Scheduling & Gantt Timeline
    # =========================================================================
    def get_scheduler_state(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        """Runs the scheduling simulator on active waiting patients."""
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
            category="SCHEDULER",
            payload={"algorithm": res["algorithm"], "process_count": len(patient_dicts), "quantum": quantum},
        )
        return res

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
            self.get_processes()
            for other_pid, st in list(self._process_states.items()):
                if other_pid != pid and st["state"] == ProcessState.RUNNING:
                    self.transition_process(
                        other_pid,
                        ProcessState.READY,
                        f"Preempted by scheduler at t={start}; returned to ready queue."
                    )

            reason = f"Dispatched by {algorithm} scheduler [slice {start}–{end}]"
            if slice_type == "emergency_preempt":
                reason = f"Clinical Emergency preemption dispatch [slice {start}–{end}]"
            elif slice_type == "quantum_expired":
                reason = f"Executing quantum slice [slice {start}–{end}]"

            self.transition_process(pid, ProcessState.RUNNING, reason)
            if pid in self._process_states:
                self._process_states[pid]["remaining_time"] = curr_slice.get("remaining_after", 0)

            if slice_type == "completed":
                self.transition_process(
                    pid,
                    ProcessState.TERMINATED,
                    f"Clinical burst completed at t={end}; process terminated."
                )
            elif slice_type in ("quantum_expired", "preempted", "emergency_preempt"):
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
            category="SCHEDULER",
            payload={},
        )
        return {
            "success": True,
            "message": "Scheduler stepping reset. Waiting processes restored to ready queue.",
            "step_index": 0,
        }

    # =========================================================================
    # Module 5: Doctor & Hospital Resource Manager
    # =========================================================================
    def get_resources(self) -> List[HospitalResource]:
        """Returns the dynamic inventory of hospital resources."""
        try:
            with self._get_db() as conn:
                on_duty = conn.execute("SELECT COUNT(*) FROM doctors WHERE on_duty=1").fetchone()[0]
                busy = conn.execute("SELECT COUNT(DISTINCT doctor_id) FROM patients WHERE status!='Completed' AND doctor_id IS NOT NULL").fetchone()[0]
            if "RES_DOC" in self._resources:
                self._resources["RES_DOC"].total = max(1, on_duty)
                self._resources["RES_DOC"].allocated = min(self._resources["RES_DOC"].total, busy)
                self._resources["RES_DOC"].available = max(0, self._resources["RES_DOC"].total - self._resources["RES_DOC"].allocated)
        except Exception:
            pass
        return list(self._resources.values())

    def _normalize_resource_id(self, r_id: str) -> str:
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

    def _normalize_primitive_id(self, p_id: str) -> str:
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

    def request_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        """Requests units of a hospital resource for an active process."""
        self.get_processes()
        if pid not in self._process_states:
            return {"success": False, "error": f"Process PID-{pid:03d} not found."}
        p_state = self._process_states[pid]
        if p_state["state"] == ProcessState.TERMINATED:
            return {"success": False, "error": f"Process PID-{pid:03d} has TERMINATED. Cannot request resources."}

        norm_id = self._normalize_resource_id(resource_id)
        if norm_id not in self._resources:
            return {"success": False, "error": f"Unknown resource ID '{resource_id}'."}
        resource_id = norm_id

        res = self._resources[resource_id]
        if units < 1:
            return {"success": False, "error": "Request units must be >= 1."}

        if res.available >= units:
            # Safe and available -> Allocate
            res.available -= units
            res.allocated += units
            if pid not in res.holders:
                res.holders.append(pid)
            p_state["assigned_resources"][res.name] = p_state["assigned_resources"].get(res.name, 0) + units
            p_state["waiting_on_resource"] = None

            self.record_event(
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
            # Resource exhausted -> Block process to WAITING
            if pid not in res.wait_queue:
                res.wait_queue.append(pid)
            p_state["waiting_on_resource"] = resource_id
            self.transition_process(
                pid,
                ProcessState.WAITING,
                f"Blocked waiting for {units} unit(s) of unavailable {res.name}."
            )
            self.record_event(
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

    def release_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        """Releases units of a hospital resource held by a process."""
        self.get_processes()
        norm_id = self._normalize_resource_id(resource_id)
        if norm_id not in self._resources:
            return {"success": False, "error": f"Unknown resource ID '{resource_id}'."}
        resource_id = norm_id
        res = self._resources[resource_id]

        if pid in self._process_states:
            p_state = self._process_states[pid]
            current_held = p_state["assigned_resources"].get(res.name, 0)
            released = min(units, current_held)
            p_state["assigned_resources"][res.name] = max(0, current_held - released)
            if p_state["assigned_resources"][res.name] == 0:
                del p_state["assigned_resources"][res.name]
                if pid in res.holders:
                    res.holders.remove(pid)
        else:
            released = units
            if pid in res.holders:
                res.holders.remove(pid)

        res.allocated = max(0, res.allocated - released)
        res.available = min(res.total, res.available + released)

        self.record_event(
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
            if next_pid in self._process_states:
                self._process_states[next_pid]["assigned_resources"][res.name] = (
                    self._process_states[next_pid]["assigned_resources"].get(res.name, 0) + 1
                )
                self._process_states[next_pid]["waiting_on_resource"] = None
                self.transition_process(
                    next_pid,
                    ProcessState.READY,
                    f"Acquired {res.name}; unblocked from wait queue to READY"
                )
                woken_pids.append(next_pid)
                self.record_event(
                    event_type="RESOURCE_UNBLOCK",
                    summary=f"Unblocked PID-{next_pid:03d} after acquiring {res.name} (READY)",
                    category="RESOURCE",
                    payload={"pid": next_pid, "resource_id": resource_id},
                )

        return {
            "success": True,
            "released": released,
            "available": res.available,
            "woken_pids": woken_pids,
            "message": f"Released {released} unit(s) of {res.name}." + (f" Woke PID(s): {woken_pids}." if woken_pids else ""),
            "resource": res.to_dict(),
        }

    # =========================================================================
    # Modules 6 & 7: Resource Allocation Graph & Deadlock (Banker's)
    # =========================================================================
    def get_deadlock_state(
        self,
        allocation: Optional[List[List[int]]] = None,
        maximum: Optional[List[List[int]]] = None,
        available: Optional[List[int]] = None,
    ) -> DeadlockState:
        """Calculates Banker's safety state and builds the live Resource Allocation Graph with cycle detection."""
        active_resources = [r for r in self.get_resources() if r.resource_id != "RES_DOC"]
        resource_names = [r.name for r in active_resources]
        avail_vector = available if available is not None else [r.available for r in active_resources]

        processes = [p for p in self.get_processes() if p.state != ProcessState.TERMINATED]
        if not processes:
            # Fallback to canonical default matrices for demonstration if no active processes
            alloc = allocation if allocation is not None else [[0, 1, 0], [2, 0, 0], [3, 0, 2], [2, 1, 1], [0, 0, 2]]
            max_m = maximum if maximum is not None else [[7, 5, 3], [3, 2, 2], [9, 0, 2], [2, 2, 2], [4, 3, 3]]
            avail = available if available is not None else [3, 3, 2]
            p_names = [f"P{i}" for i in range(len(alloc))]
            r_names = ["ICU Beds", "Ventilators", "Patient Monitors"]
        else:
            p_names = [f"PID-{p.pid:03d}" for p in processes]
            r_names = resource_names
            alloc = []
            max_m = []
            for p in processes:
                row_alloc = []
                row_max = []
                for r in active_resources:
                    held = p.assigned_resources.get(r.name, 0)
                    row_alloc.append(held)
                    # Deterministic claim based on priority and duration
                    if r.resource_id == "RES_ICU":
                        claim = max(held, 2 if p.priority == 1 else (1 if p.priority == 2 else 0))
                    elif r.resource_id == "RES_VENT":
                        claim = max(held, 1 if p.priority <= 2 else 0)
                    elif r.resource_id == "RES_MONITOR":
                        claim = max(held, 1)
                    else:
                        claim = max(held, 1 if p.burst_time >= 5 else 0)
                    row_max.append(claim)
                alloc.append(row_alloc)
                max_m.append(row_max)
            avail = avail_vector

        # Override with explicit parameters if supplied
        if allocation is not None:
            alloc = allocation
        if maximum is not None:
            max_m = maximum
        if available is not None:
            avail = available

        # Build live RAG edges
        rag_edges: List[Dict[str, Any]] = []
        for i, p_name in enumerate(p_names):
            for j, r_name in enumerate(r_names):
                if i < len(alloc) and j < len(alloc[i]) and alloc[i][j] > 0:
                    rag_edges.append({
                        "from": r_name, "to": p_name,
                        "units": alloc[i][j], "type": "allocation"
                    })
        # Request edges from resource wait queues
        for r in active_resources:
            for w_pid in r.wait_queue:
                rag_edges.append({
                    "from": f"PID-{w_pid:03d}", "to": r.name,
                    "units": 1, "type": "request"
                })

        # Build RAG nodes
        rag_nodes: List[Dict[str, Any]] = []
        for p in processes:
            st = p.state.value if hasattr(p.state, 'value') else str(p.state)
            rag_nodes.append({
                "id": f"PID-{p.pid:03d}",
                "label": p.name,
                "type": "process",
                "state": st,
            })
        if not rag_nodes:
            for p_name in p_names:
                rag_nodes.append({
                    "id": p_name,
                    "label": p_name,
                    "type": "process",
                    "state": "READY",
                })
        for r in active_resources:
            rag_nodes.append({
                "id": r.name,
                "label": r.name,
                "type": "resource",
                "instances": {"total": r.total, "available": r.available, "allocated": r.allocated},
            })

        # Cycle detection on RAG
        cycle_detected, cycle_nodes = self._detect_rag_cycle_with_path(rag_edges)

        try:
            banker_res = bankers_algorithm(alloc, max_m, avail)
            safe = banker_res["safe"]
            safe_seq = [p_names[int(s[1:])] if s.startswith("P") and s[1:].isdigit() and int(s[1:]) < len(p_names) else s for s in banker_res["safe_sequence"]]
            need = banker_res["need"]
            msg = banker_res["message"]
        except Exception as exc:
            safe = False
            safe_seq = []
            need = []
            msg = f"Banker evaluation: {exc}"

        return DeadlockState(
            safe=safe,
            safe_sequence=safe_seq,
            allocation_matrix=alloc,
            maximum_matrix=max_m,
            need_matrix=need,
            available_vector=avail,
            resource_names=r_names[:len(avail)],
            process_names=p_names,
            cycle_detected=cycle_detected,
            rag_edges=rag_edges,
            rag_nodes=rag_nodes,
            cycle_nodes=cycle_nodes,
            message=msg,
        )

    def _detect_rag_cycle_with_path(self, edges: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Runs Depth-First Search cycle detection on the Resource Allocation Graph and returns cycle path if any."""
        adj: Dict[str, List[str]] = {}
        nodes = set()
        for e in edges:
            u = e["from"]
            v = e["to"]
            adj.setdefault(u, []).append(v)
            nodes.add(u)
            nodes.add(v)

        visited = set()
        rec_stack = []

        def dfs(node: str) -> Optional[List[str]]:
            visited.add(node)
            rec_stack.append(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    cycle = dfs(neighbor)
                    if cycle:
                        return cycle
                elif neighbor in rec_stack:
                    idx = rec_stack.index(neighbor)
                    return rec_stack[idx:] + [neighbor]
            rec_stack.pop()
            return None

        for node in sorted(nodes):
            if node not in visited:
                cycle = dfs(node)
                if cycle:
                    return True, cycle
        return False, []

    def _detect_rag_cycle(self, edges: List[Dict[str, Any]]) -> bool:
        has_c, _ = self._detect_rag_cycle_with_path(edges)
        return has_c

    # =========================================================================
    # Module 8: Simulated Memory Manager
    # =========================================================================
    def get_memory_state(self) -> MemoryState:
        """Returns physical memory allocation, frame map, and pressure level."""
        processes = [p for p in self.get_processes() if p.state != ProcessState.TERMINATED]
        total_frames = self._memory_total_frames
        frame_size_kb = self._memory_frame_size_kb

        process_allocations: Dict[int, Dict[str, Any]] = {}
        for p in processes:
            frames_for_proc = [f.frame_id for f in self._memory_frames if f.pid == p.pid]
            process_allocations[p.pid] = {
                "process_name": p.name,
                "pages": p.memory_pages,
                "frames": frames_for_proc,
                "allocated_kb": len(frames_for_proc) * frame_size_kb,
            }

        allocated_count = sum(1 for f in self._memory_frames if f.status == "allocated")
        free_frames = total_frames - allocated_count

        if allocated_count <= 8:
            pressure = "NORMAL"
        elif allocated_count <= 13:
            pressure = "MODERATE"
        else:
            pressure = "HIGH"

        return MemoryState(
            total_frames=total_frames,
            free_frames=max(0, free_frames),
            frame_size_kb=frame_size_kb,
            memory_pressure=pressure,
            used_kb=allocated_count * frame_size_kb,
            total_kb=total_frames * frame_size_kb,
            frames=self._memory_frames,
            process_allocations=process_allocations,
        )

    def allocate_memory(self, pid: int, pages: Optional[int] = None) -> Dict[str, Any]:
        """Allocates physical memory frames for a process's pages."""
        pages_needed = pages if pages is not None else 2
        free_frames = [f for f in self._memory_frames if f.status == "free"]

        if len(free_frames) < pages_needed:
            self.record_event(
                event_type="MEMORY_ALLOCATION_FAILED",
                summary=f"Memory Allocation Failed for PID-{pid:03d}: Needs {pages_needed} pages, only {len(free_frames)} free frames.",
                category="MEMORY",
                payload={"pid": pid, "pages_needed": pages_needed, "free_frames": len(free_frames)},
            )
            if pid in self._process_states:
                self.transition_process(
                    pid,
                    ProcessState.WAITING,
                    f"Blocked waiting for {pages_needed} physical memory frame(s); only {len(free_frames)} available."
                )
            return {
                "success": False,
                "allocated": False,
                "error": f"Memory pressure: Insufficient physical frames. Needed {pages_needed}, available {len(free_frames)}.",
                "message": f"Memory pressure: Insufficient physical frames. Needed {pages_needed}, available {len(free_frames)}.",
            }

        allocated_ids = []
        for pg_idx in range(pages_needed):
            frame = free_frames[pg_idx]
            frame.status = "allocated"
            frame.pid = pid
            frame.page_number = pg_idx
            allocated_ids.append(frame.frame_id)

        if pid in self._process_states:
            self._process_states[pid]["memory_frames"] = allocated_ids
            self._process_states[pid]["memory_pages"] = pages_needed
            self._process_states[pid]["memory_kb"] = pages_needed * self._memory_frame_size_kb

        self.record_event(
            event_type="MEMORY_ALLOC",
            summary=f"Allocated {pages_needed} pages ({pages_needed * 4} KB) for PID-{pid:03d} -> Frames {allocated_ids}",
            category="MEMORY",
            payload={"pid": pid, "pages": pages_needed, "frames": allocated_ids},
        )
        return {
            "success": True,
            "allocated": True,
            "pages": pages_needed,
            "frames": allocated_ids,
            "message": f"Allocated {pages_needed} pages into frames {allocated_ids} for PID-{pid:03d}.",
        }

    def free_memory(self, pid: int) -> Dict[str, Any]:
        """Frees all physical memory frames allocated to a process."""
        freed_frames = []
        for f in self._memory_frames:
            if f.pid == pid:
                f.status = "free"
                f.pid = None
                f.page_number = None
                freed_frames.append(f.frame_id)

        if pid in self._process_states:
            self._process_states[pid]["memory_frames"] = []

        if freed_frames:
            self.record_event(
                event_type="MEMORY_FREE",
                summary=f"Freed {len(freed_frames)} frames ({len(freed_frames) * 4} KB) from PID-{pid:03d}: Frames {freed_frames}",
                category="MEMORY",
                payload={"pid": pid, "freed_frames": freed_frames},
            )

        # Unblock any processes that were waiting on memory
        for wait_pid, p_st in list(self._process_states.items()):
            if p_st["state"] == ProcessState.WAITING and "memory" in p_st.get("state_reason", "").lower():
                cur_free = sum(1 for f in self._memory_frames if f.status == "free")
                req_pages = p_st.get("memory_pages", 2)
                if cur_free >= req_pages:
                    self.allocate_memory(wait_pid, req_pages)
                    self.transition_process(
                        wait_pid,
                        ProcessState.READY,
                        f"Physical frames freed; unblocked PID-{wait_pid:03d} to READY queue."
                    )
                    break

        return {
            "success": True,
            "freed_count": len(freed_frames),
            "freed_frames": freed_frames,
            "message": f"Freed {len(freed_frames)} frames for PID-{pid:03d}.",
        }

    # =========================================================================
    # Module 10: Concurrency & Synchronization
    # =========================================================================
    def get_synchronization_state(self) -> SynchronizationState:
        """Returns state of synchronization primitives (mutexes & counting semaphores)."""
        recent = [
            {"timestamp": e.timestamp, "primitive": e.payload.get("primitive_id", "LOCK"), "action": e.event_type, "summary": e.summary}
            for e in self._event_log if e.category == "SYNC"
        ][-10:]
        return SynchronizationState(
            primitives=list(self._primitives.values()),
            recent_events=recent,
        )

    def acquire_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        """Attempts to acquire a synchronization primitive (mutex or semaphore)."""
        self.get_processes()
        if pid not in self._process_states:
            return {"success": False, "error": f"Process PID-{pid:03d} not found."}
        p_state = self._process_states[pid]
        if p_state["state"] == ProcessState.TERMINATED:
            return {"success": False, "error": f"Process PID-{pid:03d} has TERMINATED. Cannot acquire lock."}

        norm_id = self._normalize_primitive_id(primitive_id)
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
                p_state["waiting_on_primitive"] = None
                self.record_event(
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
                p_state["waiting_on_primitive"] = primitive_id
                self.transition_process(
                    pid,
                    ProcessState.WAITING,
                    f"Blocked waiting on locked Mutex: {prim.name} (held by PID-{prim.holder_pid:03d})"
                )
                self.record_event(
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
                p_state["waiting_on_primitive"] = None
                self.record_event(
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
                p_state["waiting_on_primitive"] = primitive_id
                self.transition_process(
                    pid,
                    ProcessState.WAITING,
                    f"Blocked waiting on Semaphore: {prim.name} (0 slots available)"
                )
                self.record_event(
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

    def release_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        """Releases a synchronization primitive (mutex or semaphore)."""
        self.get_processes()
        norm_id = self._normalize_primitive_id(primitive_id)
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
            self.record_event(
                event_type="LOCK_RELEASE",
                summary=f"PID-{pid:03d} released Mutex: {prim.name}",
                category="SYNC",
                payload={"pid": pid, "primitive_id": primitive_id},
            )
            if prim.wait_queue:
                woken_pid = prim.wait_queue.pop(0)
                prim.holder_pid = woken_pid
                prim.holders = [woken_pid]
                if woken_pid in self._process_states:
                    self._process_states[woken_pid]["waiting_on_primitive"] = None
                    self.transition_process(
                        woken_pid,
                        ProcessState.READY,
                        f"Acquired Mutex: {prim.name}; unblocked to READY queue"
                    )
                    self.record_event(
                        event_type="PROCESS_WAKE",
                        summary=f"PID-{woken_pid:03d} woke up and acquired Mutex: {prim.name} (READY)",
                        category="SYNC",
                        payload={"pid": woken_pid, "primitive_id": primitive_id},
                    )
            else:
                prim.locked = False
                prim.holder_pid = None
                prim.holders = []
        else:  # Counting semaphore
            if pid in prim.holders:
                prim.holders.remove(pid)
            self.record_event(
                event_type="SEMAPHORE_POST",
                summary=f"PID-{pid:03d} released Semaphore slot on {prim.name}",
                category="SYNC",
                payload={"pid": pid, "primitive_id": primitive_id},
            )
            if prim.wait_queue:
                woken_pid = prim.wait_queue.pop(0)
                if woken_pid not in prim.holders:
                    prim.holders.append(woken_pid)
                if woken_pid in self._process_states:
                    self._process_states[woken_pid]["waiting_on_primitive"] = None
                    self.transition_process(
                        woken_pid,
                        ProcessState.READY,
                        f"Acquired Semaphore: {prim.name} slot; unblocked to READY queue"
                    )
                    self.record_event(
                        event_type="PROCESS_WAKE",
                        summary=f"PID-{woken_pid:03d} woke up and acquired Semaphore: {prim.name} (READY)",
                        category="SYNC",
                        payload={"pid": woken_pid, "primitive_id": primitive_id},
                    )
            else:
                prim.count = min(prim.max_count, prim.count + 1)

        return {
            "success": True,
            "woken_pid": woken_pid,
            "message": f"Released '{prim.name}'." + (f" Woke PID-{woken_pid:03d}." if woken_pid else ""),
            "primitive": prim.to_dict(),
        }

    # =========================================================================
    # Module 11: Event Stream & Simulated System Calls
    # =========================================================================
    def get_events(self, limit: int = 50, category: Optional[str] = None) -> List[KernelEvent]:
        """Returns the stream of simulated system events, optionally filtered by category."""
        events = self._event_log
        if category and category.upper() != "ALL":
            events = [e for e in events if e.category.upper() == category.upper()]
        return events[-limit:]

    def get_simulated_syscalls(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns simulated OS syscall representations derived from discrete simulation actions."""
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

    # =========================================================================
    # Demonstration Scenarios (Section 48)
    # =========================================================================
    def run_demo_scenario(self, scenario_id: str) -> Dict[str, Any]:
        """Provisions and executes an educational OS demonstration scenario."""
        scenario_key = scenario_id.lower().replace("-", "_")

        if scenario_key in ("scenario_a", "scenario_a_fcfs", "a"):
            self._reset_simulation_data()
            with self._get_db() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient A (Long Surgery)', 0, 10, 3, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient B (Quick Checkup)', 0, 2, 3, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient C (Quick Checkup)', 0, 2, 3, 'Waiting', 'General Medicine', 1)")
            res = self.get_scheduler_state("FCFS")
            self.record_event(
                event_type="SCENARIO_RUN",
                summary="Demo Scenario A: FCFS Convoy Effect demonstrated (Long treatment blocks short patients)",
                category="SCHEDULER",
                payload={"scenario": "A", "algorithm": "FCFS"},
            )
            return {
                "success": True,
                "scenario": "A",
                "title": "Scenario A: FCFS & Convoy Effect",
                "message": "Scenario A: FCFS & Convoy Effect executed successfully.",
                "description": "Patient A (10u) executes first, forcing Patients B & C (2u each) to wait the entire 10 units. Demonstrates why non-preemptive FCFS can suffer high average waiting latency.",
                "scheduler_state": res,
            }

        elif scenario_key in ("scenario_b", "scenario_b_rr", "b"):
            self._reset_simulation_data()
            with self._get_db() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Alpha', 0, 6, 2, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Beta', 0, 5, 2, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Gamma', 0, 3, 2, 'Waiting', 'General Medicine', 1)")
            res = self.get_scheduler_state("ROUND_ROBIN", quantum=2)
            self.record_event(
                event_type="SCENARIO_RUN",
                summary="Demo Scenario B: Round Robin time quantum (Q=2u) slicing and queue rotation demonstrated",
                category="SCHEDULER",
                payload={"scenario": "B", "algorithm": "ROUND_ROBIN", "quantum": 2},
            )
            return {
                "success": True,
                "scenario": "B",
                "title": "Scenario B: Round Robin Time-Slicing",
                "message": "Scenario B: Round Robin Time-Slicing executed successfully.",
                "description": "Each patient receives 2 units of execution before yielding the CPU and returning to the ready queue tail. Demonstrates fair sharing and responsiveness.",
                "scheduler_state": res,
            }

        elif scenario_key in ("scenario_c", "scenario_c_srtf", "c"):
            self._reset_simulation_data()
            with self._get_db() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Long Treatment Patient', 0, 8, 3, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Short Emergency Arrival', 2, 2, 2, 'Waiting', 'General Medicine', 1)")
            res = self.get_scheduler_state("SRTF")
            self.record_event(
                event_type="SCENARIO_RUN",
                summary="Demo Scenario C: SRTF preemption at t=2 demonstrated (Shorter job preempts longer running job)",
                category="SCHEDULER",
                payload={"scenario": "C", "algorithm": "SRTF"},
            )
            return {
                "success": True,
                "scenario": "C",
                "title": "Scenario C: SRTF Preemption",
                "message": "Scenario C: SRTF Preemption executed successfully.",
                "description": "Long Treatment Patient executes from t=0 to t=2. At t=2, Short Emergency Arrival arrives with remaining burst 2u < 6u, immediately preempting Long Treatment Patient.",
                "scheduler_state": res,
            }

        elif scenario_key in ("scenario_d", "scenario_d_emergency", "d"):
            self._reset_simulation_data()
            with self._get_db() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Routine Clinic Visit', 0, 8, 3, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('CODE RED Emergency (Trauma)', 2, 3, 1, 'Waiting', 'General Medicine', 1)")
            res = self.get_scheduler_state("PRIORITY")
            self.record_event(
                event_type="SCENARIO_RUN",
                summary="Demo Scenario D: Code Red Priority 1 Emergency preemption demonstrated",
                category="SCHEDULER",
                payload={"scenario": "D", "algorithm": "PRIORITY"},
            )
            return {
                "success": True,
                "scenario": "D",
                "title": "Scenario D: Clinical Emergency Preemption",
                "message": "Scenario D: Clinical Emergency Preemption executed successfully.",
                "description": "Routine Clinic Visit (P3) is executing when CODE RED Emergency (P1) arrives at t=2. Triggers an EMERGENCY_PREEMPT event, immediately preempting P3 to prioritize trauma.",
                "scheduler_state": res,
            }

        elif scenario_key in ("scenario_e", "scenario_e_resource_block", "e"):
            self._reset_simulation_data()
            with self._get_db() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient A (ICU Resident)', 0, 5, 2, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient B (ICU Hopeful)', 0, 5, 2, 'Waiting', 'General Medicine', 1)")
            procs = self.get_processes()
            p1_id = procs[0].pid
            p2_id = procs[1].pid

            self._resources["RES_VENT"].available = 0
            self._resources["RES_VENT"].allocated = self._resources["RES_VENT"].total
            self._resources["RES_VENT"].holders = [p1_id]

            req_res = self.request_resource(p2_id, "RES_VENT", 1)

            return {
                "success": True,
                "scenario": "E",
                "title": "Scenario E: Hospital Resource Contention & Blocking",
                "message": "Scenario E: Hospital Resource Contention & Blocking executed successfully.",
                "description": f"Patient A holds all Ventilators. Patient B (PID-{p2_id:03d}) requests a Ventilator and is blocked into the resource wait queue (WAITING).",
                "request_result": req_res,
                "resources": [r.to_dict() for r in self.get_resources()],
            }

        elif scenario_key in ("scenario_f", "scenario_f_banker", "f"):
            self._reset_simulation_data()
            deadlock_state = self.get_deadlock_state()
            self.record_event(
                event_type="DEADLOCK_CHECK",
                summary="Demo Scenario F: Banker's Safety Algorithm evaluated. Safe sequence computed.",
                category="DEADLOCK",
                payload={"safe": deadlock_state.safe, "sequence": deadlock_state.safe_sequence},
            )
            return {
                "success": True,
                "scenario": "F",
                "title": "Scenario F: Banker's Algorithm Deadlock Avoidance",
                "message": "Scenario F: Banker's Algorithm Deadlock Avoidance executed successfully.",
                "description": f"Evaluates current Allocation, Maximum, and Available vectors. Safe Sequence found: {' -> '.join(deadlock_state.safe_sequence)}. Requests that would leave the system unsafe are actively denied.",
                "deadlock_state": deadlock_state.to_dict(),
            }

        elif scenario_key in ("scenario_g", "scenario_g_memory", "g"):
            self._reset_simulation_data()
            for i in range(15):
                self._memory_frames[i].status = "allocated"
                self._memory_frames[i].pid = 99
                self._memory_frames[i].page_number = i
            with self._get_db() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('High Memory Patient', 0, 10, 2, 'Waiting', 'General Medicine', 1)")
            procs = self.get_processes()
            target_pid = procs[-1].pid
            mem_res = self.allocate_memory(target_pid, pages=4)

            return {
                "success": True,
                "scenario": "G",
                "title": "Scenario G: Memory Pressure & Allocation Failure",
                "message": "Scenario G: Memory Pressure & Allocation Failure executed successfully.",
                "description": "15 of 16 physical memory frames (94%) are allocated. High Memory Patient requests 4 pages, triggering HIGH memory pressure and MEMORY_ALLOCATION_FAILED, blocking the process until frames free up.",
                "memory_result": mem_res,
                "memory_state": self.get_memory_state().to_dict(),
            }

        elif scenario_key in ("scenario_h", "scenario_h_sync", "h"):
            self._reset_simulation_data()
            with self._get_db() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Surgical Patient A', 0, 4, 2, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Surgical Patient B', 0, 4, 2, 'Waiting', 'General Medicine', 1)")
            procs = self.get_processes()
            p1_id = procs[0].pid
            p2_id = procs[1].pid

            acq1 = self.acquire_lock(p1_id, "MUTEX_OT")
            acq2 = self.acquire_lock(p2_id, "MUTEX_OT")

            return {
                "success": True,
                "scenario": "H",
                "title": "Scenario H: Mutex Contention & Wait Queue",
                "message": "Scenario H: Mutex Contention & Wait Queue executed successfully.",
                "description": f"Surgical Patient A (PID-{p1_id:03d}) acquired the Operating Theatre Mutex. Surgical Patient B (PID-{p2_id:03d}) attempted to acquire the same theatre and was placed into the wait queue (WAITING).",
                "acquire_1": acq1,
                "acquire_2": acq2,
                "synchronization": self.get_synchronization_state().to_dict(),
            }

        return {"success": False, "error": f"Unknown scenario ID '{scenario_id}'. Choose A, B, C, D, E, F, G, or H."}

    def _reset_simulation_data(self):
        """Internal helper to reset simulation processes, resources, memory, and sync to clean state."""
        with self._get_db() as conn:
            conn.execute("DELETE FROM patients")
        self._process_states.clear()
        self._scheduler_step_index = 0
        self._cached_schedule = None

        for f in self._memory_frames:
            f.status = "free"
            f.pid = None
            f.page_number = None

        for p in self._primitives.values():
            p.locked = False
            p.holder_pid = None
            p.holders.clear()
            p.wait_queue.clear()
            p.count = p.max_count

        self._resources["RES_VENT"].available = 3
        self._resources["RES_VENT"].allocated = 1
        self._resources["RES_VENT"].holders = [1]
        self._resources["RES_VENT"].wait_queue.clear()

        self._resources["RES_ICU"].available = 4
        self._resources["RES_ICU"].allocated = 2
        self._resources["RES_ICU"].holders = [1]
        self._resources["RES_ICU"].wait_queue.clear()

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
        return {"success": False, "error": "Live kernel bridge is disconnected; scheduler stepping unavailable in Live Kernel mode."}

    def reset_scheduler(self) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; scheduler reset unavailable in Live Kernel mode."}

    def get_resources(self) -> List[HospitalResource]:
        return []

    def request_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; resource allocation unavailable."}

    def release_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; resource release unavailable."}

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
            memory_pressure="NORMAL",
            used_kb=0,
            total_kb=0,
            frames=[],
            process_allocations={},
        )

    def allocate_memory(self, pid: int, pages: Optional[int] = None) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; memory management unavailable."}

    def free_memory(self, pid: int) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; memory management unavailable."}

    def get_synchronization_state(self) -> SynchronizationState:
        return SynchronizationState(primitives=[], recent_events=[])

    def acquire_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; synchronization unavailable."}

    def release_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; synchronization unavailable."}

    def get_events(self, limit: int = 50, category: Optional[str] = None) -> List[KernelEvent]:
        return []

    def get_simulated_syscalls(self, limit: int = 50) -> List[Dict[str, Any]]:
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
        return {"success": False, "error": "Live kernel bridge is disconnected; cannot modify kernel process state."}

    def step_lifecycle(self) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; simulation stepping unavailable in Live Kernel mode."}

    def readmit_patient(self, pid: int) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; process re-admission unavailable in Live Kernel mode."}

    def run_demo_scenario(self, scenario_id: str) -> Dict[str, Any]:
        return {"success": False, "error": "Live kernel bridge is disconnected; demonstration scenarios unavailable in Live Kernel mode."}


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


provider_manager = ProviderManager()


def get_provider(mode: Optional[str] = None) -> HospitalOSProvider:
    """Convenience accessor for obtaining the current HospitalOS provider."""
    return provider_manager.get_provider(mode)

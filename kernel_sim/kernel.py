"""HospitalOS Built-In Educational Kernel Simulation Engine.

Authoritative kernel simulation coordinator tying together:
- PCB / Process Lifecycle Manager
- CPU Scheduler & Timeline Engine
- Memory Manager (4KB Paging & Frames)
- Hospital Resource Manager & Inventories
- Deadlock / Banker's Avoidance & RAG
- Concurrency & Synchronization Primitives
- Kernel Event Bus & Syscall Tracer
"""

from __future__ import annotations
import time
from typing import List, Dict, Optional, Any, Callable

from contracts import (
    ProcessState,
    HospitalProcess,
    HospitalResource,
    DeadlockState,
    MemoryState,
    SynchronizationState,
    KernelEvent,
    SystemSnapshot,
)

from .event_bus import KernelEventBus
from .process_manager import ProcessManager
from .memory_manager import MemoryManager
from .resource_manager import HospitalResourceManager
from .synchronization import SynchronizationManager
from .deadlock import DeadlockManager
from .scheduler import CPUScheduler


class HospitalKernelEngine:
    """The central HospitalOS Built-In Educational Kernel Simulation Engine."""

    def __init__(self):
        self.status: str = "UNINITIALIZED"
        self.simulation_clock: int = 0
        self.event_bus = KernelEventBus(max_capacity=300)
        self.process_manager = ProcessManager(self.event_bus)
        self.memory_manager = MemoryManager(self.event_bus, total_frames=16, frame_size_kb=4, process_manager=self.process_manager)
        self.resource_manager = HospitalResourceManager(self.event_bus, process_manager=self.process_manager)
        self.sync_manager = SynchronizationManager(self.event_bus, process_manager=self.process_manager)
        self.deadlock_manager = DeadlockManager(self.event_bus)
        self.scheduler = CPUScheduler(self.event_bus)
        self._on_state_change_callback: Optional[Callable[[int, str], None]] = None

        self.boot()

    def set_state_change_callback(self, callback: Optional[Callable[[int, str], None]]):
        """Registers a callback hook invoked when a process state changes."""
        self._on_state_change_callback = callback

    def boot(self):
        """Initializes and boots the kernel simulation engine."""
        self.status = "BOOTED"
        self.event_bus.record_event(
            event_type="KERNEL_BOOT",
            summary="HospitalOS Built-In Educational Kernel Simulation Engine initialized",
            category="SYSTEM",
            payload={"source": "simulation", "frames": 16, "frame_size_kb": 4},
        )

    def reset(self):
        """Deterministically resets runtime kernel state without corrupting persistent records."""
        self.simulation_clock = 0
        self.event_bus.clear()
        self.process_manager.reset()
        self.memory_manager.reset()
        self.resource_manager.reset()
        self.sync_manager.reset()
        self.scheduler.reset(self)
        self.status = "BOOTED"

        self.event_bus.record_event(
            event_type="KERNEL_RESET",
            summary="HospitalOS Kernel Simulation Engine reset to clean initial state.",
            category="SYSTEM",
            payload={"source": "simulation"},
        )

    def get_kernel_status(self) -> Dict[str, Any]:
        """Returns the high-level status of the kernel engine and its active subsystems."""
        procs = self.process_manager.get_all_processes()
        running_pid = self.scheduler.running_pid
        if running_pid is None:
            running_procs = [p.pid for p in procs if p.state == ProcessState.RUNNING]
            running_pid = running_procs[0] if running_procs else None

        return {
            "status": self.status,
            "clock": self.simulation_clock,
            "processes_count": len(procs),
            "running_pid": running_pid,
            "memory_used_kb": self.memory_manager.get_used_kb(),
            "memory_total_kb": self.memory_manager.get_total_kb(),
            "events_count": len(self.event_bus.get_events(limit=1000)),
            "mode": "simulation",
            "name": "HospitalOS Built-In Educational Kernel Simulation Engine",
        }

    # =========================================================================
    # Process & Lifecycle Coordination
    # =========================================================================
    def transition_process(self, pid: int, target_state: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Transitions a process to a target state, coordinating across all subsystems."""
        proc = self.process_manager.get_process(pid)

        def on_terminate(terminated_pid: int):
            self.memory_manager.free_memory(terminated_pid, proc=proc)
            self.sync_manager.release_all_for_process(terminated_pid)
            self.resource_manager.release_all_for_process(terminated_pid, proc=proc)
            if self._on_state_change_callback:
                self._on_state_change_callback(terminated_pid, ProcessState.TERMINATED)

        res = self.process_manager.transition_process(pid, target_state, reason=reason, on_terminate=on_terminate)

        if res.get("success") and target_state != ProcessState.TERMINATED and self._on_state_change_callback:
            self._on_state_change_callback(pid, target_state)

        return res

    def readmit_patient(self, pid: int, burst: int = 4, doctor_id: Optional[int] = None) -> Dict[str, Any]:
        """Re-admits a completed/terminated patient into the simulation."""
        def on_allocate(target_pid: int):
            self.allocate_memory(target_pid)

        res = self.process_manager.readmit_patient(pid, burst=burst, doctor_id=doctor_id, on_allocate=on_allocate)
        if res.get("success") and self._on_state_change_callback:
            self._on_state_change_callback(pid, "Waiting")
        return res

    def step_lifecycle(self) -> Dict[str, Any]:
        """Advances one process through the lifecycle in a coherent order."""
        return self.process_manager.step_lifecycle(self)

    # =========================================================================
    # Memory Management
    # =========================================================================
    def allocate_memory(self, pid: int, pages: Optional[int] = None) -> Dict[str, Any]:
        """Allocates physical memory frames, blocking the process on starvation."""
        proc = self.process_manager.get_process(pid)

        def on_fail_block():
            self.transition_process(
                pid,
                ProcessState.WAITING,
                f"Blocked waiting for {pages or 2} physical memory frame(s); memory exhausted."
            )

        return self.memory_manager.allocate_memory(pid, pages=pages, proc=proc, on_fail_block=on_fail_block)

    def free_memory(self, pid: int) -> Dict[str, Any]:
        """Frees physical memory frames and unblocks any process waiting on memory."""
        proc = self.process_manager.get_process(pid)

        def on_unblock():
            for p in self.process_manager.get_all_processes():
                if p.state == ProcessState.WAITING and "memory" in getattr(p, "state_reason", "").lower():
                    cur_free = sum(1 for f in self.memory_manager.frames if f.status == "free")
                    req_pages = p.memory_pages or 2
                    if cur_free >= req_pages:
                        self.allocate_memory(p.pid, req_pages)
                        self.transition_process(
                            p.pid,
                            ProcessState.READY,
                            f"Physical frames freed; unblocked PID-{p.pid:03d} to READY queue."
                        )
                        break

        return self.memory_manager.free_memory(pid, proc=proc, on_unblock=on_unblock)

    # =========================================================================
    # Hospital Resources
    # =========================================================================
    def request_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        """Requests hospital resources, blocking the process if unavailable."""
        proc = self.process_manager.get_process(pid)

        def on_block(reason: str):
            self.transition_process(pid, ProcessState.WAITING, reason)

        return self.resource_manager.request_resource(pid, resource_id, units=units, proc=proc, on_block=on_block)

    def release_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        """Releases hospital resources and unblocks waiters."""
        proc = self.process_manager.get_process(pid)

        def on_unblock(woken_pid: int, res_name: str):
            w_proc = self.process_manager.get_process(woken_pid)
            if w_proc:
                w_proc.assigned_resources[res_name] = w_proc.assigned_resources.get(res_name, 0) + 1
                w_proc.waiting_on_resource = None
            self.transition_process(woken_pid, ProcessState.READY, f"Acquired {res_name}; unblocked from wait queue to READY")

        return self.resource_manager.release_resource(pid, resource_id, units=units, proc=proc, on_unblock=on_unblock)

    # =========================================================================
    # Concurrency & Synchronization
    # =========================================================================
    def acquire_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        """Attempts to acquire a synchronization primitive, blocking on contention."""
        proc = self.process_manager.get_process(pid)

        def on_block(reason: str):
            self.transition_process(pid, ProcessState.WAITING, reason)

        return self.sync_manager.acquire_lock(pid, primitive_id, proc=proc, on_block=on_block)

    def release_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        """Releases a synchronization primitive and wakes the next waiter."""
        proc = self.process_manager.get_process(pid)

        def on_unblock(woken_pid: int, prim_name: str):
            w_proc = self.process_manager.get_process(woken_pid)
            if w_proc:
                w_proc.waiting_on_primitive = None
            self.transition_process(woken_pid, ProcessState.READY, f"Acquired {prim_name}; unblocked to READY queue")

        return self.sync_manager.release_lock(pid, primitive_id, proc=proc, on_unblock=on_unblock)

    # =========================================================================
    # Deadlock & Banker's Safety
    # =========================================================================
    def get_deadlock_state(
        self,
        allocation: Optional[List[List[int]]] = None,
        maximum: Optional[List[List[int]]] = None,
        available: Optional[List[int]] = None,
    ) -> DeadlockState:
        return self.deadlock_manager.get_deadlock_state(
            self.resource_manager.get_resources(),
            self.process_manager.get_all_processes(),
            allocation=allocation,
            maximum=maximum,
            available=available,
        )

    # =========================================================================
    # System Snapshot
    # =========================================================================
    def get_system_snapshot(self, algorithm: str = "FCFS") -> SystemSnapshot:
        return SystemSnapshot(
            timestamp=time.time(),
            mode="simulation",
            is_connected=True,
            message="HospitalOS Built-In Educational Kernel Simulation Engine active",
            processes=self.process_manager.get_all_processes(),
            scheduler=self.scheduler.cached_schedule or {},
            resources=self.resource_manager.get_resources(),
            deadlock=self.get_deadlock_state(),
            memory=self.memory_manager.get_memory_state(self.process_manager.get_all_processes()),
            synchronization=self.sync_manager.get_synchronization_state(),
            recent_events=self.event_bus.get_events(limit=20),
        )

    # =========================================================================
    # Demonstration Scenarios A–H
    # =========================================================================
    def run_demo_scenario(self, scenario_id: str, db_conn_factory: Callable[[], Any]) -> Dict[str, Any]:
        """Provisions and executes an educational OS demonstration scenario against the kernel."""
        scenario_key = scenario_id.lower().replace("-", "_")

        def reset_scenario_db():
            with db_conn_factory() as conn:
                conn.execute("DELETE FROM patients")
            self.process_manager.reset()
            self.memory_manager.reset()
            self.sync_manager.reset()
            self.resource_manager.reset()

        if scenario_key in ("scenario_a", "scenario_a_fcfs", "a"):
            reset_scenario_db()
            with db_conn_factory() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient A (Long Surgery)', 0, 10, 3, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient B (Quick Checkup)', 0, 2, 3, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient C (Quick Checkup)', 0, 2, 3, 'Waiting', 'General Medicine', 1)")
                rows = conn.execute("SELECT id, name, arrival_time, burst_time, priority, status FROM patients WHERE status='Waiting' ORDER BY id").fetchall()

            patient_dicts = [dict(r) for r in rows]
            res = self.scheduler.schedule(patient_dicts, "FCFS")
            self.event_bus.record_event(
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
            reset_scenario_db()
            with db_conn_factory() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Alpha', 0, 6, 2, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Beta', 0, 5, 2, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient Gamma', 0, 3, 2, 'Waiting', 'General Medicine', 1)")
                rows = conn.execute("SELECT id, name, arrival_time, burst_time, priority, status FROM patients WHERE status='Waiting' ORDER BY id").fetchall()

            patient_dicts = [dict(r) for r in rows]
            res = self.scheduler.schedule(patient_dicts, "ROUND_ROBIN", quantum=2)
            self.event_bus.record_event(
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
            reset_scenario_db()
            with db_conn_factory() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Long Treatment Patient', 0, 8, 3, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Short Emergency Arrival', 2, 2, 2, 'Waiting', 'General Medicine', 1)")
                rows = conn.execute("SELECT id, name, arrival_time, burst_time, priority, status FROM patients WHERE status='Waiting' ORDER BY id").fetchall()

            patient_dicts = [dict(r) for r in rows]
            res = self.scheduler.schedule(patient_dicts, "SRTF")
            self.event_bus.record_event(
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
            reset_scenario_db()
            with db_conn_factory() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Routine Clinic Visit', 0, 8, 3, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('CODE RED Emergency (Trauma)', 2, 3, 1, 'Waiting', 'General Medicine', 1)")
                rows = conn.execute("SELECT id, name, arrival_time, burst_time, priority, status FROM patients WHERE status='Waiting' ORDER BY id").fetchall()

            patient_dicts = [dict(r) for r in rows]
            res = self.scheduler.schedule(patient_dicts, "PRIORITY")
            self.event_bus.record_event(
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
            reset_scenario_db()
            with db_conn_factory() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient A (ICU Resident)', 0, 5, 2, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Patient B (ICU Hopeful)', 0, 5, 2, 'Waiting', 'General Medicine', 1)")
                rows = conn.execute("SELECT p.*, d.name AS doctor_name, d.on_duty AS doctor_on_duty FROM patients p LEFT JOIN doctors d ON d.id=p.doctor_id ORDER BY p.id ASC").fetchall()

            patient_rows = [dict(r) for r in rows]
            self.process_manager.sync_from_patient_records(patient_rows)
            procs = self.process_manager.get_all_processes()
            p1_id = procs[0].pid
            p2_id = procs[1].pid

            vent = self.resource_manager.resources["RES_VENT"]
            vent.available = 0
            vent.allocated = vent.total
            vent.holders = [p1_id]

            req_res = self.request_resource(p2_id, "RES_VENT", 1)

            return {
                "success": True,
                "scenario": "E",
                "title": "Scenario E: Hospital Resource Contention & Blocking",
                "message": "Scenario E: Hospital Resource Contention & Blocking executed successfully.",
                "description": f"Patient A holds all Ventilators. Patient B (PID-{p2_id:03d}) requests a Ventilator and is blocked into the resource wait queue (WAITING).",
                "request_result": req_res,
                "resources": [r.to_dict() for r in self.resource_manager.get_resources()],
            }

        elif scenario_key in ("scenario_f", "scenario_f_banker", "f"):
            reset_scenario_db()
            deadlock_state = self.get_deadlock_state()
            self.event_bus.record_event(
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
            reset_scenario_db()
            for i in range(15):
                self.memory_manager.frames[i].status = "allocated"
                self.memory_manager.frames[i].pid = 99
                self.memory_manager.frames[i].page_number = i
            with db_conn_factory() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('High Memory Patient', 0, 10, 2, 'Waiting', 'General Medicine', 1)")
                rows = conn.execute("SELECT p.*, d.name AS doctor_name, d.on_duty AS doctor_on_duty FROM patients p LEFT JOIN doctors d ON d.id=p.doctor_id ORDER BY p.id ASC").fetchall()

            patient_rows = [dict(r) for r in rows]
            self.process_manager.sync_from_patient_records(patient_rows)
            procs = self.process_manager.get_all_processes()
            target_pid = procs[-1].pid
            mem_res = self.allocate_memory(target_pid, pages=4)

            return {
                "success": True,
                "scenario": "G",
                "title": "Scenario G: Memory Pressure & Allocation Failure",
                "message": "Scenario G: Memory Pressure & Allocation Failure executed successfully.",
                "description": "15 of 16 physical memory frames (94%) are allocated. High Memory Patient requests 4 pages, triggering HIGH memory pressure and MEMORY_ALLOCATION_FAILED, blocking the process until frames free up.",
                "memory_result": mem_res,
                "memory_state": self.memory_manager.get_memory_state(self.process_manager.get_all_processes()).to_dict(),
            }

        elif scenario_key in ("scenario_h", "scenario_h_sync", "h"):
            reset_scenario_db()
            with db_conn_factory() as conn:
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Surgical Patient A', 0, 4, 2, 'Waiting', 'General Medicine', 1)")
                conn.execute("INSERT INTO patients (name, arrival_time, burst_time, priority, status, specialization, doctor_id) VALUES ('Surgical Patient B', 0, 4, 2, 'Waiting', 'General Medicine', 1)")
                rows = conn.execute("SELECT p.*, d.name AS doctor_name, d.on_duty AS doctor_on_duty FROM patients p LEFT JOIN doctors d ON d.id=p.doctor_id ORDER BY p.id ASC").fetchall()

            patient_rows = [dict(r) for r in rows]
            self.process_manager.sync_from_patient_records(patient_rows)
            procs = self.process_manager.get_all_processes()
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
                "synchronization": self.sync_manager.get_synchronization_state().to_dict(),
            }

        return {"success": False, "error": f"Unknown scenario ID '{scenario_id}'. Choose A, B, C, D, E, F, G, or H."}

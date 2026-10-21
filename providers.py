"""HospitalOS Data Provider Layer.

Provides a unified interface (HospitalOSProvider) for retrieving system state,
processes, scheduler execution, hospital resources, deadlock avoidance (Banker's & RAG),
memory management (pages/frames), synchronization (mutexes & counting semaphores),
and simulated system events.

Includes:
- SimulationProvider: Operates as an Adapter/Facade over the HospitalOS Built-In
  Educational Kernel Simulation Engine (HospitalKernelEngine). It synchronizes
  SQLite clinical records into authoritative kernel PCBs, delegates all OS actions
  directly to the kernel subsystems, and guarantees a single authoritative process state.
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
from kernel_sim import (
    HospitalKernelEngine,
    KernelEventBus,
    ProcessManager,
    MemoryManager,
    HospitalResourceManager,
    SynchronizationManager,
    DeadlockManager,
    CPUScheduler,
)

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

    def get_kernel_status(self) -> Dict[str, Any]:
        """Returns current kernel simulation engine status."""
        return {
            "status": "OFFLINE",
            "clock": 0,
            "processes_count": 0,
            "running_pid": None,
            "memory_used_kb": 0,
            "memory_total_kb": 0,
            "events_count": 0,
            "mode": self.get_mode(),
            "name": "HospitalOS Provider",
        }


class ManagedConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


class SimulationProvider(HospitalOSProvider):
    """Simulation-backed provider serving as an Adapter/Facade over the HospitalKernelEngine.

    Maintains synchronization between SQLite persistent clinical records and
    the single authoritative OS simulation state owned by HospitalKernelEngine.
    All data is clearly identified as simulation data (source="simulation").
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.kernel = HospitalKernelEngine()
        self.kernel.set_state_change_callback(self._on_kernel_state_change)

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=ManagedConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _on_kernel_state_change(self, pid: int, new_state: str):
        """Synchronizes kernel process state changes back into SQLite clinical records."""
        try:
            conn = self._get_db()
            try:
                with conn:
                    if new_state == ProcessState.TERMINATED:
                        conn.execute("UPDATE patients SET status='Completed' WHERE id=?", (pid,))
                    else:
                        conn.execute("UPDATE patients SET status='Waiting' WHERE id=?", (pid,))
            finally:
                conn.close()
        except Exception:
            pass

    # =========================================================================
    # Compatibility properties pointing directly to authoritative kernel state
    # =========================================================================
    @property
    def _event_log(self) -> List[KernelEvent]:
        return self.kernel.event_bus._event_log

    @_event_log.setter
    def _event_log(self, val: List[KernelEvent]):
        self.kernel.event_bus._event_log = val

    @property
    def _event_counter(self) -> int:
        return self.kernel.event_bus._event_counter

    @property
    def _process_states(self) -> Dict[int, Dict[str, Any]]:
        return self.kernel.process_manager.process_states

    @property
    def _resources(self) -> Dict[str, HospitalResource]:
        return self.kernel.resource_manager.resources

    @property
    def _memory_frames(self) -> List[MemoryFrame]:
        return self.kernel.memory_manager.frames

    @property
    def _memory_total_frames(self) -> int:
        return self.kernel.memory_manager.total_frames

    @property
    def _memory_frame_size_kb(self) -> int:
        return self.kernel.memory_manager.frame_size_kb

    @property
    def _primitives(self) -> Dict[str, SynchronizationPrimitive]:
        return self.kernel.sync_manager.primitives

    @property
    def _cached_schedule(self) -> Optional[Dict[str, Any]]:
        return self.kernel.scheduler.cached_schedule

    @_cached_schedule.setter
    def _cached_schedule(self, val: Optional[Dict[str, Any]]):
        self.kernel.scheduler._cached_schedule = val

    @property
    def _scheduler_step_index(self) -> int:
        return self.kernel.scheduler.step_index

    @_scheduler_step_index.setter
    def _scheduler_step_index(self, val: int):
        self.kernel.scheduler._scheduler_step_index = val

    @property
    def _last_algo(self) -> str:
        return self.kernel.scheduler._last_algo

    @_last_algo.setter
    def _last_algo(self, val: str):
        self.kernel.scheduler._last_algo = val

    @property
    def _last_quantum(self) -> int:
        return self.kernel.scheduler._last_quantum

    @_last_quantum.setter
    def _last_quantum(self, val: int):
        self.kernel.scheduler._last_quantum = val

    # Helper delegations
    def record_event(
        self,
        event_type: str,
        summary: str,
        category: Optional[str] = None,
        simulation_time: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> KernelEvent:
        return self.kernel.event_bus.record_event(
            event_type=event_type,
            summary=summary,
            category=category,
            simulation_time=simulation_time,
            payload=payload,
            source="simulation",
        )

    def _infer_category(self, event_type: str) -> str:
        return self.kernel.event_bus.infer_category(event_type)

    def _format_syscall(self, e: KernelEvent) -> Optional[Dict[str, Any]]:
        return self.kernel.event_bus._format_syscall(e)

    def _detect_rag_cycle_with_path(self, edges: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        return self.kernel.deadlock_manager.detect_rag_cycle_with_path(edges)

    def _detect_rag_cycle(self, edges: List[Dict[str, Any]]) -> bool:
        has_c, _ = self.kernel.deadlock_manager.detect_rag_cycle_with_path(edges)
        return has_c

    def _normalize_resource_id(self, r_id: str) -> str:
        return self.kernel.resource_manager.normalize_resource_id(r_id)

    def _normalize_primitive_id(self, p_id: str) -> str:
        return self.kernel.sync_manager.normalize_primitive_id(p_id)

    def _reset_simulation_data(self):
        conn = self._get_db()
        try:
            with conn:
                conn.execute("DELETE FROM patients")
        finally:
            conn.close()
        self.kernel.reset()

    # =========================================================================
    # HospitalOSProvider Interface Implementation
    # =========================================================================
    def get_mode(self) -> str:
        return "simulation"

    def is_connected(self) -> bool:
        return True

    def get_kernel_status(self) -> Dict[str, Any]:
        return self.kernel.get_kernel_status()

    def get_processes(self) -> List[HospitalProcess]:
        """Translates SQLite patients into authoritative kernel processes across canonical states."""
        try:
            conn = self._get_db()
            try:
                rows = conn.execute("""
                    SELECT p.*, d.name AS doctor_name, d.on_duty AS doctor_on_duty
                    FROM patients p
                    LEFT JOIN doctors d ON d.id=p.doctor_id
                    ORDER BY p.id ASC
                """).fetchall()
                patient_rows = [dict(r) for r in rows]
            finally:
                conn.close()

            def on_init_allocate(pid: int, pages: int):
                self.kernel.allocate_memory(pid, pages)

            def on_terminate(pid: int):
                self.kernel.transition_process(pid, ProcessState.TERMINATED)

            return self.kernel.process_manager.sync_from_patient_records(
                patient_rows,
                memory_frame_size_kb=self.kernel.memory_manager.frame_size_kb,
                on_init_allocate=on_init_allocate,
                on_terminate=on_terminate,
            )
        except sqlite3.OperationalError:
            return self.kernel.process_manager.get_all_processes()

    def get_process_by_id(self, pid: int) -> Optional[HospitalProcess]:
        self.get_processes()
        return self.kernel.process_manager.get_process(pid)

    def register_patient_process(self, patient_id: int, name: str, doctor_id: Optional[int] = None):
        """Registers a newly admitted patient process into the simulation lifecycle."""
        self.kernel.process_manager.register_patient_process(
            patient_id,
            name,
            doctor_id,
            on_allocate=lambda pid, pages: self.kernel.allocate_memory(pid, pages),
        )

    def mark_patient_completed(self, patient_id: int):
        self.transition_process(patient_id, ProcessState.TERMINATED, "Marked complete via doctor workload manager")

    def mark_patient_unassigned(self, patient_id: int):
        self.transition_process(patient_id, ProcessState.WAITING, "Doctor assignment removed; waiting for available doctor")

    def transition_process(self, pid: int, target_state: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """Transitions a process to a target state, enforcing terminated semantics."""
        self.get_processes()
        return self.kernel.transition_process(pid, target_state, reason=reason)

    def readmit_patient(self, pid: int) -> Dict[str, Any]:
        """Re-admits a completed/terminated patient into the active simulation."""
        self.get_processes()
        conn = self._get_db()
        try:
            with conn:
                conn.execute("UPDATE patients SET status='Waiting' WHERE id=?", (pid,))
                row = conn.execute("SELECT burst_time, doctor_id FROM patients WHERE id=?", (pid,)).fetchone()
                burst = row["burst_time"] if row else 4
                doctor_id = row["doctor_id"] if row else None
        finally:
            conn.close()

        return self.kernel.readmit_patient(pid, burst=burst, doctor_id=doctor_id)

    def step_lifecycle(self) -> Dict[str, Any]:
        """Advances one process through the simulation lifecycle in a coherent order."""
        self.get_processes()
        return self.kernel.step_lifecycle()

    # =========================================================================
    # CPU Scheduling
    # =========================================================================
    def get_scheduler_state(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        """Runs the scheduling simulator on active waiting patients."""
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT id, name, arrival_time, burst_time, priority FROM patients WHERE status='Waiting' ORDER BY id"
            ).fetchall()
            patient_dicts = [dict(row) for row in rows]
        finally:
            conn.close()

        return self.kernel.scheduler.schedule(patient_dicts, algorithm=algorithm, quantum=quantum)

    def step_scheduler(self, algorithm: str = "FCFS", quantum: int = 2) -> Dict[str, Any]:
        """Advances the scheduler by one slice in the execution timeline."""
        self.get_processes()

        def waiting_patient_supplier() -> List[Dict[str, Any]]:
            conn = self._get_db()
            try:
                rows = conn.execute(
                    "SELECT id, name, arrival_time, burst_time, priority FROM patients WHERE status='Waiting' ORDER BY id"
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

        return self.kernel.scheduler.step(
            self.kernel,
            algorithm=algorithm,
            quantum=quantum,
            waiting_patient_supplier=waiting_patient_supplier,
        )

    def reset_scheduler(self) -> Dict[str, Any]:
        """Resets the scheduler stepping and restores waiting processes to READY/WAITING."""
        res = self.kernel.scheduler.reset(self.kernel)
        self.get_processes()
        conn = self._get_db()
        try:
            rows = conn.execute("SELECT id, burst_time, doctor_id FROM patients WHERE status='Waiting'").fetchall()
            for r in rows:
                pid = int(r["id"])
                proc = self.kernel.process_manager.get_process(pid)
                if proc:
                    st = ProcessState.READY if r["doctor_id"] else ProcessState.WAITING
                    proc.state = st
                    proc.remaining_time = int(r["burst_time"])
                    proc.assigned_resources = {}
                    proc.state_reason = "Scheduler reset; process returned to ready queue."
        finally:
            conn.close()

        return res

    # =========================================================================
    # Hospital Resources
    # =========================================================================
    def get_resources(self) -> List[HospitalResource]:
        """Returns the dynamic inventory of hospital resources."""
        try:
            conn = self._get_db()
            try:
                on_duty = conn.execute("SELECT COUNT(*) FROM doctors WHERE on_duty=1").fetchone()[0]
                busy = conn.execute("SELECT COUNT(DISTINCT doctor_id) FROM patients WHERE status!='Completed' AND doctor_id IS NOT NULL").fetchone()[0]
            finally:
                conn.close()
            self.kernel.resource_manager.sync_doctor_counts(on_duty, busy)
        except Exception:
            pass
        return self.kernel.resource_manager.get_resources()

    def request_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        self.get_processes()
        return self.kernel.request_resource(pid, resource_id, units=units)

    def release_resource(self, pid: int, resource_id: str, units: int = 1) -> Dict[str, Any]:
        self.get_processes()
        return self.kernel.release_resource(pid, resource_id, units=units)

    # =========================================================================
    # Deadlock / Banker's & RAG
    # =========================================================================
    def get_deadlock_state(
        self,
        allocation: Optional[List[List[int]]] = None,
        maximum: Optional[List[List[int]]] = None,
        available: Optional[List[int]] = None,
    ) -> DeadlockState:
        self.get_processes()
        return self.kernel.get_deadlock_state(
            allocation=allocation,
            maximum=maximum,
            available=available,
        )

    # =========================================================================
    # Memory Management
    # =========================================================================
    def get_memory_state(self) -> MemoryState:
        self.get_processes()
        return self.kernel.memory_manager.get_memory_state(self.kernel.process_manager.get_all_processes())

    def allocate_memory(self, pid: int, pages: Optional[int] = None) -> Dict[str, Any]:
        self.get_processes()
        return self.kernel.allocate_memory(pid, pages=pages)

    def free_memory(self, pid: int) -> Dict[str, Any]:
        self.get_processes()
        return self.kernel.free_memory(pid)

    # =========================================================================
    # Concurrency & Synchronization
    # =========================================================================
    def get_synchronization_state(self) -> SynchronizationState:
        return self.kernel.sync_manager.get_synchronization_state()

    def acquire_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        self.get_processes()
        return self.kernel.acquire_lock(pid, primitive_id)

    def release_lock(self, pid: int, primitive_id: str) -> Dict[str, Any]:
        self.get_processes()
        return self.kernel.release_lock(pid, primitive_id)

    # =========================================================================
    # Event Stream & Simulated System Calls
    # =========================================================================
    def get_events(self, limit: int = 50, category: Optional[str] = None) -> List[KernelEvent]:
        return self.kernel.event_bus.get_events(limit=limit, category=category)

    def get_simulated_syscalls(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.kernel.event_bus.get_simulated_syscalls(limit=limit)

    # =========================================================================
    # Demonstration Scenarios
    # =========================================================================
    def run_demo_scenario(self, scenario_id: str) -> Dict[str, Any]:
        return self.kernel.run_demo_scenario(scenario_id, self._get_db)

    # =========================================================================
    # Snapshot
    # =========================================================================
    def get_system_snapshot(self, algorithm: str = "FCFS") -> SystemSnapshot:
        self.get_processes()
        return self.kernel.get_system_snapshot(algorithm=algorithm)


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
                "description": "HospitalOS Built-In Educational Kernel Simulation Engine.",
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

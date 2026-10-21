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
    def get_scheduler_state(self, algorithm: str = "FCFS") -> Dict[str, Any]:
        """Returns the scheduler state and execution timeline for the specified algorithm."""
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
        """Translates SQLite patients into HospitalProcess objects."""
        processes: List[HospitalProcess] = []
        try:
            with self._get_db() as conn:
                rows = conn.execute("""
                    SELECT p.*, d.name AS doctor_name
                    FROM patients p
                    LEFT JOIN doctors d ON d.id=p.doctor_id
                    ORDER BY p.id ASC
                """).fetchall()

                for row in rows:
                    status = row["status"]
                    state = ProcessState.TERMINATED if status == "Completed" else ProcessState.READY
                    burst = int(row["burst_time"])
                    remaining = 0 if status == "Completed" else burst
                    # Deterministic simulated memory: 2 pages base + 1 per 2 duration units
                    mem_pages = max(1, 2 + burst // 2)

                    processes.append(
                        HospitalProcess(
                            pid=row["id"],
                            patient_id=row["id"],
                            name=row["name"],
                            state=state,
                            priority=int(row["priority"]),
                            arrival_time=int(row["arrival_time"]),
                            burst_time=burst,
                            remaining_time=remaining,
                            doctor_id=row["doctor_id"],
                            doctor_name=row["doctor_name"],
                            specialization=row["specialization"] if "specialization" in row.keys() else "General Medicine",
                            memory_pages=mem_pages,
                        )
                    )
        except sqlite3.OperationalError:
            # Handle empty/uninitialized DB gracefully
            pass
        return processes

    def get_scheduler_state(self, algorithm: str = "FCFS") -> Dict[str, Any]:
        """Runs the scheduling simulator on active waiting patients."""
        try:
            with self._get_db() as conn:
                rows = conn.execute(
                    "SELECT id, name, arrival_time, burst_time, priority FROM patients WHERE status='Waiting' ORDER BY id"
                ).fetchall()
            patient_dicts = [dict(row) for row in rows]
            res = schedule_patients(patient_dicts, algorithm)
            self.record_event(
                event_type="SCHEDULER_RUN",
                summary=f"Scheduler executed with {algorithm} algorithm for {len(patient_dicts)} processes",
                payload={"algorithm": algorithm, "process_count": len(patient_dicts)},
            )
            return res
        except Exception as exc:
            return {
                "algorithm": algorithm,
                "results": [],
                "gantt": [],
                "average_waiting_time": 0,
                "average_turnaround_time": 0,
                "message": f"Simulation error: {exc}",
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

    def get_scheduler_state(self, algorithm: str = "FCFS") -> Dict[str, Any]:
        return {
            "algorithm": algorithm,
            "connected": False,
            "results": [],
            "gantt": [],
            "average_waiting_time": 0,
            "average_turnaround_time": 0,
            "message": "Live Kernel disconnected. QEMU bridge implementation scheduled for a future phase.",
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

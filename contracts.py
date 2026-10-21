"""HospitalOS Shared Data Contracts.

Defines unified web-side data contracts for hospital processes, resources,
deadlock detection, memory management, synchronization, and system events.
These contracts are designed to be populated by both the local Simulation Provider
and the future Live Kernel Provider (QEMU bridge).
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
import time


class ProcessState:
    """Canonical process states for HospitalOS processes."""
    NEW = "NEW"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    TERMINATED = "TERMINATED"


class SchedulingEventType:
    """Canonical scheduling event categories."""
    DISPATCH = "DISPATCH"
    PREEMPT = "PREEMPT"
    COMPLETE = "COMPLETE"
    BLOCK = "BLOCK"
    WAKEUP = "WAKEUP"
    IDLE = "IDLE"


@dataclass
class HospitalProcess:
    """Represents a hospital patient mapped to an OS process/PCB."""
    pid: int
    name: str
    state: str = ProcessState.READY
    priority: int = 3
    arrival_time: int = 0
    burst_time: int = 1
    remaining_time: int = 1
    patient_id: Optional[int] = None
    doctor_id: Optional[int] = None
    doctor_name: Optional[str] = None
    specialization: str = "General Medicine"
    assigned_resources: Dict[str, int] = field(default_factory=dict)
    memory_pages: int = 2
    deadline: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SchedulingEvent:
    """Represents a discrete scheduler event (dispatch, preemption, idle, etc.)."""
    timestamp: float
    event_type: str
    pid: Optional[int] = None
    process_name: Optional[str] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HospitalResource:
    """Represents a hospital physical or clinical resource."""
    resource_id: str
    name: str
    total: int
    available: int
    allocated: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeadlockState:
    """Represents the deadlock detection and avoidance state (Banker's + RAG)."""
    safe: bool
    safe_sequence: List[str]
    allocation_matrix: List[List[int]]
    maximum_matrix: List[List[int]]
    need_matrix: List[List[int]]
    available_vector: List[int]
    resource_names: List[str]
    process_names: List[str]
    cycle_detected: bool = False
    rag_edges: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryFrame:
    """Represents a physical memory frame in the system."""
    frame_id: int
    pid: Optional[int] = None
    page_number: Optional[int] = None
    status: str = "free"  # "free" or "allocated"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryState:
    """Represents the system-wide physical memory and paging state."""
    total_frames: int
    free_frames: int
    frame_size_kb: int = 4
    frames: List[MemoryFrame] = field(default_factory=list)
    process_allocations: Dict[int, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_frames": self.total_frames,
            "free_frames": self.free_frames,
            "frame_size_kb": self.frame_size_kb,
            "frames": [f.to_dict() for f in self.frames],
            "process_allocations": self.process_allocations,
        }


@dataclass
class SynchronizationPrimitive:
    """Represents a synchronization primitive (mutex or counting semaphore)."""
    primitive_id: str
    name: str
    primitive_type: str  # "mutex" or "semaphore"
    locked: bool = False
    count: int = 1
    holder_pid: Optional[int] = None
    wait_queue: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SynchronizationState:
    """Represents the system synchronization state across hospital locks/semaphores."""
    primitives: List[SynchronizationPrimitive] = field(default_factory=list)
    recent_events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primitives": [p.to_dict() for p in self.primitives],
            "recent_events": self.recent_events,
        }


@dataclass
class KernelEvent:
    """Represents a kernel or simulation event logged in the system."""
    event_id: str
    timestamp: float
    source: str  # "simulation" or "kernel"
    event_type: str
    summary: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SystemSnapshot:
    """Unified snapshot of all HospitalOS subsystems."""
    timestamp: float
    mode: str  # "simulation" or "live_kernel"
    is_connected: bool
    message: str
    processes: List[HospitalProcess] = field(default_factory=list)
    scheduler: Dict[str, Any] = field(default_factory=dict)
    resources: List[HospitalResource] = field(default_factory=list)
    deadlock: Optional[DeadlockState] = None
    memory: Optional[MemoryState] = None
    synchronization: Optional[SynchronizationState] = None
    recent_events: List[KernelEvent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "mode": self.mode,
            "is_connected": self.is_connected,
            "message": self.message,
            "processes": [p.to_dict() for p in self.processes],
            "scheduler": self.scheduler,
            "resources": [r.to_dict() for r in self.resources],
            "deadlock": self.deadlock.to_dict() if self.deadlock else None,
            "memory": self.memory.to_dict() if self.memory else None,
            "synchronization": self.synchronization.to_dict() if self.synchronization else None,
            "recent_events": [e.to_dict() for e in self.recent_events],
        }

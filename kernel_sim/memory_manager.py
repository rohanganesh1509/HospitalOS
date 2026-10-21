"""HospitalOS Educational Memory Manager.

Models physical RAM as 16 frames of 4 KB (64 KB total).
Manages page-to-frame allocation, deallocation, memory pressure monitoring,
and starvation blocking/unblocking.
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, Callable
from contracts import MemoryFrame, MemoryState, HospitalProcess
from .event_bus import KernelEventBus


class MemoryManager:
    """Simulated physical memory and paging subsystem."""

    def __init__(
        self,
        event_bus: KernelEventBus,
        total_frames: int = 16,
        frame_size_kb: int = 4,
        process_manager: Optional[Any] = None,
    ):
        self._event_bus = event_bus
        self._total_frames = total_frames
        self._frame_size_kb = frame_size_kb
        self._process_manager = process_manager
        self._frames: List[MemoryFrame] = [MemoryFrame(frame_id=i, status="free") for i in range(total_frames)]

    def set_process_manager(self, process_manager: Any):
        self._process_manager = process_manager

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def frame_size_kb(self) -> int:
        return self._frame_size_kb

    @property
    def frames(self) -> List[MemoryFrame]:
        return self._frames

    def get_used_kb(self) -> int:
        allocated = sum(1 for f in self._frames if f.status == "allocated")
        return allocated * self._frame_size_kb

    def get_total_kb(self) -> int:
        return self._total_frames * self._frame_size_kb

    def get_memory_state(self, processes: List[HospitalProcess]) -> MemoryState:
        """Returns physical memory allocation, frame map, and pressure level."""
        active_processes = [p for p in processes if getattr(p, "state", "") != "TERMINATED"]
        process_allocations: Dict[int, Dict[str, Any]] = {}

        for p in active_processes:
            frames_for_proc = [f.frame_id for f in self._frames if f.pid == p.pid]
            process_allocations[p.pid] = {
                "process_name": p.name,
                "pages": p.memory_pages,
                "frames": frames_for_proc,
                "allocated_kb": len(frames_for_proc) * self._frame_size_kb,
            }

        allocated_count = sum(1 for f in self._frames if f.status == "allocated")
        free_frames = self._total_frames - allocated_count

        if allocated_count <= 8:
            pressure = "NORMAL"
        elif allocated_count <= 13:
            pressure = "MODERATE"
        else:
            pressure = "HIGH"

        return MemoryState(
            total_frames=self._total_frames,
            free_frames=max(0, free_frames),
            frame_size_kb=self._frame_size_kb,
            memory_pressure=pressure,
            used_kb=allocated_count * self._frame_size_kb,
            total_kb=self._total_frames * self._frame_size_kb,
            frames=self._frames,
            process_allocations=process_allocations,
        )

    def allocate_memory(
        self,
        pid: int,
        pages: Optional[int] = None,
        proc: Optional[HospitalProcess] = None,
        on_fail_block: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        """Allocates physical memory frames for a process's pages."""
        pages_needed = pages if pages is not None else 2
        free_frames = [f for f in self._frames if f.status == "free"]

        if len(free_frames) < pages_needed:
            self._event_bus.record_event(
                event_type="MEMORY_ALLOCATION_FAILED",
                summary=f"Memory Allocation Failed for PID-{pid:03d}: Needs {pages_needed} pages, only {len(free_frames)} free frames.",
                category="MEMORY",
                payload={"pid": pid, "pages_needed": pages_needed, "free_frames": len(free_frames)},
            )
            if on_fail_block:
                on_fail_block()
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

        if proc is not None:
            proc.memory_frames = allocated_ids
            proc.memory_pages = pages_needed
            proc.memory_kb = pages_needed * self._frame_size_kb
        if self._process_manager:
            self._process_manager.set_memory_frames(pid, allocated_ids, pages_needed, pages_needed * self._frame_size_kb)

        self._event_bus.record_event(
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

    def free_memory(
        self,
        pid: int,
        proc: Optional[HospitalProcess] = None,
        on_unblock: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        """Frees all physical memory frames allocated to a process."""
        freed_frames = []
        for f in self._frames:
            if f.pid == pid:
                f.status = "free"
                f.pid = None
                f.page_number = None
                freed_frames.append(f.frame_id)

        if proc is not None:
            proc.memory_frames = []
        if self._process_manager:
            self._process_manager.set_memory_frames(pid, [], 0, 0)

        if freed_frames:
            self._event_bus.record_event(
                event_type="MEMORY_FREE",
                summary=f"Freed {len(freed_frames)} frames ({len(freed_frames) * 4} KB) from PID-{pid:03d}: Frames {freed_frames}",
                category="MEMORY",
                payload={"pid": pid, "freed_frames": freed_frames},
            )

        if on_unblock:
            on_unblock()

        return {
            "success": True,
            "freed_count": len(freed_frames),
            "freed_frames": freed_frames,
            "message": f"Freed {len(freed_frames)} frames for PID-{pid:03d}.",
        }

    def reset(self):
        """Resets all memory frames to free state."""
        for f in self._frames:
            f.status = "free"
            f.pid = None
            f.page_number = None

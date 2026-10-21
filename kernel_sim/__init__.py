"""HospitalOS Built-In Educational Kernel Simulation Package.

This package contains the self-contained educational operating system
simulation engine powering the HospitalOS web application.
"""

from .event_bus import KernelEventBus
from .process_manager import ProcessManager
from .memory_manager import MemoryManager
from .resource_manager import HospitalResourceManager
from .synchronization import SynchronizationManager
from .deadlock import DeadlockManager
from .scheduler import CPUScheduler
from .kernel import HospitalKernelEngine

__all__ = [
    "HospitalKernelEngine",
    "KernelEventBus",
    "ProcessManager",
    "MemoryManager",
    "HospitalResourceManager",
    "SynchronizationManager",
    "DeadlockManager",
    "CPUScheduler",
]

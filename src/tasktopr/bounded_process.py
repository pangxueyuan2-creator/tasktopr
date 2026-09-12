"""Bounded subprocess transport; command authorization remains the caller's job.

This is a resource/lifetime boundary, not an OS sandbox. POSIX children that
deliberately leave their process group require an external sandbox. On Windows
the command starts suspended and is assigned to a non-breakaway job before it
can run. No output, arguments, or environment values are logged here.
"""

from __future__ import annotations

import ctypes
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


class BoundedProcessError(RuntimeError):
    """The transport could not establish its execution boundary."""


@dataclass(frozen=True)
class BoundedResult:
    return_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool
    output_limit_exceeded: bool
    cancelled: bool
    output_bytes: int
    cleanup_complete: bool


def sanitized_environment() -> dict[str, str]:
    """Return OS essentials, with no inherited credentials or interpreter hooks.

    Executables must be resolved by the caller. PATH, HOME, proxy settings,
    PYTHONPATH, NODE_OPTIONS and package-manager configuration are not inherited.
    Callers may pass an explicitly approved environment instead.
    """

    allowed = {"systemroot", "windir", "systemdrive"} if os.name == "nt" else set()
    return {key: value for key, value in os.environ.items() if key.casefold() in allowed}


class _JobLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedJobLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobLimits),
        ("IoInfo", ctypes.c_uint64 * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class _WindowsJob:
    """Documented Win32 job and thread APIs, loaded only on Windows."""

    kernel: Any
    handle: int | None

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise BoundedProcessError("Windows jobs are unavailable on this platform.")
        self.kernel: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes = arguments
            function.restype = result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise BoundedProcessError("Cannot create process job.")
        limits = _ExtendedJobLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway
        if not self.kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise BoundedProcessError("Cannot configure process job.")

    def assign_and_resume(self, pid: int) -> None:
        process = self.kernel.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise BoundedProcessError("Cannot open suspended process.")
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise BoundedProcessError("Cannot contain process in job.")
        finally:
            self.kernel.CloseHandle(process)

        snapshot = self.kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise BoundedProcessError("Cannot locate suspended process thread.")
        resumed = False
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            available = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.th32OwnerProcessID == pid:
                    thread = self.kernel.OpenThread(0x2, False, entry.th32ThreadID)
                    if not thread:
                        raise BoundedProcessError("Cannot open suspended process thread.")
                    try:
                        if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise BoundedProcessError("Cannot resume contained process.")
                        resumed = True
                    finally:
                        self.kernel.CloseHandle(thread)
                available = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            self.kernel.CloseHandle(snapshot)
        if not resumed:
            raise BoundedProcessError("Suspended process thread was not found.")

    def terminate(self) -> bool:
        return bool(self.kernel.TerminateJobObject(self.handle, 1))

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class _Capture:
    def __init__(self, output_limit: int, capture_limit: int) -> None:
        self.output_limit = output_limit
        self.capture_limit = capture_limit
        self.total = 0
        self.tails = [bytearray(), bytearray()]
        self.lock = threading.Lock()
        self.exceeded = threading.Event()
        self.failed = threading.Event()

    def drain(self, stream: BinaryIO, index: int) -> None:
        try:
            while chunk := stream.read(8192):
                with self.lock:
                    self.total += len(chunk)
                    tail = self.tails[index]
                    tail.extend(chunk)
                    if len(tail) > self.capture_limit:
                        del tail[: len(tail) - self.capture_limit]
                    if self.total > self.output_limit:
                        self.exceeded.set()
        except (OSError, ValueError):
            self.failed.set()


def _terminate_tree(process: subprocess.Popen[bytes], job: _WindowsJob | None) -> bool:
    if job is not None:
        return job.terminate()
    if sys.platform == "win32":
        return False  # Windows must have established a job before target execution.
    try:
        os.killpg(process.pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        return False


def run_bounded_command(
    command: Sequence[str],
    cwd: Path,
    timeout_seconds: float,
    *,
    output_limit_bytes: int = 1024 * 1024,
    capture_limit_bytes: int = 12_000,
    env: Mapping[str, str] | None = None,
    cancel_event: threading.Event | None = None,
) -> BoundedResult:
    """Execute caller-authorized absolute argv with bounded output and lifetime.

    Output is counted across both streams. Crossing the limit kills the process
    tree and returns 125; a deadline returns 124 and cancellation returns 130.
    Retention is at most capture_limit_bytes per stream plus two 8 KiB read chunks.
    The caller must redact returned output before display or durable storage.
    Explicit env is an authority boundary: never pass os.environ wholesale.
    """

    if not command or not Path(command[0]).is_absolute():
        raise ValueError("The executable must be an explicitly resolved absolute path.")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite.")
    for limit in (output_limit_bytes, capture_limit_bytes):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("Output limits must be positive integers.")
    if cancel_event is not None and cancel_event.is_set():
        return BoundedResult(130, "", "", 0.0, False, False, True, 0, True)

    started = time.monotonic()
    job: _WindowsJob | None = None
    process: subprocess.Popen[bytes] | None = None
    threads: list[threading.Thread] = []
    capture = _Capture(output_limit_bytes, capture_limit_bytes)
    timed_out = cancelled = False
    cleanup_complete = True
    try:
        if os.name == "nt":
            job = _WindowsJob()
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else sanitized_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
            close_fds=True,
            start_new_session=os.name != "nt",
            creationflags=0x4 if job is not None else 0,  # CREATE_SUSPENDED
        )
        if job is not None:
            job.assign_and_resume(process.pid)
        for index, stream in enumerate((process.stdout, process.stderr)):
            if stream is None:
                raise BoundedProcessError("Capture pipe was not established.")
            thread = threading.Thread(
                target=capture.drain,
                args=(stream, index),
                name=f"tasktopr-output-{index}",
                daemon=True,
            )
            threads.append(thread)
            thread.start()
        while process.poll() is None:
            if capture.exceeded.is_set() or capture.failed.is_set():
                break
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                break
            capture.exceeded.wait(min(0.02, remaining))
    finally:
        if process is not None:
            # Always reap descendants, even after a successful leader exit. They
            # otherwise outlive verification and can keep capture pipes open.
            cleanup_complete = _terminate_tree(process, job)
            if process.poll() is None:
                process.kill()  # Covers failure before job assignment.
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                cleanup_complete = False
            for thread in threads:
                thread.join(timeout=2)
                cleanup_complete = cleanup_complete and not thread.is_alive()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if job is not None:
            job.close()

    if process is None:
        raise BoundedProcessError("Process was not established.")
    cleanup_complete = cleanup_complete and not capture.failed.is_set()
    exceeded = capture.exceeded.is_set()
    return_code = process.returncode if process.returncode is not None else 125
    if timed_out:
        return_code = 124
    elif cancelled:
        return_code = 130
    elif exceeded or not cleanup_complete:
        return_code = 125
    with capture.lock:
        stdout, stderr = (bytes(tail).decode("utf-8", errors="replace") for tail in capture.tails)
        output_bytes = capture.total
    return BoundedResult(
        return_code,
        stdout,
        stderr,
        round(time.monotonic() - started, 3),
        timed_out,
        exceeded,
        cancelled,
        output_bytes,
        cleanup_complete,
    )

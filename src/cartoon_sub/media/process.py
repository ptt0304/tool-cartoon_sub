import logging
import subprocess
from threading import Event

log = logging.getLogger(__name__)

class CancelledError(RuntimeError):
    pass

def run_process(args, cancel=None, progress=None, cwd=None):
    cancel = cancel or Event()
    if cancel.is_set():
        raise CancelledError("Job cancelled")
    log.info("Running: %s", args)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=flags, cwd=cwd)
    except OSError as exc:
        log.exception("Unable to start media tool")
        raise RuntimeError(f"Cannot start {args[0]}: {exc}") from exc
    try:
        while True:
            if cancel.is_set():
                process.terminate()
                try:
                    process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise CancelledError("Job cancelled")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        diagnostic = stderr.decode("utf-8", errors="replace")
        if diagnostic:
            log.info("Media tool output: %s", diagnostic)
        if process.returncode:
            raise RuntimeError(f"{args[0]} exited {process.returncode}: {diagnostic[-6000:]}")
        if progress:
            progress("Media operation completed")
        return stdout.decode("utf-8", errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

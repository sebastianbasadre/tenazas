"""Run independent calls with a shared deadline and terminate overdue children."""

import multiprocessing
import time


def _execute(connection, function, args):
    try:
        connection.send({"ok": True, "value": function(*args)})
    except Exception as exc:
        connection.send({"ok": False, "error": str(exc)[:300]})
    finally:
        connection.close()


def bounded_calls(calls, timeout, max_workers=5):
    """calls maps names to (top-level callable, args); return status per name."""
    context = multiprocessing.get_context(
        "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    )
    deadline = time.monotonic() + timeout
    pending = list(calls.items())
    active = {}
    results = {}
    try:
        while (pending or active) and time.monotonic() < deadline:
            while pending and len(active) < max_workers:
                name, (function, args) = pending.pop(0)
                reader, writer = context.Pipe(duplex=False)
                process = context.Process(target=_execute, args=(writer, function, args))
                try:
                    process.start()
                except Exception:
                    reader.close()
                    writer.close()
                    raise
                writer.close()
                active[name] = (process, reader)
            for name, (process, reader) in list(active.items()):
                if reader.poll():
                    try:
                        results[name] = reader.recv()
                    except (EOFError, OSError):
                        results[name] = {"ok": False, "error": "child exited without result"}
                    reader.close()
                    process.join(timeout=0.05)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=0.2)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=0.2)
                    if not process.is_alive():
                        process.close()
                    del active[name]
            if active:
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        for name in calls:
            results.setdefault(name, {"ok": False, "error": "deadline exceeded", "timeout": True})
        return results
    finally:
        for process, reader in active.values():
            reader.close()
            if process.is_alive():
                process.terminate()
            process.join(timeout=0.2)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.2)
            if not process.is_alive():
                process.close()

import time


def wait_for_job(poll_seconds: float = 0.5) -> str:
    """Bug: the job is never marked done, so this polls forever."""
    done = False
    while not done:
        time.sleep(poll_seconds)
    return "done"

from worker import wait_for_job


def test_quick():
    assert True


def test_wait_for_job_completes():
    assert wait_for_job() == "done"

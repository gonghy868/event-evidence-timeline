import time
from app import jobs


def wait(job_id):
    deadline = time.monotonic()+4
    while time.monotonic()<deadline:
        j = jobs.get(job_id)
        if j["status"]!="running":
            return j
        time.sleep(.005)
    raise AssertionError("后台任务超时")


def test_job_error():
    def fail(job):
        job.step("正在处理","测试")
        raise ValueError("测试错误")
    j = wait(jobs.start(fail))
    assert j["status"] == "error" and j["error"] == "ValueError: 测试错误"
    assert j["steps"][0]["name"] == "正在处理" and j["steps"][0]["at"]

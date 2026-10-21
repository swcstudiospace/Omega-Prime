

def test_terminal_stream_limit_returns_429_before_sse_starts():
    calls = []
    async def runner(argv):
        calls.append(argv)
        return "should not run"
    service = TerminalService(runner=runner)
    async def saturate_and_request():
        for _ in range(4):
            assert await service.reserve()
        status, wire = await _wire(create_app(_poller(mapping={"pane_id": "w1:p1"}), terminal_service=service), "/api/tasks/task:one/terminal")
        for _ in range(4):
            service._slots.release()
        return status, wire
    status, wire = asyncio.run(saturate_and_request())
    assert status == 429 and "event: terminal" not in wire
    assert calls == []

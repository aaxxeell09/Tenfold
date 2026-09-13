"""app/server.py end to end in mock mode: no camera, no browser, no port.

The mock loop drives the real Engine with synthetic GestureStates, so these
tests exercise the same path the webcam takes, minus MediaPipe.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app import server
from classifier.schema import HandFrame, Window
from lesson import tally
from lesson.engine import Engine, Exercise


def run(coroutine):
    return asyncio.run(coroutine)


# --- the message the browser receives ---------------------------------------


def settled(engine: Engine, gesture, t0: float = 0.0):
    engine.observe(gesture, t0)
    return engine.observe(gesture, t0 + 0.3 + 0.01)


def test_build_message_carries_everything_the_page_renders():
    from classifier.schema import GestureState

    engine = Engine([Exercise(8, 7)])
    engine.start()
    update = settled(engine, GestureState(method="6-10", left=8, right=9,
                                          contact=False, confidence=0.9))
    fingers = [{"hand": "left", "number": 8, "x": 0.3, "y": 0.5}]
    message = server.build_message(update, fingers, hands_seen=2)

    assert set(message) == {"type", "state", "exercise", "tally", "wrong", "match",
                            "answer", "reasoning", "fingers", "reason", "reaction",
                            "hint", "hint_level", "fact", "session", "node"}
    assert message["state"] == "wrong_pose"
    assert message["exercise"] == "8 x 7"
    assert message["wrong"] == [{"hand": "right", "number": 9}]
    assert message["match"] == [{"hand": "left", "number": 8}]
    assert message["tally"].startswith("Almost")
    assert message["fingers"] == fingers
    assert json.dumps(message), "the message has to be JSON serialisable"


def test_one_hand_gets_its_own_line():
    from classifier.schema import GestureState

    engine = Engine([Exercise(8, 7)])
    engine.start()
    update = settled(engine, GestureState(method="unknown", confidence=0.1))
    assert server.moment_of(update, hands_seen=1) == "one_hand"
    assert server.moment_of(update, hands_seen=0) == "waiting_pose"


def test_fingers_are_placed_in_mirrored_image_coordinates():
    """One flat hand at the wrist, so every tip lands at a predictable spot."""
    points = [(0.0, 0.0, 0.0)] * 21
    for index in (4, 8, 12, 16, 20):
        points[index] = (1.0, -1.0, 0.0)
    hand = HandFrame(points=points, detection_conf=0.9, wrist_xy=(0.3, 0.6), scale=0.1)
    window = Window(left=[hand], right=[])

    fingers = server.fingers_from_window(window)
    assert len(fingers) == 5
    assert {f["hand"] for f in fingers} == {"left"}
    assert sorted(f["number"] for f in fingers) == [6, 7, 8, 9, 10]
    for finger in fingers:
        assert finger["x"] == pytest.approx(0.4)
        assert finger["y"] == pytest.approx(0.5)


def test_an_absent_or_broken_hand_contributes_no_fingers():
    absent = HandFrame(points=None, detection_conf=0.0, wrist_xy=None, scale=None)
    nan = HandFrame(points=[(float("nan"), float("nan"), 0.0)] * 21,
                    detection_conf=0.9, wrist_xy=(0.3, 0.5), scale=0.1)
    assert server.fingers_from_window(Window(left=[absent], right=[absent])) == []
    assert server.fingers_from_window(Window(left=[nan], right=[absent])) == []


# --- the server itself ------------------------------------------------------


async def _client(app):
    server_ = TestServer(app)
    await server_.start_server()
    return server_, TestClient(server_)


def test_the_page_is_served_at_the_root():
    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            response = await client.get("/")
            assert response.status == 200
            body = await response.text()
            assert 'id="course"' in body, "the course shell is the page now"
            assert 'src="app.js' in body and 'src="levels.js' in body
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_the_video_endpoint_streams_mjpeg():
    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            response = await client.get("/video")
            assert "multipart/x-mixed-replace" in response.headers["Content-Type"]
            chunk = await asyncio.wait_for(response.content.read(64), timeout=5)
            assert b"--frame" in chunk
            response.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_the_websocket_pushes_the_lesson_and_takes_commands():
    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "hello", "state": None})
            first = await _await_fact(ws)
            assert first["type"] == "state"
            assert first["exercise"]

            # next moves the lesson on, and the answer follows the new exercise.
            await ws.send_json({"type": "next"})
            moved = await _await_state(ws, "exercise_shown")
            assert moved["exercise"] != first["exercise"]

            # a wrong answer before the pose is latched changes nothing.
            await ws.send_json({"type": "check", "value": 1})
            # a malformed command must not take the server down.
            await ws.send_str("not json")
            await ws.send_json({"type": "unknown_command"})
            alive = await asyncio.wait_for(ws.receive_json(), timeout=5)
            assert alive["type"] == "state"
            await ws.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_the_mock_reaches_the_three_states():
    """waiting_pose, wrong_pose and correct_pose, on the real engine."""
    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "hello", "state": None})
            seen = set()
            deadline = asyncio.get_running_loop().time() + 12
            while asyncio.get_running_loop().time() < deadline:
                message = await asyncio.wait_for(ws.receive_json(), timeout=5)
                seen.add(message["state"])
                if {"waiting_pose", "wrong_pose", "correct_pose"} <= seen:
                    break
            assert {"waiting_pose", "wrong_pose", "correct_pose"} <= seen, seen
            await ws.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


async def _await_fact(ws, timeout: float = 6.0):
    """The first message carrying an exercise, once a session is under way."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        message = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
        if message.get("fact"):
            return message
    raise AssertionError("no exercise ever arrived")


async def _await_state(ws, state: str, timeout: float = 6.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        message = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
        if message["state"] == state:
            return message
    raise AssertionError(f"never reached {state}")


# --- the scheduler driving the lesson ----------------------------------------


def test_the_scheduler_picks_the_exercise_and_says_why():
    from lesson.scheduler import Pick

    engine = Engine([Exercise(8, 7)])
    engine.start()
    update = engine.snapshot()
    pick = Pick("7x8", 8, 7, "next_new", is_new=True)
    message = server.build_message(update, [], hands_seen=0, reaction="hint_1",
                                   pick=pick, hint_level=1, session=3)
    assert message["fact"] == "7x8"
    assert message["reason"] == "next_new"
    assert message["reaction"] == "hint_1"
    assert message["hint_level"] == 1
    assert message["session"] == 3
    # the reaction wins over the screen state for what Tally says
    assert message["tally"] == tally.phrase("hint_1", {"hint": update.hint})


def test_demo_mode_loads_the_fixed_scenario():
    from lesson.scheduler import ScriptedScheduler

    scheduler = server.make_scheduler(demo=True)
    assert isinstance(scheduler, ScriptedScheduler)
    scheduler.start_session()
    assert scheduler.next_exercise().fact == "7x8"
    assert server.make_scheduler(demo=False).__class__.__name__ == "Scheduler"


def test_a_session_ends_with_the_state_for_the_page_to_store():
    """The page keeps the learner record, the server keeps nothing."""
    async def scenario():
        app = server.create_app(mock=True, demo=True)
        srv, client = await _client(app)
        try:
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "hello", "state": None})
            end = None
            deadline = asyncio.get_running_loop().time() + 20
            while asyncio.get_running_loop().time() < deadline:
                message = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if message["type"] == "session_end":
                    end = message
                    break
                if message["type"] == "state" and message.get("fact"):
                    await ws.send_json({"type": "next"})
            assert end is not None, "the session never ended"
            assert end["state"]["learner_id"]
            assert end["metrics"]["session"] == 1
            assert end["summary"]["reason"] in ("end_success", "end_tired")
            assert end["tally"]
            await ws.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_the_course_assets_are_served_next_to_the_page():
    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            for name, needle in (("/levels.js", "recordLesson"),
                                 ("/app.js", "TenfoldLevels"),
                                 ("/styles.css", ".lesson")):
                response = await client.get(name)
                assert response.status == 200, name
                assert needle in await response.text(), name
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_a_node_scopes_the_session_to_its_pairs_and_length():
    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "start_node", "state": None, "node": {
                "id": "u1-l1", "kind": "lesson", "pairs": [[6, 6], [7, 7]]}})
            facts, end = [], None
            deadline = asyncio.get_running_loop().time() + 25
            while asyncio.get_running_loop().time() < deadline:
                message = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if message["type"] == "node_end":
                    end = message
                    break
                if (message["type"] == "state" and message.get("fact")
                        and message.get("node") == "u1-l1"):
                    if not facts or facts[-1] != message["fact"]:
                        facts.append(message["fact"])
                        await ws.send_json({"type": "next"})
            assert end is not None, "the node never ended"
            assert end["node_id"] == "u1-l1"
            assert end["total"] == 5, "a lesson is five exercises"
            assert set(facts) <= {"6x6", "7x7"}, facts
            await ws.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())

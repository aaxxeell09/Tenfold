"""app/server.py end to end in mock mode: no camera, no browser, no port.

The mock loop drives the real Engine with synthetic GestureStates, so these
tests exercise the same path the webcam takes, minus MediaPipe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app import server
from classifier.schema import GestureState, HandFrame, Window
from lesson import tally
from lesson.engine import Engine, Exercise
from lesson.scheduler import ScriptedScheduler, now_utc


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
                            "hint", "hint_level", "hint_auto", "pose_slip",
                            "fact", "session", "node", "demo",
                            "tutor_state", "intervention_level", "tutor_line",
                            "tutor_visual", "scored_gesture_error",
                            "scored_math_error", "first_try", "mode",
                            "tutor_line_cuts", "tutor_beat"}
    assert message["pose_slip"] is False and message["hint_auto"] is False
    # The eight tutor fields, with the defaults a page reads an absent one as.
    assert message["tutor_state"] == "WORKING"
    assert message["intervention_level"] == 0
    assert message["tutor_line"] is None and message["tutor_visual"] is None
    assert message["scored_gesture_error"] is False
    assert message["scored_math_error"] is False
    assert message["first_try"] is False
    assert message["mode"] == "normal"
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
            # the page is the export's own markup now, one view per export page
            for view in ('welcome', 'home', 'practice', 'check', 'lesson', 'profile'):
                assert f'id="{view}"' in body, f"the {view} view is part of the page"
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


# --- the camera thread: a failure has to reach the page ----------------------


class FakeCamera:
    """A capture that answers a fixed read, and remembers being released."""

    def __init__(self, read: tuple[bool, object]) -> None:
        self._read = read
        self.calls = 0
        self.released = False

    def read(self):
        self.calls += 1
        return self._read

    def release(self) -> None:
        self.released = True


class FakeDetector:
    def __init__(self, num_hands: int = 2) -> None:
        self.closed = False

    def detect(self, frame):
        return []

    def close(self) -> None:
        self.closed = True


def _blind(window):
    return GestureState(method="unknown", confidence=0.0)


def test_a_perception_failure_releases_the_camera_and_tells_the_page(monkeypatch):
    """The classifier or the model download raising used to kill the thread."""
    import app.camera as camera_module
    import classifier.loader as loader

    camera = FakeCamera((True, None))
    monkeypatch.setattr(camera_module, "open_camera", lambda index=None: (camera, 0))

    def explode():
        raise RuntimeError("rules.py does not import")

    monkeypatch.setattr(loader, "load_classifier", explode)

    lesson = _lesson()
    stop = threading.Event()
    server.camera_loop(lesson, stop, None)

    assert camera.released, "the webcam has to be given back on any failure"
    assert stop.is_set()
    assert lesson.hub.message["tally"] == server.CAMERA_LOST


def test_a_camera_that_stops_delivering_gives_up_instead_of_spinning(monkeypatch):
    import app.camera as camera_module
    import app.landmarks as landmarks
    import classifier.loader as loader

    camera = FakeCamera((False, None))
    monkeypatch.setattr(camera_module, "open_camera", lambda index=None: (camera, 0))
    monkeypatch.setattr(landmarks, "HandDetector", FakeDetector)
    monkeypatch.setattr(loader, "load_classifier", lambda: _blind)
    monkeypatch.setattr(server, "READ_FAILURES_ALLOWED", 5)
    monkeypatch.setattr(server, "READ_RETRY_S", 0.0)

    lesson = _lesson()
    stop = threading.Event()
    server.camera_loop(lesson, stop, None)

    assert camera.calls == 5, "a failing read is bounded, never a busy loop"
    assert camera.released and stop.is_set()
    assert lesson.hub.message["tally"] == server.CAMERA_LOST


def test_no_camera_says_so_on_the_page_instead_of_loading_for_ever(monkeypatch):
    import app.camera as camera_module

    def no_camera(index=None):
        raise camera_module.CameraError("no camera produced a lit image")

    monkeypatch.setattr(camera_module, "open_camera", no_camera)

    lesson = _lesson()
    stop = threading.Event()
    stop.set()                      # the fallback loop returns straight away
    server.camera_loop(lesson, stop, None)

    assert lesson.hub.message["tally"] == server.CAMERA_NONE
    assert lesson.hub.frame(), "the fallback still gives the page a picture"


# --- the hub ----------------------------------------------------------------


def test_a_stalled_browser_loses_refreshes_but_never_the_finish_screen():
    hub = server.Hub()
    queue = hub.subscribe()
    for index in range(12):
        hub._publish({"type": "state", "n": index})
    assert queue.qsize() == 9, "state refreshes stop piling up"

    hub._publish({"type": "node_end", "node_id": "u1-l1"})
    drained = [queue.get_nowait() for _ in range(queue.qsize())]
    assert drained[-1]["type"] == "node_end", "the finish screen is never dropped"


def test_publishing_after_the_loop_closed_is_a_no_op():
    """The Ctrl+C race: the camera thread publishes into a loop that is gone."""
    loop = asyncio.new_event_loop()
    hub = server.Hub()
    hub.bind(loop)
    loop.close()
    hub.publish({"type": "state", "state": "intro"})


def test_a_socket_that_dies_on_its_first_send_leaves_no_queue_behind(monkeypatch):
    def boom(self):
        raise ConnectionResetError("the browser went away")

    monkeypatch.setattr(server.Hub, "message", property(boom))

    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            ws = await client.ws_connect("/ws")
            await asyncio.wait_for(ws.receive(), timeout=5)
            hub = app[server.LESSON_KEY].hub
            assert hub._subscribers == set(), "the queue went with the socket"
            await ws.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_a_closed_socket_is_cleaned_up_and_the_next_one_still_works():
    async def scenario():
        app = server.create_app(mock=True)
        hub = app[server.LESSON_KEY].hub
        srv, client = await _client(app)
        try:
            first = await client.ws_connect("/ws")
            await first.send_json({"type": "hello", "state": None})
            await _await_fact(first)
            await first.close()
            await _until(lambda: not hub._subscribers)
            assert hub._subscribers == set()

            second = await client.ws_connect("/ws")
            message = await asyncio.wait_for(second.receive_json(), timeout=5)
            assert message["type"] == "state"
            await second.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


# --- one session at a time ---------------------------------------------------


def test_a_second_tab_cannot_take_the_running_node_and_a_reload_can():
    first_node = {"id": "u1-l1", "kind": "lesson", "pairs": [[6, 6], [7, 7]]}
    second_node = {"id": "u1-l2", "kind": "lesson", "pairs": [[6, 7]]}

    async def scenario():
        app = server.create_app(mock=True)
        lesson = app[server.LESSON_KEY]
        srv, client = await _client(app)
        try:
            first = await client.ws_connect("/ws")
            await first.send_json({"type": "start_node", "state": None, "node": first_node})
            await _await(first, lambda m: m.get("fact") and m.get("node") == "u1-l1")

            second = await client.ws_connect("/ws")
            await second.send_json({"type": "start_node", "state": None, "node": second_node})
            refused = await _await(second, lambda m: m.get("tally") == server.ALREADY_PLAYING)
            assert refused["node"] == "u1-l2", "the refusal reaches the tab that asked"
            assert lesson.node["id"] == "u1-l1", "the running lesson is untouched"

            # the first tab goes away, so a reload may take the session
            await first.close()
            await _until(lambda: lesson.owner is None)
            await second.send_json({"type": "start_node", "state": None, "node": second_node})
            started = await _await(second, lambda m: m.get("fact") and m.get("node") == "u1-l2")
            assert started["node"] == "u1-l2"
            await second.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_a_refused_tab_cannot_quit_answer_skip_hint_or_repeat_the_running_node():
    """The second tab is refused its node but keeps its keys, buttons and microphone.
    Over the real socket, none of what it sends may reach the first tab's lesson."""
    first_node = {"id": "check", "kind": "check", "pairs": [[6, 6]], "count": 1}
    second_node = {"id": "u1-l2", "kind": "lesson", "pairs": [[6, 7]]}

    async def scenario():
        app = server.create_app(mock=True)
        lesson = app[server.LESSON_KEY]
        srv, client = await _client(app)
        try:
            first = await client.ws_connect("/ws")
            await first.send_json({"type": "start_node", "tab": "one", "state": None,
                                   "node": first_node})
            # the mock never skips inside the check, so the pose latches and waits
            await _await(first, lambda m: m.get("node") == "check"
                         and m.get("state") == "correct_pose")

            second = await client.ws_connect("/ws")
            await second.send_json({"type": "start_node", "tab": "two", "state": None,
                                    "node": second_node})
            await _await(second, lambda m: m.get("tally") == server.ALREADY_PLAYING)
            for command in ({"type": "hint"}, {"type": "repeat"},
                            {"type": "check", "value": 36}, {"type": "next"},
                            {"type": "quit"}):
                await second.send_json(command)
            await asyncio.sleep(0.3)
            assert lesson.running and lesson.node["id"] == "check", "the node was taken"
            assert lesson.engine.latched, "repeat reached the running node"
            assert lesson.hint_level == 0, "hint reached the running node"
            assert lesson.scheduler.outcomes == [], "check or next reached the running node"

            await first.send_json({"type": "check", "value": 36})
            ended = await _await(first, lambda m: m.get("type") == "node_end")
            assert ended["node_id"] == "check" and ended["correct"] == 1
            await first.close()
            await second.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_only_the_owner_changes_the_session_and_its_page_can_take_it_back():
    lesson = _lesson()
    owner, intruder = object(), object()
    lesson.command({"type": "start_node", "tab": "one", "node": _node(count=2),
                    "state": None}, client=owner)
    lesson.command({"type": "start_node", "tab": "two", "node": _node("u1-l2"),
                    "state": None}, client=intruder)
    pick, learner = lesson.pick, lesson.learner
    right = GestureState(method="6-10", left=pick.left, right=pick.right,
                         contact=True, confidence=0.95)
    lesson.observe(right, [], 2, 0.0)
    lesson.observe(right, [], 2, 0.4)
    assert lesson.engine.latched

    def untouched() -> None:
        assert lesson.running and lesson.node["id"] == "u1-l1"
        assert lesson.pick is pick and lesson.engine.latched
        assert lesson.hint_level == 0 and lesson.scheduler.outcomes == []
        assert lesson.learner is learner, "a hello swapped the running learner"

    for command in ({"type": "hint"}, {"type": "repeat"}, {"type": "hello", "state": None},
                    {"type": "check", "value": pick.result}, {"type": "next"},
                    {"type": "quit"}):
        lesson.command(command, client=intruder)
    untouched()

    # the owner's socket drops: nobody commands an ownerless node, not even to quit it
    lesson.release(owner)
    assert lesson.owner is None
    lesson.command({"type": "quit"}, client=intruder)
    lesson.command({"type": "next"}, client=owner)
    untouched()

    # the page reconnects on a new socket and asks for its node again
    reconnected = object()
    lesson.command({"type": "start_node", "tab": "one", "node": _node(count=2),
                    "state": None}, client=reconnected)
    assert lesson.owner is reconnected
    lesson.command({"type": "next"}, client=reconnected)
    assert len(lesson.scheduler.outcomes) == 1, "the owner is obeyed again"


def test_a_page_that_reconnects_before_its_old_socket_is_noticed_keeps_its_node():
    """A dropped connection can stay open on the server for a heartbeat. The same page
    on a new socket is the owner, not a second tab; another page still is not."""
    lesson = _lesson()
    old, new, other = object(), object(), object()
    lesson.command({"type": "start_node", "tab": "one", "node": _node(count=2),
                    "state": None}, client=old)

    lesson.command({"type": "start_node", "tab": "two", "node": _node("u1-l2"),
                    "state": None}, client=other)
    assert lesson.owner is old and lesson.node["id"] == "u1-l1"
    lesson.command({"type": "start_node", "node": _node("u1-l2"), "state": None},
                   client=other)
    assert lesson.owner is old, "no tab at all is not the owner's tab"

    lesson.command({"type": "start_node", "tab": "one", "node": _node(count=2),
                    "state": None}, client=new)
    assert lesson.owner is new and lesson.node["id"] == "u1-l1"
    lesson.command({"type": "next"}, client=old)
    assert lesson.scheduler.outcomes == [], "the dead socket lost the node"
    lesson.release(old)                     # noticed late, and it changes nothing
    assert lesson.owner is new
    lesson.command({"type": "next"}, client=new)
    assert len(lesson.scheduler.outcomes) == 1


def test_a_bare_client_owns_its_hello_session_and_can_take_it_back():
    lesson = _lesson()
    mine, other = object(), object()
    lesson.command({"type": "hello", "state": None}, client=mine)
    assert lesson.owner is mine
    lesson.command({"type": "next"}, client=other)
    assert lesson.scheduler.outcomes == []
    lesson.release(mine)
    again = object()
    lesson.command({"type": "hello", "state": None}, client=again)
    assert lesson.owner is again
    lesson.command({"type": "next"}, client=again)
    assert len(lesson.scheduler.outcomes) == 1


def test_a_new_wrong_pose_is_named_on_the_page_not_the_first_one():
    """8 x 7, held (8, 9) then (9, 7): Tally has to name the left finger once the new
    pose settles, and the page gets the hint for the finger that is wrong now."""
    lesson = _lesson()
    lesson.command({"type": "start_node", "node": _node(pairs=((8, 7),)), "state": None})
    pick = lesson.pick
    off = next(n for n in range(6, 11) if n not in (pick.left, pick.right))

    def pose(left: int, right: int) -> GestureState:
        return GestureState(method="6-10", left=left, right=right, contact=False,
                            confidence=0.9)

    for now in (0.0, 0.4):
        lesson.observe(pose(pick.left, off), [], 2, now)
    named = lesson.hub.message
    assert named["hint"] == {"hand": "right", "move_from": off, "move_to": pick.right}

    lesson.observe(pose(off, pick.right), [], 2, 1.0)
    assert lesson.hub.message["hint"] == named["hint"], "one frame is not a new pose"
    lesson.observe(pose(off, pick.right), [], 2, 1.4)
    now_named = lesson.hub.message
    assert now_named["hint"] == {"hand": "left", "move_from": off, "move_to": pick.left}
    assert now_named["wrong"] == [{"hand": "left", "number": off}]
    assert now_named["tally"] != named["tally"]
    assert now_named["tally"] == server.PHRASE("wrong_left_finger", {
        "hint": now_named["hint"], "answer": None, "exercise": now_named["exercise"]})
    assert lesson.wrong_hand == "wrong_left_finger"


async def _await(ws, wanted, timeout: float = 8.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        message = await asyncio.wait_for(ws.receive_json(), timeout=timeout)
        if wanted(message):
            return message
    raise AssertionError("the message never arrived")


async def _until(ready, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and not ready():
        await asyncio.sleep(0.02)


# --- the lesson, without a socket -------------------------------------------


def _lesson(demo: bool = False) -> server.Lesson:
    """The lesson with no tutor behind it, which is the server on its own."""
    import types

    lesson = server.Lesson(Engine(), server.Hub(), server.make_scheduler)
    lesson.demo_available = demo
    lesson.tutor = server.TutorLink(types.SimpleNamespace(), keep_log=False)
    lesson.start()
    return lesson


def _node(node_id: str = "u1-l1", pairs=((6, 6),), count: int = 1) -> dict:
    return {"id": node_id, "kind": "lesson",
            "pairs": [list(pair) for pair in pairs], "count": count}


def test_hello_starts_a_session_again_after_a_node_has_been_played():
    """Outcomes are never emptied, so hello used to be refused for ever."""
    lesson = _lesson()
    lesson.command({"type": "start_node", "node": _node(), "state": None})
    lesson.command({"type": "quit"})
    lesson.command({"type": "hello", "state": None})
    assert lesson.running and lesson.pick is not None


def test_a_browser_with_no_record_gets_a_fresh_learner():
    """A null state is a child who has never played, not the previous child."""
    lesson = _lesson()
    lesson.command({"type": "start_node", "node": _node(), "state": None})
    lesson.command({"type": "next"})
    first = lesson.learner
    assert first.math, "the first child left a record"

    lesson.command({"type": "start_node", "node": _node(), "state": None})
    assert lesson.learner.learner_id != first.learner_id
    assert lesson.learner.math == {} and lesson.learner.sessions == 1


def _poses(lesson) -> tuple[GestureState, GestureState]:
    """A wrong finger and the right one, for whichever exercise is up."""
    pick = lesson.pick
    wrong_finger = 7 if pick.right != 7 else 8
    return (GestureState(method="6-10", left=pick.left, right=wrong_finger,
                         contact=False, confidence=0.9),
            GestureState(method="6-10", left=pick.left, right=pick.right,
                         contact=True, confidence=0.95))


def test_a_finger_fixed_inside_the_grace_is_not_a_pose_error():
    """Bringing the hands up shows a wrong finger on nearly every exercise."""
    lesson = _lesson()
    lesson.command({"type": "start_node", "node": _node(), "state": None})
    pick = lesson.pick
    wrong, right = _poses(lesson)
    for now in (0.0, 0.4, 1.0):
        lesson.observe(wrong, [], 2, now)
    assert lesson.scored_gesture_error is False, "the child is still inside the grace"
    assert lesson.hub.message["pose_slip"] is False
    assert lesson.hub.message["scored_gesture_error"] is False
    for now in (1.5, 2.0):
        lesson.observe(right, [], 2, now)
    lesson.command({"type": "check", "value": pick.result})

    outcome = lesson.scheduler.outcomes[0]
    assert outcome.correct is True and outcome.pose_error is False
    assert lesson.learner.math[pick.fact].mastery == 1, "the fact was learned"
    assert lesson.learner.pose[pick.pose].mastery == 1, "the pose was held in the end"
    assert lesson.scheduler.consecutive_errors == 0, "no rescue, no retry queue"
    assert lesson.scheduler.retry_queue == []


def test_a_wrong_pose_held_past_the_grace_is_a_pose_error_on_the_right_hand():
    lesson = _lesson()
    lesson.command({"type": "start_node", "node": _node(), "state": None})
    pick = lesson.pick
    wrong, right = _poses(lesson)
    lesson.observe(wrong, [], 2, 0.0)
    lesson.observe(wrong, [], 2, 0.4)                    # Tally names the finger
    lesson.observe(wrong, [], 2, 0.4 + server.POSE_GRACE_SECONDS - 0.1)
    assert lesson.scored_gesture_error is False, "the grace runs from the correction"
    lesson.observe(wrong, [], 2, 0.4 + server.POSE_GRACE_SECONDS)
    assert lesson.scored_gesture_error is True
    assert lesson.hub.message["pose_slip"] is True, "the page reads the slip here"
    assert lesson.hub.message["scored_gesture_error"] is True, "and its new name"

    for now in (10.0, 10.4):
        lesson.observe(right, [], 2, now)
    lesson.command({"type": "check", "value": pick.result})

    outcome = lesson.scheduler.outcomes[0]
    assert outcome.correct is False, "the pose was wrong for four seconds"
    assert outcome.pose_error is True
    assert outcome.wrong_hand == "wrong_right_finger", "which hand, from the engine"
    assert lesson.learner.error_profile["wrong_right"] == 1
    assert lesson.learner.pose[pick.pose].mastery == 0


def _recorded(lesson) -> list[dict]:
    """Every message the lesson publishes from here on, in order."""
    said: list[dict] = []
    publish = lesson.hub.publish

    def record(message: dict) -> None:
        said.append(message)
        publish(message)

    lesson.hub.publish = record
    return said


def test_tally_prompts_at_five_seconds_and_ghosts_the_finger_at_ten():
    lesson = _lesson()
    lesson.command({"type": "start_node", "state": None,
                    "node": _node(pairs=((6, 6), (7, 7)), count=2)})
    wrong, _ = _poses(lesson)
    said = _recorded(lesson)
    lesson.started_at = 0.0                  # drive the exercise clock
    lesson.observe(wrong, [], 2, 0.0)
    lesson.observe(wrong, [], 2, 0.4)
    assert lesson.hint_level == 0

    lesson.observe(wrong, [], 2, 5.0)
    assert lesson.hint_level == 1
    assert lesson.hub.message["tally"] == tally.phrase(
        "hint_1", {"hint": lesson.hub.message["hint"]})

    lesson.observe(wrong, [], 2, 7.0)        # said once, never repeated
    assert lesson.hint_level == 1

    lesson.observe(wrong, [], 2, 10.0)
    assert lesson.hint_level == 2, "the page draws the ghost finger from here"
    assert lesson.hub.message["hint"]["hand"] and lesson.hub.message["hint"]["move_to"]
    assert [m["reaction"] for m in said if m["reaction"]] == ["hint_1", "hint_2"]
    assert lesson.hub.message["hint_auto"] is True, "the clock raised it, not the child"

    # a new exercise starts both clocks again
    lesson.command({"type": "next"})
    assert lesson.hint_level == 0 and lesson.hub.message["hint_auto"] is False


def test_a_hint_the_child_asked_for_is_not_an_automatic_one():
    """Only help the child asked for costs the first try bonus on the page."""
    lesson = _lesson()
    lesson.command({"type": "start_node", "node": _node(), "state": None})
    wrong, _ = _poses(lesson)
    lesson.started_at = 0.0
    lesson.observe(wrong, [], 2, 0.0)
    lesson.observe(wrong, [], 2, 0.4)

    lesson.command({"type": "hint"})
    assert lesson.hint_level == 1
    assert lesson.hub.message["hint_auto"] is False
    assert lesson.hub.message["hint_level"] == 1

    # the ten second ghost is the clock's doing, and says so
    lesson.observe(wrong, [], 2, 10.0)
    assert lesson.hint_level == 2
    assert lesson.hub.message["hint_auto"] is True


def test_a_session_is_a_sitting_not_a_node():
    lesson = _lesson()
    state = None
    for index in (1, 2, 3):
        lesson.command({"type": "start_node", "node": _node(f"u1-l{index}"), "state": state})
        assert lesson.scheduler.session == 1, "every node of one visit is one session"
        lesson.command({"type": "next"})
        state = lesson.learner.to_dict()
    assert lesson.learner.sessions == 1

    # coming back later is a new sitting, and that one is a new session
    lesson.sitting_at = now_utc() - timedelta(seconds=server.SITTING_GAP_S + 1)
    lesson.command({"type": "start_node", "node": _node(), "state": state})
    assert lesson.scheduler.session == 2


# --- demo mode ---------------------------------------------------------------


DEMO_NODE = _node("u2-l1", pairs=((6, 8), (8, 6), (7, 8)), count=5)


def test_the_demo_stays_inside_the_node_it_is_played_in():
    lesson = _lesson(demo=True)
    lesson.command({"type": "start_node", "node": DEMO_NODE, "state": None})
    assert isinstance(lesson.scheduler, ScriptedScheduler)

    facts = []
    while lesson.running and len(facts) < 10:
        facts.append(lesson.pick.fact)
        lesson.command({"type": "next"})

    assert len(facts) == 5, f"a five question node plays five exercises: {facts}"
    assert facts[0] == "7x8", "the stage opener is still 8 x 7"
    outside = [fact for fact in facts if fact not in {"6x8", "7x8"}]
    assert len(outside) <= 1, f"at most one review from outside the node: {facts}"
    assert not lesson.running, "the node ends itself"


def test_the_scripted_demo_can_be_rehearsed_more_than_once():
    """demo_played used to burn the script on the first node of the process."""
    lesson = _lesson(demo=True)
    for _ in range(2):
        lesson.command({"type": "start_node", "node": DEMO_NODE, "state": None})
        assert isinstance(lesson.scheduler, ScriptedScheduler)
        assert lesson.pick.fact == "7x8"
        for _ in range(10):
            if not lesson.running:
                break
            lesson.command({"type": "next"})
        assert not lesson.running

    # the start check is one exercise on 6x6 and never plays the script
    lesson.command({"type": "start_node", "state": None, "node": {
        "id": "check", "kind": "check", "pairs": [[6, 6]], "count": 1}})
    assert not isinstance(lesson.scheduler, ScriptedScheduler)


def test_a_broken_demo_scenario_falls_back_to_the_live_scheduler(monkeypatch, tmp_path):
    from lesson.scheduler import DEFAULT_SCENARIO, Scheduler

    scenario_path = tmp_path / DEFAULT_SCENARIO
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    scenario_path.write_text("{not json at all", encoding="utf-8")
    assert type(server.make_scheduler(demo=True)) is Scheduler

    scenario_path.write_text('{"exercises": [{"left": 6}]}', encoding="utf-8")
    assert type(server.make_scheduler(demo=True)) is Scheduler

    scenario_path.unlink()
    assert type(server.make_scheduler(demo=True)) is Scheduler


# --- the tutor, docs/tutor_contract.md ---------------------------------------


class StubObservation:
    """app/tutor.py's Observation, with the fields the seam fills in."""

    def __init__(self, gesture=None, fingers=(), hands_seen=0, hint=None,
                 motion=None) -> None:
        self.gesture = gesture
        self.fingers = list(fingers)
        self.hands_seen = hands_seen
        self.hint = hint
        self.motion = motion


class StubTutor:
    """A tutor that records what the server feeds it and decides what a test asks.

    The server calls app/tutor.py by name, through server.TUTOR_NAMES, and by
    keyword. This stub is the pin on that vocabulary: rename a method or an
    argument here and the seam has moved.
    """

    fps = 15.0

    def __init__(self, params=None, lines=None, clock=None, log=None,
                 learner_id: str = "", factors=None) -> None:
        self.learner_id = learner_id
        self.factors = dict(factors or {})
        self.log = log
        self.handed: list[float] = []
        self.observations: list[tuple[str, float, float]] = []
        self.events: list[tuple[str, object]] = []
        self.decision: dict = {}
        self.early_stop = False
        self._taken_at: float | None = None

    def observe(self, obs, now=None):
        # Everything the server hands over, and then the tutor's own fps rule.
        self.handed.append(now)
        if self._taken_at is not None and now - self._taken_at < 1 / self.fps - 1e-6:
            return self.decision
        self._taken_at = now
        self.observations.append((obs.gesture.method, obs.motion, now))
        self.events.append(("hint", obs.hint) if obs.hint else ("observe", None))
        return self.decision

    def new_exercise(self, a, b, title=None, node=None, now=None):
        self.events.append(("exercise", title))
        return self.decision

    def end_exercise(self, reason="answered", now=None):
        self.events.append(("end", reason))

    def start_check(self):
        self.events.append(("check_start", None))

    def end_check(self):
        self.events.append(("check_end", None))
        return 0.03

    def answer(self, value, correct, now=None):
        self.events.append(("answer", correct))

    def hint_requested(self, now=None):
        self.events.append(("hint_requested", None))

    def tts_start(self, now=None):
        self.events.append(("tts", True))

    def tts_end(self, now=None):
        self.events.append(("tts", False))

    def speech(self, text, number=None, now=None):
        self.events.append(("speech", text))

    def state_fields(self) -> dict:
        return dict(self.decision)

    def learner_factors(self) -> dict:
        return dict(self.factors)


def _stub_module(tutor=StubTutor):
    """A stand in for app/tutor.py, shaped like the module the seam imports."""
    import types

    return types.SimpleNamespace(
        Tutor=tutor,
        Observation=StubObservation,
        load_params=lambda: {"global": {}},
        load_lines=lambda: {},
        jsonl_sink=lambda: (lambda record: None),
    )


def _tutored(demo: bool = False) -> server.Lesson:
    """A lesson whose app/tutor.py is the stub above."""
    lesson = _lesson(demo)
    lesson.tutor = server.TutorLink(_stub_module(), keep_log=False)
    return lesson


def _spoke(lesson) -> list[tuple[str, object]]:
    return lesson.tutor.tutor.events


def test_the_two_new_page_messages_reach_the_tutor():
    """tts around every spoken line, speech for anything the child says."""
    lesson = _tutored()
    lesson.command({"type": "start_node", "state": None,
                    "node": _node(pairs=((6, 6), (7, 7)), count=3)})
    lesson.command({"type": "tts", "speaking": True})
    lesson.command({"type": "tts", "speaking": False})
    lesson.command({"type": "tts", "speaking": False})   # two in a row are one
    lesson.command({"type": "speech", "text": "  is it fifty six  "})
    lesson.command({"type": "speech", "text": "   "})    # nothing was said
    lesson.command({"type": "speech", "text": "x" * 400})

    assert [said for kind, said in _spoke(lesson) if kind == "tts"] == [True, False, False]
    heard = [text for kind, text in _spoke(lesson) if kind == "speech"]
    assert heard[0] == "is it fifty six", "trimmed, and never stored"
    assert len(heard) == 2 and len(heard[1]) == server.SPEECH_LIMIT

    # speech never submits an answer, and the existing messages still work
    assert lesson.hub.message["answer"] is None
    lesson.command({"type": "next"})
    assert lesson.running and lesson.pick is not None


def test_the_tutor_decision_rides_the_state_message():
    lesson = _tutored()
    lesson.command({"type": "start_node", "state": None,
                    "node": _node(pairs=((6, 6), (7, 7)), count=2)})
    lesson.tutor.tutor.decision = {
        "tutor_state": "WRONG_POSE",
        "intervention_level": 2,
        "tutor_line": "Almost. Your right hand needs 7, not 9.",
        "tutor_visual": {"kind": "correction", "wrong_hand": "right",
                         "expected_finger": 7},
        "scored_gesture_error": True,
        "mode": "supportive",
    }
    pick = lesson.pick
    wrong, right = _poses(lesson)
    lesson.observe(wrong, [], 2, 0.0)
    lesson.observe(wrong, [], 2, 0.4)

    message = lesson.hub.message
    assert message["tutor_state"] == "WRONG_POSE"
    assert message["intervention_level"] == 2
    assert message["tutor_visual"]["kind"] == "correction"
    assert message["tutor_line"].startswith("Almost")
    assert message["mode"] == "supportive"
    assert message["scored_gesture_error"] is True
    assert message["pose_slip"] is True, "the old name rides along for the page"
    assert message["first_try"] is False
    assert json.dumps(message), "the message has to be JSON serialisable"

    for now in (1.0, 1.5):
        lesson.observe(right, [], 2, now)
    lesson.command({"type": "check", "value": pick.result})
    outcome = lesson.scheduler.outcomes[0]
    assert outcome.correct is False, "the outcome follows the tutor's scoring"
    assert outcome.pose_error is True


def test_with_a_tutor_the_old_grace_clock_no_longer_scores():
    """The tutor's scoring replaces POSE_GRACE_SECONDS outright."""
    lesson = _tutored()
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    wrong, _ = _poses(lesson)
    for now in (0.0, 0.4, 5.0, 10.0, 20.0):
        lesson.observe(wrong, [], 2, now)
    assert lesson.scored_gesture_error is False, "the tutor scored nothing"
    assert lesson.hub.message["scored_gesture_error"] is False


def test_no_contact_and_a_swap_never_score_however_long_they_are_held():
    """Both numbers are right in each: the pose is still being assembled."""
    for left, right in ((6, 7), (7, 6)):
        lesson = _lesson()
        lesson.command({"type": "start_node", "state": None,
                        "node": _node(pairs=((6, 7),), count=1)})
        pose = GestureState(method="6-10", left=left, right=right,
                            contact=False, confidence=0.9)
        for now in (0.0, 0.4, 10.0, 20.0):
            lesson.observe(pose, [], 2, now)
        assert lesson.hub.message["state"] == "wrong_pose"
        assert lesson.scored_gesture_error is False, (left, right)
        assert lesson.wrong_since is None


def test_a_commutative_pose_is_correct_and_counts_for_the_star_score():
    """7 on the left and 6 on the right for 6 x 7 is a correct pose."""
    lesson = _lesson()
    lesson.command({"type": "start_node", "state": None,
                    "node": _node(pairs=((6, 7),), count=1)})
    pick = lesson.pick
    assert (pick.left, pick.right) == (6, 7)
    inverted = GestureState(method="6-10", left=pick.right, right=pick.left,
                            contact=True, confidence=0.95)
    lesson.observe(inverted, [], 2, 0.0)
    lesson.observe(inverted, [], 2, 0.4)

    message = lesson.hub.message
    assert message["state"] == "correct_pose"
    assert message["wrong"] == [], "nothing is wrong about it"
    assert message["scored_gesture_error"] is False
    said = _recorded(lesson)
    lesson.command({"type": "check", "value": pick.result})
    assert lesson.correct == 1, "it counts for the star score"
    assert _answer_message(said)["first_try"] is True
    assert lesson.scheduler.outcomes[0].correct is True


def _answered_right(lesson, at: float = 0.0) -> dict:
    """Reach the correct pose and answer it, on the lesson's own clock.

    Returns the answer_correct message, which is where the page reads first_try
    and the only place the contract lets it read it.
    """
    said = _recorded(lesson)
    _, right = _poses(lesson)
    lesson.observe(right, [], 2, at)
    lesson.observe(right, [], 2, at + 0.4)
    lesson.command({"type": "check", "value": lesson.pick.result})
    return _answer_message(said)


def _answer_message(said: list[dict]) -> dict:
    found = [message for message in said if message["type"] == "state"
             and message["state"] == "answer_correct"]
    assert found, "the answer never landed"
    return found[-1]


def test_first_try_is_computed_on_the_server():
    lesson = _tutored()
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    assert lesson.hub.message["first_try"] is False, "nothing is won yet"
    assert _answered_right(lesson)["first_try"] is True


def test_first_try_is_lost_by_a_wrong_answer_and_by_asked_for_help():
    lesson = _tutored()
    lesson.command({"type": "start_node", "state": None,
                    "node": _node(pairs=((6, 6), (7, 7)), count=2)})
    said = _recorded(lesson)
    _, right = _poses(lesson)
    lesson.observe(right, [], 2, 0.0)
    lesson.observe(right, [], 2, 0.4)
    lesson.command({"type": "check", "value": 3})
    assert lesson.hub.message["scored_math_error"] is True
    assert lesson.hub.message["first_try"] is False
    lesson.command({"type": "check", "value": lesson.pick.result})
    assert _answer_message(said)["first_try"] is False, "a wrong answer costs it"

    # the next exercise starts clean, and help the child asked for costs it too
    lesson.command({"type": "hint"})
    assert lesson.requested_hints == 1
    assert ("hint_requested", None) in _spoke(lesson)
    assert _answered_right(lesson)["first_try"] is False


def test_automatic_help_never_costs_first_try():
    """first_try and a busy tutor coexist: that pair is what Loop 2 reduces."""
    lesson = _tutored()
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    wrong, _ = _poses(lesson)
    lesson.started_at = 0.0
    lesson.observe(wrong, [], 2, 0.0)
    lesson.observe(wrong, [], 2, 0.4)
    lesson.observe(wrong, [], 2, 5.0)
    assert lesson.hint_level == 1 and lesson.hub.message["hint_auto"] is True
    correct = _answered_right(lesson, at=5.5)
    assert lesson.requested_hints == 0
    assert correct["first_try"] is True


def test_a_rescue_ends_first_try():
    lesson = _tutored()
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    lesson.tutor.tutor.decision = {"intervention_level": server.RESCUE_LEVEL}
    lesson.observe(GestureState(method="unknown", confidence=0.2), [], 0, 0.0)
    assert lesson.rescue_used is True
    assert _answered_right(lesson, at=1.0)["first_try"] is False


def test_the_tutor_factors_survive_the_round_trip_through_node_end():
    lesson = _tutored()
    record = {"learner_id": "9f31c0a7bd42",
              "tutor": {"pace_factor": 1.2, "help_factor": 0.9,
                        "tutor_observations": 7}}
    said = _recorded(lesson)
    lesson.command({"type": "start_node", "node": _node(count=1), "state": record})
    assert lesson.tutor.tutor.factors == {"pace_factor": 1.2, "help_factor": 0.9,
                                          "tutor_observations": 7}
    # the tutor counts the exercise it just logged
    lesson.tutor.tutor.factors["tutor_observations"] = 8
    lesson.command({"type": "next"})

    end = [message for message in said if message["type"] == "node_end"][-1]
    assert end["state"]["learner_id"] == "9f31c0a7bd42"
    assert end["state"]["tutor"] == {"pace_factor": 1.2, "help_factor": 0.9,
                                     "tutor_observations": 8}


def test_a_record_with_no_tutor_block_round_trips_the_defaults():
    lesson = _lesson()
    said = _recorded(lesson)
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    lesson.command({"type": "next"})
    end = [message for message in said if message["type"] == "node_end"][-1]
    assert end["state"]["tutor"] == {"pace_factor": 1.0, "help_factor": 1.0,
                                     "tutor_observations": 0}


def test_an_early_stop_serves_one_mastered_fact_and_then_closes():
    from lesson.scheduler import Record

    lesson = _tutored()
    lesson.command({"type": "start_node", "state": None,
                    "node": _node(pairs=((6, 6), (7, 7)), count=5)})
    lesson.scheduler.state.math["7x7"] = Record(mastery=5)   # a fact she owns
    lesson.tutor.tutor.early_stop = True
    lesson.observe(GestureState(method="unknown", confidence=0.2), [], 0, 0.0)
    assert lesson.early_stop is True, "the tutor decided, the server acts"

    lesson.command({"type": "next"})
    assert lesson.running, "one more exercise, and it is one she owns"
    assert lesson.pick.fact == "7x7" and lesson.pick.reason == "confidence"

    lesson.command({"type": "next"})
    assert not lesson.running, "then the node closes, short of its five"
    assert len(lesson.scheduler.outcomes) == 2


def test_an_early_stop_with_nothing_mastered_closes_straight_away():
    lesson = _tutored()
    lesson.command({"type": "start_node", "state": None,
                    "node": _node(pairs=((6, 6), (7, 7)), count=5)})
    lesson.tutor.tutor.early_stop = True
    lesson.observe(GestureState(method="unknown", confidence=0.2), [], 0, 0.0)
    lesson.command({"type": "next"})
    assert not lesson.running


def test_the_tutor_is_fed_at_the_camera_rate_and_drops_what_is_too_fast():
    """Every window is handed over. The drop is the tutor's, and only the
    tutor's: dropping here as well would halve the rate it actually sees."""
    lesson = _tutored()
    lesson.command({"type": "start_node", "state": None,
                    "node": _node(pairs=((6, 6), (7, 7)), count=2)})
    gesture = GestureState(method="unknown", confidence=0.2)
    for step in range(101):                      # one second of a 100 Hz camera
        lesson.observe(gesture, [], 0, step * 0.01)

    stub = lesson.tutor.tutor
    assert len(stub.handed) == 101, "the camera rate, not a filtered one"
    assert len(stub.observations) == 15, f"15 fps inside the tutor: {len(stub.observations)}"
    gaps = [after[2] - before[2] for before, after in zip(stub.observations,
                                                         stub.observations[1:])]
    assert min(gaps) >= 1 / stub.fps - 1e-6


def _hands(shift: float = 0.0) -> list[dict]:
    """Ten fingertips, both hands, moved sideways by shift."""
    from classifier.schema import FINGER_NUMBERS

    return [{"hand": hand, "number": number, "x": base + shift, "y": 0.5}
            for hand, base in (("left", 0.3), ("right", 0.7))
            for number in FINGER_NUMBERS]


def test_the_motion_measure_is_a_median_move_in_palm_widths():
    meter = server.MotionMeter()
    assert meter.update(_hands(), 0.1, 0.0) == 0.0, "one sample cannot move"
    assert meter.update(_hands(0.02), 0.1, 0.1) == pytest.approx(0.2, abs=1e-3)
    # 0.4 s later the move has left the window and the hands are still again
    assert meter.update(_hands(0.02), 0.1, 0.6) == 0.0
    # the same move, twice as close to the camera, reads the same
    near = server.MotionMeter()
    near.update(_hands(), 0.2, 0.0)
    assert near.update(_hands(0.04), 0.2, 0.1) == pytest.approx(0.2, abs=1e-3)


def test_the_motion_measure_reaches_the_tutor():
    lesson = _tutored()
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    gesture = GestureState(method="unknown", confidence=0.2)
    lesson.observe(gesture, _hands(), 2, 0.0, palm=0.1)
    lesson.observe(gesture, _hands(0.02), 2, 0.1, palm=0.1)
    assert lesson.tutor.tutor.observations[-1][1] == pytest.approx(0.2, abs=1e-3)


def test_the_camera_check_is_where_the_jitter_is_measured():
    """The measurement lives in app/tutor.py; the server says when it runs."""
    lesson = _tutored()
    lesson.command({"type": "start_node", "state": None, "node": {
        "id": "check", "kind": "check", "pairs": [[6, 6]], "count": 1}})
    assert ("check_start", None) in _spoke(lesson)
    gesture = GestureState(method="unknown", confidence=0.2)
    for step in range(5):
        lesson.observe(gesture, _hands(), 2, step / 15, palm=0.1)
    lesson.command({"type": "next"})             # the check is one exercise
    assert ("check_end", None) in _spoke(lesson)


def test_a_lesson_never_starts_or_ends_the_measurement():
    lesson = _tutored()
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    lesson.command({"type": "next"})
    assert [kind for kind, _ in _spoke(lesson)
            if kind in ("check_start", "check_end")] == []


def test_the_engine_hint_rides_the_observation():
    """It is what the tutor builds its correction and its ghost out of."""
    lesson = _tutored()
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    wrong, _ = _poses(lesson)
    lesson.observe(wrong, [], 2, 0.0)
    lesson.observe(wrong, [], 2, 0.4)
    hints = [payload for kind, payload in _spoke(lesson) if kind == "hint"]
    assert hints and hints[-1]["hand"] and hints[-1]["move_to"]


def test_the_seam_survives_a_tutor_that_is_missing_or_raises():
    import types

    class Broken:
        def __init__(self, params=None, lines=None, clock=None, log=None,
                     learner_id="", factors=None):
            raise RuntimeError("half written")

    class Silent:
        """Every method of the contract missing, on purpose."""

        def __init__(self, params=None, lines=None, clock=None, log=None,
                     learner_id="", factors=None):
            self.learner_id = learner_id

    empty = types.SimpleNamespace()          # a module with nothing in it yet
    for module in (empty, _stub_module(Broken), _stub_module(Silent)):
        lesson = _lesson()
        lesson.tutor = server.TutorLink(module, keep_log=False)
        lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
        lesson.command({"type": "tts", "speaking": False})
        lesson.observe(GestureState(method="unknown", confidence=0.2), [], 0, 0.0)
        correct = _answered_right(lesson, at=1.0)
        assert correct["tutor_state"] == "WORKING"
        assert correct["intervention_level"] == 0
        assert correct["mode"] == "normal"
        assert correct["first_try"] is True


def test_the_seam_is_aligned_with_app_tutor_as_it_stands():
    """The stub above pins the vocabulary; this pins it to the real module."""
    module = pytest.importorskip("app.tutor")

    lesson = _lesson()
    lesson.tutor = server.TutorLink(module, keep_log=False)
    lesson.command({"type": "start_node", "node": _node(count=1), "state": None})
    assert lesson.tutor.live, "app/tutor.py built with its params and its lines"

    lesson.command({"type": "tts", "speaking": True})
    lesson.command({"type": "tts", "speaking": False})
    lesson.command({"type": "speech", "text": "is it thirty six"})
    wrong, _ = _poses(lesson)
    for now in (0.0, 0.4, 1.0):
        lesson.observe(wrong, _hands(), 2, now, palm=0.1)

    message = lesson.hub.message
    assert message["tutor_state"] in set(getattr(module, "STATES", ()))
    assert message["mode"] in ("supportive", "normal", "independent")
    assert 0 <= message["intervention_level"] <= 4
    assert json.dumps(message), "whatever it decided is JSON serialisable"

    correct = _answered_right(lesson, at=2.0)
    assert correct["first_try"] is True, "nothing was asked for and nothing scored"
    assert lesson.hub.message["state"]["tutor"]["tutor_observations"] == 1


# --- the design images -------------------------------------------------------


def test_the_design_art_is_served_and_never_escapes_its_directory():
    """The renders live in web/course/art and the page asks for art/<name>.png."""
    if not server.ART_DIR.is_dir():
        pytest.skip("the design pass has not made web/course/art yet")
    images = sorted(server.ART_DIR.glob("*.png"))
    if not images:
        pytest.skip("the design pass has not copied any art in yet")
    wanted = server.ART_DIR / "tally.png"
    image = wanted if wanted.is_file() else images[0]

    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            response = await client.get(f"/art/{image.name}")
            assert response.status == 200, image.name
            assert response.headers["Content-Type"] == "image/png"
            assert await response.read() == image.read_bytes()

            # nothing outside web/course/art, and no directory listing either
            for escape in ("/art/%2e%2e%2fapp.js", "/art/..%2Fapp.js",
                           "/art/%2Fetc%2Fpasswd", "/art/missing.png"):
                refused = await client.get(escape)
                assert refused.status == 404, escape
            assert (await client.get("/art/")).status == 403
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_a_missing_art_directory_is_not_a_broken_server(tmp_path, monkeypatch):
    """The design pass makes the directory when it is ready, not before."""
    monkeypatch.setattr(server, "ART_DIR", tmp_path / "not-there-yet")

    async def scenario():
        app = server.create_app(mock=True)
        srv, client = await _client(app)
        try:
            assert (await client.get("/art/tally.png")).status == 404
            page = await client.get("/")
            assert page.status == 200, "the rest of the app is untouched"
        finally:
            await client.close()
            await srv.close()
    run(scenario())


# --- live samples, app/live_samples.py ---------------------------------------


def _window(offset: float = 0.0) -> Window:
    """One perception window of plausible normalized points.

    The offset shifts every point, so two windows are never the same
    measurement and the writer's dedupe keeps both.
    """
    def hand(shift: float) -> HandFrame:
        points = [(0.1 * index + shift, 0.05 * index + shift, 0.0) for index in range(21)]
        return HandFrame(points=points, detection_conf=0.9,
                         wrist_xy=(0.3 + shift, 0.5), scale=0.12)

    return Window(left=[hand(offset) for _ in range(5)],
                  right=[hand(offset + 1.0) for _ in range(5)])


def _live(lesson, tmp_path, windows: int = 8, enabled: bool = True) -> Path:
    """Point the lesson's writer at a temporary file, never data/live_samples.jsonl."""
    path = tmp_path / "live_samples.jsonl"
    lesson.live = server.LiveSampleWriter(path, windows, enabled,
                                          logging.getLogger("test.server.live"))
    return path


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _start(lesson, name: str = "lea", pairs=((8, 7),), count: int = 1) -> None:
    """A node for one named child, the way the page starts one."""
    lesson.command({"type": "start_node", "state": {"learner_id": name},
                    "node": _node("u2-l1", pairs=pairs, count=count)})


def _hold(lesson, gesture, times, offset: float = 0.0, offer: bool = True) -> None:
    """Hold one pose: a window offered on every frame, as the camera thread does.

    offer is False for a writer that raises on the frame path: that path is
    guarded inside server.camera_loop, and the camera tests below drive it.
    """
    for index, now in enumerate(times):
        if offer:
            lesson.live.offer(_window(offset + index * 0.05), gesture)
        lesson.observe(gesture, [], 2, now)


def _confirmed(lesson, offset: float = 0.0, offer: bool = True) -> str:
    """The right pose, then the right answer. Returns the exercise title."""
    pick = lesson.pick
    _, right = _poses(lesson)
    _hold(lesson, right, (0.0, 0.4, 0.8, 1.2, 1.6, 2.0), offset=offset, offer=offer)
    title = lesson.engine.exercise.title
    lesson.command({"type": "check", "value": pick.result})
    return title


def test_a_confirmed_exercise_writes_the_last_windows_it_saw(tmp_path):
    """The one moment a lesson can label: a latched pose and a correct answer."""
    lesson = _lesson()
    path = _live(lesson, tmp_path, windows=3)
    _start(lesson)
    pick = lesson.pick
    title = _confirmed(lesson)

    written = _rows(path)
    assert len(written) == 3, "the last N windows of the hold, and no more"
    for row in written:
        assert row["confirmed_by_answer"] is True
        assert row["exercise"] == title
        assert row["label"] == {"method": "6-10", "left": pick.left,
                                "right": pick.right, "contact": True}
        assert row["seen_pose"] == row["label"], "the label is what was read"
        assert row["target_pose"] == row["label"]
        assert row["source"] == "live" and row["split"] == "live"
        assert len(row["window"]) == 5
    # The last three offered, not the first three: the thumb tip of _window(o)
    # sits at x = 0.4 + o, and the six holds are offered 0.05 apart.
    tips = [row["window"][-1]["left"]["points"][4][0] for row in written]
    assert tips == pytest.approx([0.55, 0.60, 0.65])


def test_only_a_scored_gesture_error_writes_a_hard_negative(tmp_path):
    """A wrong finger is how the input works. Held past the grace, it is an error."""
    lesson = _lesson()
    path = _live(lesson, tmp_path, windows=2)
    _start(lesson)
    pick = lesson.pick
    wrong, _ = _poses(lesson)

    _hold(lesson, wrong, (0.0, 0.4, 0.4 + server.POSE_GRACE_SECONDS - 0.1))
    assert lesson.scored_gesture_error is False
    assert _rows(path) == [], "a wrong pose inside the grace is not a negative"

    _hold(lesson, wrong, (0.4 + server.POSE_GRACE_SECONDS,), offset=1.0)
    assert lesson.scored_gesture_error is True

    written = _rows(path)
    assert len(written) == 2, "the windows of the held pose, N of them"
    for row in written:
        assert row["confirmed_by_answer"] is False
        assert row["seen_pose"] == {"method": "6-10", "left": wrong.left,
                                    "right": wrong.right, "contact": False}
        assert row["target_pose"] == {"method": "6-10", "left": pick.left,
                                      "right": pick.right, "contact": True}
        assert row["label"] == row["seen_pose"]
        assert row["exercise"] == lesson.engine.exercise.title

    # Scored once per exercise, so a pose still held writes nothing more.
    _hold(lesson, wrong, (12.0, 12.4), offset=2.0)
    assert len(_rows(path)) == 2


def test_a_mock_run_and_a_demo_run_record_nothing():
    """Neither is a child in front of a camera, so neither is a sample."""
    for flags in ({"mock": True}, {"demo": True}, {"mock": True, "demo": True}):
        lesson = server.create_app(**flags)[server.LESSON_KEY]
        assert lesson.live.enabled is False, flags

    live = server.create_app()[server.LESSON_KEY].live
    assert live.enabled is True, "a real camera in front of a real child records"
    assert live.path == server.LIVE_SAMPLES_PATH
    assert live.path.name == "live_samples.jsonl", "never the frozen dataset"


def test_a_disabled_writer_plays_a_whole_exercise_and_writes_nothing(tmp_path):
    lesson = _lesson()
    path = _live(lesson, tmp_path, enabled=False)
    _start(lesson)
    wrong, _ = _poses(lesson)
    _hold(lesson, wrong, (0.0, 0.4, 0.4 + server.POSE_GRACE_SECONDS))
    assert lesson.scored_gesture_error is True
    _confirmed(lesson, offset=2.0)
    assert not path.exists()


def test_a_lesson_never_opens_the_frozen_dataset(tmp_path, monkeypatch):
    """data/samples.jsonl and the held out set are frozen by the contract."""
    import builtins

    opened: list[tuple[str, str]] = []
    real_open = builtins.open

    def spy(file, mode="r", *args, **kwargs):
        opened.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)

    lesson = _lesson()
    path = _live(lesson, tmp_path, windows=2)
    _start(lesson)
    _confirmed(lesson)

    assert _rows(path), "the lesson did record something"
    assert not any(Path(name).name in ("samples.jsonl", "test.jsonl")
                   for name, _ in opened)
    written = {name for name, mode in opened if any(c in mode for c in "wax+")}
    assert written == {str(path)}, "one file is written, and it is the live one"


def test_a_row_carries_a_hash_of_the_name_and_never_the_name(tmp_path):
    """The name keeps the profiles apart on the device; the dataset gets an id."""
    import hashlib

    lesson = _lesson()
    path = _live(lesson, tmp_path, windows=2)
    _start(lesson, name="  Lea  ")
    _confirmed(lesson)

    digest = hashlib.sha256(b"lea").hexdigest()[:12]
    written = _rows(path)
    assert written
    for row in written:
        assert row["learner_id"] == digest and row["person"] == digest
        assert "lea" not in json.dumps(list(row.values())).lower()

    assert server.live_learner_id("Lea") == digest, "trimmed, lowercased, stable"
    assert server.live_learner_id("Noe") != digest, "two children, two learners"
    assert server.live_learner_id("") == "" and server.live_learner_id(None) == ""


def test_the_window_count_comes_from_the_tutor_params_file():
    raw = json.loads(server.TUTOR_PARAMS_PATH.read_text(encoding="utf-8"))
    assert raw["global"]["live_sample_windows"] == 8
    assert raw["bounds"]["live_sample_windows"] == [4, 32]
    assert server.windows_from_params(server.load_tutor_params()) == 8
    assert server.create_app(mock=True)[server.LESSON_KEY].live.windows == 8


def test_the_window_count_falls_back_to_eight_without_the_key(tmp_path):
    """A file that is gone, broken or silent on the key costs a lesson nothing."""
    missing = tmp_path / "not-there.json"
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    silent = tmp_path / "silent.json"
    silent.write_text(json.dumps({"global": {"fps": 15}}), encoding="utf-8")
    for path in (missing, broken, silent):
        assert server.load_tutor_params(path) in ({}, {"global": {"fps": 15}})
        assert server.windows_from_params(server.load_tutor_params(path)) == 8

    chosen = tmp_path / "chosen.json"
    chosen.write_text(json.dumps({"global": {"live_sample_windows": 5}}),
                      encoding="utf-8")
    assert server.windows_from_params(server.load_tutor_params(chosen)) == 5


class ExplodingWriter:
    """A writer whose every call raises, which is what a writer must never do."""

    enabled = True

    def offer(self, window, state):
        raise RuntimeError("the disk is on fire")

    def confirm_correct(self, *args):
        raise RuntimeError("the disk is on fire")

    def record_gesture_error(self, *args):
        raise RuntimeError("the disk is on fire")


def test_a_writer_that_raises_never_costs_the_child_the_lesson():
    lesson = _lesson()
    lesson.live = ExplodingWriter()
    _start(lesson)
    pick = lesson.pick
    wrong, _ = _poses(lesson)
    _hold(lesson, wrong, (0.0, 0.4, 0.4 + server.POSE_GRACE_SECONDS), offer=False)
    assert lesson.scored_gesture_error is True, "the lesson scored it all the same"
    _confirmed(lesson, offset=2.0, offer=False)

    assert lesson.scheduler.outcomes[0].given == pick.result
    assert lesson.hub.message["type"] == "node_end", "the node finished"


class FramingCamera:
    """A capture that delivers a few lit frames and then asks the loop to stop."""

    def __init__(self, stop: threading.Event, frames: int = 3) -> None:
        self._stop = stop
        self._frames = frames
        self.calls = 0
        self.released = False

    def read(self):
        import numpy as np

        self.calls += 1
        if self.calls >= self._frames:
            self._stop.set()
        return True, np.zeros((48, 64, 3), dtype="uint8")

    def release(self) -> None:
        self.released = True


def _camera_run(lesson, monkeypatch, gesture) -> FramingCamera:
    """The real camera thread, with a fake camera and a fixed classifier."""
    import app.camera as camera_module
    import app.landmarks as landmarks
    import classifier.loader as loader

    stop = threading.Event()
    camera = FramingCamera(stop)
    monkeypatch.setattr(camera_module, "open_camera", lambda index=None: (camera, 0))
    monkeypatch.setattr(landmarks, "HandDetector", FakeDetector)
    monkeypatch.setattr(loader, "load_classifier", lambda: (lambda window: gesture))
    server.camera_loop(lesson, stop, None)
    return camera


def test_the_camera_thread_only_buffers_and_touches_no_file(tmp_path, monkeypatch):
    """offer is on the frame path, so it may never do IO there."""
    lesson = _lesson()
    path = _live(lesson, tmp_path)
    gesture = GestureState(method="6-10", left=8, right=7, contact=True, confidence=0.9)
    camera = _camera_run(lesson, monkeypatch, gesture)

    assert camera.calls >= 3 and camera.released
    assert not path.exists(), "the frame path never writes"
    # The windows the classifier saw were kept, so an event can still write one.
    assert lesson.live.confirm_correct("lea", "8 x 7", (8, 7)) == 1
    assert _rows(path)[0]["label"] == {"method": "6-10", "left": 8, "right": 7,
                                       "contact": True}


def test_a_writer_that_raises_on_the_frame_path_is_not_a_camera_failure(monkeypatch):
    lesson = _lesson()
    lesson.live = ExplodingWriter()
    gesture = GestureState(method="6-10", left=8, right=7, contact=True, confidence=0.9)
    camera = _camera_run(lesson, monkeypatch, gesture)

    assert camera.calls >= 3 and camera.released
    assert lesson.hub.message["tally"] != server.CAMERA_LOST
    assert lesson.fault is None, "a sample is never worth the camera"



def test_a_refused_tab_cannot_quit_answer_skip_hint_or_repeat_the_running_node():
    """The second tab is refused its node but keeps its keys, buttons and microphone.
    Over the real socket, none of what it sends may reach the first tab's lesson."""
    first_node = {"id": "check", "kind": "check", "pairs": [[6, 6]], "count": 1}
    second_node = {"id": "u1-l2", "kind": "lesson", "pairs": [[6, 7]]}

    async def scenario():
        app = server.create_app(mock=True)
        lesson = app[server.LESSON_KEY]
        srv, client = await _client(app)
        try:
            first = await client.ws_connect("/ws")
            await first.send_json({"type": "start_node", "tab": "one", "state": None,
                                   "node": first_node})
            # the mock never skips inside the check, so the pose latches and waits
            await _await(first, lambda m: m.get("node") == "check"
                         and m.get("state") == "correct_pose")

            second = await client.ws_connect("/ws")
            await second.send_json({"type": "start_node", "tab": "two", "state": None,
                                    "node": second_node})
            await _await(second, lambda m: m.get("tally") == server.ALREADY_PLAYING)
            for command in ({"type": "hint"}, {"type": "repeat"},
                            {"type": "check", "value": 36}, {"type": "next"},
                            {"type": "quit"}):
                await second.send_json(command)
            await asyncio.sleep(0.3)
            assert lesson.running and lesson.node["id"] == "check", "the node was taken"
            assert lesson.engine.latched, "repeat reached the running node"
            assert lesson.hint_level == 0, "hint reached the running node"
            assert lesson.scheduler.outcomes == [], "check or next reached the running node"

            await first.send_json({"type": "check", "value": 36})
            ended = await _await(first, lambda m: m.get("type") == "node_end")
            assert ended["node_id"] == "check" and ended["correct"] == 1
            await first.close()
            await second.close()
        finally:
            await client.close()
            await srv.close()
    run(scenario())


def test_only_the_owner_changes_the_session_and_its_page_can_take_it_back():
    lesson = _lesson()
    owner, intruder = object(), object()
    lesson.command({"type": "start_node", "tab": "one", "node": _node(count=2),
                    "state": None}, client=owner)
    lesson.command({"type": "start_node", "tab": "two", "node": _node("u1-l2"),
                    "state": None}, client=intruder)
    pick, learner = lesson.pick, lesson.learner
    right = GestureState(method="6-10", left=pick.left, right=pick.right,
                         contact=True, confidence=0.95)
    lesson.observe(right, [], 2, 0.0)
    lesson.observe(right, [], 2, 0.4)
    assert lesson.engine.latched

    def untouched() -> None:
        assert lesson.running and lesson.node["id"] == "u1-l1"
        assert lesson.pick is pick and lesson.engine.latched
        assert lesson.hint_level == 0 and lesson.scheduler.outcomes == []
        assert lesson.learner is learner, "a hello swapped the running learner"

    for command in ({"type": "hint"}, {"type": "repeat"}, {"type": "hello", "state": None},
                    {"type": "check", "value": pick.result}, {"type": "next"},
                    {"type": "quit"}):
        lesson.command(command, client=intruder)
    untouched()

    # the owner's socket drops: nobody commands an ownerless node, not even to quit it
    lesson.release(owner)
    assert lesson.owner is None
    lesson.command({"type": "quit"}, client=intruder)
    lesson.command({"type": "next"}, client=owner)
    untouched()

    # the page reconnects on a new socket and asks for its node again
    reconnected = object()
    lesson.command({"type": "start_node", "tab": "one", "node": _node(count=2),
                    "state": None}, client=reconnected)
    assert lesson.owner is reconnected
    lesson.command({"type": "next"}, client=reconnected)
    assert len(lesson.scheduler.outcomes) == 1, "the owner is obeyed again"


def test_a_page_that_reconnects_before_its_old_socket_is_noticed_keeps_its_node():
    """A dropped connection can stay open on the server for a heartbeat. The same page
    on a new socket is the owner, not a second tab; another page still is not."""
    lesson = _lesson()
    old, new, other = object(), object(), object()
    lesson.command({"type": "start_node", "tab": "one", "node": _node(count=2),
                    "state": None}, client=old)

    lesson.command({"type": "start_node", "tab": "two", "node": _node("u1-l2"),
                    "state": None}, client=other)
    assert lesson.owner is old and lesson.node["id"] == "u1-l1"
    lesson.command({"type": "start_node", "node": _node("u1-l2"), "state": None},
                   client=other)
    assert lesson.owner is old, "no tab at all is not the owner's tab"

    lesson.command({"type": "start_node", "tab": "one", "node": _node(count=2),
                    "state": None}, client=new)
    assert lesson.owner is new and lesson.node["id"] == "u1-l1"
    lesson.command({"type": "next"}, client=old)
    assert lesson.scheduler.outcomes == [], "the dead socket lost the node"
    lesson.release(old)                     # noticed late, and it changes nothing
    assert lesson.owner is new
    lesson.command({"type": "next"}, client=new)
    assert len(lesson.scheduler.outcomes) == 1


def test_a_bare_client_owns_its_hello_session_and_can_take_it_back():
    lesson = _lesson()
    mine, other = object(), object()
    lesson.command({"type": "hello", "state": None}, client=mine)
    assert lesson.owner is mine
    lesson.command({"type": "next"}, client=other)
    assert lesson.scheduler.outcomes == []
    lesson.release(mine)
    again = object()
    lesson.command({"type": "hello", "state": None}, client=again)
    assert lesson.owner is again
    lesson.command({"type": "next"}, client=again)
    assert len(lesson.scheduler.outcomes) == 1

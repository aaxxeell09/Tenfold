"""app/server.py end to end in mock mode: no camera, no browser, no port.

The mock loop drives the real Engine with synthetic GestureStates, so these
tests exercise the same path the webcam takes, minus MediaPipe.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta

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
                            "fact", "session", "node", "demo"}
    assert message["pose_slip"] is False and message["hint_auto"] is False
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
    lesson = server.Lesson(Engine(), server.Hub(), server.make_scheduler)
    lesson.demo_available = demo
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
    assert lesson.pose_error is False, "the child is still inside the grace"
    assert lesson.hub.message["pose_slip"] is False
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
    assert lesson.pose_error is False, "the grace runs from the correction"
    lesson.observe(wrong, [], 2, 0.4 + server.POSE_GRACE_SECONDS)
    assert lesson.pose_error is True
    assert lesson.hub.message["pose_slip"] is True, "the page reads the slip here"

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

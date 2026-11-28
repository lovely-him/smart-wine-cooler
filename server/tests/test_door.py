"""门磁：模拟实现必须能干净地造出"关门一次"，开/关语义不能颠倒。"""

import pytest

from server.platform.door import CLOSED, OPENED, MockDoor, load_door


def test_initial_state_has_no_event():
    door = MockDoor()
    assert door.state() == CLOSED
    assert door.poll() is None


def test_close_produces_one_event():
    door = MockDoor()
    door.set(OPENED)
    door.poll()
    door.set(CLOSED)
    assert door.poll() == CLOSED
    assert door.poll() is None


def test_open_produces_event_too():
    door = MockDoor()
    door.set(OPENED)
    assert door.poll() == OPENED


def test_same_state_twice_is_not_an_event():
    door = MockDoor()
    door.set(CLOSED)
    assert door.poll() is None


def test_events_queue_in_order():
    door = MockDoor()
    door.set(OPENED)
    door.set(CLOSED)
    door.set(OPENED)
    assert [door.poll(), door.poll(), door.poll()] == [OPENED, CLOSED, OPENED]


def test_toggle_flips_state():
    door = MockDoor()
    door.toggle()
    assert door.poll() == OPENED
    door.toggle()
    assert door.poll() == CLOSED


def test_load_door_defaults_to_mock():
    door = load_door({})
    assert isinstance(door, MockDoor)


def test_load_door_rejects_unknown_backend():
    with pytest.raises(ValueError):
        load_door({"backend": "serial"})

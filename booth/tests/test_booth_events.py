import json

from uploader.booth_events import dispatch, parse_events


class Recorder:
    def __init__(self):
        self.calls = []

    def job_state(self, target, present_id=None):
        self.calls.append(("job_state", target, present_id))

    def media_inserted(self, media_id):
        self.calls.append(("media_inserted", media_id))


def stream(*events):
    lines = []
    for name, data in events:
        lines += [f"event: {name}\r\n".encode(), f"data: {data}\r\n".encode(), b"\r\n"]
    return lines


def state(target, present=None):
    return json.dumps({"source": "x", "target": target,
                       "jobmodel": {"typ": "collage", "present_mediaitem_id": present}})


def test_parse_events_splits_on_blank_lines():
    lines = stream(("ping", "2026-11-07"), ("ProcessStateinfo", state("counting")))
    assert list(parse_events(lines)) == [("ping", "2026-11-07"), ("ProcessStateinfo", state("counting"))]


def test_parse_events_joins_multi_line_data_and_skips_comments():
    lines = [b": keep-alive\n", b"event: x\n", b"data: a\n", b"data: b\n", b"\n"]
    assert list(parse_events(lines)) == [("x", "a\nb")]


def test_job_states_are_forwarded_with_the_presented_item():
    sessions = Recorder()
    dispatch("ProcessStateinfo", state("counting"), sessions)
    dispatch("ProcessStateinfo", state("present", "uuid-1"), sessions)
    assert sessions.calls == [("job_state", "counting", None), ("job_state", "present", "uuid-1")]


def test_an_empty_job_state_means_the_job_failed_or_was_cancelled():
    sessions = Recorder()
    dispatch("ProcessStateinfo", "{}", sessions)
    assert sessions.calls == [("job_state", None, None)]


def test_db_insert_forwards_the_media_id():
    sessions = Recorder()
    dispatch("DbInsert", json.dumps({"id": "uuid-2", "processed": "media/processed_full/x.jpg"}), sessions)
    assert sessions.calls == [("media_inserted", "uuid-2")]


def test_other_and_broken_events_are_ignored():
    sessions = Recorder()
    dispatch("LogRecord", "{}", sessions)
    dispatch("ProcessStateinfo", "{not json", sessions)
    dispatch("DbInsert", "[]", sessions)
    assert sessions.calls == []

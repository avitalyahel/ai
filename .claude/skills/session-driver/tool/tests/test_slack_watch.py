"""Run: python3 -I ~/Claude/sessions-watch/tests/test_slack_watch.py

Slack thread watching, on a temp pins file:
- A posted message's link gives the thread to watch: a top-level post its own thread,
  a reply its parent thread.
- A permalink without a message link is ignored.
- A repeated post moves its thread to the newest position.
- A thread ref gives the thread's Slack URL.
- slack-watch pins a thread to a session and slack-unwatch unpins it.
- slack-watch writes only the pins file: it creates no hub folder.
- slack-watch without a session or with a bad channel id is rejected.
- An expired pin is dropped.
"""

import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("watch", os.path.join(HERE, "..", "watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

SLACK = "https:\\/\\/axonius.slack.com\\/archives\\/"
CHANNEL = "C01234ABCDE"
THREAD = "1794130763.468379"
REF = f"slack:{CHANNEL}/{THREAD}"
SID = "00000000-0000-4000-8000-000000000001"


def tool_result(text):
    return json.dumps([{"type": "tool_result", "content": [{"type": "text", "text": text}]}])


def message_link(channel, ts, thread_ts=None):
    link = f"{SLACK}{channel}\\/p{ts.replace('.', '')}"
    if thread_ts:
        link += f"?thread_ts={thread_ts}&cid={channel}"
    return tool_result(f'{{"message_link":"{link}"}}')


class SlackRefs(unittest.TestCase):
    CASES = [
        ("top-level post", [message_link(CHANNEL, THREAD)], [REF]),
        (
            "thread reply",
            [message_link(CHANNEL, "1793894083.893169", thread_ts="1793787462.879939")],
            [f"slack:{CHANNEL}/1793787462.879939"],
        ),
        (
            "permalink only",
            [
                tool_result(
                    f"Permalink: https://axonius.slack.com/archives/{CHANNEL}/p1787143647913179"
                )
            ],
            [],
        ),
        (
            "repeat post",
            [
                message_link("C1AAAAAAA", "1700000000.000001"),
                message_link("C2BBBBBBB", "1700000000.000002"),
                message_link("C1AAAAAAA", "1700000000.000001"),
            ],
            ["slack:C2BBBBBBB/1700000000.000002", "slack:C1AAAAAAA/1700000000.000001"],
        ),
    ]

    def test_slack_refs_from_posted_messages(self):
        for name, results, expected in self.CASES:
            with self.subTest(name):
                info = {"slack": []}
                for result in results:
                    watch.slack_refs(result, info)
                self.assertEqual(info["slack"], expected)

    def test_doc_url_for_slack_thread(self):
        self.assertEqual(
            watch.doc_url(REF), f"https://axonius.slack.com/archives/{CHANNEL}/p1794130763468379"
        )


class SlackPins(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        for name, value in (("SLACK_PINS", "slack-pins.json"), ("HUB_DIR", "hub")):
            self.addCleanup(setattr, watch, name, getattr(watch, name))
            setattr(watch, name, os.path.join(self.folder, value))

    def test_slack_watch_writes_only_the_pins_file(self):
        argv = ["w", "slack-watch", CHANNEL, THREAD, "--session", SID]
        self.assertEqual(watch.cmd_slack_watch(argv), 0)
        self.assertEqual(os.listdir(self.folder), ["slack-pins.json"])

    def test_slack_watch_pins_and_unpins_a_thread(self):
        self.assertEqual(
            watch.cmd_slack_watch(["w", "slack-watch", CHANNEL, THREAD, "--session", SID]), 0
        )
        self.assertEqual(watch.slack_pins()[REF]["sid"], SID)
        self.assertEqual(watch.cmd_slack_watch(["w", "slack-unwatch", CHANNEL, THREAD]), 0)
        self.assertEqual(watch.slack_pins(), {})

    def test_slack_watch_rejects_bad_input(self):
        cases = [
            ("no session", ["w", "slack-watch", CHANNEL, THREAD]),
            ("bad channel", ["w", "slack-watch", "bad", THREAD, "--session", SID]),
        ]
        for name, argv in cases:
            with self.subTest(name):
                self.assertEqual(watch.cmd_slack_watch(argv), 1)
                self.assertEqual(watch.slack_pins(), {})

    def test_expired_pin_is_dropped(self):
        expired = {
            "slack:C1AAAAAAA/1700000000.000001": {"sid": "x", "until": "2020-01-01T00:00:00Z"}
        }
        watch.save_json(watch.SLACK_PINS, expired)
        self.assertEqual(watch.slack_pins(), {})


if __name__ == "__main__":
    unittest.main()

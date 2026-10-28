"""Tests for lib/grok_worker.py. No grok process is started; fixtures/ holds real Grok CLI 1.0.70
streaming-json captures, reduced to the events the module reads and with paths replaced.

Run: python3 -m unittest discover -s tests
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import grok_worker as g  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
SETTINGS = os.path.join(HERE, "..", "settings", "worker_settings.json")
TEMPLATE = os.path.join(HERE, "..", "templates", "worker_protocol.grok.md")

GOOD = """STATUS: DONE
## Summary
Did the thing.
## Claims verified / falsified / unverified
1. VERIFIED: the file exists.
## Root cause
n/a, not a bug.
## What I did
Read a file.
## Evidence
ls showed it.
## Open items / risks
none
## Needs from human
none
## Cost of a shortcut
none tempting
"""


def harvest(fixture):
    with tempfile.TemporaryDirectory() as d:
        return g.harvest(os.path.join(FIXTURES, fixture), os.devnull, os.path.join(d, "run.json"), "grok-4.7")


def outcome(run, result=None):
    """classify() on a run dict and an optional result.md text -> (word, printed line)."""
    with tempfile.TemporaryDirectory() as d:
        run_path, result_path = os.path.join(d, "run.json"), os.path.join(d, "result.md")
        with open(run_path, "w") as fh:
            json.dump(run, fh)
        if result is not None:
            with open(result_path, "w") as fh:
                fh.write(result)
        buf = io.StringIO()
        with redirect_stdout(buf):
            word = g.classify(run_path, result_path)
        return word, buf.getvalue()


class TranslateTests(unittest.TestCase):
    def test_prefix_rule_matches_bare_and_with_arguments(self):
        self.assertEqual(g.translate("Bash(git add:*)"), ["Bash(git add)", "Bash(git add *)"])
        self.assertEqual(g.translate("Bash(.venv/bin/pytest:*)"), ["Bash(.venv/bin/pytest)", "Bash(.venv/bin/pytest *)"])

    def test_unchanged_rules(self):
        for rule in ("Read", "Bash(pwd)", "Write(orchestration/workers/x/**)", "Bash(bash orchestration/workers/x/scratch/*)"):
            self.assertEqual(g.translate(rule), [rule])

    def test_tools_without_a_grok_rule_are_dropped(self):
        for rule in ("Glob", "WebSearch", "SendMessage", "ListAgents"):
            self.assertEqual(g.translate(rule), [])

    def test_home_is_expanded(self):
        self.assertEqual(g.translate("Edit(~/.claude/**)"), [f"Edit({os.path.expanduser('~')}/.claude/**)"])


class DenyTests(unittest.TestCase):
    def test_deny_list_covers_git_dash_c_agents_and_grok_settings(self):
        rules = g.deny_rules(SETTINGS)
        for rule in ("Bash(git push)", "Bash(git push *)", "Bash(git -C * push*)", "Bash(git -C * reset --hard*)",
                     "Bash(rm -rf *)", "Bash(claude *)", "Bash(grok *)", "Bash(orch *)", "Write(orchestration/plan.md)",
                     f"Write({os.path.expanduser('~')}/.grok/**)"):
            self.assertIn(rule, rules)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.home = os.path.join(root, "home")
        self.outer = os.path.join(self.home, "work")
        self.project = os.path.join(self.outer, "project")
        os.makedirs(os.path.join(self.project, ".claude"))
        for path, text in ((os.path.join(self.home, "CLAUDE.md"), "home file"),
                           (os.path.join(self.outer, "CLAUDE.md"), "outer rules"),
                           (os.path.join(self.project, "CLAUDE.md"), "project rules {{WORKER_ID}}"),
                           (os.path.join(self.project, ".claude", "CLAUDE.md"), "dot-claude rules")):
            with open(path, "w") as fh:
                fh.write(text)
        os.link(os.path.join(self.project, "CLAUDE.md"), os.path.join(self.project, "AGENTS.md"))   # same file twice

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_from_project_up_to_home_outermost_first(self):
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            files = g.instruction_files(self.project)
        self.assertEqual(files, [os.path.join(self.outer, "CLAUDE.md"), os.path.join(self.project, "CLAUDE.md"),
                                 os.path.join(self.project, ".claude", "CLAUDE.md")])

    def test_render_fills_placeholders_and_embeds_instructions_verbatim(self):
        out = os.path.join(self.tmp.name, "proto.md")
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            g.render(TEMPLATE, out, self.project, "p1-01-x", "orchestration/workers/p1-01-x", "mgr", "build")
        text = g._read(out)
        self.assertNotIn("{{INSTRUCTION_FILES}}", text)
        self.assertNotIn("{{ROLE_SHELL}}", text)
        self.assertIn("worker `p1-01-x` (role: **build**)", text)
        self.assertIn("bash orchestration/workers/p1-01-x/scratch/<name>.sh", text)
        self.assertIn("outer rules", text)
        self.assertIn("project rules {{WORKER_ID}}", text)   # user text is not treated as a placeholder
        self.assertNotIn("home file", text)

    def test_large_files_are_truncated_then_listed(self):
        for path in (os.path.join(self.outer, "CLAUDE.md"), os.path.join(self.project, ".claude", "CLAUDE.md")):
            with open(path, "w") as fh:
                fh.write("x" * (g.FILE_CAP + 50))
        with mock.patch.dict(os.environ, {"HOME": self.home}), mock.patch.object(g, "TOTAL_CAP", g.FILE_CAP + 1000):
            block = g.instructions_block(self.project)
        self.assertIn("truncated at", block)                     # the outer file, embedded and cut
        self.assertIn("project rules", block)                    # small file still embedded
        self.assertIn("Not reproduced here for size", block)     # the second big one only listed
        self.assertIn(os.path.join(self.project, ".claude", "CLAUDE.md"), block.split("Not reproduced")[1])


class HarvestTests(unittest.TestCase):
    def test_done_stream(self):
        run = harvest("grok_done.jsonl")
        self.assertEqual((run["engine"], run["terminal_reason"], run["is_error"]), ("grok", "completed", False))
        self.assertEqual(run["session_id"], "00000000-0000-7000-8000-000000000001")
        self.assertGreater(run["num_turns"], 0)
        self.assertEqual(run["permission_denials"], [])

    def test_unallowed_command_cancels_the_session(self):
        run = harvest("grok_cancelled.jsonl")
        self.assertEqual(run["terminal_reason"], "permission_cancelled")
        self.assertEqual(run["cancelled_by"]["input"], "cd /work/project && sqlite3 --version")
        self.assertEqual(run["permission_denials"][0]["tool_name"], "Bash")

    def test_explicit_deny_is_recorded_but_not_a_cancel(self):
        run = harvest("grok_denied.jsonl")
        self.assertEqual(run["terminal_reason"], "completed")
        self.assertIsNone(run["cancelled_by"])
        self.assertEqual(run["permission_denials"][0]["tool_input"]["command"], "wc -l calc.py")

    def test_turn_cap(self):
        self.assertEqual(harvest("grok_max_turns.jsonl")["terminal_reason"], "max_turns")

    def test_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, "stream.jsonl")
            open(empty, "w").close()
            run = g.harvest(empty, os.devnull, os.path.join(d, "run.json"), "grok-4.7")
        self.assertTrue(run["is_error"])
        self.assertEqual(run["error_message"], "no output from grok")


class ClassifyTests(unittest.TestCase):
    def test_done(self):
        self.assertEqual(outcome(harvest("grok_done.jsonl"), GOOD)[0], "DONE")

    def test_cancelled_without_result_is_blocked_with_the_command(self):
        word, line = outcome(harvest("grok_cancelled.jsonl"))
        self.assertEqual(word, "BLOCKED")
        self.assertIn("not allowed: run_terminal_command: cd /work/project && sqlite3 --version", line)

    def test_good_result_wins_over_a_late_cancel(self):
        word, line = outcome(harvest("grok_cancelled.jsonl"), GOOD)
        self.assertEqual(word, "DONE")
        self.assertIn("cancelled by an unallowed command after writing result", line)

    def test_turn_cap_without_result(self):
        self.assertEqual(outcome(harvest("grok_max_turns.jsonl"))[0], "MAX_TURNS")

    def test_turn_cap_with_broken_result_is_incomplete(self):
        self.assertEqual(outcome(harvest("grok_max_turns.jsonl"), "STATUS: DONE\n## Summary\nhalf\n")[0], "INCOMPLETE")

    def test_missing_section(self):
        word, line = outcome(harvest("grok_done.jsonl"), GOOD.replace("## Root cause\nn/a, not a bug.\n", ""))
        self.assertEqual(word, "INCOMPLETE")
        self.assertIn("missing ## Root cause", line)

    def test_too_long(self):
        self.assertEqual(outcome(harvest("grok_done.jsonl"), GOOD + "x\n" * 150)[0], "INCOMPLETE")

    def test_blocked_must_say_what_it_needs(self):
        self.assertEqual(outcome(harvest("grok_done.jsonl"), GOOD.replace("STATUS: DONE", "STATUS: BLOCKED"))[0], "INCOMPLETE")

    def test_rate_limit(self):
        run = dict(harvest("grok_done.jsonl"), is_error=True, terminal_reason="error",
                   error_message="429 Too Many Requests: weekly usage limit reached")
        self.assertEqual(outcome(run)[0], "RATE_LIMITED")

    def test_error_without_output(self):
        run = {"engine": "grok", "is_error": True, "terminal_reason": "error", "error_message": "connection reset"}
        self.assertEqual(outcome(run)[0], "API_ERROR")

    def test_unreadable_run(self):
        self.assertEqual(outcome({})[0], "SCRIPT_ERROR")


class BudgetTests(unittest.TestCase):
    WEEK = 7 * 24 * 3600

    def word(self, used, elapsed, status="ok"):
        start = 1_800_000_000
        data = {"status": status, "billing": {"used_percentage": used,
                                              "period_start": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(start)),
                                              "period_end": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(start + self.WEEK))}}
        return g.budget(data, now=start + elapsed * self.WEEK)["word"]

    def test_words(self):
        self.assertEqual(self.word(20, 0.5), "SURPLUS")      # on course for 40%
        self.assertEqual(self.word(40, 0.5), "ON_PACE")      # on course for 80%
        self.assertEqual(self.word(60, 0.5), "TIGHT")        # on course for 120%
        self.assertEqual(self.word(98, 0.9), "EXHAUSTED")
        self.assertEqual(self.word(70, 0.85), "SURPLUS")     # under 36 h left with 30% unspent
        self.assertEqual(self.word(5, 0.02), "SURPLUS")      # early week: pace floored at 10% elapsed
        self.assertEqual(self.word(9, 0.02), "ON_PACE")
        self.assertEqual(self.word(10, 0.5, status="rate_limited"), "EXHAUSTED")

    def test_unknown_without_meter(self):
        self.assertEqual(g.budget({})["word"], "UNKNOWN")


class LatchTests(unittest.TestCase):
    def test_rate_limit_latches_and_a_run_clears_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "grok_limits.json")
            g.latch(path, "RATE_LIMITED", "worker-p1-01", "usage limit reached")
            with redirect_stdout(io.StringIO()) as buf:
                self.assertEqual(g.show_limits(path), 2)
            self.assertIn("budget=EXHAUSTED", buf.getvalue())
            g.latch(path, "DONE", "worker-p1-02")
            self.assertEqual(g._load(path)["status"], "ok")

    def test_no_data(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()):
            self.assertEqual(g.show_limits(os.path.join(d, "none.json")), 3)


if __name__ == "__main__":
    unittest.main()

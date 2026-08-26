"""The provider adapters, and the config that chooses between them.

Two things are worth testing here and neither of them needs a model. One is the
command line each adapter builds, because a wrong flag is a failed review and
the only other way to find out is to pay for one. The other is the reply
parsing, because every CLI wraps its answer differently and the wrappers change.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from reviewer import model, providers
from reviewer.config import ConfigError, GlobalConfig, RepoConfig

SYSTEM = "be terse"
USER = "review this"


def prepare(name: str, cfg: dict | None = None, add_dir: Path | None = None):
    adapter = providers.get(name)
    return adapter, adapter.prepare(
        {"type": name, **(cfg or {})},
        system_prompt=SYSTEM,
        user_prompt=USER,
        add_dir=add_dir,
        scratch=Path("/tmp/scratch"),
    )


def flag(command: list[str], name: str) -> str | None:
    """The value after ``name``, or None if the flag is absent."""
    if name not in command:
        return None
    index = command.index(name)
    return command[index + 1] if index + 1 < len(command) else ""


class Claude(unittest.TestCase):
    def test_the_system_prompt_goes_in_its_own_flag(self):
        _, call = prepare("claude")
        self.assertEqual(flag(call.command, "--append-system-prompt"), SYSTEM)
        # ...and the user prompt on stdin, never as an argument: a review
        # bundle would hit ARG_MAX long before anything else went wrong.
        self.assertEqual(call.stdin, USER)
        self.assertNotIn(USER, call.command)

    def test_tools_default_to_read_only_and_the_denylist_is_always_sent(self):
        _, call = prepare("claude")
        self.assertEqual(flag(call.command, "--allowedTools"), "Read,Glob,Grep")
        self.assertEqual(
            flag(call.command, "--disallowedTools"), ",".join(providers.BLOCKED_TOOLS)
        )

    def test_an_explicit_empty_tool_list_means_no_tools(self):
        # What a merge summary asks for. Distinct from an absent setting, which
        # falls back to the read-only default above.
        _, call = prepare("claude", {"allowed_tools": []})
        self.assertIsNone(flag(call.command, "--allowedTools"))
        self.assertIn("--disallowedTools", call.command)

    def test_the_checkout_is_added_only_when_there_is_one(self):
        _, call = prepare("claude", add_dir=Path("/co"))
        self.assertEqual(flag(call.command, "--add-dir"), "/co")
        _, call = prepare("claude")
        self.assertIsNone(flag(call.command, "--add-dir"))

    def test_extra_args_come_last_so_they_can_override(self):
        _, call = prepare("claude", {"extra_args": ["--verbose"]})
        self.assertEqual(call.command[-1], "--verbose")

    def test_the_envelope_is_unwrapped_and_usage_kept(self):
        adapter, call = prepare("claude")
        reply = adapter.read(
            call,
            json.dumps(
                {
                    "result": '{"verdict": "ok"}',
                    "usage": {"output_tokens": 12},
                    "total_cost_usd": 0.5,
                }
            ),
        )
        self.assertEqual(reply.text, '{"verdict": "ok"}')
        self.assertEqual(reply.usage["output_tokens"], 12)
        self.assertEqual(reply.usage["total_cost_usd"], 0.5)

    def test_an_error_envelope_is_raised_not_parsed(self):
        adapter, call = prepare("claude")
        with self.assertRaises(providers.ProviderError):
            adapter.read(call, json.dumps({"is_error": True, "result": "nope"}))

    def test_plain_text_output_still_works(self):
        # Older CLI versions print the reply directly, with no envelope.
        adapter, call = prepare("claude")
        self.assertEqual(adapter.read(call, '{"a": 1}').text, '{"a": 1}')


def stream(*events: dict) -> str:
    """The CLI's stream-json output: one compact JSON object per line."""
    return "".join(json.dumps(event) + "\n" for event in events)


def assistant(*blocks: dict) -> dict:
    return {"type": "assistant", "message": {"content": list(blocks)}}


class ClaudeStreaming(unittest.TestCase):
    """stream-json, which is what makes a stuck call distinguishable from a slow
    one — a call that has printed nothing for ten minutes looks nothing like a
    call that is ten minutes in and still working, but only if something is
    reading the lines as they arrive."""

    def test_it_asks_for_a_stream_and_the_verbose_flag_that_needs(self):
        _, call = prepare("claude")
        self.assertEqual(flag(call.command, "--output-format"), "stream-json")
        # Required alongside stream-json under --print; without it the CLI
        # refuses the combination and the review never starts.
        self.assertIn("--verbose", call.command)

    def test_the_answer_comes_from_the_final_result_event(self):
        adapter, call = prepare("claude")
        reply = adapter.read(
            call,
            stream(
                {"type": "system", "subtype": "init"},
                assistant({"type": "text", "text": "thinking out loud"}),
                {
                    "type": "result",
                    "subtype": "success",
                    "result": '{"verdict": "ok"}',
                    "usage": {"output_tokens": 12},
                    "total_cost_usd": 0.5,
                },
            ),
        )
        self.assertEqual(reply.text, '{"verdict": "ok"}')
        self.assertEqual(reply.usage["output_tokens"], 12)
        self.assertEqual(reply.usage["total_cost_usd"], 0.5)

    def test_an_error_event_is_raised_with_only_the_readable_part(self):
        adapter, call = prepare("claude")
        with self.assertRaises(providers.ProviderError) as caught:
            adapter.read(
                call,
                stream(
                    {
                        "type": "result",
                        "is_error": True,
                        "result": "usage limit reached",
                        "session_id": "0c18e40c-9a3e-4313",
                        "usage": {"input_tokens": 1745},
                    }
                ),
            )
        message = str(caught.exception)
        self.assertIn("usage limit reached", message)
        # Not the whole event: the session id and the token counts bury the one
        # sentence that says what went wrong.
        self.assertNotIn("0c18e40c", message)

    def test_a_stream_with_no_result_event_falls_back_to_what_was_said(self):
        # A call killed part-way still has the model's own words in it, and
        # those are a far better guess than the raw JSONL — which would reach
        # the JSON extractor as a wall of events and fail to parse.
        adapter, call = prepare("claude")
        reply = adapter.read(
            call,
            stream(
                assistant({"type": "text", "text": "first"}),
                assistant({"type": "text", "text": '{"verdict": "ok"}'}),
            ),
        )
        self.assertEqual(reply.text, '{"verdict": "ok"}')

    def test_the_answer_is_found_without_reading_the_whole_stream(self):
        # Every file the model read is in the stream as a tool result with its
        # contents inline, so a big review runs to megabytes. The closing event
        # is by definition the last line, and parsing everything before it to
        # reach it would hold the review in memory twice for nothing.
        parsed = []
        bulk = stream(
            *[
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "content": "x" * 80}]},
                }
                for _ in range(500)
            ]
        )
        tail = json.dumps({"type": "result", "result": "done", "usage": {}})

        adapter, call = prepare("claude")
        real = providers.json.loads

        def counting_loads(text, *a, **kw):
            parsed.append(text)
            return real(text, *a, **kw)

        with mock.patch.object(providers.json, "loads", counting_loads):
            reply = adapter.read(call, bulk + tail + "\n")

        self.assertEqual(reply.text, "done")
        # One line read, not five hundred and one.
        self.assertEqual(len(parsed), 1)

    def test_a_single_json_object_is_still_understood(self):
        # --output-format json, from an older CLI or from extra_args.
        adapter, call = prepare("claude")
        reply = adapter.read(
            call, json.dumps({"type": "result", "result": "hi", "usage": {}})
        )
        self.assertEqual(reply.text, "hi")


class ClaudeProgress(unittest.TestCase):
    """The note beside the spinner. Never worth failing a review over, so the
    only hard requirement is that nothing here raises."""

    def setUp(self):
        self.adapter = providers.get("claude")

    def test_a_tool_call_says_what_is_being_read(self):
        note = self.adapter.progress(
            json.dumps(
                assistant(
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/repo/src/auth.py"},
                    }
                )
            )
        )
        # The basename only: this lands beside a spinner in a fixed-width line.
        self.assertEqual(note, "reading auth.py")

    def test_the_last_block_of_a_turn_wins(self):
        note = self.adapter.progress(
            json.dumps(
                assistant(
                    {"type": "text", "text": "let me look"},
                    {"type": "tool_use", "name": "Grep", "input": {"pattern": "TODO"}},
                )
            )
        )
        self.assertEqual(note, "searching TODO")

    def test_prose_with_no_tool_call_means_the_review_is_being_written(self):
        note = self.adapter.progress(
            json.dumps(assistant({"type": "text", "text": "Here are my findings"}))
        )
        self.assertEqual(note, "writing the review")

    def test_events_with_no_news_leave_the_previous_note_standing(self):
        for line in (
            "",
            "not json at all",
            json.dumps({"type": "result", "result": "done"}),
            json.dumps({"type": "user", "message": {"content": []}}),
        ):
            self.assertIsNone(self.adapter.progress(line), line)

    def test_an_unknown_tool_degrades_to_its_own_name(self):
        note = self.adapter.progress(
            json.dumps(assistant({"type": "tool_use", "name": "Sparkle", "input": {}}))
        )
        self.assertEqual(note, "sparkle")

    def test_a_provider_that_does_not_stream_reports_nothing(self):
        # The honest default: a CLI that prints one object at the end has
        # nothing to say until it is finished.
        self.assertIsNone(providers.get("gemini").progress('{"type": "assistant"}'))


class Codex(unittest.TestCase):
    def test_it_runs_exec_read_only_outside_a_repo(self):
        _, call = prepare("codex")
        self.assertEqual(call.command[1], "exec")
        self.assertEqual(flag(call.command, "--sandbox"), "read-only")
        # The working directory is a scratch dir, so there is no repository
        # there and codex would otherwise refuse to start.
        self.assertIn("--skip-git-repo-check", call.command)

    def test_the_prompt_is_stdin_and_carries_the_system_prompt(self):
        _, call = prepare("codex")
        self.assertEqual(call.command[-1], "-")
        self.assertIn(SYSTEM, call.stdin)
        self.assertIn(USER, call.stdin)
        self.assertNotIn(SYSTEM, call.command)

    def test_the_answer_is_read_from_the_file_it_was_asked_for(self):
        adapter = providers.get("codex")
        with tempfile.TemporaryDirectory() as scratch:
            call = adapter.prepare(
                {"type": "codex"},
                system_prompt=SYSTEM,
                user_prompt=USER,
                add_dir=None,
                scratch=Path(scratch),
            )
            self.assertEqual(flag(call.command, "--output-last-message"),
                             str(call.last_message_file))
            call.last_message_file.write_text('{"verdict": "ok"}', encoding="utf-8")

            events = "\n".join(
                json.dumps(event)
                for event in (
                    {"type": "agent_message", "message": "ignored"},
                    {"type": "token_count", "info": {"output_tokens": 99}},
                )
            )
            reply = adapter.read(call, events)

        self.assertEqual(reply.text, '{"verdict": "ok"}')
        self.assertEqual(reply.usage["output_tokens"], 99)

    def test_an_empty_file_falls_back_to_the_event_stream(self):
        # Rather than throwing away a review because one output channel of two
        # came up empty.
        adapter = providers.get("codex")
        with tempfile.TemporaryDirectory() as scratch:
            call = adapter.prepare(
                {"type": "codex"},
                system_prompt=SYSTEM,
                user_prompt=USER,
                add_dir=None,
                scratch=Path(scratch),
            )
            reply = adapter.read(
                call, json.dumps({"type": "agent_message", "message": '{"a": 1}'})
            )
        self.assertEqual(reply.text, '{"a": 1}')

    def test_junk_lines_in_the_event_stream_are_skipped(self):
        adapter, call = prepare("codex")
        reply = adapter.read(call, "warming up\nnot json\n{\"b\": 2}")
        self.assertIn('{"b": 2}', reply.text)

    def test_the_older_nested_event_shape_still_reads(self):
        # The event type has lived at the top level and under "msg" across
        # releases; a wrong guess here is a review parsed as junk.
        adapter, call = prepare("codex")
        reply = adapter.read(
            call,
            json.dumps({"msg": {"type": "agent_message", "message": '{"a": 1}'}}),
        )
        self.assertEqual(reply.text, '{"a": 1}')

    def test_an_event_whose_msg_is_not_an_object_is_survivable(self):
        adapter, call = prepare("codex")
        reply = adapter.read(call, json.dumps({"msg": "starting", "type": "info"}))
        self.assertIsInstance(reply.text, str)


class Gemini(unittest.TestCase):
    def test_tool_names_are_translated_to_its_own(self):
        _, call = prepare("gemini")
        self.assertEqual(
            flag(call.command, "--allowed-tools"), "read_file,glob,search_file_content"
        )

    def test_unknown_tool_names_are_passed_through(self):
        _, call = prepare("gemini", {"allowed_tools": ["read_many_files"]})
        self.assertEqual(flag(call.command, "--allowed-tools"), "read_many_files")

    def test_writes_are_never_pre_approved(self):
        _, call = prepare("gemini", {"extra_args": []})
        self.assertEqual(flag(call.command, "--approval-mode"), "default")
        self.assertNotIn("--yolo", call.command)

    def test_the_checkout_is_included_only_when_there_is_one(self):
        _, call = prepare("gemini", add_dir=Path("/co"))
        self.assertEqual(flag(call.command, "--include-directories"), "/co")
        _, call = prepare("gemini")
        self.assertIsNone(flag(call.command, "--include-directories"))

    def test_the_response_field_is_the_reply(self):
        adapter, call = prepare("gemini")
        reply = adapter.read(
            call,
            json.dumps(
                {"response": '{"a": 1}', "stats": {"models": {"x": {"candidates": 7}}}}
            ),
        )
        self.assertEqual(reply.text, '{"a": 1}')
        self.assertEqual(reply.usage["output_tokens"], 7)

    def test_an_error_object_is_raised(self):
        adapter, call = prepare("gemini")
        with self.assertRaises(providers.ProviderError):
            adapter.read(call, json.dumps({"error": {"message": "quota"}}))


class GenericCommand(unittest.TestCase):
    def test_it_assumes_nothing_beyond_stdin_and_stdout(self):
        adapter, call = prepare("command", {"command": "my-agent",
                                            "extra_args": ["--headless"]})
        self.assertEqual(call.command, ["my-agent", "--headless"])
        self.assertIn(SYSTEM, call.stdin)
        self.assertEqual(adapter.read(call, '{"a": 1}').text, '{"a": 1}')


class UnknownType(unittest.TestCase):
    def test_it_names_the_types_that_do_exist(self):
        with self.assertRaises(providers.ProviderError) as caught:
            providers.get("gpt4all")
        self.assertIn("claude", str(caught.exception))

    def test_a_call_with_an_unknown_type_fails_before_spawning_anything(self):
        with self.assertRaises(model.ModelError):
            model.run({"type": "nope"}, system_prompt="", user_prompt="")


class ExtractJson(unittest.TestCase):
    def test_it_survives_the_ways_a_model_wraps_an_object(self):
        for text in (
            '{"a": 1}',
            '```json\n{"a": 1}\n```',
            'Here you go:\n{"a": 1}\nHope that helps.',
        ):
            with self.subTest(text=text):
                self.assertEqual(model.extract_json(text), {"a": 1})

    def test_nothing_usable_is_an_error(self):
        with self.assertRaises(model.ModelError):
            model.extract_json("no object here")


def write_config(directory: Path, data: dict) -> None:
    (directory / "global.json").write_text(json.dumps(data), encoding="utf-8")


class Choosing(unittest.TestCase):
    """Which provider a given call ends up on."""

    def setUp(self):
        os.environ["GITHUB_TOKEN"] = "test-token"
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(os.environ.pop, "GITHUB_TOKEN", None)

    def repo(self, global_cfg: GlobalConfig, **raw) -> RepoConfig:
        path = self.dir / "acme__widgets.json"
        path.write_text(
            json.dumps({"repo": "acme/widgets", "local_path": None, **raw}),
            encoding="utf-8",
        )
        return RepoConfig.load(path, global_cfg)

    def load(self, **data) -> GlobalConfig:
        write_config(self.dir, data)
        return GlobalConfig.load(self.dir)

    def test_the_default_is_claude_with_no_config_at_all(self):
        cfg = GlobalConfig.load(self.dir).provider_for()
        self.assertEqual(cfg["name"], "claude")
        self.assertEqual(cfg["type"], "claude")
        self.assertEqual(cfg["command"], "claude")

    def test_a_pass_has_a_time_limit_of_its_own(self):
        # max_reviews_per_tick bounds the count, not the time. Six reviews
        # against a 900s provider timeout is ninety minutes of worst case
        # against a fifteen-minute tick, and the repositories at the end of the
        # list are the ones that wait for it.
        self.assertEqual(GlobalConfig.load(self.dir).max_tick_seconds, 1800)
        self.assertEqual(self.load(max_tick_seconds=600).max_tick_seconds, 600)

    def test_a_pass_can_be_given_no_time_limit(self):
        self.assertIsNone(self.load(max_tick_seconds=None).max_tick_seconds)
        self.assertIsNone(self.load(max_tick_seconds=0).max_tick_seconds)

    def test_a_repo_can_pin_only_the_model(self):
        global_cfg = self.load(providers={"claude": {"model": "claude-opus-5"}})
        repo = self.repo(global_cfg, model={"model": "claude-sonnet-5"})

        self.assertEqual(global_cfg.provider_for()["model"], "claude-opus-5")
        resolved = global_cfg.provider_for(repo)
        self.assertEqual(resolved["model"], "claude-sonnet-5")
        # ...and inherits everything it did not mention.
        self.assertEqual(resolved["allowed_tools"], ["Read", "Glob", "Grep"])

    def test_a_repo_can_move_provider_entirely(self):
        global_cfg = self.load()
        repo = self.repo(global_cfg, model={"provider": "codex", "model": "gpt-5.1-codex"})
        resolved = global_cfg.provider_for(repo)
        self.assertEqual(resolved["type"], "codex")
        self.assertEqual(resolved["command"], "codex")
        self.assertEqual(resolved["model"], "gpt-5.1-codex")

    def test_a_null_in_a_repo_block_means_inherit(self):
        # The sample config ships the block with every key null, so this is the
        # shape most repo configs are actually in.
        global_cfg = self.load(providers={"claude": {"model": "claude-opus-5"}})
        repo = self.repo(global_cfg, model={"provider": None, "model": None})
        self.assertEqual(global_cfg.provider_for(repo)["model"], "claude-opus-5")

    def test_a_named_profile_needs_a_type_but_then_works(self):
        global_cfg = self.load(
            providers={"cheap": {"type": "claude", "model": "claude-haiku-4-5-20251001"}},
            provider="cheap",
        )
        self.assertEqual(global_cfg.provider_for()["model"], "claude-haiku-4-5-20251001")

    def test_summaries_get_no_tools_and_a_shorter_timeout(self):
        global_cfg = self.load()
        cfg = global_cfg.summary_provider_for()
        self.assertEqual(cfg["allowed_tools"], [])
        self.assertEqual(cfg["timeout_seconds"], 180)

    def test_summaries_follow_the_repo_to_its_provider(self):
        global_cfg = self.load()
        repo = self.repo(global_cfg, model={"provider": "gemini"})
        self.assertEqual(global_cfg.summary_provider_for(repo)["name"], "gemini")

    def test_thread_replies_default_to_whatever_reviews(self):
        global_cfg = self.load(providers={"claude": {"model": "claude-opus-5"}})
        repo = self.repo(global_cfg)
        self.assertEqual(global_cfg.thread_provider_for(repo)["model"], "claude-opus-5")

    def test_thread_replies_can_drop_a_tier_on_their_own(self):
        # The point of the block: plan on the big model, follow up on the small
        # one, without touching what reviews.
        global_cfg = self.load(
            providers={"claude": {"model": "claude-opus-5"}},
            thread_reply={"model": "claude-sonnet-5"},
        )
        repo = self.repo(global_cfg)
        self.assertEqual(global_cfg.provider_for(repo)["model"], "claude-opus-5")
        self.assertEqual(global_cfg.thread_provider_for(repo)["model"], "claude-sonnet-5")

    def test_thread_replies_keep_their_tools_unlike_summaries(self):
        # Checking a claim against the code is most of the point of replying.
        global_cfg = self.load(thread_reply={"model": "claude-sonnet-5"})
        self.assertEqual(
            global_cfg.thread_provider_for()["allowed_tools"], ["Read", "Glob", "Grep"]
        )
        self.assertEqual(global_cfg.summary_provider_for()["allowed_tools"], [])

    def test_a_thread_timeout_of_null_inherits_the_provider_s_own(self):
        global_cfg = self.load(providers={"claude": {"timeout_seconds": 600}})
        self.assertEqual(global_cfg.thread_provider_for()["timeout_seconds"], 600)

    def test_a_summary_provider_drops_a_model_meant_for_another_one(self):
        # "gpt-5.1-codex" means nothing to claude, and passing it on would fail
        # the call rather than fall back.
        global_cfg = self.load(merge_summary={"provider": "claude"})
        repo = self.repo(global_cfg, model={"provider": "codex", "model": "gpt-5.1-codex"})
        cfg = global_cfg.summary_provider_for(repo)
        self.assertEqual(cfg["name"], "claude")
        self.assertIsNone(cfg["model"])


class Rejected(unittest.TestCase):
    """Bad config is an error at load, not fifteen minutes into a watch loop."""

    def setUp(self):
        os.environ["GITHUB_TOKEN"] = "test-token"
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(os.environ.pop, "GITHUB_TOKEN", None)

    def load(self, **data) -> GlobalConfig:
        write_config(self.dir, data)
        return GlobalConfig.load(self.dir)

    def test_a_default_naming_nothing(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(provider="ollama")
        self.assertIn("ollama", str(caught.exception))

    def test_a_profile_with_no_type(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(providers={"work": {"model": "x"}}, provider="work")
        self.assertIn("type", str(caught.exception))

    def test_a_type_that_does_not_exist(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(providers={"work": {"type": "gpt4all"}}, provider="work")
        self.assertIn("gemini", str(caught.exception))

    def test_the_generic_type_without_a_command_to_run(self):
        with self.assertRaises(ConfigError):
            self.load(providers={"mine": {"type": "command"}}, provider="mine")

    def test_a_summary_provider_naming_nothing(self):
        with self.assertRaises(ConfigError):
            self.load(merge_summary={"provider": "ollama"})

    def test_a_thread_reply_provider_naming_nothing(self):
        with self.assertRaises(ConfigError) as caught:
            self.load(thread_reply={"provider": "ollama"})
        self.assertIn("thread_reply.provider", str(caught.exception))

    def test_a_repo_naming_a_provider_that_is_not_configured(self):
        global_cfg = self.load()
        path = self.dir / "acme__widgets.json"
        path.write_text(
            json.dumps({"repo": "acme/widgets", "model": {"provider": "ollama"}}),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError) as caught:
            RepoConfig.load(path, global_cfg)
        self.assertIn("ollama", str(caught.exception))

    def test_a_repo_that_wrote_a_model_name_where_a_block_goes(self):
        global_cfg = self.load()
        path = self.dir / "acme__widgets.json"
        path.write_text(
            json.dumps({"repo": "acme/widgets", "model": "claude-opus-5"}),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError) as caught:
            RepoConfig.load(path, global_cfg)
        self.assertIn('{"model": {"model": "..."}}', str(caught.exception))


class Samples(unittest.TestCase):
    """The tracked sample files, which are the only config reference there is."""

    def setUp(self):
        os.environ["GITHUB_TOKEN"] = "test-token"
        self.addCleanup(os.environ.pop, "GITHUB_TOKEN", None)

    def test_the_global_sample_loads_as_it_ships(self):
        directory = Path(tempfile.mkdtemp())
        (directory / "global.json").write_text(
            Path("config/global.sample.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        global_cfg = GlobalConfig.load(directory)
        self.assertEqual(global_cfg.provider_for()["name"], "claude")
        # Every entry it documents has to be usable, not just parseable.
        for name in global_cfg.providers:
            with self.subTest(provider=name):
                providers.get(global_cfg.resolve_provider({"provider": name})["type"])

    def test_the_repo_sample_leaves_the_choice_to_the_global_config(self):
        directory = Path(tempfile.mkdtemp())
        global_cfg = GlobalConfig.load(directory)
        path = directory / "acme__widgets.json"
        raw = json.loads(
            Path("config/repos.sample/example-org__example-repo.json").read_text(
                encoding="utf-8"
            )
        )
        raw["local_path"] = None
        path.write_text(json.dumps(raw), encoding="utf-8")

        repo = RepoConfig.load(path, global_cfg)
        self.assertEqual(global_cfg.provider_for(repo)["name"], global_cfg.provider)

    def test_init_can_still_find_what_it_substitutes(self):
        # --init writes the sample with the chosen provider swapped in. A silent
        # no-op here would hand everyone claude whatever they answered.
        from reviewer import bootstrap

        sample = Path("config/global.sample.json").read_text(encoding="utf-8")
        self.assertIn(bootstrap.PROVIDER_PLACEHOLDER, sample)

        repo_sample = Path(
            "config/repos.sample/example-org__example-repo.json"
        ).read_text(encoding="utf-8")
        self.assertIn(bootstrap.REPO_PLACEHOLDER, repo_sample)
        self.assertIn(bootstrap.PATH_PLACEHOLDER, repo_sample)

    def test_every_offered_provider_is_a_real_one(self):
        from reviewer import bootstrap

        for name in bootstrap.OFFERED:
            with self.subTest(provider=name):
                self.assertEqual(providers.get(name).name, name)


if __name__ == "__main__":
    unittest.main()

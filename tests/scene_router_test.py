#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scene_classifier  # noqa: E402
from style_scenes import (  # noqa: E402
    ALLOWED_SCENES,
    DEFAULT_SCENE,
    STYLE_SCENES,
    get_scene_rules,
    normalize_scene,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> dict:
        return self.payload


class _FakeSession:
    def __init__(self, raw_model_response: str, *, status: str = "completed") -> None:
        self.raw_model_response = raw_model_response
        self.status = status
        self.posts: list[dict] = []
        self.gets: list[str] = []

    def post(self, _url: str, *, json: dict, headers: dict) -> _FakeResponse:
        self.posts.append({"json": json, "headers": headers})
        return _FakeResponse({"id": "scene-test-task"})

    def get(self, url: str, *, headers: dict) -> _FakeResponse:
        self.gets.append(url)
        if url.endswith("/status"):
            return _FakeResponse({"status": self.status})
        return _FakeResponse({"result": {"response": self.raw_model_response}})


class StyleScenesTest(unittest.TestCase):
    def test_every_allowed_scene_has_rules(self) -> None:
        self.assertEqual(set(STYLE_SCENES), ALLOWED_SCENES)
        for scene in ALLOWED_SCENES:
            self.assertTrue(get_scene_rules(scene).strip(), scene)

    def test_unknown_scene_falls_back(self) -> None:
        self.assertEqual(normalize_scene("unknown"), DEFAULT_SCENE)
        self.assertEqual(get_scene_rules("unknown"), STYLE_SCENES[DEFAULT_SCENE])

    def test_unsupported_region_rule_forbids_internal_explanation(self) -> None:
        rules = get_scene_rules("unsupported_region")
        self.assertIn("Моск", rules)
        self.assertIn("Московской области", rules)
        self.assertIn("Не объясняй", rules)
        self.assertIn("технические слова", rules)


class SceneClassifierTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.env = patch.dict(
            os.environ,
            {
                "NMBOT_SCENE_CLASSIFIER": "1",
                "NMBOT_SCENE_CONFIDENCE": "0.7",
                "OPENROUTER_API_KEY": "test-openrouter-key",
                "OVERMIND_TOKEN": "test-overmind-token",
                "OVERMIND_URL": "https://overmind.test",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    async def test_high_confidence_allowed_scene_passes(self) -> None:
        session = _FakeSession('{"scene":"unsupported_region","confidence":0.91,"reason":"city outside region"}')
        result = await scene_classifier.classify_scene(
            session,  # type: ignore[arg-type]
            user_text="Студия в Питере до 5 млн",
            search_response="{}",
            draft_response="Черновик",
            timeout=1,
        )
        self.assertEqual(result["scene"], "unsupported_region")
        self.assertFalse(result["fallback_used"])

    async def test_low_confidence_falls_back(self) -> None:
        session = _FakeSession('{"scene":"investment_request","confidence":0.42,"reason":"not sure"}')
        result = await scene_classifier.classify_scene(
            session,  # type: ignore[arg-type]
            user_text="Что есть хорошего?",
            timeout=1,
        )
        self.assertEqual(result["scene"], DEFAULT_SCENE)
        self.assertTrue(result["fallback_used"])

    async def test_unknown_scene_falls_back(self) -> None:
        session = _FakeSession('{"scene":"has_options","confidence":0.99,"reason":"not allowed"}')
        result = await scene_classifier.classify_scene(
            session,  # type: ignore[arg-type]
            user_text="Покажи варианты",
            timeout=1,
        )
        self.assertEqual(result["scene"], DEFAULT_SCENE)
        self.assertTrue(result["fallback_used"])

    async def test_markdown_wrapped_json_is_parsed(self) -> None:
        session = _FakeSession('```json\n{"scene":"ready_to_handoff","confidence":0.88,"reason":"operator"}\n```')
        result = await scene_classifier.classify_scene(
            session,  # type: ignore[arg-type]
            user_text="Позови оператора",
            timeout=1,
        )
        self.assertEqual(result["scene"], "ready_to_handoff")
        self.assertFalse(result["fallback_used"])

    async def test_disabled_classifier_does_not_call_network(self) -> None:
        with patch.dict(os.environ, {"NMBOT_SCENE_CLASSIFIER": "0"}, clear=False):
            session = _FakeSession('{"scene":"unsupported_region","confidence":1.0,"reason":"unused"}')
            result = await scene_classifier.classify_scene(
                session,  # type: ignore[arg-type]
                user_text="Студия в Сочи",
                timeout=1,
            )
        self.assertEqual(result["scene"], DEFAULT_SCENE)
        self.assertTrue(result["fallback_used"])
        self.assertEqual(session.posts, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

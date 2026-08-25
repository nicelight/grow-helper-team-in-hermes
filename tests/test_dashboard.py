from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


try:
    from fastapi import HTTPException
except ModuleNotFoundError:
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail=None) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class APIRouter:
        def get(self, *_args, **_kwargs):
            return lambda function: function

        post = get

    class BaseModel:
        def __init__(self, **kwargs) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def Field(default="", **_kwargs):
        return default

    def Query(default=None, **_kwargs):
        return default

    fastapi = types.ModuleType("fastapi")
    fastapi.APIRouter = APIRouter
    fastapi.HTTPException = HTTPException
    fastapi.Query = Query
    responses = types.ModuleType("fastapi.responses")
    responses.FileResponse = object
    pydantic = types.ModuleType("pydantic")
    pydantic.BaseModel = BaseModel
    pydantic.Field = Field
    sys.modules.setdefault("fastapi", fastapi)
    sys.modules.setdefault("fastapi.responses", responses)
    sys.modules.setdefault("pydantic", pydantic)

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "grow-helper-monitor"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from dashboard import plugin_api
from growhelper_monitor import core, telegram_client


class DashboardRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = dict(os.environ)
        os.environ["GROWHELPER_DATA_ROOT"] = str(Path(self.tmp.name) / "data")
        os.environ["GROWHELPER_TEMPLATE_ROOT"] = str(REPO / "templates")
        self.plant = core.create_plant(
            nickname="Проверка", owner_platform="telegram", owner_chat_id="100",
            board_creator=lambda **kwargs: None,
        )

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def test_repeated_recommendation_is_idempotent(self) -> None:
        body = plugin_api.RecommendationBody(text="Проверьте влажность", idempotency_key="same")
        with patch.object(plugin_api.telegram, "send_text", return_value={"message_id": "501"}) as send:
            first = asyncio.run(plugin_api.recommendation(self.plant["plant_id"], body))
            second = asyncio.run(plugin_api.recommendation(self.plant["plant_id"], body))
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        send.assert_called_once()

    def test_uncertain_recommendation_fences_retry(self) -> None:
        body = plugin_api.RecommendationBody(text="Проверьте влажность", idempotency_key="uncertain")
        error = telegram_client.TelegramDeliveryUncertainError("timeout")
        with patch.object(plugin_api.telegram, "send_text", side_effect=error) as send:
            for _ in range(2):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(plugin_api.recommendation(self.plant["plant_id"], body))
                self.assertEqual(raised.exception.status_code, 409)
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()

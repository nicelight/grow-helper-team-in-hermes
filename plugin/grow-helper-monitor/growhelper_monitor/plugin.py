"""Hermes composition root for the GrowHelper plugin."""
from __future__ import annotations

from typing import Any

from . import commands, gateway, permissions, tools


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="growhelper_plants", toolset="growhelper",
        schema=tools.PLANTS_SCHEMA, handler=tools._handle_plants,
    )
    ctx.register_tool(
        name="growhelper_start_cycle", toolset="growhelper",
        schema=tools.START_CYCLE_SCHEMA, handler=tools._handle_start_cycle,
    )
    ctx.register_tool(
        name="growhelper_publish_reply", toolset="growhelper",
        schema=tools.PUBLISH_SCHEMA, handler=tools._handle_publish_reply,
    )
    ctx.register_tool(
        name="growhelper_request_change", toolset="growhelper",
        schema=tools.REQUEST_CHANGE_SCHEMA, handler=tools._handle_request_change,
    )
    ctx.register_command(
        "addplant", handler=commands._handle_addplant_command,
        description="Создать новый Plant",
    )
    ctx.register_command(
        "plant", handler=commands._handle_plant_command,
        description="Выбрать Plant",
    )
    ctx.register_command(
        "delplant", handler=commands._handle_delplant_command,
        description="Удалить Plant",
    )
    ctx.register_hook("pre_tool_call", permissions._pre_tool_call)
    ctx.register_hook("pre_gateway_dispatch", gateway._pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", gateway._pre_llm_call)
    ctx.register_hook("post_llm_call", gateway._post_llm_call)

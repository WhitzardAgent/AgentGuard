"""Tests for the Event Bus and Actor system."""

import asyncio
import pytest
from agentguard.runtime.event_bus import EventBus, Message
from agentguard.runtime.actors.base import BaseActor


class EchoActor(BaseActor):
    actor_name = "echo"

    def __init__(self, bus: EventBus):
        super().__init__(bus)
        self.received: list[Message] = []

    async def handle(self, msg: Message):
        self.received.append(msg)
        self.reply(msg, f"echo:{msg.payload}")

    async def on_start(self):
        self.bus.subscribe("test_topic", self.receive)


@pytest.mark.asyncio
async def test_bus_pubsub():
    bus = EventBus()
    received = []

    async def handler(msg: Message):
        received.append(msg.payload)

    bus.subscribe("t", handler)
    await bus.publish(Message(topic="t", payload="hello"))
    assert received == ["hello"]


@pytest.mark.asyncio
async def test_bus_request_reply():
    bus = EventBus()

    async def handler(msg: Message):
        if msg.reply_to and not msg.reply_to.done():
            msg.reply_to.set_result(msg.payload * 2)

    bus.subscribe("double", handler)
    result = await bus.request(Message(topic="double", payload=5))
    assert result == 10


@pytest.mark.asyncio
async def test_actor_lifecycle():
    bus = EventBus()
    actor = EchoActor(bus)
    await actor.start()

    future = asyncio.get_event_loop().create_future()
    msg = Message(topic="test_topic", payload="hi", reply_to=future)
    await bus.publish(msg)

    result = await asyncio.wait_for(future, timeout=2.0)
    assert result == "echo:hi"

    await actor.stop()
    assert len(actor.received) == 1

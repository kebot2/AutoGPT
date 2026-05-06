import pytest

from backend.blocks.ai_image_generator_block import (
    TEST_CREDENTIALS,
    TEST_CREDENTIALS_INPUT,
    AIImageGeneratorBlock,
    ImageGenModel,
    ImageSize,
    ImageStyle,
)
from backend.blocks.ai_shortform_video_block import _missing_project_id_message
from backend.blocks.talking_head import _missing_clip_id_message
from backend.data.execution import ExecutionContext


@pytest.fixture(scope="session", autouse=True)
def graph_cleanup():
    yield


async def test_image_generator_storage_error_does_not_suggest_model_fallback(
    monkeypatch,
):
    async def generate_image(self, input_data, credentials):
        return "data:image/png;base64,AAAA"

    async def store_media_file(*args, **kwargs):
        raise ValueError("Workspace storage timed out")

    monkeypatch.setattr(AIImageGeneratorBlock, "generate_image", generate_image)
    monkeypatch.setattr(
        "backend.blocks.ai_image_generator_block.store_media_file", store_media_file
    )

    outputs = await _run_image_generator()

    assert outputs == [("error", "Workspace storage timed out")]


async def test_image_generator_provider_error_suggests_model_fallback(monkeypatch):
    async def generate_image(self, input_data, credentials):
        raise RuntimeError("Provider unavailable")

    monkeypatch.setattr(AIImageGeneratorBlock, "generate_image", generate_image)

    outputs = await _run_image_generator()

    assert outputs[0][0] == "error"
    assert "try another image generation model" in outputs[0][1]


async def _run_image_generator():
    block = AIImageGeneratorBlock()
    outputs = []
    async for output in block.run(
        AIImageGeneratorBlock.Input(
            credentials=TEST_CREDENTIALS_INPUT,
            prompt="A test image",
            model=ImageGenModel.NANO_BANANA_2,
            size=ImageSize.SQUARE,
            style=ImageStyle.ANY,
        ),
        credentials=TEST_CREDENTIALS,
        execution_context=ExecutionContext(user_id="user", graph_exec_id="exec"),
    ):
        outputs.append(output)

    return outputs


def test_revid_missing_project_id_preserves_provider_response_detail():
    message = _missing_project_id_message({"error": "Bad Request: invalid input"})

    assert message == (
        "Failed to create video: No project ID returned: Bad Request: invalid input"
    )
    assert "try another video generation model or block" not in message


def test_d_id_missing_clip_id_preserves_provider_response_detail():
    message = _missing_clip_id_message({"message": "Bad Request: invalid presenter"})

    assert (
        message == "Clip creation returned no clip ID: Bad Request: invalid presenter"
    )
    assert "try another video generation model or block" not in message

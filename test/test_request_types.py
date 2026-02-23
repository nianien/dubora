"""测试 request_types 的校验功能"""
import pytest

from pikppo.models.doubao.request_types import (
    DoubaoASRRequest,
    AudioConfig,
    RequestConfig,
    CorpusConfig,
)


def _make_request(**request_kwargs) -> DoubaoASRRequest:
    return DoubaoASRRequest(
        audio=AudioConfig(url="https://example.com/audio.wav", format="wav"),
        request=RequestConfig(**request_kwargs),
    )


def test_vad_segment_requires_end_window_size():
    req = _make_request(vad_segment=True, end_window_size=None)
    with pytest.raises(ValueError):
        req.validate()


def test_channel_split_requires_multi_channel():
    req = DoubaoASRRequest(
        audio=AudioConfig(
            url="https://example.com/audio.wav", format="wav", channel=1
        ),
        request=RequestConfig(enable_channel_split=True),
    )
    with pytest.raises(ValueError):
        req.validate()


def test_ssd_version_requires_speaker_info():
    req = _make_request(ssd_version="200", enable_speaker_info=False)
    with pytest.raises(ValueError):
        req.validate()


def test_corpus_config_from_hotwords():
    corpus = CorpusConfig.from_hotwords(["平安", "平安哥", "哥"])
    assert corpus.context is not None
    assert len(corpus.context) > 0

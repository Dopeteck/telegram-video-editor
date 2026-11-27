from video_editor.engine import BrollCue, build_filter_complex


def test_filter_graph_contains_timed_cutaway_and_caption_layer():
    graph = build_filter_complex([BrollCue(4.0, 9.0, "city", "City is discussed")], 1080, 1920)
    assert "[1:v]trim=duration=5.000" in graph
    assert "enable='between(t,4.000,9.000)'" in graph
    assert "subtitles=captions.ass[vout]" in graph


def test_caption_only_render_does_not_add_unrelated_footage():
    graph = build_filter_complex([], 1080, 1920)
    assert graph == "[0:v]format=yuv420p[base0];[base0]subtitles=captions.ass[vout]"

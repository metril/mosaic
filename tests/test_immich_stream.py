"""Tests for Immich stream mode (app.services.immich_stream)."""
import asyncio
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
import respx
from PIL import Image

from app.services.immich_stream import (
    StreamAsset,
    StreamFetcher,
    StreamIndex,
    StreamNotFound,
    StreamUpstreamError,
    asset_from_search,
    fit_within,
    render_image,
)

SERVER = "http://immich.test"


def make_image(w=400, h=200, fmt="JPEG") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (200, 50, 50)).save(buf, format=fmt)
    return buf.getvalue()


def make_cfg(**kw):
    base = dict(server_url=SERVER, api_key="k", stream_source="preview", stream_max_w=0,
                stream_max_h=0, stream_format="jpeg", stream_quality=85)
    base.update(kw)
    return SimpleNamespace(**base)


def make_fetcher(cfg):
    return StreamFetcher(lambda name: cfg if name == "p" else None, cache_mb=16)


def asset(mime="image/jpeg", aid="a1"):
    return asset_from_search("p", "pics", {"id": aid, "originalFileName": "IMG.HEIC", "originalMimeType": mime})


# ---- path parsing / index --------------------------------------------------- #

def test_virtual_path_and_ext():
    assert asset().path == "pics/a1.jpg"                                  # native mime keeps its ext
    assert asset("image/heic").path == "pics/a1.jpg"                      # HEIC is served as JPEG
    assert asset("image/png").path == "pics/a1.png"
    assert asset_from_search("p", "/a/b/", {"id": "x", "originalMimeType": "image/webp"}).path == "a/b/x.webp"
    assert asset_from_search("p", "", {"id": "x", "originalMimeType": "image/gif"}).path == "x.gif"


def test_asset_from_search_exif():
    a = asset_from_search("p", "pics", {
        "id": "z", "originalMimeType": "image/jpeg", "isFavorite": True,
        "exifInfo": {"exifImageWidth": 40, "exifImageHeight": 30, "city": "Oslo",
                     "country": "Norway", "dateTimeOriginal": "2024-05-06T10:00:00Z"}})
    assert (a.width, a.height, a.city, a.country, a.is_favorite) == (40, 30, "Oslo", "Norway", True)
    assert a.taken_at.startswith("2024-05-06")


def test_index_lookup_list_replace_upsert_drop(tmp_path):
    db = tmp_path / "c.db"
    idx = StreamIndex(db)
    a1, a2 = asset(aid="a1"), asset(aid="a2")
    idx.replace("p", [a1, a2])
    assert idx.is_stream_folder("pics") and idx.is_stream_folder("/pics/")
    assert idx.lookup("pics/a1.jpg").asset_id == "a1"
    assert idx.lookup("pics/../a1.jpg") is None
    assert sorted(idx.list_folder("pics")) == ["pics/a1.jpg", "pics/a2.jpg"]
    assert idx.list_folder("", recursive=False) == []
    assert len(idx.list_folder("", recursive=True)) == 2

    # replace keeps protected (favorite) paths; upsert only adds
    idx.replace("p", [asset(aid="a3")], keep_paths={"pics/a1.jpg"})
    assert sorted(idx.list_folder("pics")) == ["pics/a1.jpg", "pics/a3.jpg"]
    idx.upsert([asset(aid="a4")])
    assert idx.count("p") == 3

    # persisted: a fresh instance reloads from SQLite
    assert StreamIndex(db).count("p") == 3
    assert idx.drop("p") == 3
    assert not idx.is_stream_folder("pics") and StreamIndex(db).count("p") == 0


# ---- source selection / fallback --------------------------------------------- #

def test_plan_sources():
    plan = StreamFetcher.plan_sources
    assert plan("preview", "image/jpeg") == ["preview"]
    assert plan("fullsize", "image/jpeg") == ["fullsize", "preview"]
    assert plan("original", "image/png") == ["original", "fullsize", "preview"]
    assert plan("original", "image/heic") == ["fullsize", "preview"]       # never /original for HEIC


@respx.mock
def test_heic_original_uses_fullsize_not_original():
    fetcher = make_fetcher(make_cfg(stream_source="original"))
    full = respx.get(f"{SERVER}/api/assets/a1/thumbnail", params={"size": "fullsize"}).respond(content=make_image())
    orig = respx.get(f"{SERVER}/api/assets/a1/original").respond(content=make_image())
    out = asyncio.run(fetcher.get_bytes(asset("image/heic")))
    assert full.call_count == 1 and orig.call_count == 0
    assert full.calls[0].request.headers["x-api-key"] == "k"
    assert out.content_type == "image/jpeg"


@respx.mock
def test_fullsize_4xx_falls_back_to_preview():
    fetcher = make_fetcher(make_cfg(stream_source="fullsize"))
    full = respx.get(f"{SERVER}/api/assets/a1/thumbnail", params={"size": "fullsize"}).respond(400)
    prev = respx.get(f"{SERVER}/api/assets/a1/thumbnail", params={"size": "preview"}).respond(content=make_image())
    asyncio.run(fetcher.get_bytes(asset()))
    assert full.call_count == 1 and prev.call_count == 1


@respx.mock
def test_upstream_errors_map():
    fetcher = make_fetcher(make_cfg())
    route = respx.get(f"{SERVER}/api/assets/a1/thumbnail")
    route.respond(404)
    with pytest.raises(StreamNotFound):
        asyncio.run(fetcher.get_bytes(asset()))
    route.respond(503)
    with pytest.raises(StreamUpstreamError):
        asyncio.run(fetcher.get_bytes(asset()))
    route.mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(StreamUpstreamError):
        asyncio.run(fetcher.get_bytes(asset()))


# ---- rendering ----------------------------------------------------------------- #

def test_fit_within_no_upscale():
    assert fit_within(4000, 3000, 1920, 1080) == (1440, 1080)
    assert fit_within(4000, 3000, 1920, 0) == (1920, 1440)
    assert fit_within(800, 600, 1920, 1080) == (800, 600)      # smaller than bounds: unchanged
    assert fit_within(800, 600, 0, 0) == (800, 600)            # 0 = source size


def test_render_resizes_and_never_upscales():
    data, _ = render_image(make_image(400, 200), 100, 100, "jpeg", 85)
    assert Image.open(BytesIO(data)).size == (100, 50)
    data, _ = render_image(make_image(400, 200), 1920, 1080, "jpeg", 85)
    assert Image.open(BytesIO(data)).size == (400, 200)


@pytest.mark.parametrize("fmt,ct,pil", [("jpeg", "image/jpeg", "JPEG"), ("png", "image/png", "PNG"),
                                        ("webp", "image/webp", "WEBP")])
def test_format_encoding(fmt, ct, pil):
    data, got_ct = render_image(make_image(64, 64, "PNG"), 0, 0, fmt, 80)
    assert got_ct == ct and Image.open(BytesIO(data)).format == pil


def test_original_format_passes_bytes_through():
    src = make_image(64, 64, "PNG")
    data, ct = render_image(src, 0, 0, "original", 85)
    assert data == src and ct == "image/png"
    data, ct = render_image(src, 32, 32, "original", 85)      # resized but keeps its format
    assert ct == "image/png" and Image.open(BytesIO(data)).size == (32, 32)


# ---- cache / single flight -------------------------------------------------------- #

@respx.mock
def test_concurrent_requests_share_one_fetch_and_then_hit_cache():
    fetcher = make_fetcher(make_cfg())
    route = respx.get(f"{SERVER}/api/assets/a1/thumbnail", params={"size": "preview"}).respond(content=make_image())

    async def run():
        first = await asyncio.gather(*(fetcher.get_bytes(asset()) for _ in range(8)))
        again = await fetcher.get_bytes(asset())
        return first, again

    first, again = asyncio.run(run())
    assert route.call_count == 1
    assert len({r.key for r in first}) == 1 and again.data == first[0].data
    stats = fetcher.stats()
    assert stats["entries"] == 1 and stats["misses"] == 1 and stats["hits"] == 1


@respx.mock
def test_clear_pull_and_resize_cache():
    fetcher = make_fetcher(make_cfg())
    route = respx.get(f"{SERVER}/api/assets/a1/thumbnail").respond(content=make_image())
    asyncio.run(fetcher.get_bytes(asset()))
    fetcher.clear_pull("p")
    asyncio.run(fetcher.get_bytes(asset()))
    assert route.call_count == 2
    fetcher.set_cache_mb(1)
    assert fetcher.stats()["entries"] == 0 and fetcher.stats()["max_bytes"] == 1024 * 1024


def test_mpo_treated_as_jpeg():
    # Fake an MPO-tagged JPEG (multi-picture format some phones emit)
    src = make_image(64, 64, "JPEG")
    img = Image.open(BytesIO(src))
    img.format = "MPO"
    import app.services.immich_stream as m
    orig = m.Image.open
    m.Image.open = lambda *a, **k: img
    try:
        data, ct = render_image(src, 32, 32, "original", 85)
    finally:
        m.Image.open = orig
    assert ct == "image/jpeg" and Image.open(BytesIO(data)).format == "JPEG"



# ---- prefetch ------------------------------------------------------------------ #

@respx.mock
def test_prefetch_warms_cache_once_and_swallows_failures():
    fetcher = make_fetcher(make_cfg())
    ok = respx.get(f"{SERVER}/api/assets/a1/thumbnail").respond(content=make_image())
    respx.get(f"{SERVER}/api/assets/bad/thumbnail").respond(status_code=500)

    async def run():
        assert fetcher.prefetch(asset()) is True
        assert fetcher.prefetch(asset()) is False  # already queued
        assert fetcher.prefetch(asset(aid="bad")) is True
        await asyncio.sleep(0.3)
        assert fetcher.prefetch(asset()) is False  # now cached
        assert fetcher._prefetching == set()
        await fetcher.get_bytes(asset())

    asyncio.run(run())
    assert ok.call_count == 1 and fetcher.stats()["hits"] == 1

    fetcher.prefetch_enabled = False
    assert fetcher.prefetch(asset(aid="x")) is False


def test_prefetch_noop_without_loop():
    assert make_fetcher(make_cfg()).prefetch(asset()) is False


@respx.mock
def test_pool_prefetches_exactly_the_next_served_image(tmp_path):
    from pathlib import Path
    from app.services.sync_manager import ImagePool

    fetcher = make_fetcher(make_cfg())
    index = StreamIndex(tmp_path / "idx.db")
    assets = [asset_from_search("p", "pics", {"id": f"id{i}", "originalFileName": f"{i}.jpg",
                                                "originalMimeType": "image/jpeg"}) for i in range(6)]
    index.replace("p", assets)
    for a in assets:
        respx.get(f"{SERVER}/api/assets/{a.asset_id}/thumbnail").respond(content=make_image())
    svc = SimpleNamespace(
        image_dir=Path("/img"), stream_index=index, stream_fetcher=fetcher,
        get_image_files=lambda folder_paths=None, recursive=False: [Path("/img") / a.path for a in assets],
    )

    async def run():
        pool = ImagePool(svc, SimpleNamespace(folders="pics", recursive=False))
        pool.assign_unique_image("b1")
        upcoming = pool.peek_upcoming(1)
        await asyncio.sleep(0.5)
        nxt = pool.assign_unique_image("b1")
        return upcoming, nxt

    upcoming, nxt = asyncio.run(run())
    assert upcoming == [nxt]
    warmed = fetcher.cache_key(make_cfg(), index.lookup(nxt))
    assert fetcher.cache.contains(warmed)

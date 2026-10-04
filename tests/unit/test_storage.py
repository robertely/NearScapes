from nearscapes.storage.local import LocalStorage


def test_upload_temp_is_created_on_storage_volume(tmp_path):
    storage = LocalStorage(tmp_path)

    temporary = storage.create_upload_temp()
    destination = storage.source_path("a" * 64)

    try:
        assert temporary.parent == storage.tmp
        assert temporary.stat().st_dev == destination.parent.stat().st_dev
    finally:
        temporary.unlink(missing_ok=True)


def test_source_path_preserves_safe_suffix(tmp_path):
    storage = LocalStorage(tmp_path)

    path = storage.source_path("b" * 64, "Two Ponds Walk.MP3")

    assert path.name == "source.mp3"

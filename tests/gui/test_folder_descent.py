"""Single-child folder descent preserves deliberate parent/history navigation."""

import zipfile

import pytest

from qtvw.domain.locations import ArchiveLocation
from qtvw.ui.main_window import MainWindow


def settle(qtbot, window):
    qtbot.waitUntil(lambda: not window._listing_active)


@pytest.mark.parametrize("archived", [False, True])
def test_descend_to_images_and_preserve_history(qtbot, tmp_path, image_bytes, archived):
    if archived:
        archive = tmp_path / "album.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("one/two/photo.png", image_bytes())
        destination = ArchiveLocation(archive, "one/two")
        parent = ArchiveLocation(archive, "one")
    else:
        destination = tmp_path / "album" / "one" / "two"
        destination.mkdir(parents=True)
        (destination / "photo.png").write_bytes(image_bytes())
        parent = destination.parent
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    settle(qtbot, window)
    window._open_index(window.model.index(0))
    settle(qtbot, window)
    assert window.navigation.current == destination
    assert [entry.name for entry in window.model.entries] == ["photo.png"]
    window.back()
    settle(qtbot, window)
    assert window.navigation.current == tmp_path
    window.forward()
    settle(qtbot, window)
    assert window.navigation.current == destination
    window.up()
    settle(qtbot, window)
    assert window.navigation.current == parent
    window.refresh()
    settle(qtbot, window)
    assert window.navigation.current == parent


@pytest.mark.parametrize("contents", ["branch", "image", "empty", "loop"])
def test_descent_stops_at_choice_images_empty_or_symlink_cycle(
    qtbot, tmp_path, image_bytes, contents
):
    album = tmp_path / "album"
    child = album / "child"
    child.mkdir(parents=True)
    if contents == "branch":
        (album / "other").mkdir()
        expected = album
    elif contents == "image":
        (album / "photo.png").write_bytes(image_bytes())
        expected = album
    elif contents == "loop":
        child.rmdir()
        child.symlink_to(album, target_is_directory=True)
        expected = album
    else:
        expected = child
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    settle(qtbot, window)
    window._open_index(window.model.index(0))
    settle(qtbot, window)
    assert window.navigation.current == expected

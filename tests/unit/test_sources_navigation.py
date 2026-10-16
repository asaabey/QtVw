from qtvw.domain import natural_key
from qtvw.services import Navigation
from qtvw.sources.filesystem import iter_directory


def test_natural_sort_numeric_names():
    assert sorted(["photo10.jpg", "photo2.jpg", "photo1.jpg"], key=natural_key) == [
        "photo1.jpg",
        "photo2.jpg",
        "photo10.jpg",
    ]


def test_listing_is_immediate_and_filters_supported_types(tmp_path):
    (tmp_path / "folder").mkdir()
    for name in ["photo2.JPG", "photo10.png", ".hidden.png", "notes.txt", "book.zip"]:
        (tmp_path / name).touch()
    entries = {entry.name: entry.kind for entry in iter_directory(tmp_path)}
    assert entries == {
        "folder": "folder",
        "photo2.JPG": "image",
        "photo10.png": "image",
        "book.zip": "archive",
    }
    assert ".hidden.png" in {entry.name for entry in iter_directory(tmp_path, True)}


def test_navigation_history_branch_and_parent(tmp_path):
    nav = Navigation()
    assert nav.current is None
    nav.navigate(tmp_path)
    nav.navigate(tmp_path / "a")
    nav.navigate(tmp_path / "b")
    assert nav.back() == tmp_path / "a"
    assert nav.can_forward
    nav.navigate(tmp_path / "c")
    assert not nav.can_forward
    assert nav.up() == tmp_path
    assert nav.back() == tmp_path / "c"

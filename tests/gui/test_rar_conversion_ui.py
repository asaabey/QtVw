"""RAR grid discovery and asynchronous conversion UI."""

import zipfile

from PySide6.QtCore import Qt

from qtvw.ui.main_window import MainWindow


def test_grid_context_menu_converts_rar(qtbot, tmp_path, make_rar, image_bytes):
    source = make_rar(tmp_path / "album.rar", [("nested/photo.png", image_bytes())])
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    assert len(window.model.entries) == 1
    assert window.model.entries[0].kind == "rar"
    assert "convert" in window.model.data(window.model.index(0), Qt.ItemDataRole.ToolTipRole)
    qtbot.waitUntil(lambda: not window.browser.visualRect(window.model.index(0)).isEmpty())
    window._browser_context_menu(window.browser.visualRect(window.model.index(0)).center())
    action = window._browser_menu.actions()[0]
    assert action.text() == "Convert to ZIP…"
    window._browser_menu.close()
    action.trigger()
    qtbot.waitUntil(lambda: not window._rar_converting)
    qtbot.waitUntil(lambda: not window._listing_active)
    assert {entry.name for entry in window.model.entries} == {"album.rar", "album.zip"}
    assert source.exists()
    with zipfile.ZipFile(source.with_suffix(".zip")) as archive:
        assert archive.read("nested/photo.png") == image_bytes()
    window.close()


def test_conversion_error_is_displayed(qtbot, tmp_path, make_rar):
    source = make_rar(tmp_path / "album.rar", [("photo.png", b"x")])
    source.with_suffix(".zip").write_bytes(b"keep")
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.convert_rar_archive(source)
    qtbot.waitUntil(lambda: not window._rar_converting)
    assert "already exists" in window._conversion_error.text()
    assert source.with_suffix(".zip").read_bytes() == b"keep"
    window._conversion_error.accept()
    window.close()


def test_close_waits_for_conversion_cancellation(qtbot, tmp_path, make_rar, monkeypatch):
    from threading import Event

    from qtvw.services import rar_conversion

    entered, release = Event(), Event()

    def controlled(source, cancel, progress, max_entry_bytes):
        entered.set()
        release.wait(5)
        assert cancel.is_set()
        raise rar_conversion.ConversionCancelled()

    monkeypatch.setattr(rar_conversion, "convert_rar", controlled)
    source = make_rar(tmp_path / "album.rar", [("photo.png", b"x")])
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.convert_rar_archive(source)
    qtbot.waitUntil(entered.is_set)
    window.close()
    assert window.isVisible()
    assert window._close_after_conversion
    release.set()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert not source.with_suffix(".zip").exists()

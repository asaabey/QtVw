"""Exercise asynchronous folder browsing and image viewer transitions."""

from PySide6.QtGui import QImage

from qtvw.ui.main_window import MainWindow


def test_local_folder_and_viewer(qtbot, tmp_path):
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(0xFF336699)
    for name in ("photo10.png", "photo2.png"):
        assert image.save(str(tmp_path / name))
    (tmp_path / "child").mkdir()
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.model.rowCount() == 3)
    qtbot.waitUntil(lambda: window.model.entries[1].name == "photo2.png")
    window.browser.setCurrentIndex(window.model.index(1))
    window._open_index(window.model.index(1))
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewer.item.pixmap().size() == image.size()
    window.viewer.actual_size()
    assert window.viewer.transform().m11() == 1
    window.step_image(1)
    qtbot.waitUntil(
        lambda: (
            "photo10.png" in window.statusBar().currentMessage() and window.viewer.item is not None
        )
    )
    window.show_grid()
    assert window.browser.currentIndex().row() == 1
    assert not window.next_action.isEnabled()
    window.navigate(tmp_path / "child")
    qtbot.waitUntil(lambda: window.model.rowCount() == 0)
    window.back()
    qtbot.waitUntil(lambda: window.model.rowCount() == 3)
    window.close()


def test_visible_rows_follow_far_scroll(qtbot, tmp_path):
    from qtvw.domain import Entry
    from qtvw.ui.browser import Browser, BrowserModel

    browser = Browser()
    qtbot.addWidget(browser)
    model = BrowserModel(browser)
    browser.setModel(model)
    model.append([Entry(tmp_path / f"image{i}.png", "image") for i in range(1000)])
    browser.resize(600, 400)
    browser.show()
    qtbot.waitUntil(lambda: browser.verticalScrollBar().maximum() > 0)
    browser.scrollTo(model.index(900))
    qtbot.waitUntil(lambda: 900 in browser.visible_rows())
    assert len(browser.visible_rows()) < 40
    browser.scrollTo(model.index(0))
    qtbot.waitUntil(lambda: 0 in browser.visible_rows())

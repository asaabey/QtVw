"""Create child folders through the sidebar without blocking navigation."""

from threading import Event

from PySide6.QtWidgets import QDialogButtonBox

from qtvw.ui import main_window
from qtvw.ui.main_window import MainWindow


def test_sidebar_new_folder_menu_creates_child_and_refreshes_grid(qtbot, tmp_path):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    parent = window.folders.index(str(tmp_path))
    window.tree.setRootIndex(window.folders.index(str(tmp_path.parent)))
    window.tree.scrollTo(parent)
    qtbot.waitUntil(lambda: not window.tree.visualRect(parent).isEmpty())
    window._folder_context_menu(window.tree.visualRect(parent).center())
    actions = {action.text(): action for action in window._folder_menu.actions()}
    assert actions["New folder…"].isEnabled()
    assert "Delete folder…" in actions
    window._folder_menu.close()
    actions["New folder…"].trigger()
    dialog = window._folder_create_dialog
    assert str(tmp_path) in dialog.labelText()
    dialog.setTextValue("旅行 2026")
    buttons = dialog.findChild(QDialogButtonBox)
    buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    child = tmp_path / "旅行 2026"
    qtbot.waitUntil(lambda: not window._folder_creating)
    qtbot.waitUntil(lambda: [entry.name for entry in window.model.entries] == [child.name])
    assert child.is_dir()
    assert window.navigation.current == tmp_path
    qtbot.waitUntil(lambda: window.tree.isExpanded(window.folders.index(str(tmp_path))))
    qtbot.waitUntil(lambda: window.folders.index(str(child)).isValid())
    window.close()


def test_new_folder_cancel_invalid_names_and_existing_entries(qtbot, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "existing").mkdir()
    (parent / "file").write_text("keep")
    (parent / "link").symlink_to(parent / "existing", target_is_directory=True)
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    dialog = window.create_sidebar_folder(parent)
    dialog.setTextValue("cancelled")
    dialog.reject()
    assert not (parent / "cancelled").exists()
    for name in (" ", ".", "..", "../escape", "one/two", "one\\two", "existing", "file", "link"):
        dialog = window.create_sidebar_folder(parent)
        dialog.setTextValue(name)
        dialog.accept()
        qtbot.waitUntil(lambda: not window._folder_creating)
        assert window._folder_create_error.isVisible()
        assert window._folder_create_error.text()
        window._folder_create_error.accept()
    assert {entry.name for entry in parent.iterdir()} == {"existing", "file", "link"}
    assert (parent / "file").read_text() == "keep"
    assert not (tmp_path / "escape").exists()
    assert window.navigation.current == tmp_path
    # The clicked parent, rather than the current browser location, owns the new child.
    dialog = window.create_sidebar_folder(parent)
    dialog.setTextValue("child")
    dialog.accept()
    qtbot.waitUntil(lambda: not window._folder_creating)
    assert (parent / "child").is_dir()
    assert not (tmp_path / "child").exists()
    assert window.navigation.current == tmp_path
    window.close()


def test_folder_creation_errors_are_displayed(qtbot, tmp_path, monkeypatch):
    def denied(parent, name):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(main_window, "create_child_folder", denied)
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    dialog = window.create_sidebar_folder(tmp_path)
    dialog.setTextValue("child")
    dialog.accept()
    qtbot.waitUntil(lambda: not window._folder_creating)
    assert "Permission denied" in window._folder_create_error.text()
    assert not (tmp_path / "child").exists()
    window._folder_create_error.accept()
    window.close()


def test_close_waits_for_background_folder_creation(qtbot, tmp_path, monkeypatch):
    entered, release = Event(), Event()
    create = main_window.create_child_folder

    def controlled(parent, name):
        entered.set()
        release.wait(5)
        return create(parent, name)

    monkeypatch.setattr(main_window, "create_child_folder", controlled)
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    dialog = window.create_sidebar_folder(tmp_path)
    dialog.setTextValue("child")
    dialog.accept()
    try:
        qtbot.waitUntil(entered.is_set)
        window.close()
        assert window.isVisible()
        assert not window._closing
        assert window._close_after_folder_create
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert (tmp_path / "child").is_dir()

import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory):
    """Keep QSettings (update preferences etc.) out of the developer's real ~/.config."""
    path = str(tmp_path_factory.mktemp("qsettings"))
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        QSettings.setPath(fmt, QSettings.Scope.UserScope, path)
    yield

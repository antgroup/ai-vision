import importlib
import pkgutil
from pathlib import Path

_pkg_dir = str(Path(__file__).parent)
for _info in pkgutil.walk_packages([_pkg_dir], prefix=f"{__name__}."):
    importlib.import_module(_info.name)

"""Enterprise lake pack. Import `PACK`; submodules load lazily so importing the package is cheap."""

from packs.enterprise_lake.pack import PACK

__all__ = ["PACK"]

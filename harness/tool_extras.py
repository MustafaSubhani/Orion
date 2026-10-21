"""Compatibility alias. The lake tools now live in the enterprise_lake domain pack.

`harness.tool_extras` and `packs.enterprise_lake.tools` are the same module object, so existing
imports and `unittest.mock.patch("harness.tool_extras._load_table_role")` keep working.
"""

import sys

from packs.enterprise_lake import tools as _tools

sys.modules[__name__] = _tools

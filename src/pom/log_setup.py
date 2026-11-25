"""Old import path for pom.bookkeeper.log_setup, kept until the callers are switched over (then this file is deleted).

It is the very same module object, so a test that patches `pom.log_setup.X` still patches the real thing."""
import sys

from pom.bookkeeper import log_setup

sys.modules[__name__] = log_setup

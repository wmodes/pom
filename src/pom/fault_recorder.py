"""Old import path for pom.bookkeeper.fault_recorder, kept until the callers are switched over (then this file is deleted).

It is the very same module object, so a test that patches `pom.fault_recorder.X` still patches the real thing."""
import sys

from pom.bookkeeper import fault_recorder

sys.modules[__name__] = fault_recorder

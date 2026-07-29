import os


# Gateway tests import the application during collection and must use its test profile.
os.environ.setdefault("NODE_ENV", "test")
os.environ.setdefault("CSRF_ENFORCEMENT_MODE", "disabled")

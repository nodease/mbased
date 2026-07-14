from __future__ import annotations


class AppLifecycleError(Exception):
    code = "app.delete_failed"
    message = "App deletion failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)


class AppResourceHidden(AppLifecycleError):
    code = "resource.not_found"
    message = "App not found."


class AppPermissionDenied(AppLifecycleError):
    code = "permission.denied"
    message = "App manage permission is required."


class AppDeleteInProgress(AppLifecycleError):
    code = "app.delete_in_progress"
    message = "App has an operation in progress."


class AppDeleteRequiresRepair(AppLifecycleError):
    code = "app.delete_requires_repair"
    message = "App relationships must be repaired before deletion."

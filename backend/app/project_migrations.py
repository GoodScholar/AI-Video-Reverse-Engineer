from copy import deepcopy


def migrate_project_payload(payload: object) -> object:
    """Return a copied project payload with legacy media fields moved to their new names."""
    migrated = deepcopy(payload)
    if not isinstance(migrated, dict):
        return migrated

    _migrate_reference_media(migrated)
    _migrate_local_preprocessing(migrated)
    return migrated


def _migrate_reference_media(project: dict) -> None:
    if "referenceMedia" in project:
        return
    legacy_video = project.pop("referenceVideo", None)
    if legacy_video is None:
        return
    if not isinstance(legacy_video, dict):
        project["referenceMedia"] = legacy_video
        return
    media = deepcopy(legacy_video)
    media.setdefault("type", "video")
    project["referenceMedia"] = media


def _migrate_local_preprocessing(project: dict) -> None:
    preprocessing = project.get("localPreprocessing")
    if not isinstance(preprocessing, dict):
        return

    if "sourceReferenceMediaId" in preprocessing:
        return
    legacy_source_id = preprocessing.pop("sourceReferenceVideoId", None)
    if legacy_source_id is not None:
        preprocessing["sourceReferenceMediaId"] = legacy_source_id
        if "mediaType" not in preprocessing:
            preprocessing["mediaType"] = "video"

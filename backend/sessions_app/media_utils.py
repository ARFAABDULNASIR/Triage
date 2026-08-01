import os

ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm"}
ALLOWED_MEDIA_EXTENSIONS = ALLOWED_AUDIO_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS


def media_extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def is_allowed_media(filename: str) -> bool:
    return media_extension(filename) in ALLOWED_MEDIA_EXTENSIONS


def is_video_file(filename: str) -> bool:
    return media_extension(filename) in ALLOWED_VIDEO_EXTENSIONS

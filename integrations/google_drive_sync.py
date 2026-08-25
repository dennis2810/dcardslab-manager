from pathlib import Path
import json
from datetime import datetime

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CONFIG_NAME = "google_drive_config.json"
TOKEN_NAME = "drive_token.json"
CREDENTIALS_NAME = "credentials.json"


def _config_dir():
    """Persistent per-user Google configuration, independent of DCardLabs version folder."""
    import os
    root = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / "DCardLabs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _migrate_legacy_files(base):
    import shutil
    base = Path(base)
    user = _config_dir()
    for name in (CREDENTIALS_NAME, TOKEN_NAME, CONFIG_NAME):
        src = base / name
        dst = user / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    return user


def _paths(base):
    base = Path(base)
    user = _config_dir()
    return user / CONFIG_NAME, user / CREDENTIALS_NAME, user / TOKEN_NAME


def load_config(base):
    _migrate_legacy_files(base)
    config_path, _, _ = _paths(base)
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(base, config):
    _migrate_legacy_files(base)
    config_path, _, _ = _paths(base)
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_service(base, interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    base = Path(base)
    _, credentials_path, token_path = _paths(base)
    if not credentials_path.exists():
        if not interactive:
            return None
        raise FileNotFoundError("credentials.json fehlt. Bitte die vorhandene Google-OAuth-Konfiguration für Google Sheets verwenden.")

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.valid:
        return build("drive", "v3", credentials=creds)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return build("drive", "v3", credentials=creds)
    if not interactive:
        return None
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=creds)


def ensure_folders(base, interactive=False):
    """Create/reuse DCardLabs/Backups, DCardLabs/Cards and DCardLabs/eBay."""
    base = Path(base)
    service = _get_service(base, interactive=interactive)
    if service is None:
        return None
    cfg = load_config(base)

    def find_or_create(name, parent_id=None):
        q = "name = ? and mimeType = 'application/vnd.google-apps.folder' and trashed = false".replace("?", repr(name))
        if parent_id:
            q += f" and '{parent_id}' in parents"
        result = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=10).execute()
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            body["parents"] = [parent_id]
        return service.files().create(body=body, fields="id").execute()["id"]

    root_id = cfg.get("root_folder_id")
    if not root_id:
        root_id = find_or_create("DCardLabs")
    backups_id = cfg.get("backup_folder_id") or find_or_create("Backups", root_id)
    cards_id = cfg.get("cards_folder_id") or find_or_create("Cards", root_id)
    ebay_id = cfg.get("ebay_folder_id") or find_or_create("eBay", root_id)
    cfg.update({"root_folder_id": root_id, "backup_folder_id": backups_id, "cards_folder_id": cards_id, "ebay_folder_id": ebay_id})
    cfg["last_folder_check"] = datetime.now().isoformat(timespec="seconds")
    save_config(base, cfg)
    return service, cfg


def setup(base):
    return ensure_folders(base, interactive=True)


def upload_file(base, local_path, folder_id, mime_type="application/octet-stream", overwrite=False):
    from googleapiclient.http import MediaFileUpload
    service, cfg = ensure_folders(base, interactive=False)
    if service is None:
        raise RuntimeError("Google Drive ist noch nicht autorisiert. Bitte einmal 'Google Drive Backup einrichten' ausführen.")
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(str(local_path))
    body = {"name": local_path.name, "parents": [folder_id]}
    if overwrite:
        q = f"name = '{local_path.name.replace(chr(39), chr(39)*2)}' and '{folder_id}' in parents and trashed = false"
        existing = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=10).execute().get("files", [])
        if existing:
            fid = existing[0]["id"]
            service.files().update(fileId=fid, media_body=MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)).execute()
            return fid
    return service.files().create(body=body, media_body=MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True), fields="id,name,webViewLink").execute()["id"]


def _make_public_image(service, file_id):
    """Make an uploaded card image readable without Google login and return an eBay-friendly URL."""
    try:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
    except Exception as exc:
        # If the permission already exists, continue. Other permission errors must be surfaced.
        msg = str(exc).lower()
        if "already" not in msg and "duplicate" not in msg:
            raise
    return f"https://lh3.googleusercontent.com/d/{file_id}"


def upload_card_images(base, card_id, front_path=None, back_path=None):
    """Upload the front/back images of one card to DCardLabs/Cards.

    Returns {"urls": [...], "file_ids": [...]} in front-then-back order.
    Existing files with the same generated name are overwritten.
    """
    from googleapiclient.http import MediaFileUpload

    prepared = ensure_folders(base, interactive=False)
    if prepared is None:
        raise RuntimeError("Google Drive ist noch nicht autorisiert. Bitte einmal 'Google Drive Backup einrichten' ausführen.")
    service, cfg = prepared
    folder_id = cfg["cards_folder_id"]

    results = []
    for side, path in (("front", front_path), ("back", back_path)):
        if not path:
            continue
        local_path = Path(path)
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"{side}: {local_path}")

        ext = local_path.suffix.lower() or ".jpg"
        name = f"{int(card_id):06d}_{side}{ext}"
        safe_name = name.replace("'", "''")
        q = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
        existing = service.files().list(
            q=q, spaces="drive", fields="files(id,name)", pageSize=10
        ).execute().get("files", [])

        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png" if ext == ".png" else "application/octet-stream"
        media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)
        if existing:
            file_id = existing[0]["id"]
            service.files().update(fileId=file_id, media_body=media, fields="id,name").execute()
        else:
            body = {"name": name, "parents": [folder_id]}
            file_id = service.files().create(body=body, media_body=media, fields="id,name").execute()["id"]

        url = _make_public_image(service, file_id)
        results.append({"side": side, "file_id": file_id, "url": url})

    return {"urls": [x["url"] for x in results], "file_ids": [x["file_id"] for x in results], "files": results}


def upload_backup(base, backup_path):
    prepared = ensure_folders(base, interactive=False)
    if prepared is None:
        return None
    service, cfg = prepared
    from googleapiclient.http import MediaFileUpload
    backup_path = Path(backup_path)
    body = {"name": backup_path.name, "parents": [cfg["backup_folder_id"]]}
    result = service.files().create(
        body=body,
        media_body=MediaFileUpload(str(backup_path), mimetype="application/zip", resumable=True),
        fields="id,name,webViewLink,size"
    ).execute()
    return result


def backup_project_to_drive(base, backup_creator):
    """Create a complete local project backup and upload it to Drive/Backups."""
    backup_path = backup_creator()
    result = upload_backup(base, backup_path)
    return backup_path, result
